"""只读盘点 data/raw/ 下的商品目录与类目翻译，输出报告到标准输出。

用法（在仓库根目录）：
    .venv/Scripts/python.exe -I scripts/profile_data.py
"""
import csv
import hashlib
import sys
from collections import Counter
from pathlib import Path

RAW = Path("data/raw")
PRODUCTS = RAW / "olist_products_dataset.csv"
TRANSLATION = RAW / "product_category_name_translation.csv"
NUMERIC = [
    "product_name_lenght",
    "product_description_lenght",
    "product_photos_qty",
    "product_weight_g",
    "product_length_cm",
    "product_height_cm",
    "product_width_cm",
]
DIMENSIONS = ["product_weight_g", "product_length_cm", "product_height_cm", "product_width_cm"]
TEXT_META = ["product_name_lenght", "product_description_lenght", "product_photos_qty"]


def read(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def blank(v):
    return v is None or v.strip() == ""


def main():
    h, rows = read(PRODUCTS)
    th, trows = read(TRANSLATION)

    print("## 文件")
    for p in (PRODUCTS, TRANSLATION):
        print(f"- {p.name}: {p.stat().st_size} 字节, SHA256 {sha256(p)}")

    print(f"\n## 商品表 {PRODUCTS.name}")
    print(f"- 列: {h}")
    print(f"- 数据行数: {len(rows)}")
    ids = [r["product_id"] for r in rows]
    print(f"- product_id 重复数: {len(ids) - len(set(ids))}")
    print(f"- product_id 长度分布: {dict(Counter(len(i) for i in ids))}")
    print(f"- 整行重复数: {len(rows) - len({tuple(r.values()) for r in rows})}")
    print(f"- 首尾空白的值: {sum(v != v.strip() for r in rows for v in r.values())}")

    print("\n### 各列空值与取值")
    for c in h:
        vals = [r[c] for r in rows]
        print(f"- {c}: 空值 {sum(blank(v) for v in vals)}, 不同值 {len(set(vals))}")
    for c in NUMERIC:
        nums, bad = [], 0
        for r in rows:
            v = r[c]
            if blank(v):
                continue
            try:
                nums.append(float(v))
            except ValueError:
                bad += 1
        print(
            f"- {c}: 非数字 {bad}, 最小 {min(nums):g}, 最大 {max(nums):g}, "
            f"为 0 的 {sum(n == 0 for n in nums)}, 非整数 {sum(n != int(n) for n in nums)}"
        )

    print("\n### 空值的行间关系")
    cat_empty = {r["product_id"] for r in rows if blank(r["product_category_name"])}
    for c in TEXT_META:
        s = {r["product_id"] for r in rows if blank(r[c])}
        print(f"- {c} 为空的行集合 == 类目为空的行集合: {s == cat_empty} (各 {len(s)} / {len(cat_empty)})")
    dim_empty = [r for r in rows if any(blank(r[c]) for c in DIMENSIONS)]
    print(f"- 重量/尺寸任一为空的行: {len(dim_empty)}")
    for r in dim_empty:
        print(f"  - {r['product_id']}: 类目={r['product_category_name']!r}, " + ", ".join(f"{c}={r[c]!r}" for c in DIMENSIONS))
    zero = [r for r in rows if not blank(r["product_weight_g"]) and float(r["product_weight_g"]) == 0]
    print(f"- 重量为 0 的行: {len(zero)}")
    for r in zero:
        print(f"  - {r['product_id']}: 类目={r['product_category_name']!r}, " + ", ".join(f"{c}={r[c]!r}" for c in DIMENSIONS))

    print(f"\n## 类目翻译表 {TRANSLATION.name}")
    print(f"- 列: {th}, 数据行数: {len(trows)}")
    tkeys = [r["product_category_name"] for r in trows]
    print(f"- 类目主键重复数: {len(tkeys) - len(set(tkeys))}")
    print(f"- 空值: {sum(blank(v) for r in trows for v in r.values())}")
    cats = {r["product_category_name"] for r in rows if not blank(r["product_category_name"])}
    print(f"- 商品表非空类目数: {len(cats)}")
    print(f"- 商品表有、翻译表无: {sorted(cats - set(tkeys))}")
    print(f"- 翻译表有、商品表无: {sorted(set(tkeys) - cats)}")
    missing = Counter(r["product_category_name"] for r in rows if r["product_category_name"] in cats - set(tkeys))
    print(f"- 翻译缺失类目涉及商品数: {dict(missing)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
