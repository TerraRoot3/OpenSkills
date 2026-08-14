#!/usr/bin/env python3
"""Deterministic GitHub/GitLab test and production publish workflow."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

EXIT_CONFLICT = 3
EXIT_WAITING = 4
EXIT_SYNC_RECOMMENDED = 5
DEFAULT_COMMAND_TIMEOUT_SECONDS = 300
MAX_COMMAND_TIMEOUT_SECONDS = 3600
DEFAULT_MAIN_BEHIND_THRESHOLD = 100
DEFAULT_MAIN_OVERLAP_THRESHOLD = 20
COMMAND_TIMEOUT_SECONDS = DEFAULT_COMMAND_TIMEOUT_SECONDS
PROFILE_PATH = (
    Path(__file__).resolve().parent.parent / "references" / "repository-profiles.json"
)
SEMVER_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SAFE_COMMAND_RE = re.compile(r"^[A-Za-z0-9._+-]+$")
PROFILE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SHARED_BRANCHES = {"main", "master", "dev", "release", "pre"}
INVALID_EMAIL_DOMAINS = {
    "localhost",
    "local",
    "invalid",
    "test",
    "example.com",
    "example.net",
    "example.org",
}


class PublishError(RuntimeError):
    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class RemoteInfo:
    provider: str
    host: str
    namespace: str
    repository: str

    @property
    def slug(self) -> str:
        return f"{self.namespace}/{self.repository}"

    @property
    def fingerprint(self) -> str:
        return f"{self.provider}:{self.host}/{self.slug}".casefold()


@dataclass(frozen=True)
class RuleSource:
    kind: str
    location: str
    statement: str


@dataclass(frozen=True)
class ProductionProfile:
    workflow: str
    blocking_statuses: tuple[str, ...]
    reuse_successful_tag_for_same_sha: bool
    require_reason_after_unsuccessful_tag: bool
    monitor_required: bool
    runtime_verification_required: bool


@dataclass(frozen=True)
class RepositoryProfile:
    profile_id: str
    display_name: str
    remote_fingerprint: str
    rule_sources: tuple[RuleSource, ...]
    conflict_summary: str
    effective_rule: str
    project_files_modified: bool
    mainline_sync_policy: str
    main_behind_threshold: int
    main_overlap_files_threshold: int
    forbid_dev_into_source: bool
    expected_login: str
    production: ProductionProfile


@dataclass(frozen=True)
class TagDecision:
    tag: str
    sha: str
    reused: bool


@dataclass(frozen=True)
class MainlineDivergence:
    main: str
    source_ahead: int
    source_behind: int
    overlapping_files: tuple[str, ...]
    behind_threshold: int
    overlap_threshold: int

    @property
    def is_large(self) -> bool:
        return (
            self.source_behind >= self.behind_threshold
            or len(self.overlapping_files) >= self.overlap_threshold
        )


def log(message: str) -> None:
    print(f"[publish] {message}", flush=True)


def command_text(args: Sequence[str]) -> str:
    return " ".join(shlex.quote(value) for value in args)


def terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=3)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait()


def run(
    args: Sequence[str],
    *,
    cwd: Path,
    check: bool = True,
    show: bool = False,
    env: dict[str, str] | None = None,
    timeout_seconds: float | None = None,
) -> subprocess.CompletedProcess[str]:
    if show:
        log(f"run: {command_text(args)}")
    timeout = COMMAND_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    process = subprocess.Popen(
        list(args),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        terminate_process_tree(process)
        process.communicate()
        raise PublishError(
            f"command timed out after {timeout:g}s: {command_text(args)}"
        ) from exc
    except BaseException:
        terminate_process_tree(process)
        process.communicate()
        raise
    result = subprocess.CompletedProcess(
        list(args),
        process.returncode,
        stdout,
        stderr,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise PublishError(
            f"command failed ({result.returncode}): {command_text(args)}"
            + (f"\n{detail}" if detail else "")
        )
    return result


def git(
    repo: Path, *args: str, check: bool = True, show: bool = False
) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=repo, check=check, show=show)


def git_output(repo: Path, *args: str) -> str:
    return git(repo, *args).stdout.strip()


def resolve_repo(path: str) -> Path:
    candidate = Path(path).expanduser().resolve()
    root = run(["git", "rev-parse", "--show-toplevel"], cwd=candidate).stdout.strip()
    return Path(root).resolve()


def current_branch(repo: Path) -> str:
    branch = git_output(repo, "branch", "--show-current")
    if not branch:
        raise PublishError("detached HEAD is not a publish source branch")
    return branch


def git_path(repo: Path, name: str) -> Path:
    value = git_output(repo, "rev-parse", "--git-path", name)
    path = Path(value)
    return path if path.is_absolute() else (repo / path).resolve()


def ensure_no_operation(repo: Path) -> None:
    markers = {
        "MERGE_HEAD": "merge",
        "CHERRY_PICK_HEAD": "cherry-pick",
        "REVERT_HEAD": "revert",
    }
    for marker, operation in markers.items():
        if git_path(repo, marker).exists():
            raise PublishError(
                f"an unfinished {operation} exists; resolve or abort it, then rerun"
            )
    for marker in ("rebase-merge", "rebase-apply"):
        if git_path(repo, marker).exists():
            raise PublishError(
                "an unfinished rebase exists; resolve or abort it, then rerun"
            )


def ensure_source_branch(branch: str) -> None:
    validate_branch_name(branch, "source branch")
    if branch in SHARED_BRANCHES or branch.startswith(("dev-", "pre-")):
        raise PublishError(f"source branch must be a demand branch, got {branch!r}")


def validate_branch_name(branch: str, context: str) -> None:
    if not branch or branch.startswith("-"):
        raise PublishError(f"invalid {context}: {branch!r}")
    result = run(
        ["git", "check-ref-format", "--branch", branch],
        cwd=Path.cwd(),
        check=False,
    )
    if result.returncode != 0:
        raise PublishError(f"invalid {context}: {branch!r}")


def validate_remote_name(remote: str) -> None:
    if (
        not remote
        or remote.startswith("-")
        or not re.fullmatch(r"[A-Za-z0-9._/-]+", remote)
    ):
        raise PublishError(f"invalid remote name: {remote!r}")


def ensure_mainline_branch(branch: str) -> None:
    validate_branch_name(branch, "mainline branch")
    if branch not in {"main", "master"}:
        raise PublishError(
            f"mainline must be main or master; refusing branch {branch!r}"
        )


def ensure_test_branch(branch: str, source: str) -> None:
    validate_branch_name(branch, "test branch")
    if branch == source or branch in {"main", "master", "release", "pre"}:
        raise PublishError(
            f"test target must be a dedicated environment branch, got {branch!r}"
        )


def validate_identity(repo: Path) -> None:
    for variable in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
        identity = git_output(repo, "var", variable)
        match = re.search(r"<([^>]+)>", identity)
        if not match:
            raise PublishError(f"cannot parse {variable}: {identity}")
        email = match.group(1).strip().lower()
        domain = email.rsplit("@", 1)[-1] if "@" in email else ""
        if (
            not email
            or domain in INVALID_EMAIL_DOMAINS
            or domain.endswith((".local", ".localhost", ".invalid", ".test"))
        ):
            raise PublishError(f"invalid {variable} email: {email}")


def worktree_state(repo: Path) -> tuple[bool, bool, list[str]]:
    staged = git(repo, "diff", "--cached", "--quiet", check=False).returncode == 1
    unstaged = git(repo, "diff", "--quiet", check=False).returncode == 1
    untracked_text = git_output(repo, "ls-files", "--others", "--exclude-standard")
    untracked = [line for line in untracked_text.splitlines() if line]
    return staged, unstaged, untracked


def prepare_source(repo: Path, commit_message: str | None) -> str:
    ensure_no_operation(repo)
    source = current_branch(repo)
    ensure_source_branch(source)
    staged, unstaged, untracked = worktree_state(repo)
    if unstaged or untracked:
        details = []
        if unstaged:
            details.append("unstaged tracked changes")
        if untracked:
            details.append(f"untracked files: {', '.join(untracked[:8])}")
        raise PublishError(
            "worktree is not clean after the task allowlist was staged; "
            + "; ".join(details)
        )
    if staged:
        if not commit_message:
            raise PublishError("staged changes require --commit-message")
        validate_identity(repo)
        git(repo, "diff", "--cached", "--check", show=True)
        git(repo, "commit", "-m", commit_message, show=True)
    return source


def run_verifications(repo: Path, commands: Sequence[str]) -> None:
    git(repo, "diff", "--check", show=True)
    for verify_command in commands:
        log(f"verify: {verify_command}")
        result = run(
            ["/bin/zsh", "-lc", verify_command],
            cwd=repo,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise PublishError(
                f"verification failed ({result.returncode}): {verify_command}"
                + (f"\n{detail}" if detail else "")
            )


def remote_ref(remote: str, branch: str) -> str:
    return f"refs/remotes/{remote}/{branch}"


def fetch_branch(repo: Path, remote: str, branch: str) -> bool:
    result = git(
        repo,
        "fetch",
        remote,
        f"+refs/heads/{branch}:{remote_ref(remote, branch)}",
        "--prune",
        check=False,
        show=True,
    )
    return result.returncode == 0


def rev_parse(repo: Path, ref: str) -> str | None:
    result = git(repo, "rev-parse", "--verify", ref, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return (
        git(
            repo, "merge-base", "--is-ancestor", ancestor, descendant, check=False
        ).returncode
        == 0
    )


def branch_worktrees(repo: Path, branch: str) -> list[str]:
    output = git_output(repo, "worktree", "list", "--porcelain")
    paths: list[str] = []
    current_path: str | None = None
    for line in output.splitlines():
        if line.startswith("worktree "):
            current_path = line.removeprefix("worktree ")
        elif line == f"branch refs/heads/{branch}" and current_path:
            paths.append(current_path)
    return paths


def rebuild_local_environment_branch(repo: Path, remote: str, branch: str) -> None:
    local_ref = f"refs/heads/{branch}"
    tracked_ref = remote_ref(remote, branch)
    remote_sha = rev_parse(repo, tracked_ref)
    if not remote_sha:
        raise PublishError(f"remote environment branch is missing: {remote}/{branch}")
    local_sha = rev_parse(repo, local_ref)
    if not local_sha:
        git(repo, "branch", "--track", branch, tracked_ref, show=True)
        log(f"local {branch} created from {remote}/{branch} @ {remote_sha}")
        return
    if local_sha == remote_sha:
        log(f"local {branch} already matches {remote}/{branch} @ {remote_sha}")
        return
    counts = git_output(
        repo, "rev-list", "--left-right", "--count", f"{local_ref}...{tracked_ref}"
    )
    ahead_text, behind_text = counts.split()
    ahead = int(ahead_text)
    behind = int(behind_text)
    if ahead > 0 and behind == 0:
        raise PublishError(
            f"local {branch} is ahead of {remote}/{branch} by {ahead} commit(s); "
            "refusing to discard unpublished environment commits automatically"
        )
    checked_out = branch_worktrees(repo, branch)
    if checked_out:
        raise PublishError(
            f"local {branch} differs from {remote}/{branch} but is checked out in worktree(s): "
            + ", ".join(checked_out)
            + "; remove or switch those worktrees, then rerun"
        )
    log(
        f"rebuilding diverged local {branch}: old={local_sha}, "
        f"ahead={ahead}, behind={behind}, remote={remote_sha}"
    )
    git(repo, "branch", "-D", branch, show=True)
    git(repo, "branch", "--track", branch, tracked_ref, show=True)
    rebuilt_sha = rev_parse(repo, local_ref)
    if rebuilt_sha != remote_sha:
        raise PublishError(
            f"local {branch} rebuild verification failed: local={rebuilt_sha}, remote={remote_sha}"
        )


def ls_remote_sha(repo: Path, remote: str, ref: str) -> str | None:
    result = git(repo, "ls-remote", remote, ref, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise PublishError(f"cannot read remote ref {ref}: {detail}")
    line = next((line for line in result.stdout.splitlines() if line.strip()), "")
    return line.split()[0] if line else None


def push_source(repo: Path, remote: str, source: str) -> str:
    local_sha = git_output(repo, "rev-parse", "HEAD")
    exists = fetch_branch(repo, remote, source)
    tracked = remote_ref(remote, source)
    if exists:
        remote_sha = rev_parse(repo, tracked)
        if remote_sha == local_sha:
            log(f"source already pushed: {source} @ {local_sha}")
            return local_sha
        if not remote_sha or not is_ancestor(repo, remote_sha, local_sha):
            raise PublishError(
                f"remote {source} is ahead or diverged; inspect it before pushing"
            )
        git(repo, "push", remote, f"HEAD:refs/heads/{source}", show=True)
    else:
        git(repo, "push", "-u", remote, f"HEAD:refs/heads/{source}", show=True)
    verified = ls_remote_sha(repo, remote, f"refs/heads/{source}")
    if verified != local_sha:
        raise PublishError(
            f"source push verification failed: local={local_sha}, remote={verified}"
        )
    log(f"source pushed: {source} @ {local_sha}")
    return local_sha


def parse_remote(raw: str, provider_override: str) -> RemoteInfo:
    scp_match = re.match(r"^(?:[^/@:]+@)?([^/:]+):(.+)$", raw)
    if scp_match and not re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.IGNORECASE):
        host = scp_match.group(1).lower()
        path = scp_match.group(2)
    else:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        path = parsed.path
    segments = [
        part for part in path.strip("/").removesuffix(".git").split("/") if part
    ]
    if len(segments) < 2 or not host:
        raise PublishError("cannot parse remote host and repository namespace")
    inferred_provider = None
    if host == "github.com" or host.endswith(".github.com"):
        inferred_provider = "github"
    elif "gitlab" in host:
        inferred_provider = "gitlab"
    provider = provider_override
    if provider == "auto":
        if not inferred_provider:
            raise PublishError(
                f"unsupported forge host {host!r}; pass --provider github or gitlab"
            )
        provider = inferred_provider
    elif inferred_provider and provider != inferred_provider:
        raise PublishError(
            f"provider override {provider!r} conflicts with forge host {host!r}"
        )
    return RemoteInfo(provider, host, "/".join(segments[:-1]), segments[-1])


def resolve_main_branch(repo: Path, remote: str, requested: str) -> str:
    if requested != "auto":
        return requested
    symbolic = git(repo, "symbolic-ref", f"refs/remotes/{remote}/HEAD", check=False)
    if symbolic.returncode == 0:
        return symbolic.stdout.strip().rsplit("/", 1)[-1]
    for candidate in ("main", "master"):
        if fetch_branch(repo, remote, candidate):
            return candidate
    raise PublishError("cannot resolve mainline; pass --main-branch explicitly")


def validate_thresholds(behind_threshold: int, overlap_threshold: int) -> None:
    if behind_threshold < 1:
        raise PublishError("--main-behind-threshold must be at least 1")
    if overlap_threshold < 1:
        raise PublishError("--main-overlap-files-threshold must be at least 1")


def changed_paths(repo: Path, base: str, tip: str) -> set[str]:
    output = git_output(repo, "diff", "--name-only", f"{base}..{tip}")
    return {line for line in output.splitlines() if line}


def assess_mainline_divergence(
    repo: Path,
    remote: str,
    main: str,
    behind_threshold: int,
    overlap_threshold: int,
    source_ref: str = "HEAD",
    include_staged_paths: bool = False,
) -> MainlineDivergence:
    validate_thresholds(behind_threshold, overlap_threshold)
    main_ref = remote_ref(remote, main)
    merge_base_result = git(repo, "merge-base", source_ref, main_ref, check=False)
    merge_base = merge_base_result.stdout.strip()
    if merge_base_result.returncode != 0 or not merge_base:
        raise PublishError(
            f"demand branch and {remote}/{main} have no verified common base; inspect before publishing"
        )
    counts = git_output(
        repo, "rev-list", "--left-right", "--count", f"{source_ref}...{main_ref}"
    )
    ahead_text, behind_text = counts.split()
    source_paths = changed_paths(repo, merge_base, source_ref)
    if include_staged_paths:
        staged_output = git_output(repo, "diff", "--cached", "--name-only")
        source_paths.update(line for line in staged_output.splitlines() if line)
    main_paths = changed_paths(repo, merge_base, main_ref)
    divergence = MainlineDivergence(
        main=main,
        source_ahead=int(ahead_text),
        source_behind=int(behind_text),
        overlapping_files=tuple(sorted(source_paths & main_paths)),
        behind_threshold=behind_threshold,
        overlap_threshold=overlap_threshold,
    )
    level = "large" if divergence.is_large else "small"
    log(
        f"demand/mainline divergence is {level}: source_ahead={divergence.source_ahead}, "
        f"source_behind={divergence.source_behind}, "
        f"overlapping_files={len(divergence.overlapping_files)}, "
        f"thresholds=behind:{behind_threshold}/overlap:{overlap_threshold}"
    )
    return divergence


def divergence_payload(divergence: MainlineDivergence) -> dict[str, Any]:
    return {
        "status": "checked",
        "main": divergence.main,
        "source_ahead": divergence.source_ahead,
        "source_behind": divergence.source_behind,
        "overlapping_files": list(divergence.overlapping_files),
        "behind_threshold": divergence.behind_threshold,
        "overlap_threshold": divergence.overlap_threshold,
        "is_large": divergence.is_large,
        "recommendation": (
            "discard this isolated dev-conflict worktree; after explicit authorization, "
            "merge only mainline into the demand branch and rerun test publication"
            if divergence.is_large
            else "keep the demand branch unchanged and resolve only the dev integration conflict in the isolated worktree"
        ),
    }


def diagnose_test_conflict_against_mainline(
    repo: Path,
    remote: str,
    main: str,
    source_sha: str,
    behind_threshold: int,
    overlap_threshold: int,
) -> dict[str, Any]:
    try:
        if not fetch_branch(repo, remote, main):
            raise PublishError(f"remote mainline does not exist: {remote}/{main}")
        divergence = assess_mainline_divergence(
            repo,
            remote,
            main,
            behind_threshold,
            overlap_threshold,
            source_ref=source_sha,
        )
        return divergence_payload(divergence)
    except PublishError as exc:
        return {
            "status": "unavailable",
            "main": main,
            "error": str(exc),
            "recommendation": "preserve the isolated dev-conflict worktree and inspect mainline separately; never merge dev into the demand branch",
        }


def merge_mainline_into_source(
    repo: Path,
    remote: str,
    main: str,
    source: str,
    verify: Sequence[str],
) -> None:
    main_ref = remote_ref(remote, main)
    if is_ancestor(repo, main_ref, "HEAD"):
        log(f"source already contains latest {remote}/{main}")
        run_verifications(repo, verify)
        return
    log(f"syncing latest {remote}/{main} into {source}")
    merge = git(repo, "merge", "--no-ff", "--no-edit", main_ref, check=False, show=True)
    if merge.returncode != 0:
        unresolved = git_output(repo, "diff", "--name-only", "--diff-filter=U")
        if unresolved:
            print(
                "Resolve conflicts on the demand branch, git add the resolved files, "
                "commit the mainline merge, then rerun the same command."
            )
            raise PublishError(
                "mainline sync requires conflict resolution", EXIT_CONFLICT
            )
        detail = (merge.stderr or merge.stdout).strip()
        raise PublishError(f"mainline sync failed: {detail}")
    run_verifications(repo, verify)


def forge_run(
    command: str, args: Sequence[str], repo: Path
) -> subprocess.CompletedProcess[str]:
    if not SAFE_COMMAND_RE.fullmatch(command):
        raise PublishError(f"unsafe forge command name: {command!r}")
    executable = shutil.which(command)
    if executable:
        return run([executable, *args], cwd=repo, check=False)
    shell = Path(os.environ.get("SHELL", "/bin/zsh"))
    if shell.name != "zsh":
        shell = Path("/bin/zsh")
    env = dict(os.environ)
    env.setdefault("TERM", "xterm-256color")
    return run(
        [str(shell), "-lic", f'{command} "$@"', "publish-forge", *args],
        cwd=repo,
        check=False,
        env=env,
    )


def forge_checked(command: str, args: Sequence[str], repo: Path) -> str:
    result = forge_run(command, args, repo)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise PublishError(
            f"forge command failed ({result.returncode}): {command_text([command, *args])}"
            + (f"\n{detail}" if detail else "")
        )
    return result.stdout.strip()


def mapped_expected_login(info: RemoteInfo) -> str | None:
    if info.provider == "github":
        owner = info.namespace.split("/", 1)[0].casefold()
        if owner == "terraroot3":
            return "TerraRoot3"
        if owner in {"hanbaokun", "pagepop"}:
            return "hanbaokun"
    elif info.host in {
        "gitlab.epian1.com",
        "gitlab.pophie.com",
        "gitlab.meipian.cn",
    }:
        return "hanbaokun"
    return None


def verify_forge_identity(
    repo: Path,
    info: RemoteInfo,
    command: str,
    expected_login: str | None,
) -> str:
    mapped_login = mapped_expected_login(info)
    if (
        expected_login
        and mapped_login
        and expected_login.casefold() != mapped_login.casefold()
    ):
        raise PublishError(
            f"--expected-login {expected_login!r} conflicts with the repository mapping {mapped_login!r}"
        )
    required_login = expected_login or mapped_login
    if info.provider == "github":
        login = forge_checked(command, ["api", "user", "--jq", ".login"], repo)
    else:
        payload = forge_checked(
            command, ["api", "/user", "--hostname", info.host], repo
        )
        login = str(json.loads(payload).get("username") or "")
    if not login:
        raise PublishError(f"cannot determine active {info.provider} login")
    if required_login and login.casefold() != required_login.casefold():
        raise PublishError(
            f"forge login mismatch: expected {required_login}, active {login}"
        )
    log(f"forge identity: {info.host} -> {login}")
    return login


def load_json(text: str, context: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise PublishError(f"invalid JSON from {context}: {exc}") from exc


def require_exact_keys(value: dict[str, Any], expected: set[str], context: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise PublishError(
            f"invalid {context} keys: missing={missing or 'none'}, extra={extra or 'none'}"
        )


def require_nonempty_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PublishError(f"{context} must be a non-empty string")
    return value.strip()


def require_bool(value: Any, context: str) -> bool:
    if not isinstance(value, bool):
        raise PublishError(f"{context} must be a boolean")
    return value


def require_positive_int(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PublishError(f"{context} must be a positive integer")
    return value


def load_repository_profiles(path: Path | None = None) -> tuple[RepositoryProfile, ...]:
    profile_path = (path or PROFILE_PATH).resolve()
    if not profile_path.is_file():
        raise PublishError(f"repository profile file is missing: {profile_path}")
    payload = load_json(
        profile_path.read_text(encoding="utf-8"),
        f"repository profile file {profile_path}",
    )
    if not isinstance(payload, dict):
        raise PublishError("repository profile file must contain a JSON object")
    require_exact_keys(payload, {"version", "profiles"}, "repository profile file")
    if payload["version"] != 1:
        raise PublishError("unsupported repository profile version")
    raw_profiles = payload["profiles"]
    if not isinstance(raw_profiles, list):
        raise PublishError("repository profile profiles must be a list")

    profiles: list[RepositoryProfile] = []
    seen_ids: set[str] = set()
    seen_fingerprints: set[str] = set()
    for index, raw_profile in enumerate(raw_profiles):
        context = f"repository profile #{index + 1}"
        if not isinstance(raw_profile, dict):
            raise PublishError(f"{context} must be an object")
        require_exact_keys(
            raw_profile,
            {
                "id",
                "display_name",
                "remote_fingerprint",
                "rule_sources",
                "rule_conflict",
                "test",
                "identity",
                "production",
            },
            context,
        )
        profile_id = require_nonempty_string(raw_profile["id"], f"{context}.id")
        if not PROFILE_ID_RE.fullmatch(profile_id):
            raise PublishError(f"{context}.id must use lowercase hyphen-case")
        if profile_id in seen_ids:
            raise PublishError(f"duplicate repository profile id: {profile_id}")
        seen_ids.add(profile_id)

        fingerprint = require_nonempty_string(
            raw_profile["remote_fingerprint"], f"{context}.remote_fingerprint"
        )
        if fingerprint != fingerprint.casefold() or not re.fullmatch(
            r"(?:github|gitlab):[a-z0-9.-]+/[^/\s]+/[^/\s]+", fingerprint
        ):
            raise PublishError(
                f"{context}.remote_fingerprint must be a normalized provider:host/owner/repository value"
            )
        if fingerprint in seen_fingerprints:
            raise PublishError(
                f"duplicate repository remote fingerprint: {fingerprint}"
            )
        seen_fingerprints.add(fingerprint)

        raw_sources = raw_profile["rule_sources"]
        if not isinstance(raw_sources, list) or len(raw_sources) < 2:
            raise PublishError(
                f"{context}.rule_sources must contain at least two entries"
            )
        sources: list[RuleSource] = []
        for source_index, raw_source in enumerate(raw_sources):
            source_context = f"{context}.rule_sources[{source_index}]"
            if not isinstance(raw_source, dict):
                raise PublishError(f"{source_context} must be an object")
            require_exact_keys(
                raw_source, {"kind", "location", "statement"}, source_context
            )
            sources.append(
                RuleSource(
                    kind=require_nonempty_string(
                        raw_source["kind"], f"{source_context}.kind"
                    ),
                    location=require_nonempty_string(
                        raw_source["location"], f"{source_context}.location"
                    ),
                    statement=require_nonempty_string(
                        raw_source["statement"], f"{source_context}.statement"
                    ),
                )
            )

        raw_conflict = raw_profile["rule_conflict"]
        if not isinstance(raw_conflict, dict):
            raise PublishError(f"{context}.rule_conflict must be an object")
        require_exact_keys(
            raw_conflict,
            {"summary", "effective_rule", "project_files_modified"},
            f"{context}.rule_conflict",
        )
        project_files_modified = require_bool(
            raw_conflict["project_files_modified"],
            f"{context}.rule_conflict.project_files_modified",
        )
        if project_files_modified:
            raise PublishError(
                f"{context} cannot authorize repository file modification from an external profile"
            )

        raw_test = raw_profile["test"]
        if not isinstance(raw_test, dict):
            raise PublishError(f"{context}.test must be an object")
        require_exact_keys(
            raw_test,
            {
                "mainline_sync_policy",
                "main_behind_threshold",
                "main_overlap_files_threshold",
                "forbid_dev_into_source",
            },
            f"{context}.test",
        )
        mainline_sync_policy = require_nonempty_string(
            raw_test["mainline_sync_policy"],
            f"{context}.test.mainline_sync_policy",
        )
        if mainline_sync_policy != "large-divergence-only":
            raise PublishError(
                f"{context}.test.mainline_sync_policy must be large-divergence-only"
            )
        forbid_dev_into_source = require_bool(
            raw_test["forbid_dev_into_source"],
            f"{context}.test.forbid_dev_into_source",
        )
        if not forbid_dev_into_source:
            raise PublishError(f"{context} must forbid dev -> demand synchronization")

        raw_identity = raw_profile["identity"]
        if not isinstance(raw_identity, dict):
            raise PublishError(f"{context}.identity must be an object")
        require_exact_keys(raw_identity, {"expected_login"}, f"{context}.identity")

        raw_production = raw_profile["production"]
        if not isinstance(raw_production, dict):
            raise PublishError(f"{context}.production must be an object")
        require_exact_keys(
            raw_production,
            {
                "workflow",
                "blocking_statuses",
                "reuse_successful_tag_for_same_sha",
                "require_reason_after_unsuccessful_tag",
                "monitor_required",
                "runtime_verification_required",
            },
            f"{context}.production",
        )
        raw_statuses = raw_production["blocking_statuses"]
        if (
            not isinstance(raw_statuses, list)
            or not raw_statuses
            or any(not isinstance(status, str) or not status for status in raw_statuses)
        ):
            raise PublishError(
                f"{context}.production.blocking_statuses must be a non-empty string list"
            )
        statuses = tuple(status.casefold() for status in raw_statuses)
        if len(set(statuses)) != len(statuses):
            raise PublishError(
                f"{context}.production.blocking_statuses contains duplicates"
            )

        profiles.append(
            RepositoryProfile(
                profile_id=profile_id,
                display_name=require_nonempty_string(
                    raw_profile["display_name"], f"{context}.display_name"
                ),
                remote_fingerprint=fingerprint,
                rule_sources=tuple(sources),
                conflict_summary=require_nonempty_string(
                    raw_conflict["summary"], f"{context}.rule_conflict.summary"
                ),
                effective_rule=require_nonempty_string(
                    raw_conflict["effective_rule"],
                    f"{context}.rule_conflict.effective_rule",
                ),
                project_files_modified=project_files_modified,
                mainline_sync_policy=mainline_sync_policy,
                main_behind_threshold=require_positive_int(
                    raw_test["main_behind_threshold"],
                    f"{context}.test.main_behind_threshold",
                ),
                main_overlap_files_threshold=require_positive_int(
                    raw_test["main_overlap_files_threshold"],
                    f"{context}.test.main_overlap_files_threshold",
                ),
                forbid_dev_into_source=forbid_dev_into_source,
                expected_login=require_nonempty_string(
                    raw_identity["expected_login"],
                    f"{context}.identity.expected_login",
                ),
                production=ProductionProfile(
                    workflow=require_nonempty_string(
                        raw_production["workflow"],
                        f"{context}.production.workflow",
                    ),
                    blocking_statuses=statuses,
                    reuse_successful_tag_for_same_sha=require_bool(
                        raw_production["reuse_successful_tag_for_same_sha"],
                        f"{context}.production.reuse_successful_tag_for_same_sha",
                    ),
                    require_reason_after_unsuccessful_tag=require_bool(
                        raw_production["require_reason_after_unsuccessful_tag"],
                        f"{context}.production.require_reason_after_unsuccessful_tag",
                    ),
                    monitor_required=require_bool(
                        raw_production["monitor_required"],
                        f"{context}.production.monitor_required",
                    ),
                    runtime_verification_required=require_bool(
                        raw_production["runtime_verification_required"],
                        f"{context}.production.runtime_verification_required",
                    ),
                ),
            )
        )
    return tuple(profiles)


def match_repository_profile(
    info: RemoteInfo, profiles: Sequence[RepositoryProfile]
) -> RepositoryProfile | None:
    return next(
        (
            profile
            for profile in profiles
            if profile.remote_fingerprint == info.fingerprint
        ),
        None,
    )


def optional_remote_profile(
    repo: Path, remote: str
) -> tuple[RemoteInfo | None, RepositoryProfile | None]:
    profiles = load_repository_profiles()
    remote_url = git_output(repo, "remote", "get-url", remote)
    try:
        info = parse_remote(remote_url, "auto")
    except PublishError:
        return None, None
    return info, match_repository_profile(info, profiles)


def effective_thresholds(
    profile: RepositoryProfile | None,
    requested_behind: int | None,
    requested_overlap: int | None,
) -> tuple[int, int, str]:
    behind = (
        requested_behind
        if requested_behind is not None
        else (
            profile.main_behind_threshold if profile else DEFAULT_MAIN_BEHIND_THRESHOLD
        )
    )
    overlap = (
        requested_overlap
        if requested_overlap is not None
        else (
            profile.main_overlap_files_threshold
            if profile
            else DEFAULT_MAIN_OVERLAP_THRESHOLD
        )
    )
    validate_thresholds(behind, overlap)
    source = (
        "command_line"
        if requested_behind is not None or requested_overlap is not None
        else ("repository_profile" if profile else "global_default")
    )
    return behind, overlap, source


def github_pr(
    repo: Path,
    command: str,
    info: RemoteInfo,
    source: str,
    source_sha: str,
    main: str,
    request_title: str | None,
    request_body_file: str | None,
    wait_seconds: int,
    poll_seconds: int,
) -> tuple[str, str]:
    fields = "number,url,state,isDraft,headRefOid,mergeCommit,mergedAt,updatedAt"

    def list_prs() -> list[dict[str, Any]]:
        payload = forge_checked(
            command,
            [
                "pr",
                "list",
                "-R",
                info.slug,
                "--head",
                source,
                "--base",
                main,
                "--state",
                "all",
                "--limit",
                "50",
                "--json",
                fields,
            ],
            repo,
        )
        return list(load_json(payload, "gh pr list"))

    matches = [item for item in list_prs() if item.get("headRefOid") == source_sha]
    if matches:
        pr = max(matches, key=lambda item: int(item.get("number") or 0))
    else:
        if not request_title or not request_body_file:
            raise PublishError(
                "creating a PR requires previewed --request-title and --request-body-file"
            )
        body_path = Path(request_body_file).expanduser().resolve()
        if not body_path.is_file() or not body_path.read_text(encoding="utf-8").strip():
            raise PublishError("PR body file is missing or empty")
        log(f"creating PR: {source} -> {main}")
        forge_checked(
            command,
            [
                "pr",
                "create",
                "-R",
                info.slug,
                "--base",
                main,
                "--head",
                source,
                "--title",
                request_title,
                "--body-file",
                str(body_path),
            ],
            repo,
        )
        matches = [item for item in list_prs() if item.get("headRefOid") == source_sha]
        if not matches:
            raise PublishError(
                "PR was created but cannot be found at the pushed source SHA"
            )
        pr = max(matches, key=lambda item: int(item.get("number") or 0))

    number = str(pr["number"])
    url = str(pr.get("url") or "")
    if pr.get("state") == "MERGED":
        merge_commit = pr.get("mergeCommit") or {}
        merge_sha = str(merge_commit.get("oid") or "")
        if not merge_sha:
            raise PublishError(f"merged PR {url} has no merge commit SHA")
        return url, merge_sha
    if pr.get("isDraft"):
        raise PublishError(f"PR is draft and was not merged: {url}", EXIT_WAITING)

    result = forge_run(
        command,
        [
            "pr",
            "merge",
            number,
            "-R",
            info.slug,
            "--merge",
            "--auto",
            "--match-head-commit",
            source_sha,
        ],
        repo,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise PublishError(
            f"PR created/reused but auto-merge could not be enabled: {url}"
            + (f"\n{detail}" if detail else ""),
            EXIT_WAITING,
        )

    deadline = time.monotonic() + max(0, wait_seconds)
    while True:
        payload = forge_checked(
            command,
            [
                "pr",
                "view",
                number,
                "-R",
                info.slug,
                "--json",
                "state,isDraft,headRefOid,mergeCommit,mergedAt,url",
            ],
            repo,
        )
        view = dict(load_json(payload, "gh pr view"))
        view_head = str(view.get("headRefOid") or "")
        if view_head != source_sha:
            raise PublishError(
                f"PR head changed: expected {source_sha}, actual {view_head or '<missing>'}; inspect {url}"
            )
        state = str(view.get("state") or "")
        if state == "MERGED":
            merge_sha = str((view.get("mergeCommit") or {}).get("oid") or "")
            if not merge_sha:
                raise PublishError(f"merged PR {url} has no merge commit SHA")
            return url, merge_sha
        if state == "CLOSED":
            raise PublishError(f"PR closed without merge: {url}")
        if time.monotonic() >= deadline:
            raise PublishError(
                f"PR is still waiting for checks/review; rerun production after it merges: {url}",
                EXIT_WAITING,
            )
        time.sleep(max(1, poll_seconds))


def gitlab_field(item: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in item and item[name] not in (None, ""):
            return item[name]
    return None


def gitlab_mr(
    repo: Path,
    command: str,
    source: str,
    source_sha: str,
    main: str,
    request_title: str | None,
    request_body_file: str | None,
    wait_seconds: int,
    poll_seconds: int,
) -> tuple[str, str]:
    def list_mrs() -> list[dict[str, Any]]:
        payload = forge_checked(
            command,
            [
                "mr",
                "list",
                "--all",
                "--source-branch",
                source,
                "--target-branch",
                main,
                "--per-page",
                "100",
                "--output",
                "json",
            ],
            repo,
        )
        return list(load_json(payload, "glab mr list"))

    candidates = list_mrs()
    opened = [
        item for item in candidates if str(item.get("state", "")).lower() == "opened"
    ]
    if opened:
        mr = max(opened, key=lambda item: int(gitlab_field(item, "iid", "id") or 0))
    else:
        merged = [
            item
            for item in candidates
            if str(item.get("state", "")).lower() == "merged"
            and str(gitlab_field(item, "sha", "head_sha") or "") == source_sha
        ]
        if merged:
            mr = max(merged, key=lambda item: int(gitlab_field(item, "iid", "id") or 0))
        else:
            if not request_title or not request_body_file:
                raise PublishError(
                    "creating an MR requires previewed --request-title and --request-body-file"
                )
            body_path = Path(request_body_file).expanduser().resolve()
            if not body_path.is_file():
                raise PublishError("MR body file is missing")
            body = body_path.read_text(encoding="utf-8").strip()
            if not body:
                raise PublishError("MR body file is empty")
            log(f"creating MR: {source} -> {main}")
            forge_checked(
                command,
                [
                    "mr",
                    "create",
                    "--source-branch",
                    source,
                    "--target-branch",
                    main,
                    "--title",
                    request_title,
                    "--description",
                    body,
                    "--yes",
                ],
                repo,
            )
            opened = [
                item
                for item in list_mrs()
                if str(item.get("state", "")).lower() == "opened"
            ]
            if not opened:
                raise PublishError("MR was created but cannot be found")
            mr = max(opened, key=lambda item: int(gitlab_field(item, "iid", "id") or 0))

    iid = str(gitlab_field(mr, "iid", "id") or "")
    if not iid:
        raise PublishError("cannot determine GitLab MR IID")

    def view_mr() -> dict[str, Any]:
        payload = forge_checked(command, ["mr", "view", iid, "--output", "json"], repo)
        return dict(load_json(payload, "glab mr view"))

    def require_expected_head(view: dict[str, Any], url: str) -> None:
        view_sha = str(
            gitlab_field(view, "sha", "head_sha")
            or (
                (view.get("diff_refs") or {}).get("head_sha")
                if isinstance(view.get("diff_refs"), dict)
                else ""
            )
            or ""
        )
        if view_sha != source_sha:
            raise PublishError(
                f"MR head changed: expected {source_sha}, actual {view_sha or '<missing>'}; inspect {url}"
            )

    view = view_mr()
    url = str(
        gitlab_field(view, "web_url", "url") or gitlab_field(mr, "web_url", "url") or ""
    )
    require_expected_head(view, url)
    state = str(view.get("state") or "").lower()
    if state == "merged":
        merge_sha = str(
            gitlab_field(view, "merge_commit_sha", "squash_commit_sha") or ""
        )
        if not merge_sha:
            raise PublishError(f"merged MR {url} has no merge commit SHA")
        return url, merge_sha

    result = forge_run(
        command,
        ["mr", "merge", iid, "--auto-merge", "--sha", source_sha, "--yes"],
        repo,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise PublishError(
            f"MR created/reused but auto-merge could not be enabled: {url}"
            + (f"\n{detail}" if detail else ""),
            EXIT_WAITING,
        )

    deadline = time.monotonic() + max(0, wait_seconds)
    while True:
        view = view_mr()
        require_expected_head(view, url)
        state = str(view.get("state") or "").lower()
        if state == "merged":
            merge_sha = str(
                gitlab_field(view, "merge_commit_sha", "squash_commit_sha") or ""
            )
            if not merge_sha:
                raise PublishError(f"merged MR {url} has no merge commit SHA")
            return url, merge_sha
        if state == "closed":
            raise PublishError(f"MR closed without merge: {url}")
        if time.monotonic() >= deadline:
            raise PublishError(
                f"MR is still waiting for checks/review; rerun production after it merges: {url}",
                EXIT_WAITING,
            )
        time.sleep(max(1, poll_seconds))


def github_workflow_runs(
    repo: Path,
    command: str,
    info: RemoteInfo,
    workflow: str,
    *,
    branch: str | None = None,
) -> list[dict[str, Any]]:
    args = [
        "run",
        "list",
        "-R",
        info.slug,
        "--workflow",
        workflow,
        "--limit",
        "100",
        "--json",
        "databaseId,status,conclusion,headSha,headBranch,event,url,workflowName,createdAt",
    ]
    if branch:
        args.extend(["--branch", branch])
    payload = load_json(
        forge_checked(command, args, repo),
        f"gh run list for {workflow}" + (f" at {branch}" if branch else ""),
    )
    if not isinstance(payload, list) or any(
        not isinstance(item, dict) for item in payload
    ):
        raise PublishError("gh run list returned an unexpected payload")
    return list(payload)


def remote_semver_tags_by_sha(repo: Path, remote: str) -> dict[str, str]:
    result = git(repo, "ls-remote", "--tags", remote, "refs/tags/v*", check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise PublishError(f"cannot enumerate remote production tags: {detail}")
    tag_objects: dict[str, str] = {}
    peeled: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2 or not SHA_RE.fullmatch(parts[0]):
            continue
        sha, ref = parts
        prefix = "refs/tags/"
        if not ref.startswith(prefix):
            continue
        name = ref.removeprefix(prefix)
        if name.endswith("^{}"):
            peeled[name.removesuffix("^{}")] = sha
        else:
            tag_objects[name] = sha
    return {
        tag: peeled.get(tag, object_sha)
        for tag, object_sha in tag_objects.items()
        if SEMVER_RE.fullmatch(tag)
    }


def ensure_no_blocking_profile_runs(
    repo: Path,
    command: str,
    info: RemoteInfo,
    profile: RepositoryProfile,
) -> None:
    production = profile.production
    runs = github_workflow_runs(
        repo,
        command,
        info,
        production.workflow,
    )
    blocking_statuses = set(production.blocking_statuses)
    blocking = [
        run
        for run in runs
        if str(run.get("status") or "").casefold() in blocking_statuses
    ]
    if blocking:
        preview = ", ".join(
            str(run.get("url") or run.get("databaseId") or "unknown-run")
            for run in blocking[:5]
        )
        raise PublishError(
            f"production workflow {production.workflow} still has blocking run(s): {preview}; refusing to continue production publication"
        )


def semver_sort_key(tag: str) -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(tag)
    if not match:
        raise PublishError(f"invalid semantic version tag: {tag}")
    return tuple(int(part) for part in match.groups())


def create_or_reuse_profile_tag(
    repo: Path,
    remote: str,
    main: str,
    expected_main_sha: str,
    requested: str,
    new_tag_reason: str | None,
    command: str,
    info: RemoteInfo,
    profile: RepositoryProfile,
) -> TagDecision:
    production = profile.production
    ensure_no_blocking_profile_runs(repo, command, info, profile)

    same_sha_tags = sorted(
        (
            tag
            for tag, sha in remote_semver_tags_by_sha(repo, remote).items()
            if sha == expected_main_sha
        ),
        key=semver_sort_key,
    )
    successful_tags: list[str] = []
    unsuccessful_tags: list[str] = []
    for tag in same_sha_tags:
        tag_runs = github_workflow_runs(
            repo,
            command,
            info,
            production.workflow,
            branch=tag,
        )
        succeeded = any(
            str(run.get("headSha") or "") == expected_main_sha
            and str(run.get("status") or "").casefold() == "completed"
            and str(run.get("conclusion") or "").casefold() == "success"
            for run in tag_runs
        )
        (successful_tags if succeeded else unsuccessful_tags).append(tag)

    if successful_tags and production.reuse_successful_tag_for_same_sha:
        tag = successful_tags[-1]
        log(
            f"reusing successful production tag for exact mainline SHA: {tag} -> {expected_main_sha}"
        )
        return TagDecision(tag=tag, sha=expected_main_sha, reused=True)

    if (
        unsuccessful_tags
        and production.require_reason_after_unsuccessful_tag
        and not (new_tag_reason or "").strip()
    ):
        raise PublishError(
            "the exact mainline SHA already has non-successful or unverified production tag(s): "
            + ", ".join(unsuccessful_tags)
            + "; pass --new-tag-reason with the confirmed reason before creating a new tag"
        )
    if unsuccessful_tags:
        log(
            "creating a new production tag after non-successful tag(s); "
            "a confirmed --new-tag-reason was supplied"
        )
    tag, sha = create_and_push_tag(
        repo,
        remote,
        main,
        expected_main_sha,
        requested,
    )
    return TagDecision(tag=tag, sha=sha, reused=False)


def next_tag(repo: Path, main_ref: str) -> str:
    output = git_output(repo, "tag", "--merged", main_ref, "--list", "v*.*.*")
    versions = []
    for tag in output.splitlines():
        match = SEMVER_RE.fullmatch(tag.strip())
        if match:
            versions.append((tuple(int(part) for part in match.groups()), tag.strip()))
    if not versions:
        raise PublishError(
            "no v<major>.<minor>.<patch> tag found; pass --tag explicitly"
        )
    version, _tag = max(versions)
    return f"v{version[0]}.{version[1]}.{version[2] + 1}"


def create_and_push_tag(
    repo: Path,
    remote: str,
    main: str,
    expected_main_sha: str,
    requested: str,
) -> tuple[str, str]:
    git(repo, "fetch", remote, main, "--tags", "--prune", show=True)
    main_ref = remote_ref(remote, main)
    main_sha = git_output(repo, "rev-parse", main_ref)
    live_main_sha = ls_remote_sha(repo, remote, f"refs/heads/{main}")
    if main_sha != expected_main_sha or live_main_sha != expected_main_sha:
        raise PublishError(
            "mainline advanced before tagging; refusing to tag a different release scope: "
            f"expected={expected_main_sha}, fetched={main_sha}, remote={live_main_sha}"
        )
    tag = next_tag(repo, expected_main_sha) if requested == "auto" else requested
    if not SEMVER_RE.fullmatch(tag):
        raise PublishError(f"tag must match v<major>.<minor>.<patch>, got {tag!r}")
    if rev_parse(repo, f"refs/tags/{tag}") or ls_remote_sha(
        repo, remote, f"refs/tags/{tag}"
    ):
        raise PublishError(f"tag already exists: {tag}")
    validate_identity(repo)
    git(repo, "tag", "-a", tag, expected_main_sha, "-m", f"Release {tag}", show=True)
    try:
        live_main_sha = ls_remote_sha(repo, remote, f"refs/heads/{main}")
        if live_main_sha != expected_main_sha:
            raise PublishError(
                "mainline advanced before the tag push; refusing to publish the tag: "
                f"expected={expected_main_sha}, remote={live_main_sha}"
            )
        git(repo, "push", remote, f"refs/tags/{tag}", show=True)
        peeled = ls_remote_sha(repo, remote, f"refs/tags/{tag}^{{}}")
        if peeled != expected_main_sha:
            raise PublishError(
                f"tag verification failed: expected {expected_main_sha}, remote peeled SHA={peeled}"
            )
    except Exception:
        git(repo, "tag", "-d", tag, check=False)
        raise
    log(f"production tag pushed: {tag} -> {expected_main_sha}")
    return tag, expected_main_sha


def cleanup_test_worktree(repo: Path, parent: Path, worktree: Path) -> None:
    git(repo, "worktree", "remove", str(worktree), check=False)
    temp_root = Path(tempfile.gettempdir()).resolve()
    if (
        parent.name.startswith("git-publish-test-")
        and parent.parent.resolve() == temp_root
    ):
        shutil.rmtree(parent, ignore_errors=True)


def write_test_state(
    parent: Path,
    repo: Path,
    worktree: Path,
    remote: str,
    dev: str,
    source: str,
    source_sha: str,
    base_dev_sha: str,
    mainline_conflict_diagnosis: dict[str, Any],
) -> Path:
    state_path = parent / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 3,
                "repo": str(repo),
                "worktree": str(worktree),
                "remote": remote,
                "dev": dev,
                "source": source,
                "source_sha": source_sha,
                "base_dev_sha": base_dev_sha,
                "mainline_conflict_diagnosis": mainline_conflict_diagnosis,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    state_path.chmod(0o600)
    return state_path


def resolve_resume_paths(state_path_value: str) -> tuple[Path, Path, Path]:
    state_path = Path(state_path_value).expanduser().resolve()
    parent = state_path.parent
    temp_root = Path(tempfile.gettempdir()).resolve()
    if (
        state_path != parent / "state.json"
        or not parent.name.startswith("git-publish-test-")
        or parent.parent.resolve() != temp_root
    ):
        raise PublishError(
            "refusing a state file outside a generated test publish directory"
        )
    return state_path, parent, parent / "integration"


def git_common_dir(repo: Path) -> Path:
    raw = git_output(repo, "rev-parse", "--git-common-dir")
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def validate_resume_state(
    state: dict[str, Any],
    *,
    state_path: Path,
    repo: Path,
    worktree: Path,
    remote: str,
    dev: str,
    source: str,
    source_sha: str,
) -> str:
    if state.get("version") != 3:
        raise PublishError("unsupported test state version")
    required = {
        "version",
        "repo",
        "worktree",
        "remote",
        "dev",
        "source",
        "source_sha",
        "base_dev_sha",
        "mainline_conflict_diagnosis",
    }
    if set(state) != required:
        raise PublishError(
            "test state fields do not match the supported resume contract"
        )
    expected = {
        "remote": remote,
        "dev": dev,
        "source": source,
        "source_sha": source_sha,
    }
    mismatches = [key for key, value in expected.items() if state.get(key) != value]
    for key, value in (("repo", repo), ("worktree", worktree)):
        saved = state.get(key)
        if not isinstance(saved, str) or Path(saved).expanduser().resolve() != value:
            mismatches.append(key)
    if mismatches:
        raise PublishError(
            "resume arguments do not match saved state: " + ", ".join(mismatches)
        )
    base_dev_sha = str(state.get("base_dev_sha") or "")
    diagnosis = state.get("mainline_conflict_diagnosis")
    if not isinstance(diagnosis, dict) or diagnosis.get("status") not in {
        "checked",
        "unavailable",
    }:
        raise PublishError("saved mainline conflict diagnosis is invalid")
    if not SHA_RE.fullmatch(source_sha) or not SHA_RE.fullmatch(base_dev_sha):
        raise PublishError("saved source or dev SHA is invalid")
    if not state_path.is_file() or not repo.is_dir() or not worktree.is_dir():
        raise PublishError("saved repository or integration worktree no longer exists")
    if git_common_dir(repo) != git_common_dir(worktree):
        raise PublishError(
            "saved integration worktree does not belong to the requested repository"
        )
    worktree_list = git_output(repo, "worktree", "list", "--porcelain")
    if f"worktree {worktree}" not in worktree_list.splitlines():
        raise PublishError(
            "saved integration worktree is not registered in the repository"
        )
    merge_head = rev_parse(worktree, "MERGE_HEAD")
    if merge_head:
        if (
            merge_head != source_sha
            or git_output(worktree, "rev-parse", "HEAD") != base_dev_sha
        ):
            raise PublishError(
                "saved conflict merge no longer matches the expected source and dev SHAs"
            )
    else:
        parents = git_output(
            worktree, "rev-list", "--parents", "-n", "1", "HEAD"
        ).split()
        if (
            len(parents) < 3
            or base_dev_sha not in parents[1:]
            or source_sha not in parents[1:]
        ):
            raise PublishError(
                "resolved integration HEAD is not the expected merge commit"
            )
    return base_dev_sha


def require_unchanged_test_source(
    repo: Path,
    remote: str,
    source: str,
    source_sha: str,
) -> None:
    local_sha = rev_parse(repo, f"refs/heads/{source}")
    remote_sha = ls_remote_sha(repo, remote, f"refs/heads/{source}")
    if local_sha != source_sha or remote_sha != source_sha:
        raise PublishError(
            "demand branch changed during test integration; refusing to push dev: "
            f"expected={source_sha}, local={local_sha}, remote={remote_sha}. "
            "Never merge or synchronize dev back into the demand branch."
        )


def finish_test_publish(
    repo: Path,
    worktree: Path,
    remote: str,
    dev: str,
    source: str,
    source_sha: str,
    verify: Sequence[str],
) -> str:
    if not is_ancestor(worktree, source_sha, "HEAD"):
        raise PublishError("test integration HEAD does not contain the source commit")
    run_verifications(worktree, verify)
    if not fetch_branch(repo, remote, dev):
        raise PublishError(f"remote test branch does not exist: {remote}/{dev}")
    latest_dev = remote_ref(remote, dev)
    if not is_ancestor(worktree, latest_dev, "HEAD"):
        raise PublishError(
            f"{remote}/{dev} advanced and diverged during integration; discard this worktree and rerun test publish"
        )
    require_unchanged_test_source(repo, remote, source, source_sha)
    pre_push_dev_sha = git_output(repo, "rev-parse", latest_dev)
    integration_sha = git_output(worktree, "rev-parse", "HEAD")
    git(worktree, "push", remote, f"HEAD:refs/heads/{dev}", show=True)
    verified = ls_remote_sha(repo, remote, f"refs/heads/{dev}")
    if verified != integration_sha:
        raise PublishError(
            f"test branch push verification failed: local={integration_sha}, remote={verified}"
        )
    local_dev_sha = rev_parse(repo, f"refs/heads/{dev}")
    if local_dev_sha == pre_push_dev_sha and not branch_worktrees(repo, dev):
        git(
            repo,
            "update-ref",
            f"refs/heads/{dev}",
            integration_sha,
            pre_push_dev_sha,
            show=True,
        )
    elif local_dev_sha != integration_sha:
        log(
            f"remote {dev} was published, but local {dev} changed concurrently and was not rewritten"
        )
    log(f"test branch pushed: {dev} @ {integration_sha}")
    return integration_sha


def publish_test(args: argparse.Namespace) -> None:
    repo = resolve_repo(args.repo)
    validate_remote_name(args.remote)
    ensure_no_operation(repo)
    source = current_branch(repo)
    ensure_source_branch(source)
    ensure_test_branch(args.dev_branch, source)
    _info, profile = optional_remote_profile(repo, args.remote)
    behind_threshold, overlap_threshold, threshold_source = effective_thresholds(
        profile,
        args.main_behind_threshold,
        args.main_overlap_files_threshold,
    )
    if profile:
        log(
            f"repository profile matched: {profile.display_name} "
            f"({profile.remote_fingerprint}); thresholds={threshold_source}"
        )
    main = resolve_main_branch(repo, args.remote, args.main_branch)
    ensure_mainline_branch(main)
    if not fetch_branch(repo, args.remote, main):
        raise PublishError(f"remote mainline does not exist: {args.remote}/{main}")
    divergence = assess_mainline_divergence(
        repo,
        args.remote,
        main,
        behind_threshold,
        overlap_threshold,
        include_staged_paths=True,
    )
    if divergence.is_large and not args.sync_mainline:
        overlap_preview = ", ".join(divergence.overlapping_files[:8]) or "none"
        raise PublishError(
            "demand branch and mainline differ substantially; no commit or push was performed. "
            f"source_behind={divergence.source_behind}, "
            f"overlapping_files={len(divergence.overlapping_files)} ({overlap_preview}). "
            "Review the difference, then rerun with --sync-mainline to merge only "
            f"{args.remote}/{main} into {source} before publishing to {args.dev_branch}.",
            EXIT_SYNC_RECOMMENDED,
        )
    source = prepare_source(repo, args.commit_message)
    validate_identity(repo)
    if divergence.is_large:
        merge_mainline_into_source(repo, args.remote, main, source, args.verify)
    else:
        log(
            "mainline sync skipped because demand/mainline divergence is below the thresholds"
        )
        run_verifications(repo, args.verify)
    source_sha = push_source(repo, args.remote, source)
    if not fetch_branch(repo, args.remote, args.dev_branch):
        raise PublishError(
            f"remote test branch does not exist: {args.remote}/{args.dev_branch}"
        )
    rebuild_local_environment_branch(repo, args.remote, args.dev_branch)

    parent = Path(tempfile.mkdtemp(prefix="git-publish-test-"))
    worktree = parent / "integration"
    try:
        git(
            repo,
            "worktree",
            "add",
            "--detach",
            str(worktree),
            remote_ref(args.remote, args.dev_branch),
            show=True,
        )
        base_dev_sha = git_output(worktree, "rev-parse", "HEAD")
        merge = git(
            worktree,
            "merge",
            "--no-ff",
            "--no-edit",
            source_sha,
            check=False,
            show=True,
        )
        if merge.returncode != 0:
            unresolved = git_output(worktree, "diff", "--name-only", "--diff-filter=U")
            if unresolved:
                mainline_diagnosis = diagnose_test_conflict_against_mainline(
                    repo,
                    args.remote,
                    main,
                    source_sha,
                    behind_threshold,
                    overlap_threshold,
                )
                state = write_test_state(
                    parent,
                    repo,
                    worktree,
                    args.remote,
                    args.dev_branch,
                    source,
                    source_sha,
                    base_dev_sha,
                    mainline_diagnosis,
                )
                log("test merge has conflicts; the isolated worktree was preserved")
                print(
                    "CONFLICT_MAINLINE_DIVERGENCE="
                    + json.dumps(mainline_diagnosis, ensure_ascii=False)
                )
                print(f"STATE_FILE={state}")
                print(f"WORKTREE={worktree}")
                print("Resolve conflicts, git add the resolved files, then run:")
                resume_args = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "resume-test",
                    "--state",
                    str(state),
                    "--repo",
                    str(repo),
                    "--remote",
                    args.remote,
                    "--dev-branch",
                    args.dev_branch,
                    "--source-branch",
                    source,
                    "--source-sha",
                    source_sha,
                    "--command-timeout-seconds",
                    str(args.command_timeout_seconds),
                ]
                for verify_command in args.verify:
                    resume_args.extend(["--verify", verify_command])
                print(f"  {command_text(resume_args)}")
                raise PublishError(
                    "test merge requires conflict resolution", EXIT_CONFLICT
                )
            detail = (merge.stderr or merge.stdout).strip()
            raise PublishError(f"test merge failed: {detail}")
        integration_sha = finish_test_publish(
            repo,
            worktree,
            args.remote,
            args.dev_branch,
            source,
            source_sha,
            args.verify,
        )
    except PublishError as exc:
        if exc.exit_code != EXIT_CONFLICT:
            cleanup_test_worktree(repo, parent, worktree)
        raise
    cleanup_test_worktree(repo, parent, worktree)
    print(
        json.dumps(
            {
                "source": source,
                "source_sha": source_sha,
                "dev": args.dev_branch,
                "dev_sha": integration_sha,
            }
        )
    )


def resume_test(args: argparse.Namespace) -> None:
    validate_remote_name(args.remote)
    ensure_source_branch(args.source_branch)
    ensure_test_branch(args.dev_branch, args.source_branch)
    state_path, parent, worktree = resolve_resume_paths(args.state)
    if not state_path.is_file():
        raise PublishError("saved test state file no longer exists")
    state = load_json(state_path.read_text(encoding="utf-8"), "test state")
    if not isinstance(state, dict):
        raise PublishError("test state must be a JSON object")
    repo = resolve_repo(args.repo)
    validate_resume_state(
        state,
        state_path=state_path,
        repo=repo,
        worktree=worktree,
        remote=args.remote,
        dev=args.dev_branch,
        source=args.source_branch,
        source_sha=args.source_sha,
    )
    unresolved = git_output(worktree, "diff", "--name-only", "--diff-filter=U")
    if unresolved:
        raise PublishError(f"unresolved conflicts remain:\n{unresolved}")
    if git(worktree, "diff", "--quiet", check=False).returncode != 0:
        raise PublishError(
            "unstaged conflict-resolution changes remain in the integration worktree"
        )
    untracked = git_output(worktree, "ls-files", "--others", "--exclude-standard")
    if untracked:
        raise PublishError(
            f"untracked files remain in the integration worktree:\n{untracked}"
        )
    if git_path(worktree, "MERGE_HEAD").exists():
        validate_identity(worktree)
        git(worktree, "commit", "--no-edit", show=True)
    integration_sha = finish_test_publish(
        repo,
        worktree,
        args.remote,
        args.dev_branch,
        args.source_branch,
        args.source_sha,
        args.verify,
    )
    cleanup_test_worktree(repo, parent, worktree)
    print(
        json.dumps(
            {
                "source": args.source_branch,
                "source_sha": args.source_sha,
                "dev": args.dev_branch,
                "dev_sha": integration_sha,
                "mainline_conflict_diagnosis": state["mainline_conflict_diagnosis"],
            }
        )
    )


def publish_production(args: argparse.Namespace) -> None:
    if not args.confirm_production:
        raise PublishError("production requires --confirm-production")
    repo = resolve_repo(args.repo)
    validate_remote_name(args.remote)
    remote_url = args.forge_url or git_output(repo, "remote", "get-url", args.remote)
    info = parse_remote(remote_url, args.provider)
    profile = match_repository_profile(info, load_repository_profiles())
    command = args.github_cli if info.provider == "github" else args.gitlab_cli
    if (
        profile
        and args.expected_login
        and args.expected_login.casefold() != profile.expected_login.casefold()
    ):
        raise PublishError(
            f"--expected-login {args.expected_login!r} conflicts with repository profile {profile.expected_login!r}"
        )
    expected_login = args.expected_login or (
        profile.expected_login if profile else None
    )
    verify_forge_identity(repo, info, command, expected_login)
    if profile:
        log(
            f"repository profile matched: {profile.display_name} "
            f"({profile.remote_fingerprint})"
        )
        if info.provider != "github":
            raise PublishError(
                f"repository profile {profile.profile_id} production gate currently requires GitHub"
            )
        ensure_no_blocking_profile_runs(repo, command, info, profile)
    main = resolve_main_branch(repo, args.remote, args.main_branch)
    ensure_mainline_branch(main)
    source = prepare_source(repo, args.commit_message)
    validate_identity(repo)
    run_verifications(repo, args.verify)
    source_sha = push_source(repo, args.remote, source)
    if source == main:
        raise PublishError("production source branch cannot be the mainline branch")
    if not fetch_branch(repo, args.remote, main):
        raise PublishError(f"remote mainline does not exist: {args.remote}/{main}")
    main_ref = remote_ref(args.remote, main)

    already_merged = is_ancestor(repo, source_sha, main_ref)
    if not already_merged:
        merge_mainline_into_source(repo, args.remote, main, source, args.verify)
        source_sha = push_source(repo, args.remote, source)

    if info.provider == "github":
        request_url, merge_sha = github_pr(
            repo,
            command,
            info,
            source,
            source_sha,
            main,
            args.request_title,
            args.request_body_file,
            args.wait_seconds,
            args.poll_seconds,
        )
    else:
        request_url, merge_sha = gitlab_mr(
            repo,
            command,
            source,
            source_sha,
            main,
            args.request_title,
            args.request_body_file,
            args.wait_seconds,
            args.poll_seconds,
        )

    if not fetch_branch(repo, args.remote, main):
        raise PublishError(f"cannot refresh merged mainline: {args.remote}/{main}")
    remote_main_sha = git_output(repo, "rev-parse", main_ref)
    if remote_main_sha != merge_sha:
        raise PublishError(
            f"mainline advanced after the PR/MR merge; refusing to tag extra commits: merge={merge_sha}, latest={remote_main_sha}"
        )
    if not is_ancestor(repo, source_sha, main_ref):
        raise PublishError("merged mainline does not contain the published source SHA")
    if profile:
        tag_decision = create_or_reuse_profile_tag(
            repo,
            args.remote,
            main,
            merge_sha,
            args.tag,
            args.new_tag_reason,
            command,
            info,
            profile,
        )
    else:
        tag, tag_sha = create_and_push_tag(
            repo,
            args.remote,
            main,
            merge_sha,
            args.tag,
        )
        tag_decision = TagDecision(tag=tag, sha=tag_sha, reused=False)
    monitor_handoff = (
        {
            "provider": info.provider,
            "repository": info.slug,
            "workflow": profile.production.workflow,
            "ref": tag_decision.tag,
            "expected_sha": tag_decision.sha,
            "monitor_required": profile.production.monitor_required,
            "runtime_verification_required": profile.production.runtime_verification_required,
        }
        if profile
        else None
    )
    print(
        json.dumps(
            {
                "provider": info.provider,
                "source": source,
                "source_sha": source_sha,
                "main": main,
                "main_sha": tag_decision.sha,
                "request_url": request_url,
                "tag": tag_decision.tag,
                "tag_reused": tag_decision.reused,
                "repository_profile": profile.profile_id if profile else None,
                "monitor_handoff": monitor_handoff,
            }
        )
    )


def show_plan(args: argparse.Namespace) -> None:
    repo = resolve_repo(args.repo)
    source = current_branch(repo)
    remote_url = args.forge_url or git_output(repo, "remote", "get-url", args.remote)
    info = parse_remote(remote_url, args.provider)
    profile = match_repository_profile(info, load_repository_profiles())
    behind_threshold, overlap_threshold, threshold_source = effective_thresholds(
        profile,
        args.main_behind_threshold,
        args.main_overlap_files_threshold,
    )
    payload = {
        "repo": str(repo),
        "source": source,
        "remote": args.remote,
        "provider": info.provider,
        "host": info.host,
        "repository": info.slug,
        "remote_fingerprint": info.fingerprint,
        "repository_profile": (
            {
                "id": profile.profile_id,
                "display_name": profile.display_name,
                "matched_fingerprint": profile.remote_fingerprint,
                "configuration_scope": "OpenSkills external repository profile",
                "project_files_modified": profile.project_files_modified,
                "rule_sources": [
                    {
                        "kind": source.kind,
                        "location": source.location,
                        "statement": source.statement,
                    }
                    for source in profile.rule_sources
                ],
                "rule_conflict": profile.conflict_summary,
                "effective_rule": profile.effective_rule,
                "expected_login": profile.expected_login,
            }
            if profile
            else None
        ),
        "mainline_sync": {
            "policy": (
                profile.mainline_sync_policy if profile else "large-divergence-only"
            ),
            "behind_threshold": behind_threshold,
            "overlap_files_threshold": overlap_threshold,
            "threshold_source": threshold_source,
            "dev_into_demand_forbidden": (
                profile.forbid_dev_into_source if profile else True
            ),
        },
        "test_flow": [
            (
                "assess demand/mainline divergence and recommend mainline -> demand sync "
                f"only at behind>={behind_threshold} or "
                f"overlap>={overlap_threshold} files"
            ),
            "commit staged task changes",
            "push source branch",
            f"merge source into {args.dev_branch} in an isolated worktree",
            "if the dev merge conflicts, refresh mainline and report exact demand/mainline divergence without changing the demand branch",
            f"push {args.dev_branch}",
        ],
        "production_flow": [
            "commit staged task changes",
            "push source branch",
            "merge latest mainline into source only when needed",
            "push updated source",
            "create/reuse and merge PR/MR without bypass",
            "tag the exact merged mainline SHA",
        ],
        "production_gate": (
            {
                "workflow": profile.production.workflow,
                "blocking_statuses": list(profile.production.blocking_statuses),
                "reuse_successful_tag_for_same_sha": profile.production.reuse_successful_tag_for_same_sha,
                "require_reason_after_unsuccessful_tag": profile.production.require_reason_after_unsuccessful_tag,
                "monitor_required": profile.production.monitor_required,
                "runtime_verification_required": profile.production.runtime_verification_required,
            }
            if profile
            else None
        ),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)

    def common(target: argparse.ArgumentParser) -> None:
        target.add_argument("--repo", default=".")
        target.add_argument("--remote", default="origin")
        target.add_argument("--commit-message")
        target.add_argument("--verify", action="append", default=[])
        target.add_argument(
            "--command-timeout-seconds",
            type=int,
            default=DEFAULT_COMMAND_TIMEOUT_SECONDS,
        )

    test_parser = subparsers.add_parser(
        "test", help="merge the demand branch into dev and push dev"
    )
    common(test_parser)
    test_parser.add_argument("--dev-branch", default="dev")
    test_parser.add_argument("--main-branch", default="auto")
    test_parser.add_argument("--sync-mainline", action="store_true")
    test_parser.add_argument(
        "--main-behind-threshold",
        type=int,
        default=None,
    )
    test_parser.add_argument(
        "--main-overlap-files-threshold",
        type=int,
        default=None,
    )
    test_parser.set_defaults(func=publish_test)

    resume_parser = subparsers.add_parser(
        "resume-test", help="resume a conflict-resolved test merge"
    )
    resume_parser.add_argument("--state", required=True)
    resume_parser.add_argument("--repo", required=True)
    resume_parser.add_argument("--remote", required=True)
    resume_parser.add_argument("--dev-branch", required=True)
    resume_parser.add_argument("--source-branch", required=True)
    resume_parser.add_argument("--source-sha", required=True)
    resume_parser.add_argument("--verify", action="append", default=[])
    resume_parser.add_argument(
        "--command-timeout-seconds",
        type=int,
        default=DEFAULT_COMMAND_TIMEOUT_SECONDS,
    )
    resume_parser.set_defaults(func=resume_test)

    prod_parser = subparsers.add_parser(
        "production", help="merge through PR/MR, then tag mainline"
    )
    common(prod_parser)
    prod_parser.add_argument("--main-branch", default="auto")
    prod_parser.add_argument(
        "--provider", choices=("auto", "github", "gitlab"), default="auto"
    )
    prod_parser.add_argument(
        "--forge-url",
        help="explicit forge URL when the Git transport remote is a local mirror",
    )
    prod_parser.add_argument("--github-cli", default="gh")
    prod_parser.add_argument("--gitlab-cli", default="glab")
    prod_parser.add_argument("--expected-login")
    prod_parser.add_argument("--request-title")
    prod_parser.add_argument("--request-body-file")
    prod_parser.add_argument("--tag", default="auto")
    prod_parser.add_argument("--new-tag-reason")
    prod_parser.add_argument("--wait-seconds", type=int, default=600)
    prod_parser.add_argument("--poll-seconds", type=int, default=10)
    prod_parser.add_argument("--confirm-production", action="store_true")
    prod_parser.set_defaults(func=publish_production)

    plan_parser = subparsers.add_parser(
        "plan", help="show the resolved provider and workflow without writes"
    )
    plan_parser.add_argument("--repo", default=".")
    plan_parser.add_argument("--remote", default="origin")
    plan_parser.add_argument(
        "--provider", choices=("auto", "github", "gitlab"), default="auto"
    )
    plan_parser.add_argument(
        "--forge-url",
        help="explicit forge URL when the Git transport remote is a local mirror",
    )
    plan_parser.add_argument("--dev-branch", default="dev")
    plan_parser.add_argument(
        "--main-behind-threshold",
        type=int,
        default=None,
    )
    plan_parser.add_argument(
        "--main-overlap-files-threshold",
        type=int,
        default=None,
    )
    plan_parser.add_argument(
        "--command-timeout-seconds",
        type=int,
        default=DEFAULT_COMMAND_TIMEOUT_SECONDS,
    )
    plan_parser.set_defaults(func=show_plan)
    return result


def main() -> int:
    global COMMAND_TIMEOUT_SECONDS
    args = parser().parse_args()
    try:
        timeout = args.command_timeout_seconds
        if timeout < 1 or timeout > MAX_COMMAND_TIMEOUT_SECONDS:
            raise PublishError(
                f"--command-timeout-seconds must be between 1 and {MAX_COMMAND_TIMEOUT_SECONDS}"
            )
        if hasattr(args, "wait_seconds") and not 0 <= args.wait_seconds <= 3600:
            raise PublishError("--wait-seconds must be between 0 and 3600")
        if hasattr(args, "poll_seconds") and not 1 <= args.poll_seconds <= 300:
            raise PublishError("--poll-seconds must be between 1 and 300")
        if hasattr(args, "main_behind_threshold"):
            if args.main_behind_threshold is not None:
                require_positive_int(
                    args.main_behind_threshold, "--main-behind-threshold"
                )
            if args.main_overlap_files_threshold is not None:
                require_positive_int(
                    args.main_overlap_files_threshold,
                    "--main-overlap-files-threshold",
                )
        COMMAND_TIMEOUT_SECONDS = timeout
        args.func(args)
        return 0
    except PublishError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print(
            "ERROR: interrupted; inspect repository state before rerunning",
            file=sys.stderr,
        )
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
