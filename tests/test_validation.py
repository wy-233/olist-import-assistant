"""校验模块测试。

fixtures/TEST_ONLY_products.csv 仅测试用：其中的 ID 均以 TEST_ONLY 或空值构造，
只在本测试中读取，不属于真实数据，也不进入任何导入流程。

运行（仓库根目录）：
    .venv/Scripts/python.exe -m unittest discover -s tests -t .
"""
import ast
import unittest
from pathlib import Path

from src.validation.validator import (
    CATEGORY_EMPTY,
    CATEGORY_UNKNOWN,
    DIM_MISSING,
    DIM_NONPOSITIVE,
    ID_DUPLICATE,
    ID_MISSING,
    NON_INTEGER,
    REJECTED,
    VALID,
    WARNING,
    WARNING_REASONS,
    WEIGHT_NONPOSITIVE,
    read_csv,
    summarize,
    validate_rows,
)

ROOT = Path(__file__).resolve().parent.parent
RAW_PRODUCTS = ROOT / "data" / "raw" / "olist_products_dataset.csv"
RAW_TRANSLATION = ROOT / "data" / "raw" / "product_category_name_translation.csv"
FIXTURE = ROOT / "tests" / "fixtures" / "TEST_ONLY_products.csv"


class FixtureRules(unittest.TestCase):
    """用仅测试用夹具验证真实数据里不存在的情形。"""

    @classmethod
    def setUpClass(cls):
        _, rows = read_csv(FIXTURE)
        cls.rows = rows
        results = validate_rows(rows, {"cat_a"})
        cls.by_line = {r.csv_line: r for r in results}
        cls.results = results

    def test_fixture_is_marked_test_only_and_under_tests(self):
        self.assertEqual(FIXTURE.parent, ROOT / "tests" / "fixtures")
        self.assertTrue(FIXTURE.name.startswith("TEST_ONLY_"))
        for _, row in self.rows:
            pid = row["product_id"].strip()
            self.assertTrue(pid == "" or pid.startswith("TEST_ONLY"), pid)

    def test_valid_rows_have_no_reasons(self):
        for line in (2, 3):
            self.assertEqual(self.by_line[line].status, VALID)
            self.assertEqual(self.by_line[line].reasons, ())

    def test_raw_values_are_kept_verbatim(self):
        self.assertEqual(self.by_line[3].raw["product_weight_g"], " 100 ")

    def test_id_missing_including_whitespace_only(self):
        self.assertEqual(self.by_line[4].reasons, (ID_MISSING,))
        self.assertEqual(self.by_line[5].reasons, (ID_MISSING,))

    def test_duplicate_id_marks_every_occurrence(self):
        self.assertEqual(self.by_line[6].reasons, (ID_DUPLICATE,))
        self.assertEqual(self.by_line[7].reasons, (ID_DUPLICATE,))

    def test_non_integer_text(self):
        self.assertEqual(self.by_line[8].reasons, (NON_INTEGER,))
        self.assertEqual(self.by_line[9].reasons, (NON_INTEGER,))

    def test_nonpositive_dimensions_and_weight(self):
        self.assertEqual(self.by_line[10].reasons, (DIM_NONPOSITIVE,))
        self.assertEqual(self.by_line[11].reasons, (DIM_NONPOSITIVE,))
        self.assertEqual(self.by_line[12].reasons, (WEIGHT_NONPOSITIVE,))
        self.assertEqual(self.by_line[13].reasons, (WEIGHT_NONPOSITIVE,))

    def test_empty_dimension_is_missing_not_non_integer(self):
        self.assertEqual(self.by_line[14].reasons, (DIM_MISSING,))

    def test_category_unknown_and_empty_are_exclusive(self):
        self.assertEqual(self.by_line[15].reasons, (CATEGORY_UNKNOWN,))
        self.assertEqual(self.by_line[16].reasons, (CATEGORY_EMPTY,))

    def test_fixture_reconciliation(self):
        s = summarize(self.results)
        self.assertEqual(s["total"], 15)
        self.assertEqual(s["valid"], 2)
        self.assertEqual(s["rejected"], 13)
        self.assertTrue(s["reconciled"])

    def test_warning_level_is_reserved_but_unused(self):
        self.assertEqual(WARNING_REASONS, ())
        self.assertNotIn(WARNING, {r.status for r in self.results})

    def test_input_rows_are_not_modified(self):
        _, again = read_csv(FIXTURE)
        self.assertEqual(self.rows, again)


@unittest.skipUnless(RAW_PRODUCTS.exists() and RAW_TRANSLATION.exists(), "缺少 data/raw/ 原始文件")
class RealData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, rows = read_csv(RAW_PRODUCTS)
        _, trows = read_csv(RAW_TRANSLATION)
        cls.rows = rows
        cls.results = validate_rows(rows, {r["product_category_name"] for _, r in trows})
        cls.summary = summarize(cls.results)
        cls.by_id = {r.product_id: r for r in cls.results}

    def test_row_counts_and_reconciliation(self):
        s = self.summary
        self.assertEqual(s["total"], 32951)
        self.assertEqual(s["rejected"], 628)
        self.assertEqual(s["warning"], 0)
        self.assertEqual(s["valid"], 32323)
        self.assertEqual(s["importable"], 32323)
        self.assertTrue(s["reconciled"])

    def test_reason_counts_match_signed_decisions(self):
        by = self.summary["by_reason"]
        self.assertEqual(by[DIM_MISSING], 2)
        self.assertEqual(by[WEIGHT_NONPOSITIVE], 4)
        self.assertEqual(by[CATEGORY_UNKNOWN], 13)
        self.assertEqual(by[CATEGORY_EMPTY], 610)
        for code in (ID_MISSING, ID_DUPLICATE, NON_INTEGER, DIM_NONPOSITIVE):
            self.assertEqual(by[code], 0, code)

    def test_only_one_row_overlaps(self):
        by = self.summary["by_reason"]
        self.assertEqual(sum(by.values()) - self.summary["rejected"], 1)
        self.assertEqual(self.summary["multi_reason_rows"], 1)
        row = self.by_id["5eb564652db742ff8f28759cd8d2652a"]
        self.assertEqual(row.reasons, (DIM_MISSING, CATEGORY_EMPTY))
        row = self.by_id["09ff539a621711667c43eba6a3bd8466"]
        self.assertEqual(row.reasons, (DIM_MISSING,))

    def test_line_numbers_start_at_two(self):
        self.assertEqual(self.results[0].csv_line, 2)
        self.assertEqual(self.results[-1].csv_line, 32952)

    def test_rejected_rows_keep_raw_values(self):
        for pid in (
            "81781c0fed9fe1ad6e8c81fca1e1cb08",
            "8038040ee2a71048d4bdbbdc985b69ab",
            "36ba42dd187055e1fbe943b2d11430ca",
            "e673e90efa65a5409ff4196c038bb5af",
        ):
            r = self.by_id[pid]
            self.assertEqual(r.status, REJECTED)
            self.assertEqual(r.raw["product_weight_g"], "0")


class NoAiDependency(unittest.TestCase):
    def test_validation_package_imports_no_ai_module(self):
        banned = {"ai", "llm", "anthropic", "openai"}
        for path in (ROOT / "src" / "validation").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    self.assertFalse(banned & set(name.lower().split(".")), f"{path.name}: {name}")


if __name__ == "__main__":
    unittest.main()
