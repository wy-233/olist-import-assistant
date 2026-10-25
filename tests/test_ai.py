"""AI 审核测试：守卫、客户端、结构约束（无需网络与 Key）；接口测试只连 *_test 库。

AC-5 对应：
- 模型给错数字 / 虚构列 → 被拦，商品表与批次状态不变；
- 校验与导入模块不引用 AI 模块；AI 包不写业务表、没有审批路由；
- 路由表里的写接口只有 preview / approve / ai-review。
"""
import ast
import json
import os
import re
import unittest
from pathlib import Path
from unittest import mock

from src.ai import guard
from src.ai.client import DeepSeekClient, ModelError, ModelNotConfigured
from src.ai.contracts import MAX_HEADER_COLUMNS, MAX_SAMPLES, MAX_VALUE_LEN, REASON_CODES, STANDARD_COLUMNS
from src.ai.prompts import build_explain, build_mapping, kind_of, render_user
from src.ai.contracts import KIND_EXPLAIN, KIND_MAPPING
from src.db.connection import connect

try:
    import httpx
    from fastapi.testclient import TestClient

    from src.api.ai_routes import get_connect, get_model_client
    from src.api.app import app, get_store
    from src.api.service import REQUIRED_COLUMNS, Store
    HAVE_WEB = True
except ImportError:
    HAVE_WEB = False

from tests.test_import_db import FIXTURE, _rebuild_schema, _reachable

ROOT = Path(__file__).resolve().parent.parent
MIGRATION_003 = ROOT / "sql" / "migrations" / "003_ai_review_log.sql"
FACTS = {"total": 15, "valid": 3, "warning": 0, "rejected": 12, "importable": 3,
         **{code: 0 for code in REASON_CODES}, "CATEGORY_UNKNOWN": 4}
HEADER = list(STANDARD_COLUMNS)


def explain_json(**override):
    item = {"reason_code": "CATEGORY_UNKNOWN", "text": "类目不在翻译表中，因此无法给出可追溯的英文类目",
            "cited_numbers": {"rejected": 12, "CATEGORY_UNKNOWN": 4}}
    item.update(override)
    return json.dumps({"explanations": [item]}, ensure_ascii=False)


def mapping_json(*items):
    return json.dumps({"mappings": list(items)})


def m(src, dst, conf=0.9):
    return {"source_column": src, "target_column": dst, "confidence": conf}


class GuardExplainTest(unittest.TestCase):
    def assertBlocked(self, raw, fragment):
        result = guard.check_explain(raw, FACTS)
        self.assertEqual(result.verdict, "blocked", raw)
        self.assertTrue(any(fragment in v for v in result.violations), result.violations)
        self.assertIsNone(result.parsed)

    def test_valid_passes(self):
        result = guard.check_explain(explain_json(), FACTS)
        self.assertEqual((result.verdict, result.violations), ("passed", []))
        self.assertEqual(result.parsed["explanations"][0]["reason_code"], "CATEGORY_UNKNOWN")

    def test_wrong_number_is_blocked(self):
        self.assertBlocked(explain_json(cited_numbers={"rejected": 13}), "与代码计数 12 不符")

    def test_unknown_fact_name_is_blocked(self):
        self.assertBlocked(explain_json(cited_numbers={"products_inserted": 3}), "不存在的计数")

    def test_bool_is_not_a_number(self):
        self.assertBlocked(explain_json(cited_numbers={"warning": False}), "不符")

    def test_digits_in_text_are_blocked(self):
        self.assertBlocked(explain_json(text="共有 12 行被拒绝"), "含数字")
        self.assertBlocked(explain_json(text="共有１２行被拒绝"), "含数字")

    def test_unknown_reason_code_is_blocked(self):
        self.assertBlocked(explain_json(reason_code="PRICE_TOO_LOW"), "不是已知拒绝原因")

    def test_reason_code_without_hits_is_blocked(self):
        self.assertBlocked(explain_json(reason_code="ID_MISSING"), "没有命中行")

    def test_approval_or_fill_wording_is_blocked(self):
        for text in ("建议把类目填成其他类目", "可直接批准入库", "无需人工核对即可放行", "请补全缺失的重量",
                     "应将重量改为合理值", "可以换算为千克", "审批后即可"):
            self.assertBlocked(explain_json(text=text), "越权用语")

    def test_english_words_in_text_are_blocked_but_codes_and_columns_are_allowed(self):
        self.assertBlocked(explain_json(text="Please approve this batch"), "英文字母")
        ok = explain_json(text="原因码 CATEGORY_UNKNOWN 表示 product_category_name 不在翻译表中")
        self.assertEqual(guard.check_explain(ok, FACTS).verdict, "passed")

    def test_duplicate_json_keys_are_blocked(self):
        raw = '{"explanations": [{"reason_code": "CATEGORY_UNKNOWN", "text": "类目不在翻译表中",' \
              ' "text": "另一段", "cited_numbers": {}}]}'
        self.assertBlocked(raw, "重复键")

    def test_duplicate_reason_code_is_blocked(self):
        item = json.loads(explain_json())["explanations"][0]
        raw = json.dumps({"explanations": [item, item]}, ensure_ascii=False)
        self.assertBlocked(raw, "重复")

    def test_extra_keys_are_blocked(self):
        for key in ("new_value", "fill_value", "unit", "approve", "status"):
            item = json.loads(explain_json())["explanations"][0]
            item[key] = "x"
            self.assertBlocked(json.dumps({"explanations": [item]}, ensure_ascii=False), "未声明的键")

    def test_extra_top_level_key_is_blocked(self):
        raw = json.dumps({"explanations": json.loads(explain_json())["explanations"], "approve": True})
        self.assertBlocked(raw, "未声明的键")

    def test_not_json_or_empty_is_blocked(self):
        for raw in ("", "   ", "好的，以下是结果", "```json\n{}\n```", '{"explanations": NaN}', None):
            result = guard.check_explain(raw, FACTS)
            self.assertEqual(result.verdict, "blocked", raw)

    def test_empty_list_and_wrong_shapes_are_blocked(self):
        for raw in ('{"explanations": []}', '{"explanations": "x"}', "[]", '{"explanations": [1]}'):
            self.assertEqual(guard.check_explain(raw, FACTS).verdict, "blocked", raw)


class GuardMappingTest(unittest.TestCase):
    def assertBlocked(self, raw, fragment):
        result = guard.check_mapping(raw, HEADER)
        self.assertEqual(result.verdict, "blocked", raw)
        self.assertTrue(any(fragment in v for v in result.violations), result.violations)

    def test_valid_passes(self):
        raw = mapping_json(m("product_id", "product_id"), m("product_weight_g", "product_weight_g", 1))
        self.assertEqual(guard.check_mapping(raw, HEADER).verdict, "passed")

    def test_empty_mapping_list_passes(self):
        self.assertEqual(guard.check_mapping('{"mappings": []}', HEADER).verdict, "passed")

    def test_fabricated_source_column_is_blocked(self):
        self.assertBlocked(mapping_json(m("price", "product_id")), "虚构列")

    def test_non_standard_target_is_blocked(self):
        self.assertBlocked(mapping_json(m("product_id", "price")), "不在标准列清单")

    def test_duplicate_source_or_target_is_blocked(self):
        self.assertBlocked(mapping_json(m("product_id", "product_id"),
                                        m("product_id", "product_weight_g")), "被重复映射")
        self.assertBlocked(mapping_json(m("product_id", "product_id"),
                                        m("product_weight_g", "product_id")), "被重复映射")

    def test_value_or_unit_fields_are_blocked(self):
        for key, val in (("value", "0"), ("unit", "kg"), ("convert", "g->kg"), ("fill", 1)):
            item = m("product_weight_g", "product_weight_g")
            item[key] = val
            self.assertBlocked(mapping_json(item), "未声明的键")

    def test_missing_key_and_bad_confidence_are_blocked(self):
        self.assertBlocked(mapping_json({"source_column": "product_id", "target_column": "product_id"}), "缺少键")
        for conf in (1.5, -0.1, "high", True, None):
            self.assertBlocked(mapping_json(m("product_id", "product_id", conf)), "confidence")

    def test_unhashable_columns_do_not_crash(self):
        self.assertBlocked(mapping_json(m(["a"], "product_id")), "必须是字符串")

    def test_non_json_is_blocked(self):
        self.assertEqual(guard.check_mapping("product_id -> product_id", HEADER).verdict, "blocked")


class ClientTest(unittest.TestCase):
    KEY = "sk-test-FAKE-0123456789"

    def ok_transport(self, captured):
        def handler(request):
            captured.append(request)
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"mappings": []}'}}]})
        return httpx.MockTransport(handler)

    @unittest.skipUnless(HAVE_WEB, "需要 httpx")
    def test_request_shape_and_key_header(self):
        captured = []
        client = DeepSeekClient(self.KEY, model="m-test", transport=self.ok_transport(captured))
        self.assertEqual(client.complete("sys", "user"), '{"mappings": []}')
        request = captured[0]
        self.assertEqual(str(request.url), "https://api.deepseek.com/chat/completions")
        self.assertEqual(request.headers["authorization"], f"Bearer {self.KEY}")
        body = json.loads(request.content)
        self.assertEqual((body["model"], body["temperature"], body["stream"]), ("m-test", 0, False))
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual([x["role"] for x in body["messages"]], ["system", "user"])

    @unittest.skipUnless(HAVE_WEB, "需要 httpx")
    def test_errors_never_contain_the_key(self):
        def boom(request):
            raise httpx.ConnectError(f"cannot connect with {self.KEY}")

        cases = [
            httpx.MockTransport(boom),
            httpx.MockTransport(lambda r: httpx.Response(401, text=f"bad key {self.KEY}")),
            httpx.MockTransport(lambda r: httpx.Response(200, text="not json")),
            httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": []})),
        ]
        for transport in cases:
            with self.assertRaises(ModelError) as ctx:
                DeepSeekClient(self.KEY, transport=transport).complete("s", "u")
            self.assertNotIn(self.KEY, str(ctx.exception))
            self.assertNotIn(self.KEY, repr(ctx.exception))

    def test_repr_hides_key_and_missing_key_is_reported(self):
        client = DeepSeekClient(self.KEY)
        self.assertNotIn(self.KEY, repr(client))
        with self.assertRaises(ModelNotConfigured):
            DeepSeekClient("")
        with self.assertRaises(ModelNotConfigured):
            DeepSeekClient.from_env(env={})

    def test_from_env_reads_explicit_mapping_only(self):
        client = DeepSeekClient.from_env(env={"DEEPSEEK_API_KEY": self.KEY, "DEEPSEEK_MODEL": "m-x"})
        self.assertEqual(client.name, "m-x")

    def test_http_base_url_is_refused(self):
        with self.assertRaises(ModelError):
            DeepSeekClient(self.KEY, base_url="http://api.example.test")

    def test_empty_environment_variable_does_not_override_env_file(self):
        with mock.patch("src.ai.client.load_env", return_value={"DEEPSEEK_API_KEY": self.KEY}), \
                mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            client = DeepSeekClient.from_env()
        self.assertEqual(client._api_key, self.KEY)


class PromptTest(unittest.TestCase):
    def test_payloads_and_markers(self):
        payload, system = build_explain(FACTS, [])
        self.assertEqual(kind_of(system), KIND_EXPLAIN)
        self.assertEqual(set(payload["reason_codes"]), set(REASON_CODES))
        payload, system = build_mapping(HEADER)
        self.assertEqual(kind_of(system), KIND_MAPPING)
        self.assertEqual(payload["standard_columns"], HEADER)
        self.assertIn("header", json.loads(render_user(payload)))

    def test_mapping_header_is_clipped(self):
        payload, _ = build_mapping(["x" * 500] + [f"c{i}" for i in range(200)])
        self.assertEqual(len(payload["header"][0]), MAX_VALUE_LEN)
        self.assertEqual(len(payload["header"]), MAX_HEADER_COLUMNS)

    def test_standard_columns_match_service(self):
        if not HAVE_WEB:
            self.skipTest("需要 fastapi")
        self.assertEqual(STANDARD_COLUMNS, REQUIRED_COLUMNS)


def _imports(path):
    names = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


class StructureTest(unittest.TestCase):
    def test_business_modules_do_not_import_ai(self):
        offenders = []
        for sub in ("validation", "importing", "db"):
            for path in (ROOT / "src" / sub).rglob("*.py"):
                offenders += [f"{path.name}: {n}" for n in _imports(path) if n.split(".")[:2] == ["src", "ai"]]
        service = ROOT / "src" / "api" / "service.py"
        offenders += [n for n in _imports(service) if n.split(".")[:2] == ["src", "ai"]]
        self.assertEqual(offenders, [])

    def test_ai_package_does_not_import_importer_or_api(self):
        offenders = []
        for path in (ROOT / "src" / "ai").rglob("*.py"):
            offenders += [f"{path.name}: {n}" for n in _imports(path)
                          if n.split(".")[:2] in (["src", "importing"], ["src", "api"])]
        self.assertEqual(offenders, [])

    def test_ai_package_only_writes_ai_review_log(self):
        write = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\s+(\w+)", re.I)
        ddl = re.compile(r"\b(DROP|ALTER|TRUNCATE|CREATE)\s+(TABLE|DATABASE)\b", re.I)
        targets, offenders = [], []
        for path in (ROOT / "src" / "ai").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    targets += [mo.group(2) for mo in write.finditer(node.value)]
                    offenders += ddl.findall(node.value)
                    if "/approve" in node.value:
                        offenders.append(f"{path.name}: /approve")
        self.assertEqual(offenders, [])
        self.assertTrue(targets)
        self.assertEqual(set(targets), {"ai_review_log"})


@unittest.skipUnless(HAVE_WEB, "需要 fastapi/httpx")
class RouteTableTest(unittest.TestCase):
    def test_write_endpoints_are_exactly_the_known_ones(self):
        writes = {(method.upper(), path) for path, ops in app.openapi()["paths"].items()
                  for method in ops if method in ("post", "put", "patch", "delete")}
        self.assertEqual(writes, {
            ("POST", "/imports/preview"),
            ("POST", "/imports/{batch_id}/approve"),
            ("POST", "/imports/{batch_id}/ai-review"),
        })


class FakeClient:
    name = "fake-model"

    def __init__(self, explain=None, mapping=None, error=None):
        self.explain, self.mapping, self.error, self.calls = explain, mapping, error, []

    def complete(self, system, user):
        kind, payload = kind_of(system), json.loads(user)
        self.calls.append((kind, payload))
        if self.error:
            raise self.error
        handler = self.explain if kind == KIND_EXPLAIN else self.mapping
        return handler(payload) if callable(handler) else handler


def good_explain(payload):
    facts = payload["facts"]
    code = next(c for c in REASON_CODES if facts[c] > 0)
    return json.dumps({"explanations": [{
        "reason_code": code, "text": "这一类行被代码规则拒绝，需要人工核对原始数据",
        "cited_numbers": {"rejected": facts["rejected"], code: facts[code]},
    }]}, ensure_ascii=False)


def good_mapping(payload):
    return mapping_json(*[m(c, c) for c in payload["header"] if c in payload["standard_columns"]])


def _snapshot():
    """业务表的完整内容（不只是行数），任何改值、补值都会让快照不同。"""
    with connect(use_test=True) as conn, conn.cursor() as cur:
        out = {}
        cur.execute("SELECT * FROM products ORDER BY product_id")
        out["products"] = cur.fetchall()
        cur.execute("SELECT * FROM product_raw ORDER BY raw_id")
        out["product_raw"] = cur.fetchall()
        cur.execute("SELECT batch_id, status, mode, rows_total, rows_inserted, rows_skipped, rows_conflict,"
                    " rows_rejected, rows_pending, plan_fingerprint, approved_by, approved_at, finished_at,"
                    " file_sha256, MD5(file_content) FROM import_batch ORDER BY batch_id")
        out["batches"] = cur.fetchall()
        return out


def _scalar(sql, args=None):
    with connect(use_test=True) as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchone()[0]


@unittest.skipUnless(HAVE_WEB and _reachable(), "需要 fastapi/httpx 与可连接的 *_test 库")
class AiReviewApiTest(unittest.TestCase):
    def setUp(self):
        self.conn = connect(use_test=True)
        _rebuild_schema(self.conn)
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO category_translation (category_name, category_name_en) VALUES (%s, %s)",
                        ("cat_a", "Category A"))
        self.conn.commit()
        app.dependency_overrides[get_store] = lambda: Store(lambda: connect(use_test=True))
        app.dependency_overrides[get_connect] = lambda: (lambda: connect(use_test=True))
        self.fake = FakeClient(explain=good_explain, mapping=good_mapping)
        app.dependency_overrides[get_model_client] = lambda: self.fake
        self.client = TestClient(app)
        self.content = FIXTURE.read_bytes()

    def tearDown(self):
        app.dependency_overrides.clear()
        self.conn.close()

    def preview(self):
        response = self.client.post("/imports/preview", files={"file": ("t.csv", self.content, "text/csv")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["batch_id"]

    def review(self, batch_id):
        return self.client.post(f"/imports/{batch_id}/ai-review")

    def verdicts(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return {e["kind"]: e for e in response.json()["entries"]}

    def assertBusinessUnchanged(self, before):
        self.assertEqual(_snapshot(), before)

    def test_valid_output_is_logged_and_changes_nothing(self):
        batch_id = self.preview()
        before = _snapshot()
        entries = self.verdicts(self.review(batch_id))
        self.assertEqual(entries["explain"]["guard_verdict"], "passed")
        self.assertEqual(entries["mapping"]["guard_verdict"], "passed")
        self.assertIsNotNone(entries["explain"]["result"])
        self.assertBusinessUnchanged(before)
        self.assertEqual(_scalar("SELECT COUNT(*) FROM ai_review_log WHERE guard_verdict='passed'"), 2)
        self.assertEqual(_scalar("SELECT COUNT(*) FROM ai_review_log WHERE adopted<>0"), 0)
        self.assertEqual(_scalar("SELECT status FROM import_batch"), "previewed")

    def test_model_only_receives_header_counts_and_limited_samples(self):
        self.review(self.preview())
        explain = next(p for k, p in self.fake.calls if k == KIND_EXPLAIN)
        self.assertLessEqual(len(explain["rejected_samples"]), MAX_SAMPLES)
        self.assertEqual(set(explain), {"facts", "reason_codes", "rejected_samples"})
        for sample in explain["rejected_samples"]:
            self.assertEqual(set(sample), {"csv_line", "reasons", "raw"})
            self.assertTrue(all(v is None or len(v) <= MAX_VALUE_LEN for v in sample["raw"].values()))
        self.assertEqual(explain["facts"]["total"], 15)
        self.assertEqual(explain["facts"]["rejected"], 12)

    def test_wrong_number_is_blocked_and_business_tables_unchanged(self):
        batch_id = self.preview()
        before = _snapshot()
        self.fake.explain = lambda p: explain_json(cited_numbers={"rejected": p["facts"]["rejected"] + 1},
                                                   reason_code="CATEGORY_UNKNOWN")
        entries = self.verdicts(self.review(batch_id))
        self.assertEqual(entries["explain"]["guard_verdict"], "blocked")
        self.assertIsNone(entries["explain"]["result"])
        self.assertTrue(any("不符" in v for v in entries["explain"]["violations"]))
        self.assertBusinessUnchanged(before)
        row = _scalar("SELECT parsed_json IS NULL AND raw_output IS NOT NULL FROM ai_review_log WHERE kind='explain'")
        self.assertEqual(row, 1)

    def test_approval_or_fill_wording_is_blocked_and_business_tables_unchanged(self):
        batch_id = self.preview()
        before = _snapshot()
        for text in ("建议把类目填成其他类目，可直接批准入库", "请补全缺失的重量后放行"):
            self.fake.explain = lambda p, t=text: explain_json(text=t)
            entries = self.verdicts(self.review(batch_id))
            self.assertEqual(entries["explain"]["guard_verdict"], "blocked")
            self.assertTrue(any("越权用语" in v for v in entries["explain"]["violations"]))
            self.assertBusinessUnchanged(before)

    def test_explain_is_skipped_when_nothing_was_rejected(self):
        header = self.content.decode("utf-8").splitlines()[0]
        self.content = (header + "\nTEST_ONLY_ok,cat_a,10,20,1,100,10,10,10\n").encode("utf-8")
        entries = self.verdicts(self.review(self.preview()))
        self.assertEqual(set(entries), {"mapping"})
        self.assertEqual([kind for kind, _ in self.fake.calls], [KIND_MAPPING])

    def test_fabricated_column_is_blocked_and_business_tables_unchanged(self):
        batch_id = self.preview()
        before = _snapshot()
        self.fake.mapping = mapping_json(m("price_brl", "product_id"))
        entries = self.verdicts(self.review(batch_id))
        self.assertEqual(entries["mapping"]["guard_verdict"], "blocked")
        self.assertTrue(any("虚构列" in v for v in entries["mapping"]["violations"]))
        self.assertBusinessUnchanged(before)

    def test_model_failure_is_logged_as_error(self):
        batch_id = self.preview()
        before = _snapshot()
        self.fake.error = ModelError("模型服务返回 HTTP 500")
        entries = self.verdicts(self.review(batch_id))
        self.assertEqual({e["guard_verdict"] for e in entries.values()}, {"error"})
        self.assertBusinessUnchanged(before)

    def test_unexpected_exception_does_not_leak_message(self):
        batch_id = self.preview()
        self.fake.error = RuntimeError("secret sk-test-LEAK in message")
        response = self.review(batch_id)
        self.assertNotIn("sk-test-LEAK", response.text)
        self.assertEqual({e["guard_verdict"] for e in self.verdicts(response).values()}, {"error"})

    def test_previewed_batch_can_still_be_approved_after_ai_review(self):
        batch_id = self.preview()
        self.review(batch_id)
        approve = self.client.post(f"/imports/{batch_id}/approve",
                                   data={"approver": "唯一", "confirm": f"APPROVE {batch_id} 3"})
        self.assertEqual(approve.status_code, 200, approve.text)
        self.assertEqual(_scalar("SELECT COUNT(*) FROM products"), 3)

    def test_succeeded_batch_status_unchanged_by_ai_review(self):
        batch_id = self.preview()
        self.client.post(f"/imports/{batch_id}/approve",
                         data={"approver": "唯一", "confirm": f"APPROVE {batch_id} 3"})
        before = _snapshot()
        self.verdicts(self.review(batch_id))
        self.assertBusinessUnchanged(before)
        self.assertEqual(_scalar("SELECT status FROM import_batch"), "succeeded")

    def test_missing_batch_and_missing_content(self):
        self.assertEqual(self.review(999).status_code, 404)
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO import_batch (file_name, file_sha256, mode, status, started_at)"
                        " VALUES ('cli.csv', %s, 'apply', 'succeeded', NOW())", ("0" * 64,))
            batch_id = cur.lastrowid
        self.conn.commit()
        response = self.review(batch_id)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(_scalar("SELECT COUNT(*) FROM ai_review_log"), 0)

    def test_not_configured_returns_503(self):
        def missing():
            from fastapi import HTTPException
            raise HTTPException(status_code=503, detail={"code": "ai_not_configured", "message": "x"})

        app.dependency_overrides[get_model_client] = missing
        self.assertEqual(self.review(self.preview()).status_code, 503)

    def test_real_client_with_mock_transport_never_leaks_key(self):
        key = "sk-test-FAKE-ABCDEF"
        seen = []

        def handler(request):
            seen.append(request.headers["authorization"])
            body = json.loads(request.content)
            payload = json.loads(body["messages"][1]["content"])
            kind = kind_of(body["messages"][0]["content"])
            content = good_explain(payload) if kind == KIND_EXPLAIN else good_mapping(payload)
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        client = DeepSeekClient(key, model="deepseek-test", transport=httpx.MockTransport(handler))
        app.dependency_overrides[get_model_client] = lambda: client
        response = self.review(self.preview())
        self.assertEqual(self.verdicts(response)["explain"]["guard_verdict"], "passed")
        self.assertEqual(seen, [f"Bearer {key}"] * 2)
        self.assertNotIn(key, response.text)
        columns = ("input_json", "raw_output", "parsed_json", "guard_violations", "model_name")
        for column in columns:
            self.assertEqual(_scalar(f"SELECT COUNT(*) FROM ai_review_log WHERE {column} LIKE %s",
                                     (f"%{key}%",)), 0)
        self.assertEqual(_scalar("SELECT model_name FROM ai_review_log LIMIT 1"), "deepseek-test")


@unittest.skipUnless(_reachable(), "测试库不可连接或库名不以 _test 结尾")
class AiLogMigrationParityTest(unittest.TestCase):
    """003 迁移得到的 ai_review_log 应与 schema.sql 新建的完全一致。"""

    @staticmethod
    def _dump(cur):
        cur.execute("SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS"
                    " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ai_review_log' ORDER BY ORDINAL_POSITION")
        columns = cur.fetchall()
        cur.execute("SELECT INDEX_NAME, COLUMN_NAME, SEQ_IN_INDEX FROM information_schema.STATISTICS"
                    " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ai_review_log' ORDER BY INDEX_NAME, SEQ_IN_INDEX")
        indexes = cur.fetchall()
        cur.execute("SELECT CONSTRAINT_NAME, REFERENCED_TABLE_NAME, UPDATE_RULE, DELETE_RULE"
                    " FROM information_schema.REFERENTIAL_CONSTRAINTS"
                    " WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='ai_review_log'")
        foreign_keys = cur.fetchall()
        cur.execute("SELECT ENGINE, TABLE_COLLATION FROM information_schema.TABLES"
                    " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ai_review_log'")
        return columns, indexes, foreign_keys, cur.fetchall()

    def test_migration_matches_fresh_schema(self):
        conn = connect(use_test=True)
        try:
            _rebuild_schema(conn)
            with conn.cursor() as cur:
                cur.execute("DROP TABLE ai_review_log")
                lines = [l for l in MIGRATION_003.read_text(encoding="utf-8").splitlines()
                         if not l.strip().startswith("--")]
                for stmt in "\n".join(lines).split(";"):
                    if stmt.strip():
                        cur.execute(stmt)
            conn.commit()
            with conn.cursor() as cur:
                migrated = self._dump(cur)
            _rebuild_schema(conn)
            with conn.cursor() as cur:
                fresh = self._dump(cur)
            self.assertEqual(migrated, fresh)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
