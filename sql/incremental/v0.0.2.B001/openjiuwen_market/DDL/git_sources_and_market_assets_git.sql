-- 增量 v0.0.2.B001：Git 源接入（openjiuwen_market）
-- 适用：已有 baseline 库、尚未包含 git_sources / market_assets Git 列。
-- 执行前请备份。本脚本按版本仅执行一次；重复执行可能因列/索引已存在而报错。
-- 回滚：../../../rollback/v0.0.2.B001/openjiuwen_market/DDL/git_sources_and_market_assets_git_rollback.sql

CREATE TABLE IF NOT EXISTS `git_sources` (
  `id` varchar(64) NOT NULL,
  `name` varchar(128) NOT NULL,
  `repo_url` varchar(512) NOT NULL,
  `repo_url_canonical` varchar(640) DEFAULT NULL COMMENT 'normalized host+path for global dedup',
  `git_source_dedup_key` char(64) DEFAULT NULL COMMENT 'SHA256 hex: canonical + ref + skills_subpath',
  `ref` varchar(256) NOT NULL,
  `skills_subpath` varchar(512) DEFAULT NULL,
  `visibility_scope` varchar(32) NOT NULL,
  `created_by_user_id` varchar(64) NOT NULL,
  `create_time_ms` bigint NOT NULL,
  `update_time_ms` bigint NOT NULL,
  `last_index_status` varchar(64) DEFAULT NULL COMMENT 'syncing | success | partial_failure | failed',
  `last_index_error` text,
  `last_indexed_at_ms` bigint DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_git_source_dedup_key` (`git_source_dedup_key`),
  KEY `idx_gs_created_by` (`created_by_user_id`),
  KEY `idx_gs_visibility` (`visibility_scope`),
  KEY `idx_gs_repo_url_canonical` (`repo_url_canonical`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE `market_assets` ADD COLUMN `storage_mode` varchar(32) DEFAULT NULL;
ALTER TABLE `market_assets` ADD COLUMN `external_id` varchar(128) DEFAULT NULL;
ALTER TABLE `market_assets` ADD COLUMN `git_source_id` varchar(64) DEFAULT NULL;
ALTER TABLE `market_assets` ADD COLUMN `git_visibility` varchar(32) DEFAULT NULL;
ALTER TABLE `market_assets` ADD COLUMN `resolved_commit_sha` varchar(40) DEFAULT NULL;
ALTER TABLE `market_assets` ADD COLUMN `declared_skill_version` varchar(64) DEFAULT NULL;
ALTER TABLE `market_assets` ADD COLUMN `artifact_content_key` varchar(64) DEFAULT NULL;
ALTER TABLE `market_assets` ADD COLUMN `git_sync_payload_sha256` varchar(64) DEFAULT NULL;

CREATE UNIQUE INDEX `uk_ma_external_id` ON `market_assets` (`external_id`);
CREATE INDEX `idx_ma_storage_mode` ON `market_assets` (`storage_mode`);
CREATE INDEX `idx_ma_git_source` ON `market_assets` (`git_source_id`);
CREATE INDEX `idx_ma_artifact_ck` ON `market_assets` (`artifact_content_key`);
