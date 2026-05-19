-- ⚠️  警告：本脚本为【内部特定环境专用】，不可直接用于其他部署环境。
--
-- 背景：历史数据中所有 skill-like 资产的 plugin_type 均为 'skill'，
--       需将其中实为 SwarmSkill 的资产更新为 'swarmskill'。
--
-- 本脚本的做法是：在 skill-like 范围内将 plugin_type 改为 'swarmskill'，
-- 再将已知的普通 Skill 资产还原为 'skill'。
-- 其中"已知的普通 Skill" 列表（asset_id）是针对当前内部部署环境人工确认的，
-- 在其他环境中这些 asset_id 不存在或对应不同资产，直接执行会导致数据错误。
--
-- 如需在其他环境执行历史数据回填，应逐资产读取 zip 内 SKILL.md frontmatter.kind，
-- 命中 team-skill / swarm-skill 则更新为 'swarmskill'，否则保持 'skill'。

UPDATE market_assets SET plugin_type = 'swarmskill' WHERE plugin_type IN ('skill', 'teamskills');
UPDATE market_assets SET plugin_type = 'skill' WHERE asset_id IN (
    '2cc56bf1780a48448fe899a6a22321df', -- 团队技能生成专家
    'e54618b2d0324cc5a1c289ed80ce6e4f', -- 北碚化学教育团队
    '7719ed0ed15a41b5b98339aca610d2ce', -- doc_generator
    '533c94fbd04f4f6e974cc05c72fef156', -- screenshot
    'f6c86637611e4d3083c801a6b0b67456', -- kami
    'f86de981d71e472abc9e3645552e5d17', -- gitcode-pr-review-fix
    '183cd8738c044acf879db31117912db0', -- bill-analyzer-skill
    '6dd2433d638e446abdfb94320acc6d90', -- nasa-safe-code-rater
    'bec2e071eeaf46f7b375ea74ba21e5b6') -- pptx-craft
;
