"""启动 FastAPI 演示服务（本地）。

用法（仓库根目录）：
    .venv/Scripts/python.exe scripts/run_api.py
然后打开 http://127.0.0.1:8000/docs 上传 CSV、预览并审批。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    import uvicorn

    uvicorn.run("src.api.app:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
