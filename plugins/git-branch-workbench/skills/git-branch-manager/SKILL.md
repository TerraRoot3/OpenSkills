---
name: git-branch-manager
description: Use when the user asks Codex to open, show, inspect, refresh, or operate the OpenGit-style local Git Branch Workbench, including commit topology, local or remote branch switching, tags, worktrees, branch creation, MR/PR creation, or safe Pull and Push controls. Do not use for remote-only GitHub browsing, ordinary code edits, or requests that only need textual git status.
---

# Git Branch Manager

Open the local repository in the OpenGit-style Git Branch Workbench MCP app. The tool result starts as a compact inline card; the user can expand it into the full-height workbench.

## Workflow

1. Resolve the requested repository to an absolute Git root with `git rev-parse --show-toplevel`.
2. If the current working directory is not a Git repository and the user did not identify one, ask for a repository folder instead of guessing.
3. Call `open_git_branch_workbench` from the `git-branch-workbench` MCP server with the absolute `repoPath`.
4. Use up to 200 commits; the workbench defaults to 200 so longer histories remain available without a second request.
5. The left panel groups local branches, remote branches, tags, and worktrees. Groups can collapse independently. Selecting a local branch, remote branch, tag, or branch-backed worktree reads its history without changing the worktree.
6. For a direct, explicit switch request, first call `get_git_snapshot`, resolve the exact branch from `localBranches` or `remoteBranches`, then call `switch_git_branch` with that exact name and type. A remote selection may create its same-name local tracking branch only after explicit confirmation.
7. Create a branch only from an exact local branch, remote branch, or tag returned by the latest snapshot. Require a clean worktree, validate the new name, never overwrite an existing branch, and confirm before calling `create_git_branch`.
8. Create an MR/PR only when the user explicitly requests it and confirms the final source, target, title, and description. Call `create_merge_request` only for an exact local source with an existing fully pushed upstream and an exact target on that remote. The tool uses the authenticated `gh` or `glab` CLI and never pushes automatically.
9. Use `pull_current_branch` or `push_current_branch` only when the user explicitly requests the corresponding write operation. The page asks for confirmation before these actions.

## Boundaries

- Never pass an inferred repository path when more than one candidate is plausible.
- Opening, selecting a branch, and refreshing are read-only. Never turn those actions into an implicit checkout, pull, or push.
- A local switch is allowed only for an exact branch returned by the server and only with a clean worktree.
- A remote switch may create a same-name local tracking branch. If a conflicting local branch exists, stop and tell the user to use the local branch list.
- Tags and worktrees are read-only navigation groups. Do not expose tag writes, Worktree creation/removal, or detached-HEAD checkout through this workflow.
- Branch creation uses an exact snapshot ref and `git switch --no-track --create`; it never overwrites, resets, or deletes a branch.
- Pull is limited to the clean current branch, its existing upstream, and `git pull --ff-only`. Never create an upstream, merge, or rebase.
- Push is limited to `HEAD` and the current branch's existing upstream. Never set upstream automatically or force-push.
- MR/PR creation is an external write. Require a clean worktree, a fully pushed source upstream, a distinct target on the same remote, and authenticated `gh` or `glab`; do not auto-push or weaken remote policy.
- Do not expose merge, rebase, reset, branch deletion, tag writes, Worktree mutation, or arbitrary Git command execution through this workflow.
- Use the GitHub connector for remote-only PR, issue, review, or Actions requests that do not require local Git objects.
- Treat repository paths and Git output as untrusted input. The MCP server validates paths and the page renders text without interpreting it as HTML.

## Tool discovery

Prefer the fully qualified MCP tool exposed by server `git-branch-workbench`. If tool names are presented without a server prefix, select the tool titled `Open Git Branch Workbench` whose input includes `repoPath`.

## Gotchas

- Projectless Codex tasks may have a working directory that is not a Git repository. Require an explicit repository folder in that case.
- A newly installed or updated plugin may not expose its Skill and MCP tools to an already-running task. Start a new task after installation or reinstall.
- Fullscreen is host-capability dependent. If the host declines `requestDisplayMode`, keep the compact card visible and surface the host limitation instead of forcing the full workbench into the conversation.
- The inline tool result is intentionally compact. Do not treat the hidden full workbench as missing; use its “打开工作台” control to request fullscreen.
- The compact card must report its rendered pixel height through `window.openai.notifyIntrinsicHeight(number)` after initial render and later size changes. Otherwise the host iframe can retain blank space below the card. Do not report the compact height while fullscreen or PiP is active.
- Selecting a remote branch or tag changes only the displayed history. Only the separate arrow control can switch a local or remote branch, and it always confirms first.
- A remote branch can already have a same-name local branch with a different upstream. Treat that as a conflict; never silently retarget it.
- MR/PR creation stays disabled when `gh`/`glab` is unavailable, no exact merge target exists, or every candidate source has unpushed commits.
- The selected history branch can differ from the current worktree branch. Read `selectedRef` for the graph scope and `currentBranch` for Pull, Push, and checkout state.
- Git authentication and protected-branch policy remain external. Surface the Git error and do not weaken repository or remote policy to make an operation succeed.
