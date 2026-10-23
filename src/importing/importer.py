"""把校验后的商品目录写入 MySQL。

流程：写 batch(running) -> 原始层全量落库 -> 读已有商品 -> planner 三分类
      -> 插入新商品 -> 回填每行 import_result -> 回填 batch 计数（单事务）。
"""
from datetime import datetime

from src.importing.planner import (
    CONFLICT, INSERTED, SKIPPED, plan_fingerprint, plan_import, summarize_plan,
)

BATCH_SIZE = 500

RAW_COLS = (
    "product_id", "category_name", "name_length", "description_length", "photos_qty",
    "weight_g", "length_cm", "height_cm", "width_cm",
)
# 原始层列名 -> CSV 列名
RAW_SOURCE = {
    "product_id": "product_id",
    "category_name": "product_category_name",
    "name_length": "product_name_lenght",
    "description_length": "product_description_lenght",
    "photos_qty": "product_photos_qty",
    "weight_g": "product_weight_g",
    "length_cm": "product_length_cm",
    "height_cm": "product_height_cm",
    "width_cm": "product_width_cm",
}
STANDARD_INSERT = (
    "INSERT INTO products (product_id, category_name, name_length, description_length, photos_qty,"
    " weight_g, length_cm, height_cm, width_cm, source_batch_id, source_line, created_at)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


def load_existing(cursor):
    cursor.execute(
        "SELECT product_id, category_name, name_length, description_length, photos_qty,"
        " weight_g, length_cm, height_cm, width_cm FROM products"
    )
    return {row[0]: dict(zip(("category_name", "name_length", "description_length", "photos_qty",
                              "weight_g", "length_cm", "height_cm", "width_cm"), row[1:]))
            for row in cursor.fetchall()}


def insert_raw(cursor, batch_id, results):
    sql = ("INSERT INTO product_raw (batch_id, csv_line, product_id, category_name, name_length,"
           " description_length, photos_qty, weight_g, length_cm, height_cm, width_cm, status, reason_codes)"
           " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)")
    rows = [
        (batch_id, r.csv_line, *[r.raw.get(RAW_SOURCE[c]) for c in RAW_COLS],
         r.status, ",".join(r.reasons))
        for r in results
    ]
    for i in range(0, len(rows), BATCH_SIZE):
        cursor.executemany(sql, rows[i:i + BATCH_SIZE])


def set_import_results(cursor, batch_id, to_insert, skipped, conflicts):
    """按 csv_line 回填每行的导入结果。"""
    marks = [(INSERTED, r.csv_line) for r, _ in to_insert]
    marks += [(SKIPPED, line) for _, line in skipped]
    marks += [(CONFLICT, line) for _, line in conflicts]
    sql = "UPDATE product_raw SET import_result = %s WHERE batch_id = %s AND csv_line = %s"
    params = [(value, batch_id, line) for value, line in marks]
    for i in range(0, len(params), BATCH_SIZE):
        cursor.executemany(sql, params[i:i + BATCH_SIZE])


class PlanChanged(Exception):
    """审批时重算的计划与预览时的计划指纹不一致（文件之外的数据已变化）。"""


def apply_to_batch(cursor, batch_id, results, expected_fingerprint=None):
    """在已存在的批次上执行写入，不提交事务；调用方负责 commit / rollback。

    给定 expected_fingerprint 时，先重算计划并比对，不一致则抛 PlanChanged，此时尚未写入任何行。
    """
    rejected = sum(1 for r in results if r.status == "rejected")
    existing = load_existing(cursor)
    to_insert, skipped, conflicts = plan_import(results, existing)
    if expected_fingerprint is not None and (
        plan_fingerprint(to_insert, skipped, conflicts, rejected) != expected_fingerprint
    ):
        raise PlanChanged()

    insert_raw(cursor, batch_id, results)

    now = datetime.now()
    rows = [
        (standard["product_id"], standard["category_name"], standard["name_length"],
         standard["description_length"], standard["photos_qty"], standard["weight_g"],
         standard["length_cm"], standard["height_cm"], standard["width_cm"],
         batch_id, r.csv_line, now)
        for r, standard in to_insert
    ]
    for i in range(0, len(rows), BATCH_SIZE):
        cursor.executemany(STANDARD_INSERT, rows[i:i + BATCH_SIZE])

    set_import_results(cursor, batch_id, to_insert, skipped, conflicts)
    stats = summarize_plan(to_insert, skipped, conflicts, rejected)
    cursor.execute(
        "UPDATE import_batch SET status='succeeded', rows_inserted=%s, rows_skipped=%s,"
        " rows_conflict=%s, rows_rejected=%s, rows_pending=%s, finished_at=%s WHERE batch_id=%s",
        (stats["rows_inserted"], stats["rows_skipped"], stats["rows_conflict"],
         stats["rows_rejected"], stats["rows_pending"], datetime.now(), batch_id),
    )
    stats["batch_id"] = batch_id
    return stats


def import_products(results, file_name, file_sha256, conn, mode="apply"):
    """返回批次统计字典。mode='dry_run' 时只计算、不写库。"""
    rejected = sum(1 for r in results if r.status == "rejected")
    cursor = conn.cursor()
    try:
        if mode == "dry_run":
            existing = load_existing(cursor)
            to_insert, skipped, conflicts = plan_import(results, existing)
            return summarize_plan(to_insert, skipped, conflicts, rejected)

        cursor.execute(
            "INSERT INTO import_batch (file_name, file_sha256, mode, status, rows_total, started_at)"
            " VALUES (%s, %s, 'apply', 'running', %s, %s)",
            (file_name, file_sha256, len(results), datetime.now()),
        )
        stats = apply_to_batch(cursor, cursor.lastrowid, results)
        conn.commit()
        return stats
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
