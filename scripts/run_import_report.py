"""只读查询 olist_import，生成 docs/import-report.md。所有数字来自 SQL，不含 AI 输出。

用法（仓库根目录）：
    .venv/Scripts/python.exe scripts/run_import_report.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.db.connection import connect  # noqa: E402

REPORT = ROOT / "docs" / "import-report.md"


def main():
    conn = connect()
    cur = conn.cursor()

    def rows(sql, args=None):
        cur.execute(sql, args)
        return cur.fetchall()

    def scalar(sql, args=None):
        return rows(sql, args)[0][0]

    batches = rows(
        "SELECT batch_id, mode, status, rows_total, rows_inserted, rows_skipped, rows_conflict,"
        " rows_rejected, started_at FROM import_batch ORDER BY batch_id"
    )
    out = ["# 导入报告（第 4 天）\n"]
    out.append("> 本报告由 `scripts/run_import_report.py` 只读查询 `olist_import` 生成，数字全部来自 SQL。")
    out.append("> 复现命令（仓库根目录）：`.venv/Scripts/python.exe scripts/run_import_report.py`")
    out.append("> 独立核对：`sql/reconcile.sql` 可用 mysql CLI 另行执行。\n")

    out.append("## 1. 批次记录\n")
    out.append("| 批次 | 模式 | 状态 | 原始行 | 新增 | 相同跳过 | 冲突 | 拒绝 | 开始时间 |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for b in batches:
        out.append("| " + " | ".join(str(x) for x in b) + " |")

    if batches:
        last = batches[-1][0]
        out.append(f"\n## 2. 最近批次（{last}）的行级分布\n")
        out.append("| 维度 | 值 | 行数 |")
        out.append("|---|---|---|")
        for name, col in (("校验状态", "status"), ("导入结果", "import_result")):
            for value, n in rows(f"SELECT {col}, COUNT(*) FROM product_raw WHERE batch_id=%s GROUP BY {col}", (last,)):
                out.append(f"| {name} | {value} | {n} |")

    out.append("\n## 3. 商品表核对\n")
    out.append("| 检查 | 结果 | 期望 |")
    out.append("|---|---|---|")
    out.append(f"| products 行数 | {scalar('SELECT COUNT(*) FROM products')} | 等于所有批次“新增”之和 |")
    out.append(f"| 批次“新增”之和 | {scalar('SELECT COALESCE(SUM(rows_inserted),0) FROM import_batch WHERE mode=%s AND status=%s', ('apply', 'succeeded'))} | — |")
    out.append(
        "| 拒绝行进入 products | "
        f"{scalar('SELECT COUNT(*) FROM products p JOIN product_raw r ON r.product_id=p.product_id WHERE r.status=%s', ('rejected',))} | 0 |"
    )
    out.append(
        "| 无法追溯来源的商品 | "
        f"{scalar('SELECT COUNT(*) FROM products p LEFT JOIN product_raw r ON r.batch_id=p.source_batch_id AND r.csv_line=p.source_line AND r.import_result=%s WHERE r.raw_id IS NULL', ('inserted',))} | 0 |"
    )
    out.append(f"| 重量小于等于 0 的商品 | {scalar('SELECT COUNT(*) FROM products WHERE weight_g<=0')} | 0 |")
    out.append(
        "| 类目不在翻译表的商品 | "
        f"{scalar('SELECT COUNT(*) FROM products p LEFT JOIN category_translation t ON t.category_name=p.category_name WHERE t.category_name IS NULL')} | 0 |"
    )
    out.append(f"| category_translation 行数 | {scalar('SELECT COUNT(*) FROM category_translation')} | 71 |")

    conn.close()
    REPORT.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    print(f"已写入 {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
