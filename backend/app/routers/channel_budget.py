"""价值驱动渠道预算分配接口。

承接「LTV模型用户价值记录 + 预算渠道配置 -> 渠道预算分配明细数据集」新能力：
- GET  /value-records     按首次接入读取的用户价值记录（多选来源，核验码 19ae84df）
- GET  /channel-configs   预算渠道配置
- POST /validate          多选记录逐条核对 + 整组试算（不写库）
- POST /batches           整组提交，原子写入一个完整结果（并发/幂等收敛）
- POST /batches/{no}/continue  失败重试，只续写未完成额度
- POST /batches/{no}/close     闭环并回写 LTV 估算列表（含 LIFE-0003 回归样例）
- GET  /batches           处理批次清单，可按批次核对关联记录
- GET  /batches/{no}      预算详情：渠道额度、ROI目标偏差、预计消耗速率
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.schemas import ActionResult, PageResult
from app.services.channel_budget import BIZ_CODE, ChannelBudgetError, service

router = APIRouter(prefix="/api/channel-budget", tags=["价值驱动渠道预算分配"])


class SubmitPayload(BaseModel):
    record_ids: list[int] = Field(default_factory=list, description="多选的LTV用户价值记录ID")
    total_budget: float = Field(..., gt=0, description="预算总额")
    idem_key: str | None = Field(default=None, description="幂等键：重复登记收敛为同一数据集")
    operator: str | None = Field(default=None)
    simulate_failure: bool = Field(default=False, description="演练用：仅前两行落账，验证重试续写")


class ContinuePayload(BaseModel):
    remark: str | None = None


@router.get("/value-records", response_model=PageResult[dict])
def list_value_records(
    channel: str | None = Query(default=None, description="按渠道过滤"),
    model_no: str | None = Query(default=None, description="按LTV模型编号过滤"),
    estimate_status: str | None = Query(default=None, description="按估算状态过滤"),
    keyword: str | None = Query(default=None),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """读取按首次接入沉淀的 LTV 用户价值记录，供预算分配多选。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_value_records(
        channel=channel,
        model_no=model_no,
        estimate_status=estimate_status,
        keyword=keyword,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/channel-configs")
def list_channel_configs() -> dict[str, Any]:
    """预算渠道配置：渠道比例、ROI目标、日消耗上限。"""
    return {"biz_code": BIZ_CODE, "items": service.list_channel_configs()}


@router.post("/validate")
def validate_selection(payload: SubmitPayload) -> dict[str, Any]:
    """逐条核对多选记录（渠道比例、LTV版本、ROI目标偏差），通过后返回整组试算。"""
    try:
        return service.validate_selection(payload.record_ids, payload.total_budget)
    except ChannelBudgetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/batches", response_model=ActionResult)
def create_batch(payload: SubmitPayload) -> ActionResult:
    """整组提交：预算总额、渠道比例、LTV版本在同一写入中完成。

    并发提交只写入一个完整结果；重复登记收敛为单个已闭环/既有明细数据集。
    """
    try:
        result = service.create_batch(
            payload.record_ids,
            payload.total_budget,
            idem_key=payload.idem_key,
            operator=payload.operator or "值班管理员",
            simulate_failure=payload.simulate_failure,
        )
    except ChannelBudgetError as exc:
        return ActionResult(ok=False, message=str(exc))
    batch = result["batch"]
    if result.get("converged"):
        return ActionResult(ok=True, message=result["message"], entry=batch)
    return ActionResult(ok=True, message=result["message"], entry=batch)


@router.post("/batches/{batch_no}/continue", response_model=ActionResult)
def continue_batch(batch_no: str, payload: ContinuePayload | None = None) -> ActionResult:
    """请求失败后的重试：只续写未完成额度，已写入额度不重复落账。"""
    try:
        result = service.continue_batch(batch_no)
    except ChannelBudgetError as exc:
        return ActionResult(ok=False, message=str(exc))
    return ActionResult(ok=True, message=result["message"], entry=result["batch"])


@router.post("/batches/{batch_no}/close", response_model=ActionResult)
def close_batch(batch_no: str, payload: ContinuePayload | None = None) -> ActionResult:
    """闭环为单个已闭环渠道预算分配明细数据集，并回写 LTV 估算列表。"""
    try:
        result = service.close_batch(batch_no)
    except ChannelBudgetError as exc:
        return ActionResult(ok=False, message=str(exc))
    return ActionResult(ok=True, message=result["message"], entry=result["batch"])


@router.get("/batches", response_model=PageResult[dict])
def list_batches(
    keyword: str | None = Query(default=None, description="按批次编号/批次资料号检索"),
    status: str | None = Query(default=None),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """处理批次清单：新增LTV价值分层驱动的预算渠道分配按批次核对。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_batches(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/batches/{batch_no}")
def get_batch(batch_no: str) -> dict[str, Any]:
    """预算详情：读取同一 LTV 版本下的渠道额度、ROI目标偏差与预计消耗速率。"""
    batch = service.get_batch(batch_no)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"预算批次 {batch_no} 不存在或已归档")
    lines = service.list_lines(batch_no)
    return {
        "biz_code": BIZ_CODE,
        "batch": batch,
        "lines": lines,
        "checks": {
            "渠道比例核对": batch.get("渠道比例"),
            "LTV版本核对": batch.get("LTV版本"),
            "ROI目标偏差": [
                {
                    "渠道": line["渠道"],
                    "ROI目标": line["ROI目标"],
                    "预计ROI": line["预计ROI"],
                    "ROI目标偏差": line["ROI目标偏差"],
                    "ROI目标偏差率": line["ROI目标偏差率"],
                }
                for line in lines
            ],
        },
    }


@router.get("/export")
def export_batches() -> dict[str, Any]:
    """导出渠道预算分配明细数据集全量清单。"""
    items, total = service.list_batches(page=1, size=10000)
    return {"module": "channel-budget", "biz_code": BIZ_CODE, "total": total, "items": items}
