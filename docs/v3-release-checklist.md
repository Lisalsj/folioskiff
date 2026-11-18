# paper-reader 3.0.0 发布清单

## 当前版本事实

- 应用版本：`3.0.0`
- API：`v1`，保持兼容
- 数据库迁移：启动时沿用现有备份与 Alembic 迁移链，论文、批注、报告、计划和设置保留
- 平台：Windows 11 x64
- AI：Codex CLI、Ollama Local、Ollama Cloud（显式云端边界）
- 发布制品：安装包、便携 ZIP、Python/npm SBOM、release manifest、SHA-256 清单和 GitHub build attestation

## 已落地

- 统一 Python、npm、OpenAPI、锁文件、CITATION 和 Windows 版本资源。
- 新增 `npm run version:check`，CI 和 Windows 打包前都会验证版本元数据一致。
- 新增 `/search` 本地论文库文本搜索页，支持来源、日期、收藏和未读筛选；尚未接入自然语言解析、查询改写或统一混合检索。
- 扩展本地论文库 API，筛选在 SQL 层执行并保持分页结果一致。
- 新增中英文显示偏好基础设施，偏好保存在浏览器本地并更新 `lang` 属性。
- 发布工作流对已验证发行字节生成 GitHub artifact attestation。
- 升级 Vitest 和 `js-yaml`，`npm audit --audit-level=high` 为零漏洞。

## 发布前门禁

完整计划尚未验收：大型组件/服务拆分、保存查询与证据解释、统一 AI 任务、完整双语、四入口移动导航、升级/回滚验收及全部发布制品验证仍需完成。版本号和标签不代表这些需求已经交付。

```powershell
npm ci
npm run version:check
npm run typecheck
npm test
npm run build:budget
uv sync --locked --extra dev --extra models --extra ocr --extra packaging
uv run ruff check src tests scripts examples
uv run python -m mypy src scripts
uv run pytest -n 2 --dist=loadscope -m "not resource_serial" --basetemp=.tmp/p
uv run pytest -m resource_serial --basetemp=.tmp/s
```

1. 将候选提交推送到本仓库的 `main`，在 **Actions → CI → Run workflow** 选择 `main`。全部测试通过后，记下该次运行的完整提交 SHA。
2. 为该 SHA 创建与版本一致的附注标签（本版为 `v3.2.0`）并推送到本仓库。推送分支、更新 PR 或推送标签都不会自动启动测试或打包。
3. 手动启动标签上的打包工作流。例如，发布本版时运行 `gh workflow run package-windows.yml --repo Lisalsj/folioskiff --ref v3.2.0`。如需只验证构建，可在 **Actions → Windows packaged distributions → Run workflow** 中选择 `main`。
4. 核对打包记录和 GitHub Release 附件。标签运行先核对同一 SHA 在 `main` 上手动启动的 CI 成功记录，再验证 manifest、SHA-256、SBOM 和 source-free smoke。发布 job 为通过验证的文件登记 GitHub 构建证明，核对草稿附件后发布。
