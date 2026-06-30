---
name: mac-cleanup-safe
description: Use when Codex needs to inspect, review, or clean macOS disk space, developer caches, Xcode or Simulator files, Gradle, Go, Node, Homebrew, Codex caches, or run a local Mac cleanup workflow without installing software.
---

# Mac Cleanup Safe

Use the bundled script for deterministic, local-only cleanup. It uses a dry-run-first workflow, active-process guards, ClearDisk-inspired project artifact reporting, and staged candidates:

1. Run `python3 scripts/mac_cleanup_safe.py scan`.
2. Explain `SAFE` candidates, `REVIEW` candidates, active process guards, and protected paths.
3. Clean only after explicit user confirmation, or when the user has already asked to run confirmed safe cleanup.
4. Run `python3 scripts/mac_cleanup_safe.py clean --execute --scope safe` for default cleanup.
5. For larger or riskier items, use `--include <candidate-id>` only after the user confirms the exact candidate and any required apps are closed.
6. Re-run `scan` and `df -h / /System/Volumes/Data` after cleanup.

Project artifact reporting is enabled by default for bounded project roots. Use `--project-root <path>` to add a root, `--no-project-scan` to skip it, or tune `--max-project-depth`, `--max-project-dirs`, and `--max-project-results` when a workspace is large.

## Guardrails

Never automatically delete:

- User projects or workspaces such as `~/workspace`, Git checkouts, or the current working directory.
- `~/Downloads`; only report large files for manual review.
- Docker images, containers, or volumes.
- Chrome, Lark, Android Studio, Gradle, Xcode, or Simulator application data while the related app/process is active.
- Xcode iOS DeviceSupport matching `18.7.8` unless the user explicitly changes `--keep-ios-device-support`.
- Live Codex sessions, Codex log databases, or active conversation state.
- Simulator runtimes, CoreSimulator volumes, or system-level `/Library/Developer` files without explicit manual review.
- Project-local generated artifacts such as `node_modules`, Rust `target`, Swift `.build`, Gradle `build`, Flutter `.dart_tool`, Terraform `.terraform`, `.next`, `.nuxt`, `dist`, and `vendor`.
- Docker data, local AI models, and language version manager installs.

## Stages

- `SAFE`: regeneratable build caches and temp artifacts that are low-risk when no guarded process is active.
- `REVIEW`: large caches or app data that may affect logins, offline state, rebuild/download time, or installed runtimes.
- `RISKY`: Docker data, local AI models, installed runtimes, and other expensive-to-recreate assets; list only.
- `PROTECTED`: retained packages and user-owned locations.

Prefer cleaning the smallest safe set first. If a candidate is large but marked `REVIEW`, ask for confirmation and explain the tradeoff before including it.
