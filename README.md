# OpenSkills

Shared Codex skills for cross-machine installation and team reuse.

## Structure

- `skills/<skill-name>/`: one installable Codex skill per directory

## Current Skills

- `gitlab-publish-environment`
  - Publish GitLab backend or web frontend repositories to shared environments
  - `发布测网` maps to `dev`
  - `发布预发` maps to `release`
  - Not for Flutter, iOS, Android, React Native, or other mobile app repositories

## Install Example

On another machine, install this skill from the repo path:

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py   --repo TerraRoot3/OpenSkills   --path skills/gitlab-publish-environment
```

If the repository is private, make sure git or GitHub credentials are available first.
