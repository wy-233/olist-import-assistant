"""FastAPI 应用：上传 CSV → 预览校验 → 人工审批 → 写入 → 报告与查询。

只用于本地 /docs 演示。没有认证：approver 是可自报的字符串，仅用于留痕，不是鉴权。
"""
import sys
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.api.service import MAX_UPLOAD_BYTES, ServiceError, Store  # noqa: E402
from src.db.connection import connect  # noqa: E402

app = FastAPI(
    title="商品目录导入助手（学习 MVP）",
    version="0.5",
    description=(
        "预览阶段只读：不写入 products 与 product_raw。审批通过后才写入，且只写预览确定的新增行。\n\n"
        "审批没有认证，approver 仅用于留痕，不构成权限控制。"
    ),
)


def get_store() -> Store:
    return Store(connect)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from None


class Counts(BaseModel):
    rows_total: int
    rows_inserted: int
    rows_skipped: int
    rows_conflict: int
    rows_rejected: int
    rows_pending: int
    reconciled: bool


class PreviewOut(BaseModel):
    batch_id: int
    file_name: str
    file_sha256: str
    status: str
    counts: Counts
    validation: Dict
    rejected_samples: List[Dict]
    conflict_samples: List[Dict]
    written: Dict[str, int]


class ApproveOut(BaseModel):
    batch_id: int
    approved_by: str
    rows_total: int
    rows_inserted: int
    rows_skipped: int
    rows_conflict: int
    rows_rejected: int
    rows_pending: int
    reconciled: bool


class Check(BaseModel):
    name: str
    actual: int
    expected: int
    ok: bool


class ReportOut(BaseModel):
    batch: Dict
    counts: Dict
    validation: Optional[Dict]
    row_results: Optional[Dict]
    checks: List[Check]
    all_ok: bool


class ProductsOut(BaseModel):
    total: int
    limit: int
    offset: int
    items: List[Dict]


@app.post("/imports/preview", response_model=PreviewOut, summary="上传 CSV 并预览（不写商品表）")
def preview(
    file: UploadFile = File(..., description="UTF-8 的 products CSV（列名与原始文件一致）"),
    store: Store = Depends(get_store),
):
    """解析上传内容并生成导入计划，只写一条 `import_batch` 预览记录，不写商品表。"""
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    return _call(store.preview, file.filename or "upload.csv", content)


@app.post("/imports/{batch_id}/approve", response_model=ApproveOut, summary="人工审批并写入")
def approve(
    batch_id: int,
    approver: str = Form(..., description="审批人标识（仅留痕，不是鉴权）"),
    confirm: str = Form(..., description="确认文本，必须为 `APPROVE <batch_id> <预览新增数>`"),
    store: Store = Depends(get_store),
):
    """审批一个预览批次。批次必须处于 previewed；重复或并发审批返回 409。

    写入前会重新校验文件哈希与计划指纹；预览之后数据变化则返回 409 并要求重新预览。
    """
    return _call(store.approve, batch_id, approver, confirm)


@app.get("/imports/{batch_id}/report", response_model=ReportOut, summary="批次报告与对账检查")
def report(batch_id: int, store: Store = Depends(get_store)):
    """批次元数据、计数与对账检查；数字全部由代码或 SQL 产生。"""
    return _call(store.report, batch_id)


@app.get("/products", response_model=ProductsOut, summary="查询已入库商品（分页）")
def products(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    store: Store = Depends(get_store),
):
    """只读查询 products 表。"""
    return _call(store.list_products, limit, offset)
