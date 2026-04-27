---
name: gitlab-publish-environment
description: Publish GitLab frontend or backend repositories to shared environments by committing the current branch, pushing it, merging it into the target environment branch, pushing that branch, and switching back to the original branch. Use when the user says 发布测网, 提测, 合并到dev, 同步到dev, 发布到dev, 发布预发, 提预发, 合并到release, 同步到release, 发布到release, or asks to ship a GitLab backend or web frontend repository to the dev or release branch. Do not use for Flutter, iOS, Android, React Native, or other mobile app repositories.
---

# GitLab Publish Environment

## Overview

Use this skill to run the standard GitLab environment publish flow for backend and web frontend repositories. Keep the workflow deterministic, verify before publishing, and always return to the original branch at the end.

## Scope Check

Use this skill only for GitLab backend or web frontend repositories.

Do not use this skill for mobile app repositories, including Flutter, iOS, Android, or React Native projects. If the repository is an app project, stop and tell the user this workflow does not apply.

## Target Branch Mapping

Resolve the target branch from user intent before touching git:

- `发布测网`, `提测`, `合并到dev`, `同步到dev`, `发布到dev`: target branch is `dev`
- `发布预发`, `提预发`, `合并到release`, `同步到release`, `发布到release`: target branch is `release`
- If the user explicitly names a target branch, prefer the explicit branch over the inferred mapping.

## Workflow

1. Record the current repository path and current branch. Treat that branch as the source branch.
2. Resolve the target branch from the user request.
3. Inspect the repository type and choose verification commands. Read `references/verification-matrix.md` when the stack is unclear.
4. Check `git status --short`.
5. If the worktree is dirty, review the diff, stage only the files that belong to the requested change, and commit on the source branch before merging.
6. Run the chosen verification commands on the source branch. Do not publish if verification fails.
7. Push the source branch to `origin`.
8. Checkout the target branch.
9. Pull the latest `origin/<target-branch>` with `--ff-only`.
10. Merge the source branch into the target branch with `--no-ff`.
11. If merge conflicts occur, resolve them carefully, rerun the same verification commands that are still relevant, and complete the merge commit.
12. Push the target branch to `origin`.
13. Checkout the original source branch again.
14. Report the source commit, merge commit, push results, target branch, and final checked-out branch.

## Safety Rules

- Never use destructive reset commands.
- Never revert unrelated user changes.
- If network operations such as `git pull` or `git push` are blocked by sandbox or permissions, request escalation and continue.
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
- Target branch name
- Whether the target branch was updated successfully
- Merge commit hash when a merge commit was created
- Final branch after cleanup
- Any conflicts resolved or verification gaps
