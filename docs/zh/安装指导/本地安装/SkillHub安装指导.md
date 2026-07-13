# 本地安装指导（Windows 为主）

本文说明如何在本地安装并启动 **marketplace**（SkillHub 市场服务）。步骤以 **Windows / PowerShell** 为例；Linux / macOS 可将路径与激活命令替换为对应写法（如 `source .venv/bin/activate`）。

## 1. 环境要求

| 依赖 | 说明 |
|------|------|
| **Python** | 建议 **3.11+** |
| **包管理** | 推荐使用 [uv](https://github.com/astral-sh/uv)|
| **MySQL**（必选） | 当前版本仅支持 MySQL，需已安装并可连接；**须先手动建库**（见下文） |
| **对象存储**（必选） | **MinIO** 或 **华为云 OBS**；资产发布包上传依赖 S3 兼容 API，需可访问的桶与密钥 |
| **鉴权服务**（必选） | 需提前启动并可访问（由 `AUTH_SERVICE_HOST` / `AUTH_SERVICE_PORT` 指定）；用于 Bearer Token 鉴权 |
| **Node.js**（仅启动 Web 前端时） | 建议 **18+** 或 **20 LTS**；用于安装依赖并运行 `frontend`（Vite 开发服务器） |

> 启动 `marketplace` 前，请先确认鉴权服务已正常运行；否则接口请求会因鉴权失败而返回错误。

## 2. 获取代码

```powershell
git clone https://gitcode.com/openJiuwen/skillhub.git
```

## 3. 安装并准备 MySQL

### 3.1 安装 MySQL

若本机尚未安装 MySQL，请先完成安装后再继续后续步骤：

- 通过 [MySQL Installer](https://dev.mysql.com/downloads/installer/) 安装，建议 8.0+
- 安装后确认服务已启动，并记录 root 或业务账号密码

### 3.2 创建数据库并授权

配置 **`DB_TYPE=mysql`** 并设置 **`STORE_DB_NAME`**（例如 `openjiuwen_market`）后：

- **必须先在 MySQL 中创建对应数据库**。
- 示例（在 MySQL 客户端执行；库名请与 `STORE_DB_NAME` 保持一致）：

```sql
CREATE DATABASE IF NOT EXISTS openjiuwen_market
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci; 

-- 将下面占位符换成你在 .env 中的 DB_USER / DB_PASSWORD；库名与 STORE_DB_NAME 一致
-- 若用户已存在，可跳过 CREATE USER，只执行 GRANT + FLUSH
CREATE USER IF NOT EXISTS '你的DB_USER'@'%' IDENTIFIED BY '你的DB_PASSWORD';
GRANT ALL PRIVILEGES ON openjiuwen_market.* TO '你的DB_USER'@'%';
FLUSH PRIVILEGES;
```

## 4. 配置环境变量与对象存储

将仓库根目录的 **`.env.example`** 复制为 **`.env`**，填写相关配置信息。

### 4.1 Skill 审查相关配置

当前版本已支持 **Skill 发布后的自动系统审查**。建议关注以下配置：

- `MARKET_SKILL_REVIEW_ENABLED=false`
- `MARKET_SKILL_REVIEW_MODEL_*`：Skill 静态审查语义模型配置（OpenAI-compatible Chat Completions）

说明：

- **默认关闭**（`MARKET_SKILL_REVIEW_ENABLED=false`）：Skill 发布后跳过系统审查，直接进入人工审核。
- **开启后**（`MARKET_SKILL_REVIEW_ENABLED=true`）：Skill 先经系统审查，通过后再进入人工审核；审查不通过则发布失败。须同时配置完整的 `MARKET_SKILL_REVIEW_MODEL_*` 语义模型参数，否则发布会被前置拒绝，不会创建待审版本。
- 系统审查目前仅覆盖 `plugin_type=skill` 的普通 Skill；**Swarm Skill**（`swarmskill`）仍走原有人工审核链路。

### 4.2 对象存储（MinIO / OBS）

发布包与图标等对象通过 **S3 兼容 API** 上传。服务启动时会通过 `head_bucket` 检查桶是否存在；若桶不存在会直接报错，请先创建桶或修正配置。

**MinIO（本地/自建）**

(1). 部署 MinIO，暴露 API 端口（常见 **9000**）及控制台（常见 **9001**）。
(2). 创建 **Access Key / Secret Key**，并创建与 **`MARKET_BUCKET_NAME`** 一致的 **Bucket**。
(3). 在 `.env` 中配置（示例）：
   - `STORAGE_TYPE=MinIO`
   - `MARKET_S3_ENDPOINT=http://127.0.0.1:9000`（按实际地址）
   - `MARKET_S3_ACCESS_KEY` / `MARKET_S3_SECRET_KEY`
   - `MARKET_BUCKET_NAME` 与控制台桶名一致
   - （可选）`MARKET_S3_PRESIGNED_EXPIRES`：图标与发布包预签名链接有效期（秒），默认 1800

**华为云 OBS（云端）**

(1). 创建 OBS 桶，名称与 **`MARKET_BUCKET_NAME`** 一致，区域与 **`MARKET_S3_REGION`** 一致。
(2). 使用 IAM 用户 **AK/SK** 填入 `MARKET_S3_ACCESS_KEY` / `MARKET_S3_SECRET_KEY`。
(3). 在 `.env` 中配置（示例）：
   - `STORAGE_TYPE=OBS`
   - `MARKET_S3_ENDPOINT=https://obs.<区域>.myhuaweicloud.com`
   - `MARKET_S3_ACCESS_KEY` / `MARKET_S3_SECRET_KEY`
   - `MARKET_S3_REGION` 与桶所在区域一致
   - `MARKET_BUCKET_NAME` 与 OBS 桶名一致（桶保持私有；访问对象仅通过接口返回的预签名 URL）

### 4.3 对外鉴权服务依赖

SkillHub 默认支持 GitCode Token 鉴权，并可选启用 GitCode 或 GitHub OAuth 登录。部署环境启用相应功能时，需允许下表中的 HTTPS 通信；所有地址均可通过环境变量覆盖，以实际部署配置为准。

| 默认地址 | 发起方 | 协议/端口 | 用途 | 携带信息 | 启用条件 | 配置变量 |
|------|------|------|------|------|------|------|
| `https://gitcode.com/api/v5/user` | Marketplace 后端 | HTTPS/443 | 校验 GitCode Token 并获取用户信息 | `access_token` 查询参数 | 使用默认 GitCode Token 鉴权时 | `AUTH_USER_API_URL` |
| `https://gitcode.com/oauth/authorize` | 用户浏览器 | HTTPS/443 | 跳转至 GitCode 授权页面 | Client ID、回调地址、Scope、State | 启用 GitCode OAuth 时 | `MARKET_GITCODE_OAUTH_AUTHORIZE_URL` / `GITCODE_OAUTH_AUTHORIZE_URL` |
| `https://gitcode.com/oauth/token` | Marketplace 后端 | HTTPS/443 | OAuth 回调阶段换取访问令牌 | Client ID、Client Secret、授权码、回调地址 | 启用 GitCode OAuth 时 | `MARKET_GITCODE_OAUTH_TOKEN_URL` / `GITCODE_OAUTH_TOKEN_URL` |
| `https://github.com/login/oauth/authorize` | 用户浏览器 | HTTPS/443 | 跳转至 GitHub 授权页面 | Client ID、回调地址、Scope、State | 启用 GitHub OAuth 时 | `MARKET_GITHUB_OAUTH_AUTHORIZE_URL` / `GITHUB_OAUTH_AUTHORIZE_URL` |
| `https://github.com/login/oauth/access_token` | Marketplace 后端 | HTTPS/443 | OAuth 回调阶段换取访问令牌 | Client ID、Client Secret、授权码、回调地址 | 启用 GitHub OAuth 时 | `MARKET_GITHUB_OAUTH_TOKEN_URL` / `GITHUB_OAUTH_TOKEN_URL` |
| `https://api.github.com/user` | Marketplace 后端 | HTTPS/443 | 获取 GitHub 用户信息 | Bearer Token | 启用 GitHub OAuth 时 | `MARKET_GITHUB_AUTH_USER_API_URL` / `GITHUB_AUTH_USER_API_URL` |

> 两个授权页面由后端生成跳转地址后交由用户浏览器访问，不属于 Marketplace 后端的出站请求；其余四个地址由 Marketplace 后端访问。
>
> 完整对外通信矩阵见 [通信矩阵](../../安全/通信矩阵.md)。

## 5. 安装依赖并启动 marketplace

在 **PowerShell** 或 **cmd** 中进入 **`marketplace`** 目录：

```powershell
cd marketplace
uv sync
.venv\Scripts\activate
python main.py
```

服务启动后会监听环境变量 **`STORE_HOST` / `STORE_PORT`** 指定的地址。

## 6. 安装依赖并启动 frontend（Web）

若需在本地打开 **SkillHub Web 界面**（`frontend` 目录），在 **PowerShell** 或 **cmd** 中执行：

```powershell
cd frontend
npm install
npm run dev
```

- **访问地址**：开发服务器默认端口为 **`9002`**（见 `frontend/vite.config.ts`）。浏览器打开终端提示的本地地址，一般为 `http://127.0.0.1:9002`。若需改端口，可在**仓库根目录**的 `.env` 中设置 **`FRONTEND_PORT`**（Vite 的 `envDir` 为仓库根目录）。
- **接口代理**：开发环境下，浏览器请求 **`/api/v1`** 会由 Vite 将 **`/api`** 转发到 **`http://BACKEND_URL:BACKEND_PORT`**（变量来自**仓库根目录** `.env`，与前端 Docker 镜像中 Nginx 反代使用同一套变量名）。默认 **`BACKEND_URL=localhost`**、**`BACKEND_PORT=8100`**，请与 **marketplace 实际监听地址**（`STORE_HOST` / `STORE_PORT`）一致。

  前端请求的 API 基路径默认为 **`VITE_API_BASE_URL`**（未设置时为 `/api/v1`），一般与上述代理配合即可，无需修改。
- **跨域（CORS）说明**：`marketplace` **未对浏览器开启跨域白名单**。因此请**不要**在「页面所在源」与「`http://127.0.0.1:<STORE_PORT>`」不一致的情况下，把 **`VITE_API_BASE_URL`** 指到后端绝对地址并直连；否则浏览器会拦截请求。  
  **推荐做法**：开发时始终通过 **Vite 开发服（同源）+ 上述 `/api` 代理** 访问接口；生产或与 Docker 一致时，通过 **同域反向代理**（Nginx 将 `/api/` 转到后端）访问。
- **顺序**：请先完成 **第 5 节**启动 **marketplace**，再启动 **frontend**，否则资产列表等接口无法访问。

生产构建与预览（可选）：

```powershell
cd frontend
npm run build
npm run preview
```

## 7. 验证

可通过浏览器或命令行访问：

| 说明 | 地址 |
|------|------|
| marketplace 健康检查 | `http://127.0.0.1:<STORE_PORT>/api/health` |
| SkillHub Web（已按第 6 节启动 frontend） | 一般为 `http://127.0.0.1:9002`（以终端输出为准） |

同时请确认鉴权服务地址（`AUTH_SERVICE_HOST` / `AUTH_SERVICE_PORT`）可连通。

## 8. 常见问题

- **`Unknown database`**：确认已执行建库 SQL，且 `STORE_DB_NAME` 与授权库名一致。
- **对象存储启动即报错（`head_bucket` 失败）**：确认桶已创建、AK/SK 正确、`MARKET_S3_ENDPOINT` 可访问。
- **鉴权相关错误（401/403 或连接失败）**：确认鉴权服务已启动，且 `.env` 中 `AUTH_SERVICE_HOST` / `AUTH_SERVICE_PORT` 配置正确。
- **前端页面无法加载资产列表 / 接口连错端口**：确认 marketplace 已启动，且仓库根目录 `.env` 中 **`BACKEND_URL` / `BACKEND_PORT`** 与 **`STORE_HOST` / `STORE_PORT`** 一致（见第 6 节）。
- **浏览器报 CORS / 跨域错误**：多为页面与 API **不同源**且未走代理。请使用 Vite 默认的 **`/api/v1` 相对路径** 与 **`BACKEND_*` 代理**；勿在跨源场景下把 API 基地址指到 `http://...:8100`（见第 6 节「跨域（CORS）说明」）。

## 9. 检索系统部署（可选）

检索系统为 Skill 列表提供语义搜索能力。**不部署时，`search_keyword` 参数传入后接口会自动降级为数据库排序查询，不影响其他功能。**

### 9.1 架构概览

```
┌──────────────┐     ┌──────────────────┐     ┌───────────────┐
│  marketplace  │────▶│  IndexManager    │────▶│  OBS/MinIO    │
│  (在线检索)    │     │  (内存索引持有者)  │     │  (索引文件存储) │
└──────────────┘     └──────────────────┘     └───────────────┘
       │                    │
       │                    │ 热加载通知
       ▼                    ▼
┌──────────────┐     ┌───────────────┐
│  APScheduler │     │  Redis Stream │
│  (定时重建)    │     │  (跨实例通知)  │
└──────────────┘     └───────────────┘
```

- **离线构建**：APScheduler 按 cron 表达式定时触发，从数据库读取 Skill 元数据，调用 Embedding / LLM 生成索引文件，写入 OBS/MinIO
- **在线检索**：`IndexManager` 在内存中持有索引，接口请求时直接查询，无网络 IO
- **热加载**：构建完成后通过 Redis Stream 广播 `index:reload`，各实例收到后原子替换内存索引

### 9.2 依赖项

| 依赖 | 是否必须 | 说明 |
|------|---------|------|
| **Embedding API** | 按检索策略 | 向量检索必需；仅 BM25 时可省略 |
| **LLM API** | 按检索策略 | progressive / tree 索引必需；纯 embedding + BM25 时可省略 |
| **Redis** | 多实例必须 | 单实例可选（索引热加载通知走 Redis Stream；未配 Redis 时仅本进程内存生效） |

两个 API 均兼容 OpenAI 接口格式（`/v1/embeddings`、`/v1/chat/completions`），可使用任意兼容服务。

### 9.3 检索策略选择

根据本地环境选择合适的策略。下表中的「构建 / 检索」两列对应 `.env` 中的**完整变量名**（与代码 `plugins_market/core/config.py` 一致）：

- **`MARKET_RETRIEVAL_BUILD_METHOD`**：离线索引构建策略
- **`MARKET_RETRIEVAL_SEARCH_METHOD`**：在线检索策略

| 策略 | `MARKET_RETRIEVAL_BUILD_METHOD` 取值 | `MARKET_RETRIEVAL_SEARCH_METHOD` 取值 | 需要 Embedding | 需要 LLM | 特点 |
|------|--------------------------------------|---------------------------------------|:---:|:---:|------|
| **纯 BM25** | `bm25` | `bm25` | ❌ | ❌ | 最轻量，关键词匹配，无需外部模型 |
| **向量 + BM25**（推荐） | `embedding_bm25` | `embedding` | ✅ | ❌ | 语义搜索 + 关键词兜底，性能与精度平衡 |
| **全量** | `all` | `auto` 或 `progressive` | ✅ | ✅ | 精度最高，延迟较高 |

### 9.4 环境变量配置

在 `.env` 中添加以下配置（按所选策略填写）：

**密钥说明**：`MARKET_RETRIEVAL_EMBEDDING_API_KEY`、`MARKET_RETRIEVAL_MODEL_API_KEY`、`MARKET_RETRIEVAL_SKILL_TAG_LLM_API_KEY`（若单独配置技能标签）等与项目其他敏感项相同，由启动流程经 `SecurityUtils` 读取；若环境使用**加密配置**，请按与其它服务密钥相同的方式填写，勿与明文约定混用造成误配。

**Embedding 模型（向量检索必需）**

```ini
MARKET_RETRIEVAL_EMBEDDING_API_BASE_URL=https://your-embedding-service/v1
MARKET_RETRIEVAL_EMBEDDING_API_KEY=sk-xxx
MARKET_RETRIEVAL_EMBEDDING_MODEL=test-embedding-model
MARKET_RETRIEVAL_EMBEDDING_BATCH_SIZE=16
```

**LLM 模型（progressive 检索必需，其他策略可省略）**

```ini
MARKET_RETRIEVAL_MODEL_API_BASE_URL=https://your-llm-service/v1
MARKET_RETRIEVAL_MODEL_API_KEY=sk-xxx
MARKET_RETRIEVAL_DEFAULT_LLM_MODEL=test-llm-model
```

**检索策略**

```ini
# 离线构建策略（默认 embedding_bm25）
MARKET_RETRIEVAL_BUILD_METHOD=embedding_bm25
# 在线检索策略（默认 embedding）
MARKET_RETRIEVAL_SEARCH_METHOD=embedding
```

**Redis（多实例部署时必需）**

```ini
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=0
MARKET_REDIS_PASSWORD=
```

**定时任务（可选调整）**

```ini
# 索引重建频率，默认每小时整点
MARKET_RETRIEVAL_REBUILD_CRON=0 * * * *
# 启动时是否立即构建一次（首次部署建议设为 true）
MARKET_RETRIEVAL_REBUILD_ON_STARTUP=true
# 技能标签分类频率；代码默认每分钟触发一次（运行日志中可见 skill_tag 任务）
# 生产环境若不需要如此频繁，可改为如 "0 * * * *"（每小时）等，以降低 LLM 调用与负载
MARKET_RETRIEVAL_SKILL_TAG_CRON=* * * * *
MARKET_RETRIEVAL_SKILL_TAG_ON_STARTUP=false
```

**进阶项（一般可不改）**：以下为排障或专家场景使用，完整列表以 `marketplace/plugins_market/core/config.py` 中 `Settings` 检索相关字段为准，例如：

- `MARKET_RETRIEVAL_SKILL_INDEX_PATH` / `MARKET_RETRIEVAL_PLUGIN_INDEX_PATH`：直接指定 `obs://bucket/path` 跳过自动发现（测试或固定版本）
- `MARKET_RETRIEVAL_FINDER_LLM_BASE_URL` / `MARKET_RETRIEVAL_FINDER_LLM_MODEL`：与主 LLM 分离时的 finder 配置
- `MARKET_RETRIEVAL_SKILL_TAG_LLM_MODEL`、`MARKET_RETRIEVAL_SKILL_TAG_LLM_API_BASE_URL`：技能标签分类独立 LLM（不配则回退主检索 LLM 配置）

**召回过滤（可选调优）**

```ini
# 向量相对分阈值（0~1），仅保留 score >= best_score * 阈值 的候选；不设则不过滤
# MARKET_RETRIEVAL_EMBEDDING_RELATIVE_MIN_SCORE=0.75
# BM25 最少命中 query 词数量，0 不过滤，推荐从 1 起调
# MARKET_RETRIEVAL_BM25_MIN_QUERY_TERM_MATCHES=1
```

### 9.5 首次启动流程

(1). 确认 `.env` 中 Embedding / LLM 相关配置已填写
(2). 设置 `MARKET_RETRIEVAL_REBUILD_ON_STARTUP=true`，使服务启动后立即触发首次索引构建
(3). 启动 marketplace（见第 5 节）
(4). 观察日志确认索引构建完成：

```
retrieval index rebuild run begin [skip_lock=False]
retrieval index rebuild run end [skip_lock=False elapsed=xx.xs]
retrieval warm-start: loading group=skill from obs://bucket/skills-index/2026042910
IndexManager: loaded group=skill from obs://..., cid_map size=xxx
```

(5). 验证检索功能：访问 `GET /api/v1/plugins?search_keyword=测试&plugin_type=skill`，确认返回结果

> 首次构建耗时取决于 Skill 数量和 Embedding API 响应速度。构建期间检索接口自动降级为数据库查询，不影响服务可用性。

### 9.6 索引存储与版本管理

- 索引文件存储在 OBS/MinIO 桶中，路径格式：`{prefix}/{YYYYMMDDH}/manifest.json`
  - Skill 默认前缀：`skills-index`（可通过 `MARKET_RETRIEVAL_SKILL_INDEX_OBS_PREFIX` 修改）
  - Plugin 默认前缀：`plugins-index`（可通过 `MARKET_RETRIEVAL_PLUGIN_INDEX_OBS_PREFIX` 修改）
- 每次构建生成一个带时间戳的目录，旧版本保留用于回滚
- 保留版本数默认 168（每小时构建一次 ≈ 保留一周），可通过 `MARKET_RETRIEVAL_INDEX_MAX_VERSIONS` 调整
- 构建失败时内存索引不变，重启后加载最近一次成功的版本

### 9.7 常见问题

- **日志 `retrieval module not importable`**：检索子模块未正确安装，确认 `uv sync` 已执行且无报错
- **日志 `retrieval: failed to create embedding client`**：`MARKET_RETRIEVAL_EMBEDDING_API_BASE_URL` 或 `MARKET_RETRIEVAL_EMBEDDING_API_KEY` 配置有误，或服务不可达
- **日志 `retrieval_search: index not ready for group=skill, fallback`**：索引尚未构建完成，接口已降级为数据库查询，等待构建完成即可
- **搜索无结果**：确认 `MARKET_RETRIEVAL_BUILD_METHOD` 包含 `embedding`（若 `MARKET_RETRIEVAL_SEARCH_METHOD=embedding`），且 Embedding API 可正常调用
- **多实例索引不同步**：确认 Redis 已配置且可连通；未配 Redis 时索引热加载仅在本进程生效

## 10. 更多文档

| 文档 | 说明 |
|------|------|
| [TeamSkillsHub 接口参考](../../接口文档/v1/TeamSkillsHub-接口参考.md) | **推荐** — 端点总览、curl 示例、可见性规则 |
| [TeamSkillsHub API（OpenAPI）](../../接口文档/v1/TeamSkillsHub.md) | OpenAPI YAML 与错误码速查 |
| [ClawHub 兼容层](../../接口文档/v1/ClawHub兼容层.md) | ClawHub CLI 协议适配 |
| [用户指南索引](../../用户指南/README.md) | 终端用户操作与 FAQ |
