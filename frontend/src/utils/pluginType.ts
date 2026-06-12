// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

export const PRIMARY_SKILL_PLUGIN_TYPE = 'swarmskill'
export const SKILL_LIKE_PLUGIN_TYPES = ['skill', 'swarmskill'] as const
export const SKILL_LIKE_QUERY_VALUE = SKILL_LIKE_PLUGIN_TYPES.join(',')

export type SkillLikePluginType = (typeof SKILL_LIKE_PLUGIN_TYPES)[number]

const SKILL_LIKE_PLUGIN_TYPE_SET = new Set<string>(SKILL_LIKE_PLUGIN_TYPES)

export function normalizePluginType(value: string | null | undefined): string {
  const normalized = (value || '').trim().toLowerCase()
  return normalized === 'teamskills' ? 'swarmskill' : normalized
}

export function parseSkillLikePluginType(value: string | null | undefined): SkillLikePluginType | null {
  const normalized = normalizePluginType(value)
  return SKILL_LIKE_PLUGIN_TYPE_SET.has(normalized) ? (normalized as SkillLikePluginType) : null
}

export function isSkillLikePluginType(value: string | null | undefined): value is SkillLikePluginType {
  return parseSkillLikePluginType(value) !== null
}

export function getSkillLikePluginTypes(): SkillLikePluginType[] {
  return [...SKILL_LIKE_PLUGIN_TYPES]
}

export function getPrimarySkillPluginType(): SkillLikePluginType {
  return PRIMARY_SKILL_PLUGIN_TYPE
}
