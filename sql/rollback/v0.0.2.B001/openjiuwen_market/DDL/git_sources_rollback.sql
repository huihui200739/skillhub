-- 回滚 v0.0.2.B001：git_sources（openjiuwen_market）
-- 警告：将删除 git_sources 全表数据。
-- 执行前请备份。须先停止 marketplace 服务，且已部署代码不再依赖该表。
-- 对应增量：../../../incremental/v0.0.2.B001/openjiuwen_market/DDL/git_sources.sql
-- 同版本另需执行：market_assets_rollback.sql（建议先执行 market_assets_rollback，再执行本脚本）

DROP TABLE IF EXISTS `git_sources`;
