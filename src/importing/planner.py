"""导入计划的纯逻辑：输入校验结果和数据库中已存在的商品，输出三分类计划。

不连接数据库、不读写文件，全部是纯函数，便于在没有 MySQL 的环境下测试。
标准层字段名取自 docs/data-dictionary.md 的“标准字段名”列。
"""
import hashlib
import json

from src.validation.validator import COL_CATEGORY, COL_ID

INSERTED = "inserted"
SKIPPED = "skipped"
CONFLICT = "conflict"

# 原始列名 -> 标准字段名
NUMERIC_MAP = {
    "product_name_lenght": "name_length",
    "product_description_lenght": "description_length",
    "product_photos_qty": "photos_qty",
    "product_weight_g": "weight_g",
    "product_length_cm": "length_cm",
    "product_height_cm": "height_cm",
    "product_width_cm": "width_cm",
}
CATEGORY_STANDARD = "category_name"
# 参与“相同/冲突”判定的列：不含 product_id（它是身份键，不是内容）
COMPARED_COLS = (CATEGORY_STANDARD, *NUMERIC_MAP.values())


def to_standard(row):
    """把原始行转成标准层的值。只解析，不改值、不补值；空串统一为 None。"""
    standard = {
        "product_id": (row.get(COL_ID) or "").strip(),
        CATEGORY_STANDARD: (row.get(COL_CATEGORY) or "").strip(),
    }
    for raw_col, std_col in NUMERIC_MAP.items():
        text = row.get(raw_col)
        standard[std_col] = None if text is None or text.strip() == "" else int(text.strip())
    return standard


def _same(existing, standard):
    """NULL-safe 比较：内容列逐一相等才算相同。"""
    return all(existing.get(col) == standard.get(col) for col in COMPARED_COLS)


def plan_import(results, existing):
    """results: 校验结果列表；existing: {product_id: {标准列: 值}}。

    返回 (to_insert, skipped, conflicts)：
    - to_insert: [(RowResult, 标准字典)]，可导入且数据库中不存在
    - skipped: [(product_id, csv_line)]，可导入且内容全等
    - conflicts: [(product_id, csv_line)]，可导入但内容不全等；不覆盖，交人工
    """
    to_insert = []
    skipped = []
    conflicts = []
    for result in results:
        if result.status not in ("valid", "warning"):
            continue
        standard = to_standard(result.raw)
        pid = standard["product_id"]
        if not pid:
            continue
        current = existing.get(pid)
        if current is None:
            to_insert.append((result, standard))
        elif _same(current, standard):
            skipped.append((pid, result.csv_line))
        else:
            conflicts.append((pid, result.csv_line))
    return to_insert, skipped, conflicts


def plan_fingerprint(to_insert, skipped, conflicts, rejected):
    """计划指纹：四类计数 + 排序后的新增 ID 与冲突 ID。审批时重算，不一致说明预览已过期。"""
    payload = json.dumps(
        {
            "counts": [len(to_insert), len(skipped), len(conflicts), rejected],
            "insert": sorted(standard["product_id"] for _, standard in to_insert),
            "conflict": sorted(pid for pid, _ in conflicts),
        },
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def summarize_plan(to_insert, skipped, conflicts, rejected):
    total = len(to_insert) + len(skipped) + len(conflicts) + rejected
    return {
        "rows_total": total,
        "rows_inserted": len(to_insert),
        "rows_skipped": len(skipped),
        "rows_conflict": len(conflicts),
        "rows_rejected": rejected,
        "rows_pending": 0,
        "reconciled": len(skipped) + len(conflicts) + len(to_insert) == total - rejected,
    }
