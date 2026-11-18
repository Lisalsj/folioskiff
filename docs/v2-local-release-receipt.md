# paper-reader 2.0.0 本地发行收据

日期：2026-08-01

发行类型：个人开源、本地验证、unsigned（未签名）installer（安装器）与 portable（便携包）

制品冻结 Git commit：`0c753a5df25690ac4a3de292aab6db9850eae8f2`

本文记录 `paper-reader 2.0.0` 的 Windows 11 x64 本地候选。它把源码、封闭制品、SBOM、真实安装生命周期、性能和隐私检查绑定到可复核的哈希；它不是 GitHub hosted runner、Windows Sandbox、管理员防火墙、干净虚拟机、Authenticode 或人工辅助技术收据。

## 1. 结论

- `npm run quality:release` 已从冻结源码成功完成锁文件、Ruff、严格 MyPy、Vitest、TypeScript、生产构建、Python 全量测试、覆盖率和 PyInstaller source-free 构建门禁。
- Python 132 个锁定第三方包、npm 锁文件和 Bandit medium/high 在 2026-08-01 的审计中均为 0 个已知命中；安全聚焦集合 84 项通过。这里的“无已知漏洞”不承诺不存在尚未披露的问题。
- 封闭发行目录恰有 10 个文件，独立发行验证器和 onedir 内容验证器再次通过；安装器、portable ZIP、两份 CycloneDX 1.6 SBOM 与校验清单来自同一冻结提交。
- 本机真实静默安装生命周期 2 项通过，覆盖 PE 身份、安装、运行中保护、同版本修复、降级阻止、卸载、数据保留和 portable 二次激活。
- 严格 clean-smoke 已验证当前 v2 与历史 v1 的两套完整发行身份，然后因当前终端没有管理员防火墙权限，以 `firewall_requires_administrator` 关闭失败；没有跳过联网隔离，也没有把它写成离线成功。
- 制品与可分享证据的隐私扫描没有工作区、Windows 用户名、`.codex`、凭据形态、个人论文或提示词正文泄漏。
- 本轮未推送、未创建 tag、未创建 PR、未发布 GitHub Release，也未触发或重试 GitHub Actions。

## 2. 发行身份

| 项目 | 值 |
|---|---|
| 版本 | `2.0.0` |
| Git commit | `0c753a5df25690ac4a3de292aab6db9850eae8f2` |
| API | `v1` |
| OpenAPI SHA-256 | `99a65f26f8227bbdcbcc90ba29b9b1a08762575d2fe16e082be32ebf6fa40ddd` |
| 数据库 head | `0038_remove_legacy_conversations` |
| 插件协议 | `1.0` |
| release-manifest SHA-256 | `273dc4796101433b923809e21e96d8a52c3acfe321edf51c90e95ec66c1f3a0c` |
| `SHA256SUMS.txt` SHA-256 | `4fdc992b77873234d48096b70d8653ada12e70bca6946e2048cc7ed85600477e` |
| 签名 | unsigned；没有 Authenticode |

制品冻结后只提交了两个不进入安装包的验收工具修复：`6baa88a` 让历史升级输入使用自己的冻结清单，`cd4c5d8` 让启动基准默认隐藏绝对路径。它们不改变上述十文件集合；所有制品复验仍返回冻结 commit 和同一 manifest 哈希。

### 主要制品

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| `paper-reader-2.0.0-windows-x64-portable.zip` | 118,919,999 | `a3097c43596ed5438b81e7f181376420f8ad4e3b33a007ae7c3d4080452ebb9c` |
| `paper-reader-2.0.0-windows-x64-setup.exe` | 85,789,018 | `72f3bc3a2ca8cfc30381ed15cb940bfc50010d0edf2bcb0ed733e70ccd6d3420` |
| `paper-reader-2.0.0-python.cdx.json` | 108,858 | `2f64deb5f1e8eb53d0a74e08b550c2b77edff6a83283212c593605357cbf2399` |
| `paper-reader-2.0.0-npm.cdx.json` | 182,994 | `c5dca217b0841b769658e0d14b7f92590e73342de11a74cca316e4873346273c` |

source-free onedir 含 1,051 个文件、`289,020,827` 字节，内部 bundle manifest SHA-256 为 `705d894591f1a0109f3f3698e2c9eb2a5a44b417760b6573f8c932f77a330db7`。portable ZIP 含 1,052 个成员；额外一层只是稳定的 `paper-reader/` 根目录。

### SBOM

| 生态 | 组件 | 依赖节点 | 缺失许可证 | 根组件 |
|---|---:|---:|---:|---|
| Python | 133 | 134 | 0 | `paper-reader@2.0.0` |
| npm | 291 | 292 | 0 | `paper-reader@2.0.0` |

两份 SBOM 均为 CycloneDX 1.6。摘要用于字节完整性校验，不能替代发行者身份签名。

## 3. 质量、安全与浏览器证据

发行质量门禁的冻结结果：

| 门禁 | 本地结果 |
|---|---|
| Python | 1,638 项通过、15 项条件跳过、0 失败；总体覆盖率 `85.06%`，核心领域分支 `93.59% (365/390)` |
| Python 静态检查 | Ruff 通过；严格 MyPy 通过 264 个源与脚本文件 |
| 前端 | 96 个 Vitest 文件、242 项测试通过；TypeScript strict 与生产构建通过 |
| 浏览器与 axe | 112 项通过、8 项按视口设计跳过、0 失败；24 张三视口视觉基线通过 |
| 安全审计 | 132 个 Python 第三方包 0 个已知漏洞；npm 0；Bandit medium/high 0 |
| 安全回归 | 提示注入、SSRF/重绑定、插件权限、恶意 PDF、原生进程隔离 84 项通过 |
| 锁与制品 | `uv lock --check`、`uv pip check`、npm audit、封闭发行和 onedir 复验通过 |

条件跳过不掩盖核心产品失败：8 个 Playwright 跳过项仅关闭中等/移动视口的桌面硬件性能测量；15 个 Python 跳过项来自明确的平台、可选真实密钥/模型或制品条件。全量共收集 1,653 项 Python 测试。

## 4. Windows 生命周期

`scripts/build_release_metadata.py verify` 返回 10 个文件、版本 `2.0.0`、冻结 commit、`signed=false` 和上述两个发行摘要；`scripts/verify_distribution.py` 带当前构建根禁入参数验证 onedir 的架构、GUI 入口、逐文件哈希、许可证、顶层布局、模型/缓存/用户数据禁入和构建路径泄漏。

真实非隔离本机测试为 `2 passed in 47.59s`：

- 安装器 PE 的 ProductName、版本和 publisher 与冻结元数据一致；
- 静默安装成功，安装树逐字节对应同一 onedir；
- 运行中覆盖被阻止，同版本修复安装成功，人为更高版本的降级被阻止；
- 卸载成功，程序目录与注册项移除，测试用户数据保留；
- 已安装实例运行时，从 portable 启动的第二进程成功激活既有实例并退出。

严格 source-free clean-smoke 同时固定当前 manifest `273dc479...f3a0c`、历史 v1 manifest `d61fbc4e...265e` 和 harness SHA-256 `3f7cc7ce822a728c5df4c4589fed17fbbfc2f8d7ea61a52c0c4f11fb8b885125`。它完成两套十文件清单、9 项摘要、SBOM、portable 成员、x64 onedir 与无外部 Python/Node/Git PATH 的验证后，在启动前创建出站阻断规则时返回：

```json
{"schema_version":"paper-reader-clean-smoke-evidence-v1","ok":false,"error_code":"firewall_requires_administrator"}
```

退出后 `paper-reader` 进程、固定 HKCU 卸载项、clean-smoke 临时目录和测试防火墙规则均为 0。当前机器没有启用 Windows Sandbox，当前终端也不是管理员；因此没有把离线启动或 `1.0.0 → 2.0.0` 覆盖升级写成通过。

## 5. 性能

本机为 Windows 11 build 26200。source-free `paper-reader.exe` 使用 20 次新 state/cache/library 和 20 次复用资料样本，热资料前另有 2 次热身；证据只记录文件名，不记录绝对路径。

| 场景 | 中位数 | p95 | 最大值 | 门槛 | 结果 |
|---|---:|---:|---:|---:|---|
| 冷资料 SPA 完整启动 | 4,530.481 ms | 4,817.774 ms | 4,933.823 ms | 8,000 ms | 通过 |
| 热资料 SPA 完整启动 | 3,577.436 ms | 3,637.587 ms | 3,660.887 ms | 8,000 ms | 通过 |
| 20 万库最慢列表/检索/身份项 | — | 287.809 ms | 294.271 ms | 800 ms | 通过 |
| 阅读简报可交互代理 | 205.100 ms | 371.600 ms | 404.500 ms | 800 ms | 通过 |
| PDF 冷上下文完整首屏 | 293.650 ms | 510.100 ms | 640.500 ms | 2,500 ms | 通过 |
| PDF 热上下文完整首屏 | 159.100 ms | 192.900 ms | 194.500 ms | 2,500 ms | 通过 |
| 取消请求至事件可恢复 | 3.350 ms | 4.099 ms | 6.323 ms | 2,000 ms | 通过 |

阅读简报与阅读器 CLS 均为 0，生产首屏 JavaScript 闭包为 `236,124 / 350,000` 字节。“冷资料”不声称清空操作系统文件缓存；本机结果也不替代 16 GB 普通 SSD 干净 Windows 11 复验。

## 6. 隐私检查

- 十文件发行集的文本元数据对工作区两种路径形式、Windows 用户名、`.codex`、常见 key/token 形态均为 0 命中。
- `CHANGELOG.md` 的两处 `prompt` 命中分别描述版本化提示模板和提示注入隔离，不包含实际提示词、用户输入或论文正文。
- portable 的 1,052 个成员名没有工作区、用户名、`.codex`、凭据、PDF/BibTeX、数据库或个人论文文件名命中；onedir 验证器同时拒绝应用 SQLite、模型缓存、凭据目录、Git/Python/Node 工具和未声明大文件。
- clean-smoke 与两份 20+20 启动证据对同一敏感集合均为 0 命中。启动基准新增回归测试，保证公开身份只写 `paper-reader.exe`，不写绝对路径。
- 发行目录不含论文、作者列表、PDF、标注、聊天、报告、偏好、凭据、完整提示词、应用数据库或日志。

## 7. 未验证的外部条件

- Lisalsj 账户的 GitHub Actions 分钟已耗尽；没有当前 commit 的 hosted Windows runner 收据，本轮没有 push 或 rerun。
- 没有 Windows 11、16 GB、普通 SSD、无开发工具的干净虚拟机；没有管理员防火墙成功、Windows Sandbox、SmartScreen 或 Authenticode 收据。
- 没有当前 v2 的真实 `1.0.0 → 2.0.0` 覆盖安装收据；两套历史/当前发行身份已完整验签，但严格 harness 在安装前按权限边界停止。
- IEEE Xplore 与 Springer Nature 没有合法 key 的非空 live smoke；官方 fixture 契约通过，无 key 时产品明确禁用。
- NVDA/Narrator 中文逐屏、400% 缩放、Windows 高对比度和真实移动触控仍需独立人工复验。

这些条件透明保留，不被描述为“已通过”。对个人开源、本地使用而言，当前没有已知未修复的代码、依赖、制品完整性或本机生命周期阻断项。

## 8. 发布状态

`dist/release` 是 Git 忽略的本地制品目录。当前没有推送源码、tag 或制品，没有创建 PR、GitHub Release 或 Pages 发布，也没有消耗新的 Actions 分钟。若以后公开发布，应从冻结身份补齐远端 Windows/干净环境验证，并核对同一 manifest 与 SHA-256；不得把重新构建的不同字节冒充本收据制品。
