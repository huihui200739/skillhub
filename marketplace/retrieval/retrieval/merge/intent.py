# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import re
from typing import Iterable, List


_CAPABILITY_TAG_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        (
            "搜索",
            "搜",
            "查",
            "检索",
            "搜索引擎",
            "search",
            "find",
            "lookup",
            "discover",
            "browser",
            "browse",
            "engine",
        ),
        ("search", "retrieval", "lookup", "browse", "source-discovery"),
    ),
    (
        (
            "github",
            "reddit",
            "youtube",
            "b站",
            "bilibili",
            "小红书",
            "知乎",
            "抖音",
            "linkedin",
            "boss直聘",
            "boss",
            "微信",
            "公众号",
            "rss",
            "twitter/x",
            "twitter",
            "subtitle",
            "transcript",
            "platform",
            "social",
            "web access",
        ),
        ("platform-access", "social-content", "video-transcript", "web-browsing"),
    ),
    (
        (
            "persona",
            "persona",
            "角色",
            "人设",
            "专家",
            "顾问",
            "教练",
            "导师",
            "专业人士",
            "名师",
            "医生",
            "律师",
            "程序员",
            "设计师",
            "工程师",
            "咨询",
            "指导",
            "建议",
            "意见",
            "帮我看看",
            "评估一下",
            "法律",
            "医疗",
            "金融",
            "理财",
            "商业计划",
            "合同",
            "装修",
            "室内设计",
            "公考",
            "儿科",
            "character",
            "avatar",
            "coach",
            "advisor",
            "expert",
        ),
        ("persona", "expert-role", "advisor", "coach", "character-design"),
    ),
    (
        ("自动", "自动化", "执行", "任务", "工作流", "计划", "规划", "planner",
         "workflow", "automation", "autonomous", "proactive", "follow-up"),
        ("automation", "workflow", "planner", "task-execution", "proactive-assistant"),
    ),
    (
        ("研究", "调研", "洞察", "分析", "research", "analysis", "insight", "report"),
        ("research", "analysis", "insight", "reporting"),
    ),
)

_INTENT_BOOST_RULES: tuple[tuple[str, tuple[str, ...], int], ...] = (
    (
        "search",
        (
            "search engine",
            "searchengine",
            "multi search",
            "multisearchengine",
            "web search",
            "websearch",
            "search",
            "retrieval",
            "搜索引擎",
            "多引擎",
            "latest articles",
            "news articles",
            "reviews",
            "rankings",
            "market share",
            "stats",
            "statistics",
        ),
        4,
    ),
    (
        "platform-access",
        (
            "platform access",
            "web access",
            "github",
            "reddit",
            "youtube",
            "bilibili",
            "小红书",
            "知乎",
            "抖音",
            "linkedin",
            "boss",
            "微信",
            "公众号",
            "subtitle",
            "transcript",
            "job listings",
        ),
        4,
    ),
    (
        "persona",
        (
            "persona",
            "personas",
            "expert",
            "advisor",
            "coach",
            "character",
            "avatar",
            "人设",
            "角色",
            "专家",
            "顾问",
            "教练",
            "专业人士",
            "名师",
            "医生",
            "律师",
            "程序员",
            "设计师",
            "工程师",
            "金融专家",
            "法律顾问",
            "医学专家",
            "儿科医生",
            "室内设计师",
            "公考名师",
        ),
        4,
    ),
    ("automation", ("automation", "autonomous", "workflow", "planner",
     "proactive", "task execution", "自动化", "自驱动", "规划", "执行"), 4),
)

_GENERIC_SEARCH_MARKERS: tuple[str, ...] = (
    "search engine",
    "searchengine",
    "multi search",
    "multisearchengine",
    "web search",
    "websearch",
    "搜索引擎",
    "多引擎",
)
_SEARCH_SCOPE_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("paper", ("paper", "papers", "arxiv", "论文", "文献")),
    ("news", ("news", "headline", "新闻", "资讯", "报道")),
    ("weather", ("weather", "forecast", "天气", "预报")),
    ("map", ("map", "maps", "route", "导航", "地图", "路线")),
    ("platform", ("github", "reddit", "youtube", "bilibili", "b站", "小红书",
     "知乎", "抖音", "linkedin", "boss", "微信公众号", "subtitle", "transcript")),
)
_DOMAIN_TAG_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("finance", ("金融", "财经", "理财", "投资", "商业计划", "business plan", "finance", "financial", "investment", "market")),
    ("legal", ("法律", "法务", "合同", "律师", "legal", "lawyer", "contract")),
    ("medical", ("医疗", "医学", "用药", "儿科", "医生", "medical", "medicine", "doctor", "pediatric")),
    ("coding", ("代码", "编程", "程序员", "开发", "软件", "coding", "code", "programmer", "developer", "software")),
    ("design", ("设计", "装修", "室内", "design", "designer", "interior")),
    ("fitness", ("健身", "训练", "游泳", "fitness", "workout", "coach", "swim")),
    ("exam", ("公考", "考试", "名师", "备考", "exam", "test prep", "mentor")),
    ("github", ("github",)),
    ("reddit", ("reddit",)),
    ("youtube", ("youtube", "字幕", "transcript")),
    ("bilibili", ("b站", "bilibili")),
    ("xiaohongshu", ("小红书", "xiaohongshu")),
    ("zhihu", ("知乎", "zhihu")),
    ("douyin", ("抖音", "douyin", "tiktok")),
    ("linkedin", ("linkedin",)),
    ("jobs", ("招聘", "职位", "job", "jobs", "boss直聘", "boss")),
)
_CONTENT_CREATION_MARKERS: tuple[str, ...] = (
    "writer",
    "writing",
    "copywriter",
    "article",
    "content generation",
    "新闻写作",
    "文章写作",
    "文案",
    "写作",
)

_CAMEL_CASE_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_WORD_RE = re.compile(r"[^a-z0-9\u3400-\u4dbf\u4e00-\u9fff]+")


def infer_capability_tags(*values: str) -> List[str]:
    text = _normalize_match_text(*values)
    if not text.strip():
        return []
    tags: List[str] = []
    for triggers, tag_group in _CAPABILITY_TAG_RULES:
        if any(_contains_trigger(text, trigger) for trigger in triggers):
            for tag in tag_group:
                if tag not in tags:
                    tags.append(tag)
    return tags


def format_capability_tags(*values: str) -> str:
    tags = infer_capability_tags(*values)
    if not tags:
        return ""
    return "Capability tags: " + ", ".join(tags)


def intent_match_score(query: str, *candidate_values: str) -> int:
    query_tags = set(infer_capability_tags(query))
    if not query_tags:
        return 0
    candidate_tags = set(infer_capability_tags(*candidate_values))
    if not candidate_tags:
        return 0
    return len(query_tags & candidate_tags)


def intent_priority_score(query: str, *candidate_values: str) -> int:
    query_tags = set(infer_capability_tags(query))
    query_text = _normalize_match_text(query)
    candidate_text = _normalize_match_text(*candidate_values)
    candidate_tags = set(infer_capability_tags(*candidate_values))
    query_domains = infer_domain_tags(query)
    candidate_domains = infer_domain_tags(*candidate_values)
    score = intent_match_score(query, *candidate_values)
    score += len(query_domains & candidate_domains) * 6
    if _is_factual_lookup_query(query_text) and _is_generic_search_hub(*candidate_values):
        score += 14
    if _is_automation_workflow_query(query_text):
        if _is_end_to_end_execution_skill(*candidate_values) and _is_end_to_end_execution_query(query_text):
            score += 28
        elif _is_general_automation_hub(*candidate_values):
            score += 14
        elif "automation" in candidate_tags:
            score += 6
        if "persona" in candidate_tags and not _is_general_automation_hub(*candidate_values):
            score -= 8
    if _is_learning_to_action_query(query_text):
        if _is_learning_to_action_skill(*candidate_values):
            score += 28
        elif _is_general_automation_hub(*candidate_values):
            score += 8
    if _is_communication_query(query_text):
        if _is_communication_research_skill(*candidate_values):
            score += 28
        elif "research" in candidate_tags:
            score += 6
    if not query_tags and score <= 0:
        return 0
    for intent, markers, weight in _INTENT_BOOST_RULES:
        if intent not in query_tags:
            continue
        if any(_contains_trigger(candidate_text, marker) for marker in markers):
            score += weight
    if "search" in query_tags:
        query_scopes = _matched_search_scopes(query_text)
        candidate_scopes = _matched_search_scopes(candidate_text)
        candidate_has_generic_search = _has_generic_search_marker(candidate_text)
        if not query_scopes:
            if candidate_has_generic_search:
                score += 8
            score -= len(candidate_scopes) * 4
            if (
                _has_content_creation_marker(candidate_text)
                and not candidate_has_generic_search
                and "platform-access" not in candidate_tags
            ):
                score -= 4
        else:
            score += len(query_scopes & candidate_scopes) * 4
            if candidate_has_generic_search:
                score += 1
    if "persona" in query_tags:
        if query_domains and candidate_domains and not (query_domains & candidate_domains):
            score -= 4
    if "platform-access" in query_tags:
        platform_overlap = len(query_domains & candidate_domains)
        score += platform_overlap * 5
        if query_domains and candidate_domains and platform_overlap <= 0:
            score -= 3
    return score


def _matched_search_scopes(text: str) -> set[str]:
    scopes: set[str] = set()
    for scope, markers in _SEARCH_SCOPE_MARKERS:
        for marker in markers:
            if _contains_trigger(text, marker):
                scopes.add(scope)
                break
    return scopes


def _has_generic_search_marker(text: str) -> bool:
    for marker in _GENERIC_SEARCH_MARKERS:
        if _contains_trigger(text, marker):
            return True
    return False


def _has_content_creation_marker(text: str) -> bool:
    for marker in _CONTENT_CREATION_MARKERS:
        if _contains_trigger(text, marker):
            return True
    return False


def merge_text_parts(parts: Iterable[str]) -> str:
    return "\n".join(part for part in parts if str(part or "").strip()).strip()


def capability_hint_text(*values: str) -> str:
    tags = set(infer_capability_tags(*values))
    domains = infer_domain_tags(*values)
    hints: List[str] = []
    if "search" in tags:
        hints.append(
            "Use when the user wants general web search across sources, including latest "
            "articles, news, reviews, rankings, market data, statistics, and public "
            "information lookup."
        )
        if _is_generic_search_hub(*values):
            hints.append(
                "Generic search keywords: 搜索引擎 多引擎 网页搜索 最新资讯 文章 评论 榜单 统计 权威资料 "
                "中文来源 英文来源 论文 文献 web search search engine latest articles reviews "
                "rankings statistics authoritative sources chinese sources english sources "
                "papers literature."
            )
    if "platform-access" in tags:
        hints.append(
            "Useful for platform content access, including GitHub, Reddit, YouTube, "
            "Bilibili, Xiaohongshu, Zhihu, Douyin, LinkedIn, job boards, WeChat "
            "articles, subtitles, and transcripts."
        )
    if "persona" in tags:
        hints.append("Use when the user wants an expert role, advisor, coach, or persona tailored to the task.")
        if _is_general_persona_hub(*values):
            hints.append(
                "Can support multiple domain-specific personas, including legal, medical, "
                "finance, business, coding, design, fitness, and exam-prep roles."
            )
            hints.append(
                "Domain keywords: 法律 legal 医疗 medical 儿科医生 pediatric doctor child health "
                "金融 finance 商业 business 编程 coding 设计 design 健身 fitness 公考 exam."
            )
    if "automation" in tags:
        hints.append(
            "Use when the user wants a task or workflow planned, executed, and followed "
            "through automatically."
        )
        if _is_general_automation_hub(*values):
            hints.append(
                "Automation keywords: 自动化 完整方案 端到端 规划 执行 跟进 复盘 流程编排 "
                "end to end workflow execution planning follow through orchestration."
            )
    if "research" in tags:
        hints.append("Useful for research, analysis, synthesis, and insight generation tasks.")
    if _is_learning_to_action_skill(*values):
        hints.append(
            "Useful for turning books, courses, lessons, or learning materials into "
            "practice plans, next steps, action checklists, and execution roadmaps."
        )
    if _is_character_architecture_skill(*values):
        hints.append(
            "Useful for persona architecture, character design, NPC personalities, "
            "background stories, agent identity, emotional depth, and tone shaping."
        )
    if _is_communication_research_skill(*values):
        hints.append(
            "Useful for communication psychology, influence, negotiation, empathy, "
            "body language, trust-building, and interpersonal insight."
        )
    if domains:
        hints.append("Relevant domains: " + ", ".join(sorted(domains)))
    return " ".join(hints).strip()


def infer_domain_tags(*values: str) -> set[str]:
    text = _normalize_match_text(*values)
    if not text.strip():
        return set()
    tags: set[str] = set()
    for tag, markers in _DOMAIN_TAG_RULES:
        if any(_contains_trigger(text, marker) for marker in markers):
            tags.add(tag)
    return tags


def _is_general_persona_hub(*values: str) -> bool:
    text = _normalize_match_text(*values)
    if not text:
        return False
    markers = (
        "personas",
        "内置角色",
        "built in roles",
        "built in role",
        "multiple personas",
        "多个角色",
        "20 个内置角色",
        "20 built in",
    )
    return any(_contains_trigger(text, marker) for marker in markers)


def _is_generic_search_hub(*values: str) -> bool:
    text = _normalize_match_text(*values)
    if not text:
        return False
    markers = (
        "search engine",
        "searchengine",
        "multi search",
        "multisearchengine",
        "web search",
        "搜索引擎",
        "多引擎",
    )
    return any(_contains_trigger(text, marker) for marker in markers)


def _is_general_automation_hub(*values: str) -> bool:
    text = _normalize_match_text(*values)
    if not text:
        return False
    markers = (
        "autonomous tasks",
        "task execution",
        "workflow",
        "自动规划任务路径",
        "自动完成任务",
        "连续工作流程",
        "端到端",
    )
    return any(_contains_trigger(text, marker) for marker in markers)


def _is_learning_to_action_skill(*values: str) -> bool:
    text = _normalize_match_text(*values)
    if not text:
        return False
    markers = (
        "ship learn next",
        "learn next",
        "行动计划",
        "实践方案",
        "转化为可执行的行动计划",
        "practice plan",
        "action plan",
    )
    return any(_contains_trigger(text, marker) for marker in markers)


def _is_character_architecture_skill(*values: str) -> bool:
    text = _normalize_match_text(*values)
    if not text:
        return False
    markers = (
        "soul md",
        "soulcraft",
        "灵魂架构师",
        "人格文件",
        "虚拟角色",
        "npc",
        "character design",
        "persona architecture",
    )
    return any(_contains_trigger(text, marker) for marker in markers)


def _is_communication_research_skill(*values: str) -> bool:
    text = _normalize_match_text(*values)
    if not text:
        return False
    markers = (
        "沟通",
        "心理学",
        "影响力",
        "谈判",
        "非语言沟通",
        "肢体语言",
        "research loop",
        "communication",
        "influence",
        "negotiation",
        "body language",
        "empathy",
        "trust",
    )
    return any(_contains_trigger(text, marker) for marker in markers)


def _is_factual_lookup_query(text: str) -> bool:
    markers = (
        "资料",
        "来源",
        "权威",
        "发表情况",
        "期刊",
        "论文",
        "文献",
        "publication",
        "publications",
        "report",
        "ranking",
        "rankings",
        "财报",
        "销量",
        "统计",
    )
    return any(_contains_trigger(text, marker) for marker in markers)


def _is_automation_workflow_query(text: str) -> bool:
    markers = (
        "自动",
        "自动化",
        "完整方案",
        "执行方案",
        "从头到尾",
        "端到端",
        "跟进",
        "流程",
        "workflow",
        "follow through",
        "execution",
        "execute",
        "system automation",
    )
    return any(_contains_trigger(text, marker) for marker in markers)


def _is_learning_to_action_query(text: str) -> bool:
    markers = (
        "读完",
        "学完",
        "实践方案",
        "实践计划",
        "行动计划",
        "快速应用",
        "应用到实际工作",
        "learned",
        "after reading",
        "after learning",
        "practice plan",
        "action plan",
        "next steps",
    )
    return any(_contains_trigger(text, marker) for marker in markers)


def _is_communication_query(text: str) -> bool:
    markers = (
        "沟通",
        "谈判",
        "信任感",
        "肢体语言",
        "非语言",
        "演讲",
        "影响力",
        "communication",
        "negotiation",
        "trust",
        "body language",
        "presentation",
        "influence",
    )
    return any(_contains_trigger(text, marker) for marker in markers)


def _is_end_to_end_execution_query(text: str) -> bool:
    markers = (
        "系统自动完成",
        "完整执行方案",
        "从前期准备到后期宣传",
        "从头到尾",
        "端到端",
        "全自动",
        "end to end",
        "full workflow",
    )
    return any(_contains_trigger(text, marker) for marker in markers)


def _is_end_to_end_execution_skill(*values: str) -> bool:
    text = _normalize_match_text(*values)
    if not text:
        return False
    markers = (
        "autonomous tasks",
        "自驱动任务执行系统",
        "自动规划任务路径",
        "逐步完成任务",
        "自动触发后续任务",
        "连续工作流程",
        "end to end workflow execution",
    )
    return any(_contains_trigger(text, marker) for marker in markers)


def _normalize_match_text(*values: str) -> str:
    raw = " ".join(str(value or "") for value in values)
    expanded = _CAMEL_CASE_RE.sub(" ", raw)
    normalized = _NON_WORD_RE.sub(" ", expanded.lower())
    return " ".join(normalized.split())


def _contains_trigger(text: str, trigger: str) -> bool:
    needle = str(trigger or "").strip().lower()
    if not needle:
        return False
    if any("\u3400" <= ch <= "\u9fff" for ch in needle):
        return needle in text
    normalized = f" {text} "
    return f" {needle} " in normalized
