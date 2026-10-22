import csv
import io
import unittest

from src.importing.planner import (
    CONFLICT,
    INSERTED,
    SKIPPED,
    plan_import,
    summarize_plan,
    to_standard,
)
from src.validation.validator import read_csv, validate_rows

HEADER = (
    "product_id,product_category_name,product_name_lenght,product_description_lenght,"
    "product_photos_qty,product_weight_g,product_length_cm,product_height_cm,product_width_cm"
)


def _results(*rows):
    text = HEADER + "\n" + "\n".join(rows) + "\n"
    path = io.StringIO(text)
    import tempfile, os

    fd, name = tempfile.mkstemp(suffix=".csv")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        _, r = read_csv(name)
        return validate_rows(r, {"cama_mesa_banho"})
    finally:
        os.unlink(name)


GOOD = "a" * 32 + ",cama_mesa_banho,10,20,1,500,30,20,10"


class ToStandardTest(unittest.TestCase):
    def test_blank_text_becomes_none(self):
        s = to_standard({"product_category_name": "cama_mesa_banho", "product_photos_qty": ""})
        self.assertIsNone(s["photos_qty"])

    def test_strips_and_parses_integer(self):
        s = to_standard({"product_category_name": " cama_mesa_banho ", "product_weight_g": " 500 "})
        self.assertEqual(s["category_name"], "cama_mesa_banho")
        self.assertEqual(s["weight_g"], 500)

    def test_all_standard_columns_present(self):
        s = to_standard({})
        self.assertEqual(
            set(s),
            {"product_id", "category_name", "name_length", "description_length", "photos_qty",
             "weight_g", "length_cm", "height_cm", "width_cm"},
        )


class PlanImportTest(unittest.TestCase):
    def test_new_rows_are_inserted(self):
        results = _results(GOOD)
        to_insert, skipped, conflicts = plan_import(results, {})
        self.assertEqual(len(to_insert), 1)
        self.assertEqual(skipped, [])
        self.assertEqual(conflicts, [])

    def test_identical_row_is_skipped(self):
        results = _results(GOOD)
        existing = {"a" * 32: to_standard(results[0].raw)}
        to_insert, skipped, conflicts = plan_import(results, existing)
        self.assertEqual((len(to_insert), len(skipped), len(conflicts)), (0, 1, 0))

    def test_one_changed_field_is_conflict_and_not_overwritten(self):
        results = _results(GOOD)
        existing = {"a" * 32: to_standard(results[0].raw)}
        existing["a" * 32]["weight_g"] = 999
        to_insert, skipped, conflicts = plan_import(results, existing)
        self.assertEqual((len(to_insert), len(skipped), len(conflicts)), (0, 0, 1))
        self.assertEqual(existing["a" * 32]["weight_g"], 999)

    def test_rejected_rows_are_excluded(self):
        results = _results(GOOD, "b" * 32 + ",cama_mesa_banho,10,20,1,,30,20,10")
        to_insert, skipped, conflicts = plan_import(results, {})
        self.assertEqual(len(to_insert), 1)
        self.assertEqual(results[1].status, "rejected")

    def test_null_safe_comparison(self):
        results = _results("c" * 32 + ",cama_mesa_banho,,20,1,500,30,20,10")
        existing = {"c" * 32: to_standard(results[0].raw)}
        to_insert, skipped, conflicts = plan_import(results, existing)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(len(conflicts), 0)


class SummarizeTest(unittest.TestCase):
    def test_counts_add_up(self):
        s = summarize_plan([1, 2], [3], [4], rejected=5)
        self.assertEqual(s["rows_total"], 9)
        self.assertEqual(s["rows_inserted"] + s["rows_skipped"] + s["rows_conflict"] + s["rows_rejected"], 9)
        self.assertTrue(s["reconciled"])


if __name__ == "__main__":
    unittest.main()
