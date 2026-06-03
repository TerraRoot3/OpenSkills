---
name: gitlab-production-readiness-check
description: Use when the user says 上线检查, 做一下上线检查, 现网上线前检查, 发布现网前检查, 合并最新master检查影响, 合并最新main检查影响, or asks to sync the latest master or main into the current GitLab task branch before a production release. Do not use for Flutter, iOS, Android, React Native, or other mobile app repositories.
---

# GitLab Production Readiness Check

## Overview

Use this skill for the pre-production sync check on a GitLab backend or web frontend task branch. This skill prepares the current demand branch for a later production publish by committing the branch, pushing it, syncing the latest mainline branch into it, resolving conflicts, and reviewing whether the newly synced mainline changes affect the current demand.

This skill does not publish to `master` or `main`, does not create tags, and does not replace the actual production publish flow.

## Scope Check

Use this skill only for GitLab backend or web frontend repositories.

Do not use this skill for mobile app repositories, including Flutter, iOS, Android, or React Native projects. If the repository is an app project, stop and say this workflow does not apply.

## Mainline Branch Rule

- Read repo guidance first if `AGENTS.md`, `CLAUDE.md`, or similar rules exist.
- If repo docs define the production mainline branch, follow that.
- Otherwise prefer the remote default branch from `origin/HEAD`.
- If `origin/HEAD` is unclear, prefer `master` when it exists, otherwise `main`.

## Workflow

1. Record the repo path, current branch, `git status --short --branch`, and `git remote -v`.
2. Treat the current branch as the source branch for this check.
3. If the current branch is `master`, `main`, `dev`, or `release`, stop and tell the user this skill expects a task branch, not a shared branch.
4. Inspect the source-branch diff. If the worktree is dirty, review the files, stage only the changes that belong to the current demand, commit them on the source branch, and push the source branch to `origin`.
5. If the worktree is clean but the source branch has unpushed commits, push the source branch before the sync merge.
6. Choose the smallest repository-native verification command set that gives real confidence for this repo. Run that verification on the source branch before the sync merge. If verification fails, stop before merging the mainline branch.
7. Checkout the mainline branch locally and pull the latest `origin/<mainline>` with `--ff-only`. If the local branch does not exist yet, create a tracking branch first.
8. Checkout the source branch again and merge the updated mainline branch into the source branch.
9. If the mainline-to-source merge conflicts, resolve the conflicts carefully on the source branch, rerun the same verification that is still relevant, and complete the merge commit only when the result is understood.
10. Review whether the incoming mainline changes affect the current demand. Compare the files and modules introduced by the mainline sync against the files, routes, services, or components touched by the demand branch. Treat overlapping files, adjacent shared modules, verification failures, or behavior uncertainty as potential impact.
11. If the review finds likely impact, unresolved uncertainty, or verification failure, stop before pushing the sync merge and tell the user exactly what changed, what may be affected, and whether the merged source branch is now only local.
12. If the review finds no material impact, rerun the chosen verification on the updated source branch when the merge introduced changes, then push the updated source branch to `origin`.
13. Leave the repository on the original source branch and report the result clearly.

## Safety Rules

- Never use destructive reset commands.
- Never stash away or revert unrelated user changes.
- If network operations such as `git pull` or `git push` are blocked, request escalation and continue once approved.
- If the merge leaves unresolved conflicts, say so clearly and stop instead of hiding the partial state.
- If verification is not possible, say exactly what could not be verified before any push decision.
- If the post-merge review is risky but the sync merge commit already exists locally, do not push it silently; report that exact state to the user.

## Final Response

Always report:

- source branch name
- mainline branch used for the sync
- whether the source branch had a pre-sync commit and push
- whether the mainline branch was already up to date locally
- whether the mainline-to-source merge produced conflicts
- whether the sync merge was pushed or intentionally held back
- any impact found on the current demand
- final checked-out branch
