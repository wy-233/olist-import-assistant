"""AI 审核接口：只读批次数据，只写 ai_review_log；不改变批次状态，不写商品表，不审批。"""
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.ai.client import DeepSeekClient, ModelNotConfigured
from src.ai.review import ReviewError, run_review
from src.db.connection import connect

router = APIRouter()


def get_connect():
    return connect


def get_model_client():
    try:
        return DeepSeekClient.from_env()
    except ModelNotConfigured as exc:
        raise HTTPException(status_code=503,
                            detail={"code": "ai_not_configured", "message": str(exc)}) from None


class ReviewEntry(BaseModel):
    log_id: int
    kind: str
    model_name: str
    guard_verdict: str
    violations: List[str]
    result: Optional[Dict]


class AiReviewOut(BaseModel):
    batch_id: int
    note: str
    entries: List[ReviewEntry]


@router.post("/imports/{batch_id}/ai-review", response_model=AiReviewOut,
             summary="AI 异常解释与字段映射建议（只记日志）")
def ai_review(batch_id: int, connect_fn=Depends(get_connect), client=Depends(get_model_client)):
    """把本批的表头、计数和最多 5 条拒绝样本发给模型，输出先过守卫再写入 ai_review_log。

    不修改任何商品数据、不改变批次状态、不审批；被守卫拦截的输出只留在日志里。
    """
    try:
        entries = run_review(connect_fn, client, batch_id)
    except ReviewError as exc:
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from None
    return {
        "batch_id": batch_id,
        "note": "AI 输出仅为建议，已记入 ai_review_log；未修改商品数据，也未批准任何入库。",
        "entries": entries,
    }
