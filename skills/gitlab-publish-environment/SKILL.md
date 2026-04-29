---
name: gitlab-publish-environment
description: Use when the user says 发布测网, 提测, 合并到dev, 同步到dev, 发布到dev, 发布预发, 提预发, 合并到release, 同步到release, 发布到release, 发布现网, 发布到现网, 发布生产, 发布到生产, 合并到master, 同步到master, 发布到master, or asks to ship a GitLab backend or web frontend repository to dev, release, or master. Do not use for Flutter, iOS, Android, React Native, or other mobile app repositories.
---

# GitLab Publish Environment

## Overview

Use this skill to run the standard GitLab environment publish flow for backend and web frontend repositories. Keep the workflow deterministic, verify before publishing, and always return to the original branch at the end.

Publishing to `master` is the production flow. Before merging into `master`, first sync the latest `master` into the source branch, resolve conflicts there, verify again, and push the updated source branch. Then merge that updated source branch into `master`, push `master`, create the next release tag from the updated `master` head, push the tag, and finally switch back to the original source branch.

## Scope Check

Use this skill only for GitLab backend or web frontend repositories.

Do not use this skill for mobile app repositories, including Flutter, iOS, Android, or React Native projects. If the repository is an app project, stop and tell the user this workflow does not apply.

## Target Branch Mapping

Resolve the target branch from user intent before touching git:

- `发布测网`, `提测`, `合并到dev`, `同步到dev`, `发布到dev`: target branch is `dev`
- `发布预发`, `提预发`, `合并到release`, `同步到release`, `发布到release`: target branch is `release`
- `发布现网`, `发布到现网`, `发布生产`, `发布到生产`, `合并到master`, `同步到master`, `发布到master`: target branch is `master`
- If the user explicitly names a target branch, prefer the explicit branch over the inferred mapping.

When the resolved target branch is `master`, treat it as a production publish and create a release tag unless the user explicitly says to skip tagging.

## Tag Version Rule

Use the latest existing tag reachable from the updated `master` branch that matches `v<major>.<minor>.<patch>` and increment it numerically.

- Increment `patch` first: `v1.0.0 -> v1.0.1`
- If `patch` would become `10`, carry into `minor` and reset `patch` to `0`: `v1.0.9 -> v1.1.0`
- If `minor` would become `10`, carry into `major` and reset `minor` and `patch` to `0`: `v1.9.9 -> v2.0.0`
- Compare versions numerically, not lexicographically
- If no matching version tag exists yet on `master`, stop and ask the user for the initial tag instead of inventing one

Create the new tag on the updated local `master` head after the merge commit has been pushed.

## Workflow

1. Record the current repository path and current branch. Treat that branch as the source branch.
2. Resolve the target branch from the user request.
3. Inspect the repository type and choose verification commands. Read `references/verification-matrix.md` when the stack is unclear.
4. Check `git status --short`.
5. If the worktree is dirty, review the diff, stage only the files that belong to the requested change, and commit on the source branch before merging.
6. Run the chosen verification commands on the source branch. Do not publish if verification fails.
7. If the target branch is `dev` or `release`, push the source branch to `origin`, checkout the target branch, pull the latest `origin/<target-branch>` with `--ff-only`, merge the source branch into the target branch with `--no-ff`, resolve conflicts if needed, rerun relevant verification after conflict resolution, complete the merge commit, and push the target branch to `origin`.
8. If the target branch is `master`, checkout `master` and pull the latest `origin/master` with `--ff-only`.
9. If the target branch is `master`, checkout the original source branch and merge `master` into the source branch first.
10. If the `master -> source` sync merge conflicts, resolve them carefully on the source branch and complete the merge commit.
11. If the target branch is `master`, rerun the same verification commands on the source branch after the `master -> source` sync merge, even when that sync merge had no conflicts. Do not continue if this verification fails.
12. If the target branch is `master`, push the updated source branch to `origin`.
13. If the target branch is `master`, checkout `master` again and pull the latest `origin/master` with `--ff-only` one more time before the final merge.
14. If the target branch is `master`, merge the updated source branch into `master` with `--no-ff`.
15. If the `source -> master` merge conflicts, resolve them carefully on `master`, rerun the same verification commands that are still relevant, and complete the merge commit.
16. If the target branch is `master`, push `master` to `origin`.
17. If the target branch is `master`, fetch the latest tags from `origin` and identify the highest numeric `v<major>.<minor>.<patch>` tag reachable from the current `master` HEAD.
18. If the target branch is `master`, compute the next tag using the version rule above, create an annotated tag on the current `master` HEAD, and push that tag to `origin`.
19. If the tag push fails because the tag already exists remotely, fetch tags again, recompute once against the latest remote tags, and retry. If it still conflicts, stop and report the issue instead of guessing.
20. Checkout the original source branch again after the target branch push and, when applicable, after the tag push succeeds or fails.
21. Report the source commit, any source-branch sync merge commit when `master` was merged into the source branch, the target merge commit, push results, target branch, tag result when applicable, and the final checked-out branch.

## Safety Rules

- Never use destructive reset commands.
- Never revert unrelated user changes.
- If network operations such as `git pull` or `git push` are blocked by sandbox or permissions, request escalation and continue.
- If the target branch is `master`, always merge the latest `master` into the source branch first and push that updated source branch before merging back into `master`.
- If the target branch is `master`, do not tag from the source branch or from a stale local `master`; tag only from the updated local `master` after the merge push.
- After pushing the production tag, always switch back to the original source branch. If the tag push fails, still switch back before reporting the failure when the repository state is otherwise clean.
- If the existing tag set is malformed or ambiguous, stop and report the exact issue instead of inventing a version.
- If verification is impossible, say exactly what could not be verified before pushing.
- If the merge leaves the repository in an unresolved state, say so clearly and stop rather than hiding it.

## Verification Rules

- Prefer repository-native verification over generic guesses.
- If the repository already has a known release check from prior work in the same repo, reuse it.
- If there is no obvious command, inspect `package.json`, `Makefile`, CI config, or project docs before choosing.
- Keep verification proportional: run the smallest command set that still gives real confidence.

## Final Response

Always include:

- Source branch name
- Source branch commit hash pushed
- Source-branch sync merge result when `master` was merged into the source branch for production publish
- Target branch name
- Whether the target branch was updated successfully
- Merge commit hash when a merge commit was created
- Release tag name and tag push result when the target branch is `master`
- Final branch after cleanup
- Any conflicts resolved or verification gaps
