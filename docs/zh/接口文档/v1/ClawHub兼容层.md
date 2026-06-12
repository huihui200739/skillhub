# OpenAPI （ClawHub 兼容层）

下列说明与下方 **OpenAPI 3.1 YAML** 一致，便于导入 Swagger / codegen。

## 范围说明

| 类别 | 路径前缀 | 关键业务价值 |
|------|----------|-------------|
| ClawHub CLI 兼容层 | `/api/v1` 下的 `/search`、`/skills`、`/download`、`/resolve` 等 | **协议适配**<br>• 供 ClawHub CLI 及生态工具无缝接入<br>• 搜索、浏览、版本查询与 zip 下载一站式覆盖<br>• 可由环境变量 `MARKET_CLAWHUB_COMPAT_ENABLED` 关闭 |

### 全局约束

- 兼容层接口的 **成功响应体为裸 JSON**（不经 `code` / `message` / `data` 统一包装），与 Skill 市场原生 `ResponseModel` 不同
- 路由层 **未** 校验 Bearer / 系统令牌（部署侧应通过网络策略或网关保护）
- 列表作用域受 `MARKET_CLAWHUB_PLUGIN_TYPE`（或 `CLAWHUB_PLUGIN_TYPE`，默认 `skill`）影响

---

## 接口规范文档

下面 **接口总览表** 便于快速检索；字段级定义与示例仍以紧随其后的 **OpenAPI YAML** 为准。

### 接口总览

| 方法 | 路径 | 鉴权 | 摘要 |
|------|------|------|------|
| GET | `/api/v1/search` | **无需** | 搜索，`q`、`limit` [#CLI兼容] |
| GET | `/api/v1/skills` | **无需** | 列表/探索，`limit`、`sort` [#CLI兼容] |
| GET | `/api/v1/skills/{slug}` | **无需** | Skill 元信息 [#CLI兼容] |
| GET | `/api/v1/skills/{slug}/versions` | **无需** | 版本列表 [#CLI兼容] |
| GET | `/api/v1/skills/{slug}/versions/{version}` | **无需** | 指定版本详情（含 zip 文本文件清单） [#CLI兼容] |
| GET | `/api/v1/skills/{slug}/file` | **无需** | 读取 zip 内某路径文件（文本） [#CLI兼容] |
| GET | `/api/v1/download` | **无需** | 流式下载 zip [#CLI兼容] |
| GET | `/api/v1/resolve` | **无需** | 指纹解析版本；查询参数 **`hash`** [#CLI兼容] |

---

### OpenAPI YAML

```yaml
openapi: 3.1.0
info:
  title: ClawHub 兼容层 API
  description: |
    ClawHub CLI 兼容层 API。
    接口分类、环境变量与鉴权说明请参阅本文档「范围说明」章节。
  version: 1.0.0
servers:
  - url: http://localhost:8100
    description: 本地开发环境
  - url: https://market.example.com
    description: 生产环境
paths:
  /api/v1/search:
    get:
      summary: "[ClawHub 兼容] 搜索"
      description: 映射至市场列表接口；仅暴露 `plugin_type` 为适配层配置的类型（默认 skill）。可通过 `MARKET_CLAWHUB_COMPAT_ENABLED=false` 关闭整组兼容路由。
      operationId: clawhubSearch
      tags:
        - ClawHub 兼容
      parameters:
        - name: q
          in: query
          required: true
          schema:
            type: string
        - name: limit
          in: query
          required: false
          schema:
            type: integer
            minimum: 1
            maximum: 100
      responses:
        '200':
          description: 搜索结果
          content:
            application/json:
              schema:
                type: object
                properties:
                  results:
                    type: array
                    items:
                      $ref: '#/components/schemas/ClawhubSearchResult'

  /api/v1/skills:
    get:
      summary: "[ClawHub 兼容] 探索列表"
      operationId: clawhubExplore
      tags:
        - ClawHub 兼容
      parameters:
        - name: limit
          in: query
          schema:
            type: integer
            default: 50
        - name: sort
          in: query
          required: false
          description: 如 updated、downloads、stars 等（映射至市场排序字段）
          schema:
            type: string
      responses:
        '200':
          description: 探索列表
          content:
            application/json:
              schema:
                type: object
                properties:
                  items:
                    type: array
                    items:
                      $ref: '#/components/schemas/ClawhubExploreItem'
                  nextCursor:
                    type: string
                    nullable: true

  /api/v1/skills/{slug}:
    get:
      summary: "[ClawHub 兼容] Skill 详情（最新版本维度）"
      operationId: clawhubSkillMeta
      tags:
        - ClawHub 兼容
      parameters:
        - name: slug
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: skill + latestVersion + owner + moderation 等结构
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ClawhubSkillDetail'
        '404':
          description: 未找到或无已发布版本

  /api/v1/skills/{slug}/versions:
    get:
      summary: "[ClawHub 兼容] 版本列表"
      operationId: clawhubSkillVersions
      tags:
        - ClawHub 兼容
      parameters:
        - name: slug
          in: path
          required: true
          schema:
            type: string
        - name: limit
          in: query
          schema:
            type: integer
            default: 50
      responses:
        '200':
          description: 版本列表
          content:
            application/json:
              schema:
                type: object
                properties:
                  items:
                    type: array
                    items:
                      $ref: '#/components/schemas/ClawhubVersionListItem'
                  nextCursor:
                    type: string
                    nullable: true

  /api/v1/skills/{slug}/versions/{version}:
    get:
      summary: "[ClawHub 兼容] 指定版本详情（含 zip 内文本文件列表）"
      operationId: clawhubSkillVersionDetail
      tags:
        - ClawHub 兼容
      parameters:
        - name: slug
          in: path
          required: true
          schema:
            type: string
        - name: version
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: 版本信息 + files[]（path/sha256/size）
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ClawhubSkillVersionDetail'
        '404':
          description: 未找到

  /api/v1/skills/{slug}/file:
    get:
      summary: "[ClawHub 兼容] 读取 zip 内单个文件（纯文本）"
      operationId: clawhubSkillFile
      tags:
        - ClawHub 兼容
      parameters:
        - name: slug
          in: path
          required: true
          schema:
            type: string
        - name: path
          in: query
          required: true
          description: zip 内相对路径
          schema:
            type: string
        - name: version
          in: query
          required: false
          schema:
            type: string
      responses:
        '200':
          description: text/plain
          content:
            text/plain:
              schema:
                type: string
        '400':
          description: 非法路径
        '404':
          description: 文件不存在
        '413':
          description: 单文件过大（超过 10MB）

  /api/v1/download:
    get:
      summary: "[ClawHub 兼容] 流式下载 zip"
      description: 内部先解析预签名 URL，再以流式响应返回 application/zip。
      operationId: clawhubDownload
      tags:
        - ClawHub 兼容
      parameters:
        - name: slug
          in: query
          required: true
          schema:
            type: string
        - name: version
          in: query
          required: false
          schema:
            type: string
      responses:
        '200':
          description: application/zip 流
          content:
            application/zip:
              schema:
                type: string
                format: binary
        '4xx':
          description: 与下载/存储相关错误

  /api/v1/resolve:
    get:
      summary: "[ClawHub 兼容] 按文本指纹解析版本"
      description: |
        查询参数名 **`hash`**（非 fingerprint）：服务端对若干历史版本下载 zip 计算与 ClawHub 一致的指纹，匹配则返回对应版本。
        若大量版本拉取失败，可能返回 502。
      operationId: clawhubResolve
      tags:
        - ClawHub 兼容
      parameters:
        - name: slug
          in: query
          required: true
          schema:
            type: string
        - name: hash
          in: query
          required: true
          schema:
            type: string
      responses:
        '200':
          description: 指纹解析结果
          content:
            application/json:
              schema:
                type: object
                properties:
                  match:
                    type: object
                    nullable: true
                    properties:
                      version:
                        type: string
                  latestVersion:
                    type: object
                    nullable: true
                    properties:
                      version:
                        type: string
        '502':
          description: 上游 artifact 失败比例过高

components:
  schemas:
    ClawhubSearchResult:
      type: object
      properties:
        slug:
          type: string
        displayName:
          type: string
        summary:
          type: string
          nullable: true
        version:
          type: string
          nullable: true
        score:
          type: number
        updatedAt:
          type: integer
          nullable: true

    ClawhubExploreItem:
      type: object
      properties:
        slug:
          type: string
        displayName:
          type: string
        summary:
          type: string
          nullable: true
        tags:
          type: object
          properties:
            latest:
              type: string
        stats:
          type: object
          properties:
            installsAllTime:
              type: integer
            stars:
              type: integer
        createdAt:
          type: integer
        updatedAt:
          type: integer
        latestVersion:
          type: object
          nullable: true
          properties:
            version:
              type: string
            createdAt:
              type: integer
            changelog:
              type: string
            license:
              type: string
              nullable: true

    ClawhubSkillDetail:
      type: object
      properties:
        skill:
          type: object
          properties:
            slug:
              type: string
            displayName:
              type: string
            summary:
              type: string
              nullable: true
            tags:
              type: object
              properties:
                latest:
                  type: string
            stats:
              type: object
              properties:
                installsAllTime:
                  type: integer
                stars:
                  type: integer
            createdAt:
              type: integer
            updatedAt:
              type: integer
        latestVersion:
          type: object
          properties:
            version:
              type: string
            createdAt:
              type: integer
            changelog:
              type: string
            license:
              type: string
              nullable: true
        owner:
          type: object
          properties:
            handle:
              type: string
            displayName:
              type: string
              nullable: true
            image:
              type: string
              nullable: true
        moderation:
          type: object
          properties:
            isSuspicious:
              type: boolean
            isMalwareBlocked:
              type: boolean
            verdict:
              type: string
            reasonCodes:
              type: array
              items:
                type: string
            updatedAt:
              type: string
              nullable: true
            engineVersion:
              type: string
              nullable: true
            summary:
              type: string
              nullable: true

    ClawhubVersionListItem:
      type: object
      properties:
        version:
          type: string
        createdAt:
          type: integer
        changelog:
          type: string
        changelogSource:
          type: string

    ClawhubSkillVersionDetail:
      type: object
      properties:
        version:
          type: object
          properties:
            version:
              type: string
            createdAt:
              type: integer
            changelog:
              type: string
            changelogSource:
              type: string
            license:
              type: string
              nullable: true
            files:
              type: array
              items:
                type: object
                properties:
                  path:
                    type: string
                  sha256:
                    type: string
                  size:
                    type: integer
        skill:
          type: object
          properties:
            slug:
              type: string
            displayName:
              type: string
```
