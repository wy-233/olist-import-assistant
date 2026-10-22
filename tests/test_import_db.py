"""数据库导入测试：只连接 *_test 库，每个用例前重建四张表。

需要 MySQL 可连接且 .env 已配置；否则整体跳过（不会拿其他数据库顶替）。
"""
import hashlib
import unittest
from pathlib import Path

from src.db.connection import connect, db_config
from src.importing.importer import import_products
from src.validation.validator import read_csv, validate_rows

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "sql" / "schema.sql"
FIXTURE = ROOT / "tests" / "fixtures" / "TEST_ONLY_products.csv"


def _reachable():
    try:
        cfg = db_config(use_test=True)
        if not cfg["database"].endswith("_test"):
            return False
        connect(use_test=True).close()
        return True
    except Exception:
        return False


def _rebuild_schema(conn):
    assert db_config(use_test=True)["database"].endswith("_test")
    lines = [l for l in SCHEMA.read_text(encoding="utf-8").splitlines() if not l.strip().startswith("--")]
    with conn.cursor() as cur:
        for stmt in "\n".join(lines).split(";"):
            if stmt.strip():
                cur.execute(stmt)
    conn.commit()


def _fixture_results():
    _, rows = read_csv(FIXTURE)
    return validate_rows(rows, {"cat_a"})


def _scalar(conn, sql, args=None):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchone()[0]


@unittest.skipUnless(_reachable(), "测试库不可连接或库名不以 _test 结尾")
class ImportDbTest(unittest.TestCase):
    def setUp(self):
        self.conn = connect(use_test=True)
        _rebuild_schema(self.conn)
        self.results = _fixture_results()
        self.sha = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()

    def tearDown(self):
        self.conn.close()

    def run_import(self, mode="apply"):
        return import_products(self.results, FIXTURE.name, self.sha, self.conn, mode)

    def test_first_import_counts_and_reconciles(self):
        stats = self.run_import()
        self.assertEqual(
            (stats["rows_total"], stats["rows_inserted"], stats["rows_skipped"],
             stats["rows_conflict"], stats["rows_rejected"]),
            (15, 3, 0, 0, 12),
        )
        self.assertEqual(_scalar(self.conn, "SELECT COUNT(*) FROM products"), 3)
        self.assertEqual(_scalar(self.conn, "SELECT COUNT(*) FROM product_raw WHERE batch_id=%s", (stats["batch_id"],)), 15)

    def test_dry_run_writes_nothing(self):
        stats = self.run_import("dry_run")
        self.assertEqual(stats["rows_inserted"], 3)
        for table in ("products", "product_raw", "import_batch"):
            self.assertEqual(_scalar(self.conn, f"SELECT COUNT(*) FROM {table}"), 0)

    def test_second_import_is_idempotent(self):
        self.run_import()
        stats = self.run_import()
        self.assertEqual((stats["rows_inserted"], stats["rows_skipped"], stats["rows_conflict"]), (0, 3, 0))
        self.assertEqual(_scalar(self.conn, "SELECT COUNT(*) FROM products"), 3)
        self.assertEqual(_scalar(self.conn, "SELECT COUNT(*) FROM import_batch"), 2)

    def test_changed_field_is_conflict_and_not_overwritten(self):
        self.run_import()
        with self.conn.cursor() as cur:
            cur.execute("UPDATE products SET weight_g = 999 WHERE product_id = 'TEST_ONLY_ok'")
        self.conn.commit()
        stats = self.run_import()
        self.assertEqual((stats["rows_inserted"], stats["rows_skipped"], stats["rows_conflict"]), (0, 2, 1))
        self.assertEqual(_scalar(self.conn, "SELECT weight_g FROM products WHERE product_id = 'TEST_ONLY_ok'"), 999)

    def test_rejected_rows_never_reach_products(self):
        self.run_import()
        ids = {r[0] for r in self._all("SELECT product_id FROM products")}
        self.assertEqual(ids, {"TEST_ONLY_ok", "TEST_ONLY_spaces", "TEST_ONLY_dup"})
        self.assertEqual(
            _scalar(self.conn, "SELECT COUNT(*) FROM product_raw WHERE status='rejected' AND import_result<>'not_attempted'"),
            0,
        )

    def test_raw_layer_keeps_original_strings(self):
        self.run_import()
        raw = _scalar(self.conn, "SELECT weight_g FROM product_raw WHERE product_id='TEST_ONLY_spaces'")
        std = _scalar(self.conn, "SELECT weight_g FROM products WHERE product_id='TEST_ONLY_spaces'")
        self.assertEqual(raw, " 100 ")
        self.assertEqual(std, 100)

    def test_batch_counters_match_raw_rows(self):
        stats = self.run_import()
        bid = stats["batch_id"]
        for col, result in (("rows_inserted", "inserted"), ("rows_skipped", "skipped"), ("rows_conflict", "conflict")):
            counter = _scalar(self.conn, f"SELECT {col} FROM import_batch WHERE batch_id=%s", (bid,))
            actual = _scalar(self.conn, "SELECT COUNT(*) FROM product_raw WHERE batch_id=%s AND import_result=%s", (bid, result))
            self.assertEqual(counter, actual, col)

    def _all(self, sql):
        with self.conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()


class TestDatabaseGuardTest(unittest.TestCase):
    def test_non_test_database_name_is_refused(self):
        import os
        old = os.environ.get("OLIST_DB_NAME_TEST")
        os.environ["OLIST_DB_NAME_TEST"] = "olist_import"
        try:
            with self.assertRaises(RuntimeError):
                db_config(use_test=True)
        finally:
            if old is None:
                del os.environ["OLIST_DB_NAME_TEST"]
            else:
                os.environ["OLIST_DB_NAME_TEST"] = old


if __name__ == "__main__":
    unittest.main()
