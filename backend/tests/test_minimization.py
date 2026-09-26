"""最小安全观测实现（Hankel 精确秩）的核心测试。

覆盖：冗余维数收缩、同安全标记状态不被直接合并、满秩时仍返回完整证据、
零维边界、多命令重构，以及与“长度上限内暴力枚举”的交叉复核
（枚举只出现在测试里，被测算法本身不做枚举）。
"""

from fractions import Fraction
from itertools import product

from app.equivalence import parse_procedure
from app.minimization import _dot, _row_mul, minimal_realization


def make_proc(n, initial, safe, commands):
    data = {
        "n": n,
        "initial": initial,
        "safe": safe,
        "commands": [
            {
                "symbol": c,
                "rows": [
                    [{"target": j, "prob": m[i][j]} for j in range(n)]
                    for i in range(n)
                ],
            }
            for c, m in commands.items()
        ],
    }
    return parse_procedure("X", data)


def brute_safe_prob(proc, word):
    dist = list(proc.initial)
    for ch in word:
        dist = _row_mul(dist, proc.matrices[ch], proc.n)
    g = [Fraction(1 if i in proc.safe else 0) for i in range(proc.n)]
    return _dot(dist, g)


def compressed_safe_prob(result, word):
    r = result["minimalDim"]
    state = list(result["initial"])
    for ch in word:
        state = _row_mul(state, result["matrices"][ch], r)
    return _dot(state, result["terminal"])


def assert_matches_upto(proc, result, max_len):
    symbols = proc.symbols
    for length in range(max_len + 1):
        for tup in product(symbols, repeat=length):
            word = "".join(tup)
            assert compressed_safe_prob(result, word) == brute_safe_prob(proc, word), word


def test_redundant_states_shrink_dimension():
    # 两个状态转移完全相同且初始分布平稳：Hankel 秩 1，2 态压到 1 维
    m = [["1/3", "2/3"], ["1/3", "2/3"]]
    proc = make_proc(2, ["1/3", "2/3"], [1], {"a": m})
    res = minimal_realization(proc)
    assert res["minimalDim"] == 1
    assert res["redundantStates"] == 1
    assert res["prefixWords"] == [""]
    assert res["suffixWords"] == [""]
    assert res["initial"] == [Fraction(1)]
    assert res["terminal"] == [Fraction(2, 3)]
    assert res["matrices"]["a"] == [[Fraction(1)]]
    assert_matches_upto(proc, res, 6)


def test_three_state_duplicate_rows_shrink_to_two():
    # 状态 0/1 行完全相同：3 态压到 2 维；压缩矩阵含负数、行和不为 1，
    # 说明它只是安全概率语义模型而非随机控制器
    m = [
        ["1/3", "1/3", "1/3"],
        ["1/3", "1/3", "1/3"],
        ["1/6", "1/3", "1/2"],
    ]
    proc = make_proc(3, ["1/2", "1/2", "0"], [2], {"a": m})
    res = minimal_realization(proc)
    assert res["minimalDim"] == 2
    assert res["reachableDim"] == 3  # 行空间本身满秩，收缩来自 Hankel 配对
    mu = res["matrices"]["a"]
    assert any(x < 0 for row in mu for x in row)
    assert_matches_upto(proc, res, 6)


def test_same_safe_label_states_are_not_merged():
    # 状态 1、2 同为安全态，但后续行为不同：最小维数必须保持 3，
    # 任何“按安全标记直接合并”的做法都会丢维
    m = [["0", "1/2", "1/2"], ["0", "1", "0"], ["1", "0", "0"]]
    proc = make_proc(3, ["1", "0", "0"], [1, 2], {"a": m})
    res = minimal_realization(proc)
    assert res["minimalDim"] == 3
    assert res["redundantStates"] == 0
    assert_matches_upto(proc, res, 6)


def test_full_rank_returns_complete_evidence():
    # 3 态链：r == n，仍返回完整证据且命令矩阵重构为原矩阵
    m = [["0", "1", "0"], ["0", "0", "1"], ["0", "0", "1"]]
    proc = make_proc(3, ["1", "0", "0"], [2], {"a": m})
    res = minimal_realization(proc)
    assert res["minimalDim"] == 3
    assert res["prefixWords"] == ["", "a", "aa"]
    assert res["suffixWords"] == ["", "a", "aa"]
    assert res["initial"] == [Fraction(1), Fraction(0), Fraction(0)]
    assert res["terminal"] == [Fraction(0), Fraction(0), Fraction(1)]
    expected = [[Fraction(x) for x in row] for row in [[0, 1, 0], [0, 0, 1], [0, 0, 1]]]
    assert res["matrices"]["a"] == expected
    # 基对回放逐项相等，且 Hankel 方阵可逆（秩 3）
    assert len(res["replay"]) == 9
    assert all(item["original"] == item["compressed"] for item in res["replay"])
    assert_matches_upto(proc, res, 6)


def test_zero_series_has_zero_dimension():
    # 安全态不可达：所有串安全概率恒 0，最小实现为 0 维
    m = [["1", "0"], ["0", "1"]]
    proc = make_proc(2, ["1", "0"], [1], {"a": m})
    res = minimal_realization(proc)
    assert res["minimalDim"] == 0
    assert res["redundantStates"] == 2
    assert res["prefixWords"] == [] and res["suffixWords"] == []
    assert res["initial"] == [] and res["terminal"] == []
    assert res["matrices"]["a"] == []
    assert res["replay"] == []
    assert_matches_upto(proc, res, 4)


def test_multi_command_realization_matches_bruteforce():
    # 两条命令的一般随机矩阵：压缩模型在长度 ≤5 的全部串上与原规程一致
    ma = [["1/2", "1/2"], ["1/3", "2/3"]]
    mb = [["3/4", "1/4"], ["2/5", "3/5"]]
    proc = make_proc(2, ["1", "0"], [1], {"a": ma, "b": mb})
    res = minimal_realization(proc)
    assert res["minimalDim"] == 2
    assert set(res["matrices"]) == {"a", "b"}
    assert_matches_upto(proc, res, 5)


def test_replay_pairs_cover_hankel_entries():
    m = [["0", "1", "0"], ["0", "0", "1"], ["0", "0", "1"]]
    proc = make_proc(3, ["1", "0", "0"], [2], {"a": m})
    res = minimal_realization(proc)
    r = res["minimalDim"]
    assert len(res["replay"]) == r * r
    for item in res["replay"]:
        i, j = item["prefixIndex"], item["suffixIndex"]
        assert item["word"] == item["prefix"] + item["suffix"]
        # 回放值与 Hankel 交叠方阵对应项一致
        assert item["original"] == res["hankel"][i][j]


def test_randomized_two_command_matrices_enumerative_crosscheck():
    # 固定种子的随机随机矩阵：长度 ≤5 全部命令串上压缩/原概率精确一致
    import random

    rng = random.Random(20260926)

    def stochastic(n):
        mat = []
        for _ in range(n):
            nums = [rng.randint(1, 7) for _ in range(n)]
            total = sum(nums)
            mat.append([Fraction(x, total) for x in nums])
        return mat

    matrices = {"a": stochastic(3), "b": stochastic(3)}
    str_matrices = {c: [[str(x) for x in row] for row in m] for c, m in matrices.items()}
    proc = make_proc(3, ["1", "0", "0"], [2], str_matrices)
    res = minimal_realization(proc)
    assert 1 <= res["minimalDim"] <= 3
    assert_matches_upto(proc, res, 5)
