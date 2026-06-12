// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/** 内置品牌图标（与 `public/git-hosts/*.svg` 对应），未命中则前端使用默认 Git 图标。 */
export type KnownGitHostBrandId = 'github' | 'gitee' | 'gitcode'

/** 将常见 git 克隆地址转为可解析的 https URL，失败返回 null。 */
export function repoUrlToHttpsForHostLookup(input: string): string | null {
  let s = (input || '').trim()
  if (!s) return null
  if (s.startsWith('git@')) {
    const at = s.indexOf('@')
    const colon = s.indexOf(':', at + 1)
    if (at >= 0 && colon > at) {
      const host = s.slice(at + 1, colon)
      const path = s.slice(colon + 1)
      s = `https://${host}/${path}`
    }
  }
  if (!/^https?:\/\//i.test(s)) {
    s = `https://${s}`
  }
  try {
    const u = new URL(s)
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return null
    return u.href
  } catch {
    return null
  }
}

/**
 * 根据仓库 URL 识别托管平台（仅匹配少量热门公网域名）。
 */
export function resolveKnownGitHostBrandId(repoUrl: string): KnownGitHostBrandId | null {
  const href = repoUrlToHttpsForHostLookup(repoUrl)
  if (!href) return null
  let host: string
  try {
    host = new URL(href).hostname.toLowerCase()
  } catch {
    return null
  }
  if (host.startsWith('www.')) {
    host = host.slice(4)
  }
  switch (host) {
    case 'github.com':
      return 'github'
    case 'gitee.com':
      return 'gitee'
    case 'gitcode.com':
    case 'gitcode.net':
      return 'gitcode'
    default:
      return null
  }
}
