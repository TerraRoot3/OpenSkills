#!/usr/bin/env node

import { basename, dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  chmodSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  realpathSync,
  renameSync,
  statSync,
  unlinkSync,
  writeFileSync
} from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import { homedir } from "node:os";
import { spawnSync } from "node:child_process";
import readline from "node:readline";

const SERVER_NAME = "cicd-pipeline-monitor";
const SERVER_VERSION = "0.1.0";
const TEMPLATE_BASE_URI = "ui://cicd-pipeline-monitor/pipeline-monitor.html";
const TERMINAL_STATUSES = new Set(["success", "failed", "cancelled", "skipped"]);
const GH_COMMAND = process.env.CICD_PIPELINE_MONITOR_GH_COMMAND || "gh";
const GLAB_COMMAND = process.env.CICD_PIPELINE_MONITOR_GLAB_COMMAND || "glab";
const STATE_SCHEMA_VERSION = 1;
const STATE_RETENTION_MILLISECONDS = 180 * 24 * 60 * 60 * 1000;
const STATE_MAX_FILES = 256;
const STATE_MAX_FILE_BYTES = 2 * 1024 * 1024;
const STATE_DIR = resolve(
  process.env.CICD_PIPELINE_MONITOR_STATE_DIR || join(homedir(), ".codex/state/cicd-pipeline-monitor")
);
const scriptDir = dirname(fileURLToPath(import.meta.url));
const templateHtml = readFileSync(resolve(scriptDir, "../assets/pipeline-monitor.html"), "utf8");

const repoPathProperty = {
  type: "string",
  description: "Absolute path inside the authorized local Git repository."
};

const providerProperty = {
  type: "string",
  enum: ["github", "gitlab"],
  description: "Provider returned by list_cicd_targets. Omit only when it should be detected from origin."
};

const monitorProperties = {
  repoPath: repoPathProperty,
  provider: providerProperty,
  workflow: {
    type: "string",
    description: "Exact GitHub workflow ID, path, or name returned by discovery."
  },
  runId: {
    type: "string",
    description: "Exact GitHub Actions run database ID."
  },
  pipelineId: {
    type: "string",
    description: "Exact GitLab pipeline ID."
  },
  projectPath: {
    type: "string",
    description: "Exact GitLab namespace/project path. Normally detected from the remote."
  },
  ref: {
    type: "string",
    description: "Exact branch or tag ref."
  },
  environment: {
    type: "string",
    description: "Human-readable deployment environment label such as test, staging, or production."
  },
  triggeredAt: {
    type: "string",
    description: "ISO timestamp used to locate a just-triggered GitHub workflow before its run ID is available."
  }
};

const widgetMeta = { "openai/widgetAccessible": true };

const toolDefinitions = [
  {
    name: "list_cicd_targets",
    title: "List CI/CD Targets",
    description: "Detect the repository provider and read available GitHub Actions workflows or the GitLab pipeline target without triggering a run.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      properties: { repoPath: repoPathProperty },
      required: ["repoPath"]
    },
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: true
    },
    _meta: {
      ...widgetMeta,
      "openai/toolInvocation/invoking": "正在读取发布流水线…",
      "openai/toolInvocation/invoked": "发布流水线已读取。"
    }
  },
  {
    name: "trigger_cicd_run",
    title: "Trigger CI/CD Run",
    description: "Trigger one explicitly confirmed GitHub Actions workflow_dispatch or GitLab pipeline and render the only live monitor card needed for that run. Call once. A successful result already mounts the monitor, so never call open_cicd_monitor for the same run afterward and never retry this rendering tool after an uncertain response.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      properties: {
        repoPath: repoPathProperty,
        provider: providerProperty,
        workflow: monitorProperties.workflow,
        ref: monitorProperties.ref,
        environment: monitorProperties.environment,
        confirmed: {
          type: "boolean",
          const: true,
          description: "Must be true only after the user confirms the exact provider, repository, workflow or pipeline, ref, environment, and input or variable names."
        },
        inputs: {
          type: "object",
          description: "GitHub workflow_dispatch string inputs. Values are sent through stdin and are never returned by the tool.",
          additionalProperties: { type: "string" }
        },
        variables: {
          type: "object",
          description: "GitLab pipeline variables. Values are sent through stdin and are never returned by the tool.",
          additionalProperties: { type: "string" }
        }
      },
      required: ["repoPath", "provider", "ref", "environment", "confirmed"]
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: true,
      idempotentHint: false,
      openWorldHint: true
    },
    _meta: {
      ...widgetMeta,
      ui: { resourceUri: TEMPLATE_BASE_URI },
      "openai/outputTemplate": TEMPLATE_BASE_URI,
      "openai/toolInvocation/invoking": "正在触发发布流水线…",
      "openai/toolInvocation/invoked": "发布流水线已触发。"
    }
  },
  {
    name: "open_cicd_monitor",
    title: "Open CI/CD Monitor",
    description: "Render one compact live card for an existing or latest matching GitHub Actions run or GitLab pipeline without triggering anything. Call once per monitoring request only when no card has already been rendered for that run in the current task. Never call after trigger_cicd_run for the same run, repeat to refresh, or retry merely because the card is not yet visible; the mounted card calls get_cicd_run_status itself.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      properties: monitorProperties,
      required: ["repoPath"]
    },
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: true
    },
    _meta: {
      ...widgetMeta,
      ui: { resourceUri: TEMPLATE_BASE_URI },
      "openai/outputTemplate": TEMPLATE_BASE_URI,
      "openai/toolInvocation/invoking": "正在打开流水线监控…",
      "openai/toolInvocation/invoked": "流水线监控已打开。"
    }
  },
  {
    name: "get_cicd_run_status",
    title: "Get CI/CD Run Status",
    description: "Read and normalize one GitHub Actions run or GitLab pipeline plus its jobs without mounting a new card. This is the data-only refresh tool used automatically by the existing monitor card.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      properties: monitorProperties,
      required: ["repoPath", "provider"]
    },
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: true
    },
    _meta: {
      ...widgetMeta,
      "openai/toolInvocation/invoking": "正在刷新流水线状态…",
      "openai/toolInvocation/invoked": "流水线状态已刷新。"
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

function redactText(value, redactions = []) {
  let text = String(value || "");
  for (const secret of redactions) {
    if (typeof secret === "string" && secret.length >= 3) text = text.split(secret).join("[REDACTED]");
  }
  return text
    .replace(/(token|authorization|private[-_ ]?token)(\s*[:=]\s*)\S+/gi, "$1$2[REDACTED]")
    .trim()
    .slice(0, 1200);
}

function runCommand(command, args, { cwd, input, timeout = 30_000, allowFailure = false, redactions = [] } = {}) {
  const result = spawnSync(command, args, {
    cwd,
    input,
    encoding: "utf8",
    timeout,
    maxBuffer: 8 * 1024 * 1024,
    windowsHide: true,
    env: {
      ...process.env,
      GH_PROMPT_DISABLED: "1",
      GIT_TERMINAL_PROMPT: "0",
      GLAB_PAGER: "cat",
      PAGER: "cat",
      NO_COLOR: "1"
    }
  });

  if (result.error) {
    if (result.error.code === "ENOENT") throw new Error(`找不到 ${basename(command)}，请先安装并完成登录。`);
    throw new Error(`${basename(command)} 执行失败：${redactText(result.error.message, redactions)}`);
  }

  const output = {
    ok: result.status === 0,
    status: result.status,
    stdout: result.stdout || "",
    stderr: result.stderr || ""
  };
  if (!output.ok && !allowFailure) {
    const hasShortSecret = redactions.some((secret) => typeof secret === "string" && secret.length > 0 && secret.length < 3);
    const detail = hasShortSecret
      ? "命令返回错误，敏感输入与提供商原始输出已隐藏"
      : redactText(output.stderr || output.stdout || "命令执行失败", redactions);
    throw new Error(`${basename(command)} 执行失败：${detail || "未知错误"}`);
  }
  return output;
}

function runGit(repoRoot, args, options = {}) {
  return runCommand("git", ["-C", repoRoot, ...args], { timeout: 15_000, ...options });
}

function parseJson(raw, label) {
  try {
    return JSON.parse(raw || "null");
  } catch {
    throw new Error(`${label} 返回了无法解析的数据。`);
  }
}

function requireText(value, label, maxLength = 500) {
  if (typeof value !== "string" || !value.trim()) throw new Error(`${label} 不能为空。`);
  if (value.length > maxLength) throw new Error(`${label} 过长。`);
  return value.trim();
}

function resolveRepoRoot(repoPath) {
  if (typeof repoPath !== "string" || !repoPath.trim()) throw new Error("repoPath 必须是非空绝对路径。");
  if (!isAbsolute(repoPath)) throw new Error("repoPath 必须是绝对路径。");
  const candidate = realpathSync(repoPath.trim());
  if (!statSync(candidate).isDirectory()) throw new Error("repoPath 必须指向目录。");
  const root = runGit(candidate, ["rev-parse", "--show-toplevel"]).stdout.trim();
  if (!root) throw new Error("所选目录不是 Git 仓库。");
  return realpathSync(root);
}

function parseRemoteUrl(remoteUrl) {
  const raw = requireText(remoteUrl, "Git 远端地址", 2000);
  let host = "";
  let projectPath = "";

  const scpMatch = raw.match(/^(?:[^@/]+@)?([^:/]+):(.+)$/);
  if (scpMatch && !raw.includes("://")) {
    host = scpMatch[1];
    projectPath = scpMatch[2];
  } else {
    let parsed;
    try {
      parsed = new URL(raw);
    } catch {
      throw new Error("无法识别 origin 远端地址。仅支持标准 SSH 或 HTTPS Git 远端。");
    }
    host = parsed.hostname;
    projectPath = parsed.pathname.replace(/^\/+/, "");
  }

  projectPath = projectPath.replace(/\.git$/i, "").replace(/^\/+|\/+$/g, "");
  if (!host || !projectPath || !projectPath.includes("/")) throw new Error("远端地址缺少有效的主机或仓库路径。");
  return { host: host.toLowerCase(), projectPath };
}

function currentRef(repoRoot) {
  const result = runGit(repoRoot, ["symbolic-ref", "--quiet", "--short", "HEAD"], { allowFailure: true });
  return result.ok ? result.stdout.trim() : "";
}

function discoverRepository(repoPath, requestedProvider) {
  const repoRoot = resolveRepoRoot(repoPath);
  let remote = runGit(repoRoot, ["remote", "get-url", "origin"], { allowFailure: true });
  if (!remote.ok || !remote.stdout.trim()) {
    const remotes = runGit(repoRoot, ["remote"]).stdout.trim().split("\n").filter(Boolean);
    if (!remotes.length) throw new Error("当前仓库没有 Git 远端，无法识别 CI/CD 提供商。");
    remote = runGit(repoRoot, ["remote", "get-url", remotes[0]]);
  }
  const parsed = parseRemoteUrl(remote.stdout.trim());
  let provider = requestedProvider;
  if (!provider) {
    if (parsed.host.includes("github")) provider = "github";
    else if (parsed.host.includes("gitlab")) provider = "gitlab";
    else {
      const auth = runCommand(GLAB_COMMAND, ["auth", "status", "--hostname", parsed.host], {
        cwd: repoRoot,
        allowFailure: true,
        timeout: 15_000
      });
      if (auth.ok) provider = "gitlab";
    }
  }
  if (provider !== "github" && provider !== "gitlab") {
    throw new Error(`无法识别 ${parsed.host} 的 CI/CD 提供商。请先为该主机配置 gh 或 glab。`);
  }
  if (requestedProvider === "gitlab" && parsed.host.includes("github")) {
    throw new Error("请求的 GitLab provider 与 GitHub 远端不一致。");
  }
  if (provider === "github" && !parsed.host.includes("github")) {
    throw new Error(`远端 ${parsed.host} 未识别为 GitHub 主机。`);
  }
  return {
    repoRoot,
    repoName: basename(repoRoot),
    host: parsed.host,
    projectPath: parsed.projectPath,
    provider,
    currentRef: currentRef(repoRoot)
  };
}

function ghRepoId(repo) {
  return repo.host === "github.com" ? repo.projectPath : `${repo.host}/${repo.projectPath}`;
}

function gitlabEndpoint(repo, suffix = "") {
  return `projects/${encodeURIComponent(repo.projectPath)}${suffix}`;
}

function expectedGithubLogin(repo) {
  const owner = repo.projectPath.split("/")[0]?.toLowerCase() || "";
  if (owner === "terraroot3") return "TerraRoot3";
  if (owner === "hanbaokun" || owner === "pagepop") return "hanbaokun";
  return "";
}

function checkAuth(repo) {
  if (repo.provider === "github") {
    runCommand(GH_COMMAND, ["auth", "status", "--active", "--hostname", repo.host], {
      cwd: repo.repoRoot,
      timeout: 20_000
    });
    const login = runCommand(GH_COMMAND, ["api", "--hostname", repo.host, "user", "--jq", ".login"], {
      cwd: repo.repoRoot,
      timeout: 20_000
    }).stdout.trim();
    if (!login) throw new Error(`无法确认 ${repo.host} 的当前 GitHub 账号。`);
    const expected = expectedGithubLogin(repo);
    if (expected && login.toLowerCase() !== expected.toLowerCase()) {
      throw new Error(`GitHub 账号不匹配：仓库 Owner 要求 ${expected}，当前账号为 ${login}。请在插件外切换账号后重试。`);
    }
    return login;
  } else {
    runCommand(GLAB_COMMAND, ["auth", "status", "--hostname", repo.host], {
      cwd: repo.repoRoot,
      timeout: 20_000
    });
    const profile = parseJson(runCommand(GLAB_COMMAND, ["api", "--hostname", repo.host, "/user"], {
      cwd: repo.repoRoot,
      timeout: 20_000
    }).stdout, "GitLab 当前账号");
    const login = String(profile?.username || "").trim();
    if (!login) throw new Error(`无法确认 ${repo.host} 的当前 GitLab 账号。`);
    return login;
  }
}

function validateStringMap(value, label, keyPattern) {
  if (value === undefined) return {};
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} 必须是字符串键值对象。`);
  const entries = Object.entries(value);
  if (entries.length > 50) throw new Error(`${label} 数量不能超过 50。`);
  const result = {};
  for (const [key, item] of entries) {
    if (!keyPattern.test(key)) throw new Error(`${label}名称 ${key} 不合法。`);
    if (typeof item !== "string") throw new Error(`${label} ${key} 的值必须是字符串。`);
    if (item.length > 20_000) throw new Error(`${label} ${key} 的值过长。`);
    result[key] = item;
  }
  return result;
}

function isoDate(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toISOString();
}

function durationSeconds(start, end) {
  if (!start) return null;
  const startMs = new Date(start).getTime();
  const endMs = end ? new Date(end).getTime() : Date.now();
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) return null;
  return Math.max(0, Math.round((endMs - startMs) / 1000));
}

function normalizeGithubStatus(status, conclusion) {
  const rawStatus = String(status || "").toLowerCase();
  const rawConclusion = String(conclusion || "").toLowerCase();
  if (rawStatus !== "completed") {
    if (["in_progress"].includes(rawStatus)) return "running";
    return "queued";
  }
  if (rawConclusion === "success") return "success";
  if (["cancelled", "canceled"].includes(rawConclusion)) return "cancelled";
  if (["skipped", "neutral"].includes(rawConclusion)) return "skipped";
  if (["failure", "timed_out", "startup_failure", "action_required"].includes(rawConclusion)) return "failed";
  return "unknown";
}

function normalizeGitlabStatus(status) {
  const value = String(status || "").toLowerCase();
  if (["created", "waiting_for_resource", "preparing", "pending", "scheduled", "manual"].includes(value)) return "queued";
  if (value === "running") return "running";
  if (value === "success") return "success";
  if (value === "failed") return "failed";
  if (["canceled", "cancelled"].includes(value)) return "cancelled";
  if (value === "skipped") return "skipped";
  return "unknown";
}

function normalizeGithubJob(job) {
  const status = normalizeGithubStatus(job.status, job.conclusion);
  return {
    id: String(job.databaseId || job.id || ""),
    name: job.name || "未命名任务",
    stage: "",
    status,
    rawStatus: job.conclusion || job.status || "",
    startedAt: isoDate(job.startedAt),
    finishedAt: isoDate(job.completedAt),
    durationSeconds: durationSeconds(job.startedAt, job.completedAt),
    url: job.url || ""
  };
}

function normalizeGitlabJob(job) {
  return {
    id: String(job.id || ""),
    name: job.name || "未命名任务",
    stage: job.stage || "",
    status: normalizeGitlabStatus(job.status),
    rawStatus: job.status || "",
    startedAt: isoDate(job.started_at),
    finishedAt: isoDate(job.finished_at),
    durationSeconds: Number.isFinite(job.duration) ? Math.round(job.duration) : durationSeconds(job.started_at, job.finished_at),
    url: job.web_url || ""
  };
}

function progressFromJobs(jobs) {
  return {
    completed: jobs.filter((job) => TERMINAL_STATUSES.has(job.status)).length,
    total: jobs.length
  };
}

function monitorLocator(repo, input, extra = {}) {
  const locator = {
    repoPath: repo.repoRoot,
    provider: repo.provider,
    ref: input.ref || "",
    environment: input.environment || "",
    triggeredAt: input.triggeredAt || "",
    ...extra
  };
  return Object.fromEntries(Object.entries(locator).filter(([, value]) => value !== "" && value !== undefined && value !== null));
}

function exactMonitorIdentity(repo, id) {
  if (!id) return null;
  return {
    schemaVersion: STATE_SCHEMA_VERSION,
    provider: repo.provider,
    host: repo.host,
    repository: repo.projectPath,
    kind: repo.provider === "github" ? "github-run" : "gitlab-pipeline",
    id: String(id)
  };
}

function triggeredMonitorIdentity(repo, input = {}) {
  const triggeredAt = isoDate(input.triggeredAt);
  const workflow = String(input.workflow || (repo.provider === "gitlab" ? "gitlab-pipeline" : "")).trim();
  const ref = String(input.ref || "").trim();
  if (!triggeredAt || !workflow || !ref) return null;
  return {
    schemaVersion: STATE_SCHEMA_VERSION,
    provider: repo.provider,
    host: repo.host,
    repository: repo.projectPath,
    kind: "triggered-run",
    triggeredAt,
    workflow,
    ref
  };
}

function monitorIdentityKey(identity) {
  return JSON.stringify(identity);
}

function monitorStatePath(identity) {
  const digest = createHash("sha256").update(monitorIdentityKey(identity)).digest("hex");
  return join(STATE_DIR, `${digest}.json`);
}

function uniqueMonitorIdentities(identities) {
  const seen = new Set();
  return identities.filter((identity) => {
    if (!identity) return false;
    const key = monitorIdentityKey(identity);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function lookupMonitorIdentities(repo, input = {}) {
  const exactId = repo.provider === "github" ? input.runId : input.pipelineId;
  return uniqueMonitorIdentities([
    exactMonitorIdentity(repo, exactId),
    triggeredMonitorIdentity(repo, input)
  ]);
}

function snapshotMonitorIdentities(repo, input, snapshot) {
  const exactId = repo.provider === "github" ? snapshot.runId : snapshot.pipelineId;
  const triggerInput = {
    ...input,
    triggeredAt: input.triggeredAt || snapshot.monitor?.triggeredAt,
    workflow: input.workflow || snapshot.monitor?.workflow,
    ref: input.ref || snapshot.ref || snapshot.monitor?.ref
  };
  return uniqueMonitorIdentities([
    exactMonitorIdentity(repo, exactId),
    triggeredMonitorIdentity(repo, triggerInput)
  ]);
}

function ensureStateDirectory() {
  mkdirSync(STATE_DIR, { recursive: true, mode: 0o700 });
  chmodSync(STATE_DIR, 0o700);
}

function writeMonitorState(identity, snapshot) {
  ensureStateDirectory();
  const destination = monitorStatePath(identity);
  const temporary = join(STATE_DIR, `.${basename(destination)}.${process.pid}.${randomUUID()}.tmp`);
  const record = {
    schemaVersion: STATE_SCHEMA_VERSION,
    recordedAt: new Date().toISOString(),
    identity,
    snapshot
  };
  try {
    writeFileSync(temporary, `${JSON.stringify(record)}\n`, { encoding: "utf8", flag: "wx", mode: 0o600 });
    chmodSync(temporary, 0o600);
    renameSync(temporary, destination);
    chmodSync(destination, 0o600);
  } finally {
    try {
      unlinkSync(temporary);
    } catch {
      // The atomic rename already consumed the temporary file in the normal path.
    }
  }
}

function cleanupMonitorState() {
  let entries;
  try {
    entries = readdirSync(STATE_DIR, { withFileTypes: true })
      .filter((entry) => entry.isFile() && entry.name.endsWith(".json"))
      .map((entry) => {
        const path = join(STATE_DIR, entry.name);
        return { path, modifiedAt: statSync(path).mtimeMs };
      });
  } catch {
    return;
  }

  const cutoff = Date.now() - STATE_RETENTION_MILLISECONDS;
  const retained = [];
  for (const entry of entries) {
    if (entry.modifiedAt < cutoff) {
      try {
        unlinkSync(entry.path);
      } catch {
        // A concurrent server may already have removed this plugin-owned state file.
      }
    } else {
      retained.push(entry);
    }
  }
  retained.sort((left, right) => right.modifiedAt - left.modifiedAt);
  for (const entry of retained.slice(STATE_MAX_FILES)) {
    try {
      unlinkSync(entry.path);
    } catch {
      // A concurrent server may already have removed this plugin-owned state file.
    }
  }
}

function persistProviderSuccess(repo, input, snapshot) {
  if (snapshot?.status !== "success") return snapshot;
  const identities = snapshotMonitorIdentities(repo, input, snapshot);
  if (!identities.length) return snapshot;
  const persistedSnapshot = JSON.parse(JSON.stringify({
    ...snapshot,
    status: "success",
    errorSummary: ""
  }));
  try {
    for (const identity of identities) writeMonitorState(identity, persistedSnapshot);
    cleanupMonitorState();
  } catch (error) {
    process.stderr.write(`[${SERVER_NAME}] 无法持久化成功状态：${redactText(error?.message || error)}\n`);
  }
  return snapshot;
}

function readMonitorState(identity) {
  const path = monitorStatePath(identity);
  try {
    const metadata = statSync(path);
    if (!metadata.isFile() || metadata.size > STATE_MAX_FILE_BYTES || metadata.mtimeMs < Date.now() - STATE_RETENTION_MILLISECONDS) {
      return null;
    }
    const record = JSON.parse(readFileSync(path, "utf8"));
    if (record?.schemaVersion !== STATE_SCHEMA_VERSION) return null;
    if (monitorIdentityKey(record.identity) !== monitorIdentityKey(identity)) return null;
    if (record.snapshot?.status !== "success") return null;
    if (record.snapshot?.provider !== identity.provider) return null;
    if (record.snapshot?.host !== identity.host || record.snapshot?.repository !== identity.repository) return null;
    if (identity.kind === "github-run" && String(record.snapshot.runId || "") !== identity.id) return null;
    if (identity.kind === "gitlab-pipeline" && String(record.snapshot.pipelineId || "") !== identity.id) return null;
    return record;
  } catch {
    return null;
  }
}

function restoreProviderSuccess(repo, input) {
  for (const identity of lookupMonitorIdentities(repo, input)) {
    const record = readMonitorState(identity);
    if (!record) continue;
    const snapshot = record.snapshot;
    const monitor = {
      ...(snapshot.monitor || {}),
      repoPath: repo.repoRoot,
      provider: repo.provider
    };
    if (snapshot.runId) monitor.runId = snapshot.runId;
    if (snapshot.pipelineId) monitor.pipelineId = snapshot.pipelineId;
    return {
      ...snapshot,
      status: "success",
      rawStatus: "persisted_success",
      environment: input.environment || snapshot.environment || "",
      errorSummary: "",
      message: "已从本机持久记录恢复真实成功结果。",
      monitor
    };
  }
  return null;
}

function normalizeGithubRun(repo, run, input = {}) {
  const jobs = Array.isArray(run.jobs) ? run.jobs.map(normalizeGithubJob) : [];
  const status = normalizeGithubStatus(run.status, run.conclusion);
  const failedJobs = jobs.filter((job) => job.status === "failed").map((job) => job.name);
  const runId = String(run.databaseId || input.runId || "");
  const workflow = String(input.workflow || run.workflowDatabaseId || run.workflowName || run.name || "");
  const ref = run.headBranch || input.ref || "";
  return {
    kind: "cicd-monitor",
    provider: "github",
    providerLabel: "GitHub Actions",
    host: repo.host,
    repository: repo.projectPath,
    pipelineName: run.workflowName || run.name || input.pipelineName || "GitHub Actions",
    runNumber: run.number ? String(run.number) : "",
    runId,
    status,
    rawStatus: run.conclusion || run.status || "",
    environment: input.environment || "",
    ref,
    sha: run.headSha || "",
    url: run.url || "",
    createdAt: isoDate(run.createdAt),
    startedAt: isoDate(run.startedAt || run.createdAt),
    finishedAt: status === "running" || status === "queued" ? "" : isoDate(run.updatedAt),
    updatedAt: isoDate(run.updatedAt) || new Date().toISOString(),
    durationSeconds: durationSeconds(run.startedAt || run.createdAt, status === "running" || status === "queued" ? null : run.updatedAt),
    progress: progressFromJobs(jobs),
    jobs,
    errorSummary: failedJobs.length ? `失败任务：${failedJobs.join("、")}` : "",
    message: status === "queued" ? "等待 GitHub 分配运行资源" : "",
    monitor: monitorLocator(repo, input, { workflow, runId, ref })
  };
}

function normalizeGitlabPipeline(repo, pipeline, jobs, input = {}) {
  const normalizedJobs = Array.isArray(jobs) ? jobs.map(normalizeGitlabJob) : [];
  const status = normalizeGitlabStatus(pipeline.status);
  const failedJobs = normalizedJobs.filter((job) => job.status === "failed").map((job) => job.name);
  const pipelineId = String(pipeline.id || input.pipelineId || "");
  const ref = pipeline.ref || input.ref || "";
  return {
    kind: "cicd-monitor",
    provider: "gitlab",
    providerLabel: "GitLab CI/CD",
    host: repo.host,
    repository: repo.projectPath,
    pipelineName: input.pipelineName || "GitLab Pipeline",
    runNumber: pipeline.iid ? String(pipeline.iid) : pipelineId,
    pipelineId,
    status,
    rawStatus: pipeline.status || "",
    environment: input.environment || "",
    ref,
    sha: pipeline.sha || "",
    url: pipeline.web_url || "",
    createdAt: isoDate(pipeline.created_at),
    startedAt: isoDate(pipeline.started_at || pipeline.created_at),
    finishedAt: isoDate(pipeline.finished_at),
    updatedAt: isoDate(pipeline.updated_at) || new Date().toISOString(),
    durationSeconds: Number.isFinite(pipeline.duration) ? Math.round(pipeline.duration) : durationSeconds(pipeline.started_at || pipeline.created_at, pipeline.finished_at),
    progress: progressFromJobs(normalizedJobs),
    jobs: normalizedJobs,
    errorSummary: pipeline.failure_reason ? String(pipeline.failure_reason) : failedJobs.length ? `失败任务：${failedJobs.join("、")}` : "",
    message: status === "queued" ? "等待 GitLab 分配运行资源" : "",
    monitor: monitorLocator(repo, input, { projectPath: repo.projectPath, pipelineId, ref })
  };
}

function pendingGithubRun(repo, input) {
  return {
    kind: "cicd-monitor",
    provider: "github",
    providerLabel: "GitHub Actions",
    host: repo.host,
    repository: repo.projectPath,
    pipelineName: input.workflow || "GitHub Actions",
    runNumber: "",
    runId: "",
    status: "queued",
    rawStatus: "waiting_for_run_record",
    environment: input.environment || "",
    ref: input.ref || "",
    sha: "",
    url: `https://${repo.host}/${repo.projectPath}/actions`,
    createdAt: isoDate(input.triggeredAt),
    startedAt: "",
    finishedAt: "",
    updatedAt: new Date().toISOString(),
    durationSeconds: durationSeconds(input.triggeredAt),
    progress: { completed: 0, total: 0 },
    jobs: [],
    errorSummary: "",
    message: "触发请求已接受，等待 GitHub 建立运行记录",
    monitor: monitorLocator(repo, input, { workflow: input.workflow || "" })
  };
}

function listGithubTargets(repo) {
  const activeLogin = checkAuth(repo);
  const repoId = ghRepoId(repo);
  const metadata = parseJson(runCommand(GH_COMMAND, ["repo", "view", repoId, "--json", "defaultBranchRef,url,nameWithOwner"], {
    cwd: repo.repoRoot,
    timeout: 30_000
  }).stdout, "GitHub 仓库信息") || {};
  const workflows = parseJson(runCommand(GH_COMMAND, ["workflow", "list", "--repo", repoId, "--all", "--limit", "100", "--json", "id,name,path,state"], {
    cwd: repo.repoRoot,
    timeout: 30_000
  }).stdout, "GitHub workflow 列表");
  const defaultRef = metadata.defaultBranchRef?.name || repo.currentRef || "main";
  return {
    kind: "cicd-targets",
    repoPath: repo.repoRoot,
    repoName: repo.repoName,
    provider: "github",
    providerLabel: "GitHub Actions",
    host: repo.host,
    activeLogin,
    repository: repo.projectPath,
    webUrl: metadata.url || `https://${repo.host}/${repo.projectPath}`,
    defaultRef,
    currentRef: repo.currentRef,
    targets: (Array.isArray(workflows) ? workflows : []).map((workflow) => ({
      id: String(workflow.id),
      name: workflow.name,
      workflow: workflow.path || String(workflow.id),
      path: workflow.path || "",
      state: workflow.state || "unknown",
      defaultRef
    }))
  };
}

function listGitlabTargets(repo) {
  const activeLogin = checkAuth(repo);
  const project = parseJson(runCommand(GLAB_COMMAND, ["api", "--hostname", repo.host, gitlabEndpoint(repo)], {
    cwd: repo.repoRoot,
    timeout: 30_000
  }).stdout, "GitLab 项目信息") || {};
  const defaultRef = project.default_branch || repo.currentRef || "main";
  return {
    kind: "cicd-targets",
    repoPath: repo.repoRoot,
    repoName: repo.repoName,
    provider: "gitlab",
    providerLabel: "GitLab CI/CD",
    host: repo.host,
    activeLogin,
    repository: repo.projectPath,
    webUrl: project.web_url || `https://${repo.host}/${repo.projectPath}`,
    defaultRef,
    currentRef: repo.currentRef,
    targets: [{
      id: "gitlab-pipeline",
      name: "GitLab Pipeline",
      workflow: "gitlab-pipeline",
      path: ".gitlab-ci.yml",
      state: "active",
      defaultRef
    }]
  };
}

function listTargets(input) {
  const repo = discoverRepository(input.repoPath);
  return repo.provider === "github" ? listGithubTargets(repo) : listGitlabTargets(repo);
}

const GITHUB_RUN_FIELDS = "databaseId,workflowDatabaseId,workflowName,name,number,status,conclusion,createdAt,startedAt,updatedAt,url,headBranch,headSha,event";

function listGithubRuns(repo, input, { includeJobs = false } = {}) {
  const repoId = ghRepoId(repo);
  const args = ["run", "list", "--repo", repoId, "--all", "--limit", "30"];
  if (input.workflow) args.push("--workflow", String(input.workflow));
  if (input.ref) args.push("--branch", input.ref);
  args.push("--json", GITHUB_RUN_FIELDS);
  const runs = parseJson(runCommand(GH_COMMAND, args, { cwd: repo.repoRoot, timeout: 30_000 }).stdout, "GitHub run 列表");
  const threshold = input.triggeredAt ? new Date(input.triggeredAt).getTime() - 30_000 : 0;
  const candidates = (Array.isArray(runs) ? runs : []).filter((run) => {
    if (input.ref && run.headBranch !== input.ref) return false;
    if (input.triggeredAt && run.event && run.event !== "workflow_dispatch") return false;
    if (threshold && new Date(run.createdAt).getTime() < threshold) return false;
    return true;
  });
  if (!candidates.length) return null;
  const run = candidates.sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime())[0];
  if (!includeJobs) return run;
  return getGithubRun(repo, { ...input, runId: String(run.databaseId) });
}

function getGithubRun(repo, input) {
  if (!input.runId) return listGithubRuns(repo, input);
  const fields = `${GITHUB_RUN_FIELDS},jobs`;
  return parseJson(runCommand(GH_COMMAND, ["run", "view", String(input.runId), "--repo", ghRepoId(repo), "--json", fields], {
    cwd: repo.repoRoot,
    timeout: 30_000
  }).stdout, "GitHub run 状态");
}

function getGitlabJobs(repo, pipelineId) {
  const jobs = parseJson(runCommand(GLAB_COMMAND, ["api", "--hostname", repo.host, `${gitlabEndpoint(repo)}/pipelines/${pipelineId}/jobs?per_page=100`], {
    cwd: repo.repoRoot,
    timeout: 30_000
  }).stdout, "GitLab job 列表");
  return Array.isArray(jobs) ? jobs : [];
}

function getGitlabPipeline(repo, input) {
  let pipelineId = input.pipelineId;
  if (!pipelineId) {
    let endpoint = `${gitlabEndpoint(repo)}/pipelines?per_page=30`;
    if (input.ref) endpoint += `&ref=${encodeURIComponent(input.ref)}`;
    const pipelines = parseJson(runCommand(GLAB_COMMAND, ["api", "--hostname", repo.host, endpoint], {
      cwd: repo.repoRoot,
      timeout: 30_000
    }).stdout, "GitLab pipeline 列表");
    const threshold = input.triggeredAt ? new Date(input.triggeredAt).getTime() - 30_000 : 0;
    const match = (Array.isArray(pipelines) ? pipelines : [])
      .filter((pipeline) => !threshold || new Date(pipeline.created_at).getTime() >= threshold)
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())[0];
    if (!match) throw new Error("没有找到匹配的 GitLab pipeline。");
    pipelineId = String(match.id);
  }
  const pipeline = parseJson(runCommand(GLAB_COMMAND, ["api", "--hostname", repo.host, `${gitlabEndpoint(repo)}/pipelines/${pipelineId}`], {
    cwd: repo.repoRoot,
    timeout: 30_000
  }).stdout, "GitLab pipeline 状态");
  return { pipeline, jobs: getGitlabJobs(repo, pipelineId) };
}

function getRunStatus(input) {
  const repo = discoverRepository(input.repoPath, input.provider);
  const persisted = restoreProviderSuccess(repo, input);
  if (persisted) return persisted;
  checkAuth(repo);
  if (repo.provider === "github") {
    const run = input.runId ? getGithubRun(repo, input) : listGithubRuns(repo, input, { includeJobs: true });
    const snapshot = run ? normalizeGithubRun(repo, run, input) : pendingGithubRun(repo, input);
    return persistProviderSuccess(repo, input, snapshot);
  }
  const { pipeline, jobs } = getGitlabPipeline(repo, input);
  return persistProviderSuccess(repo, input, normalizeGitlabPipeline(repo, pipeline, jobs, input));
}

function triggerGithubRun(repo, input) {
  const workflow = requireText(input.workflow, "workflow");
  const ref = requireText(input.ref, "ref");
  const inputs = validateStringMap(input.inputs, "GitHub input", /^[A-Za-z_][A-Za-z0-9_-]*$/);
  const secretValues = Object.values(inputs);
  checkAuth(repo);
  const triggeredAt = new Date().toISOString();
  runCommand(GH_COMMAND, ["workflow", "run", workflow, "--repo", ghRepoId(repo), "--ref", ref, "--json"], {
    cwd: repo.repoRoot,
    input: JSON.stringify(inputs),
    timeout: 45_000,
    redactions: secretValues
  });
  const monitorInput = {
    repoPath: repo.repoRoot,
    provider: "github",
    workflow,
    ref,
    environment: input.environment || "",
    triggeredAt
  };
  const run = listGithubRuns(repo, monitorInput, { includeJobs: true });
  const snapshot = run ? normalizeGithubRun(repo, run, monitorInput) : pendingGithubRun(repo, monitorInput);
  return persistProviderSuccess(repo, monitorInput, snapshot);
}

function triggerGitlabRun(repo, input) {
  const ref = requireText(input.ref, "ref");
  const variables = validateStringMap(input.variables, "GitLab variable", /^[A-Za-z_][A-Za-z0-9_]*$/);
  const secretValues = Object.values(variables);
  checkAuth(repo);
  const triggeredAt = new Date().toISOString();
  const body = {
    ref,
    variables: Object.entries(variables).map(([key, value]) => ({ key, value, variable_type: "env_var" }))
  };
  const pipeline = parseJson(runCommand(GLAB_COMMAND, ["api", "--hostname", repo.host, "--method", "POST", "--input", "-", `${gitlabEndpoint(repo)}/pipeline`], {
    cwd: repo.repoRoot,
    input: JSON.stringify(body),
    timeout: 45_000,
    redactions: secretValues
  }).stdout, "GitLab pipeline 触发结果");
  const monitorInput = {
    repoPath: repo.repoRoot,
    provider: "gitlab",
    projectPath: repo.projectPath,
    pipelineId: String(pipeline.id),
    ref,
    environment: input.environment || "",
    triggeredAt
  };
  const jobs = getGitlabJobs(repo, monitorInput.pipelineId);
  return persistProviderSuccess(repo, monitorInput, normalizeGitlabPipeline(repo, pipeline, jobs, monitorInput));
}

function triggerRun(input) {
  if (input.confirmed !== true) throw new Error("触发流水线前必须确认完整目标并传入 confirmed=true。");
  requireText(input.environment, "environment");
  const repo = discoverRepository(input.repoPath, input.provider);
  return repo.provider === "github" ? triggerGithubRun(repo, input) : triggerGitlabRun(repo, input);
}

function openMonitor(input) {
  const repo = discoverRepository(input.repoPath, input.provider);
  const persisted = restoreProviderSuccess(repo, input);
  if (persisted) return persisted;
  checkAuth(repo);
  if (repo.provider === "github") {
    const run = input.runId ? getGithubRun(repo, input) : listGithubRuns(repo, input, { includeJobs: true });
    if (!run) throw new Error("没有找到匹配的 GitHub Actions run。");
    return persistProviderSuccess(repo, input, normalizeGithubRun(repo, run, input));
  }
  const { pipeline, jobs } = getGitlabPipeline(repo, input);
  return persistProviderSuccess(repo, input, normalizeGitlabPipeline(repo, pipeline, jobs, input));
}

function safeOrigin(value) {
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return "";
    return parsed.origin;
  } catch {
    return "";
  }
}

function resourceUriFor(status) {
  const origin = safeOrigin(status?.url);
  return origin ? `${TEMPLATE_BASE_URI}?redirectOrigin=${encodeURIComponent(origin)}` : TEMPLATE_BASE_URI;
}

function toolResult(payload, render = false) {
  const result = {
    structuredContent: payload,
    content: [{ type: "text", text: summarizePayload(payload) }]
  };
  if (render) {
    const resourceUri = resourceUriFor(payload);
    result._meta = {
      ui: { resourceUri },
      "openai/outputTemplate": resourceUri
    };
  }
  return result;
}

function summarizePayload(payload) {
  if (payload.kind === "cicd-targets") {
    return `${payload.providerLabel}: ${payload.repository}，发现 ${payload.targets.length} 个可用发布目标。`;
  }
  const runLabel = payload.runNumber ? ` #${payload.runNumber}` : "";
  return `${payload.providerLabel} ${payload.pipelineName}${runLabel}: ${payload.status}，${payload.ref || "未知 ref"}。`;
}

function resourceOrigins(uri) {
  const origins = new Set(["https://github.com", "https://gitlab.com"]);
  try {
    const query = uri.split("?")[1] || "";
    const requested = safeOrigin(new URLSearchParams(query).get("redirectOrigin") || "");
    if (requested) origins.add(requested);
  } catch {
    // Keep the fixed provider origins only.
  }
  return [...origins];
}

async function handleRequest(message) {
  const { id, method, params = {} } = message;
  if (id === undefined || id === null) return;
  try {
    if (method === "initialize") {
      rpcResult(id, {
        protocolVersion: params.protocolVersion || "2025-06-18",
        capabilities: { tools: {}, resources: {} },
        serverInfo: { name: SERVER_NAME, version: SERVER_VERSION },
        instructions: "Mount at most one CI/CD monitor card for the same run or pipeline in a task. trigger_cicd_run already returns that card; never follow it with open_cicd_monitor for the same run. Call open_cicd_monitor at most once when attaching to an existing run. Refresh only through the data-only get_cicd_run_status tool, normally from the mounted card itself."
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
        resources: [{ name: "CI/CD Pipeline Monitor", uri: TEMPLATE_BASE_URI, mimeType: "text/html;profile=mcp-app" }]
      });
      return;
    }
    if (method === "resources/templates/list") {
      rpcResult(id, {
        resourceTemplates: [{
          name: "CI/CD Pipeline Monitor with provider redirect",
          uriTemplate: `${TEMPLATE_BASE_URI}{?redirectOrigin}`,
          mimeType: "text/html;profile=mcp-app"
        }]
      });
      return;
    }
    if (method === "resources/read") {
      if (typeof params.uri !== "string" || !params.uri.startsWith(TEMPLATE_BASE_URI)) {
        rpcError(id, -32002, `Unknown resource: ${params.uri}`);
        return;
      }
      const redirectDomains = resourceOrigins(params.uri);
      rpcResult(id, {
        contents: [{
          uri: params.uri,
          mimeType: "text/html;profile=mcp-app",
          text: templateHtml,
          _meta: {
            ui: {
              prefersBorder: true,
              csp: { connectDomains: [], resourceDomains: [] }
            },
            "openai/widgetDescription": "Compact live GitHub Actions or GitLab pipeline status monitor.",
            "openai/widgetPrefersBorder": true,
            "openai/widgetCSP": {
              connect_domains: [],
              resource_domains: [],
              redirect_domains: redirectDomains
            }
          }
        }]
      });
      return;
    }
    if (method === "tools/call") {
      const toolName = params.name;
      const args = params.arguments || {};
      if (toolName === "list_cicd_targets") {
        rpcResult(id, toolResult(listTargets(args)));
        return;
      }
      if (toolName === "trigger_cicd_run") {
        rpcResult(id, toolResult(triggerRun(args), true));
        return;
      }
      if (toolName === "open_cicd_monitor") {
        rpcResult(id, toolResult(openMonitor(args), true));
        return;
      }
      if (toolName === "get_cicd_run_status") {
        rpcResult(id, toolResult(getRunStatus(args)));
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
    void handleRequest(JSON.parse(line));
  } catch (error) {
    rpcError(null, -32700, "Parse error", error instanceof Error ? error.message : String(error));
  }
});

process.on("uncaughtException", (error) => {
  process.stderr.write(`[${SERVER_NAME}] ${error.stack || error.message}\n`);
});
