# 数据库迁移与恢复兼容性

本文定义 `paper-reader` 本地数据库的前向兼容范围、不可逆边界和故障恢复语义。当前开发分支唯一 schema head 为 `0038_remove_legacy_conversations`。

## 0. v1 冻结收据

2026-07-16 的可复现冻结事实如下：

| 项目 | 结果 |
|---|---|
| 最旧保留发行 revision | `0001_foundation` |
| v1.0.0 冻结 head | `0032_identity_candidate_indexes` |
| 单线 revision 数 | 34 |
| HEAD 表数 | 68 |
| HEAD 非 SQLite 内部 schema objects | 289 |
| 语义 inventory SHA-256 | `6d943a03a0c9cab446f71b1aa36d4087734831614e8996f1e3635a2cfb6ba069` |
| 自动化入口 | `tests/rc/test_frozen_contracts.py`、`tests/foundation/test_migrations.py` |

冻结测试每次从空目录创建真实 SQLite 数据库，验证完整 revision 链和 HEAD inventory；兼容矩阵另从八个已保留里程碑 revision 写入关系哨兵后升级至 head，验证最终 revision、数据保留、`quick_check` 与 `foreign_key_check`。约束和索引集合按语义排序后计算哈希，因此等价创建顺序不会制造随机失败，真实列、约束、索引、触发器或 FTS 变化仍会被探测。

本收据证明当前源码的前向迁移契约，不代表用户可以用旧应用打开新数据库，也不替代最终干净 Windows 11 安装/升级生命周期验收。

## 1. 支持范围

应用支持从下列受控里程碑数据库直接前向升级到当前 head。自动化测试会在对应 revision 建立真实 SQLite fixture，写入 Foundation 关系哨兵，再逐步执行全部后续 Alembic 迁移，最后运行 `quick_check` 和 `foreign_key_check`。

| 数据来源 | 起始 revision | 当前保证 |
|---|---|---|
| v0.1 Foundation | `0001_foundation` | Job、事件和幂等键保留 |
| v0.2 Discovery | `0006_merge_review_decisions` | Work、来源、关键词、计划、运行和合并审计可前向升级 |
| v0.3 Recommendation | `0013_score_rules` | 检索、评分、偏好和规则版本可前向升级 |
| v0.4 Reader | `0018_annotation_migrations` | 本地库、文档、正文、OCR、批注和迁移审计可前向升级 |
| v0.5 Agent | `0022_comparison_reports` | 模板、报告、聊天、检查点和比较报告可前向升级 |
| v0.6 Platform | `0026_model_assets` | 备份、插件、精选发布和模型资产可前向升级 |
| v0.8 Packaging 候选 | `0027_update_settings` | 更新设置可前向升级；该行不表示 v0.8 已正式发布 |
| v0.9 RC | `0032_identity_candidate_indexes` | 当前 schema 幂等启动 |
| v2 M2 首次配置 | `0033_onboarding_state` | 增加可降级的首次配置单例，不改写论文与对象数据 |
| v2 M3 Agent/语言默认值 | `0034_agent_language_preferences` | 保留已有偏好行并追加安全默认列，可逆回到 `0033` |
| v2 M4 Assistant 核心 | `0035_assistant_core` | 增加证据绑定的唯一会话、不可变快照、任务、消息与流恢复记录，可逆回到 `0034` |
| v2 M5 翻译数据契约 | `0036_translations` | 增加文档哈希与上下文绑定的版本化成果、分段、术语和导出记录，可逆回到 `0035` |
| v2 M6 相关推荐数据契约 | `0037_related_resources` | 分离相关推荐集合、论文候选、外部知识资源与逐来源运行；冻结意图修订和联网同意，可逆回到 `0036` |
| v2 M7 旧会话存储移除 | `0038_remove_legacy_conversations` | 删除已被 Assistant 取代的旧会话四表；兼容 API 无独立存储，开发期可降回含空旧结构的 `0037` |

以下内容不属于自动迁移范围：

- 旧 `daily_paper_reader`/`daily-papper-reader` pipeline 生成的 archive、VitePress 数据或浏览器 PAT 状态。
- 人工改表、删除 Alembic revision、第三方程序写入或无法通过 SQLite 完整性检查的数据库。
- 比当前应用更新的数据库 schema。旧应用必须拒绝打开未来 schema，不得猜测降级。
- macOS、Linux 或团队版数据格式；v1 只承诺 Windows 11 x64 单机单用户。

## 2. 正常升级顺序

1. 启动器先取得单实例锁，并在建立数据库引擎或监听端口前处理已确认的恢复 journal。
2. 当前数据库不是空库且 revision 不等于 head 时，使用 SQLite online backup 创建单文件迁移快照。
3. 快照数据库和规范 manifest 分别写入 `.part`，完成 fsync 后原子发布。任一步失败都会删除半成品和孤立的单边发布文件，数据库尚未进入迁移。
4. Alembic 只按唯一线性 revision 链向前升级。成功后仅保留最近四组已验证迁移快照。
5. 自动化门禁从每个支持边界验证最终 revision、Foundation 哨兵、SQLite `quick_check` 和 `foreign_key_check`。

迁移快照是恢复证据，不是可编辑副本。应用不得把快照当作当前数据库继续写入，也不得因磁盘空间不足而跳过快照后继续迁移。

## 3. 不可逆的产品边界

Alembic 文件保留 `downgrade()` 仅用于开发期 schema 往返测试。用户产品不支持安装旧版本后降级数据库，原因如下：

- `Work` 合并保留别名墓碑并重绑定来源、文档和偏好；不能安全恢复为两个独立且同时可写的规范身份。
- `SourceRecordRevision`、`ScoreSnapshot`、`KeywordSetVersion`、`AnalysisReport`、`ComparisonReport`、`RecommendationSet` 及其审计行是不可变事实。历史版本只能新增，不能原地重写。
- 原始隐式偏好事件到期后只保留不可逆聚合权重，无法重建已删除的原始行为。
- FTS5 和后续查询索引是可重建派生数据；它们可以重建，但不能被视为用户事实的降级副本。
- 新版本 PDF 的批注迁移采用逐条接受或拒绝；源批注始终保留，已确认的目标批注不会在版本降级时自动删除。
- 插件协议、备份格式和 API v1 各自版本化。schema revision 相同不代表任意旧插件或未来备份格式都兼容。

需要回到旧应用时，应先使用升级前由该旧应用生成并验证的完整备份恢复到独立资料目录，不能让两个版本轮流写同一个数据库。

## 4. 崩溃与故障语义

| 边界 | 注入故障后的保证 | 重试语义 |
|---|---|---|
| 迁移快照数据库/manifest 发布 | 不留下单边正式文件或 `.part` | 同一路径重新建立一组完整快照 |
| 备份 ZIP finalize | 目标文件不存在，stage 与 `.part` 清理 | 同一目标重试后得到可验证 ZIP |
| 推荐集合 commit 前 | RecommendationSet 与全部 item 同时回滚 | 重试生成 version 1 |
| 推荐集合 commit 后、返回前 | 已提交集合保持完整 | 相同内容哈希返回原集合，不新增 version |
| Work 合并 commit 前 | 两个 Work、来源和审阅状态全部保持原状 | 同一人工决定可再次执行 |
| Work 合并 commit 后、返回前 | 别名墓碑、来源和偏好已形成完整事务 | 同一决定幂等返回，不重复迁移 |
| 恢复数据库/对象树发布 | 双根 journal 记录回滚、发布、检查、迁移与提交阶段 | 重启回滚旧状态或从已验证阶段继续 |
| 恢复 journal 原子替换 | Windows `winerror` 5/32 只在一秒内短暂重试；其他错误立即失败关闭 | 保留 pending/stage 供安全重启 |

恢复流程只接受用户完成预览并用 fingerprint 再确认的包。任一阶段都不得形成“新数据库 + 旧对象树”或“旧数据库 + 新对象树”的可启动混合状态。

## 5. 当前关系金样

便携备份恢复测试逐表比较以下事实，并在恢复后再次运行外键检查：

- 已确认关键词版本、计划、运行、月份来源批次和 RunCandidate。
- Work、SourceRecord、来源 revision、重要更新及其原始 run。
- 五类 ScoreSnapshot、不可变 RecommendationSet/Item 和综合排序证据。
- 收藏/反馈/阅读进度、隐式事件、不可逆聚合、兴趣作者和全局主题/时区偏好。
- PDF 内容哈希、对象字节、阅读位置、批注以及批注迁移接受/拒绝审计。
- 提示词模板、正式分析报告、单 Work 聊天消息/检查点和多论文比较。

同一 PDF、DOI 和 BibTeX 被重复或交叉导入时必须收敛到一个活动 Work、一个 LibraryEntry、一个 DocumentVersion 和一个内容寻址 PDF 对象。低置信度题名/作者相似匹配仍进入人工审阅，不能为了减少重复而静默合并。

## 6. 备份与凭据

`paper-reader-backup-v1` 只包含一致 SQLite 快照和数据库明确引用的合法 PDF 对象。Windows Credential Manager 中的 IEEE、Springer Nature、OpenAlex、Ollama Cloud 等凭据，以及日志、模型、缓存、旧备份、临时文件和诊断数据都不进入包。

恢复替换用户数据库和论文对象，但保留当前机器凭据、模型与 cache。恢复后的 `queued`/`running` Job 会标记为 `restored_interrupted`，绝不自动重放下载、模型调用、发布或其他外部副作用。

## 7. 发布门禁

数据库兼容性变更必须同时满足：

- 新 migration 只追加在当前唯一 head 后，不回改已发布 revision。
- 更新本兼容矩阵、数据模型和 CHANGELOG 中的迁移说明。
- 支持边界 fixture、故障注入、完整恢复金样、Ruff、MyPy 和全仓 pytest 通过。
- Windows 发行 manifest、安装器升级测试和干净 Windows 11 验收指向同一 schema head。

GitHub Actions 不是本地数据正确性的前提。Actions 额度不可用时，这些数据库与故障门禁可全部在隔离工作树本地执行；正式发行仍必须补齐独立干净 Windows 11 生命周期证据。
