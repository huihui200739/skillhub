# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""skill_runner config. Override via environment variables."""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass

# 独立运行 skill-runner（uvicorn skill_runner.app:app）时无 marketplace 的
# load_dotenv，best-effort 兜底加载项目根 .env。override=False 保证已 export
# 的环境变量优先（含 marketplace 同进程挂载时先行 load 的值），不被 .env 覆盖。
try:
    from dotenv import load_dotenv

    _ROOT_ENV = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"
    )
    if os.path.isfile(_ROOT_ENV):
        load_dotenv(_ROOT_ENV, override=False)
except Exception:  # noqa: BLE001 - dotenv 缺失或读取失败都不应阻断启动
    pass


def _env(name: str, default: str) -> str:
    val = os.environ.get(name)
    return val if val else default


@dataclass(frozen=True)
class Settings:
    # Executor backend: "k8s" (控制面，每 session 起 pod) | "local" (worker pod 内)
    executor: str = os.environ.get("SKILL_RUNNER_EXECUTOR", "k8s")

    # 空闲超时（idle reaper 用）：会话连续无任何事件多久算挂了。worker 每 15s 发 keepalive，
    # 健康会话不会触发；只用于回收真正卡死/worker 静默的悬挂会话。
    session_timeout_seconds: int = int(os.environ.get("SKILL_RUNNER_SESSION_TIMEOUT", "1800"))
    # 会话最大寿命（pod activeDeadlineSeconds 用）：kubelet 墙钟硬上限，到点强杀 pod，
    # 与空闲超时解耦——长 swarm 局（狼人杀等多轮对弈）需要更长的绝对寿命，否则单局会被腰斩。
    session_max_lifetime_seconds: int = int(os.environ.get("SKILL_RUNNER_SESSION_MAX_LIFETIME", "1800"))
    message_max_chars: int = int(os.environ.get("SKILL_RUNNER_MSG_MAX_CHARS", "4096"))
    message_max_turns: int = int(os.environ.get("SKILL_RUNNER_MSG_MAX_TURNS", "50"))
    # 并发 session 上限，超出则 create_session 返回 429
    max_concurrent_sessions: int = int(os.environ.get("SKILL_RUNNER_MAX_SESSIONS", "20"))

    # single-shell-command timeout (seconds)
    exec_timeout_seconds: int = int(os.environ.get("SKILL_RUNNER_EXEC_TIMEOUT", "60"))
    # !cmd 调试入口：worker 容器里直接跑用户输入的 shell（不走 LLM）。
    # 面向终端用户的确定性命令执行入口，默认关闭，生产保持 False。
    # agent 自身的 shell 工具另有容器+网络隔离边界，与此无关。
    allow_debug_shell: bool = os.environ.get("SKILL_RUNNER_ALLOW_DEBUG_SHELL", "false").lower() == "true"

    # per-session workspace root
    workspace_root: str = os.environ.get(
        "SKILL_RUNNER_WORKSPACE_ROOT",
        os.path.join(tempfile.gettempdir(), "skill-runner"),
    )

    # SSE event queue cap per session
    sse_buffer_max: int = int(os.environ.get("SKILL_RUNNER_SSE_BUFFER", "256"))

    # ---- k8s executor ----
    # worker pod 所在 namespace（与控制面隔离）；控制面 namespace 由 Deployment yaml 管理
    k8s_namespace: str = os.environ.get("SKILL_RUNNER_K8S_NAMESPACE", "skillhub-workers")
    k8s_pod_image: str = os.environ.get("SKILL_RUNNER_K8S_POD_IMAGE", "skill-agent-worker:0.1.0")
    # 本地 build 的镜像用 IfNotPresent（否则去 registry 拉）；生产推 registry 可用 Always
    k8s_image_pull_policy: str = os.environ.get("SKILL_RUNNER_K8S_IMAGE_PULL_POLICY", "IfNotPresent")
    # 默认空（Docker Desktop 无 kata，直接可跑）；生产可通过 env 指定 runtimeClass
    k8s_runtime_class: str = os.environ.get("SKILL_RUNNER_K8S_RUNTIME_CLASS", "")
    # securityContext 姿态：默认非特权。privileged=true 仅回退（个别本地运行时需要）。
    pod_privileged: bool = os.environ.get("SKILL_RUNNER_POD_PRIVILEGED", "false").lower() == "true"
    # 非特权姿态下 worker pod 的运行 uid（默认 nobody）
    pod_run_as_user: int = int(os.environ.get("SKILL_RUNNER_POD_RUN_AS_USER", "65534"))
    # seccomp 策略：使用 RuntimeDefault（K8s 默认安全配置，过滤约 44 个高危系统调用）
    pod_seccomp_profile_type: str = os.environ.get("SKILL_RUNNER_POD_SECCOMP_PROFILE", "RuntimeDefault")
    pod_port: int = int(os.environ.get("SKILL_RUNNER_POD_PORT", "8080"))
    # 冷启动含镜像拉取 + openjiuwen import，给足时间
    pod_ready_timeout_seconds: int = int(os.environ.get("SKILL_RUNNER_POD_READY_TIMEOUT", "120"))
    # pod 回调控制面 LLM 代理的可达地址（in-cluster service URL）
    proxy_base_url: str = os.environ.get("SKILL_RUNNER_PROXY_BASE_URL", "http://skill-runner:8900")

    # Optional cloud K8s pod controls. Keep all defaults empty so local Docker Desktop
    # can run without cluster-specific objects; production can enable them via env only.
    k8s_service_account_name: str = os.environ.get("SKILL_RUNNER_K8S_SERVICE_ACCOUNT", "")
    k8s_image_pull_secrets: str = os.environ.get("SKILL_RUNNER_K8S_IMAGE_PULL_SECRETS", "")
    k8s_node_selector: str = os.environ.get("SKILL_RUNNER_K8S_NODE_SELECTOR", "")
    k8s_tolerations: str = os.environ.get("SKILL_RUNNER_K8S_TOLERATIONS", "")
    k8s_resources: str = os.environ.get("SKILL_RUNNER_K8S_RESOURCES", "")

    # ---- Worker Pod 预热池 ----
    # 0 = 即用即弃（每 session 起删一个 pod）；>0 = 预热复用池
    pool_size: int = int(os.environ.get("SKILL_RUNNER_POOL_SIZE", "0"))
    # 池内最多保留多少个空闲 warm pod（release 回池超过则删）；默认等于 pool_size
    pool_max_idle: int = int(
        os.environ.get(
            "SKILL_RUNNER_POOL_MAX_IDLE",
            os.environ.get("SKILL_RUNNER_POOL_SIZE", "0"),
        )
    )


    # ---- LLM (worker pod 内 DeepAgent 使用；api_base 指控制面代理，key 不进 pod) ----
    # 配置优先级：SKILL_RUNNER_LLM_*（skill-runner 专属，推荐）
    #          > LLM_*（通用名，向后兼容）> API_*（最通用回退）
    # 专属前缀让 skill-runner 的 LLM 凭证独立成一套，避免与同进程 marketplace /
    # retrieval 的 LLM 配置互相串味。
    llm_provider: str = _env(
        "SKILL_RUNNER_LLM_PROVIDER",
        os.environ.get("LLM_PROVIDER", "OpenAI"),
    )
    llm_api_base: str = _env(
        "SKILL_RUNNER_LLM_API_BASE",
        _env("LLM_API_BASE", os.environ.get("API_BASE", "https://ark.cn-beijing.volces.com/api/coding/v3")),
    )
    llm_api_key: str = _env(
        "SKILL_RUNNER_LLM_API_KEY",
        _env("LLM_API_KEY", os.environ.get("API_KEY", "")),
    )
    llm_model_name: str = _env(
        "SKILL_RUNNER_LLM_MODEL_NAME",
        _env("LLM_MODEL_NAME", os.environ.get("MODEL_NAME", "doubao-seed-2-0-code")),
    )
    llm_client_id: str = _env(
        "SKILL_RUNNER_LLM_CLIENT_ID",
        os.environ.get("LLM_CLIENT_ID", "skill-runner-doubao"),
    )
    llm_timeout_seconds: float = float(
        _env("SKILL_RUNNER_LLM_TIMEOUT", os.environ.get("LLM_TIMEOUT", "120"))
    )
    # worker 在事件流静默期（如 deep agent 初始化、teammate 启动）每隔该秒数发一条
    # keepalive 行，防止控制面 -> pod 的 httpx read 超时把整条流掐断。必须显著小于
    # 控制面的 read 超时（见 k8s.py，= llm_timeout_seconds + 30）。
    worker_keepalive_seconds: float = float(
        _env("SKILL_RUNNER_WORKER_KEEPALIVE", os.environ.get("WORKER_KEEPALIVE", "15"))
    )
    llm_max_iterations: int = int(
        _env("SKILL_RUNNER_LLM_MAX_ITERATIONS", os.environ.get("LLM_MAX_ITERATIONS", "1000"))
    )
    llm_temperature: float = float(
        _env("SKILL_RUNNER_LLM_TEMPERATURE", os.environ.get("LLM_TEMPERATURE", "0.3"))
    )
    # 单次生成的 output token 上限。32768 覆盖复盘/长对话等大输出场景，避免 chunked 截断。
    llm_max_tokens: int = int(
        _env("SKILL_RUNNER_LLM_MAX_TOKENS", os.environ.get("LLM_MAX_TOKENS", "32768"))
    )
    swarm_max_roles: int = int(os.environ.get("SKILL_RUNNER_SWARM_MAX_ROLES", "3"))
    # 每用户每日 LLM token 上限（控制面 llm_proxy 层累计）；0 = 不限
    user_daily_token_limit: int = int(os.environ.get("SKILL_RUNNER_USER_DAILY_TOKEN_LIMIT", "500000"))
    # LLM 上游 TLS 验证。默认 false：当前部署环境缺 CA 证书链，开启会致握手失败。
    # 待镜像装好 ca-certificates / 配好内网 CA 后，置 true 防中间人。
    llm_verify_ssl: bool = os.environ.get("SKILL_RUNNER_LLM_VERIFY_SSL", "false").lower() == "true"


settings = Settings()

