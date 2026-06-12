-- 增量 v0.0.2.B001：market_asset_versions Skill Review 发布状态列（openjiuwen_market）
-- 适用：已有 baseline 库、market_asset_versions 尚未包含 publish_result。
-- 执行前请备份。本脚本按版本仅执行一次；重复执行可能因列已存在而报错。
-- 同版本另需执行：git_sources.sql、market_assets.sql、market_skill_reviews.sql
-- 建议顺序：git_sources.sql -> market_assets.sql / market_asset_versions.sql -> market_skill_reviews.sql
-- 回滚：../../../rollback/v0.0.2.B001/openjiuwen_market/DDL/market_asset_versions_rollback.sql

ALTER TABLE `market_asset_versions`
  ADD COLUMN `publish_result` varchar(32) DEFAULT NULL COMMENT 'Version-level unified publish result: reviewing | pending_moderation | publish_success | publish_failed'
  AFTER `moderation_reject_reason`;
