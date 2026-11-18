# 数据模型

## 1. 设计原则

论文身份、来源记录、文件版本、用户状态和生成内容必须分离。可复现事实采用不可变快照；可编辑配置通过新版本替换，不能原地改写历史运行。

## 2. 核心实体

| 实体 | 身份与关键约束 |
|---|---|
| `Work` | 稳定规范论文身份，不依赖某一来源；保存规范题名与当前代表版本引用 |
| `SourceRecord` | 唯一键 `connector_id + external_id`；保存一个来源的当前投影、字段级来源与最近抓取时间 |
| `SourceRevisionRecord` | 不可变；以 `source_record_id + payload_sha256` 唯一保存来源响应快照、变化字段、来源时间与抓取时间 |
| `PaperUpdateRecord` | 绑定 Work、SourceRecord 与前后修订；区分内部元数据刷新和可见重要修订，并独立保存未读/已读/忽略状态 |
| `DocumentVersion` | 绑定 Work、SHA-256、权限、获取方式、提取/OCR 状态；同一 Work 的相同哈希只保留一条版本记录 |
| `DocumentPageText` | 以文档版本+页码为身份；原生文本/坐标与 OCR 文本/坐标分列保存，页面尺寸固定坐标语义 |
| `LibraryEntry` | 每 Work 至多一条；只表示用户已把论文加入本地库，不隐含收藏、喜欢或已读 |
| `DiscoveryPlan` | 研究意图、日期模式、来源、过滤器、频率、配额与启用状态 |
| `UserPreferences` | 单用户单例；保存关闭式时区模式、自选 IANA 时区和 system/light/dark 主题，不保存浏览器或系统行为 |
| `KeywordSetVersion` | 不可变；必需、增强、排除概念组、父版本、差异和确认时间 |
| `PlanRun` | 固定绑定计划快照与已确认关键词版本；保存月份×来源批次和部分成功 |
| `MetadataObservation` | 某增强器在某时刻对 Work 的规范字段观察；保存逐字段来源、响应/观察哈希和限额信息 |
| `ScoreSnapshot` | 不可变；规则版本、权重、原始证据、缺失策略、四类分数和解释 |
| `RecommendationSet` | 不可变；冻结一次 PlanRun 评分的关键词版本、综合规则、历史月份模式、候选/推荐数量、探索配额、退化原因和集合 SHA-256；同一 Run 内容相同则幂等复用，证据变化只追加版本 |
| `RecommendationItem` | 永久绑定评分时的 `scored_work_id` 与 relevance/quality/freshness/preference/composite 五个快照；保存默认位置、推荐通道、探索原因、资格约束和完整排序证据，后续 Work 合并不改写历史身份 |
| `ScoreRuleVersion` | 不可变质量公式修订；保存七个维度的启用状态、权重、父版本与内容哈希 |
| `LibraryState` | 每 Work 一条；收藏、反馈、原因、置顶、进度、分析状态分列保存 |
| `ImplicitPreferenceEvent` | 只接受应用内白名单事件；事件内容不可改写，180 天后删除原始记录 |
| `PreferenceAggregate` | 每 Work 一条不可逆聚合权重；不保留已过期事件的类型、时间或访问轨迹 |
| `InterestedAuthor` | 用户明确维护的作者兴趣；规范作者键与显示名称分离，可停用 |
| `ReadingPosition` | 每个不可变文档版本至多一条；绑定内容哈希、页码/总页数、缩放、旋转、进度与更新时间 |
| `Annotation` | 绑定不可变文档哈希、页码、未旋转 PDF point 坐标、精确引用文本和上下文；类型为高亮、笔记或书签 |
| `AnnotationMigrationBatch` | 一次旧版到新版的不可变迁移预览；冻结两端内容哈希、来源批注集合哈希、目标提取哈希和算法版本 |
| `AnnotationMigrationCandidate` | 每条来源批注的不可变匹配快照；保存候选位置、替代位置、置信度、覆盖状态和来源批注原貌 |
| `AnnotationMigrationDecision` | 每个候选至多一条只追加的接受/拒绝事实；接受时关联新建目标批注，拒绝不改写任何批注 |
| `AgentSettings` | 单例的非秘密默认配置；保存后端、四类任务模型 ID/推理强度、service tier、报告语言和默认联网开关，不保存任何凭据 |
| `PromptTemplateVersion` | 不可变提示词版本；保存模板家族、父版本、递增版本号、语言、输出 schema 版本和正文 SHA-256；编辑只能创建子版本 |
| `AnalysisReport` | 不可变版本；绑定文档哈希、模型、组合提示词、证据范围、语言和引用 |
| `AnalysisReportFragment` | 报告 Job 的连续流式片段；按 Job+序号唯一，必须先持久化再向客户端发送，生成结束后不能继续追加 |
| `AnalysisFailureDraft` | 与正式报告互斥的失败诊断；绑定 Job、Work 和请求指纹，只保存脱敏限长错误、受限原始响应、验证错误和修复次数 |
| `AssistantSession` | 每个规范 Work 一个；Work 合并别名在读写前解析到规范身份，是聊天、解释、翻译与相关推荐任务的统一会话根 |
| `AssistantContextSnapshot` | 不可变证据范围；绑定 Work、文档内容哈希、显式页/选区/批注范围、证据清单与快照哈希 |
| `AssistantTask` | 一次显式 ask/explain/translate/related 命令；冻结上下文、网络策略、后端/模型、推理参数、幂等键和 Job |
| `AssistantAgentRun` | 每次模型尝试的可审计事实；保存实际模型、请求/提示/输出哈希、用量、修复次数与脱敏诊断 |
| `AssistantMessage` | 按会话+序号唯一的不可变用户/Assistant/系统诊断消息；绑定任务、引用、证据清单、Job 和回复关系 |
| `AssistantFragment` | 按任务+序号唯一的不可变流式片段；绑定具体模型尝试，必须先落盘再发送事件 |
| `AssistantCheckpoint` | 不可变可见上下文检查点；保存来源消息范围、本地摘要与哈希、后端/模型和保守预算估计，绝不替代或删除原消息 |
| `ComparisonReport` | 不可变综合版本；绑定论文集合键、Job、模式、模型/提示词、结构化综合、Markdown、总体覆盖度和完整 provenance |
| `ComparisonReportItem` | 按报告+用户顺序唯一；逐篇冻结 Work/DocumentVersion/内容/证据哈希、提取状态、提取检查点与覆盖度 |
| `ComparisonPaperEvidence` | 分阶段比较的不可变逐篇提取或失败事实；成功结果可由后续 Job 按完整输入身份复用，失败不会阻止显式重试 |
| `ComparisonFragment` | 比较 Job 的连续流式片段；额外标明 direct/extraction/batch/final 阶段和可选论文位置，先落盘再发事件 |
| `RelatedResourceSet` | 绑定 Work、不可变文档哈希、Assistant 上下文快照和逐次研究意图修订；学术检索与外部网站研究分开授权，未确认意图不能执行 |
| `RelatedPaperCandidate` | 以集合、连接器和外部 ID 唯一保存候选；DOI 在同一集合内去重，身份未核验时保持独立并可进入人工合并审阅 |
| `KnowledgeResource` | 与论文候选分表保存的 HTTPS 知识资源；记录规范/最终 URL、域名、类型、推荐理由、访问事实和关闭式验证状态 |
| `RelatedRunSource` | 每个集合与来源唯一的可恢复执行状态；保存候选/展示计数、游标、检查点、进度和脱敏错误，不改变论文评分或偏好 |
| `BackupArchive` | 成功生成的备份目录项；绑定创建 Job、手动/自动种类、格式/schema 版本、目录内直接相对路径、package/manifest SHA-256、字节数、对象数和删除审计 |
| `RestoreReceipt` | 不可变、无路径的成功恢复事实；保存包哈希、来源格式/应用/schema 版本、对象数、恢复时间和迁移后的 revision |
| `PluginInstallation` | 当前发现到的 entry point/distribution/manifest 身份、兼容/启用状态、连续故障计数与熔断原因；代码身份变化自动失效授权 |
| `PluginPermissionDecision` | 全局 sequence 只追加 grant/revoke；绑定插件版本与 manifest hash，只保存权限 kind 和抽象 resource，绝不保存凭据值 |
| `ModelAsset` | 绑定 bundle、固定 revision、关闭式清单、安装状态、已验证字节与完整性；同一版本幂等复用 |
| `ActiveModelAsset` | 每个 bundle 至多一个活动 revision；只能指向已完整验证且运行时可用的模型资产 |
| `ModelAssetReference` | 以任务、OCR 结果或语义索引等抽象 owner 保护精确 revision；引用存在时禁止删除 |
| `Job` | 持久状态机、进度、检查点、暂停/继续、取消、重试、错误分类和幂等键 |
| `PublishedSnapshot` | 只保存用户明确选择并通过隐私白名单的冻结内容 |
| `PublishedSnapshotWork` | 按用户顺序绑定规范 Work、明确报告版本与可选客观质量快照；不存在私有字段或全文入口 |
| `PublishedSnapshotFile` | 固定三项站点文件的原始 UTF-8 字节、媒体类型、大小和 SHA-256；与父快照一同不可更新/删除 |

## 3. 论文合并与代表版本

去重按三层证据执行：完全一致 DOI、来源稳定 ID 映射、题名与作者相似度。低置信度候选只进入人工审阅，禁止静默合并。列表选择“合法可访问、最新、最完整”的代表版本，其他来源与版本关系仍完整保留。

当前身份层由 `0002_discovery_identity` 迁移建立，具体约束如下：

- `works.doi` 保存去除 `doi:`/`https://doi.org/` 前缀后的小写规范值，并具有唯一索引。
- `source_records` 使用 `(connector_id, external_id)` 复合唯一键；`field_provenance` 和 `raw_payload` 分别保留字段来源与官方响应事实。
- `document_versions` 使用 `(work_id, content_sha256)` 唯一键；SHA-256 长度、权限状态和完整度范围均由数据库约束兜底。
- `0014_reader_library` 为 `document_versions` 增加字节数、原始文件名、获取方式、获取 URL 和最后访问时间。字节数不能为负，获取方式只允许来源占位、用户手工导入或明确下载；旧版本升级后使用 `0/source` 中性默认值，原有哈希、权限和路径保持不变。
- `0015_reader_text` 建立 `document_page_texts`，复合主键为 `(document_version_id, page_number)`，删除文档版本时级联删除页证据。页码从 1 开始；宽高和文本块坐标均使用 PDF point，并由页面宽高定义坐标变换边界。每页保存 NFC 规范化但不折叠引号/空白的原生文本、文本块、非空白字符数、扫描判定、OCR 状态/独立 payload/置信度和提取器版本。
- `0016_reader_ocr` 为页证据增加 `ocr_engine_version` 与 `ocr_model_bundle_id`，使每次 OCR 结果可追溯到具体本地引擎和哈希锁定模型包。OCR 文本块在与原生文本块相同的 PDF point 坐标中另存逐行置信度；原生文本、原生坐标和提取器版本永不被 OCR 覆盖。只有全部待识别扫描页成功、文档权限/哈希/页级原生证据仍与开始时一致后，才在一个事务中把所有 OCR 页设为完成；取消、运行时失败或并发变更不发布部分结果。
- `0017_reader_annotations` 建立 `reading_positions` 与 `annotations`。阅读位置以文档版本 ID 为主键，同时冻结内容 SHA-256；页码从 1 开始，缩放限制为 0.5–3，旋转只允许 0/90/180/270，重复提交完全相同的视图状态幂等且不改更新时间。页面进度只把更高值同步到 Work 级 `LibraryState`，因此切换版本不会倒退已读进度。批注按页码、创建时间和 ID 稳定排序，保存有限且位于页面内的未旋转 PDF point 矩形、精确引用/前后文、颜色和可选笔记；书签不要求引用或矩形，笔记类型必须包含非空笔记。
- `0018_annotation_migrations` 建立迁移批次、候选与决定三层不可变审计记录。预览键由来源/目标内容哈希、来源批注集合、目标页级提取结果和算法版本共同确定，因此相同事实重复预览幂等复用，任一文本提取或批注变化都会产生新预览。候选同时冻结来源批注原貌与目标锚点，明确区分 `matched/ambiguous/orphaned` 和 `high/medium/low/none`，并保留 OCR/native 证据及替代位置；接受决定只在目标版本原子新建一条关联批注，拒绝只追加决定。三个审计表由 SQLite 触发器禁止更新和删除，源批注始终不移动、不删除；决定前还会复核两端内容哈希、目标提取指纹和来源批注集合，避免把过期预览应用到新事实上。
- `0019_agent_templates` 建立非秘密 `agent_settings` 单例和 `prompt_template_versions`。迁移幂等播种通用、ML/AI、生物医学三个 `zh-CN` 内置模板；所有模板行均由 SQLite 触发器禁止更新和删除，自定义修改只能追加带父版本与内容 SHA-256 的新版本。设置表只保存可公开配置，Codex 登录状态与 Ollama Cloud key 仍由各自凭据边界管理。
- 当前 `agent-evidence-v2` 是按一次 Agent 调用生成的冻结值对象；`v1` 历史值保持只读兼容。证据身份覆盖规范 Work、选定文档哈希、逐页 native/OCR 文本及来源哈希、精确页码/标注过滤器、选区坐标与验证状态、覆盖度、警告和研究意图；本地文件与数据库路径永不序列化。只有重新验证本地对象 SHA-256 且所选页面、标注或选区至少一类可用时才标为 `full_text`，否则明确降级为不可声称页引用的 `abstract_only`，无摘要则拒绝调用。
- `0020_analysis_reports` 建立正式报告、流式片段和失败草稿三表。正式报告以 `(work_id, version)` 和 `job_id` 双重唯一，冻结文档/证据/提示词/请求/结构化内容四类 SHA-256 及完整来源；同一 Work 后续生成只递增版本。片段以 `(job_id, sequence)` 为复合主键并要求从 0 连续追加。正式报告与失败草稿通过双向 SQLite 插入触发器按 Job 互斥，三表另由更新/删除触发器保持不可变；失败只产生诊断草稿，旧正式版本不受影响。
- `0021_paper_conversations` 是 v0.5 的历史会话结构，曾建立 `paper_conversations`、`conversation_messages`、`conversation_fragments` 与 `conversation_checkpoints`；升级到 `0038_remove_legacy_conversations` 后这四张表被删除，当前运行时不得再读写它们。其迁移文件保留在 Alembic 线性历史中，只用于旧数据库前向升级与受控降级。
- `0022_comparison_reports` 建立 `comparison_reports`、`comparison_report_items`、`comparison_paper_evidence` 和 `comparison_fragments`。论文集合键由排序后的不同规范 Work ID 确定，同一集合的正式报告按版本递增，而每个版本仍按位置保存用户原始顺序。逐篇项目允许证据不可用但必须以 0 覆盖和 `unavailable` 状态明示；直接、提取、复用、失败状态彼此独立。分阶段提取以 `(source_job_id, position)` 唯一，成功行保存结构化内容与哈希，失败行只保存分类诊断；请求/实际模型、推理强度、service tier、证据与提示词哈希共同构成可审计复用身份，后续 Job 只复用输入身份完全一致的成功行。四表均由 SQLite 触发器禁止更新和删除，正式报告失败不会覆盖旧版本或删除完成的逐篇检查点。
- `comparison-report-v1` 禁止未知字段，固定包含概览、比较维度、共同点、差异、权衡、局限与建议；每条陈述至少一个带 Work ID 的类型化本地引用。保存前先按 Work 拆分引用，再分别与该 Work 绑定的版本化 `agent-evidence` 清单交叉验证；比较不接受外部研究引用。
- `analysis-report-v1` 是禁止额外字段的固定 Pydantic 结构。每条作者主张与 Agent 判断都有至少一个类型化引用；页/图/表/公式/标注引用在保存前与报告绑定的版本化 `agent-evidence` 清单交叉验证，外部 HTTPS 引用还受本次网络策略约束。正式 Markdown 由确定性安全渲染器生成，而不是持久化未经处理的模型 Markdown。
- 视觉证据不建立可变“当前图片”表。`analysis.report` Job 与完整组合提示词冻结候选页码、页证据 ID、文档内容 SHA-256、确定性文件名和媒体类型；执行时重新验证同一内容寻址 PDF，再在内存中生成受限 PNG。PNG 字节和 Base64 不进入 SQLite、报告、聊天、备份或缓存，任务结束即释放；正式报告仍通过文档哈希、页码和文本证据复现其视觉范围。
- `reader.extract_document` Job 冻结 Work、DocumentVersion 与内容哈希；同一幂等键只能指向同一提取命令。PyMuPDF 逐页结果先留在任务内存中，只有页数、顺序、汇总计数和版本全部一致后，才在单事务中删除旧页并写入整份新页；解析失败、取消、应用关闭或保存前文档变化均不会形成半份当前结果。失败刷新保留旧页记录并把最近提取状态标为失败。
- 同一 SHA-256 可以分别绑定到多个 Work，但物理文件统一位于 `objects/<前两位>/<次两位>/<SHA-256>.pdf`，因此不会复制字节。数据库只保存相对库根目录的路径；解析时同时拒绝绝对路径、`..`、Windows 替代分隔符和符号链接逃逸。
- `library_entries` 将“加入本地论文库”与 `library_states` 的收藏、反馈、置顶、阅读进度和分析状态分开。PDF 导入先要求用户确认合法取得，再校验文件、原子落盘并创建库成员；相同 Work 与哈希重复导入幂等复用 DocumentVersion。
- DOI、BibTeX 和 URL 引用分别形成 `manual-doi`、`manual-bibtex`、`manual-url` SourceRecord，并创建或复用 LibraryEntry，但不创建 DocumentVersion、不读取远端正文。DOI 用严格规范值复用身份；BibTeX 只接受一条平衡且有题名的记录；URL 保存去除 fragment、规范 IDNA 主机和默认端口后的 landing URL。
- `reader.acquire_document` Job 冻结规范 URL、Work/可选 SourceRecord、`open` 权限声明与 `open/save/analyze` 触发原因。下载字节进度与声明长度先进入 checkpoint，再产生事件；成功 checkpoint 追加内容哈希和 DocumentVersion ID。应用启动时恢复 `queued/running` 命令；命令幂等键复用同一 Job，内容幂等由 `(work_id, content_sha256)` 唯一约束兜底。成功下载建立或刷新 `access_status=open`、`acquisition_kind=download` 的版本并以 `save` 建立 LibraryEntry；失败或取消不创建文档行。
- Work 人工合并时 LibraryEntry 与来源、文档、偏好和阅读状态在同一事务中迁移。两边都已入库时只保留规范 Work 的一行，加入时间取更早值并保留对应加入来源，更新时间取较晚值；候选 Work 不残留不可见库成员。若两边存在相同内容哈希的重复 DocumentVersion，删除候选版本前先把批注重绑定到保留版本；阅读位置保留更新时间较新的视图，并保留两条记录中更早的创建时间，避免级联删除用户阅读事实。
- DOI 完全一致时自动合并；题名—作者相似度达到高阈值时才自动合并；中间区间创建 `merge_reviews`，候选 Work 保持分离。
- 两个不同的非空 DOI 是自动合并硬阻断：即使题名—作者相似度极高也只能进入人工审阅；用户执行合并时同样拒绝冲突 DOI，避免伪造 DOI 别名。
- `0006_merge_review_decisions` 为 Work 增加 `active/merged` 状态与自引用目标。确认合并后来源记录和不重复文档版本移动到既有规范 Work，候选 Work 作为不可展示的审计墓碑保留；“保持分开”、合并与被其他合并取代均记录决定时间、操作者类型和可选说明。
- 数据库约束保证活动 Work 没有合并目标、合并墓碑必须具有目标。`GET /api/v1/works/merge-reviews` 只默认列出待审项，用户通过受保护决定接口明确选择合并或保持分开；重复同一决定幂等，冲突的第二决定返回 409。
- 代表文档首先排除 `restricted`/`unknown`，再按来源更新时间、完整度和入库时间选择；该选择是派生结果，不删除其他版本。
- `keyword_set_versions` 只追加且由 SQLite 触发器禁止更新和删除；确认保存在独立的 `keyword_confirmations` 表，确认后同样不可撤销或改写。
- 父子关键词版本保存确定性差异；只有能关联到确认事实的版本可以编译为“组内 OR、必需组间 AND、排除组 NOT”的筛选条件。
- `discovery_plans` 保存固定/滚动月份模式、IANA 时区、已确认关键词版本、来源过滤器、频率与 20/50 默认配额；固定范围和滚动范围的互斥字段由数据库检查约束保护。
- `0030_user_preferences` 建立固定 ID 为 `default` 的单例。`timezone_mode` 只允许 `system/custom`，主题只允许 `system/light/dark`；自选模式必须保存可由 IANA `ZoneInfo` 验证的名称，系统模式不得保留自选名称。系统时区每次读取时重新解析，失败明确回退 `Asia/Shanghai` 并返回 `system_timezone_fallback=true`，不会把回退伪装成系统事实。
- `0031_library_query_indexes` 只追加查询索引，不改写 `0030` 或业务字段：`library_entries(created_at DESC, work_id)` 支持稳定 SQL 分页，`works(status, doi)` 支持规范 DOI 精确/前缀范围，`document_versions(work_id, source_updated_at DESC, completeness DESC, created_at DESC, id)` 支持批量代表版本选择。迁移可独立降级，数据行与 FTS 派生内容均保持不变。
- `0032_identity_candidate_indexes` 为 FTS 不可用时的身份候选增加 `works(status, normalized_title, id)` 与 `works(status, published_at, id)`。正常路径仍先按 DOI、再用 FTS 标题/作者候选；降级路径只做可提前停止的精确题名、前缀和年份有界查询，并显式返回降级诊断，不扫描全部 active Work。迁移可独立降回 `0031`。
- `0033_onboarding_state` 增加单例首次配置状态，保存四个实际步骤、当前步骤、隐私确认与完成时间；`complete` 始终由四个步骤推导。迁移播种未配置状态，可独立降回 `0032`，不接触论文元数据或对象库。
- `0034_agent_language_preferences` 在单例偏好上追加 Agent 后端、可选模型、推理强度、报告语言和翻译语言对。已有时区/主题行原样保留，新列使用安全默认值；所有闭集值、非空模型 ID 与不同源/目标语言同时由 API 和 SQLite 约束，迁移可独立降回 `0033`。
- `0035_assistant_core` 增加一组规范化 Assistant 表：每个 Work 唯一会话、不可变证据快照、显式任务与模型尝试、按序消息/片段和可审计压缩检查点。提问与解释在 API 和 SQLite 两层固定为仅本地论文证据；证据快照、消息、流片段和检查点由触发器阻止原地改写，迁移可独立降回 `0034`。
- `0036_translations` 增加版本化翻译成果、分段、论文级术语和导出记录。成果同时绑定规范 Work、不可变文档哈希、Assistant 上下文快照、任务与 Job；源/目标语言、四类翻译范围、OCR 置信度、分段状态和导出内容哈希均由关闭式约束保护，迁移可独立降回 `0035` 且不改写原 PDF 或提取文本。
- `0037_related_resources` 增加相关推荐集合、论文候选、外部知识资源和逐来源运行四张规范表。集合冻结 Work、文档内容哈希、上下文快照、意图修订和联网同意；`intent_history` 从修订 1 连续保存本地草案、手工编辑或 Agent 精炼的不可变内容、逐词差异、时间和最小模型身份，读取时会重新计算完整历史链。确认使用当前修订号做乐观并发检查；已确认内容再次编辑会先产生新的未确认修订，进入 queued 及以后状态的修订禁止改写。论文与网站结果分表，均保留字段来源和 `verified/pending_verification/rejected` 状态。子记录随集合级联清理，Work、文档、上下文和 Job 受 `RESTRICT` 保护；迁移可独立降回 `0036`。
- 创建或预览计划时，服务端把当时解析出的偏好时区冻结进 `DiscoveryPlan.date_range.timezone`；后续修改系统时区或用户偏好不会改变旧计划、旧预览和历史运行的自然月边界。客户端显式提交的合法计划时区继续原样保留。
- `plan_catchups` 使用 `(plan_id, scheduled_for)` 唯一键保存应用关闭期间错过的触发点。启动时只追加 `pending` 项；只有用户确认后才转为 `confirmed`，实际运行由后续 PlanRun 编排消费。
- `plan_runs` 由 `0005_discovery_runs` 建立，唯一绑定一个持久 Job，并冻结解析后的月份、计划配置、已确认关键词定义/编译表达式和每个连接器协议版本；后续修改计划不会回写历史运行。
- `run_batches` 使用 `(run_id, month, connector_id)` 唯一键。每批独立保存状态、查询指纹、下一页游标、最后成功时间、抓取/命中/入库计数、尝试次数、结构化错误、可重试性和 `Retry-After`，从而允许部分成功与单批续跑。
- `run_candidates` 由 `0008_run_candidates` 建立，以 `(run_id, source_record_id)` 唯一保存某次运行实际接受的 Work、来源记录、批次和身份匹配类型。重复页面/续跑幂等；人工合并 Work 时关系重定向到规范目标，因此可重建当次候选集合而不扫描或猜测全库。
- `metadata_observations` 由 `0009_metadata_observations` 建立。每条记录保存增强器/协议版本、`observed/not_found/not_applicable/disabled` 状态、稳定标识匹配方式、规范字段、逐字段 provenance、响应 SHA-256、抓取时间和响应限额。观察 SHA-256 覆盖除 Work ID 外的完整结果与时间：同一任务重试幂等，后续刷新保留新历史；人工合并 Work 时相同观察去重，其余观察转移到规范目标。
- `score_snapshots` 由 `0010_score_snapshots` 建立。每条快照绑定 Work、可选 PlanRun 与上下文，保存分数类型、规则版本、0–100 分数/覆盖度/置信度、逐维组件、配置权重、缺失策略、证据观察 ID、硬约束和内容 SHA-256；相同内容幂等复用，SQLite 触发器禁止任何更新或删除。Work 后续发生身份合并时旧快照作为原身份审计事实保留，不被静默改写。
- 论文完整性不另造可变“当前状态”事实，而由最新 `MetadataObservation` 按 `integrity-v1` 计算，并把状态、可见性、默认资格、强警告、置信度上限、覆盖来源和逐条来源路径冻结到新 `ScoreSnapshot.hard_constraints.integrity`。后续更正或恢复只会产生新观察与新快照，不回写历史判断。
- `0011_rating_catalogs` 将用户导入的 JCR、CAS 与新锐榜单拆成三层：`rating_catalogs` 保存种类、名称、版本、来源说明、文件 SHA-256、合法使用确认和行数；`venue_ratings` 保存规范名称、别名、标签、0–100 映射、标识符与原文件行号；两表由 SQLite 触发器保证不可变。`rating_catalog_selections` 只保存每种目录当前启用的不可变版本，可替换或删除；历史 ScoreSnapshot 中的目录 ID 与文件哈希不受活动指针变化影响。
- `0012_preferences` 建立 `library_states`、`implicit_preference_events`、`preference_aggregates` 与 `interested_authors`。收藏、反馈、原因、置顶、阅读进度和分析状态各自独立，不能由一次“喜欢”覆盖其他阅读事实；反馈只能为中立、喜欢或不喜欢，进度由数据库限制在 0–100。
- 原始偏好事件只允许列表/详情/PDF 打开、阅读时长分桶、阅读完成和生成报告六类应用内事实。全局唯一 `event_key` 令重试幂等；数据库触发器禁止修改事件身份、类型、分桶、权重和时间，仅允许论文人工合并时把同一事实重绑定到规范 Work。
- 严格早于 180 天边界的原始事件在应用启动时转成每 Work 的不可逆总权重并删除；恰在边界的事件继续保留。聚合不保存事件种类、具体时间或可逆明细，因此不能重建个人阅读轨迹。
- Work 人工合并时，收藏和置顶取并集、阅读进度取最大值、`dislike > like > neutral`、`available > failed > not_analyzed`，胜出反馈的原因去重保留；原始事件重绑定，聚合权重相加。合并墓碑不残留孤立偏好行。
- `0013_score_rules` 把自定义客观质量公式拆成不可变 `score_rule_versions` 和可变 `score_rule_selections`。每个修订保存名称、固定七维启用状态/权重、父修订、递增序号和规范 JSON SHA-256；SQLite 触发器禁止更新或删除历史公式，恢复默认只删除活动指针。
- 内置 `quality-v1` 是虚拟默认规则，不制造无意义数据库行。自定义修订必须恰好声明七个已知维度、停用维度权重为零、启用维度权重大于零且总和为 100%；新增维度只能通过后续 `ScoreDimension` 插件协议声明，不能在界面中伪造没有证据实现的因子。
- `0023_backup_archives` 建立 `backup_archives` 与 `restore_receipts`。归档行只在包已原子落盘并完成 package/manifest 哈希后写入；路径必须是备份根内的直接文件名，下载时以已打开句柄重验普通文件身份、大小与 SHA-256。删除只追加 `deleted_at` 审计，不复用 ID。恢复收据不含来源文件名、上传/staging/rollback 路径或密码；恢复成功后的迁移 revision 与对象计数可独立核验。
- `pending-restore.json` 与 restore journal 是启动前文件系统控制记录，不进入被恢复的 SQLite，也不属于可发布或可备份业务数据。pending 绑定已验证包、stage、目标根和 fingerprint；journal 逐阶段记录数据库/WAL/SHM 与对象树的 rollback/publish 身份。启动器在数据库引擎前继续或回滚它们，成功后才把无路径 `RestoreReceipt` 写入新数据库。
- `0024_plugin_registry` 以 `plugin_installations` 保存当前 entry point/distribution/manifest 身份、兼容与启用状态、故障计数和熔断原因；发现或 upsert 不能自动启用，entry point、distribution version 或 manifest SHA-256 改变会使当前启用失效。`plugin_permission_decisions` 用全局递增 sequence 保存只追加 grant/revoke，决定绑定 plugin + distribution version + manifest hash，只含权限种类和抽象 resource，不含凭据值；旧版本决定保留但不能授权新版本。
- `0025_published_snapshots` 把一次已确认精选拆成不可变 `published_snapshots`、稳定用户顺序的 `published_snapshot_works` 和逐字节 `published_snapshot_files`。父记录冻结原始选择、公开 payload、预览指纹、完整文件清单及 payload/manifest/snapshot 三层 SHA-256；子记录只关联解析后的规范 Work、用户明确选中的正式报告版本、所用客观质量快照，以及固定的 `index.html/data.json/manifest.json` 字节、媒体类型、大小和哈希。三个表均由 SQLite 触发器禁止更新或删除；同一快照哈希幂等复用，重复发布不会制造内容相同的第二份历史。
- `0026_model_assets` 建立 `model_assets`、`active_model_assets` 与 `model_asset_references`，并为 `jobs` 增加 `pause_requested/paused_at`。资产以 `(bundle_id, revision)` 唯一，保存关闭式清单身份、下载/完整性状态、精确字节和退化原因；活动指针受外键限制且只能由服务切换到已验证版本。引用按 owner kind/ID 绑定精确 revision，为正在运行的 OCR、语义索引和安装任务提供删除保护。应用重启只自动恢复 `queued/running` 模型任务，`paused` 保持静止直到用户显式继续。
- 发布预览先把合并墓碑解析到活动规范 Work，再按用户原始顺序去重。论文题名、作者、摘要、DOI 和日期只取当前规范 Work；质量只取最新不可变 `quality` 快照并公开分数、规则版本、覆盖度和置信度，缺失时保留 `null`；报告只允许用户逐篇明确指定的 `analysis_reports` 正式版本。未选报告的存在只参与过期检测，不进入公开内容。
- 元数据增强长任务使用通用 `Job` 保存冻结的 Run ID、候选 Work ID 与增强器版本，并在 checkpoint 中分别记录已完成观察和结构化失败。应用崩溃只自动恢复 `queued/running`；已形成 `partial_success` 的任务由用户明确重试，且只补 `retryable` 失败项。
- 执行只把“更新时间落入该月且命中已确认布尔规则”的来源记录交给统一身份仓库；研究意图原文和增强词不越过已确认关键词边界成为硬过滤条件。
- `0007_recommendation_fts` 新增 `work_search` FTS5 虚拟表。它只保存规范题名、摘要和作者的 `multilingual-v1` 派生词元，以 Work ID 关联但不拥有业务事实；活动 Work 入库/合并时增量同步，计数不一致时可从 `works` 完整重建。

## 4. 不可变与可变

- 不可变：确认后的关键词版本、运行输入快照、评分快照、批注迁移预览/候选/决定、提示词模板版本、Agent 证据包、正式分析报告、比较报告、恢复收据，以及已发布快照的选择、载荷、Work/报告/评分绑定、文件字节和全部哈希。
- 可变但留审计：计划配置、规则集和 Agent 设置；模板修改通过不可变子版本表达。
- 当前状态：Job 进度与暂停检查点、库状态、下载/提取状态、模型活动指针、会话检查点。
- 只追加：原始聊天消息、保留期内的隐式事件、来源抓取事实、元数据增强观察、插件权限决定。隐式事件到期后只能执行不可逆聚合与原始行删除；插件决定只能追加相反决定，不能更新或删除历史行。

## 5. 删除与保留

删除全文缓存不删除 Work、来源元数据、反馈或报告。收藏、置顶、已分析或有标注的全文受保护。原始隐式事件 180 天后转成不可逆聚合；凭据永不进入普通数据库备份。

缓存清理以 `content_sha256` 为物理删除单位，而不是以单条 DocumentVersion 为单位。同一哈希跨多个 Work 共享时，任何一个关联 Work 的保护状态都会保护整个对象；只有全部关联状态均明确符合保留规则才能进入预览中的可清理集合。应用成功后只把所有同哈希 DocumentVersion 的 `relative_path` 置空，`content_sha256`、`byte_size`、获取事实、反馈和 `extraction_status` 继续作为审计记录保留。预览令牌是当前候选与保护证据的确定性指纹，状态变化后旧令牌不可执行。

界面中的导入完成提示、任务名称、OCR 状态和清理候选都是 API 事实的投影，不形成新的领域表。客户端不保存本地路径、清理判定或迁移匹配分；刷新论文库、重连任务事件或重新打开阅读器都必须能从 Work、DocumentVersion、Job、ReadingPosition、Annotation 与迁移审计表重建相同状态。

## 6. 更新检查状态

`UpdateSettingsRecord` 是 `id = default` 的单例设置行，字段边界如下：

- `automatic_checks_enabled` 是旧版偏好列。应用统一使用手动检查。v1 设置接口接受旧客户端 PATCH 的布尔值，始终返回生效值 `false` 和 `next_automatic_check_at: null`。
- `last_checked_at`：最近一次实际访问固定 GitHub 端点的 UTC 时间；被节流的读取不推进时间。
- `last_status` / `last_error_code`：关闭式、无原始异常的粗粒度结果，例如 `update_available`、`offline` 或 `rate_limited`。
- `cached_release_json`：只保存已经通过版本、仓库 URL、日期、纯文本、能力矩阵和命名资产校验的公开 Release 投影；不保存下载 URL、响应正文、响应头、cookie、设备信息、路径、论文、偏好或凭据。

清除更新记录会重置检查时间、状态和公开 Release 缓存。`0027_update_settings` 添加了这个单例表。

## 7. 来源修订与论文更新

`0028_source_revisions_and_updates` 把“同一来源记录再次出现”拆成当前投影、不可变历史和用户更新状态三层：

- `SourceRecord` 仍以 `connector_id + external_id` 唯一，但现在只表示该来源的当前投影。完全相同的响应只推进 `fetched_at`，不会制造新历史。
- `SourceRevisionRecord` 为每个不同的规范响应保存一条不可变快照。快照覆盖题名、作者、摘要、DOI、发表时间、来源更新时间、落地页、字段级来源和官方原始载荷，并以规范 JSON 的 SHA-256 幂等去重；SQLite 触发器禁止修改或删除。旧数据库升级时，每条既有 `SourceRecord` 都生成一条 `baseline` 修订。
- `PaperUpdateRecord` 绑定 Work、来源记录和前后两个修订，只保存分类、可见性、用户状态与变更证据，不复制 PDF、标注或私有正文。纯 URL、provenance 或附加载荷变化是内部 `metadata_refresh`；题名、作者、摘要、DOI、发表时间变化，或来源版本时间向前推进，是可见的 `important_revision`。用户状态仅允许 `unread/read/dismissed`。
- 首次创建 Work 分类为 `new_work`；同一 Work 新增另一个来源分类为 `existing_work_new_source`；重复且未变分类为 `unchanged`。`run_candidates.discovery_kind` 保存该发现事实，迁移按旧 `match_kind` 确定性回填，不重写旧身份匹配证据。
- 重要修订在同一数据库事务中刷新当前 SourceRecord，并按“字段级来源优先级 → 来源更新时间 → 字段完整度 → 稳定来源键”重新计算 Work 当前规范字段。Springer Nature 与 IEEE 等出版方记录优先于预印本来源；规则与到达顺序无关。事务提交成功且规范字段确实变化后才同步派生检索索引，失败时来源、Work、修订、更新和索引都不留下半更新。
- 人工合并 Work 时，历史 SourceRevision 继续绑定原始 Work 审计身份；当前 SourceRecord 和可见更新转向规范 Work。这样既能从当前身份查看更新，又不会改写历史快照。
- `0028b_discovery_quota_counts` 为每个来源月份批次增加 `new_work_count/update_count/duplicate_count/scanned_count`。旧 `fetched/accepted/ingested` 继续用于兼容和来源诊断；新计数决定配额与更新分流。迁移以既有 `run_candidates.discovery_kind` 回填三类计数，并以旧 `fetched_count` 回填扫描数，升级和回退都不删除运行或候选证据。
- `0028c_paper_update_runs` 允许 `PaperUpdate.run_id` 记录重要修订或内部元数据刷新最初出现在哪个不可变 PlanRun；直接导入或旧数据可以为空。发现执行在创建修订的同一身份事务中写入该归属，后续来源刷新不会改写旧归属。删除运行时外键只将归属置空，更新事实、用户的未读/已读/忽略状态和不可变来源修订继续保留。

## 8. 不可变推荐结果

`0029_recommendation_sets` 将“评分事实”“推荐成员”和“界面排序”拆开：

- `ScoreSnapshotRepository.record` 只接受 relevance、quality、freshness、preference、composite 五种关闭式类型。Work、Run、上下文、规则版本、0–100 分数/覆盖度/置信度、逐维组件、权重、缺失策略、观测证据和硬约束共同形成可复现事实；相同 payload SHA-256 幂等复用，任一规则或证据变化都产生新快照。`record_quality` 只是兼容入口，不形成第二套存储语义。
- 每次 materially different 的评分为同一 `PlanRun` 追加一个 `RecommendationSet` 版本。集合冻结已确认关键词版本、`composite-v1` 规则、固定历史月份标记、默认 20/上限 50 的配额、探索目标与实际数量、退化原因和全部推荐项内容哈希；完全相同的重试复用原集合。
- `RecommendationItem` 保存候选池内每个已评分身份的五个快照外键、完整排序信号、更新时间、默认资格、硬约束、默认位置、exploitation/exploration 通道和探索原因。五种视图只能读取这些不可变信号重新排序，不能重新评分；历史匹配单独返回且不占新论文配额，撤稿等不合格项默认隐藏但可显式查看。
- 集合和项目均由 SQLite 触发器禁止更新或删除，所有 Work、PlanRun、KeywordSetVersion 与 ScoreSnapshot 外键使用 `RESTRICT`。人工合并 Work 时，项目的 `scored_work_id`、快照和集合哈希保持原样；读取 API 才沿 `merged_into_work_id` 解析当前规范身份并折叠同集合别名，同时保留原评分证据。

## 9. v1 数据库冻结边界

`0038_remove_legacy_conversations` 删除被统一 Assistant 取代的四张旧会话表；`/api/v1/conversations` 仅保留为无独立存储的 API v1 兼容适配器。降级会恢复空的旧结构，但不会重建已删除的旧行。

当前唯一 migration head 为 `0038_remove_legacy_conversations`，最旧承诺直接前向升级的发行边界为 `0001_foundation`。从 base 到 head 的 40 个 revision 构成单一线性链，冻结常量位于 `paper_reader.migrations.versions`；发布清单和 Windows smoke 必须指向同一 head。

HEAD 语义 inventory 的 SHA-256 为 `4d2261f6d12a971a0acf495585f6302d349fa71cdcd028fb1929ac6c7a4579a4`。inventory 覆盖 80 个表及其列、默认值和隐藏列，外键、唯一/检查/主键约束、普通与复合索引、触发器、FTS5 虚拟表及 shadow objects，以及 Alembic revision。表级约束、外键 ID 和索引创建序号先按语义规范排序，避免 SQLite/SQLAlchemy 的等价创建顺序造成伪哈希漂移；列顺序、复合键顺序和索引方向等真实语义顺序不会被抹去。

任何新 migration 都只能追加在当前 head 之后，并必须同时更新 revision 链、inventory 哈希、[迁移兼容报告](migration-compatibility.md)、发布 layout 和升级金样。产品只支持向前升级；migration 文件中的 `downgrade()` 仅用于开发期往返验证，不构成用户降级承诺。未来 schema、分叉 head、缺失 revision 或 inventory 漂移必须关闭失败，不能猜测修复。

## 10. v2 Assistant 冻结实体

- `onboarding_state` 保存四步向导的非敏感完成状态、当前步骤和恢复检查点；存储路径等运行事实由专用配置仓库拥有，凭据不进入该表。
- `assistant_sessions` 为每个 Work 的统一持久会话，消息、证据范围和可查看的压缩检查点只追加；旧 conversation API 只是兼容适配器。
- `translation_tasks` 绑定源文档哈希、页码/选区、源语言、目标语言、术语策略、后端和结果状态；默认目标是简体中文，源字节变化后旧结果不冒充当前翻译。
- `related_resource_tasks` 保存用户可审阅的检索意图、确认时间、`network_policy`、来源范围、外部引用与覆盖度；未确认意图不能执行来源或网站访问。

四类实体都通过 Job 幂等键和事件序列恢复，不把流式客户端状态当作事实源。翻译、相关推荐、Assistant 消息和正式分析报告保持独立版本与生命周期。
