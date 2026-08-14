# Publish workflow safety contract

## Test environment

- Before any source commit or push, fetch the latest `main`/`master` and assess divergence between the demand branch and mainline. Do not compare `dev` for this decision.
- Treat the divergence as large by default when the demand branch is at least 100 commits behind mainline or both sides changed at least 20 of the same files since their common base. Keep both thresholds configurable.
- For small divergence, skip mainline synchronization. For large divergence, stop before commit/push and recommend synchronization; perform only `main/master -> demand branch` after explicit `--sync-mainline` authorization.
- Accept only `main` or `master` as mainline and reject them as test targets; never relabel `dev` as mainline to bypass the direction rule.
- Enforce the immutable direction `demand branch -> dev`. Never merge, pull, rebase, or cherry-pick `dev` into the demand branch, including during conflict resolution or resume.
- Fetch the exact remote environment branch before integration.
- Do not call GitHub/GitLab merge APIs and do not clone another repository.
- If local `dev` is missing, create it from `origin/dev`.
- If local `dev` only trails the remote, rebuild it from `origin/dev` when it is not checked out in any worktree.
- If `ahead > 0` and `behind > 0`, regardless of the counts, treat local `dev` as an abnormal disposable integration ref: record its old SHA and counts, delete it, and recreate it from the latest `origin/dev`. Do not merge the abnormal local ref, call a provider merge API, or re-clone the repository.
- If local `dev` is ahead-only, stop because those commits may not exist remotely.
- If another worktree has local `dev` checked out, stop rather than deleting or rewriting a branch in use.
- Merge the immutable source SHA into a detached worktree created from the latest `origin/dev`.
- Before pushing `dev`, require both the local demand branch and its remote ref to equal the immutable source SHA that was pushed at the start. Stop if either changed; never synchronize it from `dev`.
- Preserve a conflicted integration worktree and a non-sensitive state file. Resume only with explicit repository, remote, branch, source SHA, and verification arguments that exactly match the generated state and registered worktree.
- When test integration conflicts, refresh the latest remote mainline and record exact demand/mainline ahead, behind, overlapping files, thresholds, and recommendation in the state file. This diagnostic is read-only and never authorizes `dev -> demand` or an automatic mainline sync.
- Fetch `origin/dev` again before push. Allow a normal push only when the latest remote `dev` is an ancestor of the integration result.
- After a verified push, align the unused local `dev` ref to the pushed SHA only if it has not changed concurrently.

## Production

- Commit only reviewed, staged task files and push the demand branch first.
- Fetch the exact remote mainline. Merge it into the demand branch only when the demand branch does not already contain it and has not already been merged.
- Resolve mainline conflicts on the demand branch, rerun focused verification, and push the updated demand branch.
- Preview and confirm the exact PR/MR provider, repository, source, target, title, and body before creating it. Pass confirmed content explicitly; do not rely on automatic fill.
- Create or reuse a PR/MR matching the exact source SHA and mainline target.
- Require a non-draft request and exact source head. Never use admin/bypass flags.
- Use normal platform auto-merge so required checks and reviews remain authoritative.
- Bound waiting. A timeout is a waiting result, not a reason to recreate, force, re-clone, or retry an uncertain merge.
- Require every PR/MR status read to expose the exact expected source head SHA; stop if it is missing or changes while auto-merge waits.
- Fetch mainline after merge and require its SHA to equal the PR/MR merge SHA before tagging. If it advanced, stop and request a release-scope decision.
- Create an annotated `v<major>.<minor>.<patch>` tag on the captured merge SHA, never on a later fetched tip. Recheck live mainline immediately before the tag push. Automatic versioning increments the numeric patch component without decimal carry (`v0.0.337` becomes `v0.0.338`).
- Verify the remote peeled tag SHA equals the mainline SHA.
- For a matched repository production profile, stop before tag creation or reuse while its configured workflow has a blocking run. Reuse an exact-SHA tag only after a real successful workflow result; require a confirmed reason before replacing a failed, incomplete, or unverifiable exact-SHA tag.
- Return an exact monitor handoff after profile tag creation or reuse. CI success, deployment completion, and runtime verification remain separate evidence layers.

## Identity and state

- Derive the provider and repository from the sanitized remote Host and path.
- Match repository profiles only by normalized `provider:host/namespace/repository`; never by checkout directory. Reject malformed, duplicate, or safety-weakening profile entries before any publish action.
- Keep repository profiles outside target repositories. A profile may preserve stricter project safety rules and record a confirmed external exception, but it must never claim to modify the project rules.
- Use the configured `gh` wrapper or exact `glab` Host selected for the repository. Mechanically require the known mappings `TerraRoot3 -> TerraRoot3`, `hanbaokun/pagepop -> hanbaokun`, and the configured private GitLab Hosts -> `hanbaokun`; stop if an explicit override conflicts.
- Never print or store tokens, keys, raw credential files, or full permission scopes.
- Apply a configurable hard timeout of at most one hour to each Git, forge, and verification command.
- Keep source push, environment push, PR/MR merge, tag push, CI, deployment, and runtime verification as separate reported states.

## Intentionally excluded

- Direct pushes to `main` or `master`.
- PR/MR use for ordinary `dev` publication.
- Re-cloning to escape a stale local environment branch.
- Automatic staging of the whole worktree.
- Automatic conflict resolution.
- Unbounded polling.
- Broad repository-wide tests when a focused authoritative gate is available.
