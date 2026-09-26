"""API 层测试：等价、反例回放、错误定位与精确分数序列化。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _rows(mat):
    n = len(mat)
    return [[{"target": j, "prob": mat[i][j]} for j in range(n)] for i in range(n)]


def _proc(n, initial, safe, matrices):
    return {
        "n": n,
        "initial": initial,
        "safe": safe,
        "commands": [{"symbol": c, "rows": _rows(m)} for c, m in matrices.items()],
    }


def test_healthz_and_index():
    assert client.get("/healthz").json() == {"status": "ok"}
    page = client.get("/")
    assert page.status_code == 200
    assert "声学" in page.text


def test_equivalent_pair():
    m = {"x": [["1/2", "1/2"], ["1/3", "2/3"]]}
    body = {"A": _proc(2, ["1", "0"], [1], m), "B": _proc(2, ["1", "0"], [1], m)}
    resp = client.post("/api/review", json=body)
    assert resp.status_code == 200
    assert resp.json()["result"] == {"equivalent": True}


def test_counterexample_payload_is_exact():
    ma = [["0", "1", "0"], ["0", "1", "0"], ["0", "0", "1"]]
    mb = [["0", "1", "0"], ["0", "0", "1"], ["0", "0", "1"]]
    body = {
        "A": _proc(3, ["1", "0", "0"], [2], {"a": ma}),
        "B": _proc(3, ["1", "0", "0"], [2], {"a": mb}),
    }
    resp = client.post("/api/review", json=body)
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["word"] == ["a", "a"]
    assert result["difference"] == {"num": -1, "den": 1, "text": "-1"}
    # 逐步分布的每个概率都是精确分数对象
    for side in ("A", "B"):
        assert len(result[side]["steps"]) == 3
        for step in result[side]["steps"]:
            for f in step["distribution"]:
                assert set(f) == {"num", "den", "text"}
                assert isinstance(f["num"], int) and isinstance(f["den"], int) and f["den"] > 0


def test_errors_are_localized_and_400():
    bad = _proc(2, ["1", "0"], [1], {"x": [["1", "0"], ["0", "1"]]})
    bad["initial"] = ["1/2", "1/3"]  # 和 5/6
    bad["safe"] = []
    bad["commands"][0]["rows"][0][0]["prob"] = "1/0"
    resp = client.post("/api/review", json={"A": bad, "B": bad})
    assert resp.status_code == 400
    locs = [tuple(e["loc"]) for e in resp.json()["errors"]]
    assert any(loc[:2] == ("A", "initial") for loc in locs)
    assert any(loc[:2] == ("A", "safe") for loc in locs)
    assert any(loc and loc[-1] == "prob" for loc in locs)


def test_dangling_state_error():
    bad = _proc(2, ["1", "0"], [1], {"x": [["1", "0"], ["0", "1"]]})
    bad["safe"] = [5]
    resp = client.post("/api/review", json={"A": bad, "B": bad})
    assert resp.status_code == 400
    assert any("悬空" in e["msg"] for e in resp.json()["errors"])


def test_malformed_json_body():
    resp = client.post("/api/review", content=b"{not json", headers={"Content-Type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["ok"] is False


# ---------------------------------------------------------------------------
# /api/audit 最小安全观测实现审计
# ---------------------------------------------------------------------------

_REDUCIBLE = _proc(
    3,
    ["1/2", "1/2", "0"],
    [2],
    {"a": [["1/3", "1/3", "1/3"], ["1/3", "1/3", "1/3"], ["1/6", "1/3", "1/2"]]},
)

_CHAIN3 = _proc(
    3,
    ["1", "0", "0"],
    [2],
    {"a": [["0", "1", "0"], ["0", "0", "1"], ["0", "0", "1"]]},
)


def test_audit_shrinks_redundant_states():
    resp = client.post("/api/audit", json={"side": "A", "A": _REDUCIBLE})
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["side"] == "A"
    assert result["n"] == 3
    assert result["minimalDim"] == 2
    assert result["redundantStates"] == 1
    assert result["compressed"] is True
    assert result["prefixWords"] == ["", "a"]
    assert result["suffixWords"] == ["", "a"]
    # 初始/终止向量与命令矩阵都是精确分数对象
    assert result["initial"] == [
        {"num": 1, "den": 1, "text": "1"},
        {"num": 0, "den": 1, "text": "0"},
    ]
    assert result["terminal"][0] == {"num": 0, "den": 1, "text": "0"}
    mu = result["matrices"]["a"]
    assert len(mu) == 2 and all(len(row) == 2 for row in mu)
    assert mu[1][0] == {"num": -1, "den": 6, "text": "-1/6"}  # 非随机矩阵：含负数
    # 逐项回放：4 个基对，原规程概率与压缩模型概率精确一致
    assert len(result["replay"]) == 4
    for item in result["replay"]:
        assert item["original"] == item["compressed"]
        assert set(item["original"]) == {"num", "den", "text"}


def test_audit_full_dimension_still_returns_evidence():
    resp = client.post("/api/audit", json={"side": "B", "B": _CHAIN3})
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["side"] == "B"
    assert result["minimalDim"] == 3
    assert result["compressed"] is False
    # 完整证据：基、Hankel 方阵、向量、命令矩阵、回放一样不少
    assert result["prefixWords"] == ["", "a", "aa"]
    assert result["suffixWords"] == ["", "a", "aa"]
    assert len(result["hankel"]) == 3
    assert len(result["initial"]) == 3 and len(result["terminal"]) == 3
    assert result["matrices"]["a"][0][1] == {"num": 1, "den": 1, "text": "1"}
    assert len(result["replay"]) == 9
    assert all(item["original"] == item["compressed"] for item in result["replay"])


def test_audit_rejects_invalid_probability_rows():
    bad = _proc(2, ["1", "0"], [1], {"x": [["1/2", "1/3"], ["0", "1"]]})  # 行和 5/6
    resp = client.post("/api/audit", json={"side": "A", "A": bad})
    assert resp.status_code == 400
    body = resp.json()
    assert body["ok"] is False
    assert "result" not in body
    locs = [tuple(e["loc"]) for e in body["errors"]]
    assert any(loc[:3] == ("A", "commands", 0) for loc in locs)
    assert any("转出概率和必须为 1" in e["msg"] for e in body["errors"])


def test_audit_rejects_bad_fraction_and_dangling_safe():
    bad = _proc(2, ["1", "0"], [5], {"x": [["1/0", "0"], ["0", "1"]]})
    resp = client.post("/api/audit", json={"side": "B", "B": bad})
    assert resp.status_code == 400
    msgs = " ".join(e["msg"] for e in resp.json()["errors"])
    assert "悬空" in msgs and "非法分数" in msgs


def test_audit_side_validation():
    resp = client.post("/api/audit", json={"side": "C", "C": _CHAIN3})
    assert resp.status_code == 400
    resp = client.post("/api/audit", json={"A": _CHAIN3})
    assert resp.status_code == 400
    resp = client.post("/api/audit", json={"side": "A"})
    assert resp.status_code == 400
    resp = client.post("/api/audit", content=b"{bad", headers={"Content-Type": "application/json"})
    assert resp.status_code == 400
