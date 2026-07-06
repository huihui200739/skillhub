# skill-runner

Playground 在线试用的会话编排服务。负责试用会话的生命周期与流式输出，本身不执行
skill 逻辑——执行委托给可插拔的**沙箱**执行器。

## 设计原则

**Playground 在线试用必须运行在沙箱里。** 不论是开发机本地起服务，还是云上
部署，skill 代码与 LLM 自动决策的 shell 命令都跑在隔离容器内，不允许任何
"无沙箱本地直接 subprocess" 的模式。

| 文件 | 职责 |
|------|------|
| `app.py` | FastAPI 应用：会话创建 / 发消息 / SSE 流 / 结束 |
| `models.py` | 请求响应与会话状态模型（含 `system_prompt`、`extra` 字段） |
| `session_store.py` | 会话存储（进程内字典，控制面单副本） |
| `skill_loader.py` | 从 marketplace 拉 skill ZIP，解析 SKILL.md/workflow.md/roles |
| `executor/` | 可插拔执行器：`local`（pod 内直接运行）/ `k8s`（控制面起 pod） |

## Executor 选型

| 名称 | 隔离边界 | 用途 |
|---|---|---|
| `local` | pod 级（容器 + k8s + VPC 网络隔离） | worker pod 内部使用 |
| `k8s`   | 每会话独立 worker Pod，可配 Kata/默认 runtime | 控制面（开发机 / 云上 K8s）|

`SKILL_RUNNER_EXECUTOR=local` 由 worker pod 使用，不直接在控制面起。
控制面始终用 `SKILL_RUNNER_EXECUTOR=k8s`。

## 本地运行（控制面 + k8s）

```bash
# 构建 worker 镜像，再起控制面（见 docker/skill-agent-worker/RUNBOOK.md 完整流程）
export SKILL_RUNNER_EXECUTOR=k8s
export SKILL_RUNNER_K8S_POD_IMAGE=skill-agent-worker:latest
export SKILL_RUNNER_K8S_IMAGE_PULL_POLICY=IfNotPresent
export SKILL_RUNNER_PROXY_BASE_URL=http://host.docker.internal:8900
export LLM_API_KEY=<your-llm-key>
uvicorn skill_runner.app:app --host 0.0.0.0 --port 8900

# 冒烟测试
python -m skill_runner.tests.st.k8s_smoke
```

## 集成方式

skill-runner 是**独立部署的进程**（独立 venv，agent-core 重依赖只装在此侧）。marketplace
通过 HTTP **反向代理**把 `/api/v1/playground/*` 透明转发过来，两者不在同进程：

```python
# marketplace/plugins_market/routers/register.py
if settings.playground_enabled:
    from plugins_market.routers.playground_proxy import router as playground_proxy_router
    app.include_router(playground_proxy_router, prefix="/api/v1")
    # playground_proxy 把请求转发到 settings.skill_runner_url（独立 skill-runner 服务）
```

前端无需任何 URL 切换，命中的依然是 `/api/v1/playground/*`；marketplace 侧只做鉴权 +
配额 + ZIP 文本注入，真正的会话编排在独立的 skill-runner 进程。

## SSE 事件

单条 SSE 连接覆盖整个 session：

| 事件 | 何时 |
|---|---|
| `ready`         | session 创建成功 |
| `text`/`reasoning` | LLM 流式增量 |
| `tool_call` / `tool_result` | sandbox 内工具调用 |
| `answer`        | 一轮最终答复 |
| `done`          | 一轮结束（连接**不**关闭，支持多轮） |
| `error`         | 单轮错误 |
| `session_ended` | session 被 DELETE，连接关闭 |


## K8s executor 配置

K8s executor 的环境变量、默认值与生产配置说明见 `.env.example` §SKILL PLAYGROUND 及 `config.py` 行内注释。
