-- 增量 v0.0.2.B002：audit_logs 索引（openjiuwen_market）
-- 适用：已有 baseline 库、audit_logs 表尚未包含 idx_created_at / idx_event_type_created_at。
-- 执行前请备份。本脚本按版本仅执行一次；重复执行可能因索引已存在而报错。
-- 回滚：../../../rollback/v0.0.2.B002/openjiuwen_market/DDL/audit_logs_rollback.sql

ALTER TABLE `audit_logs`
  ADD INDEX `idx_created_at` (`created_at` DESC),
  ADD INDEX `idx_event_type_created_at` (`event_type`, `created_at` DESC);
