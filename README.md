# OpenSkills

Shared Codex skills for cross-machine installation and team reuse.

## Structure

- `skills/<skill-name>/`: one installable Codex skill per directory

## Current Skills

- `figma-fig-dump`
  - Parse local Figma `.fig` exports without Figma API, MCP, Dev Mode, or browser screenshots
  - List canvases, dump a node subtree, and export JSON/JSONL node indexes
  - Extract text nodes, image references, embedded images, Figma blobs, and raw `.fig` internals
  - Useful when Figma API access is unavailable, rate-limited, or incomplete

- `gitlab-publish-environment`
  - Publish GitLab backend or web frontend repositories to shared environments
  - `发布测网` maps to `dev`
  - `发布预发` maps to `release`
  - `发布现网` or `发布到现网` first syncs `master` into the source branch, then merges into `master`, pushes `master`, pushes the next version tag, and switches back to the original branch
  - Default policy is to merge the full source branch into the requested target branch; do not silently switch to cherry-pick or isolated publish flows unless the user explicitly asks for that exception or repo rules require it
  - Not for Flutter, iOS, Android, React Native, or other mobile app repositories

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

## Install Example

On another machine, install this skill from the repo path:

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py   --repo TerraRoot3/OpenSkills   --path skills/gitlab-publish-environment
```

If the repository is private, make sure git or GitHub credentials are available first.
