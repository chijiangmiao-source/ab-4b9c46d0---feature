#!/usr/bin/env python3
"""Compose verify 服务入口。

验收四件事，全部通过才以退出码 0 结束：

1. 最短概率反例：直接在镜像内调用精确有理数核心，断言
   - 差异只在长度 2 暴露时给出最短反例 "aa"；
   - 同长度按 ASCII 字典序取最小；
   - 等价样例判定为等价；
   - 概率和不为一 / 非法分数被拒绝。
2. 最小安全观测实现审计（核心层）：冗余状态维数收缩、命令矩阵重构、
   同安全标记状态不被直接合并、基对回放逐项精确相等。
3. 检查构建：镜像内应用代码、静态页面与 uvicorn 均就位。
4. API 冒烟：对运行中的服务打 /healthz、/api/review（等价、反例、非法输入）
   与 /api/audit（维数收缩、完整证据、非法概率行拒绝），
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
from app.minimization import minimal_realization  # noqa: E402

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
# 2. 最小安全观测实现审计（核心，精确有理数 Hankel 秩）
# ---------------------------------------------------------------------------

# 2a. 冗余状态维数收缩：状态 0/1 转移行完全相同，3 态压到 2 维
M_DUP = [
    ["1/3", "1/3", "1/3"],
    ["1/3", "1/3", "1/3"],
    ["1/6", "1/3", "1/2"],
]
ar = minimal_realization(proc(3, ["1/2", "1/2", "0"], [2], {"a": M_DUP}))
check("审计核心: 冗余 3 态收缩到最小维数 2", ar["minimalDim"] == 2, str(ar["minimalDim"]))
check(
    "审计核心: 收缩来自 Hankel 配对而非行空间",
    ar["reachableDim"] == 3 and ar["observableDim"] == 2,
    f"{ar['reachableDim']}/{ar['observableDim']}",
)
check("审计核心: 冗余维数为 1", ar["redundantStates"] == 1, str(ar["redundantStates"]))
check(
    "审计核心: 稳定前缀/后缀基 ['', 'a']",
    ar["prefixWords"] == ["", "a"] and ar["suffixWords"] == ["", "a"],
    f"{ar['prefixWords']} / {ar['suffixWords']}",
)
# 命令矩阵重构：含负数 -1/6，证明压缩模型不是随机控制器
mu_a = ar["matrices"]["a"]
check(
    "审计核心: 命令矩阵重构含非随机元 -1/6",
    mu_a[1][0] == Fraction(-1, 6),
    str(mu_a),
)
check(
    "审计核心: 基对回放逐项精确相等（4 对）",
    len(ar["replay"]) == 4 and all(x["original"] == x["compressed"] for x in ar["replay"]),
    str([(x["word"], x["original"], x["compressed"]) for x in ar["replay"]]),
)

# 2b. 同安全标记的状态（1、2 皆安全）未来行为不同，绝不可直接合并
M_LABEL = [["0", "1/2", "1/2"], ["0", "1", "0"], ["1", "0", "0"]]
lr = minimal_realization(proc(3, ["1", "0", "0"], [1, 2], {"a": M_LABEL}))
check(
    "审计核心: 同安全标记但行为不同的状态不合并（维数保持 3）",
    lr["minimalDim"] == 3,
    str(lr["minimalDim"]),
)

# 2c. 最小维数等于原状态数时仍返回完整证据（链 3 态，矩阵重构为原矩阵）
M_CHAIN = [["0", "1", "0"], ["0", "0", "1"], ["0", "0", "1"]]
cr = minimal_realization(proc(3, ["1", "0", "0"], [2], {"a": M_CHAIN}))
check("审计核心: 满秩规程维数保持 3", cr["minimalDim"] == 3, str(cr["minimalDim"]))
check(
    "审计核心: 满秩时命令矩阵重构为原矩阵",
    cr["matrices"]["a"] == [[Fraction(x) for x in row] for row in M_CHAIN],
    str(cr["matrices"]["a"]),
)
check(
    "审计核心: 满秩时仍返回 9 项基对回放证据",
    len(cr["replay"]) == 9 and all(x["original"] == x["compressed"] for x in cr["replay"]),
)

# 2d. 安全态不可达 => 零维实现
zr = minimal_realization(proc(2, ["1", "0"], [1], {"a": [["1", "0"], ["0", "1"]]}))
check(
    "审计核心: 不可达安全态给出 0 维实现",
    zr["minimalDim"] == 0 and zr["matrices"]["a"] == [] and zr["replay"] == [],
    str(zr["minimalDim"]),
)

# ---------------------------------------------------------------------------
# 3. 构建检查
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
# 4. API 冒烟
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

# /api/audit 冒烟：冗余状态维数收缩、命令矩阵重构、回放核对
audit_reducible = {
    "side": "A",
    "A": {
        "n": 3,
        "initial": ["1/2", "1/2", "0"],
        "safe": [2],
        "commands": [
            {
                "symbol": "a",
                "rows": [
                    [{"target": 0, "prob": "1/3"}, {"target": 1, "prob": "1/3"}, {"target": 2, "prob": "1/3"}],
                    [{"target": 0, "prob": "1/3"}, {"target": 1, "prob": "1/3"}, {"target": 2, "prob": "1/3"}],
                    [{"target": 0, "prob": "1/6"}, {"target": 1, "prob": "1/3"}, {"target": 2, "prob": "1/2"}],
                ],
            }
        ],
    },
}
status, body = http("POST", "/api/audit", audit_reducible)
audit = body.get("result", {})
check("API 冒烟: 审计 200", status == 200, f"{status} {body}")
check(
    "API 冒烟: 冗余状态维数收缩 3 -> 2",
    audit.get("n") == 3 and audit.get("minimalDim") == 2 and audit.get("redundantStates") == 1,
    str(audit),
)
check(
    "API 冒烟: 稳定前缀/后缀基与压缩标记",
    audit.get("prefixWords") == ["", "a"]
    and audit.get("suffixWords") == ["", "a"]
    and audit.get("compressed") is True,
    str(audit),
)
check(
    "API 冒烟: 命令矩阵重构为含 -1/6 的非随机矩阵",
    audit.get("matrices", {}).get("a", [[], []])[1][0]
    == {"num": -1, "den": 6, "text": "-1/6"},
    str(audit.get("matrices")),
)
check(
    "API 冒烟: 初始/终止向量为精确分数",
    all(set(f) == {"num", "den", "text"} for f in audit.get("initial", []))
    and all(set(f) == {"num", "den", "text"} for f in audit.get("terminal", [])),
)
check(
    "API 冒烟: 基对逐项回放且原/压缩概率精确相等",
    len(audit.get("replay", [])) == 4
    and all(item.get("original") == item.get("compressed") for item in audit.get("replay", [])),
    str(audit.get("replay")),
)

# 满秩规程：维数相等也必须返回完整证据
audit_full = {
    "side": "B",
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
status, body = http("POST", "/api/audit", audit_full)
audit = body.get("result", {})
check(
    "API 冒烟: 满秩审计维数相等且证据完整",
    status == 200
    and audit.get("minimalDim") == 3
    and audit.get("compressed") is False
    and len(audit.get("hankel", [])) == 3
    and len(audit.get("replay", [])) == 9
    and all(item.get("original") == item.get("compressed") for item in audit.get("replay", [])),
    f"{status} {audit}",
)

# 非法概率行（行和 5/6）必须被拒绝，且不返回压缩结论
audit_bad = json.loads(json.dumps(audit_reducible))
audit_bad["A"]["commands"][0]["rows"][0][1]["prob"] = "1/4"  # 行和变 1/3+1/4+1/3 = 11/12
status, body = http("POST", "/api/audit", audit_bad)
check("API 冒烟: 非法概率行审计返回 400", status == 400, str(status))
check("API 冒烟: 拒绝时不返回压缩结论", "result" not in body, str(body))
check(
    "API 冒烟: 拒绝原因可定位到概率行",
    any(e.get("loc", [""])[0] == "A" for e in body.get("errors", []))
    and any("转出概率和必须为 1" in e.get("msg", "") for e in body.get("errors", [])),
    str(body.get("errors")),
)

# ---------------------------------------------------------------------------
# 验收结论
# ---------------------------------------------------------------------------

if failures:
    print(f"\nVERIFY FAILED: {len(failures)} 项未通过 -> {failures}")
    sys.exit(1)
print("\nVERIFY PASSED: 最短反例、审计压缩、构建检查、API 冒烟全部通过")
sys.exit(0)
