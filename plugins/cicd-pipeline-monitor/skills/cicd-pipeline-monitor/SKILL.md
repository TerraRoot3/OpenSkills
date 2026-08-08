---
name: cicd-pipeline-monitor
description: Trigger and monitor GitHub Actions or GitLab CI/CD pipelines in a compact live Codex conversation card. Use when the user asks to release or deploy to a test, staging, or production environment; inspect available workflows; monitor a current or recent pipeline; refresh deployment status; or open the provider run in the system browser. Reuse the current local gh or glab login without storing credentials.
---

# CI/CD Pipeline Monitor

Use the plugin tools for release discovery, triggering, and live status cards.

## Workflow

1. Call `list_cicd_targets` with the absolute repository path before choosing a pipeline. Use its exact provider, target identifier, default ref, and current ref values.
2. For monitoring only, call `open_cicd_monitor`. Supply an exact run or pipeline ID when known; otherwise supply the workflow and ref filters needed to select the latest matching run.
3. Before calling `trigger_cicd_run`, state the provider, repository, workflow or pipeline, ref, environment, and input or variable names. Obtain explicit user confirmation for any missing or ambiguous value. Always require explicit confirmation for a production release.
4. Call `trigger_cicd_run` once. Do not retry a trigger after an uncertain response; inspect the latest matching run first to avoid a duplicate deployment.
5. Return the rendered monitor card. The card refreshes non-terminal runs automatically and stops after success, failure, cancellation, or skip.

## Safety boundaries

- Reuse only the active account already selected by `gh` for the remote GitHub host or the existing `glab` login for the exact GitLab hostname.
- Never switch accounts, run login flows, request tokens, display tokens, copy credentials, or store credentials in the plugin.
- Never infer a production ref, environment, workflow, input, or GitLab variable. Ask when discovery does not establish an exact value.
- Treat inputs and variables as potentially sensitive. Mention names only in confirmations and summaries; never repeat their values.
- Do not use provider retries as deployment retries. A timeout or missing run ID requires read-only lookup before any new trigger.
- Do not claim that an externally triggered pipeline can insert a card into an inactive conversation. When the user returns, open the matching run; use a separate webhook or scheduled automation only if the user explicitly requests proactive wakeups.

## Tool selection

- `list_cicd_targets`: Read provider metadata and available GitHub workflows or the GitLab pipeline target.
- `trigger_cicd_run`: Trigger one confirmed workflow or pipeline and render its monitor card.
- `open_cicd_monitor`: Render an existing or latest matching run without triggering anything.
- `get_cicd_run_status`: Read unified run and job status. The conversation card calls this automatically; call it directly only when a text status is needed.

If the remote hostname is unsupported or authentication is unavailable, report the exact host and ask the user to configure `gh` or `glab` outside the plugin. Do not fall back to embedded access keys.
