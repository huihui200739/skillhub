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
    'bec2e071eeaf46f7b375ea74ba21e5b6', -- pptx-craft
    'a19afa7386394f92bc5a14116d898e1e', -- Gitcode Repo
    '55d1fa7c7bf640a28400f8a3a89a6014', -- Code review Guideline
    '3360bfd2ce25454f8f25fac90ae73a11', -- jiuwenswarm-doc-checker
    'e8e8653acd45420ebc21acd997e146fb', -- AI辅助研发-DevLeader
    '0edf052fe13847a48b114b477a63999f', -- AI辅助研发-代码测试
    '2e67d9c04b7a42db9fbd51b4e2df5392', -- AI辅助研发-代码审查
    '49643efa99be468c8c5e1794a38aa9ec', -- AI辅助研发-开发与测试计划
    'ed38da9e18824eb39b940d628a7a4535', -- AI辅助研发-架构设计
    'b8deb48efa4e455f90ca92239043f665', -- AI辅助研发-编码开发
    'b5ec169b90b94b0db08ecb74f9622f82', -- AI辅助研发-需求分析
    '0c2f857372b54c988e897911a6b3cdc7') -- technical-blog-generator
;
