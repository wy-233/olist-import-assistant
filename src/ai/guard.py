"""AI 输出守卫：纯函数，不访问数据库、不联网。

任何违规都整条拦截（blocked），不部分采纳。守卫只检查结构与可核对的事实：
- 严格的键白名单：出现 value / fill / approve / unit 等任何未声明的键即越权；
- 数字只能出现在 cited_numbers 里，且必须与代码算出的 facts 逐项相等；说明文字里不允许出现数字；
- 映射的源列必须在上传表头里，目标列必须在标准列清单里，且不得重复。
"""
import json
import re

from src.ai.contracts import BLOCKED, MAX_TEXT_LEN, PASSED, REASON_CODES, STANDARD_COLUMNS

_DIGIT = re.compile(r"\d")
_ASCII_WORD = re.compile(r"[A-Za-z]")
# 启发式：说明文字里出现这些词，视为在建议改值、补值、批准或入库。无法穷尽语义，只是多一道防线。
FORBIDDEN_PHRASES = (
    "批准", "审批", "入库", "填", "补", "改为", "改成", "修改", "替换", "默认值",
    "删除", "换算", "转换", "无需人工", "不必人工", "直接",
)


class GuardResult:
    def __init__(self, violations, parsed=None):
        self.violations = list(violations)
        self.parsed = parsed if not self.violations else None

    @property
    def verdict(self):
        return BLOCKED if self.violations else PASSED


def _reject_constant(name):
    raise ValueError(f"非法 JSON 常量：{name}")


def _no_duplicate_keys(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("JSON 对象含重复键")
    return dict(pairs)


def _parse(raw_output):
    if not isinstance(raw_output, str) or not raw_output.strip():
        return None, ["输出为空"]
    try:
        return json.loads(raw_output, parse_constant=_reject_constant,
                          object_pairs_hook=_no_duplicate_keys), []
    except ValueError as exc:
        return None, [f"不是合法 JSON：{exc}"]


def _text_violations(text, where):
    out = []
    if len(text) > MAX_TEXT_LEN:
        out.append(f"{where}.text 超过 {MAX_TEXT_LEN} 字")
    if _DIGIT.search(text):
        out.append(f"{where}.text 含数字；数字只能放在 cited_numbers 中")
    hits = [p for p in FORBIDDEN_PHRASES if p in text]
    if hits:
        out.append(f"{where}.text 含越权用语：{'、'.join(hits)}")
    stripped = text
    for name in (*REASON_CODES, *STANDARD_COLUMNS):
        stripped = stripped.replace(name, "")
    if _ASCII_WORD.search(stripped):
        out.append(f"{where}.text 含除原因码和标准列名之外的英文字母")
    return out


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _check_keys(obj, allowed, where):
    if not isinstance(obj, dict):
        return [f"{where} 必须是对象"]
    extra = sorted(set(obj) - set(allowed))
    missing = sorted(set(allowed) - set(obj))
    out = []
    if extra:
        out.append(f"{where} 含未声明的键：{', '.join(map(str, extra))}")
    if missing:
        out.append(f"{where} 缺少键：{', '.join(missing)}")
    return out


def check_explain(raw_output, facts):
    """facts: 代码算出的 {名称: 整数}。cited_numbers 的每一项都必须与之完全相等。"""
    data, violations = _parse(raw_output)
    if violations:
        return GuardResult(violations)
    violations = _check_keys(data, ("explanations",), "顶层")
    if violations:
        return GuardResult(violations)
    items = data["explanations"]
    if not isinstance(items, list) or not items:
        return GuardResult(["explanations 必须是非空数组"])

    seen = set()
    for i, item in enumerate(items):
        where = f"explanations[{i}]"
        bad = _check_keys(item, ("reason_code", "text", "cited_numbers"), where)
        if bad:
            violations += bad
            continue
        code, text, cited = item["reason_code"], item["text"], item["cited_numbers"]
        if code not in REASON_CODES:
            violations.append(f"{where}.reason_code 不是已知拒绝原因：{code!r}")
        elif facts.get(code, 0) <= 0:
            violations.append(f"{where}.reason_code 在本批中没有命中行：{code}")
        elif code in seen:
            violations.append(f"{where}.reason_code 重复：{code}")
        else:
            seen.add(code)
        if not isinstance(text, str) or not text.strip():
            violations.append(f"{where}.text 必须是非空字符串")
        else:
            violations += _text_violations(text, where)
        if not isinstance(cited, dict):
            violations.append(f"{where}.cited_numbers 必须是对象")
            continue
        for name, value in cited.items():
            if name not in facts:
                violations.append(f"{where}.cited_numbers 引用了不存在的计数：{name!r}")
            elif not _is_int(value) or value != facts[name]:
                violations.append(
                    f"{where}.cited_numbers[{name!r}]={value!r} 与代码计数 {facts[name]} 不符"
                )
    return GuardResult(violations, data)


def check_mapping(raw_output, header, standard_columns=STANDARD_COLUMNS):
    data, violations = _parse(raw_output)
    if violations:
        return GuardResult(violations)
    violations = _check_keys(data, ("mappings",), "顶层")
    if violations:
        return GuardResult(violations)
    items = data["mappings"]
    if not isinstance(items, list):
        return GuardResult(["mappings 必须是数组"])
    if len(items) > len(standard_columns):
        violations.append("mappings 条数超过标准列数")

    sources, targets = set(), set()
    for i, item in enumerate(items):
        where = f"mappings[{i}]"
        bad = _check_keys(item, ("source_column", "target_column", "confidence"), where)
        if bad:
            violations += bad
            continue
        src, dst, conf = item["source_column"], item["target_column"], item["confidence"]
        if not isinstance(src, str) or not isinstance(dst, str):
            violations.append(f"{where}.source_column 与 target_column 必须是字符串")
            continue
        if src not in header:
            violations.append(f"{where}.source_column 不在上传表头中（虚构列）：{src!r}")
        if dst not in standard_columns:
            violations.append(f"{where}.target_column 不在标准列清单中：{dst!r}")
        if src in sources:
            violations.append(f"{where}.source_column 被重复映射：{src!r}")
        if dst in targets:
            violations.append(f"{where}.target_column 被重复映射：{dst!r}")
        sources.add(src)
        targets.add(dst)
        if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
            violations.append(f"{where}.confidence 必须是 0 到 1 的数字")
    return GuardResult(violations, data)
