# Publish workflow safety contract

## Test environment

- Before any source commit or push, fetch the latest `main`/`master` and assess divergence between the demand branch and mainline. Do not compare `dev` for this decision.
- Treat the divergence as large by default when the demand branch is at least 100 commits behind mainline or both sides changed at least 20 of the same files since their common base. Keep both thresholds configurable.
- For small divergence, skip mainline synchronization. At the large-divergence threshold, automatically perform only `main/master -> demand branch`, verify it, and continue test publication.
- Accept only `main` or `master` as mainline and reject them as test targets; never relabel `dev` as mainline to bypass the direction rule.
- Enforce the immutable direction `demand branch -> dev`. Never merge, pull, rebase, or cherry-pick `dev` into the demand branch, including during conflict resolution or resume.
- Fetch the exact remote environment branch before integration.
- Do not call GitHub/GitLab merge APIs and do not clone another repository.
- Treat local `dev` as an environment mirror. If it is missing, create it from `origin/dev`; if it differs, align it exactly to the fetched remote tip before integration, including when it has local-only commits.
- Stop if another worktree has local `dev` checked out. Otherwise switch the current workspace to local `dev` and merge the immutable source SHA there.
- Use `git merge-tree` only to detect conflicts before changing the current checkout. When preflight also shows a new large demand/mainline difference, refresh and synchronize mainline on the demand branch before retrying integration.
- Before pushing `dev`, require both the local demand branch and its remote ref to equal the immutable source SHA that was pushed at the start. Stop if either changed; never synchronize it from `dev`.
- Preserve a conflicted local `dev` checkout and a non-sensitive state file. Resume only with explicit repository, remote, branch, source SHA, and integration-verification arguments that exactly match the generated state.
- When test integration conflicts, refresh the latest remote mainline and record exact demand/mainline ahead, behind, overlapping files, thresholds, and recommendation in the state file. This diagnostic is read-only and never authorizes `dev -> demand` or an automatic mainline sync.
- Fetch `origin/dev` again before push. Allow a normal push only when the latest remote `dev` is an ancestor of the integration result.
- Run source `--verify` commands once on the demand branch. Run only separately supplied `--integration-verify` commands on the merged local `dev` checkout.
- After a verified push, require local and remote `dev` to match, then switch the current workspace back to the demand branch.
- Do not create a temporary worktree during ordinary test publication. If a repository rule or explicit user decision requires one, seed compatible dependencies from the current workspace under [worktree-dependency-reuse.md](worktree-dependency-reuse.md); never use a directory symlink to the source dependencies or fall back to an online install or image download. Missing offline dependencies are a stop condition.

## Production

- Commit only reviewed, staged task files and push the demand branch first.
- Fetch the exact remote mainline. Merge it into the demand branch only when the demand branch does not already contain it and has not already been merged.
- Resolve mainline conflicts on the demand branch, rerun focused verification, and push the updated demand branch.
- After production publication is explicitly authorized, show the exact PR/MR provider, repository, source, target, title, and body as a non-blocking execution update, then continue without a separate content-confirmation pause. Pass the final content explicitly; do not rely on automatic fill.
- Create or reuse a PR/MR matching the exact source SHA and mainline target.
- Require a non-draft request and exact source head. Never use admin/bypass flags.
- Use normal platform auto-merge so required checks and reviews remain authoritative.
- Bound each wait. A timeout leads to status monitoring and the exact resume path, not a duplicate request or uncertain merge attempt.
- Require every PR/MR status read to expose the exact expected source head SHA. Reconcile an ordinary same-branch update and re-verify; preserve a genuine unknown scope as an external blocker.
- Fetch mainline after merge. If it advanced, require the captured PR/MR merge SHA to remain in mainline history and tag only that captured SHA, excluding later commits.
- Create an annotated `v<major>.<minor>.<patch>` tag on the captured merge SHA, never on a later fetched tip. Recheck live mainline immediately before the tag push. Automatic versioning increments the numeric patch component without decimal carry (`v0.0.337` becomes `v0.0.338`).
- Verify the remote peeled tag SHA equals the mainline SHA.
- For a matched repository production profile, wait while its configured workflow is active. Reuse an exact-SHA tag only after a real successful workflow result, monitor pending or unverifiable evidence without duplication, and create at most the configured number of automatic retry tags after provider-reported terminal failures.
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
- Fixed-strategy or guessed conflict resolution; evidence-backed task resolution remains part of the workflow.
- Unbounded polling.
- Broad repository-wide tests when a focused authoritative gate is available.
