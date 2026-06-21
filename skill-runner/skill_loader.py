# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Skill 内容加载：把 marketplace 的 skill ZIP 拆成 SKILL.md / workflow.md / roles 文本，并保留原 ZIP 字节。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SkillBundle:
    """playground 用 skill 包的去结构化视图。"""

    asset_id: str
    version: str
    skill_md: str = ""                            # SKILL.md 文本（可能为空）
    workflow_md: str = ""                         # workflow.md 文本（可能为空）
    roles: dict[str, str] = field(default_factory=dict)
    """SwarmSkill 角色配置：{角色名: 角色文档文本}"""
    team_mode: str = ""
    """SwarmSkill 团队模式（opt-in 动态 spawn）：""=自动推导，或 hybrid/default/predefined。"""

    package_bytes: bytes = b""                    # 整个 zip
    archive_strip_prefix: str = ""                # zip 顶层目录前缀（无则为 ""）

    def system_prompt_text(self) -> str:
        """把 skill_md / workflow_md / roles 合并成一段适合放进 system prompt 的描述。"""
        parts: list[str] = []
        if self.skill_md.strip():
            parts.append("===== SKILL.md =====\n" + self.skill_md.strip())
        if self.workflow_md.strip():
            parts.append("===== workflow.md =====\n" + self.workflow_md.strip())
        if self.roles:
            joined = "\n\n".join(
                f"--- role: {name} ---\n{text.strip()}"
                for name, text in self.roles.items()
            )
            parts.append("===== roles =====\n" + joined)
        return "\n\n".join(parts).strip()


# 注：早期同进程设计里的 parse_skill_zip / _iter_role_files / _strip_prefix 已删除。
# 现架构由 marketplace 侧解析 ZIP（含 zip bomb 校验）后注入文本字段，skill-runner
# 直接用文本重建 SkillBundle，不在本进程解压 ZIP。


