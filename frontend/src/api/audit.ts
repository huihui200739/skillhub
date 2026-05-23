// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { getApiClient } from './client'
import { API_CONFIG, API_ENDPOINTS } from './config'
import { getStoredOAuthProvider, getStoredOAuthToken } from '@/auth/gitcodeStorage'

type ResponseEnvelope<T> = { code: number; message?: string; data?: T }

/** 列表项：不含 detail / user_agent 大字段。
 * asset_* 三字段为后端 JOIN market_assets 注入，仅 skill/plugin 类资源有值。
 */
export interface AuditLogListItem {
  event_id: string
  event_type: string
  action: string
  operator_id: string
  operator_name?: string | null
  resource_type?: string | null
  resource_id?: string | null
  resource_version?: string | null
  result: string
  ip_address?: string | null
  extra?: Record<string, unknown> | null
  created_at_ms: number
  duration_ms: number
  /** web / cli / api / background */
  source_channel?: string | null
  asset_display_name?: string | null
  asset_plugin_type?: string | null
  asset_name?: string | null
}

/** 单条详情：完整字段 */
export interface AuditLogDetail extends AuditLogListItem {
  detail?: string | null
  user_agent?: string | null
  request_id?: string | null
}

export interface AuditLogListData {
  items: AuditLogListItem[]
  total: number
  page: number
  page_size: number
}

export interface AuditStatsData {
  total: number
  failed: number
  slow: number
  slow_threshold_ms: number
}

export interface AuditStatsQuery {
  date_from_ms: number
  date_to_ms: number
  keyword?: string
  resource_type?: string
  action?: string
  result?: string
  asset_plugin_type?: string
  source_channel?: string
}

export interface AuditLogQuery {
  keyword?: string
  date_from_ms: number
  date_to_ms: number
  resource_type?: string
  action?: string
  result?: string
  asset_plugin_type?: string
  source_channel?: string
  page?: number
  page_size?: number
}

export class AuditApiError extends Error {
  readonly status?: number
  constructor(message: string, status?: number) {
    super(message)
    this.name = 'AuditApiError'
    this.status = status
  }
}

function buildQueryParams(q: AuditLogQuery): Record<string, string | number> {
  const params: Record<string, string | number> = {
    date_from_ms: q.date_from_ms,
    date_to_ms: q.date_to_ms,
  }
  if (q.keyword && q.keyword.trim()) params.keyword = q.keyword.trim()
  if (q.resource_type) params.resource_type = q.resource_type
  if (q.action) params.action = q.action
  if (q.result) params.result = q.result
  if (q.asset_plugin_type) params.asset_plugin_type = q.asset_plugin_type
  if (q.source_channel) params.source_channel = q.source_channel
  if (q.page) params.page = q.page
  if (q.page_size) params.page_size = q.page_size
  return params
}

export async function getAdminAuditLogs(query: AuditLogQuery): Promise<AuditLogListData> {
  const { data } = await getApiClient().get<ResponseEnvelope<AuditLogListData>>(
    API_ENDPOINTS.AUDIT.LIST,
    { params: buildQueryParams(query) },
  )
  if (data.code !== 200 || !data.data) {
    throw new AuditApiError(data.message || `审计日志加载失败（code ${data.code}）`, data.code)
  }
  return data.data
}

export async function getAuditLogDetail(eventId: string): Promise<AuditLogDetail> {
  const { data } = await getApiClient().get<ResponseEnvelope<AuditLogDetail>>(
    API_ENDPOINTS.AUDIT.detail(eventId),
  )
  if (data.code !== 200 || !data.data) {
    throw new AuditApiError(data.message || `审计日志详情加载失败（code ${data.code}）`, data.code)
  }
  return data.data
}

export async function getAdminAuditStats(query: AuditStatsQuery): Promise<AuditStatsData> {
  const params: Record<string, string | number> = {
    date_from_ms: query.date_from_ms,
    date_to_ms: query.date_to_ms,
  }
  if (query.keyword && query.keyword.trim()) params.keyword = query.keyword.trim()
  if (query.resource_type) params.resource_type = query.resource_type
  if (query.action) params.action = query.action
  if (query.result) params.result = query.result
  if (query.asset_plugin_type) params.asset_plugin_type = query.asset_plugin_type
  if (query.source_channel) params.source_channel = query.source_channel
  const { data } = await getApiClient().get<ResponseEnvelope<AuditStatsData>>(
    API_ENDPOINTS.AUDIT.STATS,
    { params },
  )
  if (data.code !== 200 || !data.data) {
    throw new AuditApiError(data.message || `审计统计加载失败（code ${data.code}）`, data.code)
  }
  return data.data
}

/**
 * 触发浏览器下载 CSV：直接 fetch 流式响应为 Blob，再用 <a download> 触发保存。
 * 不走 axios 是为了让浏览器在大文件场景下不必把整个响应体放进 JS 内存里的 Axios 缓冲。
 */
export async function exportAuditLogsCsv(
  query: Omit<AuditLogQuery, 'page' | 'page_size'>,
): Promise<void> {
  const params = new URLSearchParams()
  params.set('date_from_ms', String(query.date_from_ms))
  params.set('date_to_ms', String(query.date_to_ms))
  if (query.keyword && query.keyword.trim()) params.set('keyword', query.keyword.trim())
  if (query.resource_type) params.set('resource_type', query.resource_type)
  if (query.action) params.set('action', query.action)
  if (query.result) params.set('result', query.result)
  if (query.asset_plugin_type) params.set('asset_plugin_type', query.asset_plugin_type)
  if (query.source_channel) params.set('source_channel', query.source_channel)

  const headers: Record<string, string> = { Accept: 'text/csv' }
  const token = getStoredOAuthToken()
  if (token) {
    headers.Authorization = `Bearer ${token}`
    headers['X-OAuth-Provider'] = getStoredOAuthProvider()
  }

  const url = `${API_CONFIG.BASE_URL}${API_ENDPOINTS.AUDIT.EXPORT}?${params.toString()}`
  const resp = await fetch(url, { method: 'GET', headers })
  if (!resp.ok) {
    let detail = ''
    try {
      const body = await resp.json()
      detail = body?.detail || ''
    } catch {
      // ignore
    }
    throw new AuditApiError(detail || `导出失败（HTTP ${resp.status}）`, resp.status)
  }
  const blob = await resp.blob()
  const objectUrl = URL.createObjectURL(blob)
  const filename = extractFilename(resp.headers.get('Content-Disposition'))
    || `audit_logs_${query.date_from_ms}_${query.date_to_ms}.csv`

  const a = document.createElement('a')
  a.href = objectUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  // 释放对象 URL：在 click 异步触发下载后稍后再清理，避免 Safari 偶发取消。
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
}

function extractFilename(disposition: string | null): string | null {
  if (!disposition) return null
  const m1 = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
  if (m1) {
    try {
      return decodeURIComponent(m1[1])
    } catch {
      return m1[1]
    }
  }
  const m2 = /filename="([^"]+)"/i.exec(disposition)
  return m2 ? m2[1] : null
}
