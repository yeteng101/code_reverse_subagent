# 接口 JSON 规范

规范版本：`1.0`。机器可读定义以 [`contracts/openapi.json`](../contracts/openapi.json) 和同目录 JSON Schema 为准；本文解释资源语义和兼容规则。

## 1. 通用约定

- Content-Type：`application/json; charset=utf-8`
- ID 使用资源前缀：分析 `an_`、节点 `n_`、边 `e_`、查询 `q_`
- 时间统一使用 ISO 8601 UTC，例如 `2026-08-18T09:30:00Z`
- 未识别或推断关系不能省略，使用 `resolution=unresolved|inferred` 表示
- 服务端可以增加新字段；客户端必须忽略未知字段
- 已发布字段不改名、不改变类型；变更字段语义时增加 `schema_version`

## 2. 创建分析

`POST /v1/analyses`

请求：`contracts/analysis.create.schema.json`

最小请求：

```json
{
  "repository": {
    "name": "libuv",
    "kind": "libuv",
    "path": "targets/libuv"
  }
}
```

关键规则：

| 字段 | 规则 |
| --- | --- |
| `repository.kind` | `libuv`、`redis`、`custom`；决定 domain profile，不代表部署依赖 |
| `repository.path` | 服务端工作区内路径，不接受工作区外路径 |
| `repository.url` | 可选的仓库来源，仅作元数据；下载/检出由受控 worker 完成 |
| `build.configurations` | 一次请求可提供多组编译配置，关系按配置标记 |
| `analysis.profiles` | 建议目标仓库同时包含 `generic_c_cpp` 和对应专用 profile |
| `request_id` | 客户端幂等键，服务端应在有效期内复用任务 |

返回 `202 Accepted`，响应头 `Location: /v1/analyses/{analysis_id}`。Demo 会同步分析并返回 `status=completed`，生产 worker 则先返回 `queued`。

## 3. 分析资源

`GET /v1/analyses/{analysis_id}`

响应：`contracts/analysis.resource.schema.json`

`status` 只能按以下状态迁移：

```text
queued -> preparing -> parsing -> indexing -> completed
                                           └-> failed
queued/preparing/parsing/indexing --------> cancelled
```

失败时保留已经完成的 `summary` 和 `limitations`，并在 `error` 字段返回机器可读的错误码。

## 4. 图查询

`GET /v1/analyses/{analysis_id}/graph`

查询参数：

| 参数 | 说明 |
| --- | --- |
| `configuration_id` | 只返回指定构建配置出现的关系 |
| `node_kind` | 过滤 `function`、`event_loop`、`callback` 等节点 |
| `edge_type` | 过滤 `direct`、`callback`、`async`、`registers_callback` 等边 |
| `limit` | 1-5000，默认 500 |
| `cursor` | 分页游标，服务端不得把游标解释成源码内容 |

图边的最小证据字段：

```json
{
  "edge_id": "e_123",
  "source": "n_uv_read_start",
  "target": "n_read_cb",
  "type": "registers_callback",
  "semantics": "registration",
  "resolution": "observed",
  "confidence": 0.97,
  "evidence": [{
    "kind": "source",
    "location": {"file": "src/uv-common.c", "start_line": 210},
    "text": "stream->read_cb = cb;"
  }],
  "configurations": ["linux-debug"]
}
```

`confidence` 是 0 到 1 的数值，不能用字符串或百分号替代。`evidence` 至少一条，且必须能定位到源码、AST、宏、构建或运行时事实。

## 5. 自然语言查询

`POST /v1/queries`

请求：`contracts/query.request.schema.json`

```json
{
  "analysis_id": "an_01JLIBUVLINUXDEBUG",
  "question": "uv_read_start 到用户回调经过哪些异步步骤？",
  "scope": {
    "configuration_ids": ["linux-debug"],
    "edge_types": ["registers_callback", "scheduled_by", "invokes_callback"],
    "direction": "forward",
    "max_hops": 6
  },
  "response_format": "both",
  "include_source": true
}
```

响应：`contracts/query.response.schema.json`

- `answer` 是面向人的解释，不作为机器关系来源。
- `focus` 是 UI 应高亮的节点。
- `paths` 是可复现的节点/边路径。
- `citations` 是回答实际引用的源码证据。
- `uncertainty.items` 必须列出未解析调用、配置缺失或低置信度原因。

## 6. 错误

错误响应使用 `contracts/error.schema.json`：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "repository.path 不能为空",
  "retryable": false,
  "trace_id": "tr_abc123"
}
```

建议错误码：

```text
INVALID_ARGUMENT   请求 JSON 或字段不合法
NOT_FOUND          分析、节点或源码证据不存在
SOURCE_UNAVAILABLE 无法读取仓库或构建产物
ANALYSIS_FAILED    解析任务失败，可查看 details
RATE_LIMITED       超过分析或查询配额
INTERNAL_ERROR     服务端未分类错误
```

