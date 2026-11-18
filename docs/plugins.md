# 插件 SDK 与隔离协议

## 1. 扩展点

固定扩展点为 `SourceConnector`、`MetadataEnricher`、`ScoreDimension`、`AgentBackend`、`Publisher`。Python entry point 只用于发现插件清单，插件逻辑必须在独立子进程运行。

## 2. 清单

插件声明：插件 ID、版本、协议版本范围、能力列表、配置 JSON Schema、每项能力需要的精确网络域、抽象文件资源和凭据别名、超时建议及许可证。未声明能力不可在运行时临时申请。

当前清单版本为 `1`，宿主协议为 `1.0`。五类能力分别只接受固定方法：来源
`probe/estimate/fetch_page`，元数据增强 `probe/enrich`，评分维度
`probe/evaluate`，Agent 后端 `probe/models/generate`，发布器 `probe/publish`。
清单按 Unicode 规范 JSON 排序并计算 SHA-256；能力、方法和权限也先规范排序，
因此输入顺序不会改变身份。

配置 schema 必须是关闭未知字段的 draft 2020-12 对象，规范字节不超过 64 KiB；
只允许受控关键词和格式，本地 fragment `$ref` 可用，远程/动态外部引用、未知
关键词、任意默认执行信息、过深或过大的结构均在启动插件前拒绝。网络权限只保存
规范 DNS 域名，文件和凭据权限只保存抽象 broker resource ID，不接受 URL、IP、
通配符、绝对路径或 `..`。

## 3. JSON Lines 信封

每行是一个完整 JSON 对象：

```json
{
  "protocol_version": "1.0",
  "request_id": "uuid",
  "capability": "source.search",
  "method": "iterate",
  "payload": {},
  "deadline": "2026-07-13T12:00:00Z"
}
```

成功响应包含同一 `request_id`、`ok: true` 和 `result`；失败响应包含 `ok: false` 与结构化 `error.code`、`error.message`、`retryable`。标准输出只允许协议行，诊断写标准错误并由主应用脱敏截断。

请求 ID 是规范 UUID，deadline 必须带时区。事件从 sequence 1 开始严格连续，最终
响应只允许一次，终态后任何输出都视为协议错误。默认每行上限 1 MiB、单请求总输出
上限 16 MiB；解码在 JSON 解析前先检查单一 LF 分帧、原始控制字符、UTF-8、BOM
与字节上限。正文中的换行只能作为 JSON 转义存在。

网络响应、网络请求正文和文件读取块的默认硬上限均为 512 KiB；Base64 与完整 broker
信封编码后仍必须小于 1 MiB 单行上限。超限在产生或转发协议帧前拒绝，不能把宿主输入
错误计入插件熔断。

## 4. 权限与故障

默认没有文件、凭据或网络访问。Windows host 为每次调用建立一次性 AppContainer，
不给任何网络 capability SID；先以挂起状态创建进程，再放入只允许一个进程、限制内存且
关闭即杀树的 Job Object，之后才恢复执行。worker 可执行文件先复制到隔离 profile，
真实工作目录和 `PATH/TEMP/LOCALAPPDATA` 都位于该 profile；子进程只继承标准输入、
标准输出和标准错误三个句柄。能力探测或任一原生限制失败时返回
`sandbox_unavailable`，绝不回退到普通子进程。

插件只能通过关联 JSONL 请求访问宿主 broker。network broker 只接受当前 capability
同时声明并获批的精确 HTTPS 域名，关闭系统代理和自动重定向，在初始请求及每次跳转前
重查 DNS，并复核实际连接地址仍属于刚验证的全局公网地址集。file broker 只接受抽象
resource ID 和受限分块，不向插件返回路径；credential broker 只读取获批别名，值仅在
本次调用内存中存在，插件事件、最终结果和用户可见错误中的精确回显都会脱敏。文件与
凭据 provider 在线程中执行，不阻塞 host 的墙钟取消与杀树。

超时、崩溃、乱码、非 JSON、错误 request ID/协议/sequence、超大帧或越权请求只影响
当前插件。同一 broker request ID 只消费一次，每次插件调用最多 128 个唯一 broker 请求，
重放或超预算都在再次产生副作用前拒绝。连续三次 host/协议失败使用原子数据库增量打开
该插件熔断；其他插件继续可用，只有用户显式 reset 才恢复。插件返回的合法结构化业务
错误不会被误计为崩溃。

## 5. 兼容性

v1.0 冻结协议 major 1。新增可选字段只增加 minor；删除字段、改变语义或错误码属于 major 变更。安装前执行清单验证和只读自检；不兼容插件保持禁用并显示原因。首版不提供市场或自动生成插件。

主进程只通过 `paper_reader.plugins` entry point 读取 name/value/distribution/version，
从不调用 `.load()`。非法 `module:attribute`、重复插件 ID、缺失 distribution metadata
会被确定性隔离，输入枚举顺序不影响结果。用户在设置页显式点“重新检查”后，宿主才
为每个候选建立无业务权限的 Windows 隔离进程，以保留方法 `plugin.manifest/probe`
读取完整清单；只有这个受限 worker 可以解析模块属性。某个候选崩溃、身份不匹配或返回
非法清单时只产生稳定问题码，其他候选仍可登记，标准错误和安装路径不会进入 API。

`plugin_installations` 的发现/upsert 默认保持禁用；entry point、distribution 版本或
manifest hash 任一变化都会再次禁用并清除当前熔断状态。权限决定按全局递增 sequence
只追加保存，并绑定 distribution version + manifest hash；旧决定保留审计但不再构成
当前授权。表中只保存 permission kind 与规范 resource，绝不保存 credential 值。
一次显式刷新中未再发现或未能重新验证的旧安装会立即停用、撤销当前 grant 并从五类
registry 移除；记录和 grant/revoke 历史仍保留用于审计。重新安装后必须重新启用和授权。

## 6. 五类领域适配与管理

清单通过、协议兼容、用户已启用、当前 capability 的全部精确权限已批准且 Windows
sandbox 健康时，宿主才把适配器原子加入对应 registry。撤销权限、停用或打开熔断会
立即移除扩展适配器；内置能力集合始终保留，插件 ID 冲突不能覆盖内置来源、增强器或
Agent 后端。

五类适配器只持有 `PluginHost`，不持有 SQLAlchemy session、领域 repository 或数据库
连接。请求先转为有大小上限的 JSON，结果按关闭式 Pydantic schema 完整验证后才生成
`SourcePage`、`EnrichmentResult`、`MetricEvidence`、`AgentResult` 或发布结果。非法结果
会计入目标插件的契约失败，不能部分写入领域对象，也不能修改论文评分、排序或反馈。

设置页只显示 distribution/版本/许可证、能力、方法、精确权限、兼容性、sandbox 和
熔断状态。每项 grant/revoke 都有独立确认；启用、停用与 reset 使用本地会话、Origin
和 CSRF 保护。页面不接收 entry point、manifest hash、凭据值、任意 stderr 或绝对路径。

## 7. 示例插件验收

`examples/plugins/fixture-source` 是一个独立 Python distribution，通过
`paper_reader.plugins` entry point 声明 `fixture.source`。它只读取包内 `papers.json`，
不请求网络、文件 broker 或凭据；按自然月严格筛选，使用不透明分页 cursor，并提供可
重试临时错误 fixture。它只用于 SDK/协议验收，不会自动安装、不会出现在内置来源中，
也不代表真实外部论文来源。

在源码工作区可独立验证：

```powershell
uv run --project examples/plugins/fixture-source --with pytest --with pytest-asyncio `
  pytest examples/plugins/fixture-source/tests/test_contract.py -q
```

命令使用示例自己的锁文件和虚拟环境，验证 distribution entry point、清单探测、精确
月份、分页、deadline、结构化临时错误和协议版本。安装器/便携包内的专用插件运行时与
第三方安装说明属于 v0.8 Windows 产品化；当前设置页不会把示例伪装为已安装扩展。
