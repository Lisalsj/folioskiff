# v1 安全边界审计（RC Task 6）

更新日期：2026-07-16

本文记录 `paper-reader` v1.0 的代码级安全审计结论。它证明当前实现的安全边界和本地故障注入结果；最终 `1.0.0` 已在冻结提交 `b3490b04f5e4a411065c9156f8cda99e2cdb4ed1` 上完成全量源码门禁、发行构建和独立验证。管理员防火墙、干净 Windows 11 x64、hosted runner 和人工辅助技术证据按个人开源验收口径列为已披露的非阻断限制，不会被冒充为已通过。

## 1. 结论

本轮发现的会话重放、最终日志输出泄密、原生调用不可硬终止、请求写入/结果退出绕过超时、跨页对象放大、原生 DLL 搜索路径污染和离线 smoke 静默降级均已关闭。应用生产路径现在把 PyMuPDF 与 RapidOCR 的不可信原生调用放入短命 worker；Windows worker 受 Job Object、完整生命周期截止时间、关闭式 JSON 协议、凭据环境剥离和结果预算共同约束。

本轮没有调用或重试 GitHub Actions。Lisalsj 账户当前 Actions 分钟已耗尽，远端 hosted runner 只保留既有 v0.8 收据；所有新增证据均来自本机。该状态不会使两个仓库共享构建目录，但会让同一账户下其他私有仓库的任务无法启动或中途缺少后续 job。

## 2. 威胁模型与非承诺

必须防护的输入包括同机恶意网页、未受信任 PDF/元数据/论文正文、恶意插件、模型响应、备份、发布选择和构建环境变量。目标是阻断未授权本地 API、CSRF、路径逃逸、凭据与私有内容泄漏、提示注入扩大能力、插件越权、资源炸弹、原生调用卡死、部分结果发布和供应链路径污染。

以下不作虚假承诺：

- 已完全控制当前 Windows 用户或操作系统的攻击者不在边界内。
- 原生 PDF/OCR worker 是可用性、资源和协议隔离，不是 AppContainer。未知的 PyMuPDF、RapidOCR 或 ONNX Runtime 原生零日若已取得同用户代码执行，仍可能继承该用户的文件访问权；插件则使用更强的 AppContainer + Job Object 边界。
- v0.8/v1.0 首版没有 Authenticode；SHA-256 只能证明字节一致，不能证明发布者身份或建立 SmartScreen 信誉。
- 回环会话是单机单用户边界，不是多用户远程身份系统。

## 3. 已审计边界

| 边界 | 关闭式规则 | 失败语义 |
|---|---|---|
| 启动与会话 | 一次性 bootstrap、严格回环 Origin、HttpOnly/Strict cookie、CSRF、服务端 12 小时过期、最多 64 个会话 | 401/403；过期或被容量淘汰的 cookie 不能重放 |
| 日志与诊断 | Uvicorn filter 后再做 formatter 最终脱敏；Authorization/cookie、JSON/YAML/环境式凭据、引号值和控制字符均处理 | 只保留 `[REDACTED]` 与单物理行，不持久化原秘密 |
| PDF/OCR 原生运行时 | 独立 worker、无 shell、固定操作名、可信 PATH、无 API key、Windows Job Object、硬截止时间与取消 | 超时/取消终止 worker；不发布部分页或 OCR 证据 |
| 原生协议 | 请求 ≤1 MiB，状态行 ≤64 KiB、累计 ≤4096 条，结果 ≤192 MiB并绑定字节数/SHA-256 | 任意额外字段、错序、超限、链接结果或收据漂移均失败关闭 |
| PDF 资源 | ≤2000 页；单页 ≤20,000 块；整份 ≤200,000 块、20,000,000 字符；几何/图像有独立预算 | 稳定脱敏错误，旧完整证据保持不变 |
| OCR 资源 | ≤500 扫描页；单页 ≤10,000 行；整份 ≤200,000 行、10,000,000 字符；PNG/像素有独立预算 | 模型或任一页失败即丢弃整批临时结果 |
| Agent | 不可信正文只进入编码数据区；模型、推理强度、联网、sandbox、工具/MCP 参数由应用生成 | 恶意论文不能改变进程能力、评分、排序或反馈 |
| 插件 | 关闭式 manifest/权限，AppContainer、无能力 SID、单进程内存 Job、broker 逐请求授权 | 超时、越权、协议错误和崩溃只禁用目标插件 |
| 精选发布 | 逐列白名单；查询不接触 PDF、批注、聊天、行为、插件配置、路径、完整提示词和原始来源响应 | canary 任一泄漏即拒绝快照/发布 |
| Windows 验收 | source-free、程序字节不变、管理员防火墙硬阻断；防火墙不可用不能写成功收据 | 稳定 `firewall_*` 失败码，无 skip-success 路径 |

## 4. 原生 worker 的完整生命周期

监督器先序列化并检查请求，再启动隐藏子进程，随后在写入任何不可信 PDF 请求前建立 Windows Job Object。冻结应用只允许一个活动进程；开发环境允许 venv 启动器加实际 Python 两个进程，但恶意夹具创建第三个进程会被系统拒绝。进程与整个 Job 的内存上限均为 1 GiB，Job handle 关闭会杀死仍存活的成员。

请求写入由独立线程执行，因此“不读取 stdin”的 worker 不能卡住监督线程。相同截止时间覆盖启动、请求写入、状态读取、结果收据和 worker 退出；结果先到但进程不退同样形成 `time_limit`。取消也覆盖这些窗口。Windows 的 `Popen.kill()` 映射到 `TerminateProcess`，随后关闭 Job handle，原生线程不能把退出无限延迟。

worker 仅继承基础 Windows/用户目录信息；API key、token、cookie、凭据和用户 `PYTHONPATH` 不进入环境。`PATH` 由当前解释器/冻结目录、Python 基础 DLL 目录和 `System32` 重建，用户自定义目录不能参与 PyMuPDF/ONNX DLL 搜索。任务目录与固定 `result.json` 都拒绝 reparse point，成功、失败和取消后均清理。

父进程不信任 worker 返回值。PDF 页号、几何、块、文本、字符汇总、扫描状态和版本逐项复算；OCR 页号、渲染尺寸、行、坐标、置信度、字符和引擎版本同样复算。子进程内与父进程解析层都执行整份块/行总量上限，避免许多短字符串对象绕过字符预算。

## 5. 本轮关闭的问题

| 编号 | 发现 | 修复与证据 |
|---|---|---|
| S6-01 | session cookie 只有客户端生命周期，服务端集合无容量/过期边界 | 服务端时钟、过期清理、64/256 容量和 cookie `Max-Age` 对齐；成员检查不因命中位置提前退出，过期重放返回 401 |
| S6-02 | 仅 filter 脱敏不能覆盖 formatter 生成的异常/访问文本 | 默认与访问 formatter 再执行最终脱敏；异常和控制字符不能伪造第二行 |
| S6-03 | JSON 引号凭据和 `U+2028/U+2029` 可能进入失败草稿 | 命名凭据支持引号值；Agent 诊断复用统一脱敏器 |
| S6-04 | clean smoke 可能把防火墙权限不足当成非阻断诊断 | 管理员、MpsSvc、规则范围与删除均为硬门禁；成功收据显式记录 outbound blocked |
| S6-05 | 单个 PyMuPDF/RapidOCR C 调用无法由线程抢占 | 两类生产服务均切换到短命 worker，真实卡死/取消测试在 2 秒内终止并清理 |
| S6-06 | 大 stdin 写入及“先报结果后不退出”不在原超时窗口内 | 异步写入；一个 deadline 覆盖完整生命周期；四种 timeout/cancel 组合均通过 |
| S6-07 | 只有单页块/行上限，跨页短对象可扩大内存 | 整份 PDF 块和 OCR 行各设 200,000 硬上限，子进程与父解析器双重检查 |
| S6-08 | 原生 worker 继承用户 PATH，可能参与 DLL 搜索 | 重建可信 PATH；恶意 `C:\attacker-bin` 夹具不可见，真实 PyMuPDF 提取仍通过 |
| S6-09 | 同一分支 push 与 PR 重复 CI，日常 PR 又重复发行构建 | CI 仅 main push、PR 或手动；日常 CI 不打包，手动/tag 打包只接受同 SHA 的成功 main-push CI，不重复执行整套质量门禁 |

## 6. 可复现证据

当前本地聚焦门禁：

- `tests/security`：24 项通过，0 失败。
- 原生隔离 + PDF 提取 + OCR：107 项通过，0 失败。
- 会话/日志/API、插件权限、发布隐私、提示注入与 Windows 工作流契约：82 项通过，0 失败。
- 全仓 Python：1,273 项通过、14 项条件跳过、0 失败；总体覆盖率 86.21%，核心领域分支覆盖率 93.56%（363/388）。
- 前端 36 个测试文件、120 项 Vitest、TypeScript strict 与生产构建通过。
- Ruff 全仓通过；严格 MyPy 对 204 个源与脚本文件通过。
- OpenAPI 与 TypeScript 类型连续两次生成一致，SHA-256 分别为 `a108a62db3f97dda0f812b85b3aaed7935be03f3eaca779a6f303a2eee194211` 与 `10c581ed7605e0e9e33ad6f48443431d3e188c4dc73f2420cdffc63c694b2177`。

前三个聚焦集合有重叠，不能相加冒充全仓数量；上面的 1,273/14 是独立、完整、一次通过的最终本地收据。

### 6.1 2026-07-16 供应链与静态审计补充

为满足个人开源发行的“无已知漏洞”门禁，本轮又对锁文件、当前树和完整 Git 历史执行了独立检查：

- 从当前 `uv.lock` 分别导出基础依赖和全部 extras 的哈希锁定清单。`pip-audit` 使用 PyPI 漏洞服务验证两份清单中的包哈希并检查已知漏洞，全部 extras 又使用 OSV 做交叉核对；三次结果均为 0 个已知漏洞。该结论只代表 2026-07-16 的已知公告状态，不承诺未来不会披露新漏洞。
- `npm audit --audit-level=low` 对当前 `package-lock.json` 返回 0 个已知漏洞。
- Bandit 以 medium severity、medium confidence 扫描 `src/paper_reader` 与 `scripts`。初次审计发现 arXiv Atom XML 使用普通解析器，以及语义索引读取使用动态占位符 SQL；两项均先增加失败测试再修复。外部 Atom 现由 `defusedxml` 解析并把 DTD/实体攻击关闭为稳定的 `response_incompatible`，向量记录查询改为固定 SQL 和单个 JSON 参数。修复后 Bandit 为 0 个 medium/high 命中。
- `detect-secrets` 离线扫描全部 Git 跟踪文件得到 50 个候选：产品与打包文件中的 6 个分别是 4 个公开契约/迁移哈希和 2 个含 `password/secret` 字样的错误码；其余均为测试假值、测试 canary、固定 fixture 哈希或用于验证拒绝内嵌凭据 URL 的样例。没有未分类候选，也没有调用远端验证服务。
- 从 Gitleaks 官方 `v8.30.1` Release 下载 Windows x64 便携包，先验证发行方公布的 SHA-256 `d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e`，再扫描完整 Git 历史。23 个候选全部分类为同一公开 OpenAPI 契约哈希，或旧生成站点中 libsodium wrapper 的 `crypto_*` 导出符号；没有未分类历史秘密。
- 安全、发现、推荐、插件权限与提示注入聚焦集合为 358 项通过、2 项条件跳过、0 失败。条件跳过只对应当前机器不可用的可选运行环境，不用于掩盖依赖、XML、SQL 或凭据边界失败。

“0 个已知漏洞”不是“数学上不存在漏洞”。正式发布后仍须持续复查新公告；任何 dependency audit、静态扫描或秘密扫描的新未分类命中都重新成为发行阻断项。

### 6.2 `1.0.0` 冻结提交复验

最终冻结提交 `b3490b04f5e4a411065c9156f8cda99e2cdb4ed1` 没有沿用旧候选收据：

- Python 基础依赖与全部 extras 分别经 PyPI 审计，全部 extras 再经 OSV 交叉检查；npm 以 low 阈值审计，结果均为 0 个已知漏洞。
- Bandit medium/high 为 0；`detect-secrets` 仍为 50 个已分类候选；完整 Git 历史仍为 23 个已分类候选，没有新增真实凭据或未分类项。
- 全量 Python 为 1,299 项通过、15 项条件跳过、0 失败，总覆盖率 85.99%，核心领域分支覆盖率 93.56%；前端、浏览器/axe、Ruff、严格 MyPy、TypeScript、生产构建、OpenAPI/类型确定性与真实 `sqlite-vec` 路径均通过。
- 封闭十文件发行的 manifest SHA-256 为 `d61fbc4ede839b0b67f0139e0347e245499771c2307d2cdd3ace6d8539fe265e`；独立验证、真实安装生命周期、`0.9.0→1.0.0` 本机升级与非管理员 `firewall_requires_administrator` 失败关闭均通过。

完整制品哈希、性能和限制见 [v1.0.0 本地发行收据](v1-local-release-receipt.md)。

## 7. Actions 额度与并行项目

GitHub-hosted Windows job 在独立临时虚拟机中运行，不会因为另一个仓库同时开发而共享工作目录、构建缓存写入面或 Release 文件。账户所有者的付费分钟与部分并发配额可以跨私有仓库共享；额度耗尽能够解释“任务未启动、后续 job 缺失或发布链停在中间”，不能解释代码层面的资产覆盖、同名上传竞争或错误 refspec。

为避免额度恢复后再次双倍消耗：

- `ci.yml` 不再对 `codex/**` 同时跑 push 与 PR；功能分支由 PR 触发，合并后的 `main` 再验证一次。
- `package-windows.yml` 的构建和 source-free 验证只在手动或 `v*` tag 运行；它通过 `actions: read` 的仓库内短期 token 只读核验同 SHA、`main`、`push`、成功的 `ci.yml`，不接受 PR/其他分支/其他 SHA，也不重复运行约一小时的完整质量门禁。日常 PR 保留 Windows 源码与浏览器门禁，不生成发行制品。
- 两个工作流继续使用 `cancel-in-progress`；版本 tag 不取消已开始的发行验证。
- 额度耗尽期间禁止为了“试一下”推送或 rerun；先完成本地 RED→GREEN 与完整门禁，再由用户决定何时恢复远端验证。
- 额度恢复后不得在同一个 `git push` 中同时发送 `main` 与 `v1.0.0`。先只推送 `main` 并等待全部 CI 分片和汇总门禁成功；再单独推送已绑定冻结提交的 tag，等待 `exact-CI prerequisite → build → verify → draft_release` 全链成功。
- draft Release 必须先核对封闭 10 文件资产、manifest 与逐项 SHA-256；随后验证仓库和 Release 的匿名公开访问及 Apache-2.0 许可证，再由用户发布 draft。失败只诊断当前阶段，不并行 rerun 上下游。

## 8. 最终候选复验与外部限制

- 本地硬门禁：最终候选已重新执行全仓 Python、前端、Playwright/axe、覆盖率、Ruff、严格 MyPy、依赖审计、静态安全、秘密扫描、OpenAPI 类型和发行 manifest；并在本机隔离根复验安装、修复安装、降级阻止、卸载、便携、程序字节不变、跨版本升级、备份恢复和性能。
- 关闭失败门禁：非管理员环境运行严格防火墙 smoke 已返回稳定 `firewall_requires_administrator`，没有写入成功收据或静默跳过。
- 非阻断后续证据：16 GB 干净 Windows 11、管理员防火墙、无 Python/Node/Git 的真实环境、hosted runner、真实按需 RapidOCR、SmartScreen 与屏幕阅读器人工收据可在条件具备后补做；在此之前文档必须明确标为未验证。

本文件与本地发行收据共同支持 v1.0 本地交付声明；它们不证明非阻断外部场景已经通过，也不表示已创建远端 tag 或 GitHub Release。
