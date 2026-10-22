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
.venv/Scripts/python.exe -m pip install pymysql
mysql -u root -p olist_import < sql/schema.sql              # 建表（会 DROP 同名表，仅学习环境）
.venv/Scripts/python.exe scripts/load_translation.py        # 载入 71 行类目翻译
.venv/Scripts/python.exe scripts/import_products.py --dry-run   # 只计算，不写库
.venv/Scripts/python.exe scripts/import_products.py --apply     # 导入；同文件再跑一次应为 0 新增
.venv/Scripts/python.exe scripts/run_import_report.py       # 生成 docs/import-report.md
.venv/Scripts/python.exe -m unittest discover -s tests -t . # 全部测试
```

独立对账 SQL：`sql/reconcile.sql`。
