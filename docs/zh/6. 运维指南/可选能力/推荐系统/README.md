# 推荐系统

推荐系统是 SkillHub 的可选增强能力，用于在市场首页「全部」与分类页（无搜索关键词时）提供个性化 Skill 排序；并对外暴露 `POST /api/v1/recommend` 等接口。基础部署可以不启用；未启用时列表仍按 `install_count` 等原有字段排序。

部署步骤见[安装指导](../../../3.%20安装指导/本地安装/SkillHub安装指导.md)可选能力「推荐系统」小节；本篇讲配置变量与运维关注点。

## 能力概览

| 场景 | 行为 |
|------|------|
| 已登录且 Redis 有该用户行为序列 | Milvus 向量召回 → MMR 多样性重排（`source=user_history`） |
| 无历史 / 召回失败 | Redis `install_count` TopK 快照兜底（`source=topk_install`） |
| `MARKET_RECOMMENDER_ENABLED=false` | 不走推荐；列表 `order_by=recommend` 自动回退为 `install_count` |
| 有搜索关键词 | 不走推荐，仍走检索 / 关键词逻辑 |

依赖：**MySQL**（行为与资产元数据）、**对象存储**（离线拉包）、**Redis**（用户序列与 TopK 快照）、**Milvus**（向量索引）、**独立 Embedding API**（与检索侧配置分离）。

## 主要配置变量

本表默认值为代码默认值。密钥类变量在配置了 `SERVER_AES_MASTER_KEY` 时须填密文，规则同其他 `MARKET_*` 密钥。

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `MARKET_RECOMMENDER_ENABLED` | 是否启用推荐（路由 + 列表推荐路径 + 离线调度） | `false` |
| `MARKET_REC_LIST_TOP_K` | 首页「全部」一次召回上限，再按 page 切片 | `200` |
| `MARKET_REC_REBUILD_ON_STARTUP` | 启动时是否立即跑 `redis_sync` + `milvus_full` | `true`（`.env.example` 示例常为 `false`） |
| `MARKET_REC_MMR_LAMBDA` | MMR 权重 λ∈[0,1]：越大越偏相关，越小越偏打散；`1.0`≈关闭多样性 | `0.5` |
| `MARKET_REC_EMBEDDING_API_BASE_URL` | 推荐 Embedding API（OpenAI-compatible `/embeddings`） | 空 |
| `MARKET_REC_EMBEDDING_API_KEY` | 推荐 Embedding 密钥（走 `SecurityUtils` 解密） | 空 |
| `MARKET_REC_EMBEDDING_MODEL` | 推荐 Embedding 模型名 | 空 |
| `MARKET_REC_EMBEDDING_BATCH_SIZE` | 离线建索引批大小 | `16` |
| `MILVUS_HOST` / `MILVUS_PORT` | Milvus 地址 | `127.0.0.1` / `19530` |
| `MILVUS_COLLECTION` | 集合名 | `skill_index` |
| `REDIS_TOPK_INSTALL_KEY` | 下载量快照 key | `skill_rec:topk:install` |
| `REDIS_TOPK_K` | `0`=全量按 install 排序写入；`>0` 只保留 TopK | `0` |
| `REDIS_TOPK_TTL_SECONDS` | TopK 快照 TTL（每次同步会覆盖并续期） | `7200` |
| `REDIS_USER_SEQ_KEY_PREFIX` | 用户序列前缀 | `skill_rec:user` |
| `REDIS_USER_SEQ_TTL_SECONDS` | 用户序列 TTL | `7200` |
| `MARKET_REC_PACKAGE_SYNC_CRON` | 拉包同步 cron | `30 * * * *` |
| `MARKET_REC_MILVUS_INCREMENTAL_CRON` | Milvus 增量索引 cron | `0 * * * *` |
| `MARKET_REC_MILVUS_FULL_CRON` | Milvus 全量重建 cron | `0 3 * * *` |
| `MARKET_REC_REDIS_SYNC_CRON` | Redis 快照同步 cron | `15 * * * *` |

> **与检索区分**：推荐必须使用 `MARKET_REC_EMBEDDING_*`，不要复用 `MARKET_RETRIEVAL_EMBEDDING_*`。换模型维度后需 **Milvus full recreate**（`MARKET_REC_REBUILD_ON_STARTUP=true` 或手动 `python -m recommender.offline.milvus_index --mode full`）。

## Redis 写入节奏

- 默认每小时第 15 分执行 `redis_sync`（可改 `MARKET_REC_REDIS_SYNC_CRON`）。
- 写入内容：`topk_install` 快照 + 各用户 `download` / `like` / `star` 序列。
- **覆盖**：每次用新快照 `SET` 同一 key，并重置 TTL；TTL 是同步中断时的过期兜底，不是“只活两小时就永久消失”。

## 离线任务

marketplace 进程内 APScheduler（需 `MARKET_RECOMMENDER_ENABLED=true`）：

| 任务 | 作用 |
|------|------|
| `package_sync` | 从对象存储拉最新 Skill zip 到本地下载目录 |
| `milvus_incremental` / `milvus_full` | 解析 `SKILL.md`（name+description）向量化并 upsert Milvus；full 可重建 schema |
| `redis_sync` | 从 MySQL 聚合行为与 install 排行写入 Redis |

也可在 `marketplace/` 下手动执行（需已 `load_dotenv` 或导出同等环境变量）：

```bash
python -m recommender.offline.package_sync
python -m recommender.offline.milvus_index --mode full   # 或 incremental
python -m recommender.offline.redis_sync
```

## 运维关注点

- Redis / Milvus 网络可达（容器或跨机部署时注意主机名与端口）。
- Embedding API 配额与维度一致性；schema 升级（如新增 `category_id`）后必须 full rebuild。
- 首次启用建议临时打开 `MARKET_REC_REBUILD_ON_STARTUP=true`，确认日志出现 `recommender job: redis_sync done` 与 milvus upsert 成功后再按需改回。
- 用户无 Redis 历史时会落到下载量兜底，属预期行为。

## 相关开发 / API 文档

- [开发指南 / 推荐系统](../../../5.%20开发指南/推荐系统/README.md)
- [API 参考 / 推荐系统 API](../../../7.%20API参考/推荐系统API.md)

## 配置边界

推荐相关变量保留在根目录 `.env.example` 的 `[Recommender]` 段。未启用时可整段保持默认关闭；启用时须配齐 Redis、Milvus 与 `MARKET_REC_EMBEDDING_*`。
