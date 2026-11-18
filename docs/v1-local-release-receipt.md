# v1.0.0 本地发行收据

日期：2026-07-16

发行类型：个人开源、本地验证、未签名

冻结提交：`b3490b04f5e4a411065c9156f8cda99e2cdb4ed1`

本地交付收尾：2026-07-17

本文记录 `paper-reader@1.0.0` 在 Windows 11 x64 本机完成的不可变发行事实。它证明下列源码、制品、生命周期和性能门禁在同一冻结提交上通过；它不是 GitHub hosted runner、管理员防火墙隔离、干净虚拟机、Authenticode 或人工辅助技术收据。

## 1. 结论

- 冻结时点的 Python 基础依赖、全部可选依赖和 npm 锁文件经 PyPI、OSV 与 npm 审计，已知漏洞均为 0。
- Bandit medium/high 为 0；当前树 50 个秘密候选和完整 Git 历史 23 个 Gitleaks 候选均已分类，没有真实凭据或未分类项。
- 全量源码、前端、浏览器、无障碍、冻结契约、可选向量运行时、发行物和本机安装生命周期没有未解决失败。
- `0.9.0 → 1.0.0` 在当前 Windows 用户范围内完成真实覆盖安装、同一数据根启动、数据保留与卸载；不冒充干净虚拟机或管理员离线验证。
- 严格离线 smoke 在验证当前与历史发行清单后，以 `firewall_requires_administrator` 失败关闭；没有跳过隔离、写入成功收据或留下规则、进程、卸载项和临时目录。
- 本地硬门禁完成。本地 annotated tag `v1.0.0` 精确指向冻结提交；当前制品和标签尚未 push，也未创建 GitHub Release，且首版没有 Authenticode。

“0 个已知漏洞”只描述 2026-07-16 冻结时可检索的已知公告与已执行检查，不承诺不存在尚未披露的漏洞。

## 2. 发行物身份

发行目录是封闭的 10 文件集合。独立验证器在生成后再次通过：

| 项目 | 值 |
|---|---|
| 版本 | `1.0.0` |
| Git 提交 | `b3490b04f5e4a411065c9156f8cda99e2cdb4ed1` |
| 数据库 revision | `0032_identity_candidate_indexes` |
| release manifest SHA-256 | `d61fbc4ede839b0b67f0139e0347e245499771c2307d2cdd3ace6d8539fe265e` |
| `SHA256SUMS.txt` SHA-256 | `4dbdc37ce19c9131ccef04076ac90c92516bc0b22e0b90cd469f3cd1e45c2fdc` |
| 签名状态 | 未签名 |

主要制品：

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| `paper-reader-1.0.0-windows-x64-portable.zip` | 118,375,253 | `e9efe3fcbd0ff75c6770ebe4bb2e92ed73b6266bd1eea607164ce1dbc3ec00c3` |
| `paper-reader-1.0.0-windows-x64-setup.exe` | 85,346,842 | `767eef9e569543c07bd54cc02f838a5ac03fd60a81f82a1aa550990ab60a0d13` |
| `paper-reader-1.0.0-python.cdx.json` | 85,535 | `ba4a614c0a868bccb45e4f6faf7de64c6c13e05b53c57f8304c68153eec0ac19` |
| `paper-reader-1.0.0-npm.cdx.json` | 182,994 | `331965978c7943a4627609c2017e10dab4e96d074b75f39659b50312ef242bad` |

source-free onedir 含 970 个文件、`288,260,042` 字节，内部 bundle manifest SHA-256 为 `30731142e25f8c7940afb0a51d54a8b9f9475e1f87f621614d1ca0aee533bea7`。冻结 smoke 验证了版本、API v1、OpenAPI 哈希、数据库 revision、插件协议、SPA、OpenAPI 路由、SQLite WAL、无 Python/Node/Git 的 PATH 和只读程序目录。

Python SBOM 为 CycloneDX 1.6 的 105 个组件/106 个依赖节点；npm SBOM 为 291 个组件/292 个依赖节点；根组件均为 `paper-reader@1.0.0`。

## 3. 源码与安全门禁

| 门禁 | 结果 |
|---|---|
| Python 全量 | 1,314 项收集；1,299 通过、15 条件跳过、0 失败 |
| 覆盖率 | 总覆盖率 85.99%；核心领域分支覆盖率 93.56%（363/388） |
| Python 静态检查 | Ruff 通过；严格 MyPy 通过 205 个源与脚本文件 |
| 前端 | 36 个文件、122 项 Vitest 通过；TypeScript strict 与生产构建通过 |
| 浏览器与 axe | 67 项通过；2 项仅在非桌面视口按设计跳过 |
| 可选向量路径 | 真实 `sqlite-vec` models 标记测试通过 |
| 制品感知回归 | 229 项通过、5 项条件跳过、0 失败 |
| Python 依赖审计 | PyPI 基础、PyPI 全部 extras、OSV 全部 extras：0 个已知漏洞 |
| npm 审计 | `--audit-level=low`：0 个已知漏洞 |
| Bandit | medium/high：0 |
| 秘密扫描 | 当前树 50 个、Git 历史 23 个候选全部分类；0 个真实凭据或未分类项 |

15 个 Python 条件跳过来自明确的平台、合法 API key 或外部可选环境，不用于掩盖依赖、安全边界或核心产品失败。全量输出中的 Python 3.12 SQLite 默认 datetime adapter 弃用警告是已记录的未来升级事项，不影响固定 Python 3.12 发行运行时。

## 4. 生命周期与升级

真实本机验证覆盖：

- 便携目录内容审计、只读运行、动态端口、会话启动、正常退出；
- 静默安装、开始菜单与当前用户卸载注册；
- 便携二次激活已有实例；
- 运行中安装保护；
- 同版本修复安装；
- 人为更高版本下的降级阻止；
- 卸载程序和注册项，同时保留用户数据；
- 经完整验签的 `0.9.0` 安装并启动，再由 `1.0.0` 覆盖安装，以同一数据根启动，最后卸载且保留 canary。

跨版本输入固定为：

- `0.9.0` manifest：`b63afc99985b6f61ffda11765454847dcf8b1dbebd925fd6d80399aac9b4a333`
- `1.0.0` manifest：`d61fbc4ede839b0b67f0139e0347e245499771c2307d2cdd3ace6d8539fe265e`

严格 clean-smoke 收据还绑定脚本 SHA-256 `aadd179652a62055506fc46c4152e552b5b6a330acca3b0150c2d49a610c7e33`，并在非管理员环境稳定返回 `firewall_requires_administrator`。验证结束后 `paper-reader` 进程、固定 HKCU 卸载键、测试防火墙规则和 smoke 临时目录均为 0。

## 5. 性能

本机平台为 Windows 11 build 26200。开发机收据不能替代 16 GB 普通 SSD 干净虚拟机，但全部低于产品回归门槛。

| 场景 | 样本 | p95 | 最大值 | 门槛 |
|---|---:|---:|---:|---:|
| 冷资料 SPA 完整启动 | 20 | 4,690.182 ms | 4,747.414 ms | 8,000 ms |
| 热资料 SPA 完整启动 | 20 | 3,727.647 ms | 3,826.856 ms | 8,000 ms |
| PDF 冷浏览器上下文首屏 | 20 | 438.4 ms | 939.4 ms | 2,500 ms |
| PDF 热浏览器上下文首屏 | 20 | 150.2 ms | 150.3 ms | 2,500 ms |
| 20 万数据首屏列表 | 20 | 197.910 ms | 200.976 ms | 800 ms |
| 20 万数据深分页 | 20 | 267.417 ms | 274.840 ms | 800 ms |
| 20 万数据中文题名检索 | 20 | 69.192 ms | 71.562 ms | 800 ms |
| 20 万数据英文作者检索 | 20 | 63.695 ms | 66.335 ms | 800 ms |
| 20 万数据 DOI 前缀检索 | 20 | 56.678 ms | 57.971 ms | 800 ms |
| 20 万数据无 DOI 身份写入 | 20 | 332.430 ms | 349.079 ms | 800 ms |
| 取消请求至状态/可恢复事件可见 | 20 | 6.071 ms | 7.485 ms | 2,000 ms |

启动“冷资料”表示每次使用新的 state/cache/library；它不声称清空操作系统文件缓存。PDF 指标从点击阅读测到真实首屏 canvas；中等和移动视口按设计不重复桌面性能样本。

## 6. 非阻断限制与发布状态

- 没有当前提交的 GitHub hosted Windows runner 收据；Actions 分钟已耗尽，本轮没有 push、rerun 或触发工作流。
- 没有 16 GB 干净 Windows 11 虚拟机和管理员防火墙隔离成功收据；非管理员路径已证明失败关闭。
- IEEE Xplore 与 Springer Nature 没有合法 key 的非空 live smoke；官方 fixture 契约通过，无 key 时诚实禁用。
- NVDA/Narrator 中文逐屏、400% 页面缩放、触控/软键盘、Windows 高对比度和 SmartScreen 没有人工收据。
- 首版没有 Authenticode；用户应以本节 SHA-256 和发行目录中的 `SHA256SUMS.txt` 校验字节。
- 本地发行目录名为 `dist/release-1.0.0-b3490b0`，它仍是 Git 忽略的本机制品，不会进入源码提交。

额度恢复后若要公开 GitHub Release，应从这一冻结提交补跑一次远端验证，核对同一版本与能力矩阵后再推送既有 tag、创建 Release；不得用远端重建的不同哈希冒充本收据。

## 7. 本地交付收尾

- 本地主工作目录已从错误拼写改为 `paper-reader`；旧拼写路径不再存在。一个无权读取的历史 pytest 缓存被隔离到仓库外的同级 `paper-reader-legacy-cache-20260717` 目录，不属于源码、制品或用户数据。
- 本地 `main` 在收据提交 `8cfe2bdf72796fece1d9541ec490788ae75287d9` 上完成目录切换；annotated tag `v1.0.0` 仍精确绑定制品冻结提交，不因收据提交而漂移。
- 两个 linked worktree 已修复到新绝对路径。`codex/rc-performance` 保持干净；`codex/reader` 的 32 个已跟踪修改和 3 个未跟踪计划文档原样保留。
- `codex/reader` 当前二进制 diff SHA-256 与迁移前安全备份完全一致：`7ba3c003f2030c98a4ab9338880d708d1c33fa8e087fd0b289ae8a1ebb964c30`；3 个未跟踪文档逐文件哈希一致。
- 用户未跟踪的 `AGENTS.md` 未暂存且内容未变，SHA-256 为 `ae9cf0952fd901e5772709697a9b6fbfd3e758c682fad34e1ecad86c5e794dc3`。
- Git 完整对象检查通过；输出仅包含开发期间产生的 unreachable/dangling 临时对象，没有 missing、corrupt 或连接性错误。
- 从最终目录再次执行发行独立验证器，10 个封闭文件、冻结提交、manifest 与 `SHA256SUMS.txt` 身份全部一致。
- 最终目录复验发现旧工作区遗留但被 Git 忽略的 `site/` VitePress cache/dist（1,013 文件、80,888,588 字节）和 `src/daily_paper_reader/` 字节码缓存（22 文件、195,357 字节）；两者都不含被跟踪源码，已原子移至仓库外安全备份。备份清单 SHA-256 分别为 `a0097ff6b7d5313722d94975401f5f56c836a5b52403389b31846a26e5789117` 与 `dde5ee44f4274a85c29ce740fe344b5a4873924092c7a063afb52a58e6bc44bd`，移动后身份与 Windows 工作流契约测试 14 项全部通过。
