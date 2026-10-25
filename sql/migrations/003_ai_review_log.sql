-- 003：AI 审核日志表
-- 适用：已按 schema.sql（含 002）建好的库，包括真实库 olist_import。
-- 执行（人工，先备份；本脚本用 CREATE TABLE，重复执行会报表已存在）：
--   mysqldump --defaults-extra-file=my.cnf olist_import > backup_before_003.sql
--   mysql --defaults-extra-file=my.cnf olist_import < sql/migrations/003_ai_review_log.sql
-- 新建库请直接用 sql/schema.sql（已包含本迁移的终态）。本迁移不改动、不删除任何已有表和数据。

SET NAMES utf8mb4;

CREATE TABLE ai_review_log (
  log_id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  batch_id          BIGINT UNSIGNED NOT NULL,
  kind              ENUM('explain','mapping') NOT NULL,
  model_name        VARCHAR(100) NOT NULL,
  prompt_sha256     CHAR(64)     NOT NULL,
  input_json        MEDIUMTEXT   NOT NULL,
  raw_output        MEDIUMTEXT   NULL,
  parsed_json       MEDIUMTEXT   NULL,
  guard_verdict     ENUM('passed','blocked','error') NOT NULL,
  guard_violations  TEXT         NOT NULL,
  adopted           TINYINT(1)   NOT NULL DEFAULT 0,
  created_at        DATETIME     NOT NULL,
  KEY idx_batch (batch_id, created_at),
  CONSTRAINT fk_ai_batch FOREIGN KEY (batch_id) REFERENCES import_batch (batch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
