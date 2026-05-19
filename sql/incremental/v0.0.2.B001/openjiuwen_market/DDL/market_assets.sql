-- 增量 v0.0.2.B001：market_assets Git 列与索引（openjiuwen_market）
-- 适用：已有 baseline 库、market_assets 尚未包含 Git 相关列。
-- 执行前请备份。本脚本按版本仅执行一次；重复执行可能因列/索引已存在而报错。
-- 同版本另需执行：git_sources.sql（建议先执行 git_sources，再执行本脚本）
-- 回滚：../../../rollback/v0.0.2.B001/openjiuwen_market/DDL/market_assets_rollback.sql

ALTER TABLE `market_assets` ADD COLUMN `storage_mode` varchar(32) DEFAULT NULL;
ALTER TABLE `market_assets` ADD COLUMN `external_id` varchar(128) DEFAULT NULL;
ALTER TABLE `market_assets` ADD COLUMN `git_source_id` varchar(64) DEFAULT NULL;
ALTER TABLE `market_assets` ADD COLUMN `git_visibility` varchar(32) DEFAULT NULL;
ALTER TABLE `market_assets` ADD COLUMN `resolved_commit_sha` varchar(40) DEFAULT NULL COMMENT 'git rev-parse HEAD after sync';
ALTER TABLE `market_assets` ADD COLUMN `declared_skill_version` varchar(64) DEFAULT NULL COMMENT 'SKILL.md frontmatter version for display';
ALTER TABLE `market_assets` ADD COLUMN `artifact_content_key` varchar(64) DEFAULT NULL;
ALTER TABLE `market_assets` ADD COLUMN `git_sync_payload_sha256` varchar(64) DEFAULT NULL COMMENT 'normalized publish zip SHA-256 hex';

CREATE UNIQUE INDEX `uk_ma_external_id` ON `market_assets` (`external_id`);
CREATE INDEX `idx_ma_storage_mode` ON `market_assets` (`storage_mode`);
CREATE INDEX `idx_ma_git_source` ON `market_assets` (`git_source_id`);
CREATE INDEX `idx_ma_artifact_ck` ON `market_assets` (`artifact_content_key`);
