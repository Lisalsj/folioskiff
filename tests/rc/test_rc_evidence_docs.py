from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]
V1_VERSION = "1.0.0"
V1_COMMIT = "b3490b04f5e4a411065c9156f8cda99e2cdb4ed1"
V1_MANIFEST_SHA256 = "d61fbc4ede839b0b67f0139e0347e245499771c2307d2cdd3ace6d8539fe265e"
V1_CHECKSUMS_SHA256 = "4dbdc37ce19c9131ccef04076ac90c92516bc0b22e0b90cd469f3cd1e45c2fdc"
V1_BUNDLE_MANIFEST_SHA256 = "30731142e25f8c7940afb0a51d54a8b9f9475e1f87f621614d1ca0aee533bea7"
V1_PORTABLE_SHA256 = "e9efe3fcbd0ff75c6770ebe4bb2e92ed73b6266bd1eea607164ce1dbc3ec00c3"
V1_INSTALLER_SHA256 = "767eef9e569543c07bd54cc02f838a5ac03fd60a81f82a1aa550990ab60a0d13"
PREVIOUS_COMMIT = "b030990c01296d6984a16c94aa4d9cf007ca0ea5"
PREVIOUS_MANIFEST_SHA256 = "b63afc99985b6f61ffda11765454847dcf8b1dbebd925fd6d80399aac9b4a333"
SECURITY_BASELINE_COMMIT = "e8d75f54bb96973b62c9b867f9eae64127cb5683"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_current_docs_identify_the_exact_local_release_candidate() -> None:
    readme = _read("README.md")
    implementation = _read("docs/implementation-status.md")
    testing = _read("docs/testing-release.md")
    packaging = _read("docs/windows-packaging.md")
    performance = _read("docs/performance-baselines.md")
    receipt = _read("docs/v1-local-release-receipt.md")

    assert f"`paper-reader@{V1_VERSION}`" in readme
    assert "本地源码、安全、发行物、安装/升级和性能硬门禁" in readme
    assert "本机 `0.6.0` 候选" not in readme
    for evidence_doc in (receipt, implementation, testing):
        assert V1_COMMIT in evidence_doc
        assert V1_MANIFEST_SHA256 in evidence_doc
        assert V1_CHECKSUMS_SHA256 in evidence_doc
        assert "firewall_requires_administrator" in evidence_doc

    for digest in (
        V1_BUNDLE_MANIFEST_SHA256,
        V1_PORTABLE_SHA256,
        V1_INSTALLER_SHA256,
    ):
        assert digest in receipt
    assert "1,299 项通过、15 项条件跳过、0 失败" in implementation
    assert "85.99%" in implementation
    assert "4,690.182/3,727.647 ms" in implementation
    assert "438.4" in implementation
    assert "Python SBOM 为 CycloneDX 1.6 的 105 个组件/106 个依赖节点" in receipt
    assert "npm SBOM 为 291 个组件/292 个依赖节点" in receipt
    assert "[v1.0.0 本地发行收据](v1-local-release-receipt.md)" in packaging
    for token in (
        V1_COMMIT,
        "4,690.182",
        "3,727.647",
        "332.430",
    ):
        assert token in performance


def test_v1_release_gate_separates_hard_gates_from_known_limitations() -> None:
    blockers = _read("docs/rc-blockers.md")

    assert "# v1.0 本地开源发布门禁与已知限制" in blockers
    for gate_id in ("V1-001", "V1-002", "V1-003", "V1-004"):
        assert gate_id in blockers
    for limitation_id in ("L-001", "L-002", "L-003", "L-004", "L-005"):
        assert limitation_id in blockers

    assert SECURITY_BASELINE_COMMIT in blockers
    assert V1_COMMIT in blockers
    assert PREVIOUS_COMMIT in blockers
    assert V1_MANIFEST_SHA256 in blockers
    assert PREVIOUS_MANIFEST_SHA256 in blockers
    assert "firewall_requires_administrator" in blockers
    assert "已知漏洞数均为 0" in blockers
    assert "不虚假承诺软件永远不存在未知漏洞" in blockers
    assert "已披露的非阻断限制" in blockers
    assert "不宣称已人工通过" in blockers
