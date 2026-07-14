# Windows 系统安装指导（Docker 方式）

本文说明在 Windows 上通过 Docker Desktop 自行构建并运行 SkillHub Backend（后端）与 Frontend（前端 Web，Nginx 静态 + 反代），并完成 MySQL、MinIO 等依赖配置。

**目标**：Backend 与 Frontend 容器启动后，浏览器与本机 CLI 能正常调用 API，且 Skill 包下载（服务端返回预签名 URL，客户端再直连对象存储）可成功。

## Skill 包下载与网络（建议先读）

Skill 下载涉及三段链路，配置不当时常表现为「能发版但下载失败」：

| 环节 | 说明 | 配置不当时的常见现象 |
|------|------|----------------------|
| Frontend → Backend | Nginx 将 `/api/` 转到 `BACKEND_URL:BACKEND_PORT` | 页面空白、接口 502 |
| Backend → MinIO | 容器内 boto3 访问 `MARKET_S3_ENDPOINT` | 启动失败、上传/读对象报错 |
| Browser / CLI → MinIO | 打开预签名 URL，直连 `MARKET_S3_ENDPOINT` 中的主机与端口 | 下载失败、超时、浏览器 502（常为公司代理） |

`MARKET_S3_ENDPOINT` 中的主机须 **同时** 满足：Backend 容器可访问，且运行浏览器与 CLI 的机器可访问。本机 MinIO 场景下，hosts 与代理配置见下文「对象存储」章节。

## 1. 环境准备

- 操作系统：Windows 10 及以上。
- Docker：Docker Desktop（推荐 WSL 2 后端）；`docker info` 能正常输出。
- MySQL：必选；库与账号须与 `.env.docker` 一致，且后端容器内可连通。

## 2. 准备环境变量文件

在 SkillHub 仓库根目录（含 `.env.example` 的目录）执行：

```powershell
Copy-Item ".env.example" ".env.docker"
```

编辑 `.env.docker`，填写 MySQL、对象存储、鉴权等。**勿将 `.env.example` 中的占位值直接用于生产**；复制后须按本文改为本机可达地址。

启用 GitCode Token 鉴权或 GitCode / GitHub OAuth 时，还需按[本地安装指导的“对外鉴权服务依赖”](../本地安装/SkillHub安装指导.md#43-对外鉴权服务依赖)放通对应 HTTPS 地址；表中配置变量同样适用于 `.env.docker`。

### Skill 审查相关配置

当前支持 Skill 发布后的自动系统审查。`.env.docker` 中可关注：

```env
MARKET_SKILL_REVIEW_ENABLED=false
MARKET_SKILL_REVIEW_MODEL_BASE_URL=
MARKET_SKILL_REVIEW_MODEL_API_KEY=
MARKET_SKILL_REVIEW_MODEL_NAME=
MARKET_SKILL_REVIEW_MODEL_TIMEOUT_SECONDS=300
```

说明：

- 默认关闭：未显式设置 `MARKET_SKILL_REVIEW_ENABLED=true` 时，Skill 跳过系统审查，直接进入人工审核
- 开启后：Skill 先经系统审查，通过后再进入人工审核；审查不通过则发布失败。须同时配置完整的 `MARKET_SKILL_REVIEW_MODEL_*` 参数，否则发布会被前置拒绝
- 系统审查目前仅覆盖 `plugin_type=skill` 的普通 Skill；**Swarm Skill**（`swarmskill`）仍走原有人工审核链路

### MySQL

后端在容器内访问宿主机上的 MySQL 时，常见写法：`DB_HOST=host.docker.internal`（与 MinIO 一致，见 Docker Desktop 说明）。

#### 0) 可选：用 Docker 起一个本机 MySQL（联调）

若本机尚未安装 MySQL，可在 PowerShell 中执行（密码、库名、用户需与下文 `.env.docker` 一致）：

```powershell
docker pull mysql:8.0

docker run -d --name mysql-market `
  -p 3306:3306 `
  -e MYSQL_ROOT_PASSWORD=your_root_password `
  -e MYSQL_DATABASE=openjiuwen_market `
  -e MYSQL_USER=your_user `
  -e MYSQL_PASSWORD=your_password `
  -v mysql-market-data:/var/lib/mysql `
  mysql:8.0 `
  --character-set-server=utf8mb4 --collation-server=utf8mb4_0900_ai_ci
```

首次启动需等待约 10～30 秒至 `ready for connections`。若宿主机 3306 已被占用，把映射改为例如 `-p 3307:3306`，并把 `.env.docker` 里 `DB_PORT` 改为 `3307`（`DB_HOST` 仍为 `host.docker.internal` 时，访问的是宿主机上映射端口）。

#### 1) 预先在 MySQL 中建库与授权

若未使用上一节镜像自动建库，请手动执行（库名、用户与 `.env.docker` 对齐）：

```sql
CREATE DATABASE IF NOT EXISTS openjiuwen_market
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE USER IF NOT EXISTS 'your_user'@'%' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON openjiuwen_market.* TO 'your_user'@'%';
FLUSH PRIVILEGES;
```

#### 2) 在 `.env.docker` 配置 MySQL（示例）

```env
DB_TYPE=mysql
DB_HOST=host.docker.internal
DB_PORT=3306
DB_USER=your_user
DB_PASSWORD=your_password
STORE_DB_NAME=openjiuwen_market
```

MySQL 若在其它主机或容器网络中，请将 `DB_HOST` / `DB_PORT` 改为容器内实际可达的地址。

### 对象存储：本地 MinIO（建议先跑通）

预签名 URL 里的主机来自 `MARKET_S3_ENDPOINT`：Backend 容器要能连上（SDK），浏览器/CLI 下载时也要能连上（见文首「Skill 包下载与网络」）。

#### 1) 拉取并启动 MinIO

```powershell
docker pull minio/minio

docker run -d --name minio `
  -p 9000:9000 `
  -p 9001:9001 `
  -v "minio-data:/data" `
  minio/minio server /data --console-address ":9001"
```

若 `--name minio` 已被占用，可改成例如 `--name skillhub-minio`。控制台 `http://localhost:9001`，用启动时的账号登录后，新建与 `.env.docker` 中 `MARKET_BUCKET_NAME` 完全一致的私有 Bucket。

#### 2) `.env.docker`（后端在 Docker 内访问宿主机上的 MinIO）

```env
STORAGE_TYPE=MinIO
MARKET_BUCKET_NAME=openjiuwen-market
MARKET_S3_ENDPOINT=http://host.docker.internal:9000
MARKET_S3_ACCESS_KEY=minioadmin
MARKET_S3_SECRET_KEY=minioadmin
# 可选：MARKET_S3_PRESIGNED_EXPIRES=1800
# 非默认控制台账号时，同步改 ACCESS_KEY / SECRET_KEY；一般不必填 MARKET_S3_REGION
```

若 `host.docker.internal` 在你环境解析异常，可把 `MARKET_S3_ENDPOINT` 改成 `http://<本机 IPv4>:9000`（`ipconfig` 里以太网/WLAN 地址，与映射端口一致），并确认后端容器内能访问该 IP、防火墙放行。

#### 3) 常见问题（hosts / 代理）

**`host.docker.internal` 不通或解析不对**

- 原因：Windows 对 hosts 同名多条记录通常匹配靠前一行；Docker Desktop 可能在 `# Added by Docker Desktop` 段写入非回环地址，导致末尾的 `127.0.0.1` 不生效。
- 处理（管理员）：编辑 `C:\Windows\System32\drivers\etc\hosts`，注释或删除该段内指向非回环的 `host.docker.internal`、`gateway.docker.internal` 行。
- 保证存在：`127.0.0.1 host.docker.internal`（可选 `127.0.0.1 gateway.docker.internal`）。
- 保存后执行 `ipconfig /flushdns`；`ping host.docker.internal` 应显示 `127.0.0.1`。
- 验证：`curl.exe -sS -o NUL -w "%{http_code}" "http://host.docker.internal:9000/minio/health/live"`，期望 `200`。
- Docker Desktop 升级后若异常行被写回，按同样步骤再处理一次。

**浏览器 502、curl 正常**

- 原因：浏览器走系统代理，本机 MinIO 请求被转到公司代理，代理无法访问 `host.docker.internal` 等本机地址。
- 处理：打开「设置 → 网络和 Internet → 代理」，为本地开发增加「不使用代理」地址（按公司策略裁剪），例如：
  `localhost;127.*;10.*;172.16.*;172.17.*;172.18.*;172.19.*;192.168.*;host.docker.internal;*.docker.internal;<local>`
- 或暂时关闭「使用代理服务器」对比是否为代理问题。
- 使用 PAC 时，需由 IT 将上述主机加入直连/绕过列表。

#### 4) 下载仍失败时快速核对

- 端口：`Test-NetConnection -ComputerName 127.0.0.1 -Port 9000`（或 `MARKET_S3_ENDPOINT` 里的主机与端口）。
- 桶名与 `MARKET_BUCKET_NAME` 一致；改 `.env.docker` 后需重启后端容器，再重新取预签名链接。
- 后端容器内（容器名按你实际为准，如 `skillhub-backend`）：`docker exec <后端容器名> curl -sS -o NUL -w "%{http_code}" http://host.docker.internal:9000/minio/health/live`（镜像需带 `curl`），期望 `200`。

### 对象存储：华为云 OBS（示例）

```env
STORAGE_TYPE=OBS
MARKET_S3_ENDPOINT=https://obs.<区域>.myhuaweicloud.com
MARKET_S3_ACCESS_KEY=你的_ACCESS_KEY
MARKET_S3_SECRET_KEY=你的_SECRET_KEY
MARKET_S3_REGION=<区域>
MARKET_BUCKET_NAME=<你的桶名>
```

桶建议保持私有；对象访问依赖接口返回的预签名 URL。

### 检索系统（可选）

在 `.env.docker` 按需填写 `MARKET_RETRIEVAL_*`；不写则带 `search_keyword` 的请求降级为数据库查询。详见 [《本地安装》「第 9 节 检索系统部署」](../本地安装/安装指导.md)。

- 容器须能访问 Embedding / LLM 的 Base URL。
- 使用 MinIO 时，索引与附件同桶；若下载异常，结合上文 hosts、代理，或改用本机 IPv4 的 `MARKET_S3_ENDPOINT` 排查。

## 3. 构建镜像并启动

镜像由你本机 `docker build` 生成（不依赖预置镜像仓库）。以下命令在 SkillHub 仓库根目录（含 `docker/`、`marketplace/`、`frontend/`）执行；路径与标签可按需修改。

推荐顺序：MySQL、MinIO 就绪且 `.env.docker` 已保存 → 3.1、3.2 构建镜像 → 启动 3.3 Backend 并确认日志无持续报错 → 再启动 3.4 Frontend（务必传入 `BACKEND_URL` / `BACKEND_PORT`，勿依赖 Frontend 镜像内默认的 `localhost`）。

更细的 Dockerfile 说明见：`docker/README.skillhub-backend.md`（后端）、`docker/README.skillhub-frontend.md`（前端）。

### 3.1 构建 SkillHub Backend 镜像

后端源码在仓库 `marketplace` 目录；构建上下文为 `.`（由根目录 `.dockerignore` 控制发送内容）。

```powershell
cd D:\workspace\openjiuwen\skillhub

docker build -f docker/Dockerfile.skillhub-backend -t skillhub-backend:latest .
```

`-t` 标签可改为例如 `skillhub-backend:0.0.1`；下文运行示例使用 `skillhub-backend:latest`。

### 3.2 构建 SkillHub Frontend 镜像

与 3.1 相同，在仓库根构建（上下文为 `.`）。

```powershell
cd D:\workspace\openjiuwen\skillhub

docker build -f docker/Dockerfile.skillhub-frontend -t skillhub-frontend:latest .
```

站点默认在根路径，API 为 `/api/v1`。若需挂载 `/hub` 与 `/hub/api/v1`，请增加 build-arg，例如：

```powershell
docker build -f docker/Dockerfile.skillhub-frontend `
  --build-arg FRONTEND_BASE_PATH=hub `
  --build-arg VITE_API_BASE_URL=/hub/api/v1 `
  -t skillhub-frontend:latest .
```

### 3.3 启动 SkillHub Backend

```powershell
docker run --rm --name skillhub-backend `
  -p 8100:8100 `
  --env-file "D:\workspace\openjiuwen\skillhub\.env.docker" `
  skillhub-backend:latest
```

将 `--env-file` 改为你的 `.env.docker` 绝对路径。

可选：持久化后端运行时数据目录（与 `docker/README.skillhub-backend.md` 一致）：

```powershell
mkdir marketplace\data -Force

docker run --rm --name skillhub-backend `
  -p 8100:8100 `
  -v "${PWD}\marketplace\data:/app/data" `
  --env-file "D:\workspace\openjiuwen\skillhub\.env.docker" `
  skillhub-backend:latest
```

（在仓库根目录执行时 `${PWD}` 为当前路径；也可写成 MinGW/Git Bash 下的 `$(pwd)`，以你终端为准。）

### 3.4 启动 SkillHub Frontend

```powershell
docker run -d --rm --name skillhub-frontend `
  -p 9002:9002 `
  -e BACKEND_URL=host.docker.internal `
  -e BACKEND_PORT=8100 `
  skillhub-frontend:latest
```

说明：

- `-p 9002:9002`：浏览器访问 `http://localhost:9002`。若构建时使用了 `FRONTEND_BASE_PATH=hub`，入口可能为 `http://localhost:9002/hub`。
- `BACKEND_URL` / `BACKEND_PORT`：Nginx 将 `/api/`（或 `/hub/api/`）转发到 `http://BACKEND_URL:BACKEND_PORT`（不要在 `BACKEND_URL` 里写 `http://`）。后端映射为 `-p 8100:8100` 时，Docker Desktop 下常见为 `BACKEND_URL=host.docker.internal`、`BACKEND_PORT=8100`。
- 与 3.3 的对应关系：`BACKEND_PORT` 必须等于 3.3 中 `-p` 的「左侧」宿主机端口（示例为 `8100`）。若后端改为 `-p 18080:8100`，前端应设 `BACKEND_PORT=18080`。若前后端在同一自定义网络且后端容器名为 `skillhub-backend`，可改为 `BACKEND_URL=skillhub-backend`、`BACKEND_PORT=8100`（容器内监听端口，一般为 `8100`）。
- `.env.docker` 中的 `BACKEND_URL=localhost` 仅供本机 `npm run dev` 使用，**不会**自动注入 Frontend 容器。Frontend 容器须通过 `-e BACKEND_URL=...` 显式指定 Backend 地址；若 `--env-file .env.docker` 且未用 `-e` 覆盖，Nginx 会连容器自身的 `localhost:8100`，导致 502。
- 跨域：Backend 未对浏览器配置 CORS；请通过 `http://localhost:9002` 同源访问 `/api/`，不要从其它源页直接请求 `http://...:8100/api/...`。

自检：浏览器打开 `http://localhost:9002/api/health`（若为 `/hub` 部署，则试 `http://localhost:9002/hub/api/health`），应非 502。

### 3.5 本机 CLI

CLI 通常直连 `http://127.0.0.1:8100`（或你的后端映射端口）调 API，再按返回的预签名 URL 访问 MinIO。请保证 `MARKET_S3_ENDPOINT` 对 CLI 所在环境可达（与浏览器相同逻辑）；公司代理可能影响 HTTP 客户端，需与浏览器类似配置或绕过。

## 4. 访问接口

- curl / 其它服务：可直接访问 `http://localhost:8100`。
- 浏览器：使用 `http://localhost:9002`，经 Nginx 同源 `/api/`。

示例：

```bash
curl --location 'http://localhost:8100/api/v1/plugins'
```

使用 `X-System-Token` 时，与 `.env.docker` 中系统 token 一致；使用 `Authorization` 时，参见 [GitCode 访问令牌](https://docs.gitcode.com/docs/help/home/user_center/security_management/user_pat)。
