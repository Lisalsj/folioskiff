from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.platform._publishing_fixtures import build_publishing_fixture

EXPECTED_REMOTE = "https://github.com/Lisalsj/folioskiff.git"
GIT = shutil.which("git")


def _git(
    cwd: Path,
    *args: str,
    check: bool = True,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    assert GIT is not None
    return subprocess.run(
        (GIT, *args),
        cwd=cwd,
        input=input_bytes,
        capture_output=True,
        check=check,
    )


def _publication_snapshot(tmp_path: Path, *, title: str = "安静图书馆精选"):
    from paper_reader.publishing.models import PublicationSelection, PublicationWorkSelection
    from paper_reader.publishing.privacy import PublishingPrivacyBoundary
    from paper_reader.publishing.repository import PublishedSnapshotRepository

    fixture = build_publishing_fixture(tmp_path / "database")
    preview = PublishingPrivacyBoundary(fixture.sessions).preview(
        PublicationSelection(
            slug="quiet-library-picks",
            title=title,
            public_intro="只公开用户明确选择的论文与正式报告。",
            works=(
                PublicationWorkSelection(work_id="work-b"),
                PublicationWorkSelection(work_id="work-alias", report_id="report-selected"),
            ),
        )
    )
    return fixture, PublishedSnapshotRepository(fixture.sessions).freeze(preview)


def _repository(tmp_path: Path) -> tuple[Path, Path]:
    assert GIT is not None
    remote = tmp_path / "remote.git"
    source = tmp_path / "source"
    remote.mkdir(parents=True)
    source.mkdir()
    _git(remote, "init", "--bare", "--initial-branch=main")
    _git(source, "init", "--initial-branch=main")
    (source / "README.md").write_text("private source branch\n", encoding="utf-8")
    _git(source, "add", "README.md")
    _git(
        source,
        "-c",
        "user.name=Source Author",
        "-c",
        "user.email=source@example.invalid",
        "commit",
        "--no-gpg-sign",
        "-m",
        "source baseline",
    )
    _git(source, "remote", "add", "origin", EXPECTED_REMOTE)
    _git(
        source,
        "config",
        "--local",
        f"url.{remote.resolve().as_uri()}.insteadOf",
        EXPECTED_REMOTE,
    )
    _git(source, "push", "origin", "main")
    return source, remote


def _source_state(source: Path) -> tuple[bytes, bytes, bytes]:
    return (
        _git(source, "rev-parse", "HEAD").stdout,
        _git(source, "symbolic-ref", "--short", "HEAD").stdout,
        _git(source, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout,
    )


@pytest.mark.skipif(GIT is None, reason="Git is required for the real worktree contract")
def test_diagnostics_require_exact_root_origin_and_credential_free_github_https(
    tmp_path: Path,
) -> None:
    from paper_reader.publishing.git import GitPublisher, is_safe_publishing_remote

    source, _remote = _repository(tmp_path)
    cache = tmp_path / "cache"
    guarded = GitPublisher(repository_root=source, cache_root=cache).diagnose()
    assert guarded.enabled is False
    assert guarded.reason == "url_rewrite_forbidden"
    healthy = GitPublisher(
        repository_root=source,
        cache_root=cache,
        allow_url_rewrites=True,
    ).diagnose()

    assert healthy.enabled is True
    assert healthy.reason is None
    assert healthy.remote_name == "origin"
    assert healthy.remote_url == EXPECTED_REMOTE
    assert healthy.branch == "published"
    assert healthy.repository_root == str(source.resolve())
    assert healthy.target_fingerprint
    assert is_safe_publishing_remote(EXPECTED_REMOTE) is True
    assert is_safe_publishing_remote("https://token@github.com/Lisalsj/folioskiff.git") is False
    assert is_safe_publishing_remote("git@github.com:Lisalsj/folioskiff.git") is False
    assert is_safe_publishing_remote("https://github.com/other/paper-reader.git") is False

    _git(source, "config", "--local", "credential.helper", "!malicious-helper")
    unsafe_config = GitPublisher(
        repository_root=source,
        cache_root=cache,
        allow_url_rewrites=True,
    ).diagnose()
    assert unsafe_config.enabled is False
    assert unsafe_config.reason == "unsafe_local_git_config"
    _git(source, "config", "--local", "--unset-all", "credential.helper")

    nested = source / "nested"
    nested.mkdir()
    assert GitPublisher(repository_root=nested, cache_root=cache).diagnose().reason == (
        "not_a_repository"
    )

    _git(source, "remote", "set-url", "origin", "https://github.com/other/repo.git")
    wrong_remote = GitPublisher(repository_root=source, cache_root=cache).diagnose()
    assert wrong_remote.enabled is False
    assert wrong_remote.reason == "remote_url_mismatch"

    _git(source, "remote", "set-url", "origin", EXPECTED_REMOTE)
    _git(
        source,
        "remote",
        "set-url",
        "--push",
        "origin",
        "https://secret@github.com/Lisalsj/folioskiff.git",
    )
    unsafe_push = GitPublisher(repository_root=source, cache_root=cache).diagnose()
    assert unsafe_push.enabled is False
    assert unsafe_push.reason == "push_url_mismatch"

    missing = GitPublisher(
        repository_root=source,
        cache_root=cache,
        git_executable=tmp_path / "missing-git.exe",
    ).diagnose()
    assert missing.enabled is False
    assert missing.reason == "git_unavailable"

    outside = tmp_path / "not-a-repository"
    outside.mkdir()
    not_repo = GitPublisher(repository_root=outside, cache_root=cache).diagnose()
    assert not_repo.enabled is False
    assert not_repo.reason == "not_a_repository"


@pytest.mark.skipif(os.name != "nt" or GIT is None, reason="Windows Git is required")
def test_diagnostics_reject_a_junction_as_the_repository_control_directory(
    tmp_path: Path,
) -> None:
    from paper_reader.publishing.git import GitPublisher

    remote = tmp_path / "remote.git"
    source = tmp_path / "source"
    remote.mkdir(parents=True)
    source.mkdir()
    _git(remote, "init", "--bare", "--initial-branch=main")
    cache = tmp_path / "cache"
    real_git_directory = tmp_path / "real-git-directory"
    _git(source, "init", "--initial-branch=main")
    (source / "README.md").write_text("private source branch\n", encoding="utf-8")
    _git(source, "add", "README.md")
    _git(
        source,
        "-c",
        "user.name=Source Author",
        "-c",
        "user.email=source@example.invalid",
        "commit",
        "--no-gpg-sign",
        "-m",
        "source baseline",
    )
    _git(source, "remote", "add", "origin", EXPECTED_REMOTE)
    _git(
        source,
        "config",
        "--local",
        f"url.{remote.resolve().as_uri()}.insteadOf",
        EXPECTED_REMOTE,
    )
    (source / ".git").rename(real_git_directory)
    junction = subprocess.run(
        (
            os.environ["COMSPEC"],
            "/d",
            "/c",
            "mklink",
            "/J",
            str(source / ".git"),
            str(real_git_directory),
        ),
        capture_output=True,
        check=False,
    )
    assert junction.returncode == 0, junction.stderr.decode(errors="replace")
    linked_git = GitPublisher(
        repository_root=source,
        cache_root=cache,
        allow_url_rewrites=True,
    ).diagnose()
    assert linked_git.enabled is False
    assert linked_git.reason == "unsafe_git_directory"


def test_git_process_environment_drops_executable_and_config_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.publishing.git import _sanitized_process_environment

    for key in (
        "GIT_ASKPASS",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "GIT_DIR",
        "GIT_EXTERNAL_DIFF",
        "GIT_SSH_COMMAND",
        "GIT_TRACE_CURL",
        "SSH_ASKPASS",
    ):
        monkeypatch.setenv(key, "CANARY-GIT-INJECTION")
    monkeypatch.setenv("PAPER_READER_HARMLESS", "kept")

    sanitized = _sanitized_process_environment()

    assert "CANARY-GIT-INJECTION" not in sanitized.values()
    assert sanitized["PAPER_READER_HARMLESS"] == "kept"


@pytest.mark.skipif(GIT is None, reason="Git is required for the real worktree contract")
def test_first_publish_uses_orphan_history_allowlist_fixed_identity_and_preserves_source(
    tmp_path: Path,
) -> None:
    from paper_reader.publishing.git import GitPublisher

    source, remote = _repository(tmp_path / "git")
    _fixture, snapshot = _publication_snapshot(tmp_path / "snapshot")
    (source / "README.md").write_text("dirty user edit\n", encoding="utf-8")
    (source / "UNTRACKED-user-note.txt").write_text("do not touch\n", encoding="utf-8")
    before = _source_state(source)
    publisher = GitPublisher(
        repository_root=source,
        cache_root=tmp_path / "cache",
        allow_url_rewrites=True,
    )

    result = publisher.publish(snapshot, operation_id="publish-first")

    assert _source_state(source) == before
    assert not (source / "site").exists()
    assert not (source / ".github").exists()
    assert result.remote_name == "origin"
    assert result.remote_url == EXPECTED_REMOTE
    assert result.branch == "published"
    assert result.snapshot_id == snapshot.id
    assert result.snapshot_sha256 == snapshot.snapshot_sha256
    assert len(result.commit_sha) == 40
    assert str(tmp_path) not in result.model_dump_json()

    tree = _git(remote, "ls-tree", "-r", "--name-only", "published").stdout.decode().splitlines()
    assert tree == [
        ".github/workflows/pages.yml",
        "site/data.json",
        "site/index.html",
        "site/manifest.json",
    ]
    modes = {
        line.split()[0]
        for line in _git(remote, "ls-tree", "-r", "published").stdout.decode().splitlines()
    }
    assert modes == {"100644"}
    assert _git(remote, "show", "published:site/data.json").stdout == next(
        item.content for item in snapshot.files if item.relative_path == "data.json"
    )
    workflow = _git(
        remote,
        "show",
        "published:.github/workflows/pages.yml",
    ).stdout.decode("utf-8")
    assert "branches: [published]" in workflow
    assert "contents: read" in workflow
    assert "pages: write" in workflow
    assert "id-token: write" in workflow
    assert "actions/configure-pages@" in workflow
    assert "actions/upload-pages-artifact@" in workflow
    assert "actions/deploy-pages@" in workflow
    assert "path: site" in workflow
    assert "npm" not in workflow and "python" not in workflow

    identity = _git(remote, "show", "-s", "--format=%an%x00%ae", "published").stdout
    assert identity.strip() == b"paper-reader\x00publish@paper-reader.local"
    unrelated = _git(remote, "merge-base", "main", "published", check=False)
    assert unrelated.returncode != 0
    assert b"README.md" not in _git(remote, "ls-tree", "-r", "published").stdout
    registered = _git(source, "worktree", "list", "--porcelain").stdout.decode()
    assert str((tmp_path / "cache").resolve()) not in registered


@pytest.mark.skipif(GIT is None, reason="Git is required for the real worktree contract")
def test_existing_branch_push_is_non_force_and_concurrent_remote_update_is_retryable(
    tmp_path: Path,
) -> None:
    from paper_reader.publishing.git import (
        GitPublisher,
        GitPublishError,
        SubprocessGitRunner,
    )

    class RecordingRunner(SubprocessGitRunner):
        def __init__(self) -> None:
            super().__init__(git_executable=Path(GIT or "git"))
            self.calls: list[tuple[str, ...]] = []

        def run(self, arguments, **kwargs):  # type: ignore[no-untyped-def]
            self.calls.append(tuple(arguments))
            return super().run(arguments, **kwargs)

    source, remote = _repository(tmp_path / "git")
    _fixture, first = _publication_snapshot(tmp_path / "snapshot-one", title="第一版")
    _fixture_two, second = _publication_snapshot(tmp_path / "snapshot-two", title="第二版")
    publisher = GitPublisher(
        repository_root=source,
        cache_root=tmp_path / "cache",
        allow_url_rewrites=True,
    )
    first_result = publisher.publish(first, operation_id="publish-one")

    concurrent = tmp_path / "concurrent"
    advanced = False

    def advance_remote(phase: str) -> None:
        nonlocal advanced
        if phase != "before_push" or advanced:
            return
        advanced = True
        _git(tmp_path, "clone", str(remote), str(concurrent))
        _git(concurrent, "checkout", "published")
        (concurrent / "site" / "concurrent.txt").write_text("remote advanced", encoding="utf-8")
        _git(concurrent, "add", "site/concurrent.txt")
        _git(
            concurrent,
            "-c",
            "user.name=Concurrent Publisher",
            "-c",
            "user.email=concurrent@example.invalid",
            "commit",
            "--no-gpg-sign",
            "-m",
            "concurrent update",
        )
        _git(concurrent, "push", "origin", "HEAD:refs/heads/published")

    before = _source_state(source)
    racing = GitPublisher(
        repository_root=source,
        cache_root=tmp_path / "cache",
        phase_hook=advance_remote,
        allow_url_rewrites=True,
    )
    with pytest.raises(GitPublishError) as captured:
        racing.publish(second, operation_id="publish-racing")

    assert captured.value.code == "push_rejected"
    assert captured.value.retryable is True
    assert _source_state(source) == before
    assert _git(remote, "rev-parse", "published").stdout.strip() != first_result.commit_sha.encode()
    assert _git(remote, "show", "published:site/concurrent.txt").stdout == b"remote advanced"
    assert (
        str((tmp_path / "cache").resolve())
        not in _git(source, "worktree", "list", "--porcelain").stdout.decode()
    )

    runner = RecordingRunner()
    retry = GitPublisher(
        repository_root=source,
        cache_root=tmp_path / "cache",
        runner=runner,
        allow_url_rewrites=True,
    ).publish(second, operation_id="publish-retry")
    assert retry.snapshot_id == second.id
    push_calls = [call for call in runner.calls if "push" in call]
    assert push_calls
    assert push_calls[-1][-2:] == ("origin", "HEAD:refs/heads/published")
    assert all("--force" not in call and "-f" not in call for call in push_calls)


@pytest.mark.skipif(GIT is None, reason="Git is required for the real worktree contract")
def test_cancel_and_stale_worktree_probe_clean_safely_without_pruning_user_records(
    tmp_path: Path,
) -> None:
    from paper_reader.publishing.git import GitPublishCancelled, GitPublisher

    source, _remote = _repository(tmp_path / "git")
    _fixture, snapshot = _publication_snapshot(tmp_path / "snapshot")
    cancelled = False

    def cancel_after_files(phase: str) -> None:
        nonlocal cancelled
        if phase == "files_written":
            cancelled = True

    publisher = GitPublisher(
        repository_root=source,
        cache_root=tmp_path / "cache",
        phase_hook=cancel_after_files,
        allow_url_rewrites=True,
    )
    before = _source_state(source)
    with pytest.raises(GitPublishCancelled):
        publisher.publish(
            snapshot,
            operation_id="publish-cancelled",
            cancelled=lambda: cancelled,
        )
    assert _source_state(source) == before
    assert (
        str((tmp_path / "cache").resolve())
        not in _git(source, "worktree", "list", "--porcelain").stdout.decode()
    )

    stale = tmp_path / "stale-user-worktree"
    _git(source, "worktree", "add", "--detach", str(stale), "HEAD")
    shutil.rmtree(stale)
    diagnostic = GitPublisher(
        repository_root=source,
        cache_root=tmp_path / "cache",
        allow_url_rewrites=True,
    ).diagnose()
    assert diagnostic.stale_worktree_count >= 1
    still_registered = _git(source, "worktree", "list", "--porcelain").stdout.decode()
    assert stale.resolve().as_posix() in still_registered
