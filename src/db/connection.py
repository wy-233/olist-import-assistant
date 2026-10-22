"""数据库连接：从 .env 读取配置，不落盘任何密钥。

.env 已在 .gitignore 中；本模块只返回连接对象，不打印任何凭据。
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = ROOT / ".env"

DEFAULT_DB = "olist_import"
TEST_SUFFIX = "_test"


def load_env(path=ENV_FILE):
    """读取 KEY=VALUE 形式的 .env；不覆盖已存在的环境变量。"""
    values = {}
    if not Path(path).exists():
        return values
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def db_config(use_test=False):
    """返回连接参数。缺项时明确报错，不猜测、不使用默认密码。"""
    env = load_env()
    env.update({k: v for k, v in os.environ.items() if k.startswith("OLIST_DB_")})
    missing = [k for k in ("OLIST_DB_USER", "OLIST_DB_PASSWORD") if not env.get(k)]
    if missing:
        raise RuntimeError(f".env 缺少配置：{', '.join(missing)}（密码不会从代码或默认值读取）")
    name_key = "OLIST_DB_NAME_TEST" if use_test else "OLIST_DB_NAME"
    name = env.get(name_key) or (env.get("OLIST_DB_NAME") if use_test else DEFAULT_DB)
    if use_test and not name.endswith(TEST_SUFFIX):
        raise RuntimeError(f"测试连接的库名必须以 {TEST_SUFFIX} 结尾，当前为 {name}")
    return {
        "host": env.get("OLIST_DB_HOST", "127.0.0.1"),
        "port": int(env.get("OLIST_DB_PORT", "3306")),
        "user": env["OLIST_DB_USER"],
        "password": env["OLIST_DB_PASSWORD"],
        "database": name,
        "charset": "utf8mb4",
    }


def connect(use_test=False):
    import pymysql

    return pymysql.connect(**db_config(use_test), autocommit=False)
