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

资源中的 `profile` 是本次快照的领域识别结果：

- `kind` 表示 `libuv`、`redis` 或通用 C/C++ 规则集；
- `status=source_verified` 只表示本地 Git 快照存在且关键符号已命中，不能替代完整编译语义；
- `evidence_basis=repository_snapshot|synthetic_fixture|workspace_source` 必须随结果返回；
- `configuration_separation=true` 表示请求带有构建配置，节点和边已按配置目标做初步平台筛选。

Redis 官方源码未进入分析工作区时，Redis profile 必须返回 `synthetic_validation` 或
`SOURCE_UNAVAILABLE`，不能返回 `source_verified`。

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

Demo 会实际执行 `scope`：`configuration_ids` 先过滤边，`edge_types` 再限定关系种类，
`direction` 决定正向/反向邻接，`max_hops` 限制路径长度。`include_source=false` 时不返回
`citations`，但仍返回路径和不确定性。路径中的每个 `edge_id` 都能在同一分析图中复现，且不会
包含悬空节点。

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

## 7. SubAgent 内部工具协议

公开 `/v1/*` HTTP API 负责创建分析、读取图和提交自然语言问题。SubAgent 推理层不直接读取数据库或
任意源码路径，而是通过受控工具协议取证：

- 调用 Schema：`contracts/tool.invoke.schema.json`
- 返回 Schema：`contracts/tool.result.schema.json`
- 完整示例：`contracts/examples/tool-trace-async-chain.invoke.json` 与
  `contracts/examples/tool-trace-async-chain.result.json`
- OpenAPI 通过 `x-subagent-tool-protocol` 声明工具注册表；该扩展不是公开 HTTP endpoint。

### 7.1 调用信封

```json
{
  "schema_version": "1.0",
  "tool_call_id": "tc_libuv_read_chain_01",
  "analysis_id": "an_01JLIBUVLINUXDEBUG",
  "tool_name": "trace_async_chain",
  "arguments": {
    "start_node_id": "n_uv_read_start",
    "configuration_ids": ["linux-debug"],
    "max_hops": 6
  }
}
```

`tool_name` 与 `arguments` 使用判别联合：选择某个工具后，参数必须满足该工具的独立必填字段和
`additionalProperties=false` 约束，不能把另一工具的参数透传进去。

| 工具 | 必要定位参数 | 结果类型 | 关键约束 |
| --- | --- | --- | --- |
| `find_symbol` | `query` | `symbols` | 可按节点类型、配置和匹配方式过滤 |
| `get_call_edges` | `node_id` | `edges` | 区分入边、出边和最低置信度 |
| `trace_async_chain` | `start_node_id` | `paths` | 路径分 registration/scheduling/execution 三阶段 |
| `resolve_pointer` | `callsite` | `pointer_candidates` | 文件行号或 source node 二选一，可返回多目标 |
| `read_slice` | node、edge 或 file:line 定位之一 | `source_slices` | 只能读取当前分析快照内的源码 |
| `query_configuration` | 可空过滤条件 | `configurations` | 返回配置来源、激活状态和变体边 |
| `report_uncertainty` | 可空作用域 | `uncertainties` | 统一机器码、严重级别与建议动作 |

### 7.2 返回信封

```json
{
  "resource_type": "tool_result",
  "schema_version": "1.0",
  "tool_call_id": "tc_libuv_read_chain_01",
  "analysis_id": "an_01JLIBUVLINUXDEBUG",
  "tool_name": "trace_async_chain",
  "ok": true,
  "result": {"result_type": "paths", "items": []},
  "evidence": [],
  "pagination": {"next_cursor": null, "has_more": false}
}
```

失败结果将 `result` 替换为 `error`，二者互斥。执行器必须原样回显 `tool_call_id`、`analysis_id` 和
`tool_name`，防止并发工具结果串线。`evidence` 始终存在；没有证据时返回空数组，不能省略，也不能让
模型生成。分页游标只由执行器解释，SubAgent 不应拼接或解码游标。

### 7.3 安全与可追溯规则

1. 所有 node、edge、configuration 必须属于调用中的 `analysis_id`。
2. `read_slice` 只能解析分析快照内的相对路径，拒绝越界路径和任意主机文件。
3. 配置过滤必须在图检索前执行；不同平台目标不能合并成一条 observed 边。
4. `trace_async_chain` 不得把 `unresolved` 目标提升为 observed；路径置信度不得高于最弱关键边。
5. 每项关键结论都应能通过返回的 `evidence` 定位；纯检索无匹配时允许空结果和空证据。

## 8. 契约验证

项目测试会检查全部本地 `$ref`、示例与 Schema 的一致性、工具注册表与分型分支同步，以及 Demo 的
analysis、graph、query 返回是否满足规范。运行：

```bash
PYTHONPATH=code_reverse_agent python3 -m unittest discover -s code_reverse_agent/tests -v
```

### 8.1 Demo 覆盖矩阵

机器契约同时描述可演进的生产边界和当前轻量 Demo。Demo 已验证同步分析、Evidence IR 图返回、领域边
保真、带引用问答和七工具运行时；以下能力仍需生产实现：

| 契约能力 | 当前 Demo | 生产实现要求 |
| --- | --- | --- |
| `request_id` 幂等 | 未实现 | 有效期内复用同一任务 |
| 异步状态与取消 | 同步返回 `completed` | worker 状态机、取消与重试 |
| `configuration_id` | 配置作为边元数据返回，未执行真实多配置分析 | 过滤前先构建独立配置图 |
| HTTP graph cursor 分页 | 小结果集截断展示 | 稳定游标、`has_more` 与 `next_cursor` |
| query `scope` / `paths` | 规则问答，`paths` 为空 | 按方向、跳数、边类型和配置生成可复现路径 |
| 七个 SubAgent 工具 | 可信内存执行器、分析隔离、受控源码切片、工具级稳定游标 | 持久化图后端、鉴权、配额和审计日志 |

因此 Demo 结果中的 `limitations` 必须保留；生产验收不能只运行 Demo 测试，还要用 libuv/Redis 固定 commit
的黄金问题集评测召回率、证据准确率和虚报率。
