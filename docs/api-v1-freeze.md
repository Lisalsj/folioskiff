# API v1 冻结契约

本文是前后端、Windows 发布清单和第三方调用者共同遵守的 API v1 兼容边界。当前路由代际为 `v1`，所有业务路由位于 `/api/v1/`；应用版本仍按 SemVer 独立演进。

## 1. 当前冻结事实

| 事实 | 冻结值 |
|---|---|
| API 路由代际 | `v1` |
| OpenAPI 语义 SHA-256 | `5c69bd4bfe08e2f3403cf15875c0b71d7df4ec6d4ea4ea7b8dffec7c33c1df8a` |
| 规范文件 | `openapi/openapi.json` |
| 生成入口 | `uv run python -m paper_reader.openapi --output openapi/openapi.json` |
| 契约测试 | `tests/rc/test_frozen_contracts.py`、`tests/foundation/test_openapi.py` |

语义哈希先解析完整 OpenAPI JSON，移除且只移除 `info.version`，再以 UTF-8、排序键、无多余空白的规范 JSON 计算 SHA-256。`info.version` 是应用发布版本，会单独写入 `release-manifest.json` 的 `openapi_version`；它从 `0.6.0` 变成 `0.9.0` 或 `1.0.0` 不等同于 API 破坏。路径、方法、请求、响应、枚举、安全声明或状态码发生任何变化都会改变语义哈希。

3.2.0 在 `PublicationSelection` 新增可选字段 `display_language`。省略时使用 `zh-CN`，已有草稿和已发布快照保留原有校验值。

3.0.2 的发现修复向 `RunBatchResponse` 增加可选、可为空的 `filter_counts`，用于说明获取记录经过日期与已确认关键词筛选后的数量。旧记录返回 `null`；升级过程中续跑的统计由 `evaluated_count` 标明覆盖范围。既有请求、批次数量、接受/入库计数和分页行为保持原有语义，旧客户端可以忽略该字段。

## 2. v1 兼容规则

保持 v1 时允许：

- 新增不改变既有行为的端点；
- 向既有响应对象增加真正可选、缺失时有明确旧语义的字段；
- 增加说明、示例或不改变验证行为的文档元数据。

保持 v1 时禁止：

- 删除或重命名路径、方法、请求字段或响应字段；
- 给请求增加必填字段，或收紧既有长度、范围、格式、互斥与鉴权要求；
- 改变字段含义、单位、默认值、null 语义、枚举集合、排序、分页或幂等语义；
- 改变既有成功/错误状态码、错误分类或会话、Origin、CSRF 边界；
- 把原本可选的响应字段变为必需，或让旧客户端无法忽略新增内容。
- 依赖重复 JSON 键的覆盖顺序；OpenAPI 加载器会在任意嵌套层级关闭失败。

任何允许的变更仍必须先让冻结哈希测试失败，由维护者审阅 OpenAPI 差异、更新哈希和本文件，再重新生成 TypeScript 类型。禁止为了让测试变绿而删除路径、放宽 `additionalProperties: false` 或跳过规范生成。

`/api/v1/assistant` 是论文对话的规范接口与唯一存储。四个既有 `/api/v1/conversations` 操作继续保留相同路径、请求与响应结构，但只在 HTTP 边界投影 Assistant 数据；每个操作在 OpenAPI 中带有 `x-paper-reader-compatibility-adapter: true`，且不得创建第二套表、服务或前端状态。

## 3. 发布交叉校验

Windows `release-manifest.json` 必须同时记录：

- `api_version = "v1"`；
- `openapi_version` 与发布 SemVer 完全一致；
- `openapi_sha256` 与本文语义哈希完全一致；
- 当前数据库 revision 与插件协议版本。

构建器在复制任何发行资产前验证提交的 OpenAPI 文件；发布目录验证器不信任清单或校验和，会再次比较冻结值。`packaging/release-layout.json` 保存同一组固定事实，任一处漂移都会以关闭失败结束构建。最终 source-free Windows 验收还必须启动冻结应用，读取 `/openapi.json` 并与清单交叉核验；仅有源码测试不等于最终发行证据。

## 4. 变更流程

1. 修改 Pydantic/FastAPI 契约并重新生成 OpenAPI 与 TypeScript 类型。
2. 检查语义差异是否符合上面的 v1 规则；不符合则新增 API 代际，不能沿用 v1。
3. 更新冻结哈希、release layout、发布 smoke 和本文件。
4. 运行 OpenAPI 确定性、前后端类型、会话安全、发布清单篡改与完整回归。
5. 在 CHANGELOG 中记录调用者可观察到的变化。

冻结哈希是变更探测器，不替代兼容性判断；兼容性判断必须依据实际请求、响应与安全语义。
