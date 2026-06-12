// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/** 与后端 git_url_safety 对齐：提交前拒绝明显的内网/本机/保留地址（不含 DNS 解析）。 */

const BLOCKED_HOSTNAMES = new Set([
  'localhost',
  'localhost.localdomain',
  'metadata.google.internal',
])

function isUnsafeIpv4(octets: number[]): boolean {
  if (octets.length !== 4) return true
  const [a, b] = octets
  if (a === 127) return true
  if (a === 0) return true
  if (a === 10) return true
  if (a === 172 && b >= 16 && b <= 31) return true
  if (a === 192 && b === 168) return true
  if (a === 169 && b === 254) return true
  if (a >= 224) return true
  return false
}

function parseIpv4Literal(host: string): number[] | null {
  const parts = host.split('.')
  if (parts.length !== 4) return null
  const octets: number[] = []
  for (const part of parts) {
    if (!/^\d+$/.test(part)) return null
    const n = Number(part)
    if (n < 0 || n > 255) return null
    octets.push(n)
  }
  return octets
}

function isUnsafeIpv6Literal(host: string): boolean {
  let h = host.trim().toLowerCase()
  if (h.startsWith('[') && h.endsWith(']')) {
    h = h.slice(1, -1)
  }
  if (h === '::1' || h === '::') return true
  if (h.startsWith('fe80:')) return true
  if (h.startsWith('fc') || h.startsWith('fd')) return true
  if (h.startsWith('ff')) return true
  return false
}

function isBlockedHostname(host: string): boolean {
  const normalized = host.trim().toLowerCase().replace(/\.$/, '')
  if (!normalized) return true
  if (BLOCKED_HOSTNAMES.has(normalized)) return true
  if (normalized.endsWith('.localhost') || normalized.endsWith('.local')) return true

  const ipv4 = parseIpv4Literal(normalized)
  if (ipv4) return isUnsafeIpv4(ipv4)
  if (normalized.includes(':')) return isUnsafeIpv6Literal(normalized)
  return false
}

/** 校验 http(s) 克隆地址；不通过则返回 false。 */
export function isPublicGitCloneUrl(repoUrl: string): boolean {
  const raw = (repoUrl || '').trim()
  if (!raw) return false
  try {
    const parsed = new URL(raw)
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return false
    const host = (parsed.hostname || '').trim()
    if (!host) return false
    if (parsed.username || parsed.password) return false
    return !isBlockedHostname(host)
  } catch {
    return false
  }
}
