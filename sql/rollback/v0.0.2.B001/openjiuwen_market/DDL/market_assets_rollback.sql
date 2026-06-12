-- 回滚 v0.0.2.B001：market_assets Git 与 Skill Review 发布状态列、索引（openjiuwen_market）
-- 警告：将移除 market_assets 上 Git 相关列与 publish_result（列内数据一并丢失）。
-- 执行前请备份。须先停止 marketplace 服务，且已部署代码不再依赖这些列。
-- 对应增量：../../../incremental/v0.0.2.B001/openjiuwen_market/DDL/market_assets.sql
-- 同版本另需执行：market_skill_reviews_rollback.sql、market_asset_versions_rollback.sql、git_sources_rollback.sql
-- 建议顺序：market_skill_reviews_rollback.sql -> market_asset_versions_rollback.sql -> market_assets_rollback.sql -> git_sources_rollback.sql

ALTER TABLE `market_assets` DROP INDEX `idx_ma_artifact_ck`;
ALTER TABLE `market_assets` DROP INDEX `idx_ma_git_source`;
ALTER TABLE `market_assets` DROP INDEX `idx_ma_storage_mode`;
ALTER TABLE `market_assets` DROP INDEX `uk_ma_external_id`;
ALTER TABLE `market_assets` DROP INDEX `idx_publish_result`;

ALTER TABLE `market_assets` DROP COLUMN `git_sync_payload_sha256`;
ALTER TABLE `market_assets` DROP COLUMN `artifact_content_key`;
ALTER TABLE `market_assets` DROP COLUMN `declared_skill_version`;
ALTER TABLE `market_assets` DROP COLUMN `resolved_commit_sha`;
ALTER TABLE `market_assets` DROP COLUMN `git_visibility`;
ALTER TABLE `market_assets` DROP COLUMN `git_source_id`;
ALTER TABLE `market_assets` DROP COLUMN `external_id`;
ALTER TABLE `market_assets` DROP COLUMN `storage_mode`;
ALTER TABLE `market_assets` DROP COLUMN `publish_result`;
