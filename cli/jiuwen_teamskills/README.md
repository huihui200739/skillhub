# jiuwen-teamskills（TeamSkills 命令行工具）

面向 TeamSkills Hub 的命令行工具：在本地生成与校验 `skill/teamskills` 目录、打包上传、检索与安装。**PyPI 发行名与安装后的入口命令为 `jiuwen-teamskills`**（`pip install jiuwen-teamskills`）。本仓库内 Python 包目录名为 **`jiuwen_teamskills/`**（下划线），与发行名中的连字符不同，属刻意约定。

> **分发说明**：当前建议从本仓库源码安装（见第 2 节）。后续发布 PyPI 后，可直接 `pip install jiuwen-teamskills`。

---

## 1. 环境要求

| 项目 | 说明 |
|------|------|
| Python | **>= 3.11.4**（与 `pyproject.toml` 一致） |
| 操作系统 | Windows / Linux / macOS |
| 对接市场 | 可选；仅在 `search/info/install/publish/delete/skill-import` 等市场相关命令时需要 |

---

## 2. 安装

在仓库根目录下进入 `skillhub/cli/jiuwen_teamskills` 后执行：

```bash
cd skillhub/cli/jiuwen_teamskills
pip install -e .
```

- `-e`（可编辑）模式适合开发联调，改源码后无需反复重装。
- 安装成功后建议执行 `jiuwen-teamskills -h` 验证命令可用。

若 PATH 未生效，可用模块方式：

```bash
cd skillhub/cli/jiuwen_teamskills
python -m jiuwen_teamskills.main -h
```

---

## 3. 快速上手（不依赖市场）

以下命令仅依赖本地文件系统，不访问市场服务：

```bash
# 1）初始化（默认类型为 teamskills）
jiuwen-teamskills init demo-ts --path .

# 也可创建普通单技能
jiuwen-teamskills init demo-skill --path . --type skill

# 2）校验目录
jiuwen-teamskills validate ./demo-ts

# 3）打包（默认输出到目标目录下 out/）
jiuwen-teamskills pack ./demo-ts
```

说明：

- `--type teamskills` 会在 `SKILL.md` frontmatter 写入 `kind: team-skill` 及默认 `roles` 占位。
- `init` 默认生成扁平结构：`<name>/SKILL.md`。
- `validate` / `pack` 的主入口识别优先级：先看 `root/SKILL.md`（flat）；若不存在，再要求 `root/<slug>/SKILL.md`（single nested，且该层仅一个候选目录）。
- 对 `teamskills`，上述规则仅用于定位“主技能入口”；`roles/` 等子目录下可包含多个角色 `SKILL.md`（或 `skill.md`）文档，不作为单包入口冲突处理。
- 如你们 Hub 还要求 `workflow.md`、`bind.md`、`dependencies.yaml`、`roles/*.md` 等文件，请按平台规范补齐。

---

## 4. 环境变量与鉴权

CLI 仅读取**当前进程**的环境变量（`os.environ`），不会自动读取任意路径的 `.env` 文件。

| 变量 | 作用 |
|------|------|
| `JIUWEN_TEAMSKILLS_MARKET_URL` | TeamSkills 市场根 URL（不含 `/api/v1/...`） |
| `OPENJIUWEN_MARKET_URL` | 兼容变量；与上项二选一 |
| `OPENJIUWEN_USER_TOKEN` | 用户 Bearer Token（用于 `publish` / `delete`） |
| `OPENJIUWEN_SYSTEM_TOKEN` | 系统管理员 Token（`X-System-Token`，用于 `publish` / `delete` / `skill-import`） |
| `LOG_LEVEL` | 日志级别（默认 `INFO`） |

优先级：**命令行参数 > 环境变量**。  
安全提示：不要把 token 提交到仓库。

PowerShell 示例：

```powershell
$env:JIUWEN_TEAMSKILLS_MARKET_URL = "http://127.0.0.1:8100"
$env:OPENJIUWEN_USER_TOKEN = "<你的 Token>"
```

Linux / macOS 示例：

```bash
export JIUWEN_TEAMSKILLS_MARKET_URL=http://127.0.0.1:8100
export OPENJIUWEN_USER_TOKEN="<你的 Token>"
```

---

## 5. 市场 URL 与版本规则

`--market-url` 与环境变量二选一即可。未提供时，依赖市场的命令会直接报错退出。

- URL 规则：传市场**根地址**，不要带 `/api/v1`。
- 版本规则：`publish --version` 仅支持 `x.y.z` 三段数字（可带 `v` 前缀后规范化），不支持 `1.0.0-rc1`。
- 帮助命令：`jiuwen-teamskills -h` 查看全部子命令；`jiuwen-teamskills <子命令> -h` 查看该子命令参数。

---

## 6. 子命令与参数

| 子命令 | 作用 |
|--------|------|
| `init` | 初始化 teamskills/skill 本地目录 |
| `validate` | 校验目录结构与元数据 |
| `pack` | 将 skill 目录打为 zip |
| `publish` | 上传单个 skill（支持直接上传 zip） |
| `info` | 查询某个 skill 的版本详情 |
| `search` | 搜索技能列表 |
| `delete` | 删除某版本或整包 |
| `install` | 下载并安装到本地目录 |
| `skill-import` | 系统管理员批量导入技能集合包 |

### 6.1 `init` — 生成脚手架

| 参数 | 必填 | 说明 |
|------|------|------|
| `name` | 是 | 技能名称；作为新建目录名 |
| `--path` | 否 | 在哪个父目录下创建 `name/` 子目录，默认 `.` |
| `--type` | 否 | `teamskills`（默认）\| `skill` |
| `--force` | 否 | 目标目录已存在且非空时仍覆盖初始化 |

### 6.2 `validate` — 校验技能目录

| 参数 | 必填 | 说明 |
|------|------|------|
| `path` | 是 | 技能根目录路径（主入口识别：`root/SKILL.md` 优先，否则 `root/<slug>/SKILL.md`） |

### 6.3 `pack` — 打包为 zip

| 参数 | 必填 | 说明 |
|------|------|------|
| `path` | 是 | 技能根目录路径 |
| `-o` / `--output` | 否 | 输出目录，默认 `out`（相对路径相对 `path` 解析） |

打包前会先执行与 `validate` 一致的结构与字段校验。

### 6.4 `publish` — 上传市场

| 参数 | 必填 | 说明 |
|------|------|------|
| `path` | 条件 | 技能根目录；与 `--file` 二选一，未传 `--file` 时必填 |
| `-f` / `--file` | 条件 | 已有 zip 路径；指定后跳过本地打包 |
| `--token` | 条件 | 用户 Bearer Token；与 `--system-token` 互斥；可由 `OPENJIUWEN_USER_TOKEN` 提供 |
| `--system-token` | 条件 | 系统管理员 Token（`X-System-Token`）；与 `--token` 互斥；可由 `OPENJIUWEN_SYSTEM_TOKEN` 提供 |
| `--market-url` | 条件 | 市场根 URL；未设置时回退环境变量 |
| `--id` | 条件 | 已存在技能 ID；首发一般不传，后续同一技能发版需传 |
| `--version` / `-v` | 是 | 发布版本号（`x.y.z`） |
| `--version-desc` | 否 | 本次版本说明（release notes / changelog） |
| `--force` | 否 | 允许覆盖市场已存在同版本（以服务端策略为准） |

### 6.5 `info` — 查询版本详情

| 参数 | 必填 | 说明 |
|------|------|------|
| `asset_id` | 是 | 技能资产 ID |
| `--version` / `-v` | 是 | 查询的目标版本 |
| `--market-url` | 条件 | 市场根 URL |

### 6.6 `search` — 列表 / 搜索

| 参数 | 必填 | 说明 |
|------|------|------|
| `query` | 否 | 关键词；可省略（表示空关键词） |
| `--market-url` | 条件 | 市场根 URL |
| `--type` | 否 | 仅筛选某一类型：`skill` \| `teamskills` |
| `--author` | 否 | 按作者名筛选 |
| `--asset-id` | 否 | 按资产 ID 筛选 |
| `--asset-type` | 否 | 按资产类型筛选（值由市场后端决定） |
| `--publisher-id` | 否 | 按发布者 ID 筛选 |
| `--page` | 否 | 页码 |
| `--page-size` | 否 | 每页条数（默认 `20`，服务端上限 `200`） |
| `--order-by` | 否 | 排序字段：`install_count` \| `like_count` \| `create_time` \| `update_time` \| `review_count` |
| `--desc` | 否 | 是否降序（默认 `true`，支持 `true/false/yes/no/1/0`） |

### 6.7 `delete` — 删除版本或整包

| 参数 | 必填 | 说明 |
|------|------|------|
| `skill_id` | 是 | 技能资产 ID |
| `--market-url` | 条件 | 市场根 URL |
| `--token` | 条件 | 用户 Bearer；与 `--system-token` 互斥；可由 `OPENJIUWEN_USER_TOKEN` 提供 |
| `--system-token` | 条件 | 系统管理员 Token；与 `--token` 互斥；可由 `OPENJIUWEN_SYSTEM_TOKEN` 提供 |
| `--version` / `-v` | 否 | 指定删除某个版本；省略则删除整包（所有版本） |

### 6.8 `install` — 下载并安装

| 参数 | 必填 | 说明 |
|------|------|------|
| `asset_id` | 是 | 市场资产 ID |
| `--market-url` | 条件 | 市场根 URL |
| `--version` / `-v` | 否 | 指定安装版本；省略通常安装最新版（以服务端返回为准） |
| `-o` / `--output` | 否 | 输出父目录，默认当前工作目录 |
| `--force` | 否 | 目标目录已存在时允许覆盖 |

`skill/teamskills` 安装结果统一为 `<output>/<slug>/`，包含 `SKILL.md` 及其相关资源目录。

### 6.9 `skill-import` — 管理员批量导入集合包

| 参数 | 必填 | 说明 |
|------|------|------|
| `BUNDLE` | 是 | 技能集合包 `.zip` 路径，或符合布局的本地目录 |
| `--market-url` | 条件 | 市场根 URL |
| `--system-token` | 条件 | 系统管理员 Token（`X-System-Token`）；可由 `OPENJIUWEN_SYSTEM_TOKEN` 提供 |
| `--force` | 否 | 强制导入每个条目（以服务端策略为准） |
| `--fail-fast` | 否 | 遇到首个失败条目即停止后续处理 |

`skill-import` 仅支持系统管理员 token，不支持普通用户 Bearer。

### 6.10 市场相关命令示例

```bash
BASE=http://127.0.0.1:8100

jiuwen-teamskills publish ./demo-ts --version 1.0.0 --token <TOKEN> --market-url $BASE
jiuwen-teamskills publish -f ./out/demo-ts.zip --version 1.0.1 --id <ASSET_ID> --token <TOKEN> --market-url $BASE
jiuwen-teamskills info <ASSET_ID> -v 1.0.0 --market-url $BASE
jiuwen-teamskills search "demo" --type teamskills --page-size 20 --order-by create_time --market-url $BASE
jiuwen-teamskills install <ASSET_ID> -o ./installed --market-url $BASE
jiuwen-teamskills delete <ASSET_ID> --version 1.0.0 --token <TOKEN> --market-url $BASE
jiuwen-teamskills skill-import ./bundle.zip --system-token <SYSTEM_TOKEN> --market-url $BASE
```

---

## 7. 目录结构与校验要点

`jiuwen-teamskills` 面向 skill-like 制品，常见结构如下：

```text
demo-skill/
  SKILL.md
  scripts/
  references/
  assets/
```

或 single nested：

```text
demo-root/
  demo-skill/
    SKILL.md
    scripts/
    references/
    assets/
```

补充说明：

- `teamskills` 类型会在 frontmatter 中强调团队技能语义（如 `kind: team-skill` 与 `roles` 约束）。
- `roles/` 内可存在多个角色文档（例如 `roles/<role>/SKILL.md`）；校验关注的是主技能入口 `SKILL.md` 及其 frontmatter 约束。
- 若同时存在 `plugin.yaml`，安装阶段的 `slug` 优先使用 `plugin.yaml name`；否则回退 `SKILL.md` frontmatter 的 `name`。
- 打包与发布前建议先执行 `validate`，便于快速定位 frontmatter 或目录问题。

---

## 8. 开发与测试

```bash
cd skillhub/cli/jiuwen_teamskills
pytest
```

如需在 `cli` 根目录统一跑测试，可按仓库测试约定补充 `PYTHONPATH` 或使用可编辑安装。

---

## 9. 常见问题（FAQ）

**Q：`jiuwen-teamskills` 命令找不到？**  
A：先试 `python -m jiuwen_teamskills.main -h`。若可用，通常是 Python `Scripts` 目录未加入 PATH。

**Q：为什么 `search/info/install/publish/delete` 报 `market-url` 缺失？**  
A：这些命令依赖市场接口。请显式传 `--market-url`，或设置 `JIUWEN_TEAMSKILLS_MARKET_URL` / `OPENJIUWEN_MARKET_URL`。

**Q：`--token` 和 `--system-token` 能一起传吗？**  
A：不能。两者互斥。`skill-import` 仅支持系统管理员 token（`X-System-Token`）。

**Q：`publish --version` 支持 `1.0.0-rc1` 吗？**  
A：不支持。仅接受 `x.y.z` 三段数字版本（可带 `v` 前缀后规范化）。

**Q：路径里有空格时命令失败？**  
A：请给路径加引号，例如：`jiuwen-teamskills validate "D:\My Skills\demo-skill"`。

**Q：`install` 后目录长什么样？怎么确认装对了？**  
A：对 `skill/teamskills`，安装结果统一为 `<output>/<slug>/SKILL.md`，例如：

```text
<output>/
  demo-skill/
    SKILL.md
    scripts/
    references/
    assets/
```

其中 `slug` 优先取 `plugin.yaml name`（若存在），否则取 `SKILL.md` frontmatter `name`。
