# 演示脚本（第 5 天初版）

> 目标：在 `/docs` 中完整走一遍 上传 → 预览 → 审批 → 查询 → 报告。
> 全程没有认证：`approver` 是可自报的字符串，只用于留痕，**不构成权限控制**。
> 第 7 天定稿时补录屏。

## 0. 启动

```
.venv/Scripts/python.exe -m pip install fastapi uvicorn httpx python-multipart
.venv/Scripts/python.exe scripts/run_api.py
```

打开 <http://127.0.0.1:8000/docs>。

前置：`olist_import` 已按 `sql/schema.sql` 建表（或对第 4 天的库执行过一次
`sql/migrations/002_preview_approval.sql`），且 `category_translation` 已载入 71 行。

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
   - `approver`：`唯一`
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

## 5. 导出与幂等

再次执行 `POST /imports/preview` 上传同一文件 → 预览显示 `rows_inserted = 0`、
`rows_skipped = 32323`；审批后 `products` 行数不变。

## 6. 演示中刻意展示的拒绝场景

| 场景 | 操作 | 预期 |
|---|---|---|
| 未审批直接写入 | 见第 2 步 | 400，商品表不变 |
| 重复审批 | 见第 3 步第 3 点 | 409 |
| 审批时数据已变化 | 预览后在 MySQL 中改动 `products`，再审批 | 409，批次置 `stale`，要求重新预览 |
| 空文件 / 缺列 / 非 UTF-8 | 上传其它文件 | 400 |

## 已知边界

- 上传内容保存在 `import_batch.file_content`，审批写的就是这份内容；命令行脚本创建的批次没有该字段，不能在网页端审批。
- 不做多用户与会话；`approver` 不校验。
- 没有自动把旧的预览批次作废；同一文件多次预览会留下多条 `previewed` 记录，由人工选择审批哪一条。
