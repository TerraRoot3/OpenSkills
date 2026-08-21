---
name: cicd-pipeline-monitor
description: Trigger and monitor GitHub Actions or GitLab CI/CD pipelines in a compact live Codex conversation card. Use when the user asks to release or deploy to a test, staging, or production environment; inspect available workflows; monitor a current or recent pipeline; refresh deployment status; or open the provider run in the system browser. Select the matching locally authenticated GitHub account per repository without changing the global active account. This Skill is plugin-distributed; load it from the path supplied by the current Codex task instead of assuming a standalone ~/.codex/skills location.
---

# CI/CD Pipeline Monitor

Use the plugin tools for release discovery, one authorized trigger, and live status cards.

Treat the Skill path injected by Codex as canonical. Plugin installs live in a versioned cache and may be namespace-prefixed; do not construct or maintain a duplicate path under `~/.codex/skills`.

## Workflow

1. Call `list_cicd_targets` with the absolute repository path before choosing a pipeline. Use its exact provider, target identifier, default ref, and current ref values.
2. For monitoring only, call `open_cicd_monitor` once, and only when this task has not already rendered a card for that run or pipeline. Supply an exact run or pipeline ID when known; otherwise supply the workflow and ref filters needed to select the latest matching run. Do not call it again merely because the card is still mounting or a refresh is needed.
3. A user request to publish or deploy to a named environment authorizes one exact target established by discovery or repository evidence. State the provider, repository, workflow or pipeline, ref, environment, and input or variable names as a non-blocking update, then pass `confirmed: true`. Authorization inherited from `git-publish-environment` covers the same resolved target. A request to inspect or monitor does not authorize a trigger.
4. Call `trigger_cicd_run` once. Its successful response already renders the only monitor card needed for that run, so never follow it with `open_cicd_monitor` for the same run. Do not retry a trigger after an uncertain response; inspect the latest matching run with the data-only `get_cicd_run_status` tool first to avoid a duplicate deployment or card.
5. Return the rendered monitor card without mounting another one. The card refreshes queued and running runs automatically through `get_cicd_run_status`. After a failure it watches the same run or pipeline for a bounded retry window so provider reruns can progress from failure to success; it stops after success, cancellation, skip, or expiration of that window. A real provider success is persisted so reopening the card does not restart polling. The user can choose `标记成功` to stop this card locally; `恢复监控` resumes provider reads.

## Safety boundaries

- For GitHub, select the configured Owner-to-login mapping first. For an unmapped Owner, try the active account first, then the other locally authenticated accounts, and use the first account that can read the exact repository.
- Read the selected account's token from the local `gh` credential store only for each plugin operation. Pass it only to that operation's child processes; never persist or return it, and never call `gh auth switch`, so the global active account remains unchanged.
- For GitLab, reuse the existing `glab` login for the exact hostname.
- Require repository evidence for the exact ref, environment, workflow, and input or variable names. Missing target data is a real input blocker, not a repeat-confirmation step.
- Treat inputs and variables as potentially sensitive. Mention names only in execution updates and summaries; omit their values.
- Do not use provider retries as deployment retries. A timeout or missing run ID requires read-only lookup before any new trigger.
- Do not claim that an externally triggered pipeline can insert a card into an inactive conversation. When the user returns, open the matching run; use a separate webhook or scheduled automation only if the user explicitly requests proactive wakeups.
- Treat `标记成功` as a local card state only. It never changes the GitHub Actions run or GitLab pipeline, and the card must say that the remote status was not modified.
- Persist a real provider success separately from a manual success. Restore only the same stable monitor identity, and never treat a stale card snapshot as permission to trigger or retry a deployment.

## Tool selection

- `list_cicd_targets`: Read provider metadata and available GitHub workflows or the GitLab pipeline target.
- `trigger_cicd_run`: Trigger one authorized workflow or pipeline and render its sole monitor card; do not pair it with `open_cicd_monitor`.
- `open_cicd_monitor`: Render an existing or latest matching run once when this task has no card for it; never use it as a refresh operation.
- `get_cicd_run_status`: Read unified run and job status without rendering a new card. The conversation card calls this automatically; call it directly only when a text status or a data-only uncertainty check is needed.

If the remote hostname is unsupported or no locally authenticated account can access the repository, report the exact host and repository and ask the user to configure `gh` or `glab` outside the plugin. Do not fall back to embedded access keys.
