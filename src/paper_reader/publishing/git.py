"""Git boundary for publishing immutable snapshots from disposable worktrees."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from paper_reader.publishing.models import PublishedSnapshot
from paper_reader.publishing.privacy import canonical_json_bytes

EXPECTED_GITHUB_REMOTE = "https://github.com/Lisalsj/folioskiff.git"
PUBLISHING_REMOTE = "origin"
PUBLISHING_BRANCH = "published"
PUBLISHING_REF = "refs/heads/published"
PUBLISHING_SITE_ROOT = "site"
PUBLISHING_WORKFLOW_PATH = ".github/workflows/pages.yml"
PUBLISHING_IDENTITY_NAME = "paper-reader"
PUBLISHING_IDENTITY_EMAIL = "publish@paper-reader.local"

_OPERATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
_OBJECT_ID = re.compile(rb"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_PUBLIC_FILES = frozenset(
    {
        PUBLISHING_WORKFLOW_PATH,
        "site/index.html",
        "site/data.json",
        "site/manifest.json",
    }
)
_PUBLIC_DIRECTORIES = frozenset({".github", ".github/workflows", "site"})


class GitPublishError(RuntimeError):
    """A stable, path-free publishing failure suitable for a durable Job."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class GitPublishCancelled(GitPublishError):
    def __init__(self) -> None:
        super().__init__("cancelled", retryable=True)


@dataclass(frozen=True, slots=True)
class GitCommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class GitCommandFailure(RuntimeError):
    def __init__(self, result: GitCommandResult) -> None:
        super().__init__(f"Git command exited with status {result.returncode}")
        self.result = result


class GitRunner(Protocol):
    def run(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path,
        input_bytes: bytes | None = None,
        environment: Mapping[str, str] | None = None,
        accepted_returncodes: tuple[int, ...] = (0,),
        cancelled: Callable[[], bool] | None = None,
    ) -> GitCommandResult: ...


class SubprocessGitRunner:
    """Run Git without a shell, prompts, user-global config, or unbounded waits."""

    def __init__(
        self,
        *,
        git_executable: Path,
        timeout_seconds: float = 180,
    ) -> None:
        self.git_executable = Path(git_executable)
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path,
        input_bytes: bytes | None = None,
        environment: Mapping[str, str] | None = None,
        accepted_returncodes: tuple[int, ...] = (0,),
        cancelled: Callable[[], bool] | None = None,
    ) -> GitCommandResult:
        if cancelled is not None and cancelled():
            raise GitPublishCancelled()
        process_environment = _sanitized_process_environment()
        process_environment.update(
            {
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
                "GCM_INTERACTIVE": "Never",
                "GIT_PAGER": "cat",
                "LC_ALL": "C",
                "LANG": "C",
            }
        )
        if environment is not None:
            process_environment.update(environment)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            process = subprocess.Popen(
                (str(self.git_executable), *(str(item) for item in arguments)),
                cwd=cwd,
                stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=process_environment,
                shell=False,
                creationflags=creationflags,
            )
        except (FileNotFoundError, PermissionError, OSError) as error:
            raise GitCommandFailure(GitCommandResult(127, b"", b"")) from error

        started = time.monotonic()
        pending_input = input_bytes
        while True:
            if cancelled is not None and cancelled():
                _stop_process(process)
                raise GitPublishCancelled()
            if time.monotonic() - started > self.timeout_seconds:
                _stop_process(process)
                raise GitCommandFailure(GitCommandResult(124, b"", b""))
            try:
                stdout, stderr = process.communicate(input=pending_input, timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                pending_input = None
                continue
        result = GitCommandResult(process.returncode, stdout, stderr)
        if result.returncode not in accepted_returncodes:
            raise GitCommandFailure(result)
        return result


class GitPublishingDiagnostic(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool
    reason: str | None
    git_available: bool
    repository_detected: bool
    repository_root: str | None
    remote_name: str
    remote_url: str | None
    branch: str
    site_root: str
    workflow_path: str
    target_fingerprint: str | None
    stale_worktree_count: int = 0


class GitPublicationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    snapshot_id: str
    snapshot_sha256: str
    remote_name: str
    remote_url: str
    branch: str
    commit_sha: str
    workflow_path: str
    site_root: str


@dataclass(frozen=True, slots=True)
class _SourceState:
    head: bytes
    branch: bytes
    porcelain: bytes


def is_safe_publishing_remote(value: str) -> bool:
    """Accept exactly the credential-free HTTPS repository fixed by the product."""

    return value == EXPECTED_GITHUB_REMOTE


class GitPublisher:
    """Publish one frozen snapshot without checking out or editing the source tree."""

    def __init__(
        self,
        *,
        repository_root: Path,
        cache_root: Path,
        git_executable: Path | None = None,
        runner: GitRunner | None = None,
        expected_remote_url: str = EXPECTED_GITHUB_REMOTE,
        phase_hook: Callable[[str], None] | None = None,
        allow_url_rewrites: bool = False,
    ) -> None:
        discovered = shutil.which("git") if git_executable is None else str(git_executable)
        self._git_executable = Path(discovered) if discovered else None
        self._runner = runner or (
            SubprocessGitRunner(git_executable=self._git_executable)
            if self._git_executable is not None
            else None
        )
        self._repository_root = Path(repository_root)
        self._cache_root = Path(cache_root)
        self._expected_remote_url = expected_remote_url
        self._phase_hook = phase_hook
        self._allow_url_rewrites = allow_url_rewrites

    def diagnose(self) -> GitPublishingDiagnostic:
        if (
            self._runner is None
            or self._git_executable is None
            or not self._git_executable.is_file()
        ):
            return self._disabled("git_unavailable", git_available=False)
        candidate = self._repository_root
        if not candidate.is_dir():
            return self._disabled("not_a_repository")
        git_entry = candidate / ".git"
        if not git_entry.exists():
            return self._disabled("not_a_repository")
        if git_entry.is_symlink() or _is_junction(git_entry):
            return self._disabled("unsafe_git_directory")
        if not (git_entry.is_file() or git_entry.is_dir()):
            return self._disabled("not_a_repository")
        try:
            root_result = self._run_read(("rev-parse", "--show-toplevel"), cwd=candidate)
        except GitCommandFailure:
            return self._disabled("not_a_repository")
        discovered_root = Path(os.fsdecode(root_result.stdout.strip())).resolve()
        candidate_root = candidate.resolve()
        if _path_key(discovered_root) != _path_key(candidate_root):
            return self._disabled(
                "repository_root_mismatch",
                repository_detected=True,
                repository_root=str(discovered_root),
            )
        try:
            branch_result = self._run_read(
                ("symbolic-ref", "-q", "--short", "HEAD"),
                cwd=candidate_root,
                accepted_returncodes=(0, 1),
            )
            if branch_result.returncode != 0:
                return self._disabled(
                    "detached_head",
                    repository_detected=True,
                    repository_root=str(candidate_root),
                )
            source_branch = os.fsdecode(branch_result.stdout.strip())
            if source_branch == PUBLISHING_BRANCH:
                return self._disabled(
                    "source_is_published",
                    repository_detected=True,
                    repository_root=str(candidate_root),
                )
            urls = self._local_config_values("remote.origin.url", cwd=candidate_root)
            if len(urls) != 1 or urls[0] != self._expected_remote_url:
                return self._disabled(
                    "remote_url_mismatch",
                    repository_detected=True,
                    repository_root=str(candidate_root),
                    remote_url=urls[0] if len(urls) == 1 else None,
                )
            push_urls = self._local_config_values("remote.origin.pushurl", cwd=candidate_root)
            effective_push_urls = push_urls or urls
            if len(effective_push_urls) != 1 or effective_push_urls[0] != urls[0]:
                return self._disabled(
                    "push_url_mismatch",
                    repository_detected=True,
                    repository_root=str(candidate_root),
                    remote_url=urls[0],
                )
            dangerous = self._local_config_matches(
                r"^(credential\..*|include\..*|includeif\..*|"
                r"filter\..*|"
                r"http\..*(extraheader|cookiefile)|"
                r"remote\.origin\.(uploadpack|receivepack|proxy))$",
                cwd=candidate_root,
            )
            if dangerous:
                return self._disabled(
                    "unsafe_local_git_config",
                    repository_detected=True,
                    repository_root=str(candidate_root),
                    remote_url=urls[0],
                )
            rewrites = self._local_config_matches(r"^url\..*$", cwd=candidate_root)
            if rewrites and not self._allow_url_rewrites:
                return self._disabled(
                    "url_rewrite_forbidden",
                    repository_detected=True,
                    repository_root=str(candidate_root),
                    remote_url=urls[0],
                )
            if not self._allow_url_rewrites:
                effective_fetch = self._remote_urls(candidate_root, push=False)
                effective_push = self._remote_urls(candidate_root, push=True)
                if effective_fetch != urls or effective_push != urls:
                    return self._disabled(
                        "effective_remote_mismatch",
                        repository_detected=True,
                        repository_root=str(candidate_root),
                        remote_url=urls[0],
                    )
            if (
                self._expected_remote_url == EXPECTED_GITHUB_REMOTE
                and not is_safe_publishing_remote(urls[0])
            ):
                return self._disabled(
                    "remote_url_mismatch",
                    repository_detected=True,
                    repository_root=str(candidate_root),
                    remote_url=urls[0],
                )
            stale = self._run_read(
                ("worktree", "prune", "--dry-run", "--verbose"),
                cwd=candidate_root,
            )
        except GitCommandFailure:
            return self._disabled(
                "git_probe_failed",
                repository_detected=True,
                repository_root=str(candidate_root),
            )
        stale_count = len(
            [line for line in (stale.stdout + stale.stderr).splitlines() if line.strip()]
        )
        fingerprint = hashlib.sha256(
            canonical_json_bytes(
                {
                    "format_version": "paper-reader-git-target-v1",
                    "repository_root": str(candidate_root),
                    "remote_name": PUBLISHING_REMOTE,
                    "remote_url": urls[0],
                    "branch": PUBLISHING_BRANCH,
                    "site_root": PUBLISHING_SITE_ROOT,
                    "workflow_path": PUBLISHING_WORKFLOW_PATH,
                }
            )
        ).hexdigest()
        return GitPublishingDiagnostic(
            enabled=True,
            reason=None,
            git_available=True,
            repository_detected=True,
            repository_root=str(candidate_root),
            remote_name=PUBLISHING_REMOTE,
            remote_url=urls[0],
            branch=PUBLISHING_BRANCH,
            site_root=PUBLISHING_SITE_ROOT,
            workflow_path=PUBLISHING_WORKFLOW_PATH,
            target_fingerprint=fingerprint,
            stale_worktree_count=stale_count,
        )

    def publish(
        self,
        snapshot: PublishedSnapshot,
        *,
        operation_id: str,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> GitPublicationResult:
        if not isinstance(snapshot, PublishedSnapshot):
            raise TypeError("snapshot must be PublishedSnapshot")
        if not _OPERATION_ID.fullmatch(operation_id):
            raise ValueError("operation_id must be a safe 1-80 character identifier")
        diagnostic = self.diagnose()
        if not diagnostic.enabled or diagnostic.repository_root is None:
            raise GitPublishError(diagnostic.reason or "git_unavailable", retryable=False)
        repository_root = Path(diagnostic.repository_root)
        before = self._capture_source_state(repository_root)
        target = self._worktree_target(operation_id)
        added = False
        result: GitPublicationResult | None = None
        primary_error: BaseException | None = None

        try:
            self._check_cancelled(cancelled)
            self._prepare_target(repository_root, target)
            base_commit = self._published_base(repository_root, cancelled=cancelled)
            self._phase("base_resolved")
            self._check_cancelled(cancelled)
            self._run_write(
                (
                    "worktree",
                    "add",
                    "--detach",
                    "--no-checkout",
                    str(target),
                    base_commit,
                ),
                cwd=repository_root,
                cancelled=cancelled,
            )
            added = True
            self._require_registered_target(repository_root, target)
            self._phase("worktree_added")
            self._check_cancelled(cancelled)
            self._replace_worktree_contents(repository_root, target, snapshot)
            self._phase("files_written")
            self._check_cancelled(cancelled)
            commit_sha = self._commit_public_tree(
                target,
                snapshot_id=snapshot.id,
                cancelled=cancelled,
            )
            self._phase("before_push")
            self._check_cancelled(cancelled)
            try:
                self._run_write(
                    (
                        "push",
                        "--porcelain",
                        PUBLISHING_REMOTE,
                        f"HEAD:{PUBLISHING_REF}",
                    ),
                    cwd=target,
                    cancelled=cancelled,
                )
            except GitCommandFailure as error:
                raise GitPublishError("push_rejected", retryable=True) from error
            self._phase("pushed")
            result = GitPublicationResult(
                snapshot_id=snapshot.id,
                snapshot_sha256=snapshot.snapshot_sha256,
                remote_name=PUBLISHING_REMOTE,
                remote_url=diagnostic.remote_url or self._expected_remote_url,
                branch=PUBLISHING_BRANCH,
                commit_sha=commit_sha,
                workflow_path=PUBLISHING_WORKFLOW_PATH,
                site_root=PUBLISHING_SITE_ROOT,
            )
        except BaseException as error:
            primary_error = error

        if added:
            try:
                self._remove_registered_worktree(repository_root, target)
            except BaseException as cleanup_error:
                if primary_error is None:
                    primary_error = cleanup_error
        try:
            after = self._capture_source_state(repository_root)
        except GitCommandFailure as state_error:
            if primary_error is None:
                primary_error = GitPublishError("source_state_unavailable", retryable=False)
                primary_error.__cause__ = state_error
        else:
            if before != after:
                primary_error = GitPublishError("source_worktree_changed", retryable=False)

        if primary_error is not None:
            raise primary_error
        if result is None:  # pragma: no cover - guarded by primary_error above
            raise GitPublishError("internal_error", retryable=False)
        return result

    def _disabled(
        self,
        reason: str,
        *,
        git_available: bool = True,
        repository_detected: bool = False,
        repository_root: str | None = None,
        remote_url: str | None = None,
    ) -> GitPublishingDiagnostic:
        return GitPublishingDiagnostic(
            enabled=False,
            reason=reason,
            git_available=git_available,
            repository_detected=repository_detected,
            repository_root=repository_root,
            remote_name=PUBLISHING_REMOTE,
            remote_url=remote_url,
            branch=PUBLISHING_BRANCH,
            site_root=PUBLISHING_SITE_ROOT,
            workflow_path=PUBLISHING_WORKFLOW_PATH,
            target_fingerprint=None,
        )

    def _local_config_values(self, key: str, *, cwd: Path) -> tuple[str, ...]:
        result = self._run_read(
            ("config", "--local", "--get-all", key),
            cwd=cwd,
            accepted_returncodes=(0, 1),
        )
        if result.returncode == 1:
            return ()
        return tuple(os.fsdecode(line).strip() for line in result.stdout.splitlines() if line)

    def _local_config_matches(self, pattern: str, *, cwd: Path) -> tuple[str, ...]:
        result = self._run_read(
            ("config", "--local", "--get-regexp", pattern),
            cwd=cwd,
            accepted_returncodes=(0, 1),
        )
        if result.returncode == 1:
            return ()
        return tuple(os.fsdecode(line).strip() for line in result.stdout.splitlines() if line)

    def _remote_urls(self, cwd: Path, *, push: bool) -> tuple[str, ...]:
        arguments = ["remote", "get-url"]
        if push:
            arguments.append("--push")
        arguments.extend(("--all", PUBLISHING_REMOTE))
        result = self._run_read(tuple(arguments), cwd=cwd)
        return tuple(os.fsdecode(line).strip() for line in result.stdout.splitlines() if line)

    def _published_base(
        self,
        repository_root: Path,
        *,
        cancelled: Callable[[], bool],
    ) -> str:
        try:
            remote = self._run_write(
                (
                    "ls-remote",
                    "--exit-code",
                    "--heads",
                    PUBLISHING_REMOTE,
                    PUBLISHING_REF,
                ),
                cwd=repository_root,
                accepted_returncodes=(0, 2),
                cancelled=cancelled,
            )
        except GitCommandFailure as error:
            raise GitPublishError("remote_unavailable", retryable=True) from error
        if remote.returncode == 2:
            return self._empty_root(repository_root, cancelled=cancelled)
        lines = [line for line in remote.stdout.splitlines() if line.strip()]
        if len(lines) != 1:
            raise GitPublishError("remote_response_invalid", retryable=True)
        object_id = _parse_object_id(lines[0].split(maxsplit=1)[0])
        try:
            self._run_write(
                ("fetch", "--no-tags", PUBLISHING_REMOTE, object_id),
                cwd=repository_root,
                cancelled=cancelled,
            )
            self._run_read(("cat-file", "-e", f"{object_id}^{{commit}}"), cwd=repository_root)
        except GitCommandFailure as error:
            raise GitPublishError("remote_changed", retryable=True) from error
        return object_id

    def _empty_root(
        self,
        repository_root: Path,
        *,
        cancelled: Callable[[], bool],
    ) -> str:
        tree = _parse_object_id(
            self._run_write(
                ("mktree",),
                cwd=repository_root,
                input_bytes=b"",
                cancelled=cancelled,
            ).stdout
        )
        return _parse_object_id(
            self._run_write(
                ("commit-tree", tree),
                cwd=repository_root,
                input_bytes=b"paper-reader published root\n",
                environment=_identity_environment(),
                cancelled=cancelled,
            ).stdout
        )

    def _capture_source_state(self, repository_root: Path) -> _SourceState:
        return _SourceState(
            head=self._run_source_read(("rev-parse", "HEAD"), cwd=repository_root).stdout,
            branch=self._run_source_read(
                ("symbolic-ref", "--short", "HEAD"), cwd=repository_root
            ).stdout,
            porcelain=self._run_source_read(
                ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
                cwd=repository_root,
            ).stdout,
        )

    def _worktree_target(self, operation_id: str) -> Path:
        self._cache_root.mkdir(parents=True, exist_ok=True)
        cache_root = self._cache_root.resolve(strict=True)
        worktree_root = cache_root / "publishing" / "worktrees"
        worktree_root.mkdir(parents=True, exist_ok=True)
        worktree_root = worktree_root.resolve(strict=True)
        if not _is_within(worktree_root, cache_root):
            raise GitPublishError("unsafe_cache_root", retryable=False)
        name = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
        target = worktree_root / name
        if not _is_within(target.resolve(strict=False), cache_root):
            raise GitPublishError("unsafe_worktree_path", retryable=False)
        return target

    def _prepare_target(self, repository_root: Path, target: Path) -> None:
        registered = self._registered_worktree(repository_root, target)
        if registered is not None:
            self._validate_registered_target(target, registered)
            self._remove_registered_worktree(repository_root, target)
        if target.exists() or target.is_symlink():
            raise GitPublishError("unsafe_worktree_path", retryable=False)

    def _replace_worktree_contents(
        self,
        repository_root: Path,
        target: Path,
        snapshot: PublishedSnapshot,
    ) -> None:
        registered = self._require_registered_target(repository_root, target)
        self._validate_registered_target(target, registered)
        for child in target.iterdir():
            if child.name == ".git":
                continue
            _remove_child(child)
        expected_snapshot_paths = {"index.html", "data.json", "manifest.json"}
        snapshot_paths = {item.relative_path for item in snapshot.files}
        if snapshot_paths != expected_snapshot_paths:
            raise GitPublishError("snapshot_file_set_invalid", retryable=False)
        workflow = Path(__file__).with_name("assets") / "pages-workflow.yml"
        workflow_bytes = workflow.read_bytes()
        output: dict[str, bytes] = {PUBLISHING_WORKFLOW_PATH: workflow_bytes}
        output.update({f"site/{item.relative_path}": item.content for item in snapshot.files})
        for relative_path, content in output.items():
            destination = target.joinpath(*relative_path.split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        self._validate_public_tree(target)

    def _commit_public_tree(
        self,
        target: Path,
        *,
        snapshot_id: str,
        cancelled: Callable[[], bool],
    ) -> str:
        self._run_write(("read-tree", "--empty"), cwd=target, cancelled=cancelled)
        for relative_path in sorted(_PUBLIC_FILES):
            blob = _parse_object_id(
                self._run_write(
                    ("hash-object", "-w", "--no-filters", "--", relative_path),
                    cwd=target,
                    cancelled=cancelled,
                ).stdout
            )
            self._run_write(
                (
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"100644,{blob},{relative_path}",
                ),
                cwd=target,
                cancelled=cancelled,
            )
        tracked = self._run_read(("ls-files", "-z"), cwd=target).stdout
        tracked_names = {os.fsdecode(item) for item in tracked.split(b"\0") if item}
        if tracked_names != _PUBLIC_FILES:
            raise GitPublishError("public_file_allowlist_failed", retryable=False)
        tree = _parse_object_id(self._run_write(("write-tree",), cwd=target).stdout)
        parent = _parse_object_id(self._run_read(("rev-parse", "HEAD"), cwd=target).stdout)
        parent_tree = _parse_object_id(
            self._run_read(("rev-parse", "HEAD^{tree}"), cwd=target).stdout
        )
        if tree == parent_tree:
            return parent
        commit = _parse_object_id(
            self._run_write(
                ("commit-tree", tree, "-p", parent),
                cwd=target,
                input_bytes=f"publish: {snapshot_id}\n".encode(),
                environment=_identity_environment(),
                cancelled=cancelled,
            ).stdout
        )
        self._run_write(
            ("update-ref", "HEAD", commit, parent),
            cwd=target,
            cancelled=cancelled,
        )
        return commit

    def _validate_public_tree(self, target: Path) -> None:
        files: set[str] = set()
        directories: set[str] = set()
        pending = [target]
        while pending:
            directory = pending.pop()
            for child in directory.iterdir():
                if directory == target and child.name == ".git":
                    continue
                relative = child.relative_to(target).as_posix()
                if child.is_symlink() or _is_junction(child):
                    raise GitPublishError("public_tree_link_forbidden", retryable=False)
                if child.is_dir():
                    directories.add(relative)
                    pending.append(child)
                    continue
                if not child.is_file() or child.stat(follow_symlinks=False).st_nlink != 1:
                    raise GitPublishError("public_tree_link_forbidden", retryable=False)
                files.add(relative)
        if files != _PUBLIC_FILES or not directories.issubset(_PUBLIC_DIRECTORIES):
            raise GitPublishError("public_file_allowlist_failed", retryable=False)

    def _registered_worktree(
        self,
        repository_root: Path,
        target: Path,
    ) -> Path | None:
        result = self._run_read(("worktree", "list", "--porcelain", "-z"), cwd=repository_root)
        target_key = _path_key(target.resolve(strict=False))
        for item in result.stdout.split(b"\0"):
            if not item.startswith(b"worktree "):
                continue
            candidate = Path(os.fsdecode(item[len(b"worktree ") :])).resolve(strict=False)
            if _path_key(candidate) == target_key:
                return candidate
        return None

    def _require_registered_target(self, repository_root: Path, target: Path) -> Path:
        registered = self._registered_worktree(repository_root, target)
        if registered is None:
            raise GitPublishError("worktree_registration_missing", retryable=False)
        self._validate_registered_target(target, registered)
        return registered

    def _validate_registered_target(self, target: Path, registered: Path) -> None:
        cache_root = self._cache_root.resolve(strict=True)
        target_resolved = target.resolve(strict=False)
        if (
            not _is_within(target_resolved, cache_root)
            or _path_key(target_resolved) != _path_key(registered.resolve(strict=False))
            or target.is_symlink()
            or _is_junction(target)
        ):
            raise GitPublishError("unsafe_worktree_path", retryable=False)

    def _remove_registered_worktree(self, repository_root: Path, target: Path) -> None:
        registered = self._require_registered_target(repository_root, target)
        self._validate_registered_target(target, registered)
        try:
            self._run_write(
                ("worktree", "remove", "--force", str(target)),
                cwd=repository_root,
            )
        except GitCommandFailure:
            if target.exists():
                self._validate_registered_target(target, registered)
                shutil.rmtree(target)
            try:
                self._run_write(
                    ("worktree", "remove", "--force", str(target)),
                    cwd=repository_root,
                )
            except GitCommandFailure as error:
                raise GitPublishError("worktree_cleanup_failed", retryable=True) from error
        if self._registered_worktree(repository_root, target) is not None or target.exists():
            raise GitPublishError("worktree_cleanup_failed", retryable=True)

    def _phase(self, value: str) -> None:
        if self._phase_hook is not None:
            self._phase_hook(value)

    @staticmethod
    def _check_cancelled(cancelled: Callable[[], bool]) -> None:
        if cancelled():
            raise GitPublishCancelled()

    def _run_read(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path,
        accepted_returncodes: tuple[int, ...] = (0,),
    ) -> GitCommandResult:
        if self._runner is None:
            raise GitCommandFailure(GitCommandResult(127, b"", b""))
        return self._runner.run(
            ("-c", "core.fsmonitor=false", *arguments),
            cwd=cwd,
            environment={"GIT_OPTIONAL_LOCKS": "0"},
            accepted_returncodes=accepted_returncodes,
        )

    def _run_write(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path,
        input_bytes: bytes | None = None,
        environment: Mapping[str, str] | None = None,
        accepted_returncodes: tuple[int, ...] = (0,),
        cancelled: Callable[[], bool] | None = None,
    ) -> GitCommandResult:
        if self._runner is None:
            raise GitCommandFailure(GitCommandResult(127, b"", b""))
        hooks = self._cache_root / "publishing" / "empty-hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        safe_arguments = ("-c", f"core.hooksPath={hooks.resolve()}", *arguments)
        return self._runner.run(
            safe_arguments,
            cwd=cwd,
            input_bytes=input_bytes,
            environment=environment,
            accepted_returncodes=accepted_returncodes,
            cancelled=cancelled,
        )

    def _run_source_read(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path,
    ) -> GitCommandResult:
        if self._runner is None:
            raise GitCommandFailure(GitCommandResult(127, b"", b""))
        return self._runner.run(
            ("-c", "core.fsmonitor=false", *arguments),
            cwd=cwd,
            environment={
                "GIT_ATTR_NOSYSTEM": "1",
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_OPTIONAL_LOCKS": "0",
            },
        )


def _identity_environment() -> dict[str, str]:
    return {
        "GIT_AUTHOR_NAME": PUBLISHING_IDENTITY_NAME,
        "GIT_AUTHOR_EMAIL": PUBLISHING_IDENTITY_EMAIL,
        "GIT_COMMITTER_NAME": PUBLISHING_IDENTITY_NAME,
        "GIT_COMMITTER_EMAIL": PUBLISHING_IDENTITY_EMAIL,
    }


def _sanitized_process_environment() -> dict[str, str]:
    value = os.environ.copy()
    exact = {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_ASKPASS",
        "GIT_ATTR_SOURCE",
        "GIT_CEILING_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_DIFF_OPTS",
        "GIT_DIR",
        "GIT_EDITOR",
        "GIT_EXEC_PATH",
        "GIT_EXTERNAL_DIFF",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_PROXY_COMMAND",
        "GIT_SEQUENCE_EDITOR",
        "GIT_SSH",
        "GIT_SSH_COMMAND",
        "GIT_WORK_TREE",
        "SSH_ASKPASS",
    }
    for key in tuple(value):
        normalized = key.upper()
        if (
            normalized in exact
            or normalized.startswith("GIT_CONFIG_KEY_")
            or normalized.startswith("GIT_CONFIG_VALUE_")
            or normalized.startswith("GIT_TRACE")
        ):
            value.pop(key, None)
    return value


def _parse_object_id(value: bytes) -> str:
    stripped = value.strip().split(maxsplit=1)[0] if value.strip() else b""
    if not _OBJECT_ID.fullmatch(stripped):
        raise GitPublishError("git_object_invalid", retryable=False)
    return stripped.decode("ascii")


def _path_key(value: Path) -> str:
    return os.path.normcase(os.path.normpath(str(value)))


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return candidate != root


def _is_junction(path: Path) -> bool:
    checker = getattr(path, "is_junction", None)
    return bool(checker()) if callable(checker) else False


def _remove_child(path: Path) -> None:
    if path.is_symlink() or _is_junction(path) or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        raise GitPublishError("unsafe_worktree_entry", retryable=False)


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    try:
        process.terminate()
        process.communicate(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        process.kill()
        process.communicate()
