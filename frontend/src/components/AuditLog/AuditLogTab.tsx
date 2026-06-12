// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useQuery } from 'react-query'
import {
  AuditApiError,
  exportAuditLogsCsv,
  getAdminAuditLogs,
  type AuditLogListItem,
} from '@/api/audit'
import { Pagination } from '@/components/Common/common-table'
import { AuditStatsCards } from './AuditStatsCards'
import {
  AuditSearchBar,
  buildLast7DaysRange,
  type DateRange,
  type SkillTypeFilterValue,
} from './AuditSearchBar'
import { AuditLogTable } from './AuditLogTable'
import { AuditLogDetailDrawer } from './AuditLogDetailDrawer'

const PAGE_SIZE_OPTIONS = [20, 50, 100] as const
const MAX_RANGE_DAYS = 90
const MS_PER_DAY = 86_400_000

const INITIAL_SKILL_TYPE_FILTER: SkillTypeFilterValue = { kind: 'all' }

/** SkillTypeFilterValue → 后端 query params */
function toBackendParams(filter: SkillTypeFilterValue): {
  asset_plugin_type?: string
  resource_type?: string
} {
  if (filter.kind === 'asset_plugin_type') return { asset_plugin_type: filter.value }
  if (filter.kind === 'resource_type') return { resource_type: filter.value }
  return {}
}

interface AuditLogTabProps {
  /** 通知父组件当前是否处于详情模式（父组件可据此隐藏自己的标题区）。 */
  onDetailModeChange?: (inDetail: boolean) => void
}

export function AuditLogTab({ onDetailModeChange }: AuditLogTabProps = {}) {
  const [range, setRange] = useState<DateRange>(() => buildLast7DaysRange())
  const [keyword, setKeyword] = useState('')
  const [skillTypeFilter, setSkillTypeFilter] = useState<SkillTypeFilterValue>(INITIAL_SKILL_TYPE_FILTER)
  const [actionFilter, setActionFilter] = useState<string>('')
  const [resultFilter, setResultFilter] = useState<string>('')
  const [sourceFilter, setSourceFilter] = useState<string>('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState<number>(50)
  const [detailEventId, setDetailEventId] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const [rangeError, setRangeError] = useState<string | null>(null)
  const [exportError, setExportError] = useState<string | null>(null)

  useEffect(() => {
    onDetailModeChange?.(detailEventId !== null)
  }, [detailEventId, onDetailModeChange])

  // 任一筛选条件变化都重置回第 1 页
  useEffect(() => {
    setPage(1)
  }, [
    keyword,
    range.date_from_ms,
    range.date_to_ms,
    skillTypeFilter.kind,
    skillTypeFilter.kind !== 'all' ? skillTypeFilter.value : '',
    actionFilter,
    resultFilter,
    sourceFilter,
  ])

  const rangeValid = useMemo(() => {
    if (range.date_to_ms < range.date_from_ms) return false
    const span = range.date_to_ms - range.date_from_ms
    return span <= MAX_RANGE_DAYS * MS_PER_DAY
  }, [range])

  useEffect(() => {
    if (!rangeValid) {
      setRangeError('日期范围最大不能超过 90 天')
    } else {
      setRangeError(null)
    }
  }, [rangeValid])

  const skillTypeParams = useMemo(() => toBackendParams(skillTypeFilter), [skillTypeFilter])

  const queryKey = useMemo(
    () =>
      [
        'audit-logs',
        keyword,
        range.date_from_ms,
        range.date_to_ms,
        skillTypeParams.asset_plugin_type || '',
        skillTypeParams.resource_type || '',
        actionFilter,
        resultFilter,
        sourceFilter,
        page,
        pageSize,
      ] as const,
    [keyword, range.date_from_ms, range.date_to_ms, skillTypeParams, actionFilter, resultFilter, sourceFilter, page, pageSize],
  )

  const { data, isFetching, error } = useQuery(
    queryKey,
    () =>
      getAdminAuditLogs({
        keyword: keyword || undefined,
        date_from_ms: range.date_from_ms,
        date_to_ms: range.date_to_ms,
        asset_plugin_type: skillTypeParams.asset_plugin_type,
        resource_type: skillTypeParams.resource_type,
        action: actionFilter || undefined,
        result: resultFilter || undefined,
        source_channel: sourceFilter || undefined,
        page,
        page_size: pageSize,
      }),
    {
      enabled: rangeValid,
      keepPreviousData: true,
      refetchOnMount: 'always',
      staleTime: 0,
    },
  )

  const items: AuditLogListItem[] = data?.items ?? []
  const total = data?.total ?? 0

  const handleExport = useCallback(async () => {
    if (!rangeValid || exporting) return
    setExporting(true)
    setExportError(null)
    try {
      await exportAuditLogsCsv({
        keyword: keyword || undefined,
        date_from_ms: range.date_from_ms,
        date_to_ms: range.date_to_ms,
        asset_plugin_type: skillTypeParams.asset_plugin_type,
        resource_type: skillTypeParams.resource_type,
        action: actionFilter || undefined,
        result: resultFilter || undefined,
        source_channel: sourceFilter || undefined,
      })
    } catch (err) {
      const msg = err instanceof AuditApiError ? err.message : (err as Error)?.message || '导出失败'
      setExportError(msg)
    } finally {
      setExporting(false)
    }
  }, [
    exporting,
    keyword,
    range.date_from_ms,
    range.date_to_ms,
    rangeValid,
    skillTypeParams,
    actionFilter,
    resultFilter,
    sourceFilter,
  ])

  const handlePagerChange = (nextPage: number, nextPageSize: number) => {
    setPageSize(nextPageSize)
    setPage(nextPage)
  }

  const errorMessage = useMemo(() => {
    if (rangeError) return rangeError
    if (!error) return null
    if (error instanceof AuditApiError) return error.message
    if (error instanceof Error) return error.message
    return '加载失败'
  }, [rangeError, error])

  const hasActiveFilter =
    skillTypeFilter.kind !== 'all' ||
    Boolean(actionFilter) ||
    Boolean(resultFilter) ||
    Boolean(sourceFilter) ||
    Boolean(keyword)

  const resetFilters = () => {
    setKeyword('')
    setSkillTypeFilter(INITIAL_SKILL_TYPE_FILTER)
    setActionFilter('')
    setResultFilter('')
    setSourceFilter('')
  }

  // 详情打开时，主区域换成详情页（保留 sidebar 等外层结构）
  if (detailEventId) {
    return (
      <div className="flex flex-col gap-4">
        <AuditLogDetailDrawer
          eventId={detailEventId}
          onClose={() => setDetailEventId(null)}
        />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <AuditStatsCards
        range={range}
        keyword={keyword || undefined}
        resourceType={skillTypeParams.resource_type}
        action={actionFilter || undefined}
        result={resultFilter || undefined}
        assetPluginType={skillTypeParams.asset_plugin_type}
        sourceChannel={sourceFilter || undefined}
        enabled={rangeValid}
      />

      <AuditSearchBar
        keyword={keyword}
        onKeywordChange={setKeyword}
        range={range}
        onRangeChange={setRange}
        onExport={handleExport}
        exporting={exporting}
        skillTypeFilter={skillTypeFilter}
        onSkillTypeFilterChange={setSkillTypeFilter}
        actionFilter={actionFilter}
        onActionFilterChange={setActionFilter}
        resultFilter={resultFilter}
        onResultFilterChange={setResultFilter}
        sourceFilter={sourceFilter}
        onSourceFilterChange={setSourceFilter}
      />

      {errorMessage ? (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {errorMessage}
        </div>
      ) : null}
      {exportError ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {exportError}
        </div>
      ) : null}

      {hasActiveFilter ? (
        <div className="flex items-center justify-end text-xs text-[#6B7280]">
          <button
            type="button"
            onClick={resetFilters}
            className="text-xs text-[#0950DE] hover:underline"
          >
            清除所有筛选
          </button>
        </div>
      ) : null}

      <AuditLogTable
        items={items}
        loading={isFetching}
        onOpenDetail={item => setDetailEventId(item.event_id)}
      />

      {total > 0 ? (
        <div className="border-t border-[#e5e7eb] pt-4">
          <Pagination
            pager={{
              total,
              currentPage: page,
              pageSize,
              pageSizeOptions: [...PAGE_SIZE_OPTIONS],
            }}
            loading={false}
            onPagerChange={handlePagerChange}
          />
        </div>
      ) : null}
    </div>
  )
}
