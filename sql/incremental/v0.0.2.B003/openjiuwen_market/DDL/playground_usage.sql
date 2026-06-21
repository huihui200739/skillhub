-- 增量 v0.0.2.B003：Playground 每日使用配额跟踪表（openjiuwen_market）
-- 适用：已有 baseline 库，尚未包含 playground_usage 表。
-- 执行前请备份。本脚本按版本仅执行一次；重复执行会因表已存在而报错。
-- 回滚：../../../rollback/v0.0.2.B003/openjiuwen_market/DDL/playground_usage_rollback.sql

CREATE TABLE `playground_usage` (
  `user_id`       varchar(128) NOT NULL COMMENT 'OAuth 用户 ID（来自 acting_user_id）',
  `usage_date`    date         NOT NULL COMMENT '日期（服务器本地时区）',
  `session_count` int          NOT NULL DEFAULT '0' COMMENT '当日已创建 session 数',
  `updated_at`    bigint       NOT NULL COMMENT '最后更新时间（Unix 毫秒）',
  PRIMARY KEY (`user_id`, `usage_date`),
  KEY `idx_usage_date` (`usage_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='Playground 每日使用配额跟踪；PLAYGROUND_DAILY_LIMIT 控制上限，0=不限制';
