"""单份规程的最小**安全观测**线性实现（精确有理数，Hankel 秩法）。

对一份已通过校验的随机规程（初始行分布 π、各 ASCII 命令的随机矩阵
M(c)、安全态指示列向量 g），求保持全部

    f(w) = π M(w₁)…M(wₖ) g        （命令串 w 结束时进入安全态的概率）

的最小维线性表示，即维数等于 Hankel 映射

    H(p, s) = f(ps) = (π M(p)) (M(s) g)

的秩。构造方式（全程 :class:`fractions.Fraction`，无浮点、无串枚举、
**不**按安全标记直接合并状态）：

1. **初始分布可达行空间**
   P = span{ π M(w) : w 为任意命令串 }，
   从 π 出发对各命令矩阵做行向量闭包，每个基向量保留其“前缀命令串”见证。
2. **安全观测反向列空间**
   Q = span{ M(w) g : w 为任意命令串 }，
   从 g 出发反向闭包（v ↦ M(c) v，见证为 c + 旧串），保留“后缀命令串”见证。
   生成元集合有限，闭包必终止，且张成的就是全部串上的空间。
3. 对两组基做 **Hankel 配对** H[i][j] = u_i·v_j，精确 RREF 求秩 r，
   并按稳定的 BFS/ASCII 顺序选出 r 个线性无关的前缀行与后缀列，
   使交叠方阵 H̃ = B̃ C̃ 在 ℚ 上可逆。
   注意 r 可能同时小于行空间维数与列空间维数：可达但永不可观测的方向
   （以及可观测但不可达的方向）都不计入最小维数。
4. 最小线性实现（行向量状态）：

   - 初始向量  α₀ = (π C̃) H̃⁻¹
   - 终止向量  b  = B̃ g
   - 命令矩阵  μ(c) = (B̃ M(c) C̃) H̃⁻¹

   恒有 α₀ μ(w) b = π M(w) g（对所有有限命令串成立，线性代数保证，
   非长度上限内枚举）。压缩模型一般**不是随机矩阵**（可出现负数、行和
   不为 1），只保持安全概率语义，不能当作可替换的随机控制器。
"""

from __future__ import annotations

from collections import deque
from fractions import Fraction
from typing import Any, Callable

from .equivalence import Procedure, _add_row, _row_mul

# ---------------------------------------------------------------------------
# 向量/矩阵精确线性代数
# ---------------------------------------------------------------------------


def _col_mul(matrix: list[list[Fraction]], v: list[Fraction], n: int) -> list[Fraction]:
    """列向量左乘：(M v)[i] = Σ_j M[i][j] v[j]。"""
    out = [Fraction(0)] * n
    for i in range(n):
        row = matrix[i]
        total = Fraction(0)
        for j, value in enumerate(v):
            if value and row[j]:
                total += row[j] * value
        out[i] = total
    return out


def _dot(a: list[Fraction], b: list[Fraction]) -> Fraction:
    return sum((x * y for x, y in zip(a, b)), Fraction(0))


def _span_rank(rows: list[list[Fraction]]) -> int:
    """行向量集合的精确秩（拷贝后做 RREF，不改入参）。"""
    basis: list[tuple[int, list[Fraction]]] = []
    for row in rows:
        _add_row(list(row), basis)
    return len(basis)


def _pivot_columns(rows: list[list[Fraction]]) -> list[int]:
    """RREF 主元列下标（升序）：这些列构成列空间的一组稳定基。"""
    basis: list[tuple[int, list[Fraction]]] = []
    for row in rows:
        _add_row(list(row), basis)
    return sorted(pivot for pivot, _ in basis)


def _inverse(mat: list[list[Fraction]]) -> list[list[Fraction]]:
    """有理数域高斯-若尔当求逆；调用方保证 mat 可逆。"""
    r = len(mat)
    aug = [list(row) + [Fraction(1 if i == j else 0) for j in range(r)] for i, row in enumerate(mat)]
    for col in range(r):
        pivot = next((i for i in range(col, r) if aug[i][col] != 0), None)
        if pivot is None:  # 理论不可达：H̃ 经秩选取保证满秩
            raise ValueError("Hankel 交叠方阵奇异，无法求逆")
        if pivot != col:
            aug[col], aug[pivot] = aug[pivot], aug[col]
        lead = aug[col][col]
        aug[col] = [x / lead for x in aug[col]]
        for i in range(r):
            if i != col and aug[i][col] != 0:
                factor = aug[i][col]
                aug[i] = [x - factor * y for x, y in zip(aug[i], aug[col])]
    return [row[r:] for row in aug]


# ---------------------------------------------------------------------------
# 带“命令串见证”的子空间闭包
# ---------------------------------------------------------------------------


def _closure_with_witnesses(
    seed: list[Fraction],
    symbols: list[str],
    extend: Callable[[list[Fraction], str], list[Fraction]],
    prepend: bool,
) -> list[tuple[str, list[Fraction]]]:
    """从 seed 出发在生成元作用下闭包，返回 (见证串, 原始向量) 列表。

    见证串取发现新维数时的最短 BFS 串，同长度按命令 ASCII 字典序，
    因而结果唯一、可复算。RREF 基只用于判线性相关，返回的向量始终是
    未经消元变形的原始 πM(w) / M(w)g。

    prepend=False（行空间，前缀串）：新见证 = 旧串 + c；
    prepend=True （列空间，后缀串）：新见证 = c + 旧串。
    """
    rref: list[tuple[int, list[Fraction]]] = []
    # 初始种子非零（π 是概率分布、g 含至少一个安全态，均已由校验保证）
    _add_row(list(seed), rref)
    witnesses: list[tuple[str, list[Fraction]]] = [("", list(seed))]
    queue: deque[tuple[str, list[Fraction]]] = deque([("", list(seed))])
    while queue:
        word, vec = queue.popleft()
        for c in symbols:  # ASCII 字典序展开，保证见证稳定
            nxt = extend(vec, c)
            if _add_row(list(nxt), rref):
                witness = c + word if prepend else word + c
                witnesses.append((witness, nxt))
                queue.append((witness, nxt))
    return witnesses


# ---------------------------------------------------------------------------
# 最小安全观测实现
# ---------------------------------------------------------------------------


def minimal_realization(proc: Procedure) -> dict[str, Any]:
    """对单份规程构造最小安全观测线性实现与逐项回放证据。"""
    n = proc.n
    symbols = proc.symbols
    pi = list(proc.initial)
    g = [Fraction(1 if i in proc.safe else 0) for i in range(n)]

    # 1) 可达行空间 P：前缀见证 u = π M(p)
    prefix_wit = _closure_with_witnesses(
        pi, symbols, lambda v, c: _row_mul(v, proc.matrices[c], n), prepend=False
    )
    # 2) 反向观测列空间 Q：后缀见证 v = M(s) g
    suffix_wit = _closure_with_witnesses(
        g, symbols, lambda v, c: _col_mul(proc.matrices[c], v, n), prepend=True
    )

    p_dim, q_dim = len(prefix_wit), len(suffix_wit)

    # 3) Hankel 配对 H[i][j] = u_i · v_j = f(p_i s_j)
    hankel = [[_dot(u, v) for _, v in suffix_wit] for _, u in prefix_wit]
    rank = _span_rank(hankel)

    # 稳定选基：RREF 主元列选后缀，再对选中列的转置 RREF 选前缀行；
    # 取下标升序（= BFS/ASCII 发现顺序），交叠子方阵可逆。
    suffix_idx = _pivot_columns(hankel)  # r 个线性无关列
    selected_cols = [
        [hankel[i][j] for i in range(p_dim)] for j in suffix_idx
    ]  # r × p，秩 r
    prefix_idx = _pivot_columns(selected_cols)  # r 个线性无关行
    prefix_idx.sort()
    suffix_idx.sort()
    assert len(prefix_idx) == len(suffix_idx) == rank

    prefix_words = [prefix_wit[i][0] for i in prefix_idx]
    suffix_words = [suffix_wit[j][0] for j in suffix_idx]
    b_rows = [list(prefix_wit[i][1]) for i in prefix_idx]      # B̃：r×n
    c_cols_mat = [list(suffix_wit[j][1]) for j in suffix_idx]  #  r 个列向量
    # C̃：n×r
    c_tilde = [[c_cols_mat[k][i] for k in range(rank)] for i in range(n)]

    # 交叠方阵 H̃ = B̃ C̃（可逆），并复核其秩确为 r
    h_tilde = [[_dot(b_rows[i], c_tilde_column(c_tilde, k, n)) for k in range(rank)] for i in range(rank)]
    if _span_rank(h_tilde) != rank:
        raise ValueError("Hankel 交叠方阵秩与选取维数不一致")
    h_inv = _inverse(h_tilde)

    # 4) 最小实现：α₀ = (π C̃) H̃⁻¹，b = B̃ g，μ(c) = (B̃ M(c) C̃) H̃⁻¹
    pi_c = [_dot(pi, c_tilde_column(c_tilde, k, n)) for k in range(rank)]
    initial = _mat_mul_vec([pi_c], h_inv)[0]
    terminal = [_dot(row, g) for row in b_rows]

    matrices: dict[str, list[list[Fraction]]] = {}
    for c in symbols:
        mat = proc.matrices[c]
        bm = [_row_mul(row, mat, n) for row in b_rows]       # B̃ M(c)：r×n
        bmc = [
            [_dot(bm[i], c_tilde_column(c_tilde, k, n)) for k in range(rank)]
            for i in range(rank)
        ]                                                    # B̃ M(c) C̃：r×r
        matrices[c] = _mat_mul_vec(bmc, h_inv)

    # 5) 逐项回放：每个（前缀基, 后缀基）对上的原规程概率 vs 压缩模型概率
    replay: list[dict[str, Any]] = []
    for i, p_word in enumerate(prefix_words):
        for j, s_word in enumerate(suffix_words):
            word = p_word + s_word
            original = _trace_safe_prob(pi, g, proc, word)
            state = list(initial)
            for ch in word:
                state = _row_mul(state, matrices[ch], rank)
            compressed = _dot(state, terminal)
            # 线性代数恒等保证；一旦不成立说明构造有误，绝不返回半证据
            if original != compressed or original != h_tilde[i][j]:
                raise ValueError("压缩模型回放与原规程概率不一致")
            replay.append(
                {
                    "prefixIndex": i,
                    "suffixIndex": j,
                    "prefix": p_word,
                    "suffix": s_word,
                    "word": word,
                    "original": original,
                    "compressed": compressed,
                }
            )

    return {
        "n": n,
        "reachableDim": p_dim,
        "observableDim": q_dim,
        "minimalDim": rank,
        "redundantStates": n - rank,
        "prefixWords": prefix_words,
        "suffixWords": suffix_words,
        "hankel": h_tilde,
        "initial": initial,
        "terminal": terminal,
        "matrices": matrices,
        "replay": replay,
    }


def c_tilde_column(c_tilde: list[list[Fraction]], k: int, n: int) -> list[Fraction]:
    return [c_tilde[i][k] for i in range(n)]


def _mat_mul_vec(a: list[list[Fraction]], b: list[list[Fraction]]) -> list[list[Fraction]]:
    """普通矩阵乘法 A·B（有理数，尺寸由调用方保证）。"""
    rows = len(a)
    inner = len(b)
    cols = len(b[0]) if b else 0
    out = [[Fraction(0) for _ in range(cols)] for _ in range(rows)]
    for i in range(rows):
        for k in range(inner):
            aik = a[i][k]
            if aik == 0:
                continue
            brow = b[k]
            orow = out[i]
            for j in range(cols):
                if brow[j]:
                    orow[j] += aik * brow[j]
    return out


def _trace_safe_prob(
    pi: list[Fraction], g: list[Fraction], proc: Procedure, word: str
) -> Fraction:
    """在原规程上沿命令串逐步推进，返回结束时的安全概率（精确分数）。"""
    dist = list(pi)
    for c in word:
        dist = _row_mul(dist, proc.matrices[c], proc.n)
    return _dot(dist, g)
