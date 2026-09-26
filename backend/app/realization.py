"""最小安全观测实现审计（加权自动机最小化），全程精确有理数。

给定**单份**规程：初始行向量 ``π``、每条 ASCII 命令的行随机矩阵 ``M(c)``、
安全态指示列向量 ``f = 1_S``，命令串 ``w = c₁…cₖ`` 结束时进入安全态的
精确概率为

    f(w) = π M(c₁)…M(cₖ) f ∈ ℚ。

本模块做四件事（**不枚举长度上限内的命令串、不使用浮点数、
不按安全标记粗合并状态**）：

1. 前向 BFS 闭包构造**初始分布可达行空间**
   ``R = span{ π M(p) : p ∈ Σ* }``，得到一组按“长度最短、同长度 ASCII
   字典序最小”确定的稳定前缀代表基；
2. 反向 BFS 闭包构造**安全观测反向列空间**
   ``O = span{ M(s) f : s ∈ Σ* }``（从 ``f`` 出发反复左乘命令矩阵），
   同样得到稳定后缀代表基；
3. 以前后基的 **Hankel 配对** ``K[i][j] = πM(p_i)·M(s_j)f = f(p_i s_j)``
   精确高斯消元求秩 ``r`` —— 它正是标量函数 ``f`` 的最小线性实现维数 ——
   并选出使配对子矩阵 ``H`` 可逆的一组主元前缀、主元后缀；
4. 导出最小线性实现（行向量约定，与原规程一致）：

       β_i = f(p_i)，
       α = (f(s₁),…,f(s_r)) · H⁻¹，
       A(c) = H_c · H⁻¹，  (H_c)[i][j] = f(p_i c s_j)，

   满足对**任意有限命令串** ``w`` 都有 ``f(w) = α A(w) β``。

维数收缩来自 Hankel 配对秩，而**不是**把安全标记相同的状态直接合并：
安全标记不同的状态仍可能因观测等价而收缩。最小实现的矩阵条目允许为负、
一般不再随机，因此它只保持“安全概率”这一标量语义，**不是**可直接替换
原控制器的随机控制器。
"""

from __future__ import annotations

from collections import deque
from fractions import Fraction
from typing import Any, Callable

from .equivalence import Procedure, _add_row, _row_mul

# ---------------------------------------------------------------------------
# 向量工具（全部 Fraction，精确）
# ---------------------------------------------------------------------------


def _col_mul(vec: list[Fraction], matrix: list[list[Fraction]], n: int) -> list[Fraction]:
    """列向量左乘：``matrix · vec``（与行向量右乘 :func:`_row_mul` 相对）。"""
    out = [Fraction(0)] * n
    for j in range(n):
        row = matrix[j]
        total = Fraction(0)
        for i, value in enumerate(vec):
            if value and row[i]:
                total += value * row[i]
        out[j] = total
    return out


def _dot(a: list[Fraction], b: list[Fraction]) -> Fraction:
    return sum((x * y for x, y in zip(a, b)), Fraction(0))


def _transpose(m: list[list[Fraction]]) -> list[list[Fraction]]:
    if not m:
        return []
    return [[m[i][j] for i in range(len(m))] for j in range(len(m[0]))]


def _mat_mul(a: list[list[Fraction]], b: list[list[Fraction]]) -> list[list[Fraction]]:
    """方阵精确相乘；兼容 0×0。"""
    r = len(a)
    if r == 0:
        return []
    out = [[Fraction(0) for _ in range(r)] for _ in range(r)]
    for i in range(r):
        for k in range(r):
            aik = a[i][k]
            if aik == 0:
                continue
            brow = b[k]
            orow = out[i]
            for j in range(r):
                if brow[j]:
                    orow[j] += aik * brow[j]
    return out


def _invert(m: list[list[Fraction]]) -> list[list[Fraction]]:
    """对可逆方阵做增广矩阵高斯-若尔当求逆（精确分数；兼容 0×0）。"""
    r = len(m)
    aug = [
        list(row) + [Fraction(1 if i == j else 0) for j in range(r)]
        for i, row in enumerate(m)
    ]
    for col in range(r):
        pivot = next((i for i in range(col, r) if aug[i][col] != 0), None)
        if pivot is None:
            raise ValueError("配对子矩阵奇异，无法构造最小实现")
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
# BFS 不动点闭包：稳定的代表串线性基（不设长度上限）
# ---------------------------------------------------------------------------


def _independent_closure(
    seed: list[Fraction],
    symbols: list[str],
    extend: Callable[[list[Fraction], str], list[Fraction]],
) -> list[tuple[str, list[Fraction]]]:
    """从 seed 出发做 BFS 闭包，返回线性无关的 ``(代表串, 原始向量)`` 列表。

    扩展顺序固定为命令的 ASCII 序（``symbols`` 已排序），队列按串长度分层，
    因此代表串是“长度最短、同长度 ASCII 字典序最小”的确定性选择；
    子空间维数有界（≤ 状态数），队列必然清空，从不枚举长度上限。
    保留的是**原始向量**（如 πM(p)），RREF 基仅用于独立性判定。
    """
    rref_basis: list[tuple[int, list[Fraction]]] = []
    if not _add_row(seed, rref_basis):  # 概率分布 / 安全指示均非零，必入基
        raise ValueError("闭包种子为零向量")
    representatives: list[tuple[str, list[Fraction]]] = [("", list(seed))]
    queue: deque[tuple[list[Fraction], str]] = deque([(list(seed), "")])
    while queue:
        vec, word = queue.popleft()
        for c in symbols:
            nxt = extend(vec, c)
            if _add_row(nxt, rref_basis):
                representatives.append((word + c, nxt))
                queue.append((nxt, word + c))
    return representatives


# ---------------------------------------------------------------------------
# 精确秩与稳定主元选择
# ---------------------------------------------------------------------------


def _independent_rows(matrix: list[list[Fraction]]) -> list[int]:
    """高斯-若尔当消元，返回线性无关的**原始行索引**（按贡献主元的顺序）。

    顺序即这些行在输入中的 BFS 发现顺序；对转置矩阵调用本函数即可等价地
    选出稳定的独立列。长度即矩阵的秩。
    """
    m = [list(row) for row in matrix]
    rows = len(m)
    cols = len(m[0]) if rows else 0
    origin = list(range(rows))
    rank = 0
    chosen: list[int] = []
    for col in range(cols):
        pivot = next((i for i in range(rank, rows) if m[i][col] != 0), None)
        if pivot is None:
            continue
        if pivot != rank:
            m[rank], m[pivot] = m[pivot], m[rank]
            origin[rank], origin[pivot] = origin[pivot], origin[rank]
        lead = m[rank][col]
        m[rank] = [x / lead for x in m[rank]]
        for i in range(rows):
            if i != rank and m[i][col] != 0:
                factor = m[i][col]
                m[i] = [x - factor * y for x, y in zip(m[i], m[rank])]
        chosen.append(origin[rank])
        rank += 1
    return chosen


# ---------------------------------------------------------------------------
# 最小安全观测实现
# ---------------------------------------------------------------------------


def minimal_realization(proc: Procedure) -> dict[str, Any]:
    """对一份已校验规程构造最小安全观测线性实现并逐项回放取证。"""
    n = proc.n
    symbols = proc.symbols
    pi = list(proc.initial)
    safe_vec = [Fraction(1 if i in proc.safe else 0) for i in range(n)]

    # 1) 初始分布可达行空间 R（前缀基）
    prefix_reps = _independent_closure(
        pi, symbols, lambda v, c: _row_mul(v, proc.matrices[c], n)
    )
    # 2) 安全观测反向列空间 O（后缀基：从 f 反复左乘 M(c)，代表串 c·s）
    suffix_reps = _independent_closure(
        safe_vec, symbols, lambda v, c: _col_mul(v, proc.matrices[c], n)
    )

    prefix_words = [w for w, _ in prefix_reps]
    suffix_words = [w for w, _ in suffix_reps]
    rvecs = [v for _, v in prefix_reps]
    ovecs = [v for _, v in suffix_reps]
    rc, ro = len(rvecs), len(ovecs)

    # 3) Hankel 配对 K[i][j] = πM(p_i)·M(s_j)f = f(p_i s_j)
    gram = [[_dot(rvecs[i], ovecs[j]) for j in range(ro)] for i in range(rc)]

    # 稳定主元：先选独立行（主元前缀），再在该行子集上选独立列（主元后缀）。
    # 选出后按 BFS 发现顺序（长度最短、同长度 ASCII 字典序最小）重排——
    # 行/列置换不改变可逆性，却给出人类可读且确定的基序。
    pivot_rows = sorted(_independent_rows(gram))
    r = len(pivot_rows)
    sub = [gram[i] for i in pivot_rows]
    pivot_cols = sorted(_independent_rows(_transpose(sub))) if r else []
    if len(pivot_cols) != r:
        raise ValueError("Hankel 主元选择异常")

    pivot_prefixes = [prefix_words[i] for i in pivot_rows]
    pivot_suffixes = [suffix_words[j] for j in pivot_cols]

    # H[i][j] = f(p_i s_j) 可逆；据此建立压缩坐标
    hankel = [[sub[i][j] for j in pivot_cols] for i in range(r)]
    hankel_inv = _invert(hankel)

    def replay_original(word: list[str]) -> Fraction:
        """沿原规程矩阵逐步回放：π M(w) f。"""
        dist = list(pi)
        for c in word:
            dist = _row_mul(dist, proc.matrices[c], n)
        return sum((dist[i] for i in proc.safe), Fraction(0))

    # 防御性交叉检查：内积构造的 K 必须与逐步回放完全一致
    for i in range(rc):
        for j in range(ro):
            assert gram[i][j] == replay_original(list(prefix_words[i]) + list(suffix_words[j]))

    # β_i = f(p_i)；α = (f(s_j)) · H⁻¹
    beta = [replay_original(list(p)) for p in pivot_prefixes]
    alpha_source = [replay_original(list(s)) for s in pivot_suffixes]
    alpha = _row_mul(alpha_source, hankel_inv, r) if r else []

    # A(c) = H_c H⁻¹，(H_c)[i][j] = f(p_i c s_j)
    minimal_matrices: dict[str, list[list[Fraction]]] = {}
    for c in symbols:
        h_c = [
            [
                replay_original(list(pivot_prefixes[i]) + [c] + list(pivot_suffixes[j]))
                for j in range(r)
            ]
            for i in range(r)
        ]
        minimal_matrices[c] = _mat_mul(h_c, hankel_inv) if r else []

    def replay_compressed(word: list[str]) -> Fraction:
        """沿压缩模型回放：α A(w) β。"""
        vec = list(alpha)
        for c in word:
            vec = _row_mul(vec, minimal_matrices[c], r)
        return _dot(vec, beta)

    # 4) 逐项回放取证：空串、全部 rc×ro 个基对（含非主元代表串）、
    #    每条命令在主元基对上的 r² 个转移对
    pivot_row_set = set(pivot_rows)
    pivot_col_set = set(pivot_cols)
    replay: list[dict[str, Any]] = []

    def add_item(
        kind: str, prefix: str, middle: str, suffix: str, pivot: bool = True
    ) -> None:
        word = list(prefix) + ([middle] if middle else []) + list(suffix)
        original = replay_original(word)
        compressed = replay_compressed(word)
        replay.append(
            {
                "kind": kind,
                "prefix": prefix,
                "middle": middle,
                "suffix": suffix,
                "pivot": pivot,
                "original": original,
                "compressed": compressed,
                "match": original == compressed,
            }
        )

    add_item("empty", "", "", "")
    # 完整行/列空间代表基对上的回放（主元对标注 pivot=True）
    for i in range(rc):
        for j in range(ro):
            add_item(
                "basis",
                prefix_words[i],
                "",
                suffix_words[j],
                pivot=(i in pivot_row_set and j in pivot_col_set),
            )
    for c in symbols:
        for i in range(r):
            for j in range(r):
                add_item("transition", pivot_prefixes[i], c, pivot_suffixes[j])

    verified = all(item["match"] for item in replay)

    return {
        "n": n,
        "reachableRank": rc,
        "observableRank": ro,
        "minimalDimension": r,
        "prefixBasis": prefix_words,
        "suffixBasis": suffix_words,
        "pivotPrefixes": pivot_prefixes,
        "pivotSuffixes": pivot_suffixes,
        "alpha": alpha,
        "beta": beta,
        "matrices": minimal_matrices,
        "replay": replay,
        "verified": verified,
    }
