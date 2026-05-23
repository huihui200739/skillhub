// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

export const API_CONFIG = {
  BASE_URL:
    (typeof import.meta !== 'undefined' && (import.meta as unknown as { env?: Record<string, string> }).env?.VITE_API_BASE_URL) ||
    '/api/v1',
  TIMEOUT: 300000,
  /** Git 源注册/触发同步：仅等待受理，同步在后台执行 */
  GIT_SYNC_ACCEPT_TIMEOUT: 60000,
  HEADERS: {
    'Content-Type': 'application/json',
    Accept: 'application/json',
  },
}

export const setApiBaseUrl = (baseUrl: string) => {
  API_CONFIG.BASE_URL = baseUrl
}

export const API_ENDPOINTS = {
  AUDIT: {
    /** GET /api/v1/audit/logs — 管理员全量审计日志（关键词 + 日期范围） */
    LIST: '/audit/logs',
    /** GET /api/v1/audit/stats — 审计日志统计 */
    STATS: '/audit/stats',
    /** GET /api/v1/audit/logs/export — 审计日志 CSV 导出（流式） */
    EXPORT: '/audit/logs/export',
    /** GET /api/v1/audit/logs/{event_id} — 单条详情 */
    detail: (eventId: string) => `/audit/logs/${encodeURIComponent(eventId)}`,
  },
  PLUGINS: {
    LIST: '/plugins',
    MY_STARS: '/plugins/my/stars',
    MY_LIKES: '/plugins/my/likes',
    interactionsBatch: '/plugins/interactions/batch',
    /** GET /api/v1/plugins/publish-template — 需 Bearer，返回私有桶模板 zip 预签名 URL */
    PUBLISH_TEMPLATE: '/plugins/publish-template',
    /** GET /api/v1/plugins/audit/skill-moderation — 仅审核管理员，本人审核审计记录 */
    MODERATION_AUDIT: '/plugins/audit/skill-moderation',
    versionDetail: (assetId: string, version: string) =>
      `/plugins/${encodeURIComponent(assetId)}/versions/${encodeURIComponent(version)}`,
    versionFiles: (assetId: string, version: string) =>
      `/plugins/${encodeURIComponent(assetId)}/versions/${encodeURIComponent(version)}/files`,
    moderation: (assetId: string) => `/plugins/${encodeURIComponent(assetId)}/moderation`,
    interactions: (assetId: string) => `/plugins/${encodeURIComponent(assetId)}/interactions`,
    interact: (assetId: string) => `/plugins/${encodeURIComponent(assetId)}/interact`,
    GIT_SOURCES: '/plugins/git-sources',
    gitSourceSync: (sourceId: string) =>
      `/plugins/git-sources/${encodeURIComponent(sourceId)}/sync`,
    gitSourceDetail: (sourceId: string) =>
      `/plugins/git-sources/${encodeURIComponent(sourceId)}`,
  },
  ARTIFACTS: {
    /** GET /api/v1/artifacts/{asset_id} */
    download: (assetId: string) => `/artifacts/${encodeURIComponent(assetId)}`,
  },
} as const
