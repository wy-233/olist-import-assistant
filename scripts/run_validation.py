"""只读校验 data/raw/ 下的商品目录，生成 docs/validation-report.md。

用法（在仓库根目录）：
    .venv/Scripts/python.exe scripts/run_validation.py

报告中所有数字和判定均由代码规则产生，不含 AI 输出。
"""
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.validation.validator import (  # noqa: E402
    COL_CATEGORY,
    COL_WEIGHT,
    DIM_COLS,
    REJECT_REASONS,
    REJECTED,
    WARNING_REASONS,
    read_csv,
    summarize,
    validate_rows,
)

RAW = ROOT / "data" / "raw"
PRODUCTS = RAW / "olist_products_dataset.csv"
TRANSLATION = RAW / "product_category_name_translation.csv"
REPORT = ROOT / "docs" / "validation-report.md"

REASON_TEXT = {
    "ID_MISSING": "product_id 缺失",
    "ID_DUPLICATE": "product_id 重复（第一条保留，之后出现的行拒绝）",
    "NON_INTEGER": "数值列存在非整数文本",
    "DIM_MISSING": "重量或尺寸为空（D-004）",
    "WEIGHT_NONPOSITIVE": "重量小于等于 0（D-005）",
    "DIM_NONPOSITIVE": "尺寸小于等于 0",
    "CATEGORY_EMPTY": "类目为空（D-007）",
    "CATEGORY_UNKNOWN": "类目在翻译表中查不到（D-006）",
}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cell(value):
    return "" if value is None else value.replace("|", "\\|").replace("\n", " ")


def build_report():
    _, rows = read_csv(PRODUCTS)
    _, trows = read_csv(TRANSLATION)
    keys = {r["product_category_name"] for _, r in trows}
    results = validate_rows(rows, keys)
    s = summarize(results)

    out = []
    out.append("# 校验报告（第 3 天）\n")
    out.append("> 本报告由 `scripts/run_validation.py` 只读扫描 `data/raw/` 生成，所有数字和判定由代码规则产生，不含 AI 输出。")
    out.append("> 复现命令（仓库根目录）：`.venv/Scripts/python.exe scripts/run_validation.py`")
    out.append("> 行号口径：CSV 文件物理行号，表头为第 1 行，首条数据为第 2 行。原始值按字符串原样列出。")
    out.append("> 规则依据：`docs/decisions.md` 的 D-004 到 D-007；“警告”等级已保留，暂未启用任何规则。\n")

    out.append("## 1. 输入文件\n")
    out.append("| 文件 | 数据行数 | SHA256 |")
    out.append("|---|---|---|")
    out.append(f"| {PRODUCTS.name} | {len(rows)} | {sha256(PRODUCTS)} |")
    out.append(f"| {TRANSLATION.name} | {len(trows)} | {sha256(TRANSLATION)} |\n")

    out.append("## 2. 对账\n")
    out.append("| 项目 | 行数 |")
    out.append("|---|---|")
    out.append(f"| 原始行数 | {s['total']} |")
    out.append(f"| 有效 | {s['valid']} |")
    out.append(f"| 警告 | {s['warning']} |")
    out.append(f"| 拒绝（按行去重） | {s['rejected']} |")
    out.append(f"| 可导入（有效 + 警告） | {s['importable']} |\n")
    out.append(f"- 原始行数 = 有效 + 警告 + 拒绝：{'成立' if s['reconciled'] else '不成立'}")
    out.append(f"- 同时命中多个拒绝原因的行：{s['multi_reason_rows']}\n")

    out.append("## 3. 拒绝原因统计\n")
    out.append("> 原因计数按“含该原因的行数”统计，一行命中多个原因时各计一次，因此各原因之和可大于拒绝行数。\n")
    out.append("| 原因代码 | 含义 | 行数 |")
    out.append("|---|---|---|")
    for code in REJECT_REASONS:
        out.append(f"| {code} | {REASON_TEXT[code]} | {s['by_reason'][code]} |")
    out.append(f"\n- 已启用的警告规则数：{len(WARNING_REASONS)}\n")

    out.append("## 4. 被拒绝的行\n")
    out.append("按文件行号排序，共 {} 行。\n".format(s["rejected"]))
    cols = (COL_CATEGORY, COL_WEIGHT, *DIM_COLS)
    out.append("| 行号 | product_id | 原因 | " + " | ".join(cols) + " |")
    out.append("|---|---|---|" + "---|" * len(cols))
    for r in results:
        if r.status == REJECTED:
            out.append(
                f"| {r.csv_line} | {cell(r.product_id)} | {', '.join(r.reasons)} | "
                + " | ".join(cell(r.raw.get(c)) for c in cols)
                + " |"
            )
    return "\n".join(out) + "\n"


def main():
    text = build_report()
    REPORT.write_text(text, encoding="utf-8", newline="\n")
    print(f"已写入 {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
