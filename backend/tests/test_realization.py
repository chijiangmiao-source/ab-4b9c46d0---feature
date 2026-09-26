"""最小安全观测实现审计的单元测试：秩收缩、精确分数、逐基回放。"""

from fractions import Fraction

import pytest

from app.equivalence import ProcedureValidationError, parse_procedure
from app.realization import minimal_realization


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
    return parse_procedure("A", data)


def test_full_rank_returns_complete_evidence():
    # 2 态满秩：最小维数必须等于原状态数，且证据完整。
    m = [["1/2", "1/2"], ["1/3", "2/3"]]
    res = minimal_realization(make_proc(2, ["1", "0"], [1], {"x": m}))
    assert res["n"] == 2
    assert res["reachableRank"] == 2
    assert res["observableRank"] == 2
    assert res["minimalDimension"] == 2
    assert len(res["alpha"]) == 2 and len(res["beta"]) == 2
    assert len(res["matrices"]["x"]) == 2 and all(len(row) == 2 for row in res["matrices"]["x"])
    # 空串 + r² 基对 + r² 转移对
    assert len(res["replay"]) == 1 + 4 + 4
    assert all(item["match"] for item in res["replay"])
    assert res["verified"] is True


def test_reduction_by_hankel_rank_not_safe_labels():
    # 状态 1、2 的转移行为完全相同（虽然初始不可达区分方向也存在），
    # 安全标记同为 1 并不是合并依据：把它们改成不同安全标记后，
    # Hankel 秩同样暴露收缩。
    m = [["0", "1/2", "1/2"], ["1", "0", "0"], ["1", "0", "0"]]
    same_label = minimal_realization(make_proc(3, ["1", "0", "0"], [1, 2], {"a": m}))
    diff_label = minimal_realization(make_proc(3, ["1", "0", "0"], [1], {"a": m}))
    assert same_label["minimalDimension"] == 2
    assert diff_label["minimalDimension"] == 2
    assert same_label["verified"] and diff_label["verified"]


def test_reduced_model_reproduces_safe_probabilities_only():
    # 3 -> 2 收缩；压缩矩阵允许负条目，但所有回放概率必须精确一致。
    m = [["1/2", "1/4", "1/4"], ["1/4", "1/2", "1/4"], ["1/4", "1/4", "1/2"]]
    res = minimal_realization(make_proc(3, ["1", "0", "0"], [2], {"a": m}))
    assert res["n"] == 3 and res["minimalDimension"] == 2
    for item in res["replay"]:
        assert item["original"] == item["compressed"]
    # α A(a)^k β 与 π M(a)^k f 对 k=0..6 逐项一致（独立枚举复核）
    from app.realization import _dot, _row_mul

    alpha, beta, aa = res["alpha"], res["beta"], res["matrices"]["a"]
    proc = make_proc(3, ["1", "0", "0"], [2], {"a": m})
    dist = list(proc.initial)
    vec = list(alpha)
    for _ in range(7):
        assert sum(dist[i] for i in proc.safe) == _dot(vec, beta)
        dist = _row_mul(dist, proc.matrices["a"], 3)
        vec = _row_mul(vec, aa, 2)


def test_zero_observable_function_has_dimension_zero():
    # 安全态从初始分布永远不可达：安全概率函数恒零，最小维数 0。
    m = [["0", "1", "0"], ["0", "1", "0"], ["0", "0", "1"]]
    res = minimal_realization(make_proc(3, ["1", "0", "0"], [2], {"a": m}))
    assert res["reachableRank"] == 2
    assert res["observableRank"] == 1
    assert res["minimalDimension"] == 0
    assert res["alpha"] == [] and res["beta"] == []
    assert res["matrices"]["a"] == []
    # 空串 + rc×ro 个基对（主元对为 0 个）；原概率与压缩概率都为 0
    basis = [item for item in res["replay"] if item["kind"] == "basis"]
    assert len(basis) == res["reachableRank"] * res["observableRank"]
    assert all(not item["pivot"] for item in basis)
    assert all(item["original"] == Fraction(0) and item["compressed"] == Fraction(0) for item in basis)
    assert not [item for item in res["replay"] if item["kind"] == "transition"]


def test_multi_symbol_basis_and_transition_replay():
    ma = [["0", "1", "0"], ["0", "0", "1"], ["0", "0", "1"]]
    mb = [["1", "0", "0"], ["0", "1", "0"], ["0", "0", "1"]]
    res = minimal_realization(make_proc(3, ["1", "0", "0"], [2], {"a": ma, "b": mb}))
    assert res["minimalDimension"] == 3
    # 前缀/后缀基都是 BFS 发现顺序（长度、ASCII 稳定）
    assert res["pivotPrefixes"] == ["", "a", "aa"]
    assert res["pivotSuffixes"] == ["", "a", "aa"]
    # 1 + r² 基对 + |Σ| r² 转移对 = 1 + 9 + 18
    assert len(res["replay"]) == 28
    transitions = {item["middle"] for item in res["replay"] if item["kind"] == "transition"}
    assert transitions == {"a", "b"}
    assert all(item["match"] for item in res["replay"])


def test_replay_covers_non_pivot_basis_pairs():
    # R=2、O=3、r=2：完整基对 2×3=6 个，其中主元对 4 个，非主元对 2 个。
    m = [["0", "1/2", "1/2"], ["1", "0", "0"], ["1", "0", "0"]]
    res = minimal_realization(make_proc(3, ["1", "0", "0"], [1], {"a": m}))
    assert res["reachableRank"] == 2 and res["observableRank"] == 3
    assert res["minimalDimension"] == 2
    basis = [item for item in res["replay"] if item["kind"] == "basis"]
    assert len(basis) == 6
    assert sum(1 for item in basis if item["pivot"]) == 4
    assert sum(1 for item in basis if not item["pivot"]) == 2
    assert all(item["match"] for item in res["replay"])


def test_single_state_trivial_model():
    res = minimal_realization(make_proc(1, ["1"], [0], {"x": [["1"]]}))
    assert res["minimalDimension"] == 1
    assert res["alpha"] == [Fraction(1)]
    assert res["beta"] == [Fraction(1)]
    assert res["matrices"]["x"] == [[Fraction(1)]]


def test_illegal_input_is_rejected_with_location():
    data = {
        "n": 2,
        "initial": ["1", "0"],
        "safe": [1],
        "commands": [
            {
                "symbol": "x",
                # 第一行概率和 1/2 ≠ 1
                "rows": [
                    [{"target": 0, "prob": "1/2"}, {"target": 1, "prob": "0"}],
                    [{"target": 0, "prob": "0"}, {"target": 1, "prob": "1"}],
                ],
            }
        ],
    }
    with pytest.raises(ProcedureValidationError) as exc:
        parse_procedure("B", data)
    # 行级错误定位（既有约定：行和不为一标记到该命令的具体行）
    assert any(
        e["loc"][:4] == ["B", "commands", 0, "rows"] and "转出概率和必须为 1" in e["msg"]
        for e in exc.value.errors
    )
