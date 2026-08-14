#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).with_name("publish_environment.py")
SPEC = importlib.util.spec_from_file_location("publish_environment_under_test", SCRIPT)
assert SPEC and SPEC.loader
PUBLISH = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PUBLISH
SPEC.loader.exec_module(PUBLISH)


def run(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed ({result.returncode}): {' '.join(args)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=repo, check=check)


def git_output(repo: Path, *args: str) -> str:
    return git(repo, *args).stdout.strip()


class RepoFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.remote = root / "remote.git"
        self.repo = root / "repo"
        run(["git", "init", "--bare", str(self.remote)], cwd=root)
        run(["git", "init", str(self.repo)], cwd=root)
        git(self.repo, "config", "user.name", "Test User")
        git(self.repo, "config", "user.email", "test-user@users.noreply.github.com")
        (self.repo / "README.md").write_text("main\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-m", "chore: initialize")
        git(self.repo, "branch", "-M", "main")
        git(self.repo, "remote", "add", "origin", str(self.remote))
        git(self.repo, "push", "-u", "origin", "main")
        run(
            [
                "git",
                "--git-dir",
                str(self.remote),
                "symbolic-ref",
                "HEAD",
                "refs/heads/main",
            ],
            cwd=root,
        )
        git(self.repo, "checkout", "-b", "dev")
        (self.repo / "dev.txt").write_text("initial dev\n", encoding="utf-8")
        git(self.repo, "add", "dev.txt")
        git(self.repo, "commit", "-m", "chore: initialize dev")
        git(self.repo, "push", "-u", "origin", "dev")
        git(self.repo, "checkout", "-b", "feature/test", "main")
        (self.repo / "feature.txt").write_text("feature\n", encoding="utf-8")
        git(self.repo, "add", "feature.txt")
        git(self.repo, "commit", "-m", "feat: add feature")


def advance_remote_main(fixture: RepoFixture, commits: int) -> tuple[Path, str]:
    updater = fixture.root / f"main-updater-{commits}"
    run(["git", "clone", str(fixture.remote), str(updater)], cwd=fixture.root)
    git(updater, "config", "user.name", "Remote User")
    git(updater, "config", "user.email", "remote-user@users.noreply.github.com")
    git(updater, "checkout", "main")
    for index in range(commits):
        path = updater / f"main-change-{index}.txt"
        path.write_text(f"main change {index}\n", encoding="utf-8")
        git(updater, "add", path.name)
        git(updater, "commit", "-m", f"chore: main change {index}")
    git(updater, "push", "origin", "main")
    return updater, git_output(updater, "rev-parse", "main")


class PublishEnvironmentTests(unittest.TestCase):
    def test_protected_branch_roles_cannot_be_relabelled(self) -> None:
        with self.assertRaises(PUBLISH.PublishError):
            PUBLISH.ensure_mainline_branch("dev")
        with self.assertRaises(PUBLISH.PublishError):
            PUBLISH.ensure_test_branch("main", "feature/test")
        with self.assertRaises(PUBLISH.PublishError):
            PUBLISH.ensure_test_branch("feature/test", "feature/test")

    def test_forge_identity_uses_repository_owner_mapping(self) -> None:
        terra = PUBLISH.RemoteInfo(
            "github",
            "github.com",
            "TerraRoot3",
            "OpenSkills",
        )
        pagepop = PUBLISH.RemoteInfo(
            "github",
            "github.com",
            "pagepop",
            "pagepop-agent",
        )
        self.assertEqual(PUBLISH.mapped_expected_login(terra), "TerraRoot3")
        self.assertEqual(PUBLISH.mapped_expected_login(pagepop), "hanbaokun")

        with (
            mock.patch.object(PUBLISH, "forge_checked", return_value="hanbaokun"),
            self.assertRaises(PUBLISH.PublishError) as raised,
        ):
            PUBLISH.verify_forge_identity(Path("."), terra, "gh", None)
        self.assertIn("expected TerraRoot3", str(raised.exception))

        with self.assertRaises(PUBLISH.PublishError):
            PUBLISH.parse_remote(
                "git@github.com:TerraRoot3/OpenSkills.git",
                "gitlab",
            )

    def test_repository_profile_matches_only_exact_remote_fingerprint(self) -> None:
        profiles = PUBLISH.load_repository_profiles()
        pagepop = PUBLISH.parse_remote(
            "git@github.com:pagepop/pagepop-agent.git", "auto"
        )
        wrong_owner = PUBLISH.parse_remote(
            "git@github.com:TerraRoot3/pagepop-agent.git", "auto"
        )
        wrong_repo = PUBLISH.parse_remote(
            "git@github.com:pagepop/pagepop-agent-2.git", "auto"
        )

        matched = PUBLISH.match_repository_profile(pagepop, profiles)
        self.assertIsNotNone(matched)
        assert matched
        self.assertEqual(matched.profile_id, "pagepop-work")
        self.assertEqual(matched.main_behind_threshold, 100)
        self.assertEqual(matched.main_overlap_files_threshold, 20)
        self.assertTrue(matched.forbid_dev_into_source)
        self.assertIsNone(PUBLISH.match_repository_profile(wrong_owner, profiles))
        self.assertIsNone(PUBLISH.match_repository_profile(wrong_repo, profiles))

    def test_repository_profile_schema_rejects_unknown_fields(self) -> None:
        source = json.loads(PUBLISH.PROFILE_PATH.read_text(encoding="utf-8"))
        source["profiles"][0]["unexpected"] = True
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "profiles.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaises(PUBLISH.PublishError) as raised:
                PUBLISH.load_repository_profiles(path)
        self.assertIn("extra=['unexpected']", str(raised.exception))

    def test_plan_reports_external_profile_rule_conflict_without_project_edits(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = RepoFixture(Path(temp))
            result = run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "plan",
                    "--repo",
                    str(fixture.repo),
                    "--provider",
                    "github",
                    "--forge-url",
                    "git@github.com:pagepop/pagepop-agent.git",
                ],
                cwd=fixture.repo,
            )
        payload = json.loads(result.stdout)
        profile = payload["repository_profile"]
        self.assertEqual(profile["id"], "pagepop-work")
        self.assertFalse(profile["project_files_modified"])
        self.assertIn("项目 Guide", profile["rule_conflict"])
        self.assertIn("小分叉不合主分支", profile["effective_rule"])
        self.assertEqual(
            payload["mainline_sync"]["threshold_source"],
            "repository_profile",
        )
        self.assertTrue(payload["mainline_sync"]["dev_into_demand_forbidden"])

    def test_pagepop_production_gate_blocks_active_workflow_run(self) -> None:
        profile = PUBLISH.load_repository_profiles()[0]
        info = PUBLISH.parse_remote("git@github.com:pagepop/pagepop-agent.git", "auto")
        with (
            mock.patch.object(
                PUBLISH,
                "github_workflow_runs",
                return_value=[
                    {
                        "databaseId": 12,
                        "status": "in_progress",
                        "url": "https://github.com/pagepop/pagepop-agent/actions/runs/12",
                    }
                ],
            ),
            self.assertRaises(PUBLISH.PublishError) as raised,
        ):
            PUBLISH.create_or_reuse_profile_tag(
                Path("."),
                "origin",
                "main",
                "a" * 40,
                "auto",
                None,
                "gh-han",
                info,
                profile,
            )
        self.assertIn("blocking run", str(raised.exception))

    def test_pagepop_production_gate_reuses_successful_exact_sha_tag(self) -> None:
        profile = PUBLISH.load_repository_profiles()[0]
        info = PUBLISH.parse_remote("git@github.com:pagepop/pagepop-agent.git", "auto")
        expected_sha = "b" * 40

        def fake_runs(*_args: object, branch: str | None = None, **_kwargs: object):
            if branch is None:
                return []
            return [
                {
                    "status": "completed",
                    "conclusion": "success",
                    "headSha": expected_sha,
                    "headBranch": branch,
                }
            ]

        with (
            mock.patch.object(PUBLISH, "github_workflow_runs", side_effect=fake_runs),
            mock.patch.object(
                PUBLISH,
                "remote_semver_tags_by_sha",
                return_value={"v0.0.8": expected_sha},
            ),
            mock.patch.object(PUBLISH, "create_and_push_tag") as create_tag,
        ):
            decision = PUBLISH.create_or_reuse_profile_tag(
                Path("."),
                "origin",
                "main",
                expected_sha,
                "auto",
                None,
                "gh-han",
                info,
                profile,
            )
        self.assertEqual(decision.tag, "v0.0.8")
        self.assertTrue(decision.reused)
        create_tag.assert_not_called()

    def test_pagepop_production_gate_requires_reason_after_failed_tag(
        self,
    ) -> None:
        profile = PUBLISH.load_repository_profiles()[0]
        info = PUBLISH.parse_remote("git@github.com:pagepop/pagepop-agent.git", "auto")
        expected_sha = "c" * 40

        def fake_runs(*_args: object, branch: str | None = None, **_kwargs: object):
            if branch is None:
                return []
            return [
                {
                    "status": "completed",
                    "conclusion": "failure",
                    "headSha": expected_sha,
                    "headBranch": branch,
                }
            ]

        common_patches = (
            mock.patch.object(PUBLISH, "github_workflow_runs", side_effect=fake_runs),
            mock.patch.object(
                PUBLISH,
                "remote_semver_tags_by_sha",
                return_value={"v0.0.8": expected_sha},
            ),
        )
        with (
            common_patches[0],
            common_patches[1],
            self.assertRaises(PUBLISH.PublishError) as raised,
        ):
            PUBLISH.create_or_reuse_profile_tag(
                Path("."),
                "origin",
                "main",
                expected_sha,
                "auto",
                None,
                "gh-han",
                info,
                profile,
            )
        self.assertIn("--new-tag-reason", str(raised.exception))

        with (
            mock.patch.object(PUBLISH, "github_workflow_runs", side_effect=fake_runs),
            mock.patch.object(
                PUBLISH,
                "remote_semver_tags_by_sha",
                return_value={"v0.0.8": expected_sha},
            ),
            mock.patch.object(
                PUBLISH,
                "create_and_push_tag",
                return_value=("v0.0.9", expected_sha),
            ) as create_tag,
        ):
            decision = PUBLISH.create_or_reuse_profile_tag(
                Path("."),
                "origin",
                "main",
                expected_sha,
                "auto",
                "retry after confirmed transient production failure",
                "gh-han",
                info,
                profile,
            )
        self.assertEqual(decision.tag, "v0.0.9")
        self.assertFalse(decision.reused)
        create_tag.assert_called_once()

    def test_test_publish_rejects_dev_merged_into_demand_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = RepoFixture(Path(temp))
            source_sha = git_output(fixture.repo, "rev-parse", "feature/test")
            git(fixture.repo, "push", "-u", "origin", "feature/test")
            git(fixture.repo, "merge", "--no-ff", "--no-edit", "dev")

            with self.assertRaises(PUBLISH.PublishError) as raised:
                PUBLISH.require_unchanged_test_source(
                    fixture.repo,
                    "origin",
                    "feature/test",
                    source_sha,
                )

            self.assertIn("refusing to push dev", str(raised.exception))
            self.assertIn(
                "Never merge or synchronize dev back into the demand branch",
                str(raised.exception),
            )

    def test_gitlab_mr_uses_exact_sha_and_auto_merge(self) -> None:
        source_sha = "b" * 40
        merge_sha = "c" * 40
        open_mr = {
            "iid": 12,
            "state": "opened",
            "source_branch": "feature/test",
            "target_branch": "main",
            "sha": source_sha,
            "web_url": "https://gitlab.example/group/repo/-/merge_requests/12",
        }
        merged_mr = {
            **open_mr,
            "state": "merged",
            "merge_commit_sha": merge_sha,
        }
        view_count = 0

        def fake_checked(_command: str, args: list[str], _repo: Path) -> str:
            nonlocal view_count
            if args[:2] == ["mr", "list"]:
                return json.dumps([open_mr])
            if args[:2] == ["mr", "view"]:
                view_count += 1
                return json.dumps(open_mr if view_count == 1 else merged_mr)
            raise AssertionError(f"unexpected forge_checked args: {args}")

        completed = subprocess.CompletedProcess(["glab"], 0, "", "")
        with (
            mock.patch.object(PUBLISH, "forge_checked", side_effect=fake_checked),
            mock.patch.object(
                PUBLISH, "forge_run", return_value=completed
            ) as merge_call,
        ):
            url, actual_merge_sha = PUBLISH.gitlab_mr(
                Path("."),
                "glab",
                "feature/test",
                source_sha,
                "main",
                None,
                None,
                1,
                1,
            )
        self.assertEqual(url, open_mr["web_url"])
        self.assertEqual(actual_merge_sha, merge_sha)
        merge_args = merge_call.call_args.args[1]
        self.assertIn("--auto-merge", merge_args)
        self.assertEqual(merge_args[merge_args.index("--sha") + 1], source_sha)

    def test_github_wait_is_bounded_and_returns_resume_state(self) -> None:
        source_sha = "a" * 40
        open_pr = {
            "number": 7,
            "url": "https://github.com/pagepop/pagepop-agent/pull/7",
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": source_sha,
            "mergeCommit": None,
            "mergedAt": None,
            "updatedAt": "2026-08-14T00:00:00Z",
        }

        def fake_checked(_command: str, args: list[str], _repo: Path) -> str:
            if args[:2] == ["pr", "list"]:
                return json.dumps([open_pr])
            if args[:2] == ["pr", "view"]:
                return json.dumps(open_pr)
            raise AssertionError(f"unexpected forge_checked args: {args}")

        completed = subprocess.CompletedProcess(["fake-gh"], 0, "", "")
        with (
            mock.patch.object(PUBLISH, "forge_checked", side_effect=fake_checked),
            mock.patch.object(PUBLISH, "forge_run", return_value=completed),
            self.assertRaises(PUBLISH.PublishError) as raised,
        ):
            PUBLISH.github_pr(
                Path("."),
                "fake-gh",
                PUBLISH.RemoteInfo("github", "github.com", "pagepop", "pagepop-agent"),
                "feature/test",
                source_sha,
                "main",
                None,
                None,
                0,
                1,
            )
        self.assertEqual(raised.exception.exit_code, 4)
        self.assertIn("rerun production", str(raised.exception))

    def test_github_wait_stops_when_pr_head_changes(self) -> None:
        source_sha = "a" * 40
        changed_sha = "d" * 40
        open_pr = {
            "number": 7,
            "url": "https://github.com/example/repo/pull/7",
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": source_sha,
            "mergeCommit": None,
        }

        def fake_checked(_command: str, args: list[str], _repo: Path) -> str:
            if args[:2] == ["pr", "list"]:
                return json.dumps([open_pr])
            if args[:2] == ["pr", "view"]:
                return json.dumps({**open_pr, "headRefOid": changed_sha})
            raise AssertionError(f"unexpected forge_checked args: {args}")

        completed = subprocess.CompletedProcess(["fake-gh"], 0, "", "")
        with (
            mock.patch.object(PUBLISH, "forge_checked", side_effect=fake_checked),
            mock.patch.object(PUBLISH, "forge_run", return_value=completed),
            self.assertRaises(PUBLISH.PublishError) as raised,
        ):
            PUBLISH.github_pr(
                Path("."),
                "fake-gh",
                PUBLISH.RemoteInfo("github", "github.com", "example", "repo"),
                "feature/test",
                source_sha,
                "main",
                None,
                None,
                1,
                1,
            )
        self.assertIn("PR head changed", str(raised.exception))

    def test_gitlab_mr_requires_a_verifiable_head_sha(self) -> None:
        source_sha = "b" * 40
        open_mr = {
            "iid": 12,
            "state": "opened",
            "sha": source_sha,
            "web_url": "https://gitlab.example/group/repo/-/merge_requests/12",
        }

        def fake_checked(_command: str, args: list[str], _repo: Path) -> str:
            if args[:2] == ["mr", "list"]:
                return json.dumps([open_mr])
            if args[:2] == ["mr", "view"]:
                return json.dumps(
                    {"iid": 12, "state": "opened", "web_url": open_mr["web_url"]}
                )
            raise AssertionError(f"unexpected forge_checked args: {args}")

        with (
            mock.patch.object(PUBLISH, "forge_checked", side_effect=fake_checked),
            mock.patch.object(PUBLISH, "forge_run") as merge_call,
            self.assertRaises(PUBLISH.PublishError) as raised,
        ):
            PUBLISH.gitlab_mr(
                Path("."),
                "glab",
                "feature/test",
                source_sha,
                "main",
                None,
                None,
                1,
                1,
            )
        self.assertIn("actual <missing>", str(raised.exception))
        merge_call.assert_not_called()

    def test_commands_have_a_hard_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "child-survived"
            child_code = (
                "import time; from pathlib import Path; time.sleep(0.4); "
                f"Path({str(marker)!r}).write_text('unsafe')"
            )
            parent_code = (
                "import subprocess, sys, time; "
                f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
                "time.sleep(2)"
            )
            with self.assertRaises(PUBLISH.PublishError) as raised:
                PUBLISH.run(
                    [sys.executable, "-c", parent_code],
                    cwd=Path("."),
                    timeout_seconds=0.05,
                )
            time.sleep(0.6)
            self.assertIn("timed out", str(raised.exception))
            self.assertFalse(marker.exists())

    def test_test_publish_preserves_conflict_worktree_and_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = RepoFixture(Path(temp))
            (fixture.repo / "README.md").write_text(
                "feature version\n", encoding="utf-8"
            )
            git(fixture.repo, "add", "README.md")
            git(fixture.repo, "commit", "-m", "feat: change readme")
            feature_sha = git_output(fixture.repo, "rev-parse", "HEAD")

            updater = Path(temp) / "conflict-updater"
            run(["git", "clone", str(fixture.remote), str(updater)], cwd=Path(temp))
            git(updater, "config", "user.name", "Remote User")
            git(updater, "config", "user.email", "remote-user@users.noreply.github.com")
            git(updater, "checkout", "dev")
            (updater / "README.md").write_text("dev version\n", encoding="utf-8")
            git(updater, "add", "README.md")
            git(updater, "commit", "-m", "chore: change dev readme")
            git(updater, "push", "origin", "dev")

            first = run(
                [sys.executable, str(SCRIPT), "test", "--repo", str(fixture.repo)],
                cwd=fixture.repo,
                check=False,
            )
            self.assertEqual(first.returncode, 3, first.stderr)
            state_line = next(
                line
                for line in first.stdout.splitlines()
                if line.startswith("STATE_FILE=")
            )
            state_path = Path(state_line.split("=", 1)[1])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIn("CONFLICT_MAINLINE_DIVERGENCE=", first.stdout)
            self.assertEqual(state["mainline_conflict_diagnosis"]["status"], "checked")
            self.assertFalse(state["mainline_conflict_diagnosis"]["is_large"])
            self.assertIn(
                "isolated worktree",
                state["mainline_conflict_diagnosis"]["recommendation"],
            )
            worktree = Path(state["worktree"])
            self.assertTrue(worktree.is_dir())
            resume_args = [
                sys.executable,
                str(SCRIPT),
                "resume-test",
                "--state",
                str(state_path),
                "--repo",
                str(fixture.repo),
                "--remote",
                "origin",
                "--dev-branch",
                "dev",
                "--source-branch",
                "feature/test",
                "--source-sha",
                feature_sha,
            ]

            state["verify"] = ["touch should-not-run"]
            state_path.write_text(json.dumps(state), encoding="utf-8")
            tampered = run(resume_args, cwd=fixture.repo, check=False)
            self.assertEqual(tampered.returncode, 1)
            self.assertIn("state fields", tampered.stderr)
            self.assertFalse((worktree / "should-not-run").exists())
            del state["verify"]
            state_path.write_text(json.dumps(state), encoding="utf-8")

            (worktree / "README.md").write_text("resolved version\n", encoding="utf-8")
            git(worktree, "add", "README.md")

            resumed = run(resume_args, cwd=fixture.repo)
            self.assertIn('"dev": "dev"', resumed.stdout)
            self.assertIn('"mainline_conflict_diagnosis"', resumed.stdout)
            remote_dev = run(
                [
                    "git",
                    "--git-dir",
                    str(fixture.remote),
                    "rev-parse",
                    "refs/heads/dev",
                ],
                cwd=fixture.root,
            ).stdout.strip()
            self.assertEqual(
                git(
                    fixture.repo,
                    "merge-base",
                    "--is-ancestor",
                    feature_sha,
                    remote_dev,
                    check=False,
                ).returncode,
                0,
            )
            self.assertEqual(
                git_output(fixture.repo, "rev-parse", "feature/test"),
                feature_sha,
            )
            self.assertFalse(state_path.parent.exists())

    def test_test_publish_rebuilds_diverged_local_dev(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = RepoFixture(Path(temp))

            git(fixture.repo, "checkout", "dev")
            (fixture.repo / "local-only.txt").write_text("local\n", encoding="utf-8")
            git(fixture.repo, "add", "local-only.txt")
            git(fixture.repo, "commit", "-m", "chore: local dev only")
            abnormal_local_dev_sha = git_output(fixture.repo, "rev-parse", "HEAD")
            git(fixture.repo, "checkout", "feature/test")

            updater = Path(temp) / "updater"
            run(["git", "clone", str(fixture.remote), str(updater)], cwd=Path(temp))
            git(updater, "config", "user.name", "Remote User")
            git(updater, "config", "user.email", "remote-user@users.noreply.github.com")
            git(updater, "checkout", "dev")
            (updater / "remote-only.txt").write_text("remote\n", encoding="utf-8")
            git(updater, "add", "remote-only.txt")
            git(updater, "commit", "-m", "chore: remote dev only")
            git(updater, "push", "origin", "dev")

            result = run(
                [sys.executable, str(SCRIPT), "test", "--repo", str(fixture.repo)],
                cwd=fixture.repo,
            )
            self.assertIn("ahead=1, behind=1", result.stdout)
            self.assertIn('"dev": "dev"', result.stdout)
            remote_dev = run(
                [
                    "git",
                    "--git-dir",
                    str(fixture.remote),
                    "rev-parse",
                    "refs/heads/dev",
                ],
                cwd=fixture.root,
            ).stdout.strip()
            local_dev = git_output(fixture.repo, "rev-parse", "refs/heads/dev")
            feature_sha = git_output(fixture.repo, "rev-parse", "feature/test")
            remote_only_dev_sha = git_output(updater, "rev-parse", "dev")
            self.assertEqual(local_dev, remote_dev)
            self.assertNotEqual(local_dev, abnormal_local_dev_sha)
            self.assertNotEqual(
                git(
                    fixture.repo,
                    "merge-base",
                    "--is-ancestor",
                    abnormal_local_dev_sha,
                    remote_dev,
                    check=False,
                ).returncode,
                0,
            )
            self.assertEqual(
                git(
                    fixture.repo,
                    "merge-base",
                    "--is-ancestor",
                    feature_sha,
                    remote_dev,
                    check=False,
                ).returncode,
                0,
            )
            self.assertNotEqual(
                git(
                    fixture.repo,
                    "merge-base",
                    "--is-ancestor",
                    remote_only_dev_sha,
                    feature_sha,
                    check=False,
                ).returncode,
                0,
            )
            worktrees = git_output(fixture.repo, "worktree", "list", "--porcelain")
            self.assertEqual(worktrees.count("worktree "), 1)

    def test_test_publish_skips_small_mainline_divergence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = RepoFixture(Path(temp))
            _updater, latest_main = advance_remote_main(fixture, 1)
            feature_sha = git_output(fixture.repo, "rev-parse", "feature/test")

            result = run(
                [sys.executable, str(SCRIPT), "test", "--repo", str(fixture.repo)],
                cwd=fixture.repo,
            )

            self.assertIn("divergence is small", result.stdout)
            self.assertIn("mainline sync skipped", result.stdout)
            self.assertEqual(
                git_output(fixture.repo, "rev-parse", "feature/test"), feature_sha
            )
            self.assertNotEqual(
                git(
                    fixture.repo,
                    "merge-base",
                    "--is-ancestor",
                    latest_main,
                    "feature/test",
                    check=False,
                ).returncode,
                0,
            )

    def test_mainline_divergence_counts_overlapping_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = RepoFixture(Path(temp))
            (fixture.repo / "README.md").write_text(
                "feature readme\n", encoding="utf-8"
            )
            git(fixture.repo, "add", "README.md")
            git(fixture.repo, "commit", "-m", "feat: change readme")

            updater = fixture.root / "main-overlap-updater"
            run(["git", "clone", str(fixture.remote), str(updater)], cwd=fixture.root)
            git(updater, "config", "user.name", "Remote User")
            git(updater, "config", "user.email", "remote-user@users.noreply.github.com")
            git(updater, "checkout", "main")
            (updater / "README.md").write_text("main readme\n", encoding="utf-8")
            git(updater, "add", "README.md")
            git(updater, "commit", "-m", "chore: change readme")
            git(updater, "push", "origin", "main")
            self.assertTrue(PUBLISH.fetch_branch(fixture.repo, "origin", "main"))

            divergence = PUBLISH.assess_mainline_divergence(
                fixture.repo,
                "origin",
                "main",
                behind_threshold=100,
                overlap_threshold=1,
            )

            self.assertTrue(divergence.is_large)
            self.assertEqual(divergence.source_behind, 1)
            self.assertEqual(divergence.overlapping_files, ("README.md",))

    def test_dev_conflict_diagnosis_recommends_separate_mainline_sync_when_large(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = RepoFixture(Path(temp))
            source_sha = git_output(fixture.repo, "rev-parse", "feature/test")
            advance_remote_main(fixture, 2)

            diagnosis = PUBLISH.diagnose_test_conflict_against_mainline(
                fixture.repo,
                "origin",
                "main",
                source_sha,
                2,
                20,
            )

        self.assertEqual(diagnosis["status"], "checked")
        self.assertTrue(diagnosis["is_large"])
        self.assertIn("discard this isolated", diagnosis["recommendation"])
        self.assertIn("mainline into the demand branch", diagnosis["recommendation"])

    def test_test_publish_stops_before_commit_or_push_for_large_divergence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = RepoFixture(Path(temp))
            advance_remote_main(fixture, 2)
            before_head = git_output(fixture.repo, "rev-parse", "HEAD")
            before_dev = run(
                [
                    "git",
                    "--git-dir",
                    str(fixture.remote),
                    "rev-parse",
                    "refs/heads/dev",
                ],
                cwd=fixture.root,
            ).stdout.strip()
            staged = fixture.repo / "staged.txt"
            staged.write_text("staged\n", encoding="utf-8")
            git(fixture.repo, "add", staged.name)

            result = run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "test",
                    "--repo",
                    str(fixture.repo),
                    "--main-behind-threshold",
                    "2",
                    "--commit-message",
                    "feat: should not commit",
                ],
                cwd=fixture.repo,
                check=False,
            )

            self.assertEqual(result.returncode, PUBLISH.EXIT_SYNC_RECOMMENDED)
            self.assertIn("no commit or push was performed", result.stderr)
            self.assertEqual(git_output(fixture.repo, "rev-parse", "HEAD"), before_head)
            self.assertEqual(
                git(fixture.repo, "diff", "--cached", "--name-only").stdout.strip(),
                staged.name,
            )
            self.assertEqual(
                run(
                    [
                        "git",
                        "--git-dir",
                        str(fixture.remote),
                        "rev-parse",
                        "refs/heads/dev",
                    ],
                    cwd=fixture.root,
                ).stdout.strip(),
                before_dev,
            )
            self.assertNotEqual(
                run(
                    [
                        "git",
                        "--git-dir",
                        str(fixture.remote),
                        "show-ref",
                        "--verify",
                        "refs/heads/feature/test",
                    ],
                    cwd=fixture.root,
                    check=False,
                ).returncode,
                0,
            )

    def test_test_publish_explicitly_syncs_mainline_never_dev_into_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = RepoFixture(Path(temp))
            _updater, latest_main = advance_remote_main(fixture, 2)
            dev_only_sha = git_output(fixture.repo, "rev-parse", "dev")

            result = run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "test",
                    "--repo",
                    str(fixture.repo),
                    "--main-behind-threshold",
                    "2",
                    "--sync-mainline",
                ],
                cwd=fixture.repo,
            )

            self.assertIn("syncing latest origin/main into feature/test", result.stdout)
            source_sha = git_output(fixture.repo, "rev-parse", "feature/test")
            self.assertEqual(
                git(
                    fixture.repo,
                    "merge-base",
                    "--is-ancestor",
                    latest_main,
                    source_sha,
                    check=False,
                ).returncode,
                0,
            )
            self.assertNotEqual(
                git(
                    fixture.repo,
                    "merge-base",
                    "--is-ancestor",
                    dev_only_sha,
                    source_sha,
                    check=False,
                ).returncode,
                0,
            )

    def test_tag_stops_if_mainline_advances_before_push(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = RepoFixture(Path(temp))
            main_sha = git_output(fixture.repo, "rev-parse", "main")
            git(fixture.repo, "tag", "-a", "v0.0.1", main_sha, "-m", "Release v0.0.1")
            git(fixture.repo, "push", "origin", "refs/tags/v0.0.1")
            advanced_sha = "f" * 40
            head_reads = 0
            original_ls_remote = PUBLISH.ls_remote_sha

            def fake_ls_remote(repo: Path, remote: str, ref: str) -> str | None:
                nonlocal head_reads
                if ref == "refs/heads/main":
                    head_reads += 1
                    return main_sha if head_reads == 1 else advanced_sha
                return original_ls_remote(repo, remote, ref)

            with (
                mock.patch.object(PUBLISH, "ls_remote_sha", side_effect=fake_ls_remote),
                self.assertRaises(PUBLISH.PublishError) as raised,
            ):
                PUBLISH.create_and_push_tag(
                    fixture.repo,
                    "origin",
                    "main",
                    main_sha,
                    "auto",
                )

            self.assertIn("advanced before the tag push", str(raised.exception))
            self.assertIsNone(PUBLISH.rev_parse(fixture.repo, "refs/tags/v0.0.2"))
            self.assertIsNone(
                original_ls_remote(fixture.repo, "origin", "refs/tags/v0.0.2")
            )

    def test_github_production_merges_then_tags_exact_main_sha(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = RepoFixture(Path(temp))
            main_sha = git_output(fixture.repo, "rev-parse", "main")
            git(fixture.repo, "tag", "-a", "v0.0.1", main_sha, "-m", "Release v0.0.1")
            git(fixture.repo, "push", "origin", "refs/tags/v0.0.1")

            fake_bin = Path(temp) / "bin"
            fake_bin.mkdir()
            fake_gh = fake_bin / "fake-gh"
            fake_gh.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json
                    import os
                    import subprocess
                    import sys
                    from pathlib import Path

                    args = sys.argv[1:]
                    state_path = Path(os.environ["FAKE_GH_STATE"])
                    remote = os.environ["FAKE_GH_REMOTE"]

                    def load():
                        return json.loads(state_path.read_text()) if state_path.exists() else None

                    def save(value):
                        state_path.write_text(json.dumps(value))

                    if args[:2] == ["api", "user"]:
                        print("hanbaokun")
                    elif args[:2] == ["run", "list"]:
                        print("[]")
                    elif args[:2] == ["pr", "list"]:
                        state = load()
                        print(json.dumps([state] if state else []))
                    elif args[:2] == ["pr", "create"]:
                        source = args[args.index("--head") + 1]
                        sha = subprocess.check_output(
                            ["git", "--git-dir", remote, "rev-parse", f"refs/heads/{source}"],
                            text=True,
                        ).strip()
                        save({
                            "number": 1,
                            "url": "https://github.com/example/repo/pull/1",
                            "state": "OPEN",
                            "isDraft": False,
                            "headRefOid": sha,
                            "mergeCommit": None,
                            "mergedAt": None,
                            "updatedAt": "2026-08-14T00:00:00Z",
                        })
                        print("https://github.com/example/repo/pull/1")
                    elif args[:2] == ["pr", "merge"]:
                        sha = args[args.index("--match-head-commit") + 1]
                        subprocess.check_call(
                            ["git", "--git-dir", remote, "update-ref", "refs/heads/main", sha]
                        )
                        state = load()
                        state.update({
                            "state": "MERGED",
                            "mergeCommit": {"oid": sha},
                            "mergedAt": "2026-08-14T00:00:01Z",
                        })
                        save(state)
                    elif args[:2] == ["pr", "view"]:
                        print(json.dumps(load()))
                    else:
                        print(f"unsupported fake gh command: {args}", file=sys.stderr)
                        raise SystemExit(2)
                    """
                ),
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            env = dict(os.environ)
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
            env["FAKE_GH_STATE"] = str(Path(temp) / "gh-state.json")
            env["FAKE_GH_REMOTE"] = str(fixture.remote)
            pr_body = Path(temp) / "pr-body.md"
            pr_body.write_text("## Summary\n\n- Test release\n", encoding="utf-8")

            result = run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "production",
                    "--repo",
                    str(fixture.repo),
                    "--provider",
                    "github",
                    "--forge-url",
                    "git@github.com:pagepop/pagepop-agent.git",
                    "--github-cli",
                    "fake-gh",
                    "--expected-login",
                    "hanbaokun",
                    "--request-title",
                    "feat: test release",
                    "--request-body-file",
                    str(pr_body),
                    "--tag",
                    "auto",
                    "--wait-seconds",
                    "1",
                    "--poll-seconds",
                    "1",
                    "--confirm-production",
                ],
                cwd=fixture.repo,
                env=env,
            )
            payload = json.loads(result.stdout.splitlines()[-1])
            self.assertEqual(payload["tag"], "v0.0.2")
            self.assertFalse(payload["tag_reused"])
            self.assertEqual(payload["repository_profile"], "pagepop-work")
            self.assertEqual(
                payload["monitor_handoff"],
                {
                    "provider": "github",
                    "repository": "pagepop/pagepop-agent",
                    "workflow": "deploy-prod.yml",
                    "ref": "v0.0.2",
                    "expected_sha": payload["main_sha"],
                    "monitor_required": True,
                    "runtime_verification_required": True,
                },
            )
            remote_main = run(
                [
                    "git",
                    "--git-dir",
                    str(fixture.remote),
                    "rev-parse",
                    "refs/heads/main",
                ],
                cwd=fixture.root,
            ).stdout.strip()
            remote_tag = run(
                [
                    "git",
                    "--git-dir",
                    str(fixture.remote),
                    "rev-parse",
                    "refs/tags/v0.0.2^{}",
                ],
                cwd=fixture.root,
            ).stdout.strip()
            self.assertEqual(remote_tag, remote_main)


if __name__ == "__main__":
    unittest.main()
