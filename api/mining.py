"""因子挖掘 API（独立功能）：触发挖掘任务、查询进度、候选列表、采纳入库/忽略、增量重估。

挖掘出的候选默认停留在候选池（status=pending），由用户逐条审阅后通过
/accept 保存为正式因子（factors 表，source=mining）或 /ignore 忽略 —— 不自动入库。
"""

from __future__ import annotations

from typing import Any

import asyncio

import pandas as pd

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import get_app_state
from backtest_store.repository import get_repository
from factor_mining.miner import MiningJob, incremental_review
from models.factor import FactorCreate

router = APIRouter(prefix="/api/mining", tags=["因子挖掘"])


class MiningRunRequest(BaseModel):
    symbol: str = "EURUSD"
    timeframe: str = "M15"
    max_candidates: int = 2000
    include_structures: bool = True
    date_from: str | None = None  # 可选：起始时间，如 2023-01-01 或 2023-01-01 00:00（留空=最近 3000 根）
    date_to: str | None = None    # 可选：结束时间（留空=今天）
    keep_ungated: bool = False    # True=未达收益门槛候选也入库（gate=ungated 供审阅）；False=只收达门槛候选
    spread_points: float = 10.0   # 点差成本（价格点，10 点 = 1 pip；0=不计点差）


class MiningCandidateRequest(BaseModel):
    candidate_id: str


@router.post("/run")
async def run_mining(req: MiningRunRequest, state=Depends(get_app_state)) -> dict[str, Any]:
    current = state.mining_job
    if current is not None and (current.status or {}).get("running"):
        return {"ok": False, "message": "已有挖掘任务进行中，请等待完成"}

    def _parse_dt(value: str | None, label: str):
        if value is None or not str(value).strip():
            return None
        try:
            return pd.to_datetime(str(value).strip()).to_pydatetime()
        except Exception:
            raise ValueError(f"{label}格式无效，示例：2023-01-01 或 2023-01-01 00:00")

    try:
        date_from = _parse_dt(req.date_from, "起始时间")
        date_to = _parse_dt(req.date_to, "结束时间")
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    if date_from and date_to and date_from >= date_to:
        return {"ok": False, "message": "起始时间必须早于结束时间"}

    job = MiningJob(
        symbol=req.symbol,
        timeframe=req.timeframe,
        repository=state.repository,
        gateway=state.mt5_gateway,
        max_candidates=req.max_candidates,
        include_structures=req.include_structures,
        sltp_policy=state.sltp_policy.to_dict() if state.sltp_policy else None,
        date_from=date_from,
        date_to=date_to,
        keep_ungated=req.keep_ungated,
        spread_points=req.spread_points,
    )
    state.mining_job = job
    asyncio.create_task(job.run())
    return {"ok": True, "message": "挖掘任务已启动", "job": job.status}


@router.post("/pause")
async def pause_mining(state=Depends(get_app_state)) -> dict[str, Any]:
    """暂停挖掘（候选边界挂起；继续后接着跑，已评估成果不丢）。"""
    job = state.mining_job
    if job is None or not (job.status or {}).get("running"):
        return {"ok": False, "message": "当前没有正在运行的挖掘任务"}
    job.paused = True
    return {"ok": True, "message": "已暂停：当前候选评估完成后挂起（可点「继续」恢复）"}


@router.post("/resume")
async def resume_mining(state=Depends(get_app_state)) -> dict[str, Any]:
    job = state.mining_job
    if job is None or not (job.status or {}).get("running"):
        return {"ok": False, "message": "当前没有正在运行的挖掘任务"}
    job.paused = False
    return {"ok": True, "message": "已继续挖掘"}


@router.post("/stop")
async def stop_mining(state=Depends(get_app_state)) -> dict[str, Any]:
    """停止挖掘：当前候选完成后终止，已评估候选保存入库（不丢弃）。"""
    job = state.mining_job
    if job is None or not (job.status or {}).get("running"):
        return {"ok": False, "message": "当前没有正在运行的挖掘任务"}
    job.paused = False
    job.cancelled = True
    return {"ok": True, "message": "停止请求已发送：当前候选完成后停止，已评估候选将保留入库"}


@router.get("/status")
async def mining_status(state=Depends(get_app_state)) -> dict[str, Any]:
    job = state.mining_job
    if job is None:
        return {"running": False, "message": "暂无挖掘任务", "total": 0, "done": 0, "ok": 0}
    status = job.status
    status["pending_count"] = state.repository.count_mining_candidates(status="pending")
    return status


@router.get("/candidates")
async def list_candidates(
    status: str = "pending",
    symbol: str | None = None,
    kind: str | None = None,
    limit: int = 50,
    state=Depends(get_app_state),
) -> dict[str, Any]:
    items = state.repository.list_mining_candidates(
        status=status or None, symbol=symbol or None, limit=limit, kind=kind or None
    )
    return {"count": len(items), "items": items}


@router.post("/accept")
async def accept_candidate(req: MiningCandidateRequest, state=Depends(get_app_state)) -> dict[str, Any]:
    """采纳入库：候选 → 正式因子（source=mining），需用户明确操作（不自动保存）。"""
    cand = state.repository.get_mining_candidate(req.candidate_id)
    if not cand:
        return {"ok": False, "message": "候选不存在"}
    if cand["status"] != "pending":
        return {"ok": False, "message": f"候选状态为 {cand['status']}，不能重复保存"}

    groups = cand.get("groups") or {}
    dominant = ""
    best = -1.0
    for tag, g in groups.items():
        gic = float(g.get("ic") or -1.0)
        if gic > best:
            best = gic
            dominant = tag
    params_text = "，".join(f"{k}={v}" for k, v in sorted((cand.get("params") or {}).items()))
    factor = state.repository.create_factor(
        FactorCreate(
            name=f"{cand['name']}（{cand['kind']}/{cand['variant']}）",
            description=(
                f"因子挖掘引擎产出 · 模板 {cand['template']} · 参数 {params_text} · "
                f"信号数 {cand.get('signal_bars') or 0} · 行情标签 {dominant or '未分组'}"
            ),
            code=cand["code"],
            symbol=cand.get("symbol") or "EURUSD",
            timeframe=cand.get("timeframe") or "M15",
            source="mining",
            model="mining",
            params=cand.get("params") or {},
            tags=[cand.get("kind") or "", cand.get("template") or "", dominant],
            backtest_stats=cand.get("backtest") or {},
            market_adapt={"mining_tags": list(groups.keys()) or [], "dominant": dominant, "source": "mining"},
        )
    )
    state.repository.update_mining_status(req.candidate_id, "saved", factor.id)
    return {"ok": True, "message": f"已保存为正式因子：{factor.name}", "factor_id": factor.id}


@router.post("/ignore")
async def ignore_candidate(req: MiningCandidateRequest, state=Depends(get_app_state)) -> dict[str, Any]:
    cand = state.repository.get_mining_candidate(req.candidate_id)
    if not cand:
        return {"ok": False, "message": "候选不存在"}
    state.repository.update_mining_status(req.candidate_id, "ignored")
    return {"ok": True, "message": "已忽略该候选"}


@router.post("/review")
async def review_candidates(state=Depends(get_app_state)) -> dict[str, Any]:
    """增量重估入口（首版占位）：对已保存的挖掘因子按新数据重新评估，供后续衰减标记。"""
    return await incremental_review(state.repository, state.mt5_gateway)