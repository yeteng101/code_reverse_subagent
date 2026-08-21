# 代码逆向 Agent Demo

这是一个离线可运行的基础 demo，完成了从 C/C++ 源码到函数关系图和自然语言解释的最小闭环：

- 识别 C/C++ 函数、lambda、模块和入口函数
- 提取直接调用关系
- 识别常见的异步调度和回调参数
- 用领域规则区分 `registers_callback`、`scheduled_by`、`invokes_callback`
- 识别局部函数指针赋值与调用
- 提取单行/多行 `#define` 与条件编译标记
- 为关系保存源码文件、行号、证据和置信度
- 输出 `profile.kind/status/evidence_basis`，区分真实仓证据与合成验证
- 按统一信封执行 7 个 SubAgent 证据工具，并隔离 analysis / node / edge / source path
- 按构建配置生成稳定分析身份，图和查询支持配置过滤、方向、边类型与跳数约束
- 通过本地 Web 工作台查看图谱并用中文提问

面向 `libuv` / `redis` 的架构设计和统一 Evidence IR 说明见 [`docs/libuv-redis-architecture.md`](docs/libuv-redis-architecture.md)。接口字段语义见 [`docs/api-json-spec.md`](docs/api-json-spec.md)，版本化接口契约位于 [`contracts/openapi.json`](contracts/openapi.json)，各请求/响应 JSON Schema 位于 `contracts/*.schema.json`。

课题调研、libuv / Redis 双目标源码剖析、SubAgent 设计方案和可复现采集脚本位于 [`research/`](research/README.md)。Redis 源码快照缺失时，研究材料会明确标为 `pending_snapshot`，不会把假设写成实测证据。

当前分析器使用标准库正则和轻量词法屏蔽，目的是让 demo 不需要安装 Clang 或 API Key。它不是完整 C++ 编译器；生产版本应将 `analyzer.py` 的解析层替换成 Clang AST，同时保留现在的 IR、证据和 UI 接口。构建配置存在时，Demo 会依据配置目标和 `unix/win` 源码目录做初步适用性筛选；这不是编译器级预处理结果，仍需保留不确定性。

## 运行

在项目根目录执行：

```bash
python3 code_reverse_agent/server.py
```

然后打开 <http://127.0.0.1:8765>。默认会加载 `code_reverse_agent/samples/async_pipeline.cpp`，也可以在左侧输入工作区内的文件或目录，例如：

```text
selected_children.cpp
code_reverse_agent/samples
```

不需要安装 `requirements.txt` 中的 OpenAI 依赖；这个 demo 的查询解释器是离线规则实现。

## API

```text
GET  /api/demo
POST /api/analyze  {"path": "selected_children.cpp"}
POST /api/query    {"analysis_id": "...", "question": "有哪些异步回调？"}
```

### Versioned API

`/v1` 接口是后续生产实现的稳定契约，旧 `/api/*` 仅用于保持 demo 兼容：

```text
POST /v1/analyses
GET  /v1/analyses/{analysis_id}
GET  /v1/analyses/{analysis_id}/graph?edge_type=callback
POST /v1/queries
GET  /v1/health
```

推荐从以下示例开始：

```text
contracts/examples/create-libuv-analysis.json
contracts/examples/create-redis-analysis.json
contracts/examples/query-async-chain.json
```

统一图边必须带 `resolution`、`confidence`、`evidence` 和 `configurations`；自然语言查询必须返回 `citations` 和 `uncertainty`。

### SubAgent 工具运行时

`subagent_tools.py` 实现了 `contracts/tool.invoke.schema.json` 声明的 7 个内部工具。调用方先通过
`DemoState.create_api_analysis()` 创建分析，再执行：

```python
from subagent_tools import invoke_tool

result = invoke_tool(state, {
    "schema_version": "1.0",
    "tool_call_id": "tc_trace_1",
    "analysis_id": analysis_id,
    "tool_name": "trace_async_chain",
    "arguments": {"start_node_id": node_id, "max_hops": 6},
})
```

运行时只允许读取当前 `analysis_id` 的节点、边、配置和源码文件；越界路径、外部 ID、错误游标和未知参数均返回结构化失败信封。

## 验证

```bash
PYTHONPATH=code_reverse_agent python3 -m unittest discover -s code_reverse_agent/tests -v
```

一键运行 libuv / Redis 领域验证：

```bash
PYTHONPATH=code_reverse_agent python3 code_reverse_agent/verify_targets.py \
  --libuv libuv --redis redis \
  --output code_reverse_agent/validation-report.json
```

若工作区没有 `redis/`，脚本会自动使用 `samples/redis_event_loop.c`，并把结果明确标为
`status=synthetic_validation`、`evidence_basis=synthetic_fixture`；这只能验证领域规则，不能替代
真实 Redis 仓库穿刺。libuv 仓库验证则标为 `repository_snapshot` 并记录 Git revision。

## Demo 能力边界

| 能力 | 当前状态 | 验证方式 |
| --- | --- | --- |
| libuv 源码结构与事件循环关键链 | 已用固定源码快照验证 | `validation-report.json` |
| Redis `ae` 事件/时间回调规则 | 合成样例验证 | `samples/redis_event_loop.c` |
| Redis 全仓源码结构与真实行号 | 等待官方源码快照 | `research/collect_source_metrics.py --require-redis` |
| 注册 / 调度 / 执行语义边 | Demo 已实现 | 领域测试与图 API |
| 7 个 SubAgent 工具 JSON 协议与运行时 | 已实现并通过契约 / 安全测试 | `subagent_tools.py`、`contracts/tool.*.schema.json` |
| 多配置 Clang AST、HTTP 图分页、幂等任务 | 生产版待实现 | 见架构文档覆盖矩阵 |
