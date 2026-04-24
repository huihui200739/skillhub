import { ENV_CONFIG } from '@/config/environment'

/** 拼出与 `VITE_API_BASE_URL` 一致的接口路径（相对或绝对均可）。 */
function apiUrl(suffix: string): string {
  const base = (ENV_CONFIG.API_BASE_URL || '/api/v1').trim().replace(/\/$/, '')
  const path = suffix.startsWith('/') ? suffix : `/${suffix}`
  if (base.startsWith('http://') || base.startsWith('https://')) {
    return `${base}${path}`
  }
  const merged = `${base}${path}`.replace(/([^:]\/)\/+/g, '$1/')
  return merged.startsWith('/') ? merged : `/${merged}`
}

/** 新标签打开：Markdown 正文（由服务端读磁盘文件）。 */
export function getPrivacyStatementViewUrl(): string {
  return apiUrl('/site/privacy-statement')
}
