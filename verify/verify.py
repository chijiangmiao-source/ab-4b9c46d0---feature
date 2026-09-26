#!/usr/bin/env python3
"""Compose verify 服务入口。

验收三件事，全部通过才以退出码 0 结束：

1. 最短概率反例：直接在镜像内调用精确有理数核心，断言
   - 差异只在长度 2 暴露时给出最短反例 "aa"；
   - 同长度按 ASCII 字典序取最小；
   - 等价样例判定为等价；
   - 概率和不为一 / 非法分数被拒绝。
2. 检查构建：镜像内应用代码、静态页面与 uvicorn 均就位。
3. API 冒烟：对运行中的服务打 /healthz、/api/review（等价、反例、非法输入），
   校验状态码、定位信息与精确分数字段，全程无浮点数。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, "/app")

from fractions import Fraction  # noqa: E402

from app.equivalence import (  # noqa: E402
    ProcedureValidationError,
    compare,
    parse_procedure,
)
from app.realization import _dot, _row_mul, minimal_realization  # noqa: E402

TARGET = os.environ.get("TARGET_URL", "http://acoustic-review:8000")

failures: list[str] = []


def _first_existing(*paths: str) -> str | None:
    for path in paths:
        if os.path.isfile(path):
            return path
    return None


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"PASS  {name}")
    else:
        print(f"FAIL  {name}  {detail}")
        failures.append(name)


# ---------------------------------------------------------------------------
# 1. 最短概率反例（核心，精确有理数）
# ---------------------------------------------------------------------------


def proc(n, initial, safe, commands):
    return parse_procedure(
        "X",
        {
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
        },
    )


# 长度 0/1 全同、长度 2 的 "aa" 才暴露
MA = [["0", "1", "0"], ["0", "1", "0"], ["0", "0", "1"]]
MB = [["0", "1", "0"], ["0", "0", "1"], ["0", "0", "1"]]
res = compare(proc(3, ["1", "0", "0"], [2], {"a": MA}), proc(3, ["1", "0", "0"], [2], {"a": MB}))
check("最短反例: word == ['a','a']", res["word"] == ["a", "a"], str(res.get("word")))
check("最短反例: 长度为 2", res["wordLength"] == 2)
check("最短反例: 精确概率差 -1", res["difference"] == Fraction(-1), str(res["difference"]))
check(
    "最短反例: 逐步回放共 3 个分布",
    len(res["A"]["steps"]) == 3 and len(res["B"]["steps"]) == 3,
)

# 同长度 ASCII 字典序最小：a 与 b 都在长度 1 暴露时必须取 'a'
IDENT = [["1", "0"], ["0", "1"]]
FLIP = [["0", "1"], ["0", "1"]]
res2 = compare(
    proc(2, ["1", "0"], [1], {"a": FLIP, "b": FLIP}),
    proc(2, ["1", "0"], [1], {"a": IDENT, "b": IDENT}),
)
check("字典序: 同长度取 ASCII 最小 'a'", res2["word"] == ["a"], str(res2.get("word")))

# 等价样例（1/3 精确求和，避免浮点漂移）
M = [["1/3", "1/3", "1/3"], ["1", "0", "0"], ["0", "1", "0"]]
check(
    "等价样例: 精确判定等价",
    compare(proc(3, ["1", "0", "0"], [2], {"x": M}), proc(3, ["1", "0", "0"], [2], {"x": M}))[
        "equivalent"
    ]
    is True,
)

# 非法输入在核心层即被拒绝
try:
    parse_procedure(
        "A",
        {
            "n": 2,
            "initial": ["1/2", "1/3"],  # 和不为一
            "safe": [1],
            "commands": [
                {"symbol": "x", "rows": [[{"target": 0, "prob": "1"}], [{"target": 1, "prob": "1"}]]}
            ],
        },
    )
    check("核心校验: 初始分布和不为一被拒", False, "未抛错")
except ProcedureValidationError:
    check("核心校验: 初始分布和不为一被拒", True)

# ---------------------------------------------------------------------------
# 1b. 最小安全观测实现审计（核心，精确有理数）
# ---------------------------------------------------------------------------

# 3 态、安全态 {2}：状态 1、2 在命令 a 下行为完全一致（1->0,2->0），
# 初始 (1,0,0) 第一步以 1/2 进入 1、1/2 进入 2，此后不可区分 -> Hankel 秩 2。
REDUCIBLE = [["0", "1/2", "1/2"], ["1", "0", "0"], ["1", "0", "0"]]
red = minimal_realization(proc(3, ["1", "0", "0"], [2], {"a": REDUCIBLE}))
check(
    "审计核心: 冗余状态维数收缩 3 -> 2",
    red["n"] == 3
    and red["reachableRank"] == 2
    and red["observableRank"] == 3
    and red["minimalDimension"] == 2,
    f"n={red['n']} R={red['reachableRank']} O={red['observableRank']} r={red['minimalDimension']}",
)
check(
    "审计核心: 收缩证据含稳定前缀/后缀主元基",
    red["pivotPrefixes"] == ["", "a"] and red["pivotSuffixes"] == ["", "a"],
    f"{red['pivotPrefixes']} / {red['pivotSuffixes']}",
)
# 命令矩阵重构：α A(a)^k β 与 π M(a)^k 1_安全态 对 k=0..6 精确一致
_red_proc = proc(3, ["1", "0", "0"], [2], {"a": REDUCIBLE})
_dist = list(_red_proc.initial)
_vec = list(red["alpha"])
_recon_ok = True
for _ in range(7):
    if sum(_dist[i] for i in _red_proc.safe) != _dot(_vec, red["beta"]):
        _recon_ok = False
        break
    _dist = _row_mul(_dist, _red_proc.matrices["a"], 3)
    _vec = _row_mul(_vec, red["matrices"]["a"], 2)
check("审计核心: 命令矩阵重构回放 k=0..6 精确一致", _recon_ok)
check(
    "审计核心: 逐基回放全部匹配（空串+基对+转移对）",
    red["verified"] is True and all(item["match"] for item in red["replay"]),
)

# 满秩规程：最小维数等于原状态数，仍返回完整证据
_full = minimal_realization(proc(2, ["1", "0"], [1], {"x": [["1/2", "1/2"], ["1/3", "2/3"]]}))
check(
    "审计核心: 满秩时维数相等且证据完整",
    _full["minimalDimension"] == 2
    and len(_full["alpha"]) == 2
    and len(_full["matrices"]["x"]) == 2
    and _full["verified"] is True,
)

# 安全概率恒零：最小维数 0，空模型精确表达零函数
_zero = minimal_realization(
    proc(3, ["1", "0", "0"], [2], {"a": [["0", "1", "0"], ["0", "1", "0"], ["0", "0", "1"]]})
)
check("审计核心: 恒零安全函数最小维数为 0", _zero["minimalDimension"] == 0, str(_zero["minimalDimension"]))

# 非法概率行在审计核心入口（复用原校验）即被拒绝
try:
    minimal_realization(
        proc(2, ["1", "0"], [1], {"x": [["1/2", "0"], ["0", "1"]]})
    )
    check("审计核心: 非法概率行被拒", False, "未抛错")
except ProcedureValidationError:
    check("审计核心: 非法概率行被拒", True)

# ---------------------------------------------------------------------------
# 2. 构建检查
# ---------------------------------------------------------------------------

# 容器内代码位于 /app；本地运行时回退到挂载的仓库路径。
_ROOT = os.environ.get("REPO_ROOT", "/workspace")
_backend = _first_existing("/app/app/main.py", os.path.join(_ROOT, "backend/app/main.py"))
_core = _first_existing("/app/app/equivalence.py", os.path.join(_ROOT, "backend/app/equivalence.py"))
_index = _first_existing("/app/static/index.html", os.path.join(_ROOT, "frontend/index.html"))
_appjs = _first_existing("/app/static/app.js", os.path.join(_ROOT, "frontend/app.js"))

check("构建: 后端入口存在", _backend is not None)
check("构建: 核心模块存在", _core is not None)
check("构建: 前端页面存在", _index is not None)
check("构建: 前端脚本存在", _appjs is not None)
import uvicorn  # noqa: E402,F401

check("构建: uvicorn 可导入", True)

# ---------------------------------------------------------------------------
# 3. API 冒烟
# ---------------------------------------------------------------------------


def http(method: str, path: str, payload=None):
    url = TARGET.rstrip("/") + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


status, body = http("GET", "/healthz")
check("API 冒烟: /healthz 200", status == 200 and body.get("status") == "ok", f"{status} {body}")

pair_eq = {
    "A": {
        "n": 2,
        "initial": ["1", "0"],
        "safe": [1],
        "commands": [
            {
                "symbol": "x",
                "rows": [
                    [{"target": 0, "prob": "1/2"}, {"target": 1, "prob": "1/2"}],
                    [{"target": 0, "prob": "1/3"}, {"target": 1, "prob": "2/3"}],
                ],
            }
        ],
    },
}
pair_eq["B"] = json.loads(json.dumps(pair_eq["A"]))
status, body = http("POST", "/api/review", pair_eq)
check(
    "API 冒烟: 等价对返回 equivalent=true",
    status == 200 and body.get("result", {}).get("equivalent") is True,
    f"{status} {body}",
)

pair_diff = {
    "A": {
        "n": 3,
        "initial": ["1", "0", "0"],
        "safe": [2],
        "commands": [
            {
                "symbol": "a",
                "rows": [
                    [{"target": 0, "prob": "0"}, {"target": 1, "prob": "1"}, {"target": 2, "prob": "0"}],
                    [{"target": 0, "prob": "0"}, {"target": 1, "prob": "1"}, {"target": 2, "prob": "0"}],
                    [{"target": 0, "prob": "0"}, {"target": 1, "prob": "0"}, {"target": 2, "prob": "1"}],
                ],
            }
        ],
    },
    "B": {
        "n": 3,
        "initial": ["1", "0", "0"],
        "safe": [2],
        "commands": [
            {
                "symbol": "a",
                "rows": [
                    [{"target": 0, "prob": "0"}, {"target": 1, "prob": "1"}, {"target": 2, "prob": "0"}],
                    [{"target": 0, "prob": "0"}, {"target": 1, "prob": "0"}, {"target": 2, "prob": "1"}],
                    [{"target": 0, "prob": "0"}, {"target": 1, "prob": "0"}, {"target": 2, "prob": "1"}],
                ],
            }
        ],
    },
}
status, body = http("POST", "/api/review", pair_diff)
result = body.get("result", {})
check("API 冒烟: 反例 200 且 word=aa", status == 200 and result.get("word") == ["a", "a"], str(body))
check(
    "API 冒烟: 概率差为精确分数 {num,den,text}",
    result.get("difference", {}).get("num") == -1
    and result["difference"].get("den") == 1
    and result["difference"].get("text") == "-1",
    str(result.get("difference")),
)
check(
    "API 冒烟: 两侧逐步分布均为精确分数",
    all(
        set(step["distribution"][0].keys()) == {"num", "den", "text"}
        for step in result.get("A", {}).get("steps", [])
    ),
)

pair_bad = json.loads(json.dumps(pair_eq))
pair_bad["A"]["initial"] = ["1/2", "1/3"]  # 和为 5/6
pair_bad["A"]["safe"] = []  # 安全态缺失
pair_bad["B"]["commands"][0]["rows"][0][1]["prob"] = "1/0"  # 非法分数
status, body = http("POST", "/api/review", pair_bad)
errors = body.get("errors", [])
locs = [tuple(e.get("loc", [])) for e in errors]
check("API 冒烟: 非法输入返回 400", status == 400, str(status))
check("API 冒烟: 错误包含可定位 loc", any(len(loc) > 0 for loc in locs), str(locs))
check(
    "API 冒烟: 定位到 initial / safe / 非法分数三处",
    any(loc[:2] == ("A", "initial") for loc in locs)
    and any(loc[:2] == ("A", "safe") for loc in locs)
    and any("prob" in loc for loc in locs),
    str(locs),
)

# --- /api/audit：维数收缩、命令矩阵重构、非法概率行拒绝 --------------------

audit_reducible = {
    "n": 3,
    "initial": ["1", "0", "0"],
    "safe": [2],
    "commands": [
        {
            "symbol": "a",
            "rows": [
                [{"target": 0, "prob": "0"}, {"target": 1, "prob": "1/2"}, {"target": 2, "prob": "1/2"}],
                [{"target": 0, "prob": "1"}, {"target": 1, "prob": "0"}, {"target": 2, "prob": "0"}],
                [{"target": 0, "prob": "1"}, {"target": 1, "prob": "0"}, {"target": 2, "prob": "0"}],
            ],
        }
    ],
}
status, body = http("POST", "/api/audit", {"side": "A", "procedure": audit_reducible})
audit = body.get("audit", {})
check(
    "API 审计: 冗余状态维数收缩 3 -> 2",
    status == 200
    and audit.get("n") == 3
    and audit.get("minimalDimension") == 2
    and audit.get("reduced") is True,
    f"{status} {body}",
)
check(
    "API 审计: 返回基串与初始/终止向量",
    audit.get("pivotPrefixes") == ["", "a"]
    and audit.get("pivotSuffixes") == ["", "a"]
    and len(audit.get("alpha", [])) == 2
    and len(audit.get("beta", [])) == 2,
    str(audit),
)
check(
    "API 审计: 命令矩阵重构为 2x2 精确分数",
    (
        lambda mat: isinstance(mat, list)
        and len(mat) == 2
        and all(len(row) == 2 for row in mat)
        and all(set(f) == {"num", "den", "text"} for row in mat for f in row)
    )(audit.get("matrices", {}).get("a", [])),
    str(audit.get("matrices")),
)
check(
    "API 审计: 基对逐项回放全部精确一致",
    audit.get("verified") is True
    and bool(audit.get("replay"))
    and all(item.get("match") is True for item in audit.get("replay", []))
    # 必须同时包含原规程概率与压缩模型概率两列精确分数
    and all(
        set(item.get("original", {})) == {"num", "den", "text"}
        and set(item.get("compressed", {})) == {"num", "den", "text"}
        for item in audit.get("replay", [])
    ),
    "回放缺失或存在不一致项",
)
check(
    "API 审计: 回放覆盖空串/基对/转移对",
    {item.get("kind") for item in audit.get("replay", [])} == {"empty", "basis", "transition"},
    str({item.get("kind") for item in audit.get("replay", [])}),
)

# 非法概率行（行和 1/2）必须 400，且 loc 定位到 A 侧具体行
audit_bad = json.loads(json.dumps(audit_reducible))
audit_bad["commands"][0]["rows"][0] = [
    {"target": 0, "prob": "1/2"},
    {"target": 1, "prob": "0"},
    {"target": 2, "prob": "0"},
]
status, body = http("POST", "/api/audit", {"side": "A", "procedure": audit_bad})
locs = [tuple(e.get("loc", [])) for e in body.get("errors", [])]
check("API 审计: 非法概率行返回 400", status == 400, str(status))
check(
    "API 审计: 错误 loc 定位到 A 侧命令行",
    any(loc[:4] == ("A", "commands", 0, "rows") for loc in locs),
    str(locs),
)

# side 非法
status, _ = http("POST", "/api/audit", {"side": "C", "procedure": audit_reducible})
check("API 审计: 非法 side 返回 400", status == 400, str(status))

# ---------------------------------------------------------------------------
# 验收结论
# ---------------------------------------------------------------------------

if failures:
    print(f"\nVERIFY FAILED: {len(failures)} 项未通过 -> {failures}")
    sys.exit(1)
print("\nVERIFY PASSED: 最短反例、构建检查、API 冒烟全部通过")
sys.exit(0)
