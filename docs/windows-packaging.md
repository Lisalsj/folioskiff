# Windows 打包与干净环境验收

更新日期：2026-11-18

本文说明页舟 · FolioSkiff 的 Windows 11 x64 安装器、便携 ZIP、干净 runner 与 Windows
Sandbox 的唯一发行流程。它是开发和发布文档，不是已发布版本声明；是否已经形成正式
候选，以[实施状态](implementation-status.md)和对应 GitHub Actions 运行记录为准。

## 1. 发行链路

```text
完整质量门禁
  → 锁定环境构建 PyInstaller onedir
  → 同一 onedir 生成便携 ZIP 与 Inno 安装器
  → 生成 Python/npm CycloneDX 1.6 SBOM
  → 生成十文件 manifest、说明与 SHA-256 清单
  → 独立验证器关闭式复核
  → 新 Windows runner 下载不可变 artifact
  → 无源码、无 Python/Node/Git 的安装/便携 smoke
  → v* tag 触发 GitHub 构建证明，校验草稿附件的 SHA-256 后发布
```

`.github/workflows/package-windows.yml` 只响应手动触发和 `v*` tag。质量、构建与验证分属
不同 job，并始终按“完整质量门 → 构建 → source-free 验证”执行。验证 job 使用新的
`windows-latest` runner，只下载上一 job 的 artifact，不 checkout 仓库，也不 setup Python、
Node.js 或 uv。主 `ci.yml` 仅响应 PR、`main` push 或手动事件，保留全 PR Windows 源码与
浏览器门禁，但不重复构建安装器；浏览器门禁与后端门禁并行，功能分支也不因 push 与 PR 双跑。

构建输入固定为 Python `3.12.10`、Node.js `24.12.0`、uv `0.11.8` 和 Inno Setup
`6.7.1`。Python 3.12.10 是官方发布 Windows x64 安装包的最后一个 3.12 版本；后续
3.12 安全维护版仅发布源码，不能作为免 Python Windows 构建的 runner 工具链。官方
Actions 使用 `checkout@v6`、`setup-python@v6`、`setup-node@v6`、
`upload-artifact@v7` 与 `download-artifact@v8`；GitHub Actions 依赖由 Dependabot 每月
检查。`setup-uv` 从 v8 起不再发布可移动的主/次版本标签，因此固定到 v8.3.2 的完整提交
`11f9893b081a58869d3b5fccaea48c9e9e46f990`。版本升级必须先通过契约测试、远程引用
解析与 `actionlint`，不能仅改标签。

Inno Setup 使用 Chocolatey 社区仓库已批准且可复核下载的精确版本 6.7.1。更改该版本
前必须先确认精确包地址存在；本机 Task 7–8 的 6.7.3 证据是历史构建事实，不代表远程
runner 可以从 Chocolatey 取得同一版本。

相关官方契约：

- [GitHub checkout](https://github.com/actions/checkout)
- [GitHub setup-python](https://github.com/actions/setup-python)
- [GitHub setup-node](https://github.com/actions/setup-node)
- [Astral setup-uv](https://github.com/astral-sh/setup-uv)
- [GitHub upload-artifact](https://github.com/actions/upload-artifact)
- [GitHub download-artifact](https://github.com/actions/download-artifact)

## 2. 权限与发布边界

日常 CI 验证代码与浏览器交互。标签构建先核对同一提交的 `main` CI 成功记录，再打包并执行独立 Windows smoke。发布 job 使用 `contents: write` 管理草稿，使用 `id-token: write` 与 `attestations: write` 为核验过的文件登记 GitHub 构建证明。流程逐一核对草稿附件的名称和 SHA-256，再发布该版本。重试时会补齐草稿，并复核已经发布的附件。

v0.8 明确不做 Authenticode。`UNSIGNED.md`、Release notes、能力矩阵和 manifest 必须
一致显示未签名。SHA-256 只能证明下载字节与清单一致，不能证明发布者身份，也不能建立
Microsoft SmartScreen 信誉。未来签名 hook 仍须显式启用，且不改变 PR 的无凭据边界。

## 3. 干净 runner 验证器

`packaging/windows/clean-smoke.ps1` 有两个必需身份输入：当前发行目录和预期
`release-manifest.json` SHA-256。真实跨版本升级验收再成对提供上一发行目录及其 manifest
SHA-256；只给其中一个、两个目录相同、上一版本不低于当前版本时都失败关闭。脚本不读取
源码、用户配置或网络，不上传数据；输出只包含固定 schema 的脱敏 JSON。

验证器按以下顺序失败关闭：

1. 发行目录必须恰有十个普通文件；manifest、九项摘要、资产大小和 SHA-256 全部一致。
2. Python 与 npm SBOM 必须是 CycloneDX 1.6，根组件版本正确，每个组件有许可证，完整
   依赖图无重复或悬空引用。
3. 便携 ZIP 在解压前拒绝绝对路径、穿越、链接、重复/大小写冲突、超量成员和压缩炸弹；
   解压后逐项复核 onedir 自身 manifest。
4. 子进程 PATH 只保留 Windows System32，环境中的 key/token/secret/password 等值被
   移除；`python.exe`、`node.exe` 与 `git.exe` 必须不可发现。
5. 程序在含中文和空格的只读目录启动，状态、缓存和论文库进入三个隔离根。从创建进程
   到 health 与 SPA 可用必须不超过 8,000 ms；动态端口、禁浏览器、无外部运行时子进程
   和程序字节不变也必须成立。
6. 第二次启动只激活已有实例；强制终止首实例后，旧状态不能阻止新实例取得内核锁。
7. 临时 Windows Firewall 规则允许 IPv4 回环，但阻断程序访问其余 IPv4 和全部 IPv6 的
   任意协议；因此 smoke 期间不是仅靠“未观察到请求”来声称离线。
8. 默认只验证静默安装、安装版启动、同版本重装、默认卸载和用户数据 canary 保留，
   并诚实记录 `installer_reinstalled=true`、`upgrade_verified=false`。提供经完整校验且
   版本更低的上一发行物时，先启动旧版，再安装当前版并用同一数据根成功启动，才记录
   `installer_upgraded=true` 与 `upgrade_verified=true`。固定卸载键预先存在时脚本立即
   拒绝，不能覆盖真实安装。
9. 所有进程、防火墙规则、测试安装和临时目录都必须回收。Windows 短暂文件占用采用
   有界重试；越界目录绝不递归删除。

成功证据写到 `paper-reader-clean-smoke-evidence.json`，同时包含候选
`release_manifest_sha256` 与实际执行脚本的 `harness_sha256`，并记录版本、文件计数、
SBOM 组件/依赖数、`portable_startup_ms`、`startup_budget_ms=8000` 以及上述布尔结果。
失败和清理失败收据也保留这两个身份字段，只返回稳定 `error_code`；所有收据均不含路径、
用户名、进程参数、环境、日志、凭据或异常正文。超出预算以 `startup_budget` 关闭失败。

严格本机运行要求具备创建和删除临时防火墙规则的权限。普通非管理员会话必须在
`offline_firewall` 阶段失败，不能静默降级后仍写出成功证据。

## 4. Windows Sandbox 复核

Windows Sandbox 模板遵循 Microsoft `.wsb` 配置：关闭网络、GPU、音视频、打印机和
剪贴板重定向，并启用 Protected Client。发行输入以只读方式映射，只有脱敏证据目录可写。
参见 [Windows Sandbox 配置参考](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file)
与[只读映射示例](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-sample-configuration)。

普通当前版本复核步骤：

1. 创建 `C:\paper-reader-sandbox\release` 与
   `C:\paper-reader-sandbox-output`；输出目录必须为空。
2. 把待验十文件发行目录复制到 `release`，把同一提交的 `clean-smoke.ps1` 复制到
   `C:\paper-reader-sandbox`。不要映射论文库、Documents、凭据或开发仓库。
3. 复制 `paper-reader-smoke.wsb`，把唯一的
   `REPLACE_WITH_64_LOWERCASE_SHA256` 替换为本次 manifest 的 64 位小写摘要。
4. 启动 `.wsb`。输入目录在 Sandbox 内是 `C:\paper-reader-input` 且只读；输出只写到
   `C:\paper-reader-output\paper-reader-clean-smoke-evidence.json`。
5. Sandbox 退出后，核对宿主输出 JSON 的 `ok=true`、预期 manifest hash、运行前独立
   计算的脚本 hash 和所有验收布尔项；同时确认输入目录字节未变。保留 JSON 与对应
   Actions run URL 作为候选证据。

真实跨版本升级使用 `paper-reader-upgrade-smoke.wsb`：

1. 另把已验证的上一版本十文件发行物放到
   `C:\paper-reader-sandbox\previous-release`，不得只复制安装器。
2. 分别独立计算当前与上一版本 `release-manifest.json` 的 SHA-256，替换模板中两个不同
   占位符；脚本会再次验证两套封闭发行物，并要求上一版本严格低于当前版本。
3. 结果必须同时满足 `previous_release_manifest_sha256`、`previous_release_version`、
   `installer_reinstalled=false`、`installer_upgraded=true`、`upgrade_verified=true`
   及数据保留；同版本再次安装的收据不能关闭 v1 跨版本升级验收。

## 5. 当前 Task 9 证据

截至 2026-07-15，工作流与脚本契约 13 项通过；Windows PowerShell 5.1 解析通过；
官方 `actionlint v1.7.12` 对 `ci.yml` 和 `package-windows.yml` 零错误，下载 ZIP 的
SHA-256 为 `6e7241b51e6817ea6a047693d8e6fed13b31819c9a0dd6c5a726e1592d22f6e9`，
与官方 release 元数据一致。

安全 Task 6 增加了重复触发与关闭式防火墙收据契约；当前工作流/clean-smoke 聚焦门禁为
15 项通过。Lisalsj 账户 Actions 分钟已耗尽，本轮没有触发、重试或推送远端工作流。两个
GitHub-hosted job 使用独立临时虚拟机，不共享仓库工作目录；账户分钟额度则跨私有仓库共享，
额度耗尽可能使另一个项目的 job 无法开始或发布链后续 job 无法继续，但不会造成跨仓库文件覆盖。

Task 8 的本机 `0.6.0` 十文件候选完成了一次非管理员诊断：验证 9 项摘要、Python
SBOM 104/105、npm SBOM 291/292、1,035 个程序文件，并通过动态端口、二次激活、
崩溃锁恢复、安装、同版本重装、卸载和数据保留。该诊断只为排除防火墙之后的脚本缺陷；由于
当前会话不能创建防火墙规则，它不构成离线验收，也不是 v0.8 发布证据。严格脚本在同一
机器按设计停在 `unexpected_offline_firewall`。

Task 9 只有在新的 GitHub Windows runner 严格运行成功，且 Windows Sandbox/干净
Windows 证据完成后才可关闭。开发机诊断、fixture 或静态测试都不能替代这两项事实。

完整 Python 门禁为每个测试设置 300 秒线程超时，并在结束时列出最慢 25 项；Windows
上的线程方法会在超时时输出所有线程堆栈并终止该 pytest 进程。它用于识别死锁或异常
慢测，不替代 75 分钟 job 上限，也不能把性能退化误写成测试通过。

提交前全仓顺序门禁收集 1,138 项 Python 测试：1,124 项通过、14 项按平台/可选真实
产物条件跳过、0 失败；总体覆盖率 86.82%，核心领域分支覆盖率 95.39%（290/304）。
packaging 聚焦回归为 209 项通过、9 项条件跳过。Ruff、181 个源/脚本文件的严格 MyPy、
29 个前端文件共 94 项 Vitest、TypeScript、生产构建、34 项桌面/Pixel 7 Playwright、
OpenAPI/类型确定性与 npm 中高危审计均通过，npm 已知漏洞为 0。

## 6. 远端 source-free 验证补充

历史 GitHub Actions run 29411199826
在提交 `ef4721fe9ae461bc8ee8073c09936b8ca7021808` 上依次完成 Windows 全质量门、审计发行构建和
安装版/便携版 source-free smoke，三项均为 `success`。PR 事件按设计跳过正式 draft Release，
因此该运行不是公开发布。

远端 clean-smoke 收据为 `paper-reader-clean-smoke-evidence-v1`，记录候选版本 `0.6.0`、十文件
发行清单摘要 `2489b34e3fbee53ea06913e52babf3246a214ebb02d0d10e40eb27da880b137a`，并确认九个公开
校验和、104/105 Python SBOM 图、291/292 npm SBOM 图、966 个程序文件、动态端口、禁用浏览器、
零禁止子进程、二次实例激活、崩溃锁恢复、离线启动、程序字节不变、安装/同版本重装/卸载和用户数据保留。
小型收据 artifact 的 GitHub 摘要为
`sha256:ef0b784b4b7556af8f2d3646adee4ba2881661ea81a70249eda2ea7fc66c9c0c`；完整 Windows
artifact 为 `203,575,900` 字节，GitHub 摘要为
`sha256:0e3e1a6fc377f124b9e8e3ff9f92174cb1c77411bc928cabc4bae8619aee3434`。

该证据关闭“GitHub hosted Windows runner 上 source-free 安装/便携 smoke”项，并证明修正后的
程序级出站阻断规则确实形成离线状态。Hosted runner 不能替代 Windows 11 Sandbox/干净 VM：正式
Windows 11、16 GB、无预装 Python/Node/Git 的生命周期与人工安装器/SmartScreen/高对比度检查仍未完成。

## 7. v0.9.0 本地候选

2026-07-16 从精确提交 `b030990c01296d6984a16c94aa4d9cf007ca0ea5` 构建了当前本地 `0.9.0` 候选。该候选包含 Europe PMC 空壳响应修复和严格 live-smoke 本地过滤，替代 `fa88859` 与更早的 `7b11c73` 产物；它没有上传到 GitHub，也没有触发 Actions。

- 封闭发行目录恰有 10 个文件；release manifest SHA-256 为 `b63afc99985b6f61ffda11765454847dcf8b1dbebd925fd6d80399aac9b4a333`，`SHA256SUMS.txt` SHA-256 为 `a8d539bacbd089364eef065846ee7e0259851598f12942958d5ce44ddbb4c052`，独立 verifier 在构建后与 clean-smoke 诊断后均通过。
- source-free onedir 含 970 个文件、`288,259,601` 字节，bundle manifest SHA-256 为 `579351c68237592a1dad75a9d31df16262b35e022c06e0a977ef78a9e3ef95a23`；便携 ZIP 含 971 个成员、`118,374,075` 字节、SHA-256 `834c5163505fa9b9b70f91121abdd9d7fe0629b7f56333426fa91a88bd8d6dc9`；未签名安装器为 `85,347,061` 字节、SHA-256 `1719e3ab81bdf81b5c2e16e3a2e01a2ed76073f2cb727f8ab0e810021278ee22`。
- Python SBOM 为 105/106 个组件/依赖节点，npm SBOM 为 291/292；两者均为 CycloneDX 1.6、根组件版本 `0.9.0`、缺失许可证数为 0。
- 候选专属 packaging 回归有 223 项直接通过、5 项条件跳过；生命周期测试的首次调用因烟测根不在安全白名单而在安装前关闭失败，换到契约允许的全新 `.tmp` 根后只精确重跑该项并通过。它验证安装、同版本修复安装、降级阻止、卸载和数据保留，不冒充跨版本升级。
- 同一 onedir 各执行 20 次冷资料和热资料启动，并先执行 2 次热身；SPA p95 分别为 `4,215.731 ms` 与 `2,703.063 ms`，均低于 8,000 ms。“冷”指新 state/cache/library，不冒充清空操作系统文件缓存。
- 当前终端不是管理员。正式 `clean-smoke.ps1` 对新候选只运行一次；收据在确认预期 manifest hash 后返回 `firewall_requires_administrator`，同时记录候选 manifest `b63afc99985b6f61ffda11765454847dcf8b1dbebd925fd6d80399aac9b4a333` 与脚本 `aadd179652a62055506fc46c4152e552b5b6a330acca3b0150c2d49a610c7e33`；证据 schema 为 `paper-reader-clean-smoke-evidence-v1`、`ok=false`。没有使用跳过防火墙的参数，也没有把本机构建启动检查写成离线生命周期成功。
- 最终 `1.0.0` 已关闭同源本地构建、静态/独立发行验证、本机安装、修复安装、降级阻止、卸载、便携、真实 `0.9.0→1.0.0` 升级和性能硬门禁；完整事实见 [v1.0.0 本地发行收据](v1-local-release-receipt.md)。hosted-runner、Windows 11 Sandbox/干净 VM、管理员防火墙成功、SmartScreen 与人工无障碍证据按 [v1.0 发布门禁与已知限制](rc-blockers.md) 透明列为未验证的非阻断项。

## 8. v2 Windows 发行边界

v2 仍只支持 **Windows 11 x64**。安装器和便携 ZIP 必须消费同一份已审计 onedir；验证进程不 checkout 源码、不带 Python/Node/Git，并以 **source-free** 方式验证回环启动、SPA、API、SQLite、单实例、便携运行和安装生命周期。

首版制品保持**未签名**，因此同时发布 SHA-256、CycloneDX SBOM、`UNSIGNED.md` 和未来签名 hook，不能把摘要校验写成发布者身份认证。工作流继续保留**全 PR Windows** 质量门和隔离的打包/验证 job；当前 Actions 配额限制下不触发远端运行，最终收据必须把本地成功与 hosted runner、Sandbox、SmartScreen 等未验证事实分开。

## 9. v2.0.0 本地候选证据

2026-08-01 从精确提交 `0c753a5df25690ac4a3de292aab6db9850eae8f2` 构建了封闭的 `2.0.0` Windows 11 x64 候选。当前用户安装的 Inno Setup 6.7.3 位于 `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`；打包命令始终传入已解析的固定绝对路径，没有从 `PATH` 或未受信环境变量选择编译器。

- 发行目录恰有 10 个文件；manifest SHA-256 为 `273dc4796101433b923809e21e96d8a52c3acfe321edf51c90e95ec66c1f3a0c`，`SHA256SUMS.txt` SHA-256 为 `4fdc992b77873234d48096b70d8653ada12e70bca6946e2048cc7ed85600477e`，独立 verifier 在生命周期检查后再次通过。
- onedir 含 1,051 个文件、`289,020,827` 字节，bundle manifest SHA-256 为 `705d894591f1a0109f3f3698e2c9eb2a5a44b417760b6573f8c932f77a330db7`。portable ZIP 为 `118,919,999` 字节、SHA-256 `a3097c43596ed5438b81e7f181376420f8ad4e3b33a007ae7c3d4080452ebb9c`；未签名 installer 为 `85,789,018` 字节、SHA-256 `72f3bc3a2ca8cfc30381ed15cb940bfc50010d0edf2bcb0ed733e70ccd6d3420`。
- Python/npm SBOM 分别为 133/134 和 291/292 个组件/依赖节点，均为 CycloneDX 1.6、根组件 `paper-reader@2.0.0`、缺失许可证数 0。
- 本机真实安装生命周期 2 项通过，覆盖 PE 身份、安装、运行中保护、修复安装、降级阻止、卸载、数据保留和 portable 二次激活；退出后无进程或 HKCU 卸载项。
- 严格 clean-smoke 在验签当前 v2 和历史 v1 两套发行物后返回 `firewall_requires_administrator`。当前终端不是管理员且 Windows Sandbox 未启用，因此没有声称离线启动或跨版本覆盖升级成功；脚本没有 skip-success 路径，退出后进程、规则、注册项和临时目录均为 0。
- 冷/热资料 source-free SPA 启动 20 次 p95 为 `4,817.774 / 3,637.587 ms`，低于 8 秒。证据默认脱敏，不保存可执行文件绝对路径。

完整事实与限制见 [v2.0.0 本地发行收据](v2-local-release-receipt.md)。本轮没有 push、tag、GitHub Release 或 Actions 调用；公开发布仍必须复用相同冻结身份或明确生成一套新的、重新验收的制品，不能混用哈希。
