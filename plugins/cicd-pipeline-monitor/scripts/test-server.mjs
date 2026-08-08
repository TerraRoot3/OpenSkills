#!/usr/bin/env node

import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawn, spawnSync } from "node:child_process";
import readline from "node:readline";

const pluginRoot = resolve(import.meta.dirname, "..");
const serverPath = resolve(pluginRoot, "scripts/server.mjs");
const tempRoot = mkdtempSync(join(tmpdir(), "cicd-monitor-test-"));

function run(command, args, cwd) {
  const result = spawnSync(command, args, { cwd, encoding: "utf8" });
  if (result.status !== 0) throw new Error(result.stderr || result.stdout || `${command} failed`);
}

function createRepo(name, remote) {
  const path = join(tempRoot, name);
  mkdirSync(path);
  run("git", ["init", "-b", "main"], path);
  run("git", ["remote", "add", "origin", remote], path);
  return path;
}

const fakeGhPath = join(tempRoot, "fake-gh.mjs");
writeFileSync(fakeGhPath, `#!/usr/bin/env node
const args = process.argv.slice(2);
const output = (value) => process.stdout.write(typeof value === "string" ? value : JSON.stringify(value));
if (args[0] === "auth") {
  if (process.env.FAKE_AUTH_FAIL === "1") { process.stderr.write("not logged in"); process.exit(1); }
  output("active account fixture");
} else if (args[0] === "repo" && args[1] === "view") {
  output({ defaultBranchRef: { name: "main" }, url: "https://github.com/acme/widgets", nameWithOwner: "acme/widgets" });
} else if (args[0] === "workflow" && args[1] === "list") {
  output([
    { id: 11, name: "Release to environment with a deliberately long name", path: ".github/workflows/release.yml", state: "active" },
    { id: 12, name: "Checks", path: ".github/workflows/checks.yml", state: "active" }
  ]);
} else if (args[0] === "workflow" && args[1] === "run") {
  let body = "";
  process.stdin.on("data", (chunk) => body += chunk);
  process.stdin.on("end", () => {
    const parsed = JSON.parse(body || "{}");
    if (parsed.environment !== "test") process.exit(2);
  });
} else if (args[0] === "run" && args[1] === "list") {
  output([{
    databaseId: 100, workflowDatabaseId: 11, workflowName: "Release to environment with a deliberately long name",
    name: "Release", number: 44, status: "in_progress", conclusion: "", event: "workflow_dispatch",
    createdAt: new Date().toISOString(), startedAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
    url: "https://github.com/acme/widgets/actions/runs/100", headBranch: "main", headSha: "1234567890abcdef"
  }]);
} else if (args[0] === "run" && args[1] === "view") {
  const id = Number(args[2]);
  const map = {
    100: ["in_progress", ""],
    101: ["completed", "success"],
    102: ["completed", "failure"],
    103: ["completed", "cancelled"],
    104: ["completed", "skipped"]
  };
  const pair = map[id] || ["queued", ""];
  const jobConclusion = pair[1] === "failure" ? "failure" : pair[1] === "cancelled" ? "cancelled" : pair[0] === "completed" ? "success" : "";
  output({
    databaseId: id, workflowDatabaseId: 11, workflowName: "Release to environment with a deliberately long name",
    name: "Release", number: id, status: pair[0], conclusion: pair[1], event: "workflow_dispatch",
    createdAt: "2026-08-09T00:00:00Z", startedAt: "2026-08-09T00:00:01Z", updatedAt: "2026-08-09T00:01:01Z",
    url: "https://github.com/acme/widgets/actions/runs/" + id, headBranch: "main", headSha: "1234567890abcdef",
    jobs: id === 105 ? [] : [
      { databaseId: 1, name: "Build application", status: "completed", conclusion: "success", startedAt: "2026-08-09T00:00:01Z", completedAt: "2026-08-09T00:00:20Z", url: "https://github.com/acme/widgets/actions/jobs/1" },
      { databaseId: 2, name: "Deploy application to the selected environment", status: pair[0], conclusion: jobConclusion, startedAt: "2026-08-09T00:00:20Z", completedAt: pair[0] === "completed" ? "2026-08-09T00:01:01Z" : "", url: "https://github.com/acme/widgets/actions/jobs/2" }
    ]
  });
} else {
  process.stderr.write("unexpected gh args: " + args.join(" "));
  process.exit(3);
}
`);
chmodSync(fakeGhPath, 0o755);

const fakeGlabPath = join(tempRoot, "fake-glab.mjs");
writeFileSync(fakeGlabPath, `#!/usr/bin/env node
const args = process.argv.slice(2);
const output = (value) => process.stdout.write(typeof value === "string" ? value : JSON.stringify(value));
if (args[0] === "auth") {
  if (process.env.FAKE_AUTH_FAIL === "1") { process.stderr.write("not logged in"); process.exit(1); }
  output("authenticated fixture");
} else if (args[0] === "api") {
  const endpoint = args.find((item) => item.startsWith("projects/"));
  if (!endpoint) process.exit(4);
  if (args.includes("POST") && endpoint.endsWith("/pipeline")) {
    let body = "";
    process.stdin.on("data", (chunk) => body += chunk);
    process.stdin.on("end", () => {
      const parsed = JSON.parse(body || "{}");
      if (parsed.ref !== "main" || parsed.variables[0].key !== "DEPLOY_ENV") process.exit(5);
      output({ id: 200, iid: 20, status: "pending", ref: "main", sha: "abcdef1234567890", web_url: "https://gitlab.example.test/acme/widgets/-/pipelines/200", created_at: new Date().toISOString(), updated_at: new Date().toISOString() });
    });
  } else if (/\\/pipelines\\/\\d+\\/jobs/.test(endpoint)) {
    const id = Number(endpoint.match(/pipelines\\/(\\d+)/)[1]);
    if (id === 204) output([]);
    else output([
      { id: 1, name: "build", stage: "build", status: "success", duration: 12, started_at: "2026-08-09T00:00:01Z", finished_at: "2026-08-09T00:00:13Z", web_url: "https://gitlab.example.test/job/1" },
      { id: 2, name: "deploy to selected environment with a deliberately long job name", stage: "deploy", status: id === 202 ? "failed" : id === 203 ? "canceled" : id === 201 ? "success" : "running", duration: 30, started_at: "2026-08-09T00:00:13Z", finished_at: id >= 201 && id <= 203 ? "2026-08-09T00:00:43Z" : null, web_url: "https://gitlab.example.test/job/2" }
    ]);
  } else if (/\\/pipelines\\/\\d+$/.test(endpoint)) {
    const id = Number(endpoint.match(/pipelines\\/(\\d+)$/)[1]);
    const statuses = { 200: "running", 201: "success", 202: "failed", 203: "canceled", 204: "running" };
    output({ id, iid: id, status: statuses[id] || "pending", ref: "main", sha: "abcdef1234567890", web_url: "https://gitlab.example.test/acme/widgets/-/pipelines/" + id, created_at: "2026-08-09T00:00:00Z", started_at: "2026-08-09T00:00:01Z", finished_at: id >= 201 && id <= 203 ? "2026-08-09T00:01:01Z" : null, updated_at: "2026-08-09T00:01:01Z", duration: 60, failure_reason: id === 202 ? "script_failure" : null });
  } else if (endpoint.includes("/pipelines?")) {
    output([{ id: 200, status: "running", ref: "main", created_at: new Date().toISOString() }]);
  } else {
    output({ id: 9, path_with_namespace: "acme/widgets", default_branch: "main", web_url: "https://gitlab.example.test/acme/widgets" });
  }
} else {
  process.stderr.write("unexpected glab args: " + args.join(" "));
  process.exit(3);
}
`);
chmodSync(fakeGlabPath, 0o755);

class RpcClient {
  constructor(env = {}) {
    this.nextId = 1;
    this.pending = new Map();
    this.process = spawn(process.execPath, [serverPath], {
      cwd: pluginRoot,
      stdio: ["pipe", "pipe", "pipe"],
      env: {
        ...process.env,
        CICD_PIPELINE_MONITOR_GH_COMMAND: fakeGhPath,
        CICD_PIPELINE_MONITOR_GLAB_COMMAND: fakeGlabPath,
        ...env
      }
    });
    this.stderr = "";
    this.process.stderr.on("data", (chunk) => this.stderr += chunk);
    const lines = readline.createInterface({ input: this.process.stdout, crlfDelay: Infinity });
    lines.on("line", (line) => {
      const message = JSON.parse(line);
      const pending = this.pending.get(message.id);
      if (pending) {
        this.pending.delete(message.id);
        pending.resolve(message);
      }
    });
  }

  call(method, params = {}) {
    const id = this.nextId++;
    this.process.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`);
    return new Promise((resolvePromise, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`RPC timeout for ${method}: ${this.stderr}`));
      }, 5000);
      this.pending.set(id, {
        resolve: (message) => {
          clearTimeout(timer);
          resolvePromise(message);
        }
      });
    });
  }

  async tool(name, args) {
    const message = await this.call("tools/call", { name, arguments: args });
    assert.equal(message.error, undefined, JSON.stringify(message.error));
    return message.result;
  }

  close() {
    this.process.kill("SIGTERM");
  }
}

const githubRepo = createRepo("github-repo", "git@github.com:acme/widgets.git");
const gitlabRepo = createRepo("gitlab-repo", "git@gitlab.example.test:acme/widgets.git");
const client = new RpcClient();

try {
  const initialized = await client.call("initialize", { protocolVersion: "2025-06-18" });
  assert.equal(initialized.result.serverInfo.name, "cicd-pipeline-monitor");
  const tools = await client.call("tools/list");
  assert.deepEqual(tools.result.tools.map((tool) => tool.name), [
    "list_cicd_targets", "trigger_cicd_run", "open_cicd_monitor", "get_cicd_run_status"
  ]);

  const ghTargets = await client.tool("list_cicd_targets", { repoPath: githubRepo });
  assert.equal(ghTargets.isError, undefined);
  assert.equal(ghTargets.structuredContent.provider, "github");
  assert.equal(ghTargets.structuredContent.targets.length, 2);
  assert.equal(ghTargets.structuredContent.targets[0].workflow, ".github/workflows/release.yml");

  const ghTriggered = await client.tool("trigger_cicd_run", {
    repoPath: githubRepo,
    provider: "github",
    workflow: ".github/workflows/release.yml",
    ref: "main",
    environment: "测网",
    inputs: { environment: "test" }
  });
  assert.equal(ghTriggered.structuredContent.status, "running");
  assert.equal(ghTriggered.structuredContent.runId, "100");
  assert.match(ghTriggered._meta.ui.resourceUri, /redirectOrigin=https%3A%2F%2Fgithub\.com/);

  for (const [runId, expected] of [["101", "success"], ["102", "failed"], ["103", "cancelled"], ["104", "skipped"]]) {
    const status = await client.tool("get_cicd_run_status", { repoPath: githubRepo, provider: "github", runId, ref: "main" });
    assert.equal(status.structuredContent.status, expected);
  }

  const glTargets = await client.tool("list_cicd_targets", { repoPath: gitlabRepo });
  assert.equal(glTargets.structuredContent.provider, "gitlab");
  assert.equal(glTargets.structuredContent.host, "gitlab.example.test");
  assert.equal(glTargets.structuredContent.targets[0].id, "gitlab-pipeline");

  const glTriggered = await client.tool("trigger_cicd_run", {
    repoPath: gitlabRepo,
    provider: "gitlab",
    ref: "main",
    environment: "现网",
    variables: { DEPLOY_ENV: "production" }
  });
  assert.equal(glTriggered.structuredContent.status, "queued");
  assert.equal(glTriggered.structuredContent.pipelineId, "200");
  assert.match(glTriggered._meta.ui.resourceUri, /gitlab\.example\.test/);

  for (const [pipelineId, expected] of [["200", "running"], ["201", "success"], ["202", "failed"], ["203", "cancelled"]]) {
    const status = await client.tool("get_cicd_run_status", { repoPath: gitlabRepo, provider: "gitlab", pipelineId, ref: "main" });
    assert.equal(status.structuredContent.status, expected);
  }
  const emptyJobs = await client.tool("get_cicd_run_status", { repoPath: gitlabRepo, provider: "gitlab", pipelineId: "204", ref: "main" });
  assert.equal(emptyJobs.structuredContent.jobs.length, 0);

  const resourceUri = `${"ui://cicd-pipeline-monitor/pipeline-monitor.html"}?redirectOrigin=${encodeURIComponent("https://gitlab.example.test")}`;
  const resource = await client.call("resources/read", { uri: resourceUri });
  const redirects = resource.result.contents[0]._meta["openai/widgetCSP"].redirect_domains;
  assert.ok(redirects.includes("https://gitlab.example.test"));
  assert.ok(resource.result.contents[0].text.includes("window.openai.callTool"));

  const authClient = new RpcClient({ FAKE_AUTH_FAIL: "1" });
  try {
    const authFailure = await authClient.tool("list_cicd_targets", { repoPath: githubRepo });
    assert.equal(authFailure.isError, true);
    assert.match(authFailure.content[0].text, /not logged in/);
    assert.doesNotMatch(authFailure.content[0].text, /token=/i);
  } finally {
    authClient.close();
  }

  console.log("cicd-pipeline-monitor fixture tests passed");
} finally {
  client.close();
  rmSync(tempRoot, { recursive: true, force: true });
}
