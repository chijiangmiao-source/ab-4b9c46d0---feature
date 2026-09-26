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


def _audit_payload(side, proc):
    return {"side": side, "procedure": proc}


def test_audit_full_dimension_returns_evidence():
    m = {"x": [["1/2", "1/2"], ["1/3", "2/3"]]}
    resp = client.post("/api/audit", json=_audit_payload("A", _proc(2, ["1", "0"], [1], m)))
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["side"] == "A"
    audit = body["audit"]
    assert audit["n"] == 2
    assert audit["minimalDimension"] == 2
    assert audit["reduced"] is False
    assert set(audit) >= {
        "n",
        "reachableRank",
        "observableRank",
        "minimalDimension",
        "reduced",
        "prefixBasis",
        "suffixBasis",
        "pivotPrefixes",
        "pivotSuffixes",
        "alpha",
        "beta",
        "matrices",
        "replay",
        "verified",
    }
    # 所有数值字段都是精确分数对象 {num, den, text}
    for f in audit["alpha"] + audit["beta"]:
        assert set(f) == {"num", "den", "text"} and isinstance(f["num"], int)
    for row in audit["matrices"]["x"]:
        for f in row:
            assert isinstance(f["den"], int) and f["den"] > 0
    for item in audit["replay"]:
        assert item["match"] is True
        assert set(item["original"]) == {"num", "den", "text"}
        assert set(item["compressed"]) == {"num", "den", "text"}
    assert audit["verified"] is True


def test_audit_dimension_shrink_and_reconstruction():
    # 3 -> 2：状态 1、2 转移行为一致，Hankel 秩暴露一维冗余。
    m = {"a": [["0", "1/2", "1/2"], ["1", "0", "0"], ["1", "0", "0"]]}
    resp = client.post("/api/audit", json=_audit_payload("B", _proc(3, ["1", "0", "0"], [1, 2], m)))
    assert resp.status_code == 200
    audit = resp.json()["audit"]
    assert audit["n"] == 3 and audit["minimalDimension"] == 2 and audit["reduced"] is True
    assert len(audit["alpha"]) == 2 and len(audit["beta"]) == 2
    assert len(audit["matrices"]["a"]) == 2
    # 逐项回放：空串 + 4 基对 + 4 转移对，全部精确相等
    kinds = [item["kind"] for item in audit["replay"]]
    assert kinds.count("basis") == 4 and kinds.count("transition") == 4
    assert all(item["match"] for item in audit["replay"])


def test_audit_rejects_illegal_probability_row_with_loc():
    # 第一行概率和 1/2 ≠ 1（精确分数校验），必须 400 且定位到 B 侧。
    bad = _proc(
        2,
        ["1", "0"],
        [1],
        {"x": [["1/2", "0"], ["0", "1"]]},
    )
    resp = client.post("/api/audit", json=_audit_payload("B", bad))
    assert resp.status_code == 400
    body = resp.json()
    assert body["ok"] is False
    locs = [tuple(e["loc"]) for e in body["errors"]]
    assert any(loc and loc[0] == "B" for loc in locs)
    # 行和不为一按既有约定定位到具体行 ['B','commands',0,'rows',0]
    assert any(
        loc[:4] == ("B", "commands", 0, "rows") and any("转出概率和必须为 1" in e["msg"] for e in body["errors"])
        for loc in locs
    )


def test_audit_rejects_bad_side_and_malformed_json():
    good = _proc(1, ["1"], [0], {"x": [["1"]]})
    resp = client.post("/api/audit", json={"side": "C", "procedure": good})
    assert resp.status_code == 400
    resp2 = client.post("/api/audit", content=b"{", headers={"Content-Type": "application/json"})
    assert resp2.status_code == 400
    assert resp2.json()["ok"] is False
