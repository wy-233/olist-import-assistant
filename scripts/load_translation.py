"""把类目翻译表导入 category_translation。

用法（仓库根目录）：
    .venv/Scripts/python.exe scripts/load_translation.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.db.connection import connect  # noqa: E402
from src.validation.validator import read_csv  # noqa: E402

TRANSLATION = ROOT / "data" / "raw" / "product_category_name_translation.csv"


def main():
    _, rows = read_csv(TRANSLATION)
    values = [(r["product_category_name"].strip(), r["product_category_name_english"].strip()) for _, r in rows]
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO category_translation (category_name, category_name_en) VALUES (%s, %s)"
                " ON DUPLICATE KEY UPDATE category_name_en = VALUES(category_name_en)",
                values,
            )
            cur.execute("SELECT COUNT(*) FROM category_translation")
            count = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    print(f"已载入 {len(values)} 行，表中现有 {count} 行")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
