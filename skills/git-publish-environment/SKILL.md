---
name: git-publish-environment
description: Use when the user says 发布测网, 提测, 合并到dev, 同步到dev, 发布现网, 发布生产, 上线, or asks to publish a GitHub or GitLab backend/web demand branch to dev or production. Automates demand-versus-mainline divergence assessment, a full-branch test merge, PR/MR-only mainline merge, and production tag with bounded commands, waiting, and conflict resume. Do not use for mobile app repositories or when the user only asks for a read-only production readiness check.
---

# Git Publish Environment

Run the deterministic script instead of manually repeating Git discovery and merge steps. Keep test and production authorization separate.

## Prepare

1. Read the repository rules that own protected branches, verification, PR/MR, and tags. Reuse their safety gates without copying unrelated manual ceremony.
2. Resolve the real Git root, source branch, remote Host, Owner/Group, and active CLI identity.
3. Require a demand branch. Never use `main`, `master`, `dev`, `release`, `pre`, or their environment derivatives as the source branch.
4. Review the task diff and stage only the task file allowlist. The script commits staged changes only and stops when unstaged or untracked files remain. Preserve a known unrelated untracked subtree with a repeated exact `--allow-untracked-path <repo-relative-path>`; never broaden it to `.`.
5. Choose the smallest relevant source verification command. Pass each command with `--verify`; the test workflow runs these commands once on the demand branch. Use `--integration-verify` only for a distinct check that must run on the merged local `dev` tree.
6. Run the read-only plan before the first publish in a repository. It resolves any exact remote-fingerprint profile, its rule sources, external exceptions, thresholds, identity, and production gates without modifying the target repository:

```bash
python3 scripts/publish_environment.py plan --repo <repo>
```

Resolve `scripts/publish_environment.py` relative to this Skill directory.

## Publish test

Run only after the user authorizes test publication:

```bash
python3 scripts/publish_environment.py test \
  --repo <repo> \
  --commit-message '<angular commit message>' \
  --verify '<focused verification command>'
```

Before any commit or push, fetch `main`/`master` and assess the divergence between the current demand branch and that mainline. This is not a comparison involving `dev`. Default to recommending a mainline sync only when the demand branch is at least 100 commits behind mainline or both sides changed at least 20 of the same files since their common base. Override those thresholds only with `--main-behind-threshold` and `--main-overlap-files-threshold`. Below both thresholds, skip mainline sync even when it would be mechanically possible.

When the threshold is reached, exit code `5` means no commit or push occurred and a mainline sync is recommended. Review the reported counts and overlap, then rerun with `--sync-mainline` only after that direction is authorized. This option performs only `main/master -> demand branch`; it never makes `dev` a source. If conflicts occur, resolve and commit that mainline merge on the demand branch, rerun focused verification, then repeat test publication.

Enforce the test integration direction `demand branch -> dev`. Never merge, pull, rebase, or cherry-pick `dev` into the demand branch. The script verifies and pushes the immutable source SHA, fetches `origin/dev`, checks that local `dev` is not checked out in another worktree, aligns local `dev` exactly to `origin/dev`, switches the current workspace to local `dev`, merges the source SHA, pushes `dev`, verifies the remote SHA, and switches back to the demand branch. Local `dev` is an environment mirror in this workflow: local-only or diverged `dev` commits are replaced by the fetched remote tip before integration. Do not call a PR/MR merge API or re-clone the repository.

The script uses `git merge-tree` only as a conflict preflight. If a conflict also requires a large mainline decision, it stops on the demand branch before changing local `dev`. Otherwise it performs the merge in the current workspace. On conflict it leaves the current checkout on local `dev` and reports `CONFLICT_MAINLINE_DIVERGENCE`, `STATE_FILE`, and `INTEGRATION_CHECKOUT`; resolve only those integration conflicts, stage the resolutions, and run the exact `resume-test` command. Resume commits and pushes local `dev`, then returns to the demand branch. Never modify the demand branch from `dev`.

Do not create a temporary worktree for ordinary test publication, and do not install or download dependencies during publication. The current workspace reuses its existing dependencies. If an explicit `--integration-verify` cannot run with them, stop and use an already-provisioned environment or the target CI; do not improvise symlinks, copies, or package installation. Keep the default command timeout for ordinary publication unless the repository's known focused check genuinely requires a different bound.

## Publish production

Run only after the user explicitly authorizes production publication. Select the CLI from the verified remote mapping: use `gh-terra`/`TerraRoot3` for Owner `TerraRoot3`, and `gh-han`/`hanbaokun` for Owners `hanbaokun` or `pagepop`. The script enforces these known Owner mappings even when `--expected-login` is omitted. Known private GitLab Hosts require login `hanbaokun`; unknown Hosts still use an explicitly verified identity.

Before creating a new PR/MR, prepare and show the final title, body, provider, repository, source branch, and mainline target. After the user confirms that preview, save the body in a temporary Markdown file and pass both values explicitly:

```bash
python3 scripts/publish_environment.py production \
  --repo <repo> \
  --commit-message '<angular commit message>' \
  --github-cli <gh-wrapper> \
  --expected-login <login> \
  --request-title '<confirmed title>' \
  --request-body-file <confirmed-body.md> \
  --tag auto \
  --confirm-production \
  --verify '<focused verification command>'
```

For GitLab, use `--gitlab-cli glab`; the script derives the exact Host from the remote.

The script pushes the demand branch, merges the latest `origin/main` or `origin/master` into it only when needed, pushes the synced demand branch, creates or reuses the exact PR/MR, enables normal auto-merge without admin bypass, waits at most 600 seconds by default, and tags only the exact merged remote mainline SHA. It never pushes the mainline branch directly.

For the exact PagePop remote fingerprint `github:github.com/pagepop/pagepop-agent`, use the external repository profile rather than editing PagePop project files. Before any new production tag, query `deploy-prod.yml` and stop for `queued`, `pending`, `in_progress`, or `waiting` runs. Reuse an existing successful production tag for the same mainline SHA. If the same SHA has only failed, incomplete, or unverifiable production tags, require the user-confirmed reason through `--new-tag-reason` before creating a new tag. After tag creation or reuse, hand the exact repository, workflow, tag, and SHA to `cicd-pipeline-monitor`; pipeline success remains distinct from runtime verification.

When an exact-SHA PR/MR already exists, the script reuses it and does not require new content. When checks or review remain pending, exit code `4` means the PR/MR is valid but production is waiting. Report its URL, use `cicd-pipeline-monitor` when available, and rerun the same production command after merge. Do not create a duplicate PR/MR or tag early.

## CI/CD monitor handoff

Resolve `cicd-pipeline-monitor` from the current task's Available Skills entry and read the exact path supplied by Codex. The Skill is distributed by a plugin, so its visible name may be namespace-prefixed and its file normally lives in a versioned plugin cache. Never construct `~/.codex/skills/cicd-pipeline-monitor/SKILL.md`, search only the standalone Skill root, or create a duplicate copy or symlink there.

If the injected path no longer exists because the plugin was installed, updated, or moved between marketplaces after the task began, run `codex plugin list` to identify the one enabled marketplace and version, then read that exact installed cache entry. Do not choose among caches by filename sorting. Finish the current monitoring work with the enabled plugin and tell the user that a new Codex task is the reliable boundary for refreshing future Skill paths.

## Safety contract

- Never use force push, destructive reset, automatic conflict guesses, admin merge, check bypass, or direct mainline push.
- For test publication, assess only `demand branch <-> main/master`; never describe it as a test-environment difference.
- Recommend `main/master -> demand branch` only at the configured large-divergence threshold, require explicit `--sync-mainline`, and skip it for small divergence.
- For test integration, permit only `demand branch -> dev`; never merge, pull, rebase, or cherry-pick `dev` into the demand branch.
- On a test merge conflict, refresh and report the exact demand/mainline divergence without automatically changing the demand branch.
- Treat local `dev` as a mirror of the fetched remote environment branch: stop if another worktree uses it; otherwise align it exactly to `origin/dev` before merging the demand branch.
- Run `--verify` once on the demand branch. Run only separately supplied `--integration-verify` commands on the merged local `dev` tree.
- Stop if the remote source branch is ahead or diverged.
- Verify source, environment branch, mainline, and tag SHAs against the remote after writes.
- Stop before tagging if mainline advanced after the PR/MR merge; do not include unrelated later commits silently.
- Apply a hard timeout to every Git, forge, and verification command; a timeout is not authorization to retry through a different workflow.
- Treat a pushed branch, merged PR/MR, pushed tag, triggered pipeline, successful deployment, and verified runtime as separate states.
- Match repository exceptions only by normalized provider, Host, Owner/Group, and repository fingerprint; never by a local directory name.
- Read [references/safety-contract.md](references/safety-contract.md) before modifying this workflow or adapting it to another branch model.

## Report

Return the matched repository profile and rule source, demand/mainline divergence counts and decision, source branch/SHA, dev or mainline SHA, PR/MR URL when applicable, tag and reuse state, conflicts, monitor handoff, verification performed, waiting state, and anything not deployed or runtime-verified.
