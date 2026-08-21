#!/usr/bin/env node

import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, mkdirSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawn, spawnSync } from "node:child_process";
import readline from "node:readline";

const pluginRoot = resolve(import.meta.dirname, "..");
const serverPath = resolve(pluginRoot, "scripts/server.mjs");
const tempRoot = mkdtempSync(join(tmpdir(), "cicd-monitor-test-"));
const stateDir = join(tempRoot, "state");

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
const activeLogin = process.env.FAKE_GH_LOGIN || "fixture-user";
const configuredLogins = [...new Set(
  (process.env.FAKE_GH_ACCOUNTS || [activeLogin, "TerraRoot3", "hanbaokun"].join(","))
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean)
)];
const selectedLogin = process.env.GH_TOKEN?.startsWith("fixture-token-")
  ? process.env.GH_TOKEN.slice("fixture-token-".length)
  : activeLogin;
const repositoryArg = () => {
  const index = args.indexOf("--repo");
  if (index >= 0) return args[index + 1] || "";
  return args[0] === "repo" && args[1] === "view" ? args[2] || "" : "";
};
const requiredLogin = (repository) => {
  if (process.env.FAKE_GH_REPO_LOGIN) return process.env.FAKE_GH_REPO_LOGIN;
  const owner = String(repository).split("/")[0].toLowerCase();
  if (owner === "terraroot3") return "TerraRoot3";
  if (owner === "hanbaokun" || owner === "pagepop") return "hanbaokun";
  return "";
};
const requireRepositoryAccess = (repository) => {
  const expected = requiredLogin(repository);
  if (expected && selectedLogin.toLowerCase() !== expected.toLowerCase()) {
    process.stderr.write("repository access denied for " + selectedLogin);
    process.exit(9);
  }
};
const requireEphemeralCredential = () => {
  if (
    process.env.FAKE_REQUIRE_EPHEMERAL_GH_TOKEN === "1"
    && !process.env.GH_TOKEN?.startsWith("fixture-token-")
  ) {
    process.stderr.write("missing per-command GitHub credential");
    process.exit(11);
  }
};
if (args[0] === "auth" && args[1] === "status") {
  if (process.env.FAKE_AUTH_FAIL === "1") { process.stderr.write("not logged in"); process.exit(1); }
  if (args.includes("--json")) {
    const host = args[args.indexOf("--hostname") + 1] || "github.com";
    output({ hosts: { [host]: configuredLogins.map((login) => ({ login, active: login === activeLogin })) } });
  } else {
    output("active account fixture");
  }
} else if (args[0] === "auth" && args[1] === "token") {
  const login = args[args.indexOf("--user") + 1] || activeLogin;
  const configured = configuredLogins.some((item) => item.toLowerCase() === login.toLowerCase());
  if (!configured || process.env.FAKE_AUTH_FAIL === "1" || process.env.FAKE_GH_TOKEN_FAIL === login) {
    process.stderr.write("not logged in");
    process.exit(1);
  }
  output("fixture-token-" + login);
} else if (args[0] === "auth" && args[1] === "switch") {
  process.stderr.write("global account switching is forbidden in this fixture");
  process.exit(10);
} else if (args[0] === "api") {
  requireEphemeralCredential();
  output(selectedLogin);
} else if (args[0] === "repo" && args[1] === "view") {
  requireEphemeralCredential();
  const repository = repositoryArg();
  requireRepositoryAccess(repository);
  output({ defaultBranchRef: { name: "main" }, url: "https://github.com/" + repository, nameWithOwner: repository });
} else if (args[0] === "workflow" && args[1] === "list") {
  requireEphemeralCredential();
  requireRepositoryAccess(repositoryArg());
  output([
    { id: 11, name: "Release to environment with a deliberately long name", path: ".github/workflows/release.yml", state: "active" },
    { id: 12, name: "Checks", path: ".github/workflows/checks.yml", state: "active" }
  ]);
} else if (args[0] === "workflow" && args[1] === "run") {
  requireEphemeralCredential();
  requireRepositoryAccess(repositoryArg());
  let body = "";
  process.stdin.on("data", (chunk) => body += chunk);
  process.stdin.on("end", () => {
    const parsed = JSON.parse(body || "{}");
    if (parsed.environment !== "test") process.exit(2);
  });
} else if (args[0] === "run" && args[1] === "list") {
  requireEphemeralCredential();
  requireRepositoryAccess(repositoryArg());
  output([{
    databaseId: 100, workflowDatabaseId: 11, workflowName: "Release to environment with a deliberately long name",
    name: "Release", number: 44, status: "in_progress", conclusion: "", event: "workflow_dispatch",
    createdAt: new Date().toISOString(), startedAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
    url: "https://github.com/acme/widgets/actions/runs/100", headBranch: "main", headSha: "1234567890abcdef"
  }]);
} else if (args[0] === "run" && args[1] === "view") {
  requireEphemeralCredential();
  requireRepositoryAccess(repositoryArg());
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
  if (args.includes("/user")) {
    output({ username: "hanbaokun" });
  } else if (!endpoint) process.exit(4);
  else if (args.includes("POST") && endpoint.endsWith("/pipeline")) {
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
        CICD_PIPELINE_MONITOR_STATE_DIR: stateDir,
        FAKE_REQUIRE_EPHEMERAL_GH_TOKEN: "1",
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
  assert.match(initialized.result.instructions, /at most one CI\/CD monitor card/);
  assert.match(initialized.result.instructions, /never follow it with open_cicd_monitor/);
  const tools = await client.call("tools/list");
  assert.deepEqual(tools.result.tools.map((tool) => tool.name), [
    "list_cicd_targets", "trigger_cicd_run", "open_cicd_monitor", "get_cicd_run_status"
  ]);
  const toolsByName = Object.fromEntries(tools.result.tools.map((tool) => [tool.name, tool]));
  assert.match(toolsByName.trigger_cicd_run.description, /inspect provider state before any retry/);
  assert.match(toolsByName.open_cicd_monitor.description, /Call once per monitoring request/);
  assert.match(toolsByName.get_cicd_run_status.description, /without mounting a new card/);

  const ghTargets = await client.tool("list_cicd_targets", { repoPath: githubRepo });
  assert.equal(ghTargets.isError, undefined);
  assert.equal(ghTargets.structuredContent.provider, "github");
  assert.equal(ghTargets.structuredContent.activeLogin, "fixture-user");
  assert.equal(ghTargets.structuredContent.targets.length, 2);
  assert.equal(ghTargets.structuredContent.targets[0].workflow, ".github/workflows/release.yml");

  const ghTriggered = await client.tool("trigger_cicd_run", {
    repoPath: githubRepo,
    provider: "github",
    workflow: ".github/workflows/release.yml",
    ref: "main",
    environment: "测网",
    confirmed: true,
    inputs: { environment: "test" }
  });
  assert.equal(ghTriggered.structuredContent.status, "running");
  assert.equal(ghTriggered.structuredContent.runId, "100");
  assert.match(ghTriggered._meta.ui.resourceUri, /redirectOrigin=https%3A%2F%2Fgithub\.com/);

  for (const [runId, expected] of [["101", "success"], ["102", "failed"], ["103", "cancelled"], ["104", "skipped"]]) {
    const status = await client.tool("get_cicd_run_status", {
      repoPath: githubRepo,
      provider: "github",
      runId,
      ref: "main",
      ...(runId === "101" ? {
        workflow: ".github/workflows/release.yml",
        triggeredAt: "2026-08-09T00:00:00Z"
      } : {})
    });
    assert.equal(status.structuredContent.status, expected);
  }

  const glTargets = await client.tool("list_cicd_targets", { repoPath: gitlabRepo });
  assert.equal(glTargets.structuredContent.provider, "gitlab");
  assert.equal(glTargets.structuredContent.host, "gitlab.example.test");
  assert.equal(glTargets.structuredContent.activeLogin, "hanbaokun");
  assert.equal(glTargets.structuredContent.targets[0].id, "gitlab-pipeline");

  const glTriggered = await client.tool("trigger_cicd_run", {
    repoPath: gitlabRepo,
    provider: "gitlab",
    ref: "main",
    environment: "现网",
    confirmed: true,
    variables: { DEPLOY_ENV: "production" }
  });
  assert.equal(glTriggered.structuredContent.status, "queued");
  assert.equal(glTriggered.structuredContent.pipelineId, "200");
  assert.match(glTriggered._meta.ui.resourceUri, /gitlab\.example\.test/);

  for (const [pipelineId, expected] of [["200", "running"], ["201", "success"], ["202", "failed"], ["203", "cancelled"]]) {
    const status = await client.tool("get_cicd_run_status", {
      repoPath: gitlabRepo,
      provider: "gitlab",
      pipelineId,
      ref: "main",
      ...(pipelineId === "201" ? { triggeredAt: "2026-08-09T00:00:00Z" } : {})
    });
    assert.equal(status.structuredContent.status, expected);
  }
  const emptyJobs = await client.tool("get_cicd_run_status", { repoPath: gitlabRepo, provider: "gitlab", pipelineId: "204", ref: "main" });
  assert.equal(emptyJobs.structuredContent.jobs.length, 0);

  const stateFiles = readdirSync(stateDir).filter((name) => name.endsWith(".json"));
  assert.ok(stateFiles.length >= 4);
  assert.equal(statSync(stateDir).mode & 0o777, 0o700);
  for (const name of stateFiles) assert.equal(statSync(join(stateDir, name)).mode & 0o777, 0o600);

  const restartedClient = new RpcClient({ FAKE_AUTH_FAIL: "1" });
  try {
    const restoredGithub = await restartedClient.tool("get_cicd_run_status", {
      repoPath: githubRepo,
      provider: "github",
      runId: "101",
      ref: "main"
    });
    assert.equal(restoredGithub.isError, undefined);
    assert.equal(restoredGithub.structuredContent.status, "success");
    assert.equal(restoredGithub.structuredContent.rawStatus, "persisted_success");
    assert.match(restoredGithub.structuredContent.message, /本机持久记录/);

    const reopenedGithub = await restartedClient.tool("open_cicd_monitor", {
      repoPath: githubRepo,
      provider: "github",
      runId: "101",
      ref: "main"
    });
    assert.equal(reopenedGithub.structuredContent.rawStatus, "persisted_success");

    const restoredGithubAlias = await restartedClient.tool("open_cicd_monitor", {
      repoPath: githubRepo,
      provider: "github",
      workflow: ".github/workflows/release.yml",
      ref: "main",
      triggeredAt: "2026-08-09T00:00:00Z"
    });
    assert.equal(restoredGithubAlias.structuredContent.runId, "101");
    assert.equal(restoredGithubAlias.structuredContent.rawStatus, "persisted_success");

    const uncachedGithub = await restartedClient.tool("get_cicd_run_status", {
      repoPath: githubRepo,
      provider: "github",
      runId: "106",
      ref: "main"
    });
    assert.equal(uncachedGithub.isError, true);
    assert.match(uncachedGithub.content[0].text, /not logged in/);

    const restoredGitlab = await restartedClient.tool("get_cicd_run_status", {
      repoPath: gitlabRepo,
      provider: "gitlab",
      pipelineId: "201",
      ref: "main"
    });
    assert.equal(restoredGitlab.isError, undefined);
    assert.equal(restoredGitlab.structuredContent.status, "success");
    assert.equal(restoredGitlab.structuredContent.rawStatus, "persisted_success");

    const restoredGitlabAlias = await restartedClient.tool("open_cicd_monitor", {
      repoPath: gitlabRepo,
      provider: "gitlab",
      ref: "main",
      triggeredAt: "2026-08-09T00:00:00Z"
    });
    assert.equal(restoredGitlabAlias.structuredContent.pipelineId, "201");
    assert.equal(restoredGitlabAlias.structuredContent.rawStatus, "persisted_success");
  } finally {
    restartedClient.close();
  }

  const resourceUri = `${"ui://cicd-pipeline-monitor/pipeline-monitor.html"}?redirectOrigin=${encodeURIComponent("https://gitlab.example.test")}`;
  const resource = await client.call("resources/read", { uri: resourceUri });
  const redirects = resource.result.contents[0]._meta["openai/widgetCSP"].redirect_domains;
  assert.ok(redirects.includes("https://gitlab.example.test"));
  assert.ok(resource.result.contents[0].text.includes("window.openai.callTool"));
  assert.ok(resource.result.contents[0].text.includes('id="cicd-manual-success"'));
  assert.ok(resource.result.contents[0].text.includes("window.openai.setWidgetState"));
  assert.ok(resource.result.contents[0].text.includes("远端流水线状态未修改"));
  assert.ok(resource.result.contents[0].text.includes("refreshEpoch !== state.refreshEpoch || isManuallyCompleted()"));
  assert.ok(resource.result.contents[0].text.includes("providerCompletion"));
  assert.ok(resource.result.contents[0].text.includes("snapshot.monitor?.triggeredAt || snapshot.runId"));
  assert.ok(resource.result.contents[0].text.includes("failureWatchMilliseconds = 10 * 60 * 1000"));
  assert.ok(resource.result.contents[0].text.includes("已从当前卡片的临时状态恢复真实成功结果"));
  assert.ok(!resource.result.contents[0].text.includes("已从卡片持久状态恢复真实成功结果"));
  assert.ok(resource.result.contents[0].text.includes("void refresh({ force: true })"));
  assert.ok(resource.result.contents[0].text.includes('id="cicd-jobs-track"'));
  assert.ok(resource.result.contents[0].text.includes("function selectJobFocusIndex"));
  assert.ok(resource.result.contents[0].text.includes("requestAnimationFrame(positionJobTrack)"));
  assert.ok(resource.result.contents[0].text.includes("transition: transform 360ms"));
  assert.ok(!resource.result.contents[0].text.includes("jobs.slice(0, 4)"));

  const missingAuthorization = await client.tool("trigger_cicd_run", {
    repoPath: githubRepo,
    provider: "github",
    workflow: ".github/workflows/release.yml",
    ref: "main",
    environment: "测网",
    inputs: { environment: "test" }
  });
  assert.equal(missingAuthorization.isError, true);
  assert.match(missingAuthorization.content[0].text, /confirmed=true/);

  const shortSecretFailure = await client.tool("trigger_cicd_run", {
    repoPath: githubRepo,
    provider: "github",
    workflow: ".github/workflows/release.yml",
    ref: "main",
    environment: "测网",
    confirmed: true,
    inputs: { environment: "XY" }
  });
  assert.equal(shortSecretFailure.isError, true);
  assert.match(shortSecretFailure.content[0].text, /敏感输入与提供商原始输出已隐藏/);

  const mappedGithubRepo = createRepo("mapped-github-repo", "git@github.com:TerraRoot3/OpenSkills.git");
  const mappedAccountClient = new RpcClient({
    FAKE_GH_LOGIN: "hanbaokun",
    FAKE_GH_ACCOUNTS: "hanbaokun,TerraRoot3"
  });
  try {
    const mappedTargets = await mappedAccountClient.tool("list_cicd_targets", { repoPath: mappedGithubRepo });
    assert.equal(mappedTargets.isError, undefined);
    assert.equal(mappedTargets.structuredContent.activeLogin, "TerraRoot3");
  } finally {
    mappedAccountClient.close();
  }

  const fallbackGithubRepo = createRepo("fallback-github-repo", "git@github.com:unknown-org/widgets.git");
  const fallbackAccountClient = new RpcClient({
    FAKE_GH_LOGIN: "hanbaokun",
    FAKE_GH_ACCOUNTS: "hanbaokun,TerraRoot3",
    FAKE_GH_REPO_LOGIN: "TerraRoot3"
  });
  try {
    const fallbackTargets = await fallbackAccountClient.tool("list_cicd_targets", { repoPath: fallbackGithubRepo });
    assert.equal(fallbackTargets.isError, undefined);
    assert.equal(fallbackTargets.structuredContent.activeLogin, "TerraRoot3");
  } finally {
    fallbackAccountClient.close();
  }

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
