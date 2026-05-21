// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/** 与服务端 ``SKILL_LIKE_PLUGIN_TYPES`` 对齐。 */
export const SKILL_LIKE_PLUGIN_TYPES = ['skill', 'swarmskill'] as const

/** 多值查询字符串，用于 ``plugin_type=skill,swarmskill`` 形式拉取全部 skill-like 资产。 */
export const SKILL_LIKE_QUERY_VALUE = SKILL_LIKE_PLUGIN_TYPES.join(',')

export function isSkillLikePluginType(pluginType: string | null | undefined): boolean {
  const normalized = (pluginType || '').trim().toLowerCase()
  return (SKILL_LIKE_PLUGIN_TYPES as readonly string[]).includes(normalized)
}
