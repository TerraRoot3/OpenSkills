# OpenSkills

Shared Codex skills for cross-machine installation and team reuse.

## Structure

- `skills/<skill-name>/`: one installable Codex skill per directory
- `plugins/<plugin-name>/`: one installable Codex plugin per directory
- `.agents/plugins/marketplace.json`: Codex plugin marketplace index for this repository

## Current Skills

- `figma-fig-dump`
  - Parse local Figma `.fig` exports without Figma API, MCP, Dev Mode, or browser screenshots
  - List canvases, dump a node subtree, and export JSON/JSONL node indexes
  - Extract text nodes, image references, embedded images, Figma blobs, and raw `.fig` internals
  - Useful when Figma API access is unavailable, rate-limited, or incomplete

- `git-publish-environment`
  - Publish GitHub or GitLab backend/web demand branches to test or production
  - Before test publication, compare only the demand branch with `main`/`master`; skip mainline sync for small divergence and require an explicit opt-in when the configurable large-divergence threshold is reached
  - Test publication is strictly one-way (`demand branch -> dev`): rebuild stale/diverged local `dev` from `origin/dev`, then merge the immutable demand SHA in an isolated worktree without ever merging `dev` back into the demand branch
  - If that test merge conflicts, refresh and report the exact demand-versus-mainline divergence before choosing isolated conflict resolution or a separately authorized mainline sync
  - Production syncs mainline into the demand branch only when needed, merges through PR/MR, bounds CI/review waiting, and tags the exact merged mainline SHA
  - Exact remote-fingerprint profiles keep confirmed repository exceptions and production gates outside project files; the PagePop profile conditionally syncs mainline, blocks concurrent `deploy-prod.yml` runs, reuses successful exact-SHA tags, and returns a CI/CD monitor handoff
  - The bundled script refuses whole-worktree staging, force push, admin bypass, direct mainline push, automatic conflict guesses, and unrelated later commits in a tag

- `gitlab-publish-environment` (legacy compatibility)
  - Retained only for repositories that explicitly require the old direct target-branch merge workflow
  - Disabled for implicit invocation; use `git-publish-environment` for normal `发布测网` and `发布现网` requests

- `gitlab-production-readiness-check`
  - Run the pre-production sync check on the current GitLab demand branch before a real production publish
  - Trigger phrases include `上线检查`, `做一下上线检查`, `现网上线前检查`, and requests to merge the latest `master` or `main` into the current demand branch to review impact
  - Commit and push the current demand branch first, sync the latest `master` or `main` into it, resolve conflicts, and review whether incoming mainline changes affect the current demand
  - If there is no material impact, push the updated demand branch; if there is impact or uncertainty, stop and report it instead of silently pushing
  - Not for Flutter, iOS, Android, React Native, or other mobile app repositories

- `mac-cleanup-safe`
  - Dry-run-first macOS storage cleanup for developer machines
  - Scans safe, review, risky, and protected cleanup candidates before deleting anything
  - Guards active apps and processes such as Chrome, Lark, Xcode, Simulator, Gradle, Docker, Node, Python, Go, and JetBrains tools
  - Cleans only confirmed safe items by default; larger review/risky items require explicit candidate selection
  - Reports project-local generated artifacts without deleting user projects or workspaces

## Current Plugins

- `git-branch-workbench`
  - Adds a compact project selector at the top of the workbench for switching among local Git repositories
  - Saves one projects directory, discovers repositories within three child-directory levels, and refreshes that directory on demand; macOS also gets a native directory chooser
  - Falls back to repositories already known to Codex until a projects directory is configured
  - Defaults to the current conversation's repository when opened from a project task, and restores the most recently viewed repository from a global or task-panel entry
  - Project selection only changes the displayed repository snapshot; it never checks out a branch or mutates either repository
  - Opens as a compact repository card in a Codex conversation and expands into a fullscreen Git workbench
  - Matches Codex light and dark themes with compact native spacing and no extra inline-card height
  - Groups local branches, Worktrees, remote branches, and tags with independent expand/collapse state
  - Reads or switches exact local and remote branches, renders up to 200 continuous parent-topology commits, and shows complete commit details
  - Shows local/remote branch, tag, and MR/PR badges before each commit summary
  - Lets every commit-table column resize by drag or keyboard; the default graph width follows only the newest 10 commits
  - Creates a local branch from an exact branch or tag without overwriting existing branches
  - Creates GitHub PRs or GitLab MRs through an authenticated `gh` or `glab` CLI without automatically pushing
  - Pulls only with `--ff-only` on a clean branch with an existing upstream
  - Pushes only `HEAD` to the existing upstream; never sets upstream or force-pushes

- `cicd-pipeline-monitor`
  - Detects GitHub Actions or GitLab CI/CD from the current repository remote
  - Reuses the active local `gh` or host-specific `glab` login without storing access tokens
  - Triggers an explicitly confirmed test, staging, or production pipeline and returns a compact conversation card
  - Refreshes queued and running pipelines automatically, watches failed runs for a bounded retry window, and persists real success so reopened cards do not resume polling
  - Shows environment, ref, commit, duration, job progress, and provider failure summary without an internal scrollbar
  - Opens the exact GitHub or GitLab run in the system browser through a CSP-allowlisted action
  - Requires an explicit mechanical confirmation flag for provider, repository, workflow, ref, environment, and inputs before any trigger, with Owner-to-account checks for configured GitHub identities

## Install Example

On another machine, install this skill from the repo path:

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py   --repo TerraRoot3/OpenSkills   --path skills/git-publish-environment
```

If the repository is private, make sure git or GitHub credentials are available first.

To install the Git Branch Workbench plugin from this repository:

```bash
codex plugin marketplace add TerraRoot3/OpenSkills --ref main
codex plugin add git-branch-workbench@openskills
```

To install the CI/CD Pipeline Monitor plugin from this repository:

```bash
codex plugin marketplace add TerraRoot3/OpenSkills --ref main
codex plugin add cicd-pipeline-monitor@openskills
```

Start a new Codex task after installation so the plugin's Skill and MCP tools are discovered.
