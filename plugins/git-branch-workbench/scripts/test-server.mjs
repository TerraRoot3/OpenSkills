#!/usr/bin/env node

import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawn, spawnSync } from "node:child_process";
import readline from "node:readline";

const pluginRoot = resolve(import.meta.dirname, "..");
const serverPath = resolve(pluginRoot, "scripts/server.mjs");
const tempRoot = mkdtempSync(join(tmpdir(), "git-branch-workbench-test-"));
const stateDir = join(tempRoot, "state");
const repoPath = join(tempRoot, "fixture-repo");

function run(command, args, cwd) {
  const result = spawnSync(command, args, { cwd, encoding: "utf8" });
  if (result.status !== 0) throw new Error(result.stderr || result.stdout || `${command} failed`);
}

mkdirSync(repoPath);
const repoRoot = realpathSync(repoPath);
run("git", ["init", "-b", "main"], repoPath);
writeFileSync(join(repoPath, "README.md"), "# Fixture\n");
run("git", ["add", "README.md"], repoPath);
run("git", ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-m", "Initial commit"], repoPath);

class RpcClient {
  constructor() {
    this.nextId = 1;
    this.pending = new Map();
    this.stderr = "";
    this.process = spawn(process.execPath, [serverPath], {
      cwd: pluginRoot,
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, GIT_BRANCH_WORKBENCH_STATE_DIR: stateDir }
    });
    this.process.stderr.on("data", (chunk) => this.stderr += chunk);
    const lines = readline.createInterface({ input: this.process.stdout, crlfDelay: Infinity });
    lines.on("line", (line) => {
      const message = JSON.parse(line);
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      clearTimeout(pending.timer);
      pending.resolve(message);
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
      this.pending.set(id, { resolve: resolvePromise, timer });
    });
  }

  async tool(name, args = {}) {
    const message = await this.call("tools/call", { name, arguments: args });
    assert.equal(message.error, undefined, JSON.stringify(message.error));
    assert.equal(message.result?.isError, undefined, message.result?.content?.[0]?.text);
    return message.result;
  }

  close() {
    this.process.kill("SIGTERM");
  }
}

const client = new RpcClient();

try {
  const listed = await client.call("tools/list");
  const homeTool = listed.result.tools.find((tool) => tool.name === "open_git_branch_workbench_home");
  assert.deepEqual(homeTool._meta.ui.visibility, ["app"]);
  assert.deepEqual(homeTool._meta["openai/ui"].entrypoints, [{ type: "global" }, { type: "thread" }]);

  const firstTabOpen = await client.tool("open_git_branch_workbench_home");
  assert.equal(firstTabOpen.structuredContent.view, "home");
  assert.equal(firstTabOpen.structuredContent.launchSource, "tab");

  const messageOpen = await client.tool("open_git_branch_workbench", { repoPath, limit: 20 });
  assert.equal(messageOpen.structuredContent.launchSource, "message");
  assert.equal(messageOpen.structuredContent.repoRoot, repoRoot);
  assert.ok(Array.isArray(messageOpen.structuredContent.commits));

  const secondTabOpen = await client.tool("open_git_branch_workbench_home");
  assert.equal(secondTabOpen.structuredContent.view, "home");
  assert.equal(secondTabOpen.structuredContent.launchSource, "tab");
  assert.equal(secondTabOpen.structuredContent.recentRepoPath, repoRoot);
  assert.ok(secondTabOpen.structuredContent.repositories.some((repository) => repository.path === repoRoot));

  process.stdout.write("Git Branch Workbench server tests passed.\n");
} finally {
  client.close();
  rmSync(tempRoot, { recursive: true, force: true });
}
