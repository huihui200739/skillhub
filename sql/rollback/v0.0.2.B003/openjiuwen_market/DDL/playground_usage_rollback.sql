-- 回滚 v0.0.2.B003：删除 playground_usage 表
-- 执行前请确认业务已停用 Playground，该表数据将永久删除。

DROP TABLE IF EXISTS `playground_usage`;
