# Verification Matrix

Use this file only when the repository type or verification command is not obvious.

## Repository Detection

Treat the repository as backend or web frontend when it looks like one of these:

- `go.mod` present: Go backend or Go service
- `package.json` present without Flutter/mobile structure: Node backend or web frontend
- `pnpm-lock.yaml`, `package-lock.json`, or `yarn.lock`: web or Node project

Treat the repository as mobile app and do not use this skill when you see signals like:

- `pubspec.yaml` with `flutter`
- `ios/` and `android/` app structure
- React Native mobile structure

## Target Branch Reminder

Use the target branch resolved from the user request:

- Test environment: `dev`
- Pre-release environment: `release`
- Production environment: first merge the latest `master` into the source branch, rerun verification, push that source branch, then merge into `master`, then push the next release tag from `master`
- Explicit branch name from the user overrides the default mapping

## Common Verification Commands

### Go repositories

Prefer:

```bash
go test ./...
go build ./...
```

If the repository already uses a narrower release command, reuse that instead of broadening scope.

### Node or web repositories

Choose the package manager from lockfiles:

- `pnpm-lock.yaml`: use `pnpm`
- `package-lock.json`: use `npm`
- `yarn.lock`: use `yarn`

Then inspect `package.json` scripts and prefer the smallest meaningful set, usually:

```bash
<pkg> test
<pkg> run build
```

Examples:

```bash
npm test
npm run build
pnpm test
pnpm build
yarn test
yarn build
```

If no `test` script exists, use another meaningful project check such as `lint`, `typecheck`, or the documented CI entrypoint.

## Conflict Resolution Reminder

After resolving merge conflicts on the target branch, rerun the same verification family that was used on the source branch whenever practical.

For production publishes:

- always rerun the same verification family after merging `master` into the source branch before pushing that source branch
- once after resolving conflicts while merging the source branch back into `master`
