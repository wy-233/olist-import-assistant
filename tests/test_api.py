"""预览 / 审批接口测试：只连接 *_test 库，每个用例前重建表并写入类目翻译。

需要 MySQL 可连接、.env 已配置；缺少 fastapi/httpx 或库不可达时整体跳过。
"""
import hashlib
import unittest
from pathlib import Path

from src.db.connection import connect

try:
    from fastapi.testclient import TestClient

    from src.api.app import app, get_store
    from src.api.service import Store
    HAVE_WEB = True
except ImportError:  # 依赖未安装时跳过，不影响其余测试
    HAVE_WEB = False

from tests.test_import_db import FIXTURE, _rebuild_schema, _reachable

TRANSLATION = "cat_a"
MIGRATION = Path(__file__).resolve().parent.parent / "sql" / "migrations" / "002_preview_approval.sql"


def _seed_translation(conn):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO category_translation (category_name, category_name_en) VALUES (%s, %s)",
                    (TRANSLATION, "Category A"))
    conn.commit()


def _scalar(conn, sql, args=None):
    # 每次用新连接读，避免长事务的 REPEATABLE READ 快照看不到接口刚提交的数据
    with connect(use_test=True) as fresh, fresh.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchone()[0]


OLD_IMPORT_BATCH = """
CREATE TABLE import_batch (
  batch_id        BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  file_name       VARCHAR(255) NOT NULL,
  file_sha256     CHAR(64)     NOT NULL,
  mode            ENUM('dry_run','apply') NOT NULL,
  status          ENUM('running','succeeded','failed') NOT NULL DEFAULT 'running',
  rows_total      INT NOT NULL DEFAULT 0,
  rows_inserted   INT NOT NULL DEFAULT 0,
  rows_skipped    INT NOT NULL DEFAULT 0,
  rows_conflict   INT NOT NULL DEFAULT 0,
  rows_rejected   INT NOT NULL DEFAULT 0,
  rows_pending    INT NOT NULL DEFAULT 0,
  started_at      DATETIME NOT NULL,
  finished_at     DATETIME NULL,
  KEY idx_sha (file_sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def _col_dump(cur):
    cur.execute(
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS"
        " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='import_batch' ORDER BY ORDINAL_POSITION"
    )
    return cur.fetchall()


@unittest.skipUnless(HAVE_WEB and _reachable(), "需要 fastapi/httpx 与可连接的 *_test 库")
class PreviewApproveTest(unittest.TestCase):
    def setUp(self):
        self.conn = connect(use_test=True)
        _rebuild_schema(self.conn)
        _seed_translation(self.conn)
        app.dependency_overrides[get_store] = lambda: Store(lambda: connect(use_test=True))
        self.client = TestClient(app)
        self.content = FIXTURE.read_bytes()

    def tearDown(self):
        app.dependency_overrides.clear()
        self.conn.close()

    def preview(self, content=None, name="TEST_ONLY_products.csv"):
        body = self.content if content is None else content
        return self.client.post("/imports/preview", files={"file": (name, body, "text/csv")})

    def approve(self, batch_id, approver="唯一", confirm=None):
        return self.client.post(f"/imports/{batch_id}/approve", data={
            "approver": approver,
            "confirm": confirm or f"APPROVE {batch_id} 3",
        })

    def test_preview_writes_nothing_to_product_tables(self):
        response = self.preview()
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(
            (body["counts"]["rows_total"], body["counts"]["rows_inserted"],
             body["counts"]["rows_skipped"], body["counts"]["rows_conflict"],
             body["counts"]["rows_rejected"]),
            (15, 3, 0, 0, 12),
        )
        self.assertEqual(body["written"], {"products": 0, "product_raw": 0})
        self.assertEqual(body["file_sha256"], hashlib.sha256(self.content).hexdigest())
        self.assertEqual(body["validation"]["reconciled"], True)
        for table in ("products", "product_raw"):
            self.assertEqual(_scalar(self.conn, f"SELECT COUNT(*) FROM {table}"), 0)
        self.assertEqual(_scalar(self.conn, "SELECT status FROM import_batch"), "previewed")

    def test_approve_writes_only_after_confirmation(self):
        batch_id = self.preview().json()["batch_id"]
        self.assertEqual(self.approve(batch_id, confirm="yes").status_code, 400)
        self.assertEqual(_scalar(self.conn, "SELECT COUNT(*) FROM products"), 0)

        response = self.approve(batch_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["rows_inserted"], 3)
        self.assertEqual(_scalar(self.conn, "SELECT COUNT(*) FROM products"), 3)
        self.assertEqual(_scalar(self.conn, "SELECT COUNT(*) FROM product_raw WHERE batch_id=%s",
                                 (batch_id,)), 15)
        self.assertEqual(
            _scalar(self.conn, "SELECT CONCAT(status, '/', mode, '/', approved_by) FROM import_batch"),
            "succeeded/apply/唯一",
        )

    def test_repeat_approve_is_conflict(self):
        batch_id = self.preview().json()["batch_id"]
        self.assertEqual(self.approve(batch_id).status_code, 200)
        self.assertEqual(self.approve(batch_id).status_code, 409)
        self.assertEqual(_scalar(self.conn, "SELECT COUNT(*) FROM products"), 3)

    def test_second_round_is_fully_skipped(self):
        first = self.preview().json()["batch_id"]
        self.approve(first)
        second = self.preview().json()
        self.assertEqual((second["counts"]["rows_inserted"], second["counts"]["rows_skipped"]), (0, 3))
        self.assertEqual(self.approve(second["batch_id"], confirm=f"APPROVE {second['batch_id']} 0",
                                      ).status_code, 200)
        self.assertEqual(_scalar(self.conn, "SELECT COUNT(*) FROM products"), 3)

    def test_changed_plan_after_preview_is_stale(self):
        batch_id = self.preview().json()["batch_id"]
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO products (product_id, category_name, name_length, description_length,"
                " photos_qty, weight_g, length_cm, height_cm, width_cm, source_batch_id, source_line,"
                " created_at) VALUES ('TEST_ONLY_ok','cat_a',1,1,1,100,1,1,1,0,0,NOW())"
            )
        self.conn.commit()
        response = self.approve(batch_id)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(_scalar(self.conn, "SELECT status FROM import_batch WHERE batch_id=%s",
                                 (batch_id,)), "stale")
        self.assertEqual(_scalar(self.conn, "SELECT COUNT(*) FROM products"), 1)

    def test_approve_unknown_batch_is_404(self):
        self.assertEqual(self.approve(9999).status_code, 404)

    def test_bad_upload_is_rejected(self):
        self.assertEqual(self.preview(b"product_id\nabc\n").status_code, 400)
        self.assertEqual(self.preview(b"").status_code, 400)
        self.assertEqual(self.preview(b"product_id,category\n\xff\xfe,1\n").status_code, 400)

    def test_report_reconciles_after_approval(self):
        batch_id = self.preview().json()["batch_id"]
        self.approve(batch_id)
        body = self.client.get(f"/imports/{batch_id}/report").json()
        self.assertTrue(body["all_ok"], body["checks"])
        # 拒绝行仍留在 product_raw 且为 not_attempted，正是“拒绝行未进商品表”的证据
        self.assertEqual(body["row_results"], {"inserted": 3, "not_attempted": 12})

    def test_report_before_approval_has_no_row_results(self):
        batch_id = self.preview().json()["batch_id"]
        body = self.client.get(f"/imports/{batch_id}/report").json()
        self.assertIsNone(body["row_results"])
        self.assertEqual(body["batch"]["status"], "previewed")

    def test_products_endpoint_pages(self):
        batch_id = self.preview().json()["batch_id"]
        self.approve(batch_id)
        body = self.client.get("/products", params={"limit": 2, "offset": 1}).json()
        self.assertEqual((body["total"], body["limit"], body["offset"], len(body["items"])), (3, 2, 1, 2))
        self.assertEqual(self.client.get("/products", params={"limit": 999}).status_code, 422)


@unittest.skipUnless(_reachable(), "测试库不可连接或库名不以 _test 结尾")
class MigrationParityTest(unittest.TestCase):
    """迁移后的旧表结构应与新建库（schema.sql）完全一致。"""

    def setUp(self):
        self.conn = connect(use_test=True)

    def tearDown(self):
        self.conn.close()

    def test_migration_matches_fresh_schema(self):
        # 先按 schema.sql 重建，再删子表与 import_batch（product_raw 有外键指向后者）
        _rebuild_schema(self.conn)
        with self.conn.cursor() as cur:
            cur.execute("DROP TABLE product_raw")
            cur.execute("DROP TABLE import_batch")
            cur.execute(OLD_IMPORT_BATCH)
            for stmt in _sql_statements(MIGRATION):
                cur.execute(stmt)
        self.conn.commit()
        with self.conn.cursor() as cur:
            migrated = _col_dump(cur)

        _rebuild_schema(self.conn)
        with self.conn.cursor() as cur:
            fresh = _col_dump(cur)
        self.assertEqual(migrated, fresh)


def _sql_statements(path):
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if not l.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


if __name__ == "__main__":
    unittest.main()
