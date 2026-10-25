# 演示脚本（第 7 天定稿）

> 目标：在 `/docs` 中完整走一遍 上传 → 预览 → 审批 → 查询 → 报告，并展示 AI 审核只记日志。
> 全程没有认证：`approver` 是可自报的字符串，只用于留痕，**不构成权限控制**。
> 录屏需人工在 `/docs` 操作；录屏文件不随仓库提交。录屏时不要展示 `.env` 或任何 Key。

## 0. 启动

```
.venv/Scripts/python.exe -m pip install fastapi uvicorn httpx python-multipart
.venv/Scripts/python.exe scripts/run_api.py
```

打开 <http://127.0.0.1:8000/docs>。

前置：
- 本机 MySQL 已启动，`olist_import` 已按 `sql/schema.sql` 建表（或对已有库先备份，再依次执行一次
  `sql/migrations/002_preview_approval.sql` 与 `sql/migrations/003_ai_review_log.sql`），
  且 `category_translation` 已载入 71 行。
- 仅演示第 7 步（AI 审核）需要：`.env` 中配置 `DEEPSEEK_API_KEY`。

## 1. 预览（AC-2：预览不写商品表）

1. 展开 `POST /imports/preview` → **Try it out**。
2. `file` 选择 `data/raw/olist_products_dataset.csv`，**Execute**。
3. 记录响应中的 `batch_id`（记为 `<id>`）与 `counts`：

| 字段 | 预期 |
|---|---|
| `counts.rows_total` | 32951 |
| `counts.rows_inserted` | 首次演示为 32323；已在第 4 天导入过的库为 0 |
| `counts.rows_skipped` | 已在第 4 天导入过的库为 32323 |
| `counts.rows_conflict` | 0 |
| `counts.rows_rejected` | 628 |
| `counts.reconciled` | true |
| `written` | `{"products": 0, "product_raw": 0}` |

4. 验证没有写入商品表：`GET /products` 返回的 `total` 与预览前一致；或执行
   `SELECT COUNT(*) FROM products;` 与预览前比较。

## 2. 审批前无法写入（AC-2）

1. 直接调用 `POST /imports/<id>/approve`，`confirm` 随便填（如 `yes`）→ 返回 **400**，提示正确的确认文本。
2. 此时 `GET /products` 的 `total` 仍与预览前一致。

## 3. 审批写入

1. 在 `POST /imports/<id>/approve` 填入：
   - `approver`：自报的审批人名称（仅留痕）
   - `confirm`：`APPROVE <id> <预览时的 rows_inserted>`
2. **Execute** → 200，响应中的四类计数与预览一致，`reconciled` 为 true。
3. 再点一次 **Execute** → **409**（批次已不是 `previewed`，重复审批被拒）。
4. `GET /products?limit=5` → `total` 增加，且可看到 `source_batch_id = <id>` 的商品。

## 4. 报告与对账（AC-3）

1. `GET /imports/<id>/report` → 检查 `checks` 每项 `ok` 为 true，`all_ok` 为 true。
2. 对账两条主线：
   - 本批内容：原始行 = 有效 + 警告 + 拒绝；
   - 可导入 = 新增 + 相同跳过 + 冲突。
3. 独立核对（另开终端，mysql CLI）：
   ```
   mysql -u root -p olist_import < sql/reconcile.sql
   ```
   `sql/reconcile.sql` 全部为只读 SELECT，共 8 项检查；输出应与报告接口和 `docs/import-report.md` 一致。

## 5. 幂等

再次执行 `POST /imports/preview` 上传同一文件 → 预览显示 `rows_inserted = 0`、
`rows_skipped = 32323`；审批后 `products` 行数不变。

## 6. 分页查询

`GET /products?limit=5&offset=0` → 返回最多 5 条商品与 `total`；`limit` 允许 1–200，`offset` 不小于 0。

## 7. AI 审核（只记日志，需要 Key）

1. 展开 `POST /imports/<id>/ai-review` → **Try it out**，`batch_id` 填一个由网页上传创建的批次（如第 1 步的 `<id>`）→ **Execute**。
2. 响应 `entries` 中最多两条：
   - `kind = explain`：对拒绝行的说明（本批没有拒绝行时不会调用模型，也没有这一条）；
   - `kind = mapping`：字段映射建议（当前预览要求表头已是标准列名，所以基本是恒等映射）。
3. 检查每条的 `guard_verdict`：`passed` 表示通过守卫；`blocked` 表示被拦截，`violations` 列出原因；`error` 表示调用失败。
4. 验证 AI 没有改动任何数据：
   - `GET /products` 的 `total` 与调用前一致；
   - 该批次状态不变（`previewed` 的批次仍可正常审批）；
   - `SELECT guard_verdict, adopted FROM ai_review_log WHERE batch_id = <id>;` → `adopted` 全为 0。
5. 引用的数字（如拒绝行数）应与第 1 步的代码计数一致；说明文字中不应出现数字。

## 8. 演示中刻意展示的拒绝场景

| 场景 | 操作 | 预期 |
|---|---|---|
| 未审批直接写入 | 见第 2 步 | 400，商品表不变 |
| 重复审批 | 见第 3 步第 3 点 | 409 |
| 审批时数据已变化 | 预览后在 MySQL 中改动 `products`，再审批 | 409，批次置 `stale`，要求重新预览 |
| 空文件 / 缺列 / 非 UTF-8 | 上传其它文件 | 400 |
| 未配置 Key 调用 AI 审核 | `.env` 中没有 `DEEPSEEK_API_KEY` | 503（`ai_not_configured`） |
| 不存在的批次做 AI 审核（已配置 Key） | `batch_id` 填不存在的编号 | 404 |
| 无上传内容的批次做 AI 审核（已配置 Key） | 填命令行脚本创建的批次 | 409 |
| `limit` 越界 | 见下方说明 | 422 |

`limit` 越界（如 `limit=0` 或 `limit=201`）会被 Swagger UI 前端先拦截，无法在 `/docs` 触发 422。
请在另一终端用 `curl.exe "http://127.0.0.1:8000/products?limit=0"` 验证，预期 422；
`tests/test_api.py` 另用 `limit=999` 覆盖了 422。

## 已知边界

- 上传内容保存在 `import_batch.file_content`，审批写的就是这份内容；命令行脚本创建的批次没有该字段，不能在网页端审批，也不能做 AI 审核。
- 不做多用户与会话；`approver` 不校验。
- 没有自动把旧的预览批次作废；同一文件多次预览会留下多条 `previewed` 记录，由人工选择审批哪一条。
- AI 审核接口没有鉴权与限流，每次请求最多触发 2 次付费模型调用。
- 字段映射建议只写入 `ai_review_log`，`adopted` 恒为 0；“采纳”功能未实现，属后续工作，且必须经人工确认。
- 守卫的说明文字语义检查是启发式的（例如中文数字可能绕过），映射语义错误也可能通过，AI 输出只能当作建议。
