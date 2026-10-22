"""校验并导入商品目录。

用法（仓库根目录）：
    .venv/Scripts/python.exe scripts/import_products.py --dry-run
    .venv/Scripts/python.exe scripts/import_products.py --apply
"""
import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.db.connection import connect  # noqa: E402
from src.importing.importer import import_products  # noqa: E402
from src.validation.validator import read_csv, summarize, validate_rows  # noqa: E402

RAW = ROOT / "data" / "raw"
PRODUCTS = RAW / "olist_products_dataset.csv"
TRANSLATION = RAW / "product_category_name_translation.csv"


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    _, rows = read_csv(PRODUCTS)
    _, trows = read_csv(TRANSLATION)
    results = validate_rows(rows, {r["product_category_name"] for _, r in trows})
    check = summarize(results)

    mode = "apply" if args.apply else "dry_run"
    conn = connect()
    try:
        stats = import_products(
            results, PRODUCTS.name, hashlib.sha256(PRODUCTS.read_bytes()).hexdigest(), conn, mode
        )
    finally:
        conn.close()

    print(f"模式：{mode}")
    print(f"原始行数：{stats['rows_total']}")
    print(f"新增：{stats['rows_inserted']}")
    print(f"相同跳过：{stats['rows_skipped']}")
    print(f"冲突：{stats['rows_conflict']}")
    print(f"拒绝：{stats['rows_rejected']}")
    print(f"对账（校验侧）：{'成立' if check['reconciled'] else '不成立'}")
    print(f"对账（导入侧）：{'成立' if stats['reconciled'] else '不成立'}")
    if "batch_id" in stats:
        print(f"批次号：{stats['batch_id']}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
