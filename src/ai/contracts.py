"""AI 审核的输入输出契约与常量。本包只读批次数据，只写 ai_review_log。"""
from src.validation.validator import REJECT_REASONS

KIND_EXPLAIN = "explain"
KIND_MAPPING = "mapping"
KINDS = (KIND_EXPLAIN, KIND_MAPPING)

PASSED = "passed"
BLOCKED = "blocked"
ERROR = "error"

# 与 src.api.service.REQUIRED_COLUMNS 必须一致（tests/test_ai.py 校验），这里不 import api 层。
STANDARD_COLUMNS = (
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

REASON_CODES = tuple(REJECT_REASONS)

MAX_SAMPLES = 5
MAX_HEADER_COLUMNS = 50
MAX_VALUE_LEN = 64
MAX_TEXT_LEN = 500
MAX_RAW_OUTPUT_CHARS = 100_000

REASON_DESCRIPTIONS = {
    "ID_MISSING": "product_id 为空",
    "ID_DUPLICATE": "product_id 在文件中重复出现（保留第一条，其后的行被拒绝）",
    "NON_INTEGER": "数值列含非整数文本",
    "DIM_MISSING": "重量或长、高、宽任一为空",
    "WEIGHT_NONPOSITIVE": "重量小于等于 0",
    "DIM_NONPOSITIVE": "长、高、宽任一小于等于 0",
    "CATEGORY_EMPTY": "类目为空",
    "CATEGORY_UNKNOWN": "类目不在类目翻译表中",
}
