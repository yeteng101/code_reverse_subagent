# 代码逆向 Agent Demo

这是一个离线可运行的基础 demo，完成了从 C/C++ 源码到函数关系图和自然语言解释的最小闭环：

- 识别 C/C++ 函数、lambda、模块和入口函数
- 提取直接调用关系
- 识别常见的异步调度和回调参数
- 识别局部函数指针赋值与调用
- 提取 `#define` 与条件编译标记
- 为关系保存源码文件、行号、证据和置信度
- 通过本地 Web 工作台查看图谱并用中文提问

面向 `libuv` / `redis` 的架构设计和统一 Evidence IR 说明见 [`docs/libuv-redis-architecture.md`](docs/libuv-redis-architecture.md)。接口字段语义见 [`docs/api-json-spec.md`](docs/api-json-spec.md)，版本化接口契约位于 [`contracts/openapi.json`](contracts/openapi.json)，各请求/响应 JSON Schema 位于 `contracts/*.schema.json`。

当前分析器使用标准库正则和轻量词法屏蔽，目的是让 demo 不需要安装 Clang 或 API Key。它不是完整 C++ 编译器；生产版本应将 `analyzer.py` 的解析层替换成 Clang AST，同时保留现在的 IR、证据和 UI 接口。

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

## 验证

```bash
PYTHONPATH=code_reverse_agent python3 -m unittest discover -s code_reverse_agent/tests -v
```
