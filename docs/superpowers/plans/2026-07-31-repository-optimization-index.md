# paper-reader 仓库优化执行索引

> 本文件是 2026-07-31 重构计划集的唯一执行入口。具体 TDD 步骤位于下列八份子计划中；较早日期的计划仅作为历史记录，不参与本轮实施。

## 1. 权威输入

- 已确认设计：[`../specs/2026-07-31-repository-optimization-design.md`](../specs/2026-07-31-repository-optimization-design.md)
- 仓库：项目根目录
- Git 分支：本地 `main`
- 远端策略：本轮不推送、不创建 PR、不消耗 GitHub Actions 分钟
- 开发方法：每项任务按“失败测试 → 最小实现 → 局部验证 → 文档同步 → Conventional Commit”执行
- 版本假设：数据重建是破坏性边界，因此最终候选版本按 `2.0.0` 规划；若要保留 `1.x`，必须在 M8 前统一修改版本契约

## 2. 执行顺序与依赖

| 顺序 | 子计划 | 主要结果 | 前置条件 | 结束时迁移头 |
|---|---|---|---|---|
| 1 | [M0–M1 Foundation / Open Source](2026-07-31-m0-m1-foundation-open-source.md) | 依赖修复、开源治理、诊断语义、CI/打包收口 | 已确认设计 | `0032_identity_candidate_indexes` |
| 2 | [M2 Runtime / Onboarding Data](2026-07-31-m2-runtime-onboarding-data.md) | 可迁移运行目录、安全重建、首次使用后端状态 | M0–M1 | `0033_onboarding_state` |
| 3 | [M3 Onboarding / Settings UI](2026-07-31-m3-onboarding-settings-ui.md) | 四步引导、设置中心、Agent 与语言默认项 | M2 | `0034_agent_language_preferences` |
| 4 | [M4 Assistant Core](2026-07-31-m4-assistant-core.md) | 统一提问/解释助手、显式上下文、持久任务 | M3 | `0035_assistant_core` |
| 5 | [M5 Translation](2026-07-31-m5-translation.md) | 英译中、多范围翻译、双语阅读、可恢复产物 | M4 | `0036_translations` |
| 6 | [M6 Related Resources](2026-07-31-m6-related-resources.md) | 相关论文与知识网站、意图确认、来源核验 | M5 | `0037_related_resources` |
| 7 | [M7 Modularization / Performance](2026-07-31-m7-modularization-performance.md) | 前后端拆分、懒加载、预算、旧存储移除 | M6 | `0038_remove_legacy_conversations` |
| 8 | [M8 Release Verification](2026-07-31-m8-release-verification.md) | 契约冻结、安全/无障碍/性能验证、Windows 闭环制品 | M7 | `0038_remove_legacy_conversations` |

不得交换 M2–M7 的顺序。后续迁移、生成类型、前端交互和兼容适配器都依赖前一个里程碑的稳定契约。

## 3. 数据库迁移链

```text
0032_identity_candidate_indexes
  -> 0033_onboarding_state
  -> 0034_agent_language_preferences
  -> 0035_assistant_core
  -> 0036_translations
  -> 0037_related_resources
  -> 0038_remove_legacy_conversations
```

每次新增迁移必须同时更新迁移注册、单一 head 测试和干净数据库建库测试。`0038` 只删除旧会话存储；`/api/v1/conversations` 仍由无旧存储的兼容适配器提供。

## 4. 跨里程碑不可破坏契约

### 用户触发与联网

- 页面加载、切换论文、打开 PDF、选择文本均不得自动调用模型。
- 提问、解释、翻译只读取用户明确选择的本地证据，强制关闭外部研究。
- 相关论文必须先展示并确认检索意图；知识网站检索必须单独、显式开启外部研究。
- 正式论文报告只能由“分析”按钮与预检面板启动，不能被对话、翻译或推荐隐式触发。

### 数据与评分

- Agent 任务不得修改 `ScoreSnapshot`、推荐排序、用户反馈或已有正式报告。
- 翻译、相关资源和聊天均绑定不可变文档哈希与证据快照。
- 数据重建前必须生成可恢复快照；保留用户合法取得的 PDF，凭据不得进入快照或备份。
- 缺失证据降低覆盖度与置信度，不等同于低质量。

### API 与任务

- Pydantic/OpenAPI 是 API 唯一事实来源；任何 API 变化都必须重新生成 `openapi/openapi.json` 与 `web/src/api/schema.d.ts`。
- 长任务返回持久 `Job`，片段先落库再发送，取消、重试和断线恢复均有测试。
- `/api/v1/assistant` 是统一助手的规范 API；既有 `/api/v1/conversations` 路径保留为兼容适配器。
- 插件协议、正式分析、比较和评分 API 不因本轮前端重构改变。

### UI、性能与无障碍

- 延续“安静图书馆”：暖纸色、深森林绿、克制酒红、编辑感标题与高可读正文。
- 桌面维持三栏阅读；中等宽度使用抽屉；移动端使用有文字标签的四项主导航和阅读标签。
- 页面级功能懒加载；初始 JavaScript 原始体积上限为 350,000 字节，PDF.js、OCR、Reader、设置、比较和发布不得进入首包。
- 键盘、焦点、对比度、降低动画和屏幕阅读器达到 WCAG AA。

### Git 与发布

- 仅修改计划列出的路径；用户未跟踪的 `.rc/`、`.w/`、`AGENTS.md` 原样保留且不暂存。
- 在本地 `main` 逐任务提交；每个提交前运行对应局部测试。
- 每个里程碑结束运行全量 Python、前端、端到端和 Windows 打包门槛。
- 本轮不执行 `git push`、发布、打标签或触发远端 Actions。

## 5. 设计覆盖矩阵

| 设计范围 | 负责计划 | 最终验证 |
|---|---|---|
| 开源治理、依赖、GitHub Actions、Codex 诊断 | M0–M1 | M8 安全与仓库契约 |
| 首次使用、路径配置、数据重建、备份边界 | M2、M3 | M8 首启与恢复场景 |
| 设置中心与响应式信息架构 | M3、M7 | M8 axe、键盘与截图 |
| 提问、解释、上下文范围、引用、持久会话 | M4 | M8 Assistant 契约 |
| 英文到简体中文翻译与双语阅读 | M5 | M8 翻译数据完整性 |
| 相关论文、知识网站、外部研究边界 | M6 | M8 网络与来源核验 |
| 大文件拆分、懒加载、旧会话存储替换 | M7 | M8 bundle 与兼容 API |
| Windows 安装器、便携包、SBOM、哈希、发布文档 | M0–M1、M8 | M8 干净机闭环 |

## 6. 每个里程碑的执行循环

1. 阅读该子计划的 Global Constraints、File Map 和首个任务。
2. 确认工作树只包含用户已有文件与当前任务改动。
3. 先添加能够准确失败的测试，并记录失败原因。
4. 只实现使当前测试通过的最小产品改动。
5. 运行任务指定的局部测试、类型检查、生成契约和静态检查。
6. 同步稳定文档；检查没有凭据、私有论文或个性化产物进入 Git。
7. 创建一个 Conventional Commit。
8. 完成子计划末尾的全量门槛和 Windows 打包门槛，再进入下一里程碑。

若同一失败连续出现两次，停止重复命令，按 `superpowers:systematic-debugging` 收集最小复现、根因证据和替代方案后再继续。

## 7. 计划规模

| 子计划 | 任务数 | TDD 步骤数 |
|---|---:|---:|
| M0–M1 | 5 | 30 |
| M2 | 5 | 30 |
| M3 | 6 | 36 |
| M4 | 8 | 49 |
| M5 | 7 | 43 |
| M6 | 8 | 50 |
| M7 | 9 | 58 |
| M8 | 6 | 37 |
| **合计** | **54** | **333** |

这些步骤是执行顺序，不是并行文件清单。直接在本地 `main` 实施时，同一时间只推进一个测试—实现闭环，避免跨里程碑半成品相互污染。
