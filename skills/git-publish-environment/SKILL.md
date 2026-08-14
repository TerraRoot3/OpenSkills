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
4. Review the task diff and stage only the task file allowlist. The script commits staged changes only and stops when unstaged or untracked files remain.
5. Choose the smallest relevant verification command. Pass each command with `--verify`; omit broad unrelated suites.
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

Enforce the test integration direction `demand branch -> dev`. Never check out the demand branch and merge, pull, rebase, or cherry-pick `dev` into it. The script pushes the source branch, fetches `origin/dev`, and compares the local and remote refs. When both ahead and behind counts are greater than zero, it treats local `dev` as an abnormal disposable integration ref: record its old SHA and counts, delete it, and recreate it from `origin/dev`. It then merges the immutable source SHA in an isolated temporary worktree and pushes `dev` normally. Before pushing `dev`, require both local and remote demand refs to remain at that immutable source SHA. Never try a PR/MR merge API or re-clone the repository to repair a diverged local `dev`.

If the merge conflicts, refresh the latest mainline and report `CONFLICT_MAINLINE_DIVERGENCE`, `STATE_FILE`, and `WORKTREE`. For a small demand/mainline difference, resolve only the integration conflicts inside that isolated `dev` worktree, stage the resolutions, and run the exact `resume-test` command. For a large difference, do not mix mainline synchronization into the preserved conflict: discard that isolated worktree, obtain explicit authorization for `main/master -> demand branch`, then rerun test publication. Never modify the demand branch from `dev`.

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

## Safety contract

- Never use force push, destructive reset, automatic conflict guesses, admin merge, check bypass, or direct mainline push.
- For test publication, assess only `demand branch <-> main/master`; never describe it as a test-environment difference.
- Recommend `main/master -> demand branch` only at the configured large-divergence threshold, require explicit `--sync-mainline`, and skip it for small divergence.
- For test integration, permit only `demand branch -> dev`; never merge, pull, rebase, or cherry-pick `dev` into the demand branch.
- On a test merge conflict, refresh and report the exact demand/mainline divergence without automatically changing either branch.
- Never discard an ahead-only local environment branch automatically. Rebuild only a missing, behind-only, or genuinely diverged local environment branch that is not checked out in any worktree.
- Stop if the remote source branch is ahead or diverged.
- Verify source, environment branch, mainline, and tag SHAs against the remote after writes.
- Stop before tagging if mainline advanced after the PR/MR merge; do not include unrelated later commits silently.
- Apply a hard timeout to every Git, forge, and verification command; a timeout is not authorization to retry through a different workflow.
- Treat a pushed branch, merged PR/MR, pushed tag, triggered pipeline, successful deployment, and verified runtime as separate states.
- Match repository exceptions only by normalized provider, Host, Owner/Group, and repository fingerprint; never by a local directory name.
- Read [references/safety-contract.md](references/safety-contract.md) before modifying this workflow or adapting it to another branch model.

## Report

Return the matched repository profile and rule source, demand/mainline divergence counts and decision, source branch/SHA, dev or mainline SHA, PR/MR URL when applicable, tag and reuse state, conflicts, monitor handoff, verification performed, waiting state, and anything not deployed or runtime-verified.
