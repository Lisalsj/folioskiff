from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]

REQUIRED_GUIDES = {
    "docs/getting-started.md": (
        "Windows 11 x64",
        "安装器",
        "便携",
        "首次配置",
        "无需安装 Python 或 Node.js",
    ),
    "docs/first-run-setup.md": (
        "存储与隐私",
        "Agent",
        "论文来源",
        "阅读偏好",
        "重置本地数据",
    ),
    "docs/ai-reading-assistant.md": (
        "提问",
        "解释",
        "翻译",
        "相关推荐",
        "普通提问、解释和翻译不会联网",
    ),
    "docs/codex-ollama-setup.md": (
        "Codex CLI",
        "Ollama Local",
        "Ollama Cloud",
        "configuration_invalid",
        "service_not_running",
    ),
    "docs/troubleshooting.md": (
        "安全诊断包",
        "configuration_invalid",
        "service_not_running",
        "source_rate_limited",
        "不要提交凭据",
    ),
    "docs/threat-model-v2.md": (
        "PDF 提示注入",
        "SSRF",
        "凭据",
        "回环",
        "插件",
        "剩余风险",
    ),
}

STABLE_V2_FACTS = {
    "docs/product-requirements.md": (
        "四步首次配置",
        "统一论文 Assistant",
        "英文到简体中文",
        "显式同意",
    ),
    "docs/architecture.md": (
        "OnboardingService",
        "RuntimeConfigStore",
        "network_policy",
        "Assistant",
    ),
    "docs/data-model.md": (
        "onboarding_state",
        "assistant_sessions",
        "translation_tasks",
        "related_resource_tasks",
    ),
    "docs/ui-ux.md": (
        "提问、解释、翻译、相关推荐",
        "沉浸式阅读工作台",
        "1440×900",
        "Pixel 7",
    ),
    "docs/agent-analysis.md": (
        "local_only",
        "external_research",
        "英文到简体中文",
    ),
    "docs/security-privacy.md": (
        "PDF 提示注入",
        "SSRF",
        "network_policy",
        "重置本地数据",
    ),
    "docs/testing-release.md": (
        "350,000",
        "24 张",
        "全 PR Windows",
    ),
    "docs/windows-packaging.md": (
        "Windows 11 x64",
        "source-free",
        "未签名",
        "全 PR Windows",
    ),
}


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_v2_guides_cover_exact_product_boundaries() -> None:
    for relative, phrases in REQUIRED_GUIDES.items():
        text = _read(relative)
        assert len(text.strip()) >= 400, relative
        for phrase in phrases:
            assert phrase in text, f"{relative}: {phrase}"
        assert "TODO" not in text
        assert "PLACEHOLDER" not in text


def test_v2_guides_are_linked_from_documentation_and_repository_entries() -> None:
    docs_index = _read("docs/README.md")
    english = _read("README.md")
    chinese = _read("README.zh-CN.md")

    for relative in REQUIRED_GUIDES:
        guide = relative.removeprefix("docs/")
        assert f"({guide})" in docs_index, guide
    assert "docs/getting-started.md" in english
    assert "docs/getting-started.md" in chinese
    assert "docs/first-run-setup.md" in english
    assert "docs/first-run-setup.md" in chinese


def test_stable_docs_describe_the_v2_runtime_and_ai_boundaries() -> None:
    for relative, phrases in STABLE_V2_FACTS.items():
        text = _read(relative)
        for phrase in phrases:
            assert phrase in text, f"{relative}: {phrase}"

    english = _read("README.md")
    chinese = _read("README.zh-CN.md")
    assert "Unified ask/explain/translate/related-resource assistant | Available" in english
    assert "统一提问、解释、翻译与相关资源助手 | 可用" in chinese
    assert "Planned in the active repository optimization" not in english
    assert "正在按当前仓库优化计划实施" not in chinese


def test_v2_release_receipt_records_closed_artifact_evidence() -> None:
    receipt = ROOT / "docs" / "v2-local-release-receipt.md"

    assert receipt.is_file(), receipt
    text = receipt.read_text(encoding="utf-8")
    for phrase in (
        "paper-reader 2.0.0",
        "Git commit",
        "release-manifest SHA-256",
        "installer",
        "portable",
        "SBOM",
        "source-free",
        "unsigned",
        "未推送",
    ):
        assert phrase in text
