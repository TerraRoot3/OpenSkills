# Worktree dependency reuse

Read this reference only when a repository rule or explicit user decision requires an isolated worktree. Ordinary test publication uses the current workspace and does not enter this path.

## Compatibility gate

Before copying dependencies, resolve the exact source workspace and target worktree and verify all of the following:

- The source dependencies already support the verification command that will run in the target.
- The source and target use the same operating system, CPU architecture, Node runtime, package-manager version, and native-module ABI.
- Their lockfile, workspace definition, root manifest, and every package manifest in the verification scope are byte-identical. For pnpm this normally includes `pnpm-lock.yaml`, `pnpm-workspace.yaml`, the root `package.json`, and the affected workspace `package.json` files.
- The target dependency directory does not already contain unrelated files. Never merge two uncertain dependency trees.

If any compatibility check fails, do not present a copied `node_modules` as valid. Use the existing package-manager store with an offline frozen-lockfile install in the target when the repository permits it. For pnpm, use `pnpm install --offline --frozen-lockfile`; if the store lacks a required package, stop or use the target CI. Do not retry online, change registries, install from a mirror, or download a container image.

## Copy compatible dependencies

Copy the complete package-manager-managed dependency directories required by the verification scope, including root and relevant workspace-level `node_modules` directories. Preserve permissions and symbolic links; do not dereference pnpm's relative links.

On macOS when source and target are on APFS, prefer a copy-on-write clone for each dependency directory whose target does not yet exist:

```bash
/bin/cp -cR -P <source>/node_modules <target>/node_modules
```

If clone copy is unavailable, use an archive-preserving copy only after checking the dependency size and free space. Never replace the copy with `ln -s <source>/node_modules <target>/node_modules`: a directory symlink couples the worktrees, can make pnpm reject the layout, and breaks when either workspace is removed.

Do not copy branch-dependent outputs or caches such as `dist`, `.next`, `.nuxt`, coverage output, or generated application assets unless a separate repository contract explicitly owns them.

## Verify the result

Before running the integration command:

- Confirm the target dependency directories are real directories rather than links to the source workspace.
- Reject absolute links or any link that escapes into the source workspace; pnpm's internal relative links inside the copied tree are expected.
- Confirm the source workspace remains unchanged.
- Run only the focused integration verification already authorized for the worktree.

Removing the worktree must not remove or mutate the source dependencies. Report whether dependency reuse was clone copy, full copy, or offline store reconstruction, and report any verification that was left to CI.
