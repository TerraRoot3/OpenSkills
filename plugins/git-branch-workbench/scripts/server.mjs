#!/usr/bin/env node

import { basename, dirname, isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { readFileSync, realpathSync, statSync } from "node:fs";
import { spawnSync } from "node:child_process";
import readline from "node:readline";

const SERVER_NAME = "git-branch-workbench";
const SERVER_VERSION = "0.4.0";
const TEMPLATE_URI = "ui://git-branch-workbench/v4.html";
const DEFAULT_COMMIT_LIMIT = 200;
const MAX_COMMIT_LIMIT = 200;
const scriptDir = dirname(fileURLToPath(import.meta.url));
const templatePath = resolve(scriptDir, "../assets/branch-workbench.html");
const templateHtml = readFileSync(templatePath, "utf8");

const repoPathProperty = {
  type: "string",
  description: "Absolute path inside the authorized local Git repository."
};

const refProperties = {
  ref: {
    type: "string",
    description: "Exact local branch, remote branch, or tag name returned by a previous snapshot."
  },
  refType: {
    type: "string",
    enum: ["local", "remote", "tag"],
    description: "Whether ref belongs to the local branch, remote branch, or tag list."
  }
};

const limitProperty = {
  type: "integer",
  minimum: 3,
  maximum: MAX_COMMIT_LIMIT,
  default: DEFAULT_COMMIT_LIMIT
};

const widgetMeta = {
  "openai/widgetAccessible": true
};

const toolDefinitions = [
  {
    name: "get_git_snapshot",
    title: "Get Git Repository Snapshot",
    description: "Read local and remote branches, tags, worktrees, hosting metadata, and up to 200 commits for one validated ref.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      properties: {
        repoPath: repoPathProperty,
        ...refProperties,
        limit: limitProperty
      },
      required: ["repoPath"]
    },
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: false
    },
    _meta: {
      ...widgetMeta,
      "openai/toolInvocation/invoking": "Reading Git history…",
      "openai/toolInvocation/invoked": "Git history loaded."
    }
  },
  {
    name: "open_git_branch_workbench",
    title: "Open Git Branch Workbench",
    description: "Open a compact repository card that expands into an interactive Git workbench with local and remote branches, tags, worktrees, up to 200 commits, branch creation, MR/PR creation, and safe pull/push controls.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      properties: {
        repoPath: repoPathProperty,
        ...refProperties,
        limit: limitProperty
      },
      required: ["repoPath"]
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: false
    },
    _meta: {
      ...widgetMeta,
      ui: { resourceUri: TEMPLATE_URI },
      "openai/outputTemplate": TEMPLATE_URI,
      "openai/toolInvocation/invoking": "Opening Git Branch Workbench…",
      "openai/toolInvocation/invoked": "Git Branch Workbench opened."
    }
  },
  {
    name: "switch_git_branch",
    title: "Switch Git Branch",
    description: "Switch to an exact branch from the latest snapshot. Requires a clean worktree. A remote selection creates a same-name local tracking branch when needed.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      properties: {
        repoPath: repoPathProperty,
        branch: { type: "string", description: "Exact branch name from the latest snapshot." },
        branchType: { type: "string", enum: ["local", "remote"] },
        limit: limitProperty
      },
      required: ["repoPath", "branch", "branchType"]
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: false
    },
    _meta: {
      ...widgetMeta,
      "openai/toolInvocation/invoking": "Switching branch…",
      "openai/toolInvocation/invoked": "Branch switched."
    }
  },
  {
    name: "create_git_branch",
    title: "Create Git Branch",
    description: "Create and switch to a new local branch from an exact local branch, remote branch, or tag returned by the latest snapshot. Requires a clean worktree and never overwrites an existing branch.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      properties: {
        repoPath: repoPathProperty,
        branchName: { type: "string", description: "New local branch name validated by git check-ref-format." },
        startRef: { type: "string", description: "Exact source ref name from the latest snapshot." },
        startRefType: { type: "string", enum: ["local", "remote", "tag"] },
        limit: limitProperty
      },
      required: ["repoPath", "branchName", "startRef", "startRefType"]
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: false,
      openWorldHint: false
    },
    _meta: {
      ...widgetMeta,
      "openai/toolInvocation/invoking": "Creating branch…",
      "openai/toolInvocation/invoked": "Branch created."
    }
  },
  {
    name: "pull_current_branch",
    title: "Pull Current Git Branch",
    description: "Fast-forward the clean current branch from its existing upstream with git pull --ff-only. Never creates an upstream and never merges or rebases.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      properties: {
        repoPath: repoPathProperty,
        limit: limitProperty
      },
      required: ["repoPath"]
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: false,
      openWorldHint: true
    },
    _meta: {
      ...widgetMeta,
      "openai/toolInvocation/invoking": "Pulling current branch…",
      "openai/toolInvocation/invoked": "Pull completed."
    }
  },
  {
    name: "push_current_branch",
    title: "Push Current Git Branch",
    description: "Push only HEAD to the current branch's existing upstream. Never force-pushes and never creates or changes an upstream.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      properties: {
        repoPath: repoPathProperty,
        limit: limitProperty
      },
      required: ["repoPath"]
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: false,
      openWorldHint: true
    },
    _meta: {
      ...widgetMeta,
      "openai/toolInvocation/invoking": "Pushing current branch…",
      "openai/toolInvocation/invoked": "Push completed."
    }
  },
  {
    name: "create_merge_request",
    title: "Create Merge Request or Pull Request",
    description: "Create a GitLab merge request or GitHub pull request with the installed authenticated glab or gh CLI. Requires a clean worktree, an exact pushed local source branch, an exact remote target branch, and explicit title/body input. Never pushes automatically.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      properties: {
        repoPath: repoPathProperty,
        sourceBranch: { type: "string", description: "Exact local source branch from the latest snapshot." },
        targetBranch: { type: "string", description: "Exact target branch from mergeTargets in the latest snapshot." },
        title: { type: "string", minLength: 1, maxLength: 200 },
        description: { type: "string", maxLength: 10000 },
        limit: limitProperty
      },
      required: ["repoPath", "sourceBranch", "targetBranch", "title"]
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: false,
      openWorldHint: true
    },
    _meta: {
      ...widgetMeta,
      "openai/toolInvocation/invoking": "Creating MR/PR…",
      "openai/toolInvocation/invoked": "MR/PR created."
    }
  }
];

function writeMessage(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function rpcResult(id, result) {
  writeMessage({ jsonrpc: "2.0", id, result });
}

function rpcError(id, code, message, data) {
  const error = { code, message };
  if (data !== undefined) error.data = data;
  writeMessage({ jsonrpc: "2.0", id, error });
}

function runGit(repoRoot, args, { allowFailure = false, timeout = 10_000 } = {}) {
  const result = spawnSync("git", ["-C", repoRoot, ...args], {
    encoding: "utf8",
    timeout,
    maxBuffer: 8 * 1024 * 1024,
    windowsHide: true
  });

  if (result.error) {
    throw new Error(`Unable to run git: ${result.error.message}`);
  }
  if (result.status !== 0 && !allowFailure) {
    const detail = (result.stderr || result.stdout || "Git command failed").trim();
    throw new Error(detail);
  }
  return {
    ok: result.status === 0,
    stdout: result.stdout || "",
    stderr: result.stderr || ""
  };
}

function resolveRepoRoot(repoPath) {
  if (typeof repoPath !== "string" || !repoPath.trim()) {
    throw new Error("repoPath must be a non-empty absolute path.");
  }
  if (!isAbsolute(repoPath)) {
    throw new Error("repoPath must be absolute.");
  }

  const candidate = realpathSync(repoPath.trim());
  if (!statSync(candidate).isDirectory()) {
    throw new Error("repoPath must point to a directory.");
  }

  const rootOutput = runGit(candidate, ["rev-parse", "--show-toplevel"]).stdout.trim();
  if (!rootOutput) throw new Error("The selected folder is not a Git repository.");
  return realpathSync(rootOutput);
}

function normalizeLimit(value) {
  return Math.max(3, Math.min(MAX_COMMIT_LIMIT, Number.isInteger(value) ? value : DEFAULT_COMMIT_LIMIT));
}

function parseStatus(rawStatus) {
  const result = {
    branch: "HEAD detached",
    upstream: "",
    ahead: 0,
    behind: 0,
    changedFiles: 0,
    clean: true
  };

  for (const line of rawStatus.split("\n")) {
    if (line.startsWith("# branch.head ")) {
      const branch = line.slice(14).trim();
      result.branch = branch === "(detached)" ? "HEAD detached" : branch;
    } else if (line.startsWith("# branch.upstream ")) result.upstream = line.slice(18).trim();
    else if (line.startsWith("# branch.ab ")) {
      const match = line.match(/\+(\d+)\s+-(\d+)/);
      if (match) {
        result.ahead = Number(match[1]);
        result.behind = Number(match[2]);
      }
    } else if (line && !line.startsWith("# ")) {
      result.changedFiles += 1;
    }
  }
  result.clean = result.changedFiles === 0;
  return result;
}

function parseTracking(value) {
  const ahead = value.match(/ahead\s+(\d+)/i);
  const behind = value.match(/behind\s+(\d+)/i);
  return {
    ahead: ahead ? Number(ahead[1]) : 0,
    behind: behind ? Number(behind[1]) : 0
  };
}

function parseCommits(rawLog) {
  return rawLog
    .split("\u001e")
    .map((record) => record.trim())
    .filter(Boolean)
    .map((record) => {
      const [hash, shortHash, parents, decorations, author, email, date, subject = "", ...bodyParts] = record.split("\u001f");
      const refs = (decorations || "")
        .replace(/^\s*\(|\)\s*$/g, "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
      return {
        hash,
        shortHash,
        parents: parents ? parents.split(" ").filter(Boolean) : [],
        refs,
        author,
        email,
        date,
        subject,
        body: bodyParts.join("\u001f").trim()
      };
    });
}

function parseWorktrees(rawWorktrees) {
  const worktrees = [];
  let current = null;
  for (const line of rawWorktrees.split("\n")) {
    if (line.startsWith("worktree ")) {
      if (current) worktrees.push(current);
      current = { path: line.slice(9), head: "", branch: "", detached: false, locked: false, prunable: false };
    } else if (current && line.startsWith("HEAD ")) current.head = line.slice(5);
    else if (current && line.startsWith("branch ")) current.branch = line.slice(7).replace(/^refs\/heads\//, "");
    else if (current && line === "detached") current.detached = true;
    else if (current && line.startsWith("locked")) current.locked = true;
    else if (current && line.startsWith("prunable")) current.prunable = true;
  }
  if (current) worktrees.push(current);
  return worktrees;
}

function getBranchRefs(repoRoot) {
  const remoteNames = runGit(repoRoot, ["remote"], { allowFailure: true }).stdout
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean)
    .sort((left, right) => right.length - left.length);

  const localRaw = runGit(
    repoRoot,
    [
      "for-each-ref",
      "--format=%(refname)%09%(refname:short)%09%(objectname)%09%(upstream:short)%09%(upstream:track)%09%(upstream:remotename)%09%(upstream:remoteref)",
      "refs/heads"
    ],
    { allowFailure: true }
  ).stdout;

  const localBranches = localRaw
    .split("\n")
    .filter(Boolean)
    .map((line) => {
      const [fullRef = "", name = "", hash = "", upstream = "", tracking = "", upstreamRemote = "", upstreamMergeRef = ""] = line.split("\t");
      return {
        fullRef,
        name,
        hash,
        upstream,
        upstreamRemote,
        upstreamMergeRef,
        ...parseTracking(tracking)
      };
    })
    .filter((branch) => branch.fullRef && branch.name);

  const remoteRaw = runGit(
    repoRoot,
    ["for-each-ref", "--format=%(refname)%09%(refname:short)%09%(objectname)%09%(symref)", "refs/remotes"],
    { allowFailure: true }
  ).stdout;

  const remoteBranches = remoteRaw
    .split("\n")
    .filter(Boolean)
    .map((line) => {
      const [fullRef = "", name = "", hash = "", symref = ""] = line.split("\t");
      const remoteName = remoteNames.find((candidate) => fullRef.startsWith(`refs/remotes/${candidate}/`)) || "";
      const shortName = remoteName ? fullRef.slice(`refs/remotes/${remoteName}/`.length) : "";
      const trackingLocal = localBranches.find((branch) => branch.upstream === name)?.name || "";
      return { fullRef, name, hash, symref, remoteName, shortName, trackingLocal };
    })
    .filter((branch) => branch.fullRef && branch.name && branch.remoteName && branch.shortName && branch.shortName !== "HEAD" && !branch.symref);

  const tagRaw = runGit(
    repoRoot,
    [
      "for-each-ref",
      "--format=%(refname)%09%(refname:short)%09%(*objectname)%09%(objectname)%09%(creatordate:iso-strict)%09%(subject)",
      "--sort=-creatordate",
      "refs/tags"
    ],
    { allowFailure: true }
  ).stdout;

  const tags = tagRaw
    .split("\n")
    .filter(Boolean)
    .map((line) => {
      const [fullRef = "", name = "", peeledHash = "", objectHash = "", date = "", ...subjectParts] = line.split("\t");
      return {
        fullRef,
        name,
        hash: peeledHash || objectHash,
        objectHash,
        annotated: Boolean(peeledHash),
        date,
        subject: subjectParts.join("\t")
      };
    })
    .filter((tag) => tag.fullRef && tag.name && tag.hash);

  return { localBranches, remoteBranches, remoteNames, tags };
}

function parseRemoteUrl(remoteUrl = "") {
  const raw = String(remoteUrl || "").trim();
  if (!raw) return { provider: "unknown", host: "", repoSlug: "" };

  let host = "";
  let repoSlug = "";
  const scpLike = raw.match(/^[^@\s]+@([^:\s]+):(.+)$/);
  if (scpLike) {
    host = scpLike[1];
    repoSlug = scpLike[2];
  } else {
    try {
      const url = new URL(raw);
      host = url.hostname;
      repoSlug = url.pathname.replace(/^\//, "");
    } catch {
      return { provider: "unknown", host: "", repoSlug: "" };
    }
  }

  repoSlug = repoSlug.replace(/\.git$/, "").replace(/^\/+|\/+$/g, "");
  const normalizedHost = host.toLowerCase();
  const provider = normalizedHost.includes("github")
    ? "github"
    : normalizedHost.includes("gitlab")
      ? "gitlab"
      : "unknown";
  return { provider, host, repoSlug };
}

function commandAvailable(command) {
  const result = spawnSync(command, ["--version"], {
    encoding: "utf8",
    timeout: 5_000,
    maxBuffer: 1024 * 1024,
    windowsHide: true
  });
  return !result.error && result.status === 0;
}

function getHostingInfo(repoRoot, refs, status) {
  const current = refs.localBranches.find((branch) => branch.name === status.branch);
  const remoteName = current?.upstreamRemote || (refs.remoteNames.includes("origin") ? "origin" : refs.remoteNames[0] || "");
  const remoteUrl = remoteName
    ? runGit(repoRoot, ["remote", "get-url", remoteName], { allowFailure: true }).stdout.trim()
    : "";
  const parsed = parseRemoteUrl(remoteUrl);
  const mergeTargets = refs.remoteBranches
    .filter((branch) => branch.remoteName === remoteName)
    .map((branch) => branch.shortName)
    .filter((name, index, values) => name && values.indexOf(name) === index)
    .sort((left, right) => left.localeCompare(right));
  const symbolicDefault = remoteName
    ? runGit(repoRoot, ["symbolic-ref", "--quiet", "--short", `refs/remotes/${remoteName}/HEAD`], { allowFailure: true }).stdout.trim()
    : "";
  const defaultPrefix = remoteName ? `${remoteName}/` : "";
  const symbolicName = symbolicDefault.startsWith(defaultPrefix) ? symbolicDefault.slice(defaultPrefix.length) : "";
  const suggestedBaseBranch = [symbolicName, "main", "master", mergeTargets[0]]
    .find((name) => name && mergeTargets.includes(name)) || "";
  const cli = parsed.provider === "github" ? "gh" : parsed.provider === "gitlab" ? "glab" : "";

  return {
    provider: parsed.provider,
    host: parsed.host,
    repoSlug: parsed.repoSlug,
    remoteName,
    cli,
    cliAvailable: cli ? commandAvailable(cli) : false,
    mergeTargets,
    suggestedBaseBranch
  };
}

function selectRevision(input, refs, currentBranch, head) {
  const requested = typeof input.ref === "string" ? input.ref.trim() : "";
  const requestedType = input.refType;
  let selected = null;
  let selectedRefType = "local";

  if (requested) {
    if (!requestedType || requestedType === "local") {
      selected = refs.localBranches.find((branch) => branch.name === requested);
      if (selected) selectedRefType = "local";
    }
    if (!selected && (!requestedType || requestedType === "remote")) {
      selected = refs.remoteBranches.find((branch) => branch.name === requested);
      if (selected) selectedRefType = "remote";
    }
    if (!selected && (!requestedType || requestedType === "tag")) {
      selected = refs.tags.find((tag) => tag.name === requested);
      if (selected) selectedRefType = "tag";
    }
    if (!selected) throw new Error("The selected ref is no longer present. Refresh the branch, tag, and worktree lists and try again.");
  } else {
    selected = refs.localBranches.find((branch) => branch.name === currentBranch) || null;
  }

  if (selected) {
    return { selectedRef: selected.name, selectedRefType, revision: selected.fullRef };
  }
  if (!head) throw new Error("The repository does not contain a commit yet.");
  return { selectedRef: "HEAD", selectedRefType: "detached", revision: head };
}

function getSnapshot(input = {}, operation = null) {
  const repoRoot = resolveRepoRoot(input.repoPath);
  const limit = normalizeLimit(input.limit);
  const status = parseStatus(runGit(repoRoot, ["status", "--porcelain=v2", "--branch"]).stdout);
  const head = runGit(repoRoot, ["rev-parse", "HEAD"], { allowFailure: true }).stdout.trim();
  const refs = getBranchRefs(repoRoot);
  const selection = selectRevision(input, refs, status.branch, head);
  const logResult = runGit(
    repoRoot,
    [
      "log",
      selection.revision,
      "--topo-order",
      `--max-count=${limit}`,
      "--date=iso-strict",
      "--decorate=short",
      "--pretty=format:%H%x1f%h%x1f%P%x1f%D%x1f%an%x1f%ae%x1f%ad%x1f%s%x1f%b%x1e"
    ],
    { allowFailure: true }
  );
  const worktreeResult = runGit(repoRoot, ["worktree", "list", "--porcelain"], { allowFailure: true });
  const hosting = getHostingInfo(repoRoot, refs, status);

  return {
    schemaVersion: 4,
    generatedAt: new Date().toISOString(),
    repoRoot,
    repoName: basename(repoRoot),
    head,
    currentBranch: status.branch,
    ...status,
    ...selection,
    commits: logResult.ok ? parseCommits(logResult.stdout) : [],
    localBranches: refs.localBranches
      .map((branch) => ({ ...branch, current: branch.name === status.branch }))
      .sort((left, right) => Number(right.current) - Number(left.current) || left.name.localeCompare(right.name)),
    remoteBranches: refs.remoteBranches,
    tags: refs.tags,
    worktrees: worktreeResult.ok
      ? parseWorktrees(worktreeResult.stdout).map((worktree) => ({ ...worktree, current: worktree.path === repoRoot }))
      : [],
    hosting,
    mergeTargets: hosting.mergeTargets,
    suggestedBaseBranch: hosting.suggestedBaseBranch,
    limit,
    operation
  };
}

function ensureClean(repoRoot) {
  const status = parseStatus(runGit(repoRoot, ["status", "--porcelain=v2", "--branch"]).stdout);
  if (!status.clean) {
    throw new Error(`工作区有 ${status.changedFiles} 个未提交改动。请先提交或暂存处理后再切换/拉取。`);
  }
  return status;
}

function switchBranch(input = {}) {
  const repoRoot = resolveRepoRoot(input.repoPath);
  ensureClean(repoRoot);
  const refs = getBranchRefs(repoRoot);
  const branchName = typeof input.branch === "string" ? input.branch.trim() : "";
  const branchType = input.branchType;
  if (!branchName || !["local", "remote"].includes(branchType)) {
    throw new Error("branch and branchType are required.");
  }

  let nextBranch = branchName;
  let message = "";
  if (branchType === "local") {
    const target = refs.localBranches.find((branch) => branch.name === branchName);
    if (!target) throw new Error("本地分支已不存在。请刷新分支列表后重试。");
    runGit(repoRoot, ["switch", target.name], { timeout: 30_000 });
    message = `已切换到本地分支 ${target.name}`;
  } else {
    const target = refs.remoteBranches.find((branch) => branch.name === branchName);
    if (!target) throw new Error("远端分支已不存在。请刷新分支列表后重试。");
    const existing = refs.localBranches.find((branch) => branch.name === target.shortName);
    if (existing) {
      if (existing.upstream !== target.name) {
        throw new Error(`本地分支 ${target.shortName} 已存在，但未跟踪 ${target.name}。请从本地分支列表切换。`);
      }
      runGit(repoRoot, ["switch", existing.name], { timeout: 30_000 });
      nextBranch = existing.name;
      message = `已切换到跟踪 ${target.name} 的本地分支 ${existing.name}`;
    } else {
      runGit(repoRoot, ["switch", "--track", "--create", target.shortName, target.name], { timeout: 30_000 });
      nextBranch = target.shortName;
      message = `已创建并切换到本地跟踪分支 ${target.shortName}`;
    }
  }

  return getSnapshot(
    { repoPath: repoRoot, ref: nextBranch, refType: "local", limit: normalizeLimit(input.limit) },
    { type: "switch", ok: true, message }
  );
}

function resolveExactStartRef(refs, startRef, startRefType) {
  if (startRefType === "local") return refs.localBranches.find((branch) => branch.name === startRef) || null;
  if (startRefType === "remote") return refs.remoteBranches.find((branch) => branch.name === startRef) || null;
  if (startRefType === "tag") return refs.tags.find((tag) => tag.name === startRef) || null;
  return null;
}

function createBranch(input = {}) {
  const repoRoot = resolveRepoRoot(input.repoPath);
  ensureClean(repoRoot);
  const refs = getBranchRefs(repoRoot);
  const branchName = typeof input.branchName === "string" ? input.branchName.trim() : "";
  const startRef = typeof input.startRef === "string" ? input.startRef.trim() : "";
  const startRefType = input.startRefType;
  if (!branchName || !startRef || !["local", "remote", "tag"].includes(startRefType)) {
    throw new Error("branchName, startRef, and startRefType are required.");
  }
  if (!runGit(repoRoot, ["check-ref-format", "--branch", branchName], { allowFailure: true }).ok) {
    throw new Error("分支名称不符合 Git ref 格式。请修改后重试。");
  }
  if (refs.localBranches.some((branch) => branch.name === branchName)) {
    throw new Error(`本地分支 ${branchName} 已存在。请直接从本地分支列表切换。`);
  }

  const source = resolveExactStartRef(refs, startRef, startRefType);
  if (!source) throw new Error("创建基准引用已不存在。请刷新列表后重试。");
  runGit(repoRoot, ["switch", "--no-track", "--create", branchName, source.fullRef], { timeout: 30_000 });
  return getSnapshot(
    { repoPath: repoRoot, ref: branchName, refType: "local", limit: normalizeLimit(input.limit) },
    { type: "createBranch", ok: true, message: `已从 ${startRefType === "tag" ? "标签" : "分支"} ${startRef} 创建并切换到 ${branchName}` }
  );
}

function runHostingCli(command, args, repoRoot) {
  const result = spawnSync(command, args, {
    cwd: repoRoot,
    encoding: "utf8",
    timeout: 120_000,
    maxBuffer: 8 * 1024 * 1024,
    windowsHide: true
  });
  if (result.error) throw new Error(`无法运行 ${command}: ${result.error.message}`);
  if (result.status !== 0) {
    throw new Error((result.stderr || result.stdout || `${command} 执行失败`).trim());
  }
  return `${result.stdout || ""}\n${result.stderr || ""}`.trim();
}

function createMergeRequest(input = {}) {
  const repoRoot = resolveRepoRoot(input.repoPath);
  const status = ensureClean(repoRoot);
  const refs = getBranchRefs(repoRoot);
  const sourceBranch = typeof input.sourceBranch === "string" ? input.sourceBranch.trim() : "";
  const targetBranch = typeof input.targetBranch === "string" ? input.targetBranch.trim() : "";
  const title = typeof input.title === "string" ? input.title.trim() : "";
  const description = typeof input.description === "string" ? input.description.trim() : "";
  if (!sourceBranch || !targetBranch || !title) {
    throw new Error("源分支、目标分支和标题都是必填项。");
  }
  if (title.length > 200 || description.length > 10_000) {
    throw new Error("MR/PR 标题最多 200 个字符，描述最多 10000 个字符。");
  }

  const source = refs.localBranches.find((branch) => branch.name === sourceBranch);
  if (!source) throw new Error("源本地分支已不存在。请刷新后重试。");
  if (!source.upstream || !source.upstreamRemote || !source.upstreamMergeRef) {
    throw new Error(`源分支 ${sourceBranch} 没有 upstream。请先显式推送并设置 upstream 后再创建 MR/PR。`);
  }
  if (source.ahead > 0) {
    throw new Error(`源分支 ${sourceBranch} 还有 ${source.ahead} 个本地提交未推送。请先显式推送后再创建 MR/PR。`);
  }
  if (sourceBranch === targetBranch) {
    throw new Error("源分支和目标分支不能相同。");
  }
  const remoteTarget = refs.remoteBranches.find(
    (branch) => branch.remoteName === source.upstreamRemote && branch.shortName === targetBranch
  );
  if (!remoteTarget) throw new Error(`远端 ${source.upstreamRemote} 上不存在目标分支 ${targetBranch}。请刷新后重试。`);

  const hosting = getHostingInfo(repoRoot, refs, { ...status, branch: sourceBranch });
  if (!hosting.cli || !hosting.cliAvailable || !hosting.host || !hosting.repoSlug) {
    throw new Error("当前 remote 不是可识别的 GitHub/GitLab 仓库，或本机缺少已登录的 gh/glab CLI。");
  }
  const repoSelector = hosting.host === "github.com" ? hosting.repoSlug : `https://${hosting.host}/${hosting.repoSlug}`;
  let output = "";
  if (hosting.provider === "github") {
    output = runHostingCli(
      "gh",
      ["pr", "create", "--repo", repoSelector, "--base", targetBranch, "--head", sourceBranch, "--title", title, "--body", description],
      repoRoot
    );
  } else if (hosting.provider === "gitlab") {
    output = runHostingCli(
      "glab",
      ["mr", "create", "--repo", repoSelector, "--source-branch", sourceBranch, "--target-branch", targetBranch, "--title", title, "--description", description, "--yes", "--no-editor"],
      repoRoot
    );
  } else {
    throw new Error("当前 Git 托管平台暂不支持直接创建 MR/PR。");
  }

  const url = output.match(/https?:\/\/[^\s]+/)?.[0]?.replace(/[),.;]+$/, "") || "";
  const label = hosting.provider === "github" ? "Pull Request" : "Merge Request";
  return getSnapshot(
    { repoPath: repoRoot, ref: status.branch, refType: "local", limit: normalizeLimit(input.limit) },
    {
      type: "createMergeRequest",
      ok: true,
      provider: hosting.provider,
      url,
      message: `${label} 已创建：${sourceBranch} → ${targetBranch}${url ? `\n${url}` : ""}`
    }
  );
}

function requireCurrentUpstream(repoRoot, { requireClean = false } = {}) {
  const status = requireClean
    ? ensureClean(repoRoot)
    : parseStatus(runGit(repoRoot, ["status", "--porcelain=v2", "--branch"]).stdout);
  if (!status.branch || status.branch === "HEAD detached") {
    throw new Error("当前处于 detached HEAD，不能执行此操作。请先切换到本地分支。");
  }
  const refs = getBranchRefs(repoRoot);
  const current = refs.localBranches.find((branch) => branch.name === status.branch);
  if (!current) throw new Error("找不到当前本地分支。请刷新后重试。");
  if (!current.upstream || !current.upstreamRemote || !current.upstreamMergeRef) {
    throw new Error(`当前分支 ${current.name} 没有 upstream。本工作台不会自动创建或修改 upstream。`);
  }
  if (!refs.remoteNames.includes(current.upstreamRemote)) {
    throw new Error(`upstream 远端 ${current.upstreamRemote} 不在当前仓库远端列表中。`);
  }
  if (!current.upstreamMergeRef.startsWith("refs/heads/")) {
    throw new Error("当前 upstream 不是可支持的远端分支引用。");
  }
  return { status, current };
}

function pullCurrentBranch(input = {}) {
  const repoRoot = resolveRepoRoot(input.repoPath);
  const { current } = requireCurrentUpstream(repoRoot, { requireClean: true });
  const remoteBranch = current.upstreamMergeRef.slice("refs/heads/".length);
  const result = runGit(repoRoot, ["pull", "--ff-only", current.upstreamRemote, remoteBranch], { timeout: 120_000 });
  const message = (result.stdout || result.stderr).trim() || `已从 ${current.upstream} 快进拉取`;
  return getSnapshot(
    { repoPath: repoRoot, ref: current.name, refType: "local", limit: normalizeLimit(input.limit) },
    { type: "pull", ok: true, message }
  );
}

function pushCurrentBranch(input = {}) {
  const repoRoot = resolveRepoRoot(input.repoPath);
  const { current } = requireCurrentUpstream(repoRoot);
  const result = runGit(
    repoRoot,
    ["push", current.upstreamRemote, `HEAD:${current.upstreamMergeRef}`],
    { timeout: 120_000 }
  );
  const message = (result.stdout || result.stderr).trim() || `已推送到 ${current.upstream}`;
  return getSnapshot(
    { repoPath: repoRoot, ref: current.name, refType: "local", limit: normalizeLimit(input.limit) },
    { type: "push", ok: true, message }
  );
}

function toolResult(snapshot, render = false) {
  const summary = `${snapshot.repoName}: ${snapshot.currentBranch}, ${snapshot.commits.length} commits on ${snapshot.selectedRef}, ${snapshot.localBranches.length} local branches, ${snapshot.remoteBranches.length} remote branches, ${snapshot.tags.length} tags, ${snapshot.worktrees.length} worktrees.`;
  const result = {
    structuredContent: snapshot,
    content: [{ type: "text", text: snapshot.operation?.message || summary }]
  };
  if (render) {
    result._meta = {
      ui: { resourceUri: TEMPLATE_URI },
      "openai/outputTemplate": TEMPLATE_URI
    };
  }
  return result;
}

async function handleRequest(message) {
  const { id, method, params = {} } = message;
  if (id === undefined || id === null) return;

  try {
    if (method === "initialize") {
      rpcResult(id, {
        protocolVersion: params.protocolVersion || "2025-06-18",
        capabilities: { tools: {}, resources: {} },
        serverInfo: { name: SERVER_NAME, version: SERVER_VERSION }
      });
      return;
    }
    if (method === "ping") {
      rpcResult(id, {});
      return;
    }
    if (method === "tools/list") {
      rpcResult(id, { tools: toolDefinitions });
      return;
    }
    if (method === "resources/list") {
      rpcResult(id, {
        resources: [{ name: "Git Branch Workbench", uri: TEMPLATE_URI, mimeType: "text/html;profile=mcp-app" }]
      });
      return;
    }
    if (method === "resources/templates/list") {
      rpcResult(id, { resourceTemplates: [] });
      return;
    }
    if (method === "resources/read") {
      if (params.uri !== TEMPLATE_URI) {
        rpcError(id, -32002, `Unknown resource: ${params.uri}`);
        return;
      }
      rpcResult(id, {
        contents: [
          {
            uri: TEMPLATE_URI,
            mimeType: "text/html;profile=mcp-app",
            text: templateHtml,
            _meta: {
              ui: { prefersBorder: false },
              "openai/widgetPrefersBorder": false
            }
          }
        ]
      });
      return;
    }
    if (method === "tools/call") {
      const toolName = params.name;
      const args = params.arguments || {};
      if (toolName === "get_git_snapshot") {
        rpcResult(id, toolResult(getSnapshot(args)));
        return;
      }
      if (toolName === "open_git_branch_workbench") {
        rpcResult(id, toolResult(getSnapshot(args), true));
        return;
      }
      if (toolName === "switch_git_branch") {
        rpcResult(id, toolResult(switchBranch(args)));
        return;
      }
      if (toolName === "create_git_branch") {
        rpcResult(id, toolResult(createBranch(args)));
        return;
      }
      if (toolName === "pull_current_branch") {
        rpcResult(id, toolResult(pullCurrentBranch(args)));
        return;
      }
      if (toolName === "push_current_branch") {
        rpcResult(id, toolResult(pushCurrentBranch(args)));
        return;
      }
      if (toolName === "create_merge_request") {
        rpcResult(id, toolResult(createMergeRequest(args)));
        return;
      }
      rpcError(id, -32602, `Unknown tool: ${toolName}`);
      return;
    }
    rpcError(id, -32601, `Method not found: ${method}`);
  } catch (error) {
    rpcResult(id, {
      isError: true,
      content: [{ type: "text", text: error instanceof Error ? error.message : String(error) }]
    });
  }
}

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on("line", (line) => {
  if (!line.trim()) return;
  try {
    const message = JSON.parse(line);
    void handleRequest(message);
  } catch (error) {
    rpcError(null, -32700, "Parse error", error instanceof Error ? error.message : String(error));
  }
});

process.on("uncaughtException", (error) => {
  process.stderr.write(`[${SERVER_NAME}] ${error.stack || error.message}\n`);
});
