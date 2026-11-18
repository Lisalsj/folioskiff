# 插件协议 1.x 冻结契约

本文冻结 `paper-reader` 插件 manifest、权限声明、五类能力方法和 JSON Lines 信封。开发指南见[插件 SDK](plugin-sdk.md)，进程与权限架构见[插件协议](plugins.md)。

## 1. 当前冻结事实

| 事实 | 冻结值 |
|---|---|
| 宿主协议 | `1.0` |
| 支持的 major | `1` |
| 契约语义 SHA-256 | `520cdcc36b7e5bcede23a9b6ae891ec9e7896ca41020f628fae76b757973bae5` |
| Manifest 版本 | `1` |
| 编码与分帧 | UTF-8、每行一个 JSON object、单个 LF 结尾 |
| 旧插件夹具 | `examples/plugins/fixture-source`，协议范围 `1.0–1.0` |

哈希覆盖 `PluginManifest`、协议范围、能力种类、权限种类与资源、`PluginRequest`、`PluginEvent`、`PluginResponse`、结构化 `PluginError`，以及五类能力允许的方法。Pydantic 模型全部 `extra="forbid"` 且冻结；规范 JSON 不允许 NaN、Infinity、BOM、CRLF 或原始控制字符。

## 2. 五类能力与方法

| 能力 | 1.x 允许的方法 |
|---|---|
| `source_connector` | `estimate`、`fetch_page`、`probe` |
| `metadata_enricher` | `enrich`、`probe` |
| `score_dimension` | `evaluate`、`probe` |
| `agent_backend` | `generate`、`models`、`probe` |
| `publisher` | `publish`、`probe` |

插件只能声明表内方法。新增方法、能力种类、权限种类或改变现有 payload/result 语义至少需要新的协议 minor，并且必须通过范围协商保持旧宿主可拒绝；删除、重命名、收紧或改变既有语义需要新的 major。

## 3. 信封与错误边界

请求固定包含 `protocol_version`、规范 UUID `request_id`、`capability`、`method`、关闭式 `payload` 和带时区 `deadline`。事件与最终响应复用协议版本和 request ID，并使用从 1 开始、连续递增的 `sequence`。一次调用必须恰好有一个最终响应，之后不得再输出。

失败响应必须包含关闭式错误对象：稳定小写 `code`、无控制字符的有限 `message`、`retryable` 和可选 JSON `details`；成功响应必须包含 result 且不得同时包含 error。stderr、异常堆栈、主机路径、凭据和完整上游响应不属于协议数据。

协议错误按关闭失败处理：版本不匹配、未知 major、错 request ID、断裂 sequence、重复终态、超时、大小超限或无效 UTF-8 都只禁用/计错目标插件，不能降级为不受控 subprocess，也不能影响其他插件和内置能力。

## 4. 兼容性与权限规则

- 宿主只接受 major 1 且 manifest 范围实际包含宿主版本的插件；`2.x` 等未知 major 必须拒绝。
- 协议 minor 不是“忽略未知字段”开关。既有关闭式信封和 manifest 字段在 1.x 内保持不变。
- 新 minor 若增加可选能力，旧插件无需声明；新插件必须把 minimum 设置到真正需要的版本，不能伪报 `1.0`。
- 网络、文件和凭据权限必须逐 capability 声明精确抽象资源；默认无权限，用户授权绑定插件版本与 manifest hash。
- entry point、distribution version 或 manifest hash 改变后旧启用状态和授权失效。
- AgentBackend、ScoreDimension 与 Publisher 不能借插件协议改写论文评分、用户反馈或领域数据库。

## 5. 发布门槛

`release-manifest.json` 和 `packaging/release-layout.json` 都必须记录 `plugin_protocol_version = "1.0"`。冻结测试会重新计算契约哈希、运行旧 1.0 来源夹具，并验证未知 major 拒绝；worker、host、catalog、broker、AppContainer 与 Job Object 测试仍须全部通过。

任何协议变更都必须先保留失败用例，再更新实现、示例插件、SDK 文档和兼容矩阵。不能通过扩大允许字段、吞掉未知 major 或绕过隔离来维持表面兼容。
