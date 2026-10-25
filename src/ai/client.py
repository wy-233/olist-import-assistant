"""模型客户端。DeepSeek 实现是本包唯一的联网点。

API Key 只从环境变量或被 Git 忽略的 .env 读取；不进入日志、异常信息、repr 或响应。
"""
import os

from src.db.connection import load_env

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
ENV_KEYS = ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL")


class ModelError(Exception):
    """调用模型失败。消息由本模块构造，不含 Key 和响应原文。"""


class ModelNotConfigured(ModelError):
    pass


class DeepSeekClient:
    def __init__(self, api_key, model=DEFAULT_MODEL, base_url=DEFAULT_BASE_URL,
                 timeout=60.0, transport=None):
        if not api_key:
            raise ModelNotConfigured("未配置 DEEPSEEK_API_KEY（请在 .env 中填写）")
        if not base_url.startswith("https://"):
            raise ModelError("DEEPSEEK_BASE_URL 必须以 https:// 开头")
        self._api_key = api_key
        self.name = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    @classmethod
    def from_env(cls, env=None):
        if env is None:
            env = {k: v for k, v in load_env().items() if k in ENV_KEYS and v}
            env.update({k: v for k, v in os.environ.items() if k in ENV_KEYS and v})
        return cls(
            env.get("DEEPSEEK_API_KEY", ""),
            model=env.get("DEEPSEEK_MODEL") or DEFAULT_MODEL,
            base_url=env.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL,
        )

    def __repr__(self):
        return f"DeepSeekClient(model={self.name!r})"

    def complete(self, system, user):
        import httpx

        body = {
            "model": self.name,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": 2048,
            "stream": False,
        }
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as http:
                response = http.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=body,
                )
        except httpx.HTTPError as exc:
            raise ModelError(f"调用模型失败：{type(exc).__name__}") from None
        if response.status_code != 200:
            raise ModelError(f"模型服务返回 HTTP {response.status_code}")
        try:
            return response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise ModelError("模型响应格式不符合预期") from None
