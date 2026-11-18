# 测试、发布与运维

## 3.2.0 实测

两版使用同一台 Windows 11 机器、相同的合成数据和桌面 Chromium。20 万篇论文库每个查询预热 3 次，再取 20 次的 p95。

| 指标 | 3.0.2 | 3.2.0 |
|---|---:|---:|
| 首载 JavaScript（未压缩） | 318,043 B | 287,012 B |
| 推荐列表 SQL 次数（1 / 20 / 100 篇） | 3 / 22 / 102 | 3 / 3 / 3 |
| 40 个计划的简报 SQL 次数 | 178 | 18 |
| 40 个计划的简报 API p95 | 48.6 ms | 7.4 ms |
| 论文库首页 / 深分页 p95 | 167.1 / 224.4 ms | 168.0 / 222.5 ms |
| 中文标题 / 英文作者 / DOI 前缀 p95 | 53.0 / 53.1 / 49.8 ms | 52.1 / 53.2 / 43.2 ms |

浏览器覆盖桌面、平板和手机，在 320 至 1440 像素之间另测六种宽度的双语导航、焦点和可点击区域。PDF、批注、阅读位置以及导出语言使用隔离的合成论文库检验。构建工具的依赖审计例外见[安全与隐私](security-privacy.md#picomatch-407)。

## 1. 自动化门槛

- Python：pytest、Ruff、MyPy strict；核心领域分支覆盖率 ≥90%，总体行覆盖率 ≥80%。
- 基础测试必须在未安装模型额外依赖时验证词法降级；独立门禁安装锁定的 `models` 额外依赖并执行真实 sqlite-vec 更新/删除/余弦 KNN 契约测试，但不在 CI 下载 E5 权重。
- 前端：Vitest、Vue TypeScript strict、生产构建、npm audit。
- E2E：Playwright 覆盖首次启动、计划、关键词、预览、运行、真实 PDF.js worker/画布/文本检索/响应式阅读、阅读位置恢复与合并保存、真实 DOM 选区到 PDF 坐标、高亮覆盖层、批注/笔记/书签、分析、聊天、比较、备份和发布。
- 无障碍：axe 加人工键盘、200% 缩放、高对比度与屏幕阅读器抽查，目标 WCAG AA。
- 连接器：官方 fixture 契约、分页、日期、限流、错误；live smoke 只读且不进常规 CI。
- 安全：会话、CSRF、路径、日志、凭据、插件、恶意 PDF、下载 SSRF/DNS 重绑定、流量上限/截断、安全内容流/byte range/410 降级、批注库归属/内容哈希/坐标边界/长度限制、Work 合并与缓存清理中的批注保留、缓存清理预览/并发/共享对象保护、加密/损坏/超页数提取、原子页替换、PDF backing canvas 上限、E5/OCR 关闭式清单、无代理 HTTPS、逐跳 DNS/peer、Range/Content-Range/强 ETag、部分哈希检查点、暂停/继续/取消、Windows 路径与链接边界、原子发布、活动指针和引用删除保护、扫描页边界/DLL 隔离探测/像素上限、关闭恢复、论文提示注入、Agent 只读进程参数不变性、视觉页数量/像素/字节上限、发布白名单与不可变快照、固定无凭据 Git 目标、主工作树逐字节不变、non-fast-forward 重试、worktree 安全清理和 Pages 最小权限，以及 Agent 成功/失败前后评分与排序逐字节不变。

测试遵循失败测试 → 最小实现 → 通过 → 文档同步。禁止以放宽断言、跳过测试或降低门槛解决真实回归。

CI 将完整 Python 测试按实测耗时拆成两个 Windows `-n2` 分片，分别上传隐藏 coverage 数据，再由保持稳定检查名称的汇总 job 合并 coverage JSON。汇总结果统计 `identity.py`、`keywords.py`、`plans.py`、`runs.py` 与 `suggestions.py` 的真实分支计数，并由 `scripts/check_core_coverage.py` 强制 90% 门槛；不得用分片结果、总体行覆盖率或提高单 runner worker 数替代完整的核心分支指标。

### 1.1 本地统一质量入口

Windows PowerShell 5.1 与 PowerShell 7 均可执行 `scripts/quality.ps1`。贡献者应通过 npm 别名使用同一入口：

```powershell
npm run quality:fast
npm run quality
npm run quality:release
```

- `quality:fast`：锁文件、已安装 Python 环境、Ruff、MyPy、npm 漏洞审计、Vitest、TypeScript 和生产构建。
- `quality`：在 fast 基础上增加完整 pytest/核心分支覆盖率与 Playwright。
- `quality:release`：在 full 基础上构建并验证 Windows 便携目录。

full 与 release 在 Windows 上可能耗时较长并占用较多磁盘。三个范围都只检查，不会执行 `npm audit fix`、强制升级、Git 重置或其他自动修复。Ruff 只扫描当前仓库的 `src`、`tests`、`scripts` 和 `examples`，不会进入用户保留的其他 worktree。

前端路由与首屏性能另有关闭式门槛：

```powershell
npm run build:budget
npx playwright test web/e2e/performance.spec.ts --project=desktop-chromium
```

`build:budget` 读取生产构建的 `.vite/manifest.json`，只沿静态 imports 对 JavaScript 原始字节去重计数。入口闭包上限为 350,000 bytes，且不得包含 PDF.js、OCR、阅读器、比较、发布或设置功能块；所有页面由路由对象契约保证异步加载。浏览器性能门槛使用固定本地 fixture，验证阅读简报交互 p95 ≤800 ms、PDF 首屏 canvas ≤2.5 s、简报与阅读器 CLS <0.1，并证明进入阅读路由前不请求 PDF 运行时资源。

## 2. 测试层次

1. 纯领域单元测试：时间、关键词布尔式、去重、评分、状态机。
2. 适配器契约测试：连接器、Agent、插件、文件存储。
3. SQLite 集成测试：迁移、事务、崩溃恢复、FTS/向量降级。
4. API 集成测试：OpenAPI、会话、幂等、SSE 恢复。
5. 浏览器 E2E：真实构建和本地服务。
6. Windows 干净虚拟机：安装、升级、卸载、便携、离线与模型下载。

Git Publisher 的自动测试使用临时源码仓库和 bare remote，不访问真实外部仓库。契约同时覆盖首次 orphan、已有发布历史、并发远端推进、失败、取消、重试、陈旧 worktree dry-run、危险本地 Git 配置、URL rewrite、`.git` junction 和环境变量注入。Windows 插件沙箱的攻击夹具必须是 `/MT` 静态 CRT 的真实 x64 原生程序；不得以依赖 CLR 初始化的托管 EXE 冒充原生边界测试。

## 3. Git 与版本

使用 SemVer 和 Conventional Commits。每个里程碑一个可审查 PR，`codex/*` 短分支完成后合并。`main` 不提交个人生成数据；精选内容只进 `published`。CHANGELOG 记录用户可见变更、迁移和已知限制。

## 4. Windows 构建

流水线顺序：锁定依赖 → 前后端测试 → Vite 构建 → PyInstaller 收集 SPA/Alembic 资源 → 便携目录 smoke → Inno Setup → 从实际构建环境生成 SBOM → 封闭清单/SHA-256 → 独立干净 runner/VM 验收 → GitHub 构建证明 → 核对每个草稿附件的 SHA-256 → 发布 GitHub Release。首次发布不做 Authenticode，但保留关闭式签名 hook 并明确显示未签名。

Task 7 本机候选构建命令如下；构建器会另建无 editable、同时锁定
`packaging/models/ocr` extras 的隔离环境，不能用开发环境现状替代：

```powershell
uv run --frozen python scripts/build_windows.py
$buildPython = (Resolve-Path ".build/windows/venv/Scripts/python.exe").Path
$npm = (Get-Command npm.cmd).Source
$commit = git rev-parse HEAD
uv run --frozen --extra packaging python scripts/package_release.py dist/windows/paper-reader `
  --output-dir dist/release --python $buildPython --npm $npm `
  --git-commit $commit --database-revision 0038_remove_legacy_conversations
uv run --frozen --extra packaging python scripts/build_release_metadata.py verify dist/release
```

打包命令只从固定 Program Files/LocalAppData 位置发现 `ISCC.exe`，也可用绝对
`--iscc` 显式指定；不读取 `PATH` 或 `ISCC` 环境覆盖。Task 7–8 的本机候选以 Inno
Setup 6.7.3 真实编译；远程工作流使用 Chocolatey 可取得的 6.7.1。Python、npm、commit
与数据库 revision 都必须显式给出，避免开发环境、
隐式 PATH 或脏工作树事实混入发行清单。独立 `verify` 不生成或修改文件，只在本机
重算封闭成员、哈希、派生文本和两份 SBOM 的 schema/图/许可。普通测试不安装产品；
只有同时设置 `PAPER_READER_INSTALLER_PATH` 与
`PAPER_READER_DISTRIBUTION_PATH` 后，显式的生命周期 smoke 才会在固定 HKCU AppId
为空时执行真实安装、升级、运行中阻断、降级阻断和卸载。Task 9 的干净 runner 脚本
仍是正式发布门槛，本机 smoke 不能替代它。

### 4.1 独立 Windows runner 与 Sandbox

`.github/workflows/_windows-package.yml` 只接受 `workflow_call`，是 Windows 构建与干净验证
的唯一实现。日常 `ci.yml` 在 PR、`main` push 或手动触发时运行完整 Windows 后端、前端
与浏览器门禁，但不重复构建发行制品；Python 测试由两个 `-n2` runner 分片并行执行，
resource-sensitive 契约只在指定分片串行运行，随后由轻量 job 合并覆盖率并维持
`Python 3.12 / Windows` 门禁名称。前端与浏览器也同时启动，最终状态要求所有分片和门禁
全部成功。`package-windows.yml` 只接受手动触发和 `v*` tag，并通过只读 GitHub API 要求
当前 SHA 已存在 `main` push 触发且成功的 `ci.yml`；PR、其他分支、其他 SHA、进行中或失败
的结果都不能授权打包。它不再串行重跑同一套约一小时的质量门禁，确认精确 CI 证据后才
调用可复用工作流。手动构建与 tag 因而使用相同的锁定 Python 3.12.10、Node 24.12.0、
uv 0.11.8、Inno Setup 6.7.1 和数据库 revision 输入，不维护第二份打包逻辑。

可复用工作流的 build artifact 同时包含封闭 release 与 `clean-smoke.ps1`；后续 verify job
使用新的 Windows runner，只执行官方 `download-artifact@v8`，不 checkout 源码、不 setup
Python/Node，也不继承构建 job 的工作目录或进程。维护者按需选择 Python、npm 或
GitHub Actions 依赖版本，提交锁文件或工作流变更，并通过 CI 验证。安装器、便携包、SBOM 与 source-free smoke 属于
显式发布门禁，不在每次 PR 中重复消耗 Windows 分钟；维护者在准备发行时手动运行一次，
正式 tag 则再次执行同一条关闭式链路。

verify 把 PATH 收敛到 Windows System32，核对 manifest/SHA-256/SBOM 后，在中文、
空格和只读目录运行便携程序，并覆盖二次激活、异常锁恢复、完整出站防火墙隔离、静默
安装/同版本重装/卸载和数据保留。失败 JSON 仅含绑定哈希、稳定错误码、脱敏阶段以及启动
毫秒/预算；成功 JSON 同样不含路径、环境、异常、凭据或日志。基础权限仅为 `actions: read`
与 `contents: read`，前者只用于核验
精确 CI 证据；只有所有验证通过的 tag 才让独立 job 获得 `contents: write` 并创建 draft
Release，正式发布继续要求人工确认。

固定 Windows 11 x64、16 GB 验收机仍以默认 `8000 ms` 检查冷启动性能。GitHub hosted
runner 是 2 vCPU Windows Server，不能冒充目标性能环境；其 source-free smoke 显式使用
`40000 ms` 上限，只验证应用在内部 35 秒 ready + 5 秒健康请求功能边界内可启动。无论成功
或失败，证据都记录实际启动毫秒和采用的预算；失败还记录脱敏阶段。固定环境的 8 秒门槛
由 20 次冷/热启动性能门禁独立强制，不能用 hosted smoke 结果替代。

完整 Python 门禁安装 `pytest-timeout 2.4.x`，在 Windows 使用线程方法把单测试上限设为
300 秒，并输出最慢 25 项。超时必须留下线程堆栈并失败，不能让无进展的测试一直消耗
整个 job 的 75 分钟上限。

Windows Sandbox 使用同一脚本，关闭网络并把输入映射为只读，仅把脱敏 JSON 输出映射
为可写。准备目录、替换 manifest hash 和证据核对步骤见
[Windows 打包与干净环境验收](windows-packaging.md)。本机非管理员诊断不能创建严格
防火墙规则，必须明确记录为诊断而非离线验收。

Hosted verify 默认只有当前发行物，因此 `installer_reinstalled=true` 只证明修复式重装，
不能冒充跨版本升级。v1 终验必须再向同一脚本提供完整、哈希锁定且版本更低的 v0.9
发行物；旧版先用隔离数据根启动，当前版覆盖安装并用同一数据根启动后，
`upgrade_verified=true` 才成立。

## 5. 更新与迁移

应用只读取固定仓库的 GitHub Latest Release 公共元数据，向用户展示版本、变更、迁移说明、能力矩阵和未签名状态。Release 正文中的版本化摘要与命名 `release-manifest.json` 必须同时存在且一致；检查过程不读取任何 Release 资产。用户二次确认后仅打开固定 GitHub Release 页面，下载与安装完全发生在应用之外。迁移前自动创建快照，失败后恢复旧版本与数据库。禁止静默下载、执行或重启。

## 6. 运行诊断

日志为结构化本地日志，默认脱敏并轮转。任务抽屉显示对用户有用的摘要，不暴露堆栈或 secret。诊断包手动生成，预览文件清单后导出；用户数据和凭据默认排除。

## 7. v0.4 验收证据

- 后端：474 项收集，472 通过、2 项按 Windows/可选运行时条件跳过；`paper_reader` 总体分支覆盖率 91.50%，高于 80% 总体门槛。
- 前端：47 项 Vitest 全部通过，Vue/TypeScript strict、生产构建、Ruff、MyPy 和 `npm audit --audit-level=high` 全部通过；高危依赖为 0。
- 浏览器：18 项 Playwright 在 1440×900 桌面 Chromium 与 Pixel 7 移动视口全部通过。闭环包括导入、真实 PDF.js、搜索、批注、书签、位置恢复、新版本与迁移接受，以及超限/非 PDF、OCR 不可用、取消下载、缺失文件和缓存保护失败路径；所有场景的 axe 结果为零违规。
- 视觉：测试生成的原生文本、图像扫描页与迁移前后 PDF 已用 PyMuPDF 渲染为 PNG 人工检查；桌面/移动阅读器和设置页截图已复核白色 PDF 纸张、控件裁切、焦点层级与对比度。PNG 和浏览器截图仅作为本地验收产物，不提交源码分支。

## 8. v0.5 验收证据

- 后端：586 项收集，582 通过、4 项按 Windows/可选运行时条件跳过；总体覆盖率 89.07%，核心领域分支覆盖率 95.39%（290/304），分别高于 80% 与 90% 门槛。安装锁定 `models` 额外依赖后，真实 sqlite-vec 更新、删除与余弦 KNN 契约另行通过，未下载 E5 权重。
- Agent 隔离：成功报告和无效结构化输出失败均在非空候选/评分快照上运行；`score_snapshots`（含组件 JSON）与 `run_candidates` 原始行按稳定顺序序列化后逐字节相同。恶意论文指令不能改变 Codex/Ollama 进程参数、工具/网络能力或只读证据边界。
- 前端与接口：24 个 Vitest 文件共 62 项通过；Ruff、严格 MyPy、Vue/TypeScript strict、生产构建、OpenAPI/TypeScript 类型确定性重复生成、uv/npm 锁检查和 `npm audit --audit-level=high` 全部通过，npm 漏洞为 0。
- 浏览器：22 项 Playwright 在 1440×900 桌面 Chromium 与 Pixel 7 移动视口全部通过。闭环覆盖零调用预检、云端标识、显式报告、失败草稿与旧报告保留、重试/版本/导出、聊天片段先落盘与刷新恢复、可见检查点、2–10 篇直接比较和 11 篇分阶段比较；所有场景 axe 零违规。
- 视觉：桌面云端分析预检、失败且保留 v1、直接比较，以及 Pixel 7 聊天恢复/检查点、11 篇分阶段选择截图已人工复核。截图只作为本地验收产物，不提交源码分支。
- 外部模型：自动化验收只使用确定性假后端。Codex 与 Ollama 的本地 smoke 默认仅探测版本、能力和模型目录；CI 不执行真实付费或云端生成。

## 9. v0.6 增量验收证据

- Task 9 后完整 Python 门禁为 879 项通过、6 项条件跳过，总体分支覆盖率 87.54%；核心领域分支覆盖率 95.39%（290/304）。Ruff 全仓、严格 MyPy（153 个源文件）和差异空白检查通过；发布模块聚焦分支覆盖率为 90.28%。
- Task 9 发布隐私与迁移精确门禁为 19 项通过。测试用不同唯一 canary 填充 PDF/全文、批注、聊天、偏好、凭据样本、本机路径、完整提示词、失败草稿、未选正式报告、来源原始响应和评分私有证据，并逐字节检查预览、HTML、JSON 和 manifest；SQL 捕获同时证明报告提示词/证据清单与评分组件/权重未被预览查询选取。另覆盖合并别名、用户顺序、缺失不补造、报告归属、Work/报告/评分过期、复核—冻结并发写锁、三表触发器不可变、迁移往返、CSP/HTML/JSON 注入和确定性哈希。
- 备份恢复启动与 E2E 精确门禁为 22 项通过、1 项仅在 Windows 无法建立所需权限边界时跳过；连同归档、加密和迁移快照的完整 `backups` 门禁为 136 项通过、2 项条件跳过、分支覆盖率 83.14%，高于 80% 门槛。最新全仓为 719 项通过、6 项条件跳过、总体分支覆盖率 88.08%；Ruff、严格 MyPy、62 项 Vitest、TypeScript、生产构建、OpenAPI 类型确定性、锁文件和高危依赖审计全部通过。
- 崩溃恢复对 journal 的数据库回滚、对象回滚、数据库发布、对象发布、完整性检查、迁移和清理阶段逐一注入失败；额外覆盖主数据库已移动但 WAL/SHM 尚未移动、对象树暂时缺失时的真实应用重启窗口。每种情形均证明不会启动 HTTP 服务并保留可恢复的一致状态。
- 不可信输入覆盖错误密码、损坏或截断密文、未来格式/schema、ZIP 路径穿越、重复与大小写冲突、链接成员、压缩炸弹、大小/哈希变化、数据库损坏、PDF 引用缺失、preview 过期/替换/重复确认和已有 pending；错误只暴露稳定代码，不含密码或本机路径。
- 恢复金样对计划、论文、评分、偏好、阅读、Agent 与比较等核心业务表逐行比较，并额外验证内容寻址 PDF；当前机器凭据库保持为空，模型与 cache 哨兵字节不变。正式确认前当前数据库保持原样，确认后也只在下次启动时原子应用。
- 备份设置界面新增 7 项组件测试，连同全前端为 25 个文件、70 项 Vitest 全部通过。聚焦浏览器验收为 2 个场景 × 桌面/Pixel 7 共 4 项通过：加密手动导出、密码不回填、完整范围预览、二次确认、等待重启、错误密码无危险按钮、焦点恢复、200% 文字缩放、窄屏、reduced motion 与 axe 零违规；截图已人工复核且不进入源码。
- 插件 manifest/discovery/JSONL/registry 的 Task 6 精确门禁为 72 项通过；独立插件包 58 项通过，分支覆盖率 96.73%。覆盖五类能力与方法白名单、规范 manifest hash、远程 schema 引用/未知字段/64 KiB 上限、抽象权限、entry point 零 `.load()`、重复/缺失/乱序发现、UUID/deadline/UTF-8/控制字符/单行与总量限制、请求关联/连续 sequence/唯一终态、结构化错误、版本变化权限失效、存储 hash 篡改和权限行不可变。
- 新增 `0024_plugin_registry` 后的最终全仓门禁为 778 项通过、6 项按 Windows/可选运行时条件跳过，总体分支覆盖率 88.41%；Ruff 全仓与严格 MyPy 通过。第一次全仓运行仅发现 2 个仍把 `0023` 当最新 head 的迁移快照断言；更新 head 并补强恶意输入边界后，相关 72 项精确复测和完整全仓门禁均通过。
- 插件 Task 7 精确门禁为 59 项通过；与 manifest/discovery/SDK 合并后为 117 项通过，插件包覆盖率 86.55%。Windows 原生测试真实建立 AppContainer + Job Object，并用编译期恶意 fixture 验证宿主文件/数据库哨兵、凭据环境、越界写、loopback/公网 TCP、子进程、宿主 DLL、非法 UTF-8、无限输出和无限内存均被拒绝或杀树；后续安全调用仍成功。broker 契约另覆盖每跳 DNS/peer、无代理、精确域、抽象文件/凭据、512 KiB 帧预算、凭据结果脱敏、请求 ID 防重放和单调用 128 次副作用预算；三次失败熔断只影响目标插件且必须显式 reset。
- Task 7 后最新全仓门禁为 837 项通过、6 项条件跳过、总体分支覆盖率 87.89%，核心领域分支覆盖率 95.39%（290/304），分别高于 80% 与 90% 门槛；Ruff 全仓与严格 MyPy（142 个源文件）通过。
- 插件 Task 8 精确插件包门禁为 148 项通过，覆盖五类领域 schema 映射、registry 原子注入/撤销、内置 ID 防覆盖、真实 worker 分派、显式 metadata 发现与隔离 manifest 探测、卸载/验证失败撤权、管理 API/CSRF、熔断恢复和单插件失败隔离；连同来源、增强器与 Agent 凭据生命周期为 157 项通过。示例 distribution 在独立 uv 项目中 3 项通过；插件设置与来源设置 10 项 Vitest 通过。
- 插件设置聚焦 Playwright 在 1440×900 与 Pixel 7 共 2 项通过：授权只针对一个精确资源、AlertDialog 键盘焦点、状态即时更新、200% 字号无水平溢出、reduced motion 和 axe 零违规。空状态明确说明示例未安装，页面与 API 均不渲染任意 stderr、路径或凭据值。
- Task 8 后全仓覆盖率门禁为 869 项通过、6 项条件跳过，总体分支覆盖率 87.44%；核心领域分支为 95.39%（290/304），均高于硬门槛。26 个前端文件共 77 项通过，Ruff、严格 MyPy（145 个源文件）、Vue/TypeScript strict、生产构建、0 个高危 npm 漏洞以及 OpenAPI/TypeScript 类型重复生成哈希一致性全部通过。
- Task 10 发布隐私、不可变快照、真实临时 Git 仓库与 API 精确门禁为 18 项通过；插件沙箱 23 项通过。最终全仓为 888 项通过、6 项条件跳过、0 失败，总体分支覆盖率 87.28%，核心领域分支覆盖率 95.39%（290/304），均高于硬门槛。Ruff 全仓与严格 MyPy（158 个源文件）通过；26 个前端文件共 77 项 Vitest 与生产构建通过。OpenAPI 与 TypeScript 类型顺序重生成后的 SHA-256 分别为 `083deee30634dcac9f598de01572fdd11e4de337921fdcd233a29397ff027b79` 和 `1e97650236dda8e5af09943f989e7d092610f928d58348f5f77ca191a8c3c93a`，与实现完成后的基准一致。
- v0.6 最终 Python 门禁共收集 894 项：889 项通过、5 项按平台或可选运行时条件跳过、0 失败；总体分支覆盖率 87.53%，核心领域分支覆盖率 95.39%（290/304），分别高于 80% 与 90% 硬门槛。Ruff 全仓通过，严格 MyPy 对 157 个源文件报告零问题。
- Windows 精确门禁真实运行 AppContainer + Job Object 与原生恶意插件 23 项、恢复启动/故障注入 22 项通过和 1 项权限条件跳过、真实 `sqlite-vec` 1 项、临时 Git Publisher/API 9 项、独立示例插件 3 项；全部使用本地 fixture、临时仓库或离线响应，从不把 fixture 当成 live smoke，也不访问真实发布分支。
- 前端最终门禁为 27 个 Vitest 文件、81 项测试，Vue/TypeScript strict 和生产构建全部通过。Playwright 在 1440×900 与 Pixel 7 共 30 项通过，覆盖发现、阅读、评分、Agent、比较、备份、插件和精选发布；普通、200% 字号、窄屏、reduced motion 与 Windows 强制颜色模式均通过 axe，人工截图复核无裁切、重叠或危险操作层级混淆。强制颜色验收发现并修复品牌标记及主按钮被系统白色背景吞没的问题。
- OpenAPI 与 TypeScript 类型连续两次重生成完全一致，SHA-256 分别为 `c668a373d1fa28511bbb05f6bfa2abef89dd251ad566fe8fa6a8ddb4e225467b` 与 `1e97650236dda8e5af09943f989e7d092610f928d58348f5f77ca191a8c3c93a`。主项目 122 个包与示例插件 52 个包的 uv 锁可离线解析；npm 与 Python 依赖审计均为 0 个已知漏洞，项目自身因不在 PyPI 而被明确跳过。
- 自动验收没有执行真实外部 push、付费模型调用、云端生成或带密钥 live smoke。IEEE Xplore、Springer Nature 等 live smoke 继续由用户在设置页按只读指南显式执行；v0.6 交付不把网络可用性误当作平台正确性的前提。

## 10. v0.8 增量验收证据

- Task 1 冻结资源与发行布局契约共 30 项精确测试通过；连同 foundation、迁移、E5 推荐与 OCR 回归共 105 项通过。测试覆盖源码、wheel、模拟冻结布局、只读/Unicode/长路径、缺失资源、关闭式 JSON、固定 revision/HTTPS、大小/哈希/许可证和发行允许/禁止清单。
- Ruff 与严格 MyPy（157 个源文件）通过。使用锁定构建环境实际生成 wheel 后检查 180 个成员，SPA、Alembic、OCR/E5 manifest、Pages workflow、应用许可证和第三方声明均存在；该 wheel 仅作为本地增量验证，不作为 v0.8 最终发行物。
- 本阶段没有下载 OCR/E5 权重，也没有运行 PyInstaller、Inno Setup 或外部发布；相关证据必须在后续 Task 6–10 单独取得。
- Task 2 foundation + packaging 组合门禁为 112 项通过、2 项因当前 Windows 账户不能创建测试符号链接而条件跳过。单实例、激活、CLI 与会话安全聚焦门禁为 58 项通过、2 项条件跳过，总分支覆盖率 83.73%；`instance.py`、Windows monitor、CLI 和会话安全分别为 81%、82%、88% 和 92%。
- 真实双进程测试运行完整 FastAPI/Uvicorn 首实例，并给第二进程注入“触碰 App 工厂或监听 socket 即失败”的探针；第二进程只通过回环激活以 0 退出。普通和覆盖率插桩环境均验证正常关停删除 `instance.json`/ready 文件，强制杀死锁持有者后验证内核释放，恢复失败则在取端口前停止。
- Windows ACL 通过实际 `Get-Acl` SDDL 检查：lock/state 均为 protected DACL 且只有当前用户 Full Access ACE。其他门禁覆盖不可继承句柄、陈旧/畸形/超限/链接状态、PID/端口复用、普通与 chunked 1 KiB 上限、严格 Origin、非回环、token 过期/重放、浏览器失败、启动/停服竞态和无 secret/path 错误。
- OpenAPI 与 TypeScript 类型已重生成，Ruff 与严格 MyPy（159 个源文件）通过；本阶段没有把真实浏览器打开、PyInstaller frozen 进程或安装版升级写成已完成证据。
- Task 2 提交前全仓 Python 回归收集 969 项：962 项通过、7 项平台/可选能力条件跳过、0 失败。OpenAPI 与前端类型连续两次生成哈希分别固定为 `79fdd8efa02882d29d380ffbb8b1140fb42ea5fb4d8992289458f4463f799782` 和 `a021094460cd207535616ac57c4e867f0ec6176ea3c8b72716aef0c13df1e4a8`。
- Task 3 模型资源聚焦门禁收集 120 项：119 项通过、1 项因当前 Windows 账户不能建立测试符号链接而条件跳过。覆盖 E5/OCR 共用关闭式清单、Windows 路径、无代理 HTTPS、逐跳 DNS/peer、重定向、响应头/正文/磁盘预算、Range/Content-Range/强 ETag、部分哈希与进程重启、暂停/继续/取消、原子发布、版本共存、活动指针、运行时激活回滚、引用删除保护、旧 OCR 任务兼容、无模型词法降级、迁移与无路径 API；下载响应全部来自本地 fixture，没有访问真实模型源。
- Task 3 提交前全仓 Python 回归收集 1014 项：1006 项通过、8 项平台/可选能力条件跳过、0 失败。Ruff 全仓、改动文件格式检查和严格 MyPy（167 个源文件）通过；27 个前端测试文件共 82 项通过，Vue/TypeScript strict 与生产构建通过。
- OpenAPI 与 TypeScript 类型连续两次重生成完全一致，SHA-256 分别为 `dc1417299c01076022c8d4c55fda868cda0983f76732c055033dc9dd290041b9` 和 `bd2085e31f7623e1000feb63344488b165410efbdadf27ee5a76a4d357007b05`。本阶段仍未把 PyInstaller onedir、模型设置浏览器闭环或干净虚拟机写成完成证据。
- Task 4 模型设置组件 6 项、任务抽屉与缓存设置组合 16 项全部通过。首屏只读、精确大小/许可/来源、下载确认、服务端进度、暂停/继续/取消、可重试与不可重试失败、显式启用、旧版清理预览/二次确认、链接 query/fragment 清除和原始诊断不渲染均有断言。模型专用任务读取不受全局最近 100 条摘要限制：在 105 个后续非模型任务存在时，旧暂停任务仍能恢复；关闭式响应不含完整 payload/checkpoint/result 或 `error_message`。
- Task 4 完整浏览器门禁为桌面 Chromium 与 Pixel 7 合计 32 项通过，其中模型生命周期闭环各 1 项。模型场景覆盖刷新后暂停恢复、继续/取消、磁盘不足稳定中文、私有路径/堆栈/query canary 不可见、旧版删除、键盘、200% 字号、窄屏、reduced motion、Windows forced colors 与 axe 零违规；普通与强制颜色截图已人工复核。PDF 页面继续使用独立白色渲染层，不随设置主题反色。
- Task 4 最终全仓 Python 回归仍为 1014 项收集、1006 项通过、8 项平台/可选能力条件跳过、0 失败；冻结资源长路径契约另以短临时根 20/20 通过。Ruff 全仓、改动文件格式、严格 MyPy（167 个源文件）、28 个前端文件共 89 项、Vue/TypeScript strict 和生产构建全部通过。
- OpenAPI 与 TypeScript 类型连续两次重生成完全一致，SHA-256 分别为 `cdc7097c5b9f058825cd9df7e6c498bdc8ae3199851b481e42fca11cb193b1c3` 和 `ceb6d68476a8f6a6abb67fbc68926ad48534e14523def5dcfb6d0e38063c6627`。本阶段没有访问真实模型源、下载模型权重、运行 PyInstaller/Inno Setup 或把干净虚拟机验收写成完成。
- Task 5 更新服务与 API 精确门禁覆盖固定 GitHub URL/请求头、无凭据与空请求体、严格 SemVer、当前/新版/服务器旧版/预发布/草稿、公开缓存重算、24 小时节流、关闭/清除、限流、离线、5xx、损坏 JSON、超限正文、重定向、跨 host URL、版本不一致和缺失清单。两次手动检查即使收到 `Set-Cookie` 也不回送 cookie；API 不存在资产下载路由且不暴露下载 URL 或原始异常。
- 更新设置组件只在首次渲染读取本地缓存，手动按钮才触发检查；页面显示当前/可用版本、发布时间、纯文本变更、迁移、能力矩阵和未签名提示。“查看并更新”先进入 Reka UI 二次确认，再只打开固定仓库 Release；恶意 host 时按钮完全不渲染。组件测试与桌面/Pixel 7 聚焦 Playwright 覆盖离线、恶意响应、键盘、200% 字号、窄屏、reduced motion、forced colors 和 axe 零违规。
- Task 5 的自动验收全部使用 `httpx.MockTransport` 与浏览器路由 fixture；Playwright 启动器显式关闭真实自动检查。没有访问真实 GitHub Release、下载发行物、运行安装器、执行迁移升级或把 PyInstaller/干净虚拟机写成已完成证据。
- Task 5 提交前全仓 Python 回归收集 1038 项：1030 项通过、8 项平台/可选能力条件跳过、0 失败；总体分支覆盖率 86.93%，核心领域分支覆盖率 95.39%（290/304）。Ruff 全仓、严格 MyPy（172 个源文件）、29 个 Vitest 文件共 94 项、Vue/TypeScript strict、生产构建与桌面/Pixel 7 全 Playwright 34/34 通过。
- OpenAPI 与 TypeScript 类型连续两次重生成完全一致，SHA-256 分别为 `b4ed37e3112ea9b3ee1f404466ac3d80e755a727d159265acf03e5c495ac3a1f` 和 `fdc81269e0e02297a1d2131632b5f788d5c1da729c3155af0c4dd33ede4f9dc9`；`uv lock --check` 与高危 npm 依赖审计通过。
- Task 6 的 spec、图标、版本资源、环境清理、原子替换、内容扫描和真实冻结包聚焦门禁为 38 项通过、1 项因当前 Windows 账户不能创建符号链接而条件跳过。真实冻结包共 963 个受清单约束的文件、`287,944,440` 字节，独立内容审计报告 x64、GUI 唯一入口、版本 `0.6.0` 与完整 SHA-256 清单均有效；唯一超过 64 MiB 的成员是声明过的 OpenCV 运行时 DLL，不含 OCR/E5 权重。
- 发行包在含中文、空格和额外长路径的目录中连续构建两次。两次文件路径、成员大小、总大小和超大文件清单完全一致；`_internal/base_library.zip` 与 `paper-reader.exe` 的内容哈希因 PyInstaller 允许的 ZIP/PE 时间戳不同而变化，其余 961 个成员哈希一致。每次构建均生成并复核新的根清单，不把跨构建 bit-for-bit 一致误写成事实。
- 真实 frozen smoke 把 PATH 限制为 System32 并确认 Python、Node 与 Git 不可发现；发行文件设为只读，可变目录全部指向隔离临时根。进程实际通过 health、SPA、OpenAPI、SQLite WAL、5 秒优雅退出和发行字节不变检查。首轮 smoke 揭示 SQLite 上下文管理器不关闭连接的 Windows 文件锁问题；失败测试锁定后改为显式关闭，重跑两次完整构建均正常清理 staging 并原子保留/替换上一成功包。
- Task 6 提交前全仓共收集 1076 项 Python 测试：1065 项通过、11 项按平台或可选真实发行环境条件跳过、0 失败；总体分支覆盖率 86.84%，核心领域分支覆盖率 95.39%（290/304）。Ruff、严格 MyPy（178 个源与脚本文件）、29 个 Vitest 文件共 94 项、Vue/TypeScript strict、生产构建、锁文件检查和高危 npm 审计均通过。
- Task 7 的默认聚焦门禁为 34 项通过、3 项条件跳过：跳过项分别要求当前账户可创建符号链接或显式提供真实发行路径。静态契约覆盖 ZIP 路径/排序/权限/复现、关闭式 Inno 指令、固定 AppId、Windows 11 x64、每用户安装、无系统集成副作用、运行中保护、降级、默认保留数据、危险清理确认、原子发布回滚、编译环境脱敏和未知退出码失败关闭。Ruff 与严格 MyPy 对 179 个源与脚本文件零问题。
- 使用 Inno Setup 6.7.3 从最新真实 onedir 编译安装器。该 onedir 共 1,035 个清单成员、`290,496,855` 字节；便携 ZIP 共 1,036 个成员、解压后 `290,651,570` 字节、压缩后 `120,274,626` 字节。两次独立生成的 ZIP SHA-256 均为 `2c8f64ec1c5de0c7079f780f64a174d5d6e0ba0b13725caf696d379ff01bc81e`，证明同一输入逐字节复现；该哈希先作为 Task 7 本机验收证据，随后由 Task 8 纳入封闭发布清单。
- ZIP 已解压到含中文和空格的隔离目录，并以程序目录只读、独立 state/cache/library、隐藏浏览器和无外部 Python/Node/Git 依赖的方式通过 health、SPA、OpenAPI、WAL 与正常退出 smoke；解压字节与源 onedir 逐成员大小/SHA-256 完全一致。
- 真实静默安装生命周期测试单项用时约 52 秒并通过：安装退出 `0`，ARP 版本/位置/卸载命令正确，开始菜单目标为安装根 `paper-reader.exe serve`；安装版持有资料库锁时，便携版只激活已有实例并以 `0` 退出，安装器被运行标记以非零码阻止且不改程序字节。随后同版本重装恢复被篡改的许可文件并保留未知 canary，伪造更高已安装版本使降级在复制前以非零码停止；静默卸载退出 `0`，移除程序/ARP/快捷方式而保留隔离用户数据与未知文件。该测试不构成跨版本升级证据。
- 生命周期测试开始前确认固定 HKCU 卸载键不存在，结束后再次确认卸载键、测试快捷方式和 `paper-reader` 进程均为零；没有触碰默认用户资料库。Task 7 验收时安装器仍是未签名的 `0.6.0` 开发预览；其 SBOM 与封闭 SHA-256 清单已由 Task 8 补齐，干净 GitHub runner、Windows Sandbox 与正式发布仍属于 Task 9–10，不能由本机证据替代。
- Task 7 提交前全仓收集 1,105 项 Python 测试：1,091 项通过、14 项平台/可选真实产物条件跳过、0 失败；总体覆盖率 86.84%，核心领域分支覆盖率 95.39%（290/304）。Ruff、严格 MyPy（179 个源与脚本文件）、29 个 Vitest 文件共 94 项、Vue/TypeScript strict、生产构建、`uv lock --check` 与高危 npm 审计均通过，npm 已知漏洞为 0。
- Task 8 以真实 Task 7 onedir 和 Inno Setup 6.7.3 生成一个本地 `0.6.0` 元数据候选。封闭目录恰有 10 个文件：安装器 `86,931,213` 字节、便携 ZIP `120,274,626` 字节、两份 SBOM 和六份公开说明/清单。便携 ZIP SHA-256 仍为 `2c8f64ec1c5de0c7079f780f64a174d5d6e0ba0b13725caf696d379ff01bc81e`，证明元数据阶段没有重写 Task 7 字节。
- Python CycloneDX 1.6 SBOM 从实际冻结构建 venv 生成，含 104 个组件和 105 个依赖节点；npm SBOM 从精确锁文件离线生成，含 291 个组件和 292 个依赖节点。两者缺失许可证数均为 0，并分别通过官方严格 schema、根组件、唯一 bom-ref 与完整依赖图复核；同一输入重复生成逐字节一致。
- manifest 与 `paper-reader-update-manifest:v1` Release 注释块来自同一事实，`SHA256SUMS.txt` 重算除自身外全部九个文件。独立验证器已对真实候选通过，并由篡改测试证明缺失、额外、重复、大小写冲突、链接、CRLF、大小、哈希、SBOM schema/图/许可漂移全部失败关闭。
- 当前本机候选的 manifest SHA-256 为 `6157f650f37ef87ab7e8003d1fab3add109113b2fe35bbf703be8e272bc22c7b`，`SHA256SUMS.txt` SHA-256 为 `3c03cf9b93805f45ef3aa0e75af0b36bc5a2a1e18c607966904397f4e52577d0`；两次独立验证结果一致。Task 8 聚焦门禁为 39 项通过、3 项条件跳过；全仓为 1,111 项通过、14 项条件跳过、0 失败，总体覆盖率 86.86%，核心领域分支覆盖率 95.39%（290/304）。Ruff、严格 MyPy（181 个源与脚本文件）、29 个 Vitest 文件共 94 项、TypeScript strict、生产构建、锁文件和 npm 高危审计全部通过，npm 已知漏洞为 0。
- 该候选仍是未签名 `0.6.0` 本机开发证据，不是 v0.8 Release。正式 `paper-reader@0.8.0` 根组件、干净 GitHub runner、Windows Sandbox、版本推进和 draft Release 均属于 Task 9–10；本机哈希不能替代这些证据。

## 11. v0.9 增量验收证据

论文库性能使用固定种子脚本与独立 20k 正确性门禁。20 万外部门禁的环境、命令、原始样本和 `EXPLAIN QUERY PLAN` 统一记录在 [性能基线](performance-baselines.md)；开发机数据不得替代 16 GB 干净虚拟机终验。

- 用户偏好与首次配置迁移/API 聚焦测试覆盖系统时区、自选 IANA、Agent/模型/推理默认值、报告与翻译语言、失败回退、严格请求、会话/CSRF、计划时区冻结、数据库备份、UTC 跨月与 DST 边界；Assistant 迁移另验证七表规范化、唯一幂等键、级联边界、不可变触发器和可逆降级，翻译迁移验证四表规范化、内容哈希、语言/范围闭集、唯一键与可逆降级，相关推荐迁移验证论文/网站结果分离、确认与联网同意、来源检查点、验证状态、身份唯一键及可逆降级，旧会话移除迁移验证四张旧表不存在且兼容 API 只读写 Assistant。数据库头、迁移快照、Windows 工作流和干净机发行清单必须共同指向 `0038_remove_legacy_conversations`。
- 相关推荐的外部知识资源门禁还覆盖显式持久化同意、后端联网能力、封闭输出、提示注入转义、公共 DNS 全答案、IP 固定 TLS SNI、证书与发送前 peer 校验、逐跳重解析、五跳上限、HEAD 到 64 KiB Range GET 降级、单一多地址超时、短链/搜索页/镜像拒绝、部分成功和 12 条结果上限；自动化只使用假 Agent、假 DNS 和内存流，不进行真实联网或产生云端用量。
- 身份候选门禁使用固定 20k 正确性样本证明相似度调用不超过 256，并验证 FTS 正常路径、标题前缀/年份降级索引和退化标记；固定 20 万外部门禁记录 20 次完整 ingest 原始样本、候选数量和查询计划。开发机 p95 只作回归证据，仍须在 16 GB 干净机复跑。
- 性能 Task 2 最终全量门禁共收集 1,212 项 Python 测试：1,198 通过、14 条件跳过、0 失败；总体分支覆盖率 86.90%，核心领域分支覆盖率 93.56%（363/388）。Ruff、严格 MyPy（201 个源与脚本文件）、锁文件和差异空白检查通过。
- 性能 Task 3 使用实际 source-free PyInstaller onedir 分别保留 20 次冷资料与热资料进程样本。冷资料 SPA ready p95 为 3547.832 ms，热资料为 2553.495 ms，均通过 8 秒预算；脚本同时验证清单、`_internal`、API 和 SPA，拒绝源码入口或不足 20 次的伪基线。
- 生产构建 Chromium 分别保留 20 次冷/热浏览器 context 样本，从实际点击“阅读”测到首屏 canvas 非透明像素和可选择文本。冷 canvas/text p95 为 194.6/202.4 ms，热为 178.7/186.8 ms；测试附加完整结构化原始证据，2.5 秒 canvas 门槛通过。
- 取消路径先以 RED 证明旧实现没有立即可恢复事件，随后将首次 `cancel_requested` 与 `job.cancel_requested` 同事务提交；20 次完整 FastAPI/会话/CSRF/SQLite/事件轮询样本 p95 为 4.771 ms、最大 5.710 ms，通过 2 秒反馈预算，重复取消不重复事件。以上均为开发机回归证据；16 GB 干净 Windows 11 复跑和各长任务协作停止仍未关闭。
- 性能 Task 3 收口时全仓共收集 1,217 项 Python 测试：1,203 通过、14 项平台/可选真实产物条件跳过、0 失败；总体分支覆盖率 86.89%，核心领域分支覆盖率 93.56%（363/388）。Ruff 全仓、严格 MyPy（202 个源与脚本文件）、Vue/TypeScript strict、生产构建、PDF 性能 Playwright 和差异空白检查通过。
- 安全 Task 4 先以 11 个 Python 红灯和 4 个浏览器红灯锁定页几何、块/图像/字符、OCR 页/PNG/行/字符、累计预算、软时限与 PDF.js 安全选项；实现后提取/OCR/生成型恶意语料聚焦门禁为 94/94。运行时语料由最小对象图生成，覆盖小文件大解压文本、巨型 MediaBox、自引用页树、加密和超页数，不提交外部二进制 PDF。
- 完整 `tests/reader + tests/security` 为 261 项通过、2 项可选环境条件跳过、0 失败；36 个前端文件共 120 项 Vitest、浏览器适配器与阅读器组件聚焦 14/14、Vue/TypeScript strict 通过。三视口真实阅读/存储 Playwright 为 9/9，覆盖合法导入、阅读、版本迁移、缓存清理、取消与缺失文件路径脱敏。超限测试同时证明数据库不发布部分提取/OCR 结果、PDF.js 不分配荒谬画布并释放全文搜索的非活动页缓存。
- Task 4 当时记录的原生硬终止残余已在安全 Task 6 关闭：PyMuPDF/RapidOCR 生产路径进入短命 worker，一个 deadline 覆盖启动、stdin 写入、状态、结果和退出；Windows Job Object 负责活动进程、1 GiB 内存和关闭杀树。结果先到但进程不退、从不读取 stdin、原生卡死和取消夹具都在 2 秒内终止并清理。该边界仍是同用户资源/协议隔离而非 AppContainer，冻结候选必须在干净 Windows 11 复验。
- Windows Actions run `29411199826` 在 `ef4721f` 上通过全质量门、审计发行构建和 source-free 安装/便携 smoke。收据确认离线启动、安装/同版本重装/卸载、二次实例、崩溃锁、程序字节不变与数据保留；完整 artifact 为 203,575,900 字节，GitHub SHA-256 摘要 `0e3e1a6fc377f124b9e8e3ff9f92174cb1c77411bc928cabc4bae8619aee3434`。PR 运行按设计跳过 draft Release；hosted runner 成功不等同于跨版本升级或 Windows 11 Sandbox/干净 VM 终验。
- 最终本地全仓门禁为 1,230 项通过、14 项平台/可选真实产物条件跳过、0 失败；总体覆盖率 86.98%，核心领域分支覆盖率 93.56%（363/388）。完整 platform 回归为 306 项通过、2 项条件跳过。确定性故障注入证明恢复 journal 的原子发布会吸收 Windows `winerror` 5/32 的短暂占用，但一秒上限和所有非瞬时错误仍立即进入安全回滚/待重试语义；启动器测试使用独立临时 profile，不依赖或修改用户正在运行的资料库锁。
- 数据完整性 Task 5 的聚焦关系回归为 160 项通过、2 项条件跳过：八个受支持迁移边界前向升级、迁移快照两次原子替换、备份最终发布、RecommendationSet/Work 合并提交前后故障、恢复金样、跨格式重复导入、批注决定和外键矩阵全部通过。每个故障窗口只留下旧状态或完整新状态，恢复重试幂等；凭据不进入备份或恢复数据库。
- Task 5 最终全仓门禁为 1,247 项通过、14 项平台/可选真实产物条件跳过、0 失败；总体覆盖率 86.98%，核心领域分支覆盖率 93.56%（363/388）。Ruff 全仓、严格 MyPy（202 个源与脚本文件）、本次测试格式和差异空白检查通过。Windows 本地全仓命令必须使用短 `--basetemp`：深工作树临时根会与资源契约的故意长路径叠加到 261 字符并在 fixture 创建前失败；242 字符短根下该项及全仓均通过，因此不得把该测试夹具路径错误归类为产品回归。
- 安全 Task 6 聚焦门禁为 `tests/security` 24 项、原生隔离/提取/OCR 107 项，以及会话/日志/API、插件权限、精选隐私、提示注入与工作流契约 82 项，全部通过且集合不相加。测试覆盖过期会话重放、无提前退出的成员比较、最终 formatter 脱敏、控制字符日志伪造、跨页对象放大、完整 worker 生命周期超时/取消、可信 DLL 搜索路径、Windows Job 进程/内存边界、发布 canary 和严格防火墙失败语义。最终全仓一次收据为 1,273 项通过、14 项条件跳过、0 失败，总体覆盖率 86.21%，核心领域分支覆盖率 93.56%（363/388）；完整结论见 [v1 安全边界审计](security-audit-v1.md)。
- 同一 `codex/**` push 与 PR 不再双触发主 CI；打包 PR 只执行真实构建/source-free 验证，完整质量 job 只在手动或 tag 运行。账户 Actions 分钟耗尽期间不推送、不 rerun，所有 Task 6 新证据均为本地结果；共享额度能阻止另一个仓库的 job 启动或后续 job 继续，不能造成仓库间文件污染。
- 主题与时区组件覆盖服务器事实、保存失败不乐观更新、非敏感主题缓存和回退说明。Playwright 在桌面 Chromium 与 Pixel 7 验证缓存首帧、服务端校准、浅色/深色/跟随系统、保存刷新、PDF 白底和 axe 零违规；测试真实发现并修复深色主按钮和浅色选中主题卡片的对比度问题。
- Task 9 最终门禁为 1181 项 Python 通过、14 项平台/可选真实产物条件跳过、0 失败；36 个前端文件 116 项 Vitest、38 项桌面/移动 Playwright、Ruff、严格 MyPy（192 个源文件）、TypeScript strict、生产构建、`uv lock --check` 和 OpenAPI/类型连续生成确定性全部通过。
- v0.9 Task 10 将 Playwright 固定为桌面 1440×900、中等 900×900 与 Pixel 7 三视口；全量 63/63，个性化与无模型主流程聚焦矩阵 9/9。验收同时覆盖键盘关闭/启用偏好上下文、200% 字号无页面级横向滚动、浅色/深色/跟随系统、reduced motion、forced colors 和 axe 零自动违规。最终 Python 仍为 1181 通过、14 条件跳过、0 失败；36 个前端文件 116 项 Vitest、生产构建、TypeScript strict、Ruff、严格 MyPy（192 个源文件）、锁文件与 npm 高危审计通过，已知 npm 漏洞为 0。
- 隐私与无障碍 Task 7 的聚焦门禁为：备份/恢复 88 项通过、1 项条件跳过；发布白名单、偏好与 Agent 证据 31 项通过；App/AppShell/BackupSettings 12 项 Vitest；ReaderWorkspacePage 4 项；主壳层与备份三视口 18 项 Playwright；真实 PDF.js reader 三视口 6 项。测试先锁定查询参数抢焦点和阅读器无方向键两个缺陷，再验证页面路径焦点、单一 Tab 停靠点、方向键激活、报告/聊天保留和移动 PDF 位置。
- Task 7 完整本地收据为 1,273 项 Python 通过、14 项平台/可选真实产物条件跳过、0 失败，总体覆盖率 86.17%，核心领域分支覆盖率 93.56%（363/388）；36 个前端文件 122 项 Vitest 通过；Playwright 共 69 项，其中 67 项通过、2 项桌面性能测试在中等/移动项目按设计跳过。桌面 PDF 冷浏览器上下文完整首屏 p95 为 406.4 ms。Ruff、严格 MyPy（204 个源与脚本文件）、TypeScript strict、生产构建、OpenAPI/类型确定性、真实 `sqlite-vec` 可选运行时和 npm 中危审计全部通过，npm 已知漏洞为 0。
- 自动化不冒充最终无障碍与产品验收：NVDA/Narrator 中文宣告、浏览器 400% 页面缩放、Windows 高对比度逐屏检查、触控/软键盘和干净 Windows 11 安装生命周期仍需独立人工收据。构建仍报告 Reka UI 第三方 pure-annotation 提示和 596.67 kB 主 chunk；当前行为与 PDF 性能门槛通过，但干净机冷启动终验必须继续观察该项。本轮未调用远端 Actions。
- Python 全仓输出还包含 Python 3.12 `sqlite3` 默认 datetime adapter 经 SQLAlchemy 触发的弃用警告；固定的 3.12 发行运行时不受当前功能影响，但升级 Python 前必须切换并迁移到显式 adapter/converter，不能通过隐藏 warning 解决。

## 12. v0.9.0 本地发行候选收据

精确提交 `b030990c01296d6984a16c94aa4d9cf007ca0ea5` 已在隔离构建环境生成 `0.9.0` source-free onedir、便携 ZIP、未签名安装器和两套 SBOM。它包含 Europe PMC 空壳响应修复及严格 live-smoke 本地过滤，替代 `fa88859` 与更早的 `7b11c73` 本地产物；候选仍是本地预览，不是 tag、GitHub Release 或 v1.0。

- 完整代码门禁收集 1,311 项 Python 测试：1,297 项通过、14 项条件跳过、0 失败；总体覆盖率 86.17%，核心领域分支覆盖率 93.56%（363/388）。Ruff、严格 MyPy（205 个源与脚本文件）、36 个前端文件 122 项 Vitest、TypeScript strict、生产构建、三视口 Playwright/axe 67 项通过及 2 项按设计跳过。桌面 PDF 冷上下文完整首屏 p95 为 `353.7 ms`。
- `uv lock --check`、OpenAPI/TypeScript 类型确定性与 `npm audit --audit-level=moderate` 通过，npm 已知漏洞为 0。`uv.lock` 与 `package-lock.json` 相对已完成精确 Python 审计的上一候选没有 Git blob 变化，因此基础依赖和全部 extras 的 0 漏洞收据仍适用。
- 10 文件发行目录的 manifest SHA-256 为 `b63afc99985b6f61ffda11765454847dcf8b1dbebd925fd6d80399aac9b4a333`，`SHA256SUMS.txt` SHA-256 为 `a8d539bacbd089364eef065846ee7e0259851598f12942958d5ce44ddbb4c052`。便携 ZIP 是 `118,374,075` 字节、SHA-256 `834c5163505fa9b9b70f91121abdd9d7fe0629b7f56333426fa91a88bd8d6dc9`；安装器是 `85,347,061` 字节、SHA-256 `1719e3ab81bdf81b5c2e16e3a2e01a2ed76073f2cb727f8ab0e810021278ee22`；独立 verifier 在构建后和 clean-smoke 诊断后均通过。
- Python SBOM 是 CycloneDX 1.6 的 105 个组件/106 个依赖节点，npm SBOM 是 291 个组件/292 个依赖节点；根组件均为 `paper-reader@0.9.0`，缺失许可证数均为 0。
- source-free onedir 含 970 个文件、`288,259,601` 字节，bundle manifest SHA-256 为 `579351c68237592a1dad75a9d31df16262b35e022c06e0a977ef78a9e3ef95a23`。冻结启动 smoke 已验证实际数据库 revision、API 代际、OpenAPI 哈希、插件协议、SPA、OpenAPI 路由和 SQLite WAL。
- 候选专属 packaging 回归先有 223 项通过、5 项环境条件跳过；真实安装生命周期因测试根不在安全白名单而在安装前关闭失败，改用契约允许的全新 `.tmp` 根后只精确重跑该项并通过。最终没有未解决失败，验证了静默安装、便携激活、运行中保护、同版本修复安装、降级阻止、卸载和用户数据保留；它不构成跨版本升级或干净机证据。
- 同一 onedir 保留 20 次冷资料和 20 次热资料样本，另有 2 次热身。SPA p95 分别为 `4,215.731 ms` 与 `2,703.063 ms`，均通过 8 秒门槛；“冷资料”只表示每次使用新的 state/cache/library，不冒充清空操作系统文件缓存。
- 加固后的严格 clean-smoke 对新候选只运行一次。收据在验证 manifest 后以 `firewall_requires_administrator` 失败关闭，并同时绑定候选 `release_manifest_sha256=b63afc99985b6f61ffda11765454847dcf8b1dbebd925fd6d80399aac9b4a333` 与验收脚本 `harness_sha256=aadd179652a62055506fc46c4152e552b5b6a330acca3b0150c2d49a610c7e33`。当前非管理员执行没有进入便携或安装生命周期，不能替代管理员、防火墙隔离、无外部运行时的干净 Windows 11 证据，也不再重试。
- arXiv 最小请求和包含双语 OR、ANDNOT、分类过滤的应用 httpx 请求均成功；复杂查询估算 3,321 条。先前 `remote_5xx` 来自临时 PowerShell 管道把中文转成 `???`，不是应用 UTF-8 请求。Europe PMC live smoke 发现无效 `sort=UPDATE_DATE desc` 会得到只有版本号的空壳 JSON；红灯锁定后移除该参数并要求 `hitCount`/`resultList.result` 完整。脱敏工具对最多 50 条远端候选再次执行月界与已编译关键词过滤；Europe PMC `2026-06 / cancer` 估量 101,535 条并返回 3 条严格月内样例，arXiv `2026-07 / agent` 返回 3 条，OpenReview `2026-05 / agent` 返回 2 条。Hosted-runner、IEEE/Springer 合法 key 和人工无障碍/干净机生命周期收据按 [v1.0 发布门禁与已知限制](rc-blockers.md) 透明列为未验证的非阻断项。未触发 GitHub Actions。

## 13. v1.0.0 本地发行收据

精确提交 `b3490b04f5e4a411065c9156f8cda99e2cdb4ed1` 已完成最终本地门禁并生成 `1.0.0` source-free onedir、便携 ZIP、未签名安装器和两套 SBOM。

- 全量 Python 为 1,299 项通过、15 项条件跳过、0 失败，总覆盖率 85.99%，核心领域分支覆盖率 93.56%（363/388）。Ruff、严格 MyPy（205 个源与脚本文件）、36 个前端文件 122 项 Vitest、TypeScript strict、生产构建、Playwright/axe 67 项通过及 2 项按设计跳过。
- Python 基础与全部 extras 经 PyPI、全部 extras 再经 OSV、npm 经 low 阈值审计，已知漏洞均为 0。Bandit medium/high 为 0；当前树 50 个和完整 Git 历史 23 个秘密候选全部分类。
- 10 文件发行 manifest SHA-256 为 `d61fbc4ede839b0b67f0139e0347e245499771c2307d2cdd3ace6d8539fe265e`，`SHA256SUMS.txt` SHA-256 为 `4dbdc37ce19c9131ccef04076ac90c92516bc0b22e0b90cd469f3cd1e45c2fdc`。便携 ZIP 为 `118,375,253` 字节、SHA-256 `e9efe3fcbd0ff75c6770ebe4bb2e92ed73b6266bd1eea607164ce1dbc3ec00c3`；安装器为 `85,346,842` 字节、SHA-256 `767eef9e569543c07bd54cc02f838a5ac03fd60a81f82a1aa550990ab60a0d13`。
- source-free onedir 含 970 个文件、`288,260,042` 字节，bundle manifest SHA-256 为 `30731142e25f8c7940afb0a51d54a8b9f9475e1f87f621614d1ca0aee533bea7`。Python/npm SBOM 分别为 105/106 与 291/292 个组件/依赖节点，根组件均为 `paper-reader@1.0.0`。
- 制品感知回归为 229 项通过、5 项条件跳过、0 失败；真实同版本安装生命周期与 `0.9.0→1.0.0` 当前用户范围覆盖升级均通过，结束后没有进程、卸载注册或测试目录残留。
- 严格 clean-smoke 同时绑定当前 manifest、历史 manifest 与脚本 SHA-256 `aadd179652a62055506fc46c4152e552b5b6a330acca3b0150c2d49a610c7e33`，在非管理员环境按设计返回 `firewall_requires_administrator`，没有创建规则或写入成功收据。
- 启动、PDF、20 万列表/检索、身份写入与取消反馈全部低于固定门槛。完整数值、哈希、边界和未验证外部项见 [v1.0.0 本地发行收据](v1-local-release-receipt.md)。

该验证轮次没有 push、tag、GitHub Release 或 Actions 调用。2026-07-17 随后创建的本地 annotated tag `v1.0.0` 精确指向冻结提交，但没有推送或触发工作流。当前本地制品已可用于个人验证和使用；公开远端发布仍须在额度恢复后按“`main` CI 成功 → 推送 tag → Windows 打包链成功 → 核对 draft 资产与匿名公开访问”的顺序完成。

## 14. v2 发行候选门槛

v2 的前端预算把初始 JavaScript 闭包限制为 `350,000` 字节，并在桌面 `1440×900`、中等 `900×900`、`Pixel 7` 三视口维护 **24 张**人工审阅的视觉基线。Assistant E2E 必须覆盖选区提问、解释、英文到简体中文、相关推荐确认、联网拒绝/同意、刷新恢复和正式报告隔离。

仓库工作流契约要求**全 PR Windows** 质量门与独立 source-free 打包验证；GitHub Actions 额度耗尽期间只做本地与静态验证，不推送、不 rerun，也不把未运行的远端 job 写成成功。最终 v2 收据必须分别记录 Python、前端、Playwright、无障碍、安全审计、依赖锁、制品哈希、SBOM、安装生命周期与明确未验证的外部条件。

### 14.1 封闭安全审计

`npm run audit:security` 是 Windows PowerShell 5.1 可运行的只读入口：先验证 `uv.lock`，同步锁定的基础依赖及 `dev/models/ocr/packaging` 四组 extras，执行 `uv pip check`，再从锁文件导出带哈希且排除本项目自身的第三方清单。`pip-audit` 以 strict、禁用二次解析方式审计该清单；npm 从 `package-lock.json` 审计且最终要求所有严重度合计为零；Bandit 扫描 `src` 与 `scripts` 的 medium/high 项。脚本不提供自动修复，不打印环境变量、凭据、论文路径或命中文件内容，JSON 缺失或不可解析时关闭失败。

2026-08-01 的本地收据为：132 个锁定 Python 第三方包 0 个已知漏洞，npm 0 个已知漏洞，Bandit 0 个 medium/high 命中；提示注入、SSRF/重绑定、插件权限、恶意 PDF 和原生进程隔离聚焦集合 84 项全部通过。Bandit 首轮曾报告缓存清理中的 1 个低置信度动态列名查询；列名原本受固定白名单限制，但仍改为三个完全字面量 SQL 并通过 16 项缓存清理回归（另 1 项按平台条件跳过）。这些结论只表示审计时没有已知命中，不承诺未来公告状态不变。本轮没有推送或触发 GitHub Actions。

### 14.2 v2.0.0 封闭本地制品

精确提交 `0c753a5df25690ac4a3de292aab6db9850eae8f2` 的 release 质量门、source-free onedir、portable、未签名安装器、CycloneDX SBOM 和十文件发行目录均已在 Windows 11 build 26200 本机生成并复验。manifest SHA-256 为 `273dc4796101433b923809e21e96d8a52c3acfe321edf51c90e95ec66c1f3a0c`，`SHA256SUMS.txt` SHA-256 为 `4fdc992b77873234d48096b70d8653ada12e70bca6946e2048cc7ed85600477e`。

最终本地 `npm run quality` 收集 1,653 项 Python 测试：1,638 项通过、15 项条件跳过、0 失败，总覆盖率 85.06%，核心领域分支 93.59%（365/390）；Ruff、264 个源与脚本文件的严格 MyPy、96 个 Vitest 文件 242 项、TypeScript strict、生产构建和 112/8 的 Playwright/axe 矩阵通过。`npm run build:budget` 再次确认首屏闭包 `236,124 / 350,000` 字节。

真实安装生命周期 2 项通过；严格 clean-smoke 正确验签 v2 与历史 v1 后，在非管理员防火墙边界以 `firewall_requires_administrator` 失败关闭且零残留。两套发行身份通过不等于已经执行 `1.0.0 → 2.0.0` 覆盖安装，非隔离安装通过也不等于离线 Sandbox 通过。冷/热资料各 20 次 source-free SPA p95 为 `4,817.774 / 3,637.587 ms`。制品与脱敏证据的路径、用户名、`.codex`、凭据和个人数据扫描为 0 命中；两处 `prompt` 文本只是 CHANGELOG 说明。

完整制品字节、SBOM 图、生命周期范围和未验证条件见 [v2.0.0 本地发行收据](v2-local-release-receipt.md)。当前 Actions 分钟已耗尽，没有 push、rerun、tag、PR 或 GitHub Release；本地门禁不能冒充 hosted runner、管理员防火墙、干净 VM、SmartScreen、人工辅助技术或 Authenticode 收据。
