---
name: gitlab-start-development
description: Use when starting a new task in a GitLab repository where the real GitLab issue, branch naming, and local worktree must match the hosting project exactly.
---

# GitLab Start Development

## Overview

Use this skill to start GitLab development work in a deterministic way: sync the latest mainline branch, create the real GitLab issue first, derive the branch name from the returned issue number, create the remote branch from the latest mainline, and only then set up the local worktree.

Core rule: never guess the issue number from local branches, stale notes, or open-issue counts. The real source of truth is the issue `iid` returned by GitLab when the issue is created.

## When To Use

Use this skill when:

- the user asks to start a new task in a GitLab repository
- the repository expects branch names like `[issue]-feature-...`, `[issue]-fix-...`, or `[issue]-hotfix-...`
- the user wants an actual GitLab issue, not just a local markdown issue doc
- the repository uses worktrees or you need isolated local development
- the local repo already had a mistaken branch or worktree and you need to realign everything to the real GitLab issue number

Do not use this skill when:

- the repository is GitHub-only and the user did not ask for GitLab issue setup
- the repository intentionally skips issue-based branch naming
- the user only asked to publish or merge existing work; use the publish skill instead

## Workflow

1. Read repository guidance first.
   - If `AGENTS.md`, `CLAUDE.md`, or similar repo rules exist, read them before touching git.
   - Respect the documented mainline branch (`master`, `main`, or another branch), branch naming convention, worktree location, and issue-doc requirements.
2. Record the current repo state.
   - Capture repo path, current branch, `git status --short --branch`, and `git remote -v`.
   - If there are unrelated dirty changes, do not stash them away by force. Prefer a new worktree.
3. Resolve the mainline branch.
   - Use repo docs if they specify it.
   - Otherwise use the GitLab project default branch or `origin/HEAD`.
4. Sync the local mainline branch to the latest remote mainline.
   - Checkout the mainline branch locally.
   - Pull the latest `origin/<mainline>` before creating the issue branch.
   - Do not create a task branch from stale local history.
5. Create the real GitLab issue.
   - Use the GitLab API, not local numbering heuristics.
   - Capture the returned issue `iid`, title, and URL.
   - Use the returned `iid` as the only branch-number source of truth.
6. Build the branch name.
   - If repo docs specify a format, follow it exactly.
   - Otherwise default to `<iid>-feature-<short-slug>` for features, `<iid>-fix-<short-slug>` for bugfixes, and `<iid>-hotfix-<short-slug>` for hotfixes.
   - Keep the semantic suffix short and recognizable.
7. Create the remote branch from the latest mainline.
   - Prefer creating the branch in GitLab first, based on the up-to-date mainline ref.
   - This avoids local branches drifting from the intended remote branch source.
8. Fetch the remote branch locally.
   - Fetch `origin/<branch>` into a matching local branch name.
   - Set local tracking to `origin/<branch>`.
9. Create the worktree from the correct branch.
   - Prefer the existing `.worktrees/` or `worktrees/` directory if the repo already uses one.
   - If repo docs specify a worktree location, follow that.
   - If creating a project-local worktree directory, ensure it is ignored before using it.
10. Only after the correct worktree exists, create repo-local issue/design/ipml docs if the repo requires them.
    - Put those docs in the correct worktree and correct branch from the start.
11. If a mistaken local branch or worktree was created before the real issue existed:
    - migrate any useful local docs or notes into the correct branch/worktree
    - remove the mistaken worktree
    - delete the mistaken local branch if safe
    - report the cleanup clearly

## GitLab API Requirements

You need:

- GitLab API base URL
- GitLab personal access token with issue and branch creation permissions

Preferred sources:

- existing environment variables
- agent or MCP config already available in the local environment
- project-specific documented setup

If these credentials are unavailable, stop and report the blocker instead of inventing a fake issue number.

## Branch Rules

- Always create the issue before the branch.
- Always create the branch from the latest synced mainline branch.
- Always use the real GitLab `iid`.
- Never infer the next issue number from:
  - local branch names
  - the highest historical branch prefix
  - open issue count
  - markdown issue docs alone

## Worktree Rules

- Prefer isolated worktrees for new task branches.
- One task branch should map to one worktree.
- Do not reuse a dirty working directory for a new issue branch when a worktree is feasible.
- If the repo already has a preferred worktree convention, reuse it instead of inventing a new location.

## Safety Rules

- Never use destructive reset commands to “clean things up”.
- Never delete unrelated user changes.
- If network access is blocked, request escalation and continue once approved.
- If the repo has authoritative task-start rules in docs, follow those over generic defaults.
- If the issue was created remotely but branch creation fails, report the exact failure and stop before making a mismatched local branch.
- If the remote branch exists but local fetch or worktree creation fails, keep the remote issue/branch intact and report the partial state clearly.

## Final Response

Always report:

- GitLab issue number and URL
- mainline branch used as the source
- remote branch name created
- local worktree path created
- current checked-out branch in that worktree
- any mistaken branch/worktree cleaned up
- any local issue/design/ipml docs created afterward
