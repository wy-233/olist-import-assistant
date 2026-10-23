"""预览 / 审批 / 报告的业务逻辑。

所有判定由确定性代码产生，不调用任何 AI 模块，不修改、不补造任何商品值。

预览只写 import_batch 一行（status='previewed'），不写 products，也不写 product_raw。
审批分两步：先用条件 UPDATE 原子抢占 previewed 批次（并发或重复审批只会成功一次），
再在独立事务里按预览时保存的文件内容与计划指纹写入；指纹不一致则批处置为 stale。
"""
import hashlib
import io
from datetime import datetime

from src.importing.importer import PlanChanged, apply_to_batch, load_existing
from src.importing.planner import plan_fingerprint, plan_import, summarize_plan
from src.validation.validator import read_csv_stream, summarize, validate_rows

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_PAGE = 200
SAMPLE_SIZE = 20

REQUIRED_COLUMNS = (
    "product_id",
    "product_category_name",
    "product_name_lenght",
    "product_description_lenght",
    "product_photos_qty",
    "product_weight_g",
    "product_length_cm",
    "product_height_cm",
    "product_width_cm",
)

BATCH_COLUMNS = (
    "batch_id", "file_name", "file_sha256", "mode", "status", "rows_total", "rows_inserted",
    "rows_skipped", "rows_conflict", "rows_rejected", "rows_pending", "plan_fingerprint",
    "approved_by", "approved_at", "started_at", "finished_at",
)


class ServiceError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class BadRequest(ServiceError):
    status_code = 400
    code = "bad_request"


class NotFound(ServiceError):
    status_code = 404
    code = "not_found"


class Conflict(ServiceError):
    status_code = 409
    code = "conflict"


def parse_content(content):
    """把上传的字节解析成 (表头, 行列表)；不合法时抛 BadRequest。"""
    if not content:
        raise BadRequest("上传内容为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise BadRequest(f"文件超过 {MAX_UPLOAD_BYTES} 字节上限")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise BadRequest("文件不是 UTF-8 编码的文本 CSV") from None
    try:
        header, rows = read_csv_stream(io.StringIO(text, newline=""))
    except ValueError as exc:
        raise BadRequest(str(exc)) from None
    missing = [col for col in REQUIRED_COLUMNS if col not in header]
    if missing:
        raise BadRequest("缺少必需列：" + "、".join(missing))
    if not rows:
        raise BadRequest("文件中没有数据行")
    return header, rows


def _rejected_samples(results, limit=SAMPLE_SIZE):
    return [
        {"csv_line": r.csv_line, "product_id": r.product_id, "reasons": list(r.reasons)}
        for r in results if r.status == "rejected"
    ][:limit]


class Store:
    """数据库访问层。connect 是无参可调用对象，返回一条 pymysql 连接。"""

    def __init__(self, connect):
        self._connect = connect

    def _run(self, fn):
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                result = fn(cur)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _query(self, fn):
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                return fn(cur)
        finally:
            conn.close()

    def translation_keys(self):
        def fetch(cur):
            cur.execute("SELECT category_name FROM category_translation")
            return {row[0].strip() for row in cur.fetchall()}

        keys = self._query(fetch)
        if not keys:
            raise BadRequest("category_translation 为空：请先运行 scripts/load_translation.py")
        return keys

    def _batch(self, batch_id):
        def fetch(cur):
            cur.execute(
                "SELECT " + ", ".join(BATCH_COLUMNS) + ", file_content FROM import_batch WHERE batch_id=%s",
                (batch_id,),
            )
            row = cur.fetchone()
            return None if row is None else dict(zip((*BATCH_COLUMNS, "file_content"), row))

        batch = self._query(fetch)
        if batch is None:
            raise NotFound(f"批次 {batch_id} 不存在")
        return batch

    def _set_status(self, batch_id, status):
        self._run(lambda cur: cur.execute(
            "UPDATE import_batch SET status=%s WHERE batch_id=%s", (status, batch_id)
        ))

    def preview(self, file_name, content):
        _, rows = parse_content(content)
        results = validate_rows(rows, self.translation_keys())
        check = summarize(results)
        sha = hashlib.sha256(content).hexdigest()

        def insert(cur):
            to_insert, skipped, conflicts = plan_import(results, load_existing(cur))
            counts = summarize_plan(to_insert, skipped, conflicts, check["rejected"])
            cur.execute(
                "INSERT INTO import_batch (file_name, file_sha256, mode, status, rows_total,"
                " rows_inserted, rows_skipped, rows_conflict, rows_rejected, rows_pending,"
                " started_at, plan_fingerprint, file_content)"
                " VALUES (%s, %s, 'dry_run', 'previewed', %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (file_name, sha, counts["rows_total"], counts["rows_inserted"],
                 counts["rows_skipped"], counts["rows_conflict"], counts["rows_rejected"],
                 counts["rows_pending"], datetime.now(),
                 plan_fingerprint(to_insert, skipped, conflicts, check["rejected"]), content),
            )
            return cur.lastrowid, counts, [
                {"product_id": pid, "csv_line": line} for pid, line in conflicts[:SAMPLE_SIZE]
            ]

        batch_id, counts, conflicts = self._run(insert)
        return {
            "batch_id": batch_id,
            "file_name": file_name,
            "file_sha256": sha,
            "status": "previewed",
            "counts": counts,
            "validation": check,
            "rejected_samples": _rejected_samples(results),
            "conflict_samples": conflicts,
            "written": {"products": 0, "product_raw": 0},
        }

    def approve(self, batch_id, approver, confirm):
        approver = (approver or "").strip()
        if not approver:
            raise BadRequest("approver 不能为空")
        batch = self._batch(batch_id)
        if batch["status"] != "previewed":
            raise Conflict(f"批次 {batch_id} 当前状态为 {batch['status']}，只有 previewed 可审批")

        expected = f"APPROVE {batch_id} {batch['rows_inserted']}"
        if (confirm or "").strip() != expected:
            raise BadRequest(f"确认文本不匹配，应输入：{expected}")

        claimed = self._run(lambda cur: cur.execute(
            "UPDATE import_batch SET status='running', mode='apply', approved_by=%s, approved_at=%s"
            " WHERE batch_id=%s AND status='previewed'",
            (approver, datetime.now(), batch_id),
        ))
        if claimed != 1:
            raise Conflict(f"批次 {batch_id} 已被他人审批或状态已变化，请刷新后重试")

        content = batch["file_content"]
        if content is None:
            self._set_status(batch_id, "failed")
            raise Conflict("该批次没有保存上传内容（可能由命令行脚本创建），不能在网页端审批")
        if hashlib.sha256(content).hexdigest() != batch["file_sha256"]:
            self._set_status(batch_id, "failed")
            raise Conflict("批次保存的内容与预览时的哈希不一致，请重新预览")

        try:
            _, rows = parse_content(content)
            results = validate_rows(rows, self.translation_keys())
        except ServiceError:
            self._set_status(batch_id, "failed")
            raise

        try:
            stats = self._run(lambda cur: apply_to_batch(
                cur, batch_id, results, expected_fingerprint=batch["plan_fingerprint"]
            ))
        except PlanChanged:
            self._set_status(batch_id, "stale")
            raise Conflict("预览之后数据库中的数据已变化，重新计算的计划与预览不一致；请重新预览") from None
        except Exception:
            self._set_status(batch_id, "failed")
            raise

        stats["batch_id"] = batch_id
        stats["approved_by"] = approver
        return stats

    def report(self, batch_id):
        batch = self._batch(batch_id)
        validation = None
        if batch["file_content"] is not None:
            _, rows = parse_content(batch["file_content"])
            validation = summarize(validate_rows(rows, self.translation_keys()))

        counts = {key: batch[key] for key in (
            "rows_total", "rows_inserted", "rows_skipped", "rows_conflict",
            "rows_rejected", "rows_pending",
        )}
        row_results = None
        if batch["status"] in ("succeeded", "failed", "stale"):
            def fetch_results(cur):
                cur.execute(
                    "SELECT import_result, COUNT(*) FROM product_raw WHERE batch_id=%s"
                    " GROUP BY import_result", (batch_id,),
                )
                return dict(cur.fetchall())

            row_results = self._query(fetch_results)

        checks = []
        if validation is not None:
            checks.append(_check(
                "本批内容：原始行 = 有效 + 警告 + 拒绝",
                validation["total"],
                validation["valid"] + validation["warning"] + validation["rejected"],
            ))
            importable = validation["importable"]
        else:
            importable = counts["rows_total"] - counts["rows_rejected"]
        checks.append(_check(
            "可导入 = 新增 + 相同跳过 + 冲突",
            importable,
            counts["rows_inserted"] + counts["rows_skipped"] + counts["rows_conflict"],
        ))

        global_checks = self._query(self._global_checks)
        return {
            "batch": {key: batch[key] for key in BATCH_COLUMNS},
            "counts": counts,
            "validation": validation,
            "row_results": row_results,
            "checks": checks + global_checks,
            "all_ok": all(c["ok"] for c in checks + global_checks),
        }

    @staticmethod
    def _global_checks(cur):
        def scalar(sql, args=None):
            cur.execute(sql, args)
            return cur.fetchone()[0]

        return [
            _check("products 行数 = 全部成功批次新增之和",
                   scalar("SELECT COUNT(*) FROM products"),
                   scalar("SELECT COALESCE(SUM(rows_inserted),0) FROM import_batch"
                          " WHERE mode='apply' AND status='succeeded'")),
            _check("拒绝行进入 products 的行数", scalar(
                "SELECT COUNT(*) FROM products p JOIN product_raw r"
                " ON r.batch_id=p.source_batch_id AND r.csv_line=p.source_line"
                " WHERE r.status='rejected'"), 0),
            _check("无法追溯来源的商品行数", scalar(
                "SELECT COUNT(*) FROM products p LEFT JOIN product_raw r"
                " ON r.batch_id=p.source_batch_id AND r.csv_line=p.source_line"
                " AND r.import_result='inserted' WHERE r.raw_id IS NULL"), 0),
            _check("重量小于等于 0 的商品行数",
                   scalar("SELECT COUNT(*) FROM products WHERE weight_g<=0"), 0),
            _check("类目不在翻译表的商品行数", scalar(
                "SELECT COUNT(*) FROM products p LEFT JOIN category_translation t"
                " ON t.category_name=p.category_name WHERE t.category_name IS NULL"), 0),
        ]

    def list_products(self, limit, offset):
        limit = max(1, min(int(limit), MAX_PAGE))
        offset = max(0, int(offset))

        def fetch(cur):
            cur.execute("SELECT COUNT(*) FROM products")
            total = cur.fetchone()[0]
            cur.execute(
                "SELECT product_id, category_name, name_length, description_length, photos_qty,"
                " weight_g, length_cm, height_cm, width_cm, source_batch_id, source_line"
                " FROM products ORDER BY product_id LIMIT %s OFFSET %s", (limit, offset),
            )
            cols = [d[0] for d in cur.description]
            return total, [dict(zip(cols, row)) for row in cur.fetchall()]

        total, items = self._query(fetch)
        return {"total": total, "limit": limit, "offset": offset, "items": items}


def _check(name, actual, expected):
    return {"name": name, "actual": actual, "expected": expected, "ok": actual == expected}
