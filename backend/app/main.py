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
from .realization import minimal_realization


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


def _serialize_audit(result: dict[str, Any]) -> dict[str, Any]:
    """最小实现结果序列化：所有数值均为精确分数对象，绝不使用浮点。"""
    r = result["minimalDimension"]

    def frac_row(row: list[Fraction]) -> list[dict[str, Any]]:
        return [_fraction(x) for x in row]

    replay = []
    for item in result["replay"]:
        word = item["prefix"] + item["middle"] + item["suffix"]
        replay.append(
            {
                "kind": item["kind"],
                "prefix": item["prefix"],
                "middle": item["middle"],
                "suffix": item["suffix"],
                "pivot": item["pivot"],
                "word": word,
                "wordLength": len(word),
                "original": _fraction(item["original"]),
                "compressed": _fraction(item["compressed"]),
                "match": item["match"],
            }
        )

    return {
        "n": result["n"],
        "reachableRank": result["reachableRank"],
        "observableRank": result["observableRank"],
        "minimalDimension": r,
        "reduced": r < result["n"],
        "prefixBasis": result["prefixBasis"],
        "suffixBasis": result["suffixBasis"],
        "pivotPrefixes": result["pivotPrefixes"],
        "pivotSuffixes": result["pivotSuffixes"],
        "alpha": [_fraction(x) for x in result["alpha"]],
        "beta": [_fraction(x) for x in result["beta"]],
        "matrices": {
            symbol: [frac_row(row) for row in mat]
            for symbol, mat in result["matrices"].items()
        },
        "replay": replay,
        "verified": result["verified"],
    }


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
    """对单份规程（A 或 B）做最小安全观测实现审计。

    请求体：``{"side": "A"|"B", "procedure": <规程>}``。
    复用双规程复核的**原有校验**（同一 parse_procedure、同一 loc 约定）；
    校验不通过时同样返回 400 与错误表，不产出任何压缩结论。
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "errors": [{"loc": [], "msg": "请求体不是合法 JSON"}]},
        )
    if (
        not isinstance(payload, dict)
        or payload.get("side") not in ("A", "B")
        or not isinstance(payload.get("procedure"), dict)
    ):
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "errors": [{"loc": [], "msg": "请求体必须是 {side: 'A'|'B', procedure: {...}}"}],
            },
        )

    side = payload["side"]
    try:
        proc = parse_procedure(side, payload["procedure"])
    except ProcedureValidationError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "errors": exc.errors})

    result = minimal_realization(proc)
    return JSONResponse(
        status_code=200, content={"ok": True, "side": side, "audit": _serialize_audit(result)}
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(Path(STATIC_DIR) / "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
