-- 独立对账：全部为只读 SELECT，不经过导入脚本的计数逻辑。
-- 执行（人工）：
--   mysql --defaults-extra-file=my.cnf olist_import < sql/reconcile.sql
-- 把输出贴进 docs/import-report.md。

SET @b := (SELECT MAX(batch_id) FROM import_batch WHERE mode = 'apply' AND status = 'succeeded');

SELECT '1. 原始行数（product_raw）' AS check_name, COUNT(*) AS value
FROM product_raw WHERE batch_id = @b;

SELECT '2. 状态分布' AS check_name, status, COUNT(*) AS value
FROM product_raw WHERE batch_id = @b GROUP BY status;

SELECT '3. 导入结果分布' AS check_name, import_result, COUNT(*) AS value
FROM product_raw WHERE batch_id = @b GROUP BY import_result;

SELECT '4. products 总行数' AS check_name, COUNT(*) AS value FROM products;

SELECT '5. 拒绝行误入商品表（应为 0）' AS check_name, COUNT(*) AS value
FROM products p
JOIN product_raw r ON r.product_id = p.product_id
WHERE r.batch_id = @b AND r.status = 'rejected';

SELECT '6. 无法追溯来源的商品（应为 0）' AS check_name, COUNT(*) AS value
FROM products p
LEFT JOIN product_raw r
  ON r.batch_id = p.source_batch_id AND r.csv_line = p.source_line
 AND r.import_result = 'inserted' AND r.product_id = p.product_id
WHERE r.raw_id IS NULL;

-- 7. 批次计数器与实际行数交叉核对：三处不等即停止排查，不得调参凑数
SELECT '7. 批次计数器 vs 实际' AS check_name,
       b.rows_inserted, b.rows_skipped, b.rows_conflict, b.rows_rejected, b.rows_total,
       (SELECT COUNT(*) FROM product_raw WHERE batch_id = b.batch_id AND import_result = 'inserted')  AS raw_inserted,
       (SELECT COUNT(*) FROM product_raw WHERE batch_id = b.batch_id AND import_result = 'skipped')   AS raw_skipped,
       (SELECT COUNT(*) FROM product_raw WHERE batch_id = b.batch_id AND import_result = 'conflict')  AS raw_conflict,
       (SELECT COUNT(*) FROM product_raw WHERE batch_id = b.batch_id AND status = 'rejected')        AS raw_rejected
FROM import_batch b WHERE b.batch_id = @b;

SELECT '8. products 行数 vs 所有成功批次新增之和' AS check_name,
       (SELECT COUNT(*) FROM products) AS products_count,
       (SELECT COALESCE(SUM(rows_inserted), 0) FROM import_batch WHERE mode = 'apply' AND status = 'succeeded') AS inserted_sum;
