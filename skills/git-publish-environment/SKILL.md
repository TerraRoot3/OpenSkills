---
name: git-publish-environment
description: Use when the user says 发布测网, 提测, 合并到dev, 同步到dev, 发布现网, 发布生产, 上线, or asks to publish a GitHub or GitLab backend/web demand branch to dev or production. Runs the authorized flow continuously through branch integration, PR/MR, CI/CD, and runtime verification. Do not use for mobile repositories or read-only production readiness checks.
---

# Git Publish Environment

Run the deterministic script instead of rebuilding the Git workflow manually. Test and production remain separate user intents. Once the user requests one environment, continue its routine substeps to a verified outcome without additional confirmations.

## Prepare

1. Read the repository rules for protected branches, verification, PR/MR, tags, deployment, and public health checks.
2. Resolve the real Git root, source branch, remote Host, Owner/Group, and active CLI identity.
3. Require a demand branch; shared mainline and environment branches are not valid sources.
4. Build an exact task file allowlist and stage only that scope. Resolve task-related unstaged files before running. Preserve a known unrelated untracked subtree with a repeated exact `--allow-untracked-path <repo-relative-path>`; `.` is not an allowlist.
5. Choose the smallest relevant source verification command. Pass each with `--verify`; use `--integration-verify` only for a distinct merged-`dev` check.
6. Run the read-only plan before the first publish in a repository:

```bash
python3 scripts/publish_environment.py plan --repo <repo>
```

Resolve the script relative to this Skill directory. The plan resolves the live remote `HEAD` as `main` or `master` with `git ls-remote` and reports its resolution source without fetching or modifying repository refs. It also reports the repository-mapped forge command and expected login. Before production publication, verify the mapped login through the same wrapper execution path used by production:

```bash
python3 scripts/publish_environment.py plan \
  --repo <repo> \
  --verify-forge-identity
```

Known GitHub Owners automatically map to `gh-terra` or `gh-han`. These may be zsh functions rather than executable files, so do not use a non-interactive `command -v` result to declare them missing; the script safely retries them through an interactive login zsh. Likewise, do not require `origin/main` before resolving the remote mainline: a repository whose remote `HEAD` is `master` is valid and should report `origin/master`.

## Publish test

Run only after the user authorizes test publication:

```bash
python3 scripts/publish_environment.py test \
  --repo <repo> \
  --commit-message '<angular commit message>' \
  --verify '<focused verification command>'
```

Fetch `main`/`master` and assess divergence against the demand branch; `dev` is not part of this decision. When the demand branch is at least 100 commits behind or both sides changed at least 20 common files since their base, automatically merge `main/master -> demand branch`. Below both thresholds, skip the sync. Threshold flags remain configurable.

If the mainline merge conflicts, inspect the task intent, history, and tests; resolve evidence-backed cases, commit the merge, rerun focused verification, and repeat test publication. Pause only for a genuine business choice that cannot be derived from repository evidence.

Keep test integration one-way: `demand branch -> dev`. The script verifies and pushes the immutable source SHA, aligns the available local `dev` mirror to `origin/dev`, merges the source in the current workspace, pushes and verifies `dev`, then returns to the demand branch. `dev` is not a source for demand-branch changes.

The script preflights conflicts with `git merge-tree`. A `dev` conflict preserves the checkout and reports `STATE_FILE` plus the exact `resume-test` command. Resolve evidence-backed file conflicts, stage them, and resume automatically. Refresh and synchronize mainline first if it materially changed during the attempt.

Ordinary test publication uses the current workspace and its existing dependencies. If a repository rule or explicit user decision requires a worktree, read [references/worktree-dependency-reuse.md](references/worktree-dependency-reuse.md) before creating it. Publication itself does not download packages or images.

## Publish production

Run only after the user requests production publication. That single authorization covers the required source push, PR/MR creation or reuse, normal auto-merge, captured-SHA tag, derived deployment trigger, monitoring, and public runtime verification. Use `gh-terra` for Owner `TerraRoot3`, `gh-han` for Owners `hanbaokun` or `pagepop`, and the exact-host `glab` login for GitLab. Verify unknown mappings from the remote.

Before creating a new PR/MR, prepare and show the final title, body, provider, repository, source branch, and mainline target as a non-blocking execution update. Save the body in a temporary Markdown file and pass both values explicitly without requesting another confirmation:

```bash
python3 scripts/publish_environment.py production \
  --repo <repo> \
  --commit-message '<angular commit message>' \
  --github-cli <gh-wrapper> \
  --expected-login <login> \
  --request-title '<final title>' \
  --request-body-file <final-body.md> \
  --tag auto \
  --confirm-production \
  --verify '<focused verification command>'
```

For GitLab, use `--gitlab-cli glab`; the script derives the exact Host from the remote.

The script pushes the demand branch, conditionally merges latest mainline into it, creates or reuses the exact PR/MR, enables normal platform auto-merge, and waits up to 600 seconds per command attempt. If mainline later advances, tag only the captured merge SHA when it remains in mainline history; later commits stay outside the release scope.

For `github:github.com/pagepop/pagepop-agent`, use the external repository profile. Wait for active `deploy-prod.yml` runs, reuse an existing successful exact-SHA tag, monitor pending or unverifiable tag evidence, and create at most one automatic retry tag after a provider-reported terminal failure. The retry reason is derived from provider evidence; no user response is required. Hand the exact repository, workflow, tag, and SHA to `cicd-pipeline-monitor`.

Exit code `4` means an existing PR/MR, workflow, or tag still needs monitoring. Refresh its status and rerun the same production command after the state changes. For failed checks, inspect logs, implement only task-scoped fixes, verify, push, and resume. Reuse existing requests and tag evidence.

## Continuous completion

- Treat bounded timeouts as monitoring checkpoints and continue with status reads plus the exact resume/rerun command.
- Reconcile same-branch source races with ordinary Git history, then re-verify. Resolve unambiguous code conflicts from task intent, history, and tests.
- Monitor checks and deployments to a terminal state. Permit one duplicate-checked retry for unchanged failing SHA and inputs; a verified code fix starts a new bounded attempt.
- After deployment succeeds, discover the health target from repository rules, profiles, workflow output, or deployed configuration. Verify the exact public HTTPS endpoint and expected application response.
- Report only genuine external blockers: a product choice with no evidence-backed resolution, missing permission, or unavailable required input.

## CI/CD monitor handoff

Resolve `cicd-pipeline-monitor` from the current task's Available Skills entry and read that exact plugin-cache path. If it moved after a plugin update, use `codex plugin list` to identify the enabled marketplace and version.

Monitor a workflow already started by merge or tag creation. If repository evidence identifies an exact manual trigger, the original environment authorization covers it: show the provider, repository, workflow, ref, environment, and input names as a non-blocking update, pass the tool's mechanical confirmation field, trigger once, and inspect provider state before any retry.

## Safety contract

- Use ordinary Git writes and provider-managed protected-branch merges; required checks and reviews remain authoritative.
- Keep the branch directions `main/master -> demand` when threshold sync applies and `demand -> dev` for test integration.
- Preserve the task file allowlist, exact remote identity, and immutable source/merge/tag SHAs.
- Use bounded waits and duplicate detection for every retry or trigger.
- Keep branch publication, merge, tag, CI success, deployment, and runtime health as separate evidence states.
- Match repository exceptions by normalized provider, Host, Owner/Group, and repository fingerprint.
- Read [references/safety-contract.md](references/safety-contract.md) before modifying this workflow or adapting it to another branch model.

## Report

Return the matched repository profile and rule source, demand/mainline divergence counts and decision, source branch/SHA, dev or mainline SHA, PR/MR URL when applicable, tag and reuse state, conflicts, monitor handoff, verification performed, waiting state, and anything not deployed or runtime-verified.
