# SkillHub Backend Docker

镜像对应仓库 `marketplace` 模块，构建产物标记为 `skillhub-backend`（多阶段 wheel 构建）。

## Build

Run from repository root:

```powershell
cd <repo-root>

docker build -f docker/Dockerfile.skillhub-backend -t skillhub-backend:latest .
```

Notes:
- Build context is the repository root (`.`), same as the frontend image — see root `.dockerignore` for what is excluded from the context.
- Dockerfile automatically installs the wheel generated in `/app/dist` (no `VERSION` build-arg needed).
- Build does not depend on `uv.lock` (uses `uv sync` instead of `uv sync --frozen`).
- Runtime image includes `git` (required for Git 源接入 / `git-sources` sync).

## Run

```powershell
cd <repo-root>
mkdir marketplace\data -Force

docker run --rm --name skillhub-backend -p 8100:8100 `
  -v "${PWD}\marketplace\data:/app/data" `
  skillhub-backend:latest
```

Endpoints:
- Docs: `http://localhost:8100/api/docs`
- Health: `http://localhost:8100/api/health`
