-- 002：为“预览 → 人工审批 → 写入”增加批次状态与审批留痕列
-- 适用：第 4 天已按旧 schema.sql 建好的库（含真实库 olist_import 的 32323 行）。
-- 执行（人工，先备份；MySQL 5.7 的 ALTER 不能重复执行，本脚本只跑一次）：
--   mysqldump --defaults-extra-file=my.cnf olist_import > backup_before_002.sql
--   mysql --defaults-extra-file=my.cnf olist_import < sql/migrations/002_preview_approval.sql
-- 新建库请直接用 sql/schema.sql（已包含本迁移的终态）。

SET NAMES utf8mb4;

ALTER TABLE import_batch
  MODIFY COLUMN status
    ENUM('previewed','running','succeeded','failed','stale') NOT NULL DEFAULT 'running',
  ADD COLUMN plan_fingerprint CHAR(64) NULL AFTER finished_at,
  ADD COLUMN approved_by VARCHAR(64) NULL AFTER plan_fingerprint,
  ADD COLUMN approved_at DATETIME NULL AFTER approved_by,
  ADD COLUMN file_content MEDIUMBLOB NULL AFTER approved_at;
