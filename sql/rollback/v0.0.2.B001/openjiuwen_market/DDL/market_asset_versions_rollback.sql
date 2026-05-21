-- 回滚 v0.0.2.B001：market_asset_versions Skill Review 发布状态列（openjiuwen_market）
-- 警告：将移除 market_asset_versions.publish_result（列内数据一并丢失）。
-- 执行前请备份。须先停止 marketplace 服务，且已部署代码不再依赖该列。
-- 对应增量：../../../incremental/v0.0.2.B001/openjiuwen_market/DDL/market_asset_versions.sql
-- 同版本另需执行：market_skill_reviews_rollback.sql、market_assets_rollback.sql、git_sources_rollback.sql
-- 建议顺序：market_skill_reviews_rollback.sql -> market_asset_versions_rollback.sql -> market_assets_rollback.sql -> git_sources_rollback.sql

ALTER TABLE `market_asset_versions` DROP COLUMN `publish_result`;
