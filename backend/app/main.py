"""FastAPI 服务：声学应急控制器规程等价性复核。"""

from __future__ import annotations

import os
from fractions import Fraction
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .equivalence import ProcedureValidationError, compare, parse_procedure
from .minimization import minimal_realization


def _static_dir() -> str:
    configured = os.environ.get("STATIC_DIR")
    candidates = [
        configured,
        "/app/static",
        str(Path(__file__).resolve().parents[2] / "frontend"),
    ]
    for path in candidates:
        if path and Path(path).is_dir():
            return path
    return "/app/static"  # 保留默认，交由 StaticFiles 报出明确错误


STATIC_DIR = _static_dir()

app = FastAPI(title="声学规程等价性复核", version="1.0.0")


def _fraction(value: Fraction) -> dict[str, Any]:
    """有理数序列化：同时给出精确分子/分母与分数串，绝不使用浮点。"""
    return {"num": value.numerator, "den": value.denominator, "text": str(value)}


def _frac_vec(vec: list[Fraction]) -> list[dict[str, Any]]:
    return [_fraction(x) for x in vec]


def _frac_mat(mat: list[list[Fraction]]) -> list[list[dict[str, Any]]]:
    return [[_fraction(x) for x in row] for row in mat]


def _serialize_audit(side: str, result: dict[str, Any]) -> dict[str, Any]:
    """最小安全观测实现审计结果序列化（全部精确分数）。"""
    return {
        "side": side,
        "n": result["n"],
        "reachableDim": result["reachableDim"],
        "observableDim": result["observableDim"],
        "minimalDim": result["minimalDim"],
        "redundantStates": result["redundantStates"],
        "compressed": result["minimalDim"] < result["n"],
        "prefixWords": result["prefixWords"],
        "suffixWords": result["suffixWords"],
        "hankel": _frac_mat(result["hankel"]),
        "initial": _frac_vec(result["initial"]),
        "terminal": _frac_vec(result["terminal"]),
        "matrices": {c: _frac_mat(m) for c, m in result["matrices"].items()},
        "replay": [
            {
                "prefixIndex": item["prefixIndex"],
                "suffixIndex": item["suffixIndex"],
                "prefix": item["prefix"],
                "suffix": item["suffix"],
                "word": item["word"],
                "original": _fraction(item["original"]),
                "compressed": _fraction(item["compressed"]),
            }
            for item in result["replay"]
        ],
    }


def _serialize(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("equivalent"):
        return {"equivalent": True}
    out: dict[str, Any] = {
        "equivalent": False,
        "word": result["word"],
        "wordLength": result["wordLength"],
        "difference": _fraction(result["difference"]),
    }
    for side in ("A", "B"):
        trace = result[side]
        out[side] = {
            "final": _fraction(trace["final"]),
            "steps": [
                {
                    "distribution": [_fraction(x) for x in step["distribution"]],
                    "safeProb": _fraction(step["safeProb"]),
                }
                for step in trace["steps"]
            ],
        }
    return out


@app.post("/api/review")
async def review(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "errors": [{"loc": [], "msg": "请求体不是合法 JSON"}]},
        )
    if not isinstance(payload, dict) or not isinstance(payload.get("A"), dict) or not isinstance(
        payload.get("B"), dict
    ):
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "errors": [{"loc": [], "msg": "请求体必须是包含 A、B 两份规程的对象"}],
            },
        )

    errors: list[dict[str, Any]] = []
    proc_a = proc_b = None
    try:
        proc_a = parse_procedure("A", payload["A"])
    except ProcedureValidationError as exc:
        errors.extend(exc.errors)
    try:
        proc_b = parse_procedure("B", payload["B"])
    except ProcedureValidationError as exc:
        errors.extend(exc.errors)

    if proc_a is None or proc_b is None:
        return JSONResponse(status_code=400, content={"ok": False, "errors": errors})

    try:
        result = compare(proc_a, proc_b)
    except ProcedureValidationError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "errors": exc.errors})

    return JSONResponse(status_code=200, content={"ok": True, "result": _serialize(result)})


@app.post("/api/audit")
async def audit(request: Request) -> JSONResponse:
    """最小安全观测实现审计：对 A 或 B 单侧规程构造 Hankel 最小线性实现。

    复用与 /api/review 完全相同的录入校验；校验失败返回 400 与带 loc
    的错误表，不产生任何压缩结论。
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "errors": [{"loc": [], "msg": "请求体不是合法 JSON"}]},
        )
    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "errors": [{"loc": [], "msg": "请求体必须是对象"}]},
        )
    side = payload.get("side")
    if side not in ("A", "B"):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "errors": [{"loc": ["side"], "msg": "side 必须是 \"A\" 或 \"B\""}]},
        )
    raw = payload.get(side)
    if not isinstance(raw, dict):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "errors": [{"loc": [side], "msg": f"缺少规程 {side} 的录入数据"}]},
        )

    try:
        proc = parse_procedure(side, raw)
    except ProcedureValidationError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "errors": exc.errors})

    result = minimal_realization(proc)
    return JSONResponse(status_code=200, content={"ok": True, "result": _serialize_audit(side, result)})


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(Path(STATIC_DIR) / "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
