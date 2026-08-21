# 代码逆向 Agent 课题调研

## 交付物

1. `dual-target-source-audit.md`：libuv / Redis 双目标源码结构剖析、证据状态和 Demo 验收矩阵。
2. `libuv-code-reverse-research.md`：基于真实 libuv 快照的专项调研结果。
3. `subagent-design.md`：代码分析专项 SubAgent 课题设计方案。
4. `collect_source_metrics.py`：双目标固定快照、规模和关键符号证据采集器。
5. `collect_libuv_metrics.py`：libuv 轻量分析器基线与专项指标采集器。

## 数据来源

- libuv 调研基准：`../libuv`，commit `e43e3d8a464ddb5ce2ac24f4db780db747691a7b`。
- 编译数据库：`../libuv/build/compile_commands.json`，共 475 个编译单元。
- Redis 目标路径：`../redis`。当前工作区尚无官方 Redis checkout，相关研究假设统一标为 `pending_snapshot`，不计为实测结论。
- 文档中的 libuv 文件与行号均直接取自本地源码树；Redis 补齐后由采集脚本生成同等级证据。

## 快速复现

```bash
cd /Users/andye/Documents/New\ project/code_reverse_agent
python3 research/collect_source_metrics.py
python3 research/collect_libuv_metrics.py
```

两个脚本均不依赖第三方库。双目标脚本不会把“源码缺失”误报为 0；如需在 CI 中强制 Redis 快照存在，执行：

```bash
python3 research/collect_source_metrics.py --require-redis
```

Redis 官方源码放入其他位置时可显式指定：

```bash
python3 research/collect_source_metrics.py --redis /absolute/path/to/redis
```
