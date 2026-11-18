# 页舟 · FolioSkiff 开发文档

本目录只维护稳定、可审查的产品与工程事实。运行日志、生成报告、个人论文、标注、备份、凭据和本地状态不得写入这里。

## 使用指南

| 文档 | 适用场景 |
|---|---|
| [开始使用](getting-started.md) | 选择安装器/便携包、启动应用并完成最小研究流程 |
| [首次配置](first-run-setup.md) | 配置存储与隐私、Agent、论文来源和阅读偏好 |
| [论文 AI 阅读助手](ai-reading-assistant.md) | 提问、解释、英译中、相关推荐及联网边界 |
| [Codex 与 Ollama 配置](codex-ollama-setup.md) | 安装、登录、能力探测、模型发现和状态码处理 |
| [故障排查](troubleshooting.md) | 处理启动、存储、来源、Agent、PDF 与模型问题 |
| [v2 威胁模型](threat-model-v2.md) | 理解 PDF 提示注入、SSRF、凭据、插件和剩余风险 |

## 产品与架构

| 文档 | 负责回答的问题 |
|---|---|
| [产品需求与验收](product-requirements.md) | 产品做什么、不做什么以及如何验收 |
| [系统架构](architecture.md) | 本地模块如何协作、责任边界在哪里 |
| [数据模型](data-model.md) | 论文身份、版本、任务与不可变约束如何表达 |
| [API v1 冻结契约](api-v1-freeze.md) | 哪些 HTTP 与事件契约必须兼容 |
| [数据库迁移与恢复兼容性](migration-compatibility.md) | 历史 revision、升级、回滚和恢复边界 |
| [架构决定记录](adr/) | 已接受的重要工程取舍及其理由 |

## 产品能力

| 文档 | 负责回答的问题 |
|---|---|
| [UI/UX 与安静图书馆](ui-ux.md) | 工作流、响应式布局、视觉和无障碍规范 |
| [来源连接器](connectors.md) | 来源能力、日期、分页、限流和查询语义 |
| [推荐、评分与偏好](recommendation.md) | 分数、证据、缺失数据、探索和偏好学习 |
| [Agent 分析](agent-analysis.md) | 关键词、报告、聊天、比较、证据与提示词边界 |
| [插件系统](plugins.md) | 扩展点、权限和隔离协议 |
| [插件 SDK](plugin-sdk.md) | 如何编写、授权、测试和发布插件 distribution |
| [插件协议 1.x](plugin-protocol-1.x.md) | 已冻结的逐行 JSON 插件契约 |

## 安全、质量与发布

| 文档 | 负责回答的问题 |
|---|---|
| [安全、隐私与凭据](security-privacy.md) | 会话、凭据、网络、论文内容和数据边界 |
| [v1 安全审计](security-audit-v1.md) | 已验证发现、关闭证据和剩余限制 |
| [v1 隐私与无障碍审计](privacy-accessibility-audit-v1.md) | 数据流、WCAG 证据与人工复验边界 |
| [v2 隐私与无障碍审计](privacy-accessibility-audit-v2.md) | 首次配置、统一 Assistant、翻译、相关推荐与三视口证据 |
| [测试与发布](testing-release.md) | 自动化门槛、测试命令、打包和发布流程 |
| [3.0.0 发布清单](v3-release-checklist.md) | 版本事实、搜索工作台和公开发布前门禁 |
| [性能基线](performance-baselines.md) | 固定样本、延迟、查询计划和目标机证据 |
| [Windows 打包](windows-packaging.md) | 安装器、便携包、Sandbox 和干净环境验收 |
| [发布门禁与限制](rc-blockers.md) | 阻断项和不能冒充通过的外部条件 |
| [v1.0.0 本地发行收据](v1-local-release-receipt.md) | 冻结提交、制品哈希、SBOM、升级与性能收据 |
| [v2.0.0 本地发行收据](v2-local-release-receipt.md) | v2 冻结提交、制品哈希、SBOM、生命周期、性能与剩余边界 |
| [实施状态](implementation-status.md) | 当前完成度和远端发布边界 |

## 当前优化工作

- [2026-07-31 仓库优化设计](superpowers/specs/2026-07-31-repository-optimization-design.md)
- [M0–M8 执行索引](superpowers/plans/2026-07-31-repository-optimization-index.md)
- [可执行 TDD 子计划](superpowers/plans/)

较早日期的执行计划是历史记录，不是当前实施入口。实现若与稳定文档冲突，应先新增或修订 ADR，再同步代码、测试与相关文档。
