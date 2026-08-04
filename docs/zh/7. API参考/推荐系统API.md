# 推荐系统 API

对外仅暴露 **个性化推荐** 接口。

- 路径：`POST /api/v1/recommend`
- 开关：需 `MARKET_RECOMMENDER_ENABLED=true`，否则 `503`
- 鉴权：当前**不强制** Bearer；`user_id` 由调用方传入（网关另有鉴权时按部署要求附加）

市场 Web 列表侧的推荐（`GET /api/v1/plugins?order_by=recommend`）见 [TeamSkillsHub 接口参考](./TeamSkillsHub-接口参考.md)。

---

## 召回顺序

1. `user_id` 非空，且 Redis 有该用户 download / like / star 序列 → Milvus 相似召回 → MMR → `source=user_history`
2. 否则（空用户、无历史、召回失败）→ Redis 下载量快照兜底 → `source=topk_install`

可选 `category_id` 时，Milvus / TopK 均按该类目过滤。

---

## `POST /api/v1/recommend`

### 请求体

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `user_id` | string | 否 | `""` | 用户 ID；空则冷启动，走下载量兜底 |
| `request_id` | string | 否 | `""` | 调用方请求 ID，响应原样回显 |
| `timestamp` | number \| null | 否 | `null` | 客户端时间戳，仅写日志 |
| `top_k` | int | 否 | `10` | 返回条数，范围 1–500 |
| `category_id` | string | 否 | `""` | 根类目 ID，如 `lifestyle-health`、`software-development`；空=不限 |

### 响应

外层为统一 `ResponseModel`：

| 字段 | 说明 |
|------|------|
| `code` | 业务码，成功为 `200` |
| `message` | 如 `ok` |
| `data` | 见下表 |

`data`：

| 字段 | 类型 | 说明 |
|------|------|------|
| `request_id` | string | 回显 |
| `user_id` | string | 回显 |
| `source` | string | `user_history`：个性化；`topk_install`：下载量兜底 |
| `category_id` | string | 请求类目回显 |
| `items` | array | 有序列表，元素为 `{ "asset_id": string, "score": number }` |

### 错误

| HTTP | 说明 |
|------|------|
| `422` | 请求体 JSON / 字段校验失败 |
| `503` | 推荐未启用（`MARKET_RECOMMENDER_ENABLED` 非 true） |
| `500` | 服务内部错误（如 Milvus/Embedding 异常且未正确兜底） |

---

## 调用示例

### Python

```python
import json
import urllib.request

BASE = "http://127.0.0.1:8100"


def recommend(
    *,
    user_id: str,
    top_k: int = 10,
    request_id: str = "",
    category_id: str = "",
    timestamp: int | None = None,
) -> dict:
    body = {
        "user_id": user_id,
        "request_id": request_id,
        "top_k": top_k,
        "category_id": category_id,
        "timestamp": timestamp,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/api/v1/recommend",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


# 个性化
print(json.dumps(
    recommend(
        user_id="682df4f3ddc3c54994a92b1b",
        request_id="demo-1",
        top_k=10,
    ),
    ensure_ascii=False,
    indent=2,
))

# 限定类目
print(json.dumps(
    recommend(
        user_id="682df4f3ddc3c54994a92b1b",
        request_id="demo-health",
        top_k=10,
        category_id="lifestyle-health",
    ),
    ensure_ascii=False,
    indent=2,
))

# 冷启动
print(json.dumps(
    recommend(user_id="", request_id="demo-cold", top_k=10),
    ensure_ascii=False,
    indent=2,
))
```

### curl（bash / Git Bash）

```bash
curl -sS -X POST "http://127.0.0.1:8100/api/v1/recommend" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "<your-user-id>",
    "request_id": "demo-1",
    "timestamp": 1710000000,
    "top_k": 10,
    "category_id": "lifestyle-health"
  }'
```

### 成功响应示例

```json
{
  "code": 200,
  "message": "ok",
  "data": {
    "request_id": "demo-1",
    "user_id": "<your-user-id>",
    "source": "user_history",
    "category_id": "lifestyle-health",
    "items": [
      { "asset_id": "3e53729978e84a5aafe210742fa31c82", "score": 0.7637 }
    ]
  }
}
```

---

## 相关文档

- [运维指南 / 推荐系统](../6.%20运维指南/可选能力/推荐系统/README.md)（配置与依赖）
- [开发指南 / 推荐系统](../5.%20开发指南/推荐系统/README.md)（链路说明）
