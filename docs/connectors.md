# 来源连接器与查询语义

## 1. 统一契约

`SourceConnector` 必须声明 ID、版本、能力、配置 schema、支持的日期/分类/分页语义、速率限制和凭据需求，并实现：

1. `estimate(query)`：估量数量和能力警告。
2. `sample(query, limit)`：返回 5–10 条不落正式库的预览样例。
3. `iterate(batch, checkpoint)`：按页返回规范记录和下一检查点。
4. `fetch_document(record)`：只在合法且用户触发的场景获取全文。
5. `health()`：只读能力探测，不能写远端状态。

当前核心协议以 `ConnectorCapabilities`、`SourceQuery`、`ConnectorEstimate`、`SourcePage` 和 `ConnectorRequestError` 表达上述能力。错误码必须落入配置、认证、限流、暂时网络、远端 5xx、响应不兼容或永久请求错误；`retryable` 单独标识，禁止用字符串猜测是否重试。注册表拒绝重复 connector ID，为后续隔离插件复用同一逻辑契约。

连接器负责远端尽可能收窄；本地层负责最终月份、关键词布尔规则、去重和排序。所有响应保留抓取时间、原始响应哈希与字段级 provenance。

预览服务并行处理所选来源，但按计划顺序返回卡片。每个来源先估量，再最多获取 50 条候选供本地裁剪，最终展示 5–10 条命中样例以及 `remote_candidates`、`in_date_range`、`matched_confirmed_keywords` 三段影响。`POST /api/v1/plans/preview` 接受尚未保存的完整计划草稿，在确认关键词边界内生成带 `draft:` 指纹的只读 Job 结果；预览不得创建 DiscoveryPlan、调用身份仓库或写入 Work。已保存计划也可按 ID 重新预览；单来源错误、未安装或缺密钥只改变该卡片状态。

## 2. 日期与批次

用户选择固定 `YYYY-MM` 起止范围，或滚动最近 N 个完整/当前自然月。论文筛选日期统一使用最近更新时间，首次发布日期单独保存。运行拆成“月份 × 来源”批次；重要修订进入更新区，不占新论文配额。

当前滚动语义为“包含当前月的最近 N 个自然月”：例如 `2026-07-13` 的最近 3 个月解析为 `2026-05` 至 `2026-07`。固定起止月均包含在范围内；所有月边界先按计划 IANA 时区解释，再判断来源的 `updated_at`。Windows 包内携带 `tzdata`，不依赖系统是否安装时区数据库。超过 24 个月返回 `date_range_over_24_months` 警告，但不拒绝保存或运行。

跨月 API 请求若不能精确表达月份，连接器可扩大远端窗口，但必须在本地严格裁剪。每次运行冻结计划、关键词编译结果与连接器协议版本，再按月份优先、计划来源顺序建立唯一批次。每个成功页在一个数据库事务中持久化候选分类、下一页游标、最后成功时间、请求指纹和批次计数；累计抓取、命中、来源入库、新 Work、更新、重复与扫描数分别保存。重试只选择可重试失败批次，并从最后成功游标继续；已提交页通过其输入游标幂等复用，不重复累计候选或计数。应用重启后遗留的 `running` 批次按同一规则恢复。

`candidate_limit` 现在只限制本批的新 Work：`metadata_refresh`、`important_revision`、未变化来源和同一 Work 的新来源映射都进入运行审计，但不占新论文名额。默认闭合扫描上限为 `max(50, candidate_limit × 10)`，创建 PlanRun 时冻结到 `plan_snapshot.quota.scan_limit`；每次官方请求最多取 50 条，并在达到新 Work 配额、来源耗尽或扫描上限时写入明确 `stop_reason`。因此重复数据不会过早挤掉新论文，也不能导致无界抓取。来源返回的结果仍需本地严格验证月份、必需组和排除组。某一来源失败不会回滚其他 Work/SourceRecord；运行最终明确区分 `succeeded`、`partial_success`、`failed` 与 `cancelled`。

计划运行历史按创建时间倒序分页读取。重要修订进入全局更新流，并通过创建它的 `run_id` 出现在对应运行更新流；后续修订不会被错误归到旧运行。纯元数据刷新只保留内部审计，不进入用户更新流。更新响应只暴露变更字段名、来源身份、来源更新时间、当前 Work 摘要和本地可读版本标志，不返回来源原始载荷、载荷哈希、凭据或本地文件路径；未读、已读与忽略状态均随数据库备份恢复。

计划频率支持手动、每日、每周和每月。应用启动时扫描上次检查点到当前时刻之间的本地计划触发点，以幂等键写入待补跑列表；扫描本身不运行来源请求，用户必须逐项确认。确认命令返回 HTTP 202，并以 `catchup ID` 派生的幂等键创建正式 PlanRun；重复确认复用同一个 Job，不会双重抓取。每月日期首版限制为 1–28 日，避免不同月长度造成隐式漂移。

## 3. 关键词编译

- 概念组内同义词使用 OR。
- 所有必需组使用 AND。
- 增强组提高候选召回或相关性，不构成硬条件。
- 排除组使用 NOT，但不得排除已收藏/已分析的历史匹配展示。
- 来源不支持的操作符由本地严格过滤补足，并在预览显示影响。

只有存在不可变 `KeywordConfirmation`、因而投影出的 `confirmed_at` 非空的 `KeywordSetVersion` 可被编译。版本行本身从不更新；查询编译结果与 connector 版本必须写入 PlanRun 快照。

研究意图建议通过 `/api/v1/keywords/suggestions` 产生新的未确认版本。首轮目标限制为 12–24 个双语词；继续扩展只限制本轮建议量，合并父版本后的最终词数没有上限。`generator_info.intent_used_for_filtering` 固定为 `false`，用于防止意图文本越过确认边界。

### 3.1 相关推荐的学术检索

相关推荐只接受仓储中当前、已确认且可执行的意图修订。`AcademicRelatedSearch` 把必需组、增强词和排除词交给与发现计划相同的 `compile_concept_groups` 路径，再按用户时区生成包含当前月的最近 1–120 个自然月；意图文本和 Agent 输出不能直接生成来源身份、DOI 或 Work。

执行按已确认的来源顺序串行、确定性推进，每个来源最多 3 页，全部来源累计最多接收 50 条远端记录，最终只展示 20 个规范候选。每页仍在本地复核最近更新时间、必需 AND/组内 OR 与逐项排除词。当前论文来源元数据中有界提取的 DOI、结构化来源 ID 或受支持官方 URL 引用可越过月份限制，但不能越过关键词规则；解析最多检查 20 条来源记录、200 个 JSON 节点、50,000 个字符、50 个 DOI 和 50 个来源 ID。

通过本地过滤的记录只进入现有 `IdentityRepository`：单页身份批量硬上限为 50，DOI、`connector_id + external_id`、题名—作者相似度与既有 merge review 阈值保持唯一事实来源。跨来源显示先按规范 DOI、再按 Work/review 身份去重；DOI 冲突的高相似结果保持 `pending_verification`，不会静默合并。标题、作者、原始与规范 DOI、来源日期和字段 provenance 分开保留。论文落地页只接受无凭据、非 IP、默认 HTTPS 公开主机；危险 scheme 或伪造 connector ID 使该来源以 `response_incompatible` 失败。

来源限流、协议错误或内部异常不会回滚其他来源及已提交页。即使只有一个来源，只要失败前已有候选，汇总仍明确标为部分成功；错误码、可重试性和 `Retry-After` 分开保留。取消在页边界检查，后续来源标为取消，不调用 Agent、不写评分、排序、反馈或偏好。

### 3.2 外部知识资源验证

知识资源不通过论文来源连接器冒充论文，也不把 Agent 提案直接标记为可信。显式联网 Agent 最多提出 36 条 `official_docs/review/course/textbook/database` 资源；应用先排除短链、搜索结果页、SEO/下载镜像和二进制归档，再以独立公共 URL 策略验证，最终最多保留 12 条。规范 URL、重定向后的最终 URL、域名、HTTP 状态、访问时间、推荐理由及逐字段来源分别保存。

验证只允许无凭据、默认 443 端口的 HTTPS 多标签 DNS 主机。DNS 的每个答案都必须是公共地址；TLS 连接直接固定到已验证 IP，同时用原始域名进行 SNI 和证书校验，并在发送 HTTP 请求前复核真实对端。先发有界 `HEAD`；仅遇到 405/501 才发 `Range: bytes=0-65535` 的 GET。最多跟随 5 次重定向且每跳重新解析，响应头和正文均受 64 KiB 上限约束，不下载任意网页正文。

## 4. 五个首版来源

| 来源 | 基础访问 | 关键差异 |
|---|---|---|
| arXiv | 免费官方 API | 类别、提交/更新时间、版本、开放 PDF |
| OpenReview | 官方 API v2 | venue/invitation/content 字段差异，公开记录无需凭据优先 |
| Europe PMC | 免费 REST | 生物医学字段、PMCID/PMID/DOI、开放全文标记 |
| IEEE Xplore | API key | 官方申请与速率限制，无 key 时禁用并展示配置指南 |
| Springer Nature | API key | Metadata/OpenAccess 等服务能力分开，无 key 时禁用 |

IEEE 与 Springer Nature 在常规 CI 使用官方结构 fixture 做契约测试；带 key 的 live smoke 只读、手动或受保护触发。任何聚合器都不能冒充原始来源。

只读 live smoke 使用 `scripts/smoke_sources.py`，只接受来源、单月、普通研究词和 1–10 条
样例上限。每次先估量、再检查最多 50 条远端候选；只有更新时间位于所选月且标题/摘要
满足同一已编译关键词的记录才能进入证据，JSON 分别记录 `remote_record_count` 与
`record_count`。IEEE/Springer 密钥只能来自环境回退，不进入命令参数；成功 JSON 只含
计数、公开 external ID/DOI/更新时间，失败 JSON 只含稳定错误码，不输出请求 URL、异常
正文、摘要、全文链接或凭据。2026-07-16 的实际运行中，Europe PMC
`2026-06 / cancer` 估量 101,535 条并返回 3 条严格月内样例，arXiv
`2026-07 / agent` 返回 3 条，OpenReview `2026-05 / agent` 返回 2 条；schema 均为
`paper-reader-source-live-smoke-v1`。

当前内置连接器实现：

- arXiv 使用官方 Atom API 的 `start`/`max_results` 分页和 `lastUpdatedDate desc` 排序，并遵守连续请求至少 3 秒的礼貌间隔。官方远端日期条件只有 `submittedDate`，因此不会错误地拿提交日期过滤修订；估量标记为需按 `<entry><updated>` 本地裁剪。[arXiv API 手册](https://info.arxiv.org/help/api/user-manual.html)
- OpenReview 使用 API v2 `/notes/search`，限定 `source=forum`，通过 `limit`/`offset`/`count` 分页；公开记录无需登录，token 只作为可选能力。`content.*.value`、`cdate` 和 `tmdate` 分别映射论文字段与版本时间。[OpenReview API v2](https://docs.openreview.net/reference/api-v2/openapi-definition)
- Europe PMC 使用 REST `resultType=core`、cursorMark 分页和官方 `UPDATE_DATE:[from TO to]` 精确月份条件，保留 PMID/PMCID、开放获取状态、全文链接和版本信息。远端候选保持默认相关性排序；不能发送 `UPDATE_DATE desc`，因为官方 `sort` 只接受可排序单值字段，该无效值会返回只有版本号的空壳响应。连接器要求 `hitCount` 与 `resultList.result` 同时存在，空壳响应明确归类为 `response_incompatible`，不能伪装成零结果。[Europe PMC REST](https://europepmc.org/RestfulWebService)
- IEEE Xplore 使用官方 Metadata API、1 基分页及 `start_date`/`end_date` 条件；二者对应记录最后插入或更新的 `insert_date`，所以月份筛选可标记为精确。连接器保留 DOI、文章编号、作者、机构、内容类型和开放获取状态；无 API key 时在发出任何网络请求前返回配置缺失。[IEEE Metadata API 响应](https://developer.ieee.org/docs/read/Metadata_API_responses)
- Springer Nature 使用官方 Metadata API v2、1 基 `s`/`p` 分页和 `onlineDate` 条件。该服务没有等价的最近修订日期，因此估量和结果始终附带 `online_date_is_not_revision_date` 警告，不能宣称为精确更新时间筛选；无 API key 时在发出任何网络请求前返回配置缺失。[Springer Nature API 服务概览](https://dev.springernature.com/docs/introduction/overview-services/)

五者的常规测试全部读取仓库内固定官方响应结构，不访问真实服务。真实 smoke 必须只读、显式启用且不能成为普通 CI 前置条件。IEEE Xplore 与 Springer Nature 设置项必须链接官方申请页面，密钥仅从本机受保护配置注入，不进入查询快照、日志或备份。

计划向导只展示连接器实际支持的首版来源过滤器：arXiv `categories`；OpenReview `venue`/`invitation`；Europe PMC `source`/`open_access`；IEEE Xplore `content_type`/`publisher`/`open_access`；Springer Nature `content_type`/`open_access`。这些值随计划快照冻结，预览和正式运行使用完全相同的输入；空值不写入快照。

`GET /api/v1/settings/sources` 只返回五来源能力、日期精确度、启用状态、`credential_configured` 布尔值、禁用原因、协议版本和官方配置链接，响应中不存在凭据字段。普通用户通过受会话、回环 Origin 与 CSRF 保护的 `PUT/DELETE /api/v1/settings/sources/{id}/credential` 写入或删除 Windows Credential Manager 条目；保存后运行时注册表立即重建，但已有任务继续使用其启动时适配器。开发/live-smoke 仍可用 `IEEE_XPLORE_API_KEY`、`SPRINGER_NATURE_API_KEY` 与可选 `OPENREVIEW_ACCESS_TOKEN` 作为非持久回退，保险库值优先。

## 5. 错误与限流

错误分类为：配置缺失、认证、限流、暂时网络、远端 5xx、响应不兼容、永久请求错误、取消。429/暂时故障使用有上限指数退避和 `Retry-After`；一个来源失败不回滚其他来源，批次保存为部分成功并可单独重试。

## 6. 全文权限

只有明确开放或用户合法导入的文件进入本地库。连接器不得绕过 robots、付费墙、登录限制或许可证。受限记录仍可保存元数据，并引导用户导入合法取得的 PDF。
