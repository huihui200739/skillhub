#!/bin/bash
# MySQL 初始化脚本：按版本+依赖顺序执行全部 DDL 建表和增量变更。
# 被 MySQL 官方镜像的 entrypoint 在首次启动时自动调用。
set -e

SKIPPED=0

run_sql() {
  local label="$1"
  local file="$2"
  if [ -f "$file" ]; then
    echo "[mysql-init] $label: $file"
    mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" < "$file"
  else
    echo "[mysql-init] WARNING: $file not found, skipped ($label)"
    SKIPPED=$((SKIPPED + 1))
  fi
}

# ── Baseline：初始建表（按外键依赖排序） ──
BL="/sql/baseline/openjiuwen_market/DDL"
run_sql "baseline" "$BL/market_assets.sql"
run_sql "baseline" "$BL/market_asset_versions.sql"
run_sql "baseline" "$BL/market_asset_interactions.sql"
run_sql "baseline" "$BL/audit_logs.sql"
run_sql "baseline" "$BL/plugin_fetch_records.sql"
run_sql "baseline" "$BL/site_notifications.sql"

# ── Incremental v0.0.2.B001：Git 源、审查、资产字段扩展 ──
INC1_DDL="/sql/incremental/v0.0.2.B001/openjiuwen_market/DDL"
INC1_DML="/sql/incremental/v0.0.2.B001/openjiuwen_market/DML"
run_sql "B001" "$INC1_DDL/market_assets.sql"
run_sql "B001" "$INC1_DDL/market_asset_versions.sql"
run_sql "B001" "$INC1_DDL/market_skill_reviews.sql"
run_sql "B001" "$INC1_DDL/git_sources.sql"
run_sql "B001-DML" "$INC1_DML/market_assets.sql"

# ── Incremental v0.0.2.B002：audit_logs 重建 ──
INC2_DDL="/sql/incremental/v0.0.2.B002/openjiuwen_market/DDL"
run_sql "B002" "$INC2_DDL/audit_logs.sql"

# ── Incremental v0.0.2.B003：群组管理 + 可见性 + 试用配额 ──
INC3_DDL="/sql/incremental/v0.0.2.B003/openjiuwen_market/DDL"
run_sql "B003" "$INC3_DDL/01_market_groups.sql"
run_sql "B003" "$INC3_DDL/02_market_group_members.sql"
run_sql "B003" "$INC3_DDL/03_market_group_join_requests.sql"
run_sql "B003" "$INC3_DDL/04_market_group_skill_grants.sql"
run_sql "B003" "$INC3_DDL/market_assets_visibility.sql"
run_sql "B003" "$INC3_DDL/playground_usage.sql"

if [ "$SKIPPED" -gt 0 ]; then
  echo "[mysql-init] ERROR: $SKIPPED SQL file(s) skipped (not found). DDL may be incomplete."
  exit 1
fi

echo "[mysql-init] all baseline + incremental DDL executed"
