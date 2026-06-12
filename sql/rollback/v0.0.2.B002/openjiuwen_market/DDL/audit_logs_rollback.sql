-- 回滚 v0.0.2.B002：移除 audit_logs 索引

ALTER TABLE `audit_logs`
  DROP INDEX `idx_event_type_created_at`,
  DROP INDEX `idx_created_at`;
