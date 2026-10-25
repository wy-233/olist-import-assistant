# 电商商品资料入库与异常复核助手（学习 MVP）

把一份真实的商品目录表格，经过**代码规则校验**和**人工审批**后，可追溯地导入 MySQL。
大模型只提供字段映射建议和异常解释，不修改数据、不补造缺失值、不批准入库。

> 本项目用于学习与作品展示，不用于生产部署。

## 数据来源

- 数据集：Olist Brazilian E-Commerce Public Dataset（Kaggle）
- 使用文件：商品目录 `olist_products_dataset.csv`、类目翻译 `product_category_name_translation.csv`
- 来源链接、下载日期、许可条款与文件哈希：记录在本地 `data/SOURCE.md`（不随仓库上传）
- 原始文件放在 `data/raw/`，不提交到 Git

该数据不是天猫数据，也不是任何目标公司的数据。

## 项目范围

- 只处理商品目录及类目翻译，不涉及订单、价格、库存。
- 不补造价格、库存、订单或缺失字段；缺失值记录为异常或提醒。
- 技术栈：Python、FastAPI、MySQL、一个大模型 API；用 FastAPI 自带 `/docs` 演示，不做前端。
- 导入流程：预览 → 人工审批 → 写入。

## 核心约束

1. 预览（preview）不写入商品表。
2. 所有计数和判错由代码或 SQL 产生，AI 输出不参与。
3. AI 输出只写入 AI 审核日志；被采纳的字段映射只改列名，不转换值或单位。
4. 原始值按字符串原样保存，入库值由代码解析。
5. 行数可对账：原始行数 = 有效 + 警告 + 拒绝；可导入行 = 新增 + 相同跳过 + 冲突。

## 文档

- [数据字典](docs/data-dictionary.md)
- [数据盘点报告](docs/data-profile.md)
- [校验报告](docs/validation-report.md)
- [导入报告](docs/import-report.md)
- [演示脚本](docs/demo-script.md)

## 启动方式

前置：本机 MySQL 5.7，建库 `olist_import` 与 `olist_import_test`（utf8mb4），项目根目录建 `.env`（已被 Git 忽略）：

```
OLIST_DB_HOST=127.0.0.1
OLIST_DB_PORT=3306
OLIST_DB_USER=root
OLIST_DB_PASSWORD=<你的密码>
OLIST_DB_NAME=olist_import
OLIST_DB_NAME_TEST=olist_import_test
```

在仓库根目录依次执行：

```
.venv/Scripts/python.exe -m pip install pymysql fastapi uvicorn httpx python-multipart
mysql -u root -p olist_import < sql/schema.sql              # 建表（会 DROP 同名表，仅学习环境）
.venv/Scripts/python.exe scripts/load_translation.py        # 载入 71 行类目翻译
.venv/Scripts/python.exe scripts/import_products.py --dry-run   # 只计算，不写库
.venv/Scripts/python.exe scripts/import_products.py --apply     # 导入；同文件再跑一次应为 0 新增
.venv/Scripts/python.exe scripts/run_import_report.py       # 生成 docs/import-report.md
.venv/Scripts/python.exe -m unittest discover -s tests -t . # 全部测试
```

第 4 天已建好表、不想重建时，先备份再执行一次迁移（只跑一次）：

```
mysqldump -u root -p olist_import > backup_before_002.sql
mysql -u root -p olist_import < sql/migrations/002_preview_approval.sql
```

独立对账 SQL：`sql/reconcile.sql`。

## 网页端（第 5 天）

```
.venv/Scripts/python.exe scripts/run_api.py     # http://127.0.0.1:8000/docs
```

| 接口 | 作用 |
|---|---|
| `POST /imports/preview` | 上传 CSV，返回校验与导入计划；只写一条预览记录，不写商品表 |
| `POST /imports/{id}/approve` | 人工审批并写入；确认文本为 `APPROVE <id> <预览新增数>` |
| `GET /imports/{id}/report` | 批次报告与对账检查 |
| `GET /products` | 分页查询已入库商品 |

没有认证：`approver` 仅留痕，不构成权限控制。完整演示步骤见 [演示脚本](docs/demo-script.md)。

## AI 审核（第 6 天）

| 接口 | 作用 |
|---|---|
| `POST /imports/{id}/ai-review` | 对一个批次请求 DeepSeek 做“异常解释”和“字段映射建议”；输出先过守卫，再写入 `ai_review_log` |

- AI 不改商品数据、不改批次状态、不审批；映射建议只记日志（`adopted` 恒为 0，采纳功能未实现）。
- 发给外部模型的内容只有：表头、代码算出的计数、最多 5 条拒绝样本（每个值截断到 64 字符）。
- 守卫整条拦截：数字与代码计数不符、说明文字含数字、虚构列、非标准目标列、重复映射、任何未声明的键（如 value / fill / approve）、非 JSON 输出。被拦截和调用失败的结果也写入日志，状态分别为 `blocked` / `error`。
- 当前上传预览要求表头已是标准列名，所以映射建议在现有流程里基本是“恒等映射”，主要用于证明守卫有效。

在 `.env` 中增加（`.env` 已被 Git 忽略，Key 不要写进代码或文档）：

```
DEEPSEEK_API_KEY=<你的 Key>
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

后两项可省略，默认值如上；模型名请以 DeepSeek 官方文档为准。未配置 Key 时接口返回 503。
每次请求最多触发 2 次付费模型调用（本批没有拒绝行时只调用 1 次），并写入对应的日志行；接口没有鉴权与限流。

对已有库先备份再执行一次迁移（只跑一次，不改动已有表）：

```
mysqldump -u root -p olist_import > backup_before_003.sql
mysql -u root -p olist_import < sql/migrations/003_ai_review_log.sql
```
