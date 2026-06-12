-- 回滚 v0.0.2.B001：market_skill_reviews（openjiuwen_market）
-- 警告：将删除 market_skill_reviews 全表数据。
-- 执行前请备份。须先停止 marketplace 服务，且已部署代码不再依赖该表。
-- 对应增量：../../../incremental/v0.0.2.B001/openjiuwen_market/DDL/market_skill_reviews.sql
-- 同版本另需执行：market_asset_versions_rollback.sql、market_assets_rollback.sql、git_sources_rollback.sql
-- 建议顺序：market_skill_reviews_rollback.sql -> market_asset_versions_rollback.sql -> market_assets_rollback.sql -> git_sources_rollback.sql

DROP TABLE IF EXISTS `market_skill_reviews`;
