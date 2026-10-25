"""AI 审核编排：只读批次数据 → 调用模型 → 守卫 → 只写 ai_review_log。

本模块不 import 导入器或 API 业务层，对业务表只有 SELECT；唯一的写操作是插入 ai_review_log。
模型的任何输出都先过守卫；被拦截或调用失败的结果也会记入日志，但不产生任何业务副作用。
"""
import hashlib
import io
import json
from datetime import datetime

from src.ai import guard, prompts
from src.ai.client import ModelError
from src.ai.contracts import (
    ERROR, KIND_EXPLAIN, KIND_MAPPING, KINDS, MAX_RAW_OUTPUT_CHARS, MAX_SAMPLES,
    MAX_VALUE_LEN, PASSED, STANDARD_COLUMNS,
)
from src.validation.validator import read_csv_stream, summarize, validate_rows


class ReviewError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class BatchNotFound(ReviewError):
    status_code = 404
    code = "not_found"


class BatchUnreadable(ReviewError):
    status_code = 409
    code = "conflict"


def _clip(value):
    return None if value is None else value[:MAX_VALUE_LEN]


def load_inputs(cur, batch_id):
    """只读：返回 (表头, facts, 拒绝样本)。facts 的每个数字都由代码计算。"""
    cur.execute("SELECT file_content FROM import_batch WHERE batch_id=%s", (batch_id,))
    row = cur.fetchone()
    if row is None:
        raise BatchNotFound(f"批次 {batch_id} 不存在")
    if row[0] is None:
        raise BatchUnreadable("该批次没有保存上传内容（可能由命令行脚本创建），无法做 AI 审核")
    cur.execute("SELECT category_name FROM category_translation")
    keys = {r[0].strip() for r in cur.fetchall()}
    try:
        header, rows = read_csv_stream(io.StringIO(bytes(row[0]).decode("utf-8-sig"), newline=""))
    except (ValueError, UnicodeDecodeError):
        raise BatchUnreadable("批次保存的内容无法解析") from None

    results = validate_rows(rows, keys)
    check = summarize(results)
    facts = {key: check[key] for key in ("total", "valid", "warning", "rejected", "importable")}
    facts.update(check["by_reason"])
    samples = [
        {
            "csv_line": r.csv_line,
            "reasons": list(r.reasons),
            "raw": {c: _clip(r.raw.get(c)) for c in STANDARD_COLUMNS if c in r.raw},
        }
        for r in results if r.status == "rejected"
    ][:MAX_SAMPLES]
    return header, facts, samples


def _write_log(connect, batch_id, kind, model_name, prompt_sha, payload, raw, parsed, verdict, violations):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_review_log (batch_id, kind, model_name, prompt_sha256, input_json,"
                " raw_output, parsed_json, guard_verdict, guard_violations, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (batch_id, kind, model_name, prompt_sha,
                 json.dumps(payload, ensure_ascii=False, sort_keys=True),
                 raw[:MAX_RAW_OUTPUT_CHARS] if isinstance(raw, str) else None,
                 json.dumps(parsed, ensure_ascii=False) if parsed is not None else None,
                 verdict, json.dumps(violations, ensure_ascii=False), datetime.now()),
            )
            log_id = cur.lastrowid
        conn.commit()
        return log_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_review(connect, client, batch_id, kinds=KINDS):
    """对一个批次依次做所选类型的审核，返回每条日志的摘要。connect 是无参可调用对象。"""
    conn = connect()
    try:
        with conn.cursor() as cur:
            header, facts, samples = load_inputs(cur, batch_id)
    finally:
        conn.close()

    entries = []
    for kind in kinds:
        if kind == KIND_EXPLAIN:
            if facts["rejected"] == 0:
                continue  # 没有拒绝行就没有可解释的内容，不调用模型
            payload, system = prompts.build_explain(facts, samples)
        elif kind == KIND_MAPPING:
            payload, system = prompts.build_mapping(header)
        else:
            raise ValueError(f"未知审核类型：{kind}")
        user = prompts.render_user(payload)
        prompt_sha = hashlib.sha256((system + "\n" + user).encode("utf-8")).hexdigest()

        raw = parsed = None
        try:
            raw = client.complete(system, user)
        except ModelError as exc:
            verdict, violations = ERROR, [str(exc)]
        except Exception as exc:  # 不回显异常内容，避免带出请求头等敏感信息
            verdict, violations = ERROR, [f"调用模型时发生 {type(exc).__name__}"]
        else:
            if kind == KIND_EXPLAIN:
                result = guard.check_explain(raw, facts)
            else:
                result = guard.check_mapping(raw, header)
            verdict, violations, parsed = result.verdict, result.violations, result.parsed

        log_id = _write_log(connect, batch_id, kind, client.name, prompt_sha, payload,
                            raw, parsed, verdict, violations)
        entries.append({
            "log_id": log_id,
            "kind": kind,
            "model_name": client.name,
            "guard_verdict": verdict,
            "violations": violations,
            "result": parsed if verdict == PASSED else None,
        })
    return entries
