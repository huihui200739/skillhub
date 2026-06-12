from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Pattern

from skill_review.domain.types import Confidence, SectionKey, Severity


@dataclass(frozen=True)
class RulePattern:
    pattern_id: str
    regex: Pattern[str]
    section_key: SectionKey
    check_id: str
    severity: Severity
    confidence: Confidence
    category: str
    capability: str
    title: str
    description: str
    matcher: str = "literal"
    allow_context_downgrade: bool = True
    requires_external_endpoint: bool = False


def compile_rule(definition: dict[str, object]) -> RulePattern:
    return RulePattern(
        pattern_id=str(definition["pattern_id"]),
        regex=re.compile(str(definition["regex"]), re.I),
        section_key=definition["section_key"],  # type: ignore[arg-type]
        check_id=str(definition["check_id"]),
        severity=definition["severity"],  # type: ignore[arg-type]
        confidence=definition["confidence"],  # type: ignore[arg-type]
        category=str(definition["category"]),
        capability=str(definition["capability"]),
        title=str(definition["title"]),
        description=str(definition["description"]),
        matcher=str(definition.get("matcher", "literal")),
        allow_context_downgrade=bool(definition.get("allow_context_downgrade", True)),
        requires_external_endpoint=bool(definition.get("requires_external_endpoint", False)),
    )


DEFAULT_RULE_PATTERNS = [
    compile_rule(
        {
            "pattern_id": "private_key_block",
            "regex": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
            "section_key": "data_asset",
            "check_id": "hardcoded_secret_material",
            "severity": "high",
            "confidence": "high",
            "category": "sensitive_info_exposure",
            "capability": "hardcoded_secret",
            "title": "发现私钥内容块",
            "description": "包内文本包含私钥块特征，应移除并改为运行时安全注入。",
            "matcher": "signature",
            "allow_context_downgrade": False,
        }
    ),
    compile_rule(
        {
            "pattern_id": "well_known_api_key",
            "regex": r"\b(?:AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z\-_]{20,})\b",
            "section_key": "data_asset",
            "check_id": "hardcoded_secret_material",
            "severity": "high",
            "confidence": "high",
            "category": "sensitive_info_exposure",
            "capability": "hardcoded_secret",
            "title": "发现常见 API Key 形态",
            "description": "包内文本包含常见云厂商或开发平台 API Key 形态，应移除并轮换凭证。",
            "matcher": "signature",
            "allow_context_downgrade": False,
        }
    ),
    compile_rule(
        {
            "pattern_id": "generic_secret_assignment",
            "regex": r"\b(api[_-]?key|secret|token|password|passwd|private[_-]?key)\b\s*[:=]\s*['\"][^'\"]{8,}['\"]",
            "section_key": "data_asset",
            "check_id": "hardcoded_secret_material",
            "severity": "medium",
            "confidence": "medium",
            "category": "sensitive_info_exposure",
            "capability": "hardcoded_secret_candidate",
            "title": "发现疑似明文凭证赋值",
            "description": "包内文本包含疑似凭证或口令赋值，需要确认是否为真实敏感值。",
        }
    ),
    compile_rule(
        {
            "pattern_id": "bearer_token_literal",
            "regex": r"\bBearer\s+[A-Za-z0-9._\-]{12,}\b",
            "section_key": "data_asset",
            "check_id": "hardcoded_secret_material",
            "severity": "medium",
            "confidence": "medium",
            "category": "sensitive_info_exposure",
            "capability": "hardcoded_secret_candidate",
            "title": "发现 Bearer Token 字面量",
            "description": "包内文本包含 Bearer Token 字面量，需要确认是否为真实敏感值。",
        }
    ),
    compile_rule(
        {
            "pattern_id": "default_or_backdoor_credential",
            "regex": (
                r"\b(?:default|emergency|backdoor|debug|temporary)\b.{0,40}"
                r"\b(?:password|passwd|credential|admin|account)\b"
            ),
            "section_key": "data_asset",
            "check_id": "default_credentials_or_backdoor",
            "severity": "high",
            "confidence": "high",
            "category": "sensitive_info_exposure",
            "capability": "default_credential",
            "title": "发现默认凭证或后门账号描述",
            "description": "包内文本包含默认口令、紧急账号或后门访问描述，应移除或改为受控配置。",
        }
    ),
    compile_rule(
        {
            "pattern_id": "prompt_override",
            "regex": (
                r"\b(ignore|disregard|forget)\b.{0,30}\b(previous|prior|above)\b.{0,30}"
                r"\b(instruction|prompt|message)s?\b"
            ),
            "section_key": "instruction",
            "check_id": "direct_override_or_jailbreak",
            "severity": "high",
            "confidence": "high",
            "category": "prompt_injection",
            "capability": "prompt_override",
            "title": "发现覆盖上文指令表达",
            "description": "包内文本包含覆盖系统或开发者指令的表达，应移除。",
        }
    ),
    compile_rule(
        {
            "pattern_id": "jailbreak_keyword",
            "regex": r"\bjailbreak\b",
            "section_key": "instruction",
            "check_id": "direct_override_or_jailbreak",
            "severity": "high",
            "confidence": "high",
            "category": "prompt_injection",
            "capability": "prompt_override",
            "title": "发现越狱提示词表达",
            "description": "包内文本包含明确越狱提示词表达，应移除。",
        }
    ),
    compile_rule(
        {
            "pattern_id": "prompt_or_system_leakage",
            "regex": r"\b(reveal|show|print)\b.{0,30}\b(system prompt|developer message|hidden instruction)s?\b",
            "section_key": "instruction",
            "check_id": "prompt_or_system_leakage",
            "severity": "high",
            "confidence": "high",
            "category": "prompt_injection",
            "capability": "prompt_leakage",
            "title": "发现提示词泄露表达",
            "description": "包内文本包含索要系统提示词、隐藏规则或开发者消息的表达，应移除。",
        }
    ),
    compile_rule(
        {
            "pattern_id": "eval_execution",
            "regex": r"\beval\s*\(",
            "section_key": "execution",
            "check_id": "dynamic_or_shell_execution",
            "severity": "high",
            "confidence": "high",
            "category": "dangerous_script_or_code",
            "capability": "dynamic_code_execution",
            "title": "发现 eval 动态执行入口",
            "description": "包内脚本包含 eval 动态执行入口，可能执行非预期代码。",
            "allow_context_downgrade": False,
        }
    ),
    compile_rule(
        {
            "pattern_id": "dynamic_function_execution",
            "regex": r"\bnew\s+Function\s*\(",
            "section_key": "execution",
            "check_id": "dynamic_or_shell_execution",
            "severity": "high",
            "confidence": "high",
            "category": "dangerous_script_or_code",
            "capability": "dynamic_code_execution",
            "title": "发现动态函数构造入口",
            "description": "包内脚本包含动态函数构造入口，可能执行非预期代码。",
            "allow_context_downgrade": False,
        }
    ),
    compile_rule(
        {
            "pattern_id": "invoke_expression",
            "regex": r"\bInvoke-Expression\b",
            "section_key": "execution",
            "check_id": "dynamic_or_shell_execution",
            "severity": "high",
            "confidence": "high",
            "category": "dangerous_script_or_code",
            "capability": "dynamic_code_execution",
            "title": "发现 PowerShell 动态执行入口",
            "description": "包内脚本包含 Invoke-Expression 动态执行入口，可能执行非预期代码。",
        }
    ),
    compile_rule(
        {
            "pattern_id": "shell_execution",
            "regex": (
                r"\b(?:child_process|os\.system\s*\(|"
                r"subprocess\.(?:run|Popen|call|check_call|check_output)\s*\(|shell\s*=\s*True\b)"
            ),
            "section_key": "execution",
            "check_id": "dynamic_or_shell_execution",
            "severity": "medium",
            "confidence": "high",
            "category": "dangerous_script_or_code",
            "capability": "shell_execution",
            "title": "发现 Shell 或子进程执行入口",
            "description": "包内脚本包含 Shell 或子进程执行能力，需要确认触发条件和用户确认边界。",
        }
    ),
    compile_rule(
        {
            "pattern_id": "remote_script_pipe_to_shell",
            "regex": r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:bash|sh|zsh)\b",
            "section_key": "execution",
            "check_id": "remote_script_or_binary_fetch",
            "severity": "high",
            "confidence": "high",
            "category": "remote_execution",
            "capability": "remote_script_execution",
            "title": "发现下载后直接执行命令链",
            "description": "包内文本包含远程脚本下载后直接执行的命令链，应固定来源并补充完整性校验或移除。",
        }
    ),
    compile_rule(
        {
            "pattern_id": "credential_upload_command",
            "regex": (
                r"\b(?:curl|wget|Invoke-WebRequest)\b.*(?:-F|--form|--data(?:-raw|-binary)?|-d)\b.*"
                r"(?:\.env|file=@|token|secret|credential|key|envs?)"
            ),
            "section_key": "execution",
            "check_id": "external_network_io",
            "severity": "high",
            "confidence": "high",
            "category": "data_exfiltration",
            "capability": "external_network_upload",
            "title": "发现凭证或环境数据上传命令",
            "description": "包内文本包含把环境变量、文件或凭证上传到外部端点的命令，应移除或明确收紧数据边界。",
        }
    ),
    compile_rule(
        {
            "pattern_id": "external_transfer_command",
            "regex": r"\b(?:curl|wget|Invoke-WebRequest)\b[^\n]*(?:https?://|file=@|--data|-d\b|--form|-F\b)",
            "section_key": "execution",
            "check_id": "external_network_io",
            "severity": "medium",
            "confidence": "high",
            "category": "suspicious_external_endpoint",
            "capability": "external_network_io",
            "title": "发现外部网络传输命令",
            "description": "包内文本包含外部网络下载或上传命令，需要确认必要性、数据边界和用户确认。",
        }
    ),
    compile_rule(
        {
            "pattern_id": "destructive_command",
            "regex": r"\b(?:rm\s+-rf|git\s+reset\s+--hard|git\s+clean\b[^\n]*\b-f\b|Remove-Item\b[^\n]*-Recurse)\b",
            "section_key": "execution",
            "check_id": "destructive_operation",
            "severity": "high",
            "confidence": "high",
            "category": "destructive_action",
            "capability": "destructive_operation",
            "title": "发现破坏性命令",
            "description": "包内文本包含删除、强制清理或强制回滚命令，需要移除或增加明确作用域和确认边界。",
        }
    ),
    compile_rule(
        {
            "pattern_id": "repository_boundary_change",
            "regex": r"\bgit\s+(?:remote\s+(?:add|set-url)|push\b[^\n]*(?:--force|-f))\b",
            "section_key": "execution",
            "check_id": "repository_boundary_change",
            "severity": "high",
            "confidence": "high",
            "category": "permission_boundary_violation",
            "capability": "repository_boundary_change",
            "title": "发现 Git 仓库边界变更命令",
            "description": "包内文本包含修改远端或强制推送命令，需要确认是否越过用户仓库边界。",
        }
    ),
    compile_rule(
        {
            "pattern_id": "external_git_push",
            "regex": r"\bgit\s+push\b.{0,160}(?:https?://|git@[\w.-]+:)",
            "section_key": "execution",
            "check_id": "repository_boundary_change",
            "severity": "high",
            "confidence": "high",
            "category": "permission_boundary_violation",
            "capability": "repository_boundary_change",
            "title": "发现向显式外部 Git 远端推送",
            "description": "包内文本包含向显式外部 Git 远端推送仓库内容的指令，需要移除或增加明确授权边界。",
        }
    ),
    compile_rule(
        {
            "pattern_id": "permission_weakening",
            "regex": (
                r"\b(?:chmod\s+777|--no-verify|StrictHostKeyChecking\s*=\s*no|"
                r"GIT_SSL_NO_VERIFY|NODE_TLS_REJECT_UNAUTHORIZED)\b"
            ),
            "section_key": "execution",
            "check_id": "permission_weakening",
            "severity": "high",
            "confidence": "high",
            "category": "permission_boundary_violation",
            "capability": "permission_weakening",
            "title": "发现安全校验或权限弱化配置",
            "description": "包内文本包含关闭校验或放宽权限的配置，需要恢复安全边界。",
        }
    ),
]

DOWNLOAD_THEN_EXECUTE_PATTERN = compile_rule(
    {
        "pattern_id": "download_then_execute_script",
        "regex": r"$^",
        "section_key": "execution",
        "check_id": "remote_script_or_binary_fetch",
        "severity": "high",
        "confidence": "high",
        "category": "remote_execution",
        "capability": "remote_script_execution",
        "title": "发现下载后执行脚本",
        "description": "包内文本包含下载远程内容后继续执行脚本的命令链，应固定来源并补充完整性校验或移除。",
    }
)
