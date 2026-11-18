# 插件 SDK 开发指南

本文面向准备为 `paper-reader` 编写 Python 插件的开发者。协议事实与安全边界以[插件协议](plugins.md)、[插件协议 1.x 冻结契约](plugin-protocol-1.x.md)和代码中的关闭式 Pydantic schema 为准；本文给出可执行的最小开发路径。当前宿主版本为 `3.0.0`，manifest 版本为 `1`，JSONL 协议为 `1.0`。

> v2.0 只在 Windows 11 x64 上运行第三方插件，并要求 AppContainer 与 Job Object 隔离可用。当前没有插件市场、自动安装器或兼容性后备进程；插件 distribution 由开发者放入专用运行环境后，用户还必须在设置页重新检查、启用并逐项授权。`examples/plugins/fixture-source` 只是离线契约夹具，不是真实论文来源，也不会自动出现在内置来源列表。

## 1. 最小 distribution

一个插件是独立 Python distribution，通过 `paper_reader.plugins` entry point 暴露一个 `PluginApplication`：

```toml
[project]
name = "my-paper-reader-plugin"
version = "1.0.0"
requires-python = ">=3.12,<3.14"
dependencies = ["paper-reader>=0.6,<3"]

[project.entry-points."paper_reader.plugins"]
"example.source" = "my_plugin:plugin"
```

entry point 名、manifest 的 `plugin_id` 和宿主探测到的 distribution 名称/版本必须一致。入口值只能是 `module:attribute`；主进程只读取这些 metadata，不调用 `.load()`。真正导入发生在无业务权限的隔离 worker 中。

最小来源插件可以直接复用 SDK 的 manifest、上下文和错误类型：

```python
from paper_reader.plugins.contracts import PluginManifest
from paper_reader.plugins.sdk import (
    PluginApplication,
    PluginContext,
    PluginOperationError,
)

manifest = PluginManifest.model_validate(
    {
        "manifest_version": "1",
        "plugin_id": "example.source",
        "display_name": "Example source",
        "distribution_name": "my-paper-reader-plugin",
        "distribution_version": "1.0.0",
        "protocol": {"minimum": "1.0", "maximum": "1.0"},
        "license": "Apache-2.0",
        "config_schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "capabilities": [
            {
                "capability_id": "example.source",
                "kind": "source_connector",
                "methods": ["estimate", "fetch_page", "probe"],
                "permissions": [],
                "timeout_seconds": 30,
            }
        ],
    }
)

async def estimate(payload: dict, context: PluginContext) -> dict:
    query = payload.get("query")
    if not isinstance(query, dict):
        raise PluginOperationError("invalid_query", "Query must be an object")
    return {"total": 0, "exact": True, "warnings": []}

async def fetch_page(payload: dict, context: PluginContext) -> dict:
    return {"records": [], "next_cursor": None, "total": 0}

async def probe(payload: dict, context: PluginContext) -> dict:
    return {"available": True}

plugin = PluginApplication(
    manifest=manifest,
    handlers={
        ("example.source", "estimate"): estimate,
        ("example.source", "fetch_page"): fetch_page,
        ("example.source", "probe"): probe,
    },
)
```

每个 handler 接收关闭式 JSON `payload` 与唯一的 `PluginContext`，返回 JSON object 或 awaitable。未在 manifest 声明的 handler 会在应用构造时被拒绝。用 `PluginOperationError(code, message, retryable=...)` 表达预期业务失败；不要把异常堆栈、凭据、路径或上游完整响应放进错误。

## 2. Manifest 与权限

稳定 ID 使用小写字母、数字、点、下划线或连字符。配置 schema 必须以 `type: object` 为根、设置 `additionalProperties: false`、使用 draft 2020-12、规范 JSON 不超过 64 KiB；远程 `$ref`、未知 schema 关键字和任意执行信息会被拒绝。

权限按 capability 声明，用户按精确资源逐项决定：

```json
{
  "kind": "network",
  "resource": "api.example.org",
  "purpose": "Read public paper metadata"
}
```

- `network` 的 resource 只能是精确公网 DNS 主机名，不能是 URL、IP、端口、通配符或 `.local`。
- `file` 与 `credential` 的 resource 只能是宿主定义的抽象 broker ID，不能是路径、URL 或凭据值。
- 插件版本、entry point 或 manifest hash 改变后旧授权自动失效；重新安装也必须重新启用和授权。
- 未声明、未批准或当前 capability 不需要的权限不能在运行时临时申请。

插件本身没有任意网络、文件或凭据访问面。只有获批调用可以使用：

```python
response = await context.broker(
    "network.fetch",
    "api.example.org",
    {"method": "GET", "url": "https://api.example.org/papers"},
)
chunk = await context.broker(
    "file.read",
    "public-taxonomy",
    {"offset": 0, "limit": 65536},
)
secret = await context.broker(
    "credential.read",
    "example-api-key",
    {},
)
```

实际 payload 仍须符合宿主 broker schema。网络每次跳转都会重验 DNS 与实际公网 peer，环境代理关闭；文件和凭据永不返回主机路径。单次调用最多 128 个唯一 broker 请求，单块二进制默认不超过 512 KiB，同一 broker request ID 不能重放。凭据只在本次调用内存在，并从事件、结果和错误中精确脱敏。

## 3. 五类能力契约

`probe` 是可选的健康信息方法；业务适配器要求下表中的其他方法完整存在。所有结果都拒绝未知字段，时间必须携带时区，JSON 不允许 NaN 或 Infinity。

| kind | 必需方法 | 宿主输入 | 插件结果要点 |
|---|---|---|---|
| `source_connector` | `estimate`, `fetch_page` | `query.months`、已确认关键词表达式、来源 filters、fingerprint；分页另含 `cursor`、`limit` | estimate：`total|null`、`exact`、`warnings`；page：`records`、不透明 `next_cursor|null`、`total|null`。记录至少含 `external_id`、`title`，可含作者、摘要、DOI、带时区日期、HTTPS landing URL、字段 provenance 与 JSON 原始事实 |
| `metadata_enricher` | `enrich` | `work_id`、DOI、题名、作者、来源 ID | `status` 为 `observed/not_found/not_applicable/disabled`；只有 observed 可带 `external_id`、`matched_by` 和 fields，每个 field 必须有 provenance，可附 rate-limit 观察 |
| `score_dimension` | `evaluate` | Work ID、题名与宿主选定的 evidence fields | `status` 为 `observed/unknown/not_applicable`；observed 的 `value` 必须在 0–100，其他状态不得带 value 且必须解释 reason；可返回 0–100 confidence 和结构化 evidence |
| `agent_backend` | `models`, `generate` | 模型目录请求；生成请求包含 task、提示词、关闭式输出 schema、推理/service tier、模型、联网开关与有限内联附件 | models 返回模型 ID、显示名、可选视觉/上下文能力；generate 返回 `raw_output`、`structured_output`、解析模型和 token 用量，可用连续 `agent.fragment` 事件流式发送 text/reasoning/diagnostic |
| `publisher` | `publish` | 已冻结 `snapshot_id` 与宿主白名单 payload | `publication_id`、可选 HTTPS `public_url` 和结构化 provenance |

来源插件仍受产品的研究边界约束：月份和最终筛选只使用用户确认的关键词版本，插件不得把研究意图原文变成隐藏过滤条件。ScoreDimension 只提供证据值，不能写评分快照、排序、反馈或 Agent 报告。AgentBackend 与 Publisher 同样不能取得领域数据库。

## 4. JSONL 与事件

宿主每次调用只发送一个 UTF-8、LF 结尾的 JSON object：

```json
{
  "protocol_version": "1.0",
  "request_id": "8d1ee59e-1674-48b7-b8c6-bd767b574003",
  "capability": "example.source",
  "method": "fetch_page",
  "payload": {},
  "deadline": "2026-07-14T12:00:00Z"
}
```

SDK 为事件分配从 1 开始的连续 sequence，并在最后写出唯一 response。可用 `await context.emit("progress", {...})` 报告有限、非敏感进度。默认每行不超过 1 MiB、一次调用总输出不超过 16 MiB；禁止 BOM、CRLF、原始控制字符、非 UTF-8、错 request ID、断裂 sequence、第二个终态或终态后的输出。诊断写 stderr，但宿主不会把任意 stderr 暴露给用户。

## 5. 发现、授权与熔断生命周期

1. 用户在设置页点击“重新检查”。宿主只枚举 distribution metadata，再用零 broker 权限的隔离 worker 读取 manifest。
2. 新安装记录默认禁用。用户检查兼容性、许可证、能力和精确权限后分别授权并启用。
3. 只有 manifest 合法、协议兼容、sandbox 健康、插件启用且当前 capability 的全部权限已批准时，适配器才进入 registry。
4. 停用、撤权、版本变化、未再发现或探测失败会立即从 registry 移除；其他插件与内置能力不受影响。
5. 连续三次超时、崩溃或协议/结果契约失败只熔断目标插件。合法结构化业务错误不算崩溃；恢复必须由用户显式 reset。

AppContainer/Job Object、无普通 subprocess 回退和 broker 是强制运行边界，不是可选的调试模式。如果本机无法建立隔离，插件保持不可用并显示稳定状态码。

## 6. 测试与发布前检查

从离线示例复制项目结构，并保持它是测试依赖而非用户能力：

```powershell
uv lock --project examples/plugins/fixture-source --check
uv run --project examples/plugins/fixture-source --python 3.12 --frozen `
  --with pytest --with pytest-asyncio --with pytest-timeout `
  pytest examples/plugins/fixture-source/tests/test_contract.py -q
```

自己的插件至少应测试：

- entry point metadata、manifest canonical hash 与协议范围；
- 每个方法的正常、空结果、分页、deadline、取消与可重试业务错误；
- 输入/输出关闭式 schema、时区、大小、UTF-8、sequence 和唯一终态；
- 每项声明权限的批准/拒绝，以及 broker 域名、资源 ID、流量和重放边界；
- 三次宿主/协议失败后的单插件熔断与显式 reset；
- 不把凭据、绝对路径、论文全文或 stderr 放进事件、结果和错误。

仓库内示例位于 [`examples/plugins/fixture-source`](../examples/plugins/fixture-source)。它只读取随 wheel 打包的 `papers.json`，用于验证自然月、分页、临时错误和协议；请勿把它列为 live smoke、真实来源或生产数据适配器。真实网络/付费 API 测试必须由插件作者单独标记为只读、显式 opt-in，并与默认 CI 隔离。

## 7. 1.x 冻结收据

协议 `1.0` 的 manifest、权限、能力方法、请求/事件/响应/错误信封语义哈希为 `520cdcc36b7e5bcede23a9b6ae891ec9e7896ca41020f628fae76b757973bae5`。发布前必须同时验证：

- `plugin_protocol_contract_sha256()` 与冻结值一致；
- 离线 `fixture-source` 的 `1.0–1.0` manifest 仍能完成真实 worker 调用；
- `2.x` 等未知 major 在发现、worker 和会话层关闭失败；
- release layout 与 release manifest 都声明同一 `1.0`；
- 任何字段、枚举、能力方法或权限语义变化先触发失败测试，再按兼容规则决定 minor 或 major。

完整兼容规则和变更流程以[插件协议 1.x 冻结契约](plugin-protocol-1.x.md)为准。
