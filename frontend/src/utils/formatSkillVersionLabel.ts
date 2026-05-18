// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/** 纯十六进制、长度在 7–32 之间，视为 Git commit 版本（与库表 version 列一致），展示 7 位短 SHA。 */
const FULL_HEX_VERSION = /^[0-9a-fA-F]{7,32}$/

/** 标准 semver x.y.z，可选预发布后缀；忽略大小写；允许多个前导 v（归一成单一 v）。 */
const SEMVER_CORE = /^(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)$/i

/** 列表/详情：后端判定与可选字段。 */
export type SkillVersionLabelOpts = {
  gitVersionDisplayAsCommit?: boolean | null
  resolvedCommitSha?: string | null
  declaredSkillVersion?: string | null
  storageMode?: string | null
}

/**
 * 市场 / 个人中心等处统一版本文案：
 * - 后端 `git_version_display_as_commit` + `resolved_commit_sha` → 7 位小写 commit（无 v）
 * - 版本号为 hex（Git 同步 commit）→ 7 位短 SHA
 * - semver → `v` + `x.y.z`（单一 v，避免 `vv1.0.0`）
 * - 其它 → 原样返回
 */
export function formatSkillVersionLabel(
  raw: string | null | undefined,
  opts?: SkillVersionLabelOpts | null,
): string {
  const sha = String(opts?.resolvedCommitSha || '').trim().toLowerCase()
  const s0 = (raw ?? '').trim()
  const strippedV = s0.replace(/^v+/i, '')

  if (opts?.gitVersionDisplayAsCommit && sha) {
    return sha.slice(0, 7)
  }

  if (!s0) return ''
  if (FULL_HEX_VERSION.test(strippedV)) {
    return strippedV.slice(0, 7).toLowerCase()
  }
  const m = strippedV.match(SEMVER_CORE)
  if (m) {
    return `v${m[1]}`
  }
  return s0
}

/** 市场列表：下拉中仅与「当前展示的 latest」相同的项用 commit 短码，其余仍走 semver 文案。 */
export function formatMarketSkillVersionLabel(
  raw: string | null | undefined,
  plugin: {
    latestVersion: string
    gitVersionDisplayAsCommit?: boolean | null
    resolvedCommitSha?: string | null
    declaredSkillVersion?: string | null
    storageMode?: string | null
  },
): string {
  const v = (raw ?? '').trim()
  if (!v) return ''
  const latest = (plugin.latestVersion || '').trim()
  const passCommitFlag = Boolean(plugin.gitVersionDisplayAsCommit) && v === latest
  return formatSkillVersionLabel(v, {
    gitVersionDisplayAsCommit: passCommitFlag,
    resolvedCommitSha: plugin.resolvedCommitSha,
    declaredSkillVersion: plugin.declaredSkillVersion,
    storageMode: plugin.storageMode,
  })
}

/** 下载 zip 文件名中的版本段，与列表/详情展示文案一致。 */
export function skillVersionFilenameSegment(
  raw: string | null | undefined,
  opts?: SkillVersionLabelOpts | null,
): string {
  const label = formatSkillVersionLabel(raw, opts)
  const safe = label.replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^-+|-+$/g, '')
  return safe || (raw ?? '').trim() || 'version'
}

export function marketSkillVersionFilenameSegment(
  raw: string | null | undefined,
  plugin: {
    latestVersion: string
    gitVersionDisplayAsCommit?: boolean | null
    resolvedCommitSha?: string | null
    declaredSkillVersion?: string | null
    storageMode?: string | null
  },
): string {
  const v = (raw ?? '').trim()
  const latest = (plugin.latestVersion || '').trim()
  return skillVersionFilenameSegment(v, {
    gitVersionDisplayAsCommit: Boolean(plugin.gitVersionDisplayAsCommit) && v === latest,
    resolvedCommitSha: plugin.resolvedCommitSha,
    declaredSkillVersion: plugin.declaredSkillVersion,
    storageMode: plugin.storageMode,
  })
}
