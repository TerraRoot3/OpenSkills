# OpenSkills

Shared Codex skills for cross-machine installation and team reuse.

## Structure

- `skills/<skill-name>/`: one installable Codex skill per directory

## Current Skills

- `gitlab-start-development`
  - Start a new GitLab task with the real GitLab issue as the numbering source of truth
  - The user prepares the clean worktree first; this skill does not create it
  - Sync the latest `master` or `main` first, based on repo rules
  - Create the remote task branch from that latest mainline branch
  - Fetch the correct branch locally and switch the prepared worktree onto it before coding
  - Useful when the repo also requires local issue/design/ipml docs after the correct branch exists

- `gitlab-publish-environment`
  - Publish GitLab backend or web frontend repositories to shared environments
  - `发布测网` maps to `dev`
  - `发布预发` maps to `release`
  - `发布现网` or `发布到现网` first syncs `master` into the source branch, then merges into `master`, pushes `master`, pushes the next version tag, and switches back to the original branch
  - Default policy is to merge the full source branch into the requested target branch; do not silently switch to cherry-pick or isolated publish flows unless the user explicitly asks for that exception or repo rules require it
  - Not for Flutter, iOS, Android, React Native, or other mobile app repositories

## Install Example

On another machine, install this skill from the repo path:

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py   --repo TerraRoot3/OpenSkills   --path skills/gitlab-publish-environment
```

If the repository is private, make sure git or GitHub credentials are available first.
