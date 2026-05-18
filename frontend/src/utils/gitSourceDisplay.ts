// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import type { GitSourceItemDto } from '@/api/plugin'

/**
 * 将 http(s) 克隆地址规范为带 `.git` 后缀的展示形式（与常见托管平台默认克隆 URL 一致）。
 * 非 http(s) 或无法解析的字符串原样返回。
 */
export function formatGitHttpsCloneUrlDisplay(raw: string): string {
  const s = (raw || '').trim()
  if (!s) return ''
  let normalized = s
  if (normalized.startsWith('git@')) {
    const at = normalized.indexOf('@')
    const colon = normalized.indexOf(':', at + 1)
    if (at >= 0 && colon > at) {
      const host = normalized.slice(at + 1, colon)
      let path = normalized.slice(colon + 1).replace(/\\/g, '/')
      if (!path.startsWith('/')) path = `/${path}`
      normalized = `https://${host}${path}`
    }
  }
  try {
    const u = new URL(normalized)
    if (u.protocol !== 'http:' && u.protocol !== 'https:') {
      return s
    }
    let path = (u.pathname || '/').replace(/\/+$/, '') || '/'
    if (!path.toLowerCase().endsWith('.git')) {
      path = `${path}.git`
    }
    const host = u.hostname.toLowerCase()
    const port = u.port ? `:${u.port}` : ''
    return `${u.protocol}//${host}${port}${path}`
  } catch {
    return s
  }
}

/** 与后端 `normalize_git_repo_global_key` 对齐：用于列表去重与展示辅助。 */
export function normalizeGitRepoUrlForDedup(url: string): string {
  let s = (url || '').trim().toLowerCase()
  if (!s) return ''
  if (s.startsWith('git@')) {
    const at = s.indexOf('@')
    const colon = s.indexOf(':', at + 1)
    if (at >= 0 && colon > at) {
      const host = s.slice(at + 1, colon)
      const path = s.slice(colon + 1)
      s = `https://${host}/${path}`
    }
  }
  s = s.replace(/\.git$/i, '')
  s = s.replace(/\/+$/, '')
  try {
    const u = new URL(s)
    let path = (u.pathname || '').replace(/\/+$/, '')
    if (path.toLowerCase().endsWith('.git')) {
      path = path.slice(0, -4)
    }
    return `${u.hostname}${path}`.replace(/\/+$/, '')
  } catch {
    return s
  }
}

/** 同 dedup_key 多条时：优先展示「上次同步成功」的源，避免仅因 update_time 更新而盖住健康记录。 */
function gitSourceSyncHealthRank(status: string | null | undefined): number {
  const s = (status ?? '').trim().toLowerCase()
  if (s === 'success') return 3
  if (s === 'partial_failure') return 2
  if (s === 'failed') return 0
  return 1
}

function pickBetterGitSourceForDisplay(a: GitSourceItemDto, b: GitSourceItemDto): GitSourceItemDto {
  const ra = gitSourceSyncHealthRank(a.last_index_status)
  const rb = gitSourceSyncHealthRank(b.last_index_status)
  if (rb !== ra) {
    return rb > ra ? b : a
  }
  if (b.update_time_ms !== a.update_time_ms) {
    return b.update_time_ms > a.update_time_ms ? b : a
  }
  return b.create_time_ms > a.create_time_ms ? b : a
}

function gitSourceListDedupKey(g: GitSourceItemDto): string {
  const dk = (g.git_source_dedup_key ?? g.gitSourceDedupKey ?? '').trim().toLowerCase()
  if (dk) return `dk:${dk}`
  const sub = (g.skills_subpath ?? '').trim().toLowerCase()
  return `${normalizeGitRepoUrlForDedup(g.repo_url)}@@${(g.ref || 'main').trim().toLowerCase()}@@${sub}`
}

/**
 * 同一用户下列表可能仍存在重复记录（历史数据）；按「git_source_dedup_key（若有）或规范化 URL + ref + skills_subpath」合并为一条展示。
 * 若同键下既有成功又有失败，只保留「健康度最高」的一组再比时间，避免下拉仍出现已失败的重复源。
 */
export function dedupeGitSourcesByRepoRef(sources: GitSourceItemDto[]): GitSourceItemDto[] {
  const byKey = new Map<string, GitSourceItemDto[]>()
  for (const g of sources) {
    const k = gitSourceListDedupKey(g)
    const arr = byKey.get(k)
    if (arr) arr.push(g)
    else byKey.set(k, [g])
  }
  const out: GitSourceItemDto[] = []
  for (const group of byKey.values()) {
    let maxRank = -1
    for (const g of group) {
      maxRank = Math.max(maxRank, gitSourceSyncHealthRank(g.last_index_status))
    }
    const tier = group.filter(g => gitSourceSyncHealthRank(g.last_index_status) === maxRank)
    let best = tier[0]
    for (let i = 1; i < tier.length; i++) {
      best = pickBetterGitSourceForDisplay(best, tier[i]!)
    }
    out.push(best)
  }
  return out
}
