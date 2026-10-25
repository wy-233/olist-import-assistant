-- 商品目录导入：建表脚本（MySQL 5.7，utf8mb4）
-- 执行（人工，不经 Python，保持通道独立）：
--   mysql --defaults-extra-file=my.cnf olist_import < sql/schema.sql
-- 说明：本脚本会 DROP 已存在的同名表，仅用于学习环境重建。

SET NAMES utf8mb4;

DROP TABLE IF EXISTS ai_review_log;
DROP TABLE IF EXISTS product_raw;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS category_translation;
DROP TABLE IF EXISTS import_batch;

-- 每次导入一行，记录该批的四分类计数（AC-3 / AC-4 的证据）
-- status：previewed=已预览待审批；running=审批通过写入中；succeeded；failed；stale=审批时计划已变化，需重新预览
-- 预览批次的 rows_* 是“计划数”；审批写入后改写为实际数
CREATE TABLE import_batch (
  batch_id        BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  file_name       VARCHAR(255) NOT NULL,
  file_sha256     CHAR(64)     NOT NULL,
  mode            ENUM('dry_run','apply') NOT NULL,
  status          ENUM('previewed','running','succeeded','failed','stale') NOT NULL DEFAULT 'running',
  rows_total      INT NOT NULL DEFAULT 0,
  rows_inserted   INT NOT NULL DEFAULT 0,
  rows_skipped    INT NOT NULL DEFAULT 0,
  rows_conflict   INT NOT NULL DEFAULT 0,
  rows_rejected   INT NOT NULL DEFAULT 0,
  rows_pending    INT NOT NULL DEFAULT 0,
  started_at      DATETIME NOT NULL,
  finished_at     DATETIME NULL,
  plan_fingerprint CHAR(64) NULL,
  approved_by     VARCHAR(64) NULL,
  approved_at     DATETIME NULL,
  -- 预览时上传的文件内容；审批只写这份内容，保证“审的就是导入的”
  file_content    MEDIUMBLOB  NULL,
  KEY idx_sha (file_sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 原始层：CSV 每一行一条，值按字符串原样保存，含被拒绝的行
CREATE TABLE product_raw (
  raw_id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  batch_id            BIGINT UNSIGNED NOT NULL,
  csv_line            INT NOT NULL,
  product_id          VARCHAR(64)  COLLATE utf8mb4_bin NULL,
  category_name       VARCHAR(100) COLLATE utf8mb4_bin NULL,
  name_length         VARCHAR(32) NULL,
  description_length  VARCHAR(32) NULL,
  photos_qty          VARCHAR(32) NULL,
  weight_g            VARCHAR(32) NULL,
  length_cm           VARCHAR(32) NULL,
  height_cm           VARCHAR(32) NULL,
  width_cm            VARCHAR(32) NULL,
  status              ENUM('valid','warning','rejected') NOT NULL,
  reason_codes        VARCHAR(255) NOT NULL DEFAULT '',
  import_result       ENUM('inserted','skipped','conflict','not_attempted') NOT NULL DEFAULT 'not_attempted',
  UNIQUE KEY uk_batch_line (batch_id, csv_line),
  KEY idx_pid (product_id),
  KEY idx_status (batch_id, status),
  CONSTRAINT fk_raw_batch FOREIGN KEY (batch_id) REFERENCES import_batch (batch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 标准层：只放代码解析后的值，只含可导入行
CREATE TABLE products (
  product_id          VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL PRIMARY KEY,
  category_name       VARCHAR(100) COLLATE utf8mb4_bin NOT NULL,
  name_length         INT NULL,
  description_length  INT NULL,
  photos_qty          INT NULL,
  weight_g            INT NOT NULL,
  length_cm           INT NOT NULL,
  height_cm           INT NOT NULL,
  width_cm            INT NOT NULL,
  source_batch_id     BIGINT UNSIGNED NOT NULL,
  source_line         INT NOT NULL,
  created_at          DATETIME NOT NULL,
  KEY idx_category (category_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 类目翻译：71 行参考数据
CREATE TABLE category_translation (
  category_name     VARCHAR(100) COLLATE utf8mb4_bin NOT NULL PRIMARY KEY,
  category_name_en  VARCHAR(100) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- AI 审核日志：模型的输入、原始输出、守卫结论。AI 只写这张表；adopted 只能由人置 1（第 6 天未实现采纳）
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
