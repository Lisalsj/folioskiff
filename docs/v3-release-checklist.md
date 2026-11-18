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

发布标签必须指向已经通过 `main` push CI 的同一提交。打包工作流会在生成制品后执行 source-free smoke、manifest 校验、SBOM 校验和 attestation；只有这些步骤全部成功才发布 GitHub Release。
