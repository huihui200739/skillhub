// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useQuery } from 'react-query'
import { CalendarDays, HelpCircle, Timer, XCircle } from 'lucide-react'
import { getAdminAuditStats } from '@/api/audit'
import type { DateRange } from './AuditSearchBar'

type CardProps = {
  label: string
  /** 鼠标 hover 在 ? 图标上时展示，用于解释口径 */
  tooltip?: string
  /** 副标题（如慢操作阈值），始终可见，与 tooltip 互补 */
  hint?: string
  value: number | string
  icon: React.ComponentType<{ className?: string }>
  iconClassName: string
}

function StatCard({ label, tooltip, hint, value, icon: Icon, iconClassName }: CardProps) {
  return (
    <div className="flex items-center gap-4 rounded-2xl border border-[#E5E7EB] bg-white px-6 py-5">
      <div
        className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-xl ${iconClassName}`}
      >
        <Icon className="h-5 w-5" />
      </div>
      <div className="min-w-0">
        <div className="flex items-center gap-1">
          <span className="truncate text-sm text-[#6B7280]">{label}</span>
          {hint ? <span className="text-xs text-[#9CA3AF]">· {hint}</span> : null}
          {tooltip ? (
            <span
              title={tooltip}
              aria-label={tooltip}
              className="inline-flex cursor-help items-center text-[#9CA3AF] hover:text-[#6B7280]"
            >
              <HelpCircle className="h-3.5 w-3.5" />
            </span>
          ) : null}
        </div>
        <div className="mt-1 text-2xl font-semibold tabular-nums text-[#111827]">{value}</div>
      </div>
    </div>
  )
}

interface AuditStatsCardsProps {
  range: DateRange
  /** 与列表筛选保持一致——卡片数字必须等于列表 total，否则用户认知会断 */
  keyword?: string
  resourceType?: string
  action?: string
  result?: string
  assetPluginType?: string
  sourceChannel?: string
  /** 日期范围非法时不应请求 /audit/stats（与列表 enabled 口径一致，避免 400 噪声） */
  enabled?: boolean
}

export function AuditStatsCards({
  range,
  keyword,
  resourceType,
  action,
  result,
  assetPluginType,
  sourceChannel,
  enabled = true,
}: AuditStatsCardsProps) {
  const { data, isLoading, error } = useQuery(
    [
      'audit-stats',
      range.date_from_ms,
      range.date_to_ms,
      keyword || '',
      resourceType || '',
      action || '',
      result || '',
      assetPluginType || '',
      sourceChannel || '',
    ],
    () =>
      getAdminAuditStats({
        date_from_ms: range.date_from_ms,
        date_to_ms: range.date_to_ms,
        keyword: keyword || undefined,
        resource_type: resourceType || undefined,
        action: action || undefined,
        result: result || undefined,
        asset_plugin_type: assetPluginType || undefined,
        source_channel: sourceChannel || undefined,
      }),
    {
      enabled,
      keepPreviousData: true,
      staleTime: 60_000,
    },
  )

  const total = data?.total ?? 0
  const failed = data?.failed ?? 0
  const slow = data?.slow ?? 0
  const slowThresholdMs = data?.slow_threshold_ms ?? 3000

  const placeholder = isLoading ? '…' : error ? '—' : null
  const slowHint = `${(slowThresholdMs / 1000).toFixed(
    slowThresholdMs % 1000 === 0 ? 0 : 1,
  )} s`

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
      <StatCard
        label="操作"
        value={placeholder ?? total.toLocaleString()}
        icon={CalendarDays}
        iconClassName="bg-blue-50 text-blue-600"
      />
      <StatCard
        label="失败"
        value={placeholder ?? failed.toLocaleString()}
        icon={XCircle}
        iconClassName="bg-rose-50 text-rose-600"
      />
      <StatCard
        label="慢操作"
        hint={slowHint}
        value={placeholder ?? slow.toLocaleString()}
        icon={Timer}
        iconClassName="bg-amber-50 text-amber-600"
      />
    </div>
  )
}
