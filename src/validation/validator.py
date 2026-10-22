"""商品目录的确定性校验。只用代码规则判定，不依赖任何 AI 模块，不修改、不补造任何值。

行号口径：CSV 文件物理行号，表头为第 1 行，首条数据为第 2 行；多行字段取记录起始行。
"""
import csv
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

VALID = "valid"
WARNING = "warning"
REJECTED = "rejected"

ID_MISSING = "ID_MISSING"
ID_DUPLICATE = "ID_DUPLICATE"
NON_INTEGER = "NON_INTEGER"
DIM_MISSING = "DIM_MISSING"
WEIGHT_NONPOSITIVE = "WEIGHT_NONPOSITIVE"
DIM_NONPOSITIVE = "DIM_NONPOSITIVE"
CATEGORY_EMPTY = "CATEGORY_EMPTY"
CATEGORY_UNKNOWN = "CATEGORY_UNKNOWN"

REJECT_REASONS = (
    ID_MISSING,
    ID_DUPLICATE,
    NON_INTEGER,
    DIM_MISSING,
    WEIGHT_NONPOSITIVE,
    DIM_NONPOSITIVE,
    CATEGORY_EMPTY,
    CATEGORY_UNKNOWN,
)
# 警告等级已保留，暂不启用任何规则（决策：第 3 天）。
WARNING_REASONS = ()

COL_ID = "product_id"
COL_CATEGORY = "product_category_name"
COL_WEIGHT = "product_weight_g"
DIM_COLS = ("product_length_cm", "product_height_cm", "product_width_cm")
NUMERIC_COLS = (
    "product_name_lenght",
    "product_description_lenght",
    "product_photos_qty",
    COL_WEIGHT,
    *DIM_COLS,
)

_INTEGER = re.compile(r"-?[0-9]+")


@dataclass(frozen=True)
class RowResult:
    csv_line: int
    product_id: str
    raw: dict
    status: str
    reasons: tuple


def is_blank(value):
    return value is None or value.strip() == ""


def read_csv(path):
    """返回 (表头, [(csv_line, 行字典), ...])；原始值按字符串原样保留。"""
    rows = []
    with open(Path(path), newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)
        while True:
            start = reader.line_num + 1
            try:
                record = next(reader)
            except StopIteration:
                break
            if not record:
                continue
            if len(record) != len(header):
                raise ValueError(f"第 {start} 行列数 {len(record)} 与表头 {len(header)} 不一致")
            rows.append((start, dict(zip(header, record))))
    return header, rows


def _row_reasons(row, translation_keys):
    reasons = []
    pid = row.get(COL_ID)
    if is_blank(pid):
        reasons.append(ID_MISSING)

    non_integer = False
    values = {}
    for col in NUMERIC_COLS:
        text = row.get(col)
        if is_blank(text):
            continue
        if _INTEGER.fullmatch(text.strip()):
            values[col] = int(text.strip())
        else:
            non_integer = True
    if non_integer:
        reasons.append(NON_INTEGER)

    if any(is_blank(row.get(col)) for col in (COL_WEIGHT, *DIM_COLS)):
        reasons.append(DIM_MISSING)
    if values.get(COL_WEIGHT, 1) <= 0:
        reasons.append(WEIGHT_NONPOSITIVE)
    if any(values.get(col, 1) <= 0 for col in DIM_COLS):
        reasons.append(DIM_NONPOSITIVE)

    category = row.get(COL_CATEGORY)
    if is_blank(category):
        reasons.append(CATEGORY_EMPTY)
    elif category.strip() not in translation_keys:
        reasons.append(CATEGORY_UNKNOWN)
    return reasons


def validate_rows(rows, translation_keys):
    """rows: [(csv_line, 行字典)]；translation_keys: 类目翻译表中的类目集合。

    重复 ID 按去除首尾空白后精确比较；第一次出现的行按其他规则判定，
    之后再出现的同 ID 行标 ID_DUPLICATE（保留第一条，决策 D-013）。
    """
    translation_keys = {k.strip() for k in translation_keys}
    seen_ids = set()
    results = []
    for line, row in rows:
        reasons = _row_reasons(row, translation_keys)
        if not is_blank(row.get(COL_ID)):
            pid = row[COL_ID].strip()
            if pid in seen_ids:
                reasons.append(ID_DUPLICATE)
            seen_ids.add(pid)
        reasons = tuple(r for r in REJECT_REASONS if r in reasons)
        status = REJECTED if reasons else VALID
        results.append(RowResult(line, row.get(COL_ID, ""), row, status, reasons))
    return results


def summarize(results):
    """全部计数由代码得出。原因计数按“含该原因的行数”，多原因的行各计一次。"""
    by_status = Counter(r.status for r in results)
    by_reason = Counter(reason for r in results for reason in r.reasons)
    total = len(results)
    summary = {
        "total": total,
        "valid": by_status[VALID],
        "warning": by_status[WARNING],
        "rejected": by_status[REJECTED],
        "importable": by_status[VALID] + by_status[WARNING],
        "by_reason": {code: by_reason[code] for code in REJECT_REASONS},
        "multi_reason_rows": sum(len(r.reasons) > 1 for r in results),
    }
    summary["reconciled"] = total == summary["valid"] + summary["warning"] + summary["rejected"]
    return summary
