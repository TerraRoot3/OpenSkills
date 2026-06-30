#!/usr/bin/env node

import { createHash } from 'node:crypto';
import { createReadStream, createWriteStream, existsSync } from 'node:fs';
import fs from 'node:fs/promises';
import { tmpdir, homedir } from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { once } from 'node:events';

const OPENFIG_VERSION = '0.3.7';

function usage() {
  console.error(`Usage:
  dump-fig.mjs --fig <file.fig> --list-canvases [--out <dir>] [--skip-images] [--skip-blobs]
  dump-fig.mjs --fig <file.fig> --node <session:local> [--out <dir>] [--skip-images] [--target-images-only] [--skip-blobs] [--skip-full-tree] [--skip-all-index]

Options:
  --fig <path>          Local .fig file exported from Figma.
  --node <id>           Target node id, e.g. 22486:25570 or 22486-25570.
  --list-canvases       Only parse and list top-level canvases.
  --out <dir>           Output directory. Defaults to ~/.codex/figma-dumps/<fig>-<node>.
  --skip-images         Do not extract embedded images from the .fig zip.
  --target-images-only  Extract only images referenced by the target node subtree.
  --skip-blobs          Do not export raw Figma blobs from canvas.fig.
  --skip-full-tree      Do not write target-subtree.full.json.
  --skip-all-index      Do not write all-nodes-index.jsonl for the whole file.
  --help                Show this help.
`);
}

function parseArgs(argv) {
  const args = {
    fig: '',
    node: '',
    out: '',
    listCanvases: false,
    skipImages: false,
    targetImagesOnly: false,
    skipBlobs: false,
    skipFullTree: false,
    skipAllIndex: false,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--fig') args.fig = argv[++i] || '';
    else if (arg === '--node') args.node = normalizeNodeId(argv[++i] || '');
    else if (arg === '--out') args.out = argv[++i] || '';
    else if (arg === '--list-canvases') args.listCanvases = true;
    else if (arg === '--skip-images') args.skipImages = true;
    else if (arg === '--target-images-only') args.targetImagesOnly = true;
    else if (arg === '--skip-blobs') args.skipBlobs = true;
    else if (arg === '--skip-full-tree') args.skipFullTree = true;
    else if (arg === '--skip-all-index') args.skipAllIndex = true;
    else if (arg === '--help' || arg === '-h') {
      usage();
      process.exit(0);
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  if (!args.fig) throw new Error('Missing required --fig <file.fig>');
  if (!args.listCanvases && !args.node) {
    throw new Error('Missing --node <id>. Use --list-canvases to inspect canvas ids first.');
  }

  return args;
}

function normalizeNodeId(input) {
  return String(input).trim().replace(/^node-id=/, '').replace(/-/g, ':');
}

function codexHome() {
  return process.env.CODEX_HOME || path.join(homedir(), '.codex');
}

function slug(value) {
  return String(value)
    .normalize('NFKD')
    .replace(/[^\w.-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 120) || 'fig';
}

function defaultOutDir(figPath, nodeId, listOnly) {
  const figName = slug(path.basename(figPath, path.extname(figPath)));
  const target = listOnly ? 'canvases' : slug(nodeId.replace(/:/g, '-'));
  return path.join(codexHome(), 'figma-dumps', `${figName}-${target}`);
}

function run(command, args, options = {}) {
  return execFileSync(command, args, {
    stdio: options.stdio || 'pipe',
    encoding: options.encoding || 'utf8',
    ...options,
  });
}

function ensureCommand(command) {
  try {
    run(command, ['-v']);
  } catch {
    throw new Error(`Required command is missing or not executable: ${command}`);
  }
}

async function ensureOpenfigCore() {
  const cacheDir = path.join(codexHome(), 'cache', 'figma-fig-dump', `openfig-core-${OPENFIG_VERSION}`);
  const packageJson = path.join(cacheDir, 'node_modules', 'openfig-core', 'package.json');

  if (!existsSync(packageJson)) {
    await fs.mkdir(cacheDir, { recursive: true });
    console.error(`[fig-dump] installing openfig-core@${OPENFIG_VERSION} into ${cacheDir}`);
    execFileSync('npm', ['install', '--silent', '--prefix', cacheDir, `openfig-core@${OPENFIG_VERSION}`], {
      stdio: 'inherit',
    });
  }

  const requireFromCache = createRequire(path.join(cacheDir, 'require.cjs'));
  return requireFromCache('openfig-core');
}

async function hashFile(filePath) {
  const hash = createHash('sha256');
  const stream = createReadStream(filePath);
  stream.on('data', chunk => hash.update(chunk));
  await once(stream, 'end');
  return hash.digest('hex');
}

async function safeUnzip(figPath, entries, destDir, required = false) {
  await fs.mkdir(destDir, { recursive: true });
  try {
    run('unzip', ['-q', '-o', figPath, ...entries, '-d', destDir]);
    return true;
  } catch (error) {
    if (required) throw error;
    return false;
  }
}

async function extractRaw(figPath, rawDir) {
  await fs.rm(rawDir, { recursive: true, force: true });
  await fs.mkdir(rawDir, { recursive: true });
  await safeUnzip(figPath, ['canvas.fig'], rawDir, true);
  await safeUnzip(figPath, ['meta.json'], rawDir, false);
  await safeUnzip(figPath, ['thumbnail.png'], rawDir, false);
}

function byteExt(buffer) {
  if (buffer.length >= 8 && buffer.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) {
    return { ext: 'png', mime: 'image/png' };
  }
  if (buffer.length >= 3 && buffer[0] === 0xff && buffer[1] === 0xd8 && buffer[2] === 0xff) {
    return { ext: 'jpg', mime: 'image/jpeg' };
  }
  if (buffer.length >= 6 && buffer.subarray(0, 3).toString('ascii') === 'GIF') {
    return { ext: 'gif', mime: 'image/gif' };
  }
  if (buffer.length >= 12 && buffer.subarray(0, 4).toString('ascii') === 'RIFF' && buffer.subarray(8, 12).toString('ascii') === 'WEBP') {
    return { ext: 'webp', mime: 'image/webp' };
  }
  if (buffer.length >= 12 && buffer.subarray(4, 12).toString('ascii').includes('ftypavif')) {
    return { ext: 'avif', mime: 'image/avif' };
  }
  const prefix = buffer.subarray(0, Math.min(buffer.length, 256)).toString('utf8').trimStart();
  if (prefix.startsWith('<svg') || prefix.startsWith('<?xml')) {
    return { ext: 'svg', mime: 'image/svg+xml' };
  }
  return { ext: 'bin', mime: 'application/octet-stream' };
}

async function walkFiles(rootDir) {
  const out = [];
  async function visit(dir) {
    const entries = await fs.readdir(dir, { withFileTypes: true });
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) await visit(full);
      else if (entry.isFile()) out.push(full);
    }
  }
  if (existsSync(rootDir)) await visit(rootDir);
  return out;
}

function listZipEntries(figPath, prefix) {
  try {
    return run('unzip', ['-Z1', figPath, `${prefix}/*`])
      .split('\n')
      .map(line => line.trim())
      .filter(Boolean);
  } catch {
    return [];
  }
}

async function extractImages(figPath, outDir, targetHashes = null) {
  const tmp = path.join(tmpdir(), `fig-images-${process.pid}-${Date.now()}`);
  const imagesDir = path.join(outDir, 'images');
  await fs.rm(imagesDir, { recursive: true, force: true });
  await fs.mkdir(imagesDir, { recursive: true });

  let imageEntries = ['images/*'];
  if (targetHashes && targetHashes.size > 0) {
    imageEntries = listZipEntries(figPath, 'images')
      .filter(entry => targetHashes.has(path.basename(entry).replace(/\.[^.]+$/, '')));
  }

  const extracted = imageEntries.length > 0
    ? await safeUnzip(figPath, imageEntries, tmp, false)
    : false;
  if (!extracted) {
    await fs.rm(tmp, { recursive: true, force: true });
    await writeJson(path.join(outDir, 'images-index.json'), []);
    return [];
  }

  const rawImagesDir = path.join(tmp, 'images');
  const files = await walkFiles(rawImagesDir);
  const index = [];

  for (const file of files) {
    const stat = await fs.stat(file);
    const handle = await fs.open(file, 'r');
    const probe = Buffer.alloc(Math.min(512, stat.size));
    await handle.read(probe, 0, probe.length, 0);
    await handle.close();

    const { ext, mime } = byteExt(probe);
    const hash = path.basename(file).replace(/\.[^.]+$/, '');
    const destName = `${hash}.${ext}`;
    const dest = path.join(imagesDir, destName);
    await fs.copyFile(file, dest);
    await fs.rm(file, { force: true });

    index.push({
      hash,
      ext,
      mime,
      bytes: stat.size,
      path: dest,
    });
  }

  index.sort((a, b) => a.hash.localeCompare(b.hash));
  await writeJson(path.join(outDir, 'images-index.json'), index);
  await fs.rm(tmp, { recursive: true, force: true });
  return index;
}

function formatGuid(guid) {
  if (!guid) return null;
  const sessionID = guid.sessionID ?? guid.sessionId;
  const localID = guid.localID ?? guid.localId;
  if (sessionID === undefined || localID === undefined) return null;
  return `${sessionID}:${localID}`;
}

function nodeId(node) {
  return node?.id || node?.nodeId || formatGuid(node?.guid);
}

function parentId(node) {
  return formatGuid(node?.parentIndex?.guid) || node?.parentId || null;
}

function childrenOf(parsed, id) {
  return parsed.childrenMap.get(id) || [];
}

function summarizeNode(parsed, node, parent = parentId(node), depth = undefined) {
  const id = nodeId(node);
  const summary = {
    id,
    type: node.type || node.nodeType || null,
    name: node.name || '',
    parentId: parent,
    childCount: childrenOf(parsed, id).length,
  };

  if (depth !== undefined) summary.depth = depth;
  if (node.visible !== undefined) summary.visible = node.visible;
  if (node.opacity !== undefined) summary.opacity = node.opacity;
  if (node.size) summary.size = node.size;
  if (node.transform) summary.transform = node.transform;
  return summary;
}

function getCanvases(parsed) {
  return childrenOf(parsed, '0:0')
    .filter(node => node.type === 'CANVAS')
    .map(node => ({
      id: nodeId(node),
      name: node.name || '',
      childCount: childrenOf(parsed, nodeId(node)).length,
    }));
}

function sanitizeValue(value) {
  if (typeof value === 'bigint') {
    return { __type: 'BigInt', value: value.toString() };
  }
  if (value instanceof Uint8Array) {
    const buffer = Buffer.from(value);
    const result = {
      __type: 'Uint8Array',
      length: value.length,
      hex: buffer.subarray(0, 64).toString('hex'),
    };
    if (value.length <= 128) result.base64 = buffer.toString('base64');
    return result;
  }
  if (value instanceof Map) {
    return Object.fromEntries(value.entries());
  }
  return value;
}

function stableJson(value, space = 2) {
  return JSON.stringify(value, (_key, item) => sanitizeValue(item), space);
}

async function writeJson(filePath, value) {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, `${stableJson(value)}\n`);
}

async function writeLine(stream, line) {
  if (!stream.write(`${line}\n`)) await once(stream, 'drain');
}

async function writeJsonl(filePath, rows) {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  const stream = createWriteStream(filePath);
  for await (const row of rows) {
    await writeLine(stream, stableJson(row, 0));
  }
  stream.end();
  await once(stream, 'finish');
}

async function* allNodeSummaries(parsed) {
  for (const node of parsed.nodes) {
    yield summarizeNode(parsed, node);
  }
}

function buildSubtree(parsed, id) {
  const node = parsed.nodeMap.get(id);
  if (!node) return null;
  const children = childrenOf(parsed, id).map(child => buildSubtree(parsed, nodeId(child)));
  return {
    ...node,
    children,
  };
}

async function* flatSubtreeRows(parsed, rootId) {
  function* visit(node, parent, depth) {
    yield summarizeNode(parsed, node, parent, depth);
    for (const child of childrenOf(parsed, nodeId(node))) {
      yield* visit(child, nodeId(node), depth + 1);
    }
  }
  yield* visit(parsed.nodeMap.get(rootId), parentId(parsed.nodeMap.get(rootId)), 0);
}

function hashToString(value) {
  if (value instanceof Uint8Array) return Buffer.from(value).toString('hex');
  if (Buffer.isBuffer(value)) return value.toString('hex');
  if (typeof value === 'string') return value;
  if (value && typeof value === 'object' && Array.isArray(value.data)) return Buffer.from(value.data).toString('hex');
  return null;
}

function pathLabel(parts) {
  return parts
    .map(part => (typeof part === 'number' ? `[${part}]` : String(part)))
    .join('.')
    .replace(/\.?\[(\d+)\]/g, '[$1]');
}

function collectImageRefs(node) {
  const refs = [];
  const currentNodeId = nodeId(node);
  const currentNodeName = node.name || '';
  const currentNodeType = node.type || node.nodeType || null;

  function visit(value, parts) {
    if (!value || typeof value !== 'object') return;

    if ('hash' in value) {
      const hash = hashToString(value.hash);
      const label = pathLabel(parts);
      if (hash && (label.toLowerCase().includes('image') || hash.length >= 16)) {
        refs.push({
          nodeId: currentNodeId,
          nodeName: currentNodeName,
          nodeType: currentNodeType,
          at: label,
          hash,
          name: value.name || null,
        });
      }
    }

    if (value instanceof Uint8Array) return;
    if (Array.isArray(value)) {
      value.forEach((item, index) => visit(item, [...parts, index]));
      return;
    }

    for (const [key, child] of Object.entries(value)) {
      if (key === 'guid' || key === 'parentIndex') continue;
      visit(child, [...parts, key]);
    }
  }

  visit(node, []);
  return refs;
}

function collectTargetData(parsed, rootId) {
  const texts = [];
  const imageRefs = [];
  const typeCounts = new Map();
  let nodeCount = 0;
  let maxDepth = 0;

  function visit(node, parent, depth) {
    const id = nodeId(node);
    const type = node.type || node.nodeType || null;
    nodeCount += 1;
    maxDepth = Math.max(maxDepth, depth);
    typeCounts.set(type || 'UNKNOWN', (typeCounts.get(type || 'UNKNOWN') || 0) + 1);

    if (type === 'TEXT') {
      texts.push({
        id,
        parentId: parent,
        depth,
        name: node.name || '',
        characters: node.characters || '',
        fontSize: node.fontSize ?? null,
        fontName: node.fontName ?? null,
        fillPaints: node.fillPaints ?? null,
        size: node.size ?? null,
        transform: node.transform ?? null,
      });
    }

    imageRefs.push(...collectImageRefs(node));

    for (const child of childrenOf(parsed, id)) {
      visit(child, id, depth + 1);
    }
  }

  visit(parsed.nodeMap.get(rootId), parentId(parsed.nodeMap.get(rootId)), 0);

  return {
    texts,
    imageRefs,
    nodeCount,
    maxDepth,
    typeCounts: [...typeCounts.entries()].sort((a, b) => b[1] - a[1]),
  };
}

async function exportBlobs(parsed, outDir) {
  const blobs = parsed.message?.blobs || [];
  const blobsDir = path.join(outDir, 'blobs');
  await fs.rm(blobsDir, { recursive: true, force: true });
  await fs.mkdir(blobsDir, { recursive: true });

  const index = [];
  let totalBytes = 0;

  for (let i = 0; i < blobs.length; i += 1) {
    const bytes = blobs[i]?.bytes instanceof Uint8Array ? blobs[i].bytes : blobs[i];
    if (!(bytes instanceof Uint8Array)) continue;

    const shard = String(Math.floor(i / 1000)).padStart(2, '0');
    const fileName = `${String(i).padStart(6, '0')}.bin`;
    const relPath = path.join('blobs', shard, fileName);
    const fullPath = path.join(outDir, relPath);
    await fs.mkdir(path.dirname(fullPath), { recursive: true });
    await fs.writeFile(fullPath, bytes);

    totalBytes += bytes.length;
    index.push({
      index: i,
      bytes: bytes.length,
      path: fullPath,
    });
  }

  await writeJson(path.join(outDir, 'blobs-index.json'), {
    count: index.length,
    totalBytes,
    items: index,
  });

  return { count: index.length, totalBytes };
}

async function readMeta(rawDir) {
  const metaPath = path.join(rawDir, 'meta.json');
  if (!existsSync(metaPath)) return null;
  try {
    return JSON.parse(await fs.readFile(metaPath, 'utf8'));
  } catch {
    return null;
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const figPath = path.resolve(args.fig);
  if (!existsSync(figPath)) throw new Error(`File not found: ${figPath}`);

  ensureCommand('unzip');
  const { parseFigBinary } = await ensureOpenfigCore();
  if (typeof parseFigBinary !== 'function') {
    throw new Error('openfig-core did not expose parseFigBinary');
  }

  const outDir = path.resolve(args.out || defaultOutDir(figPath, args.node, args.listCanvases));
  const rawDir = path.join(outDir, 'raw');
  await fs.rm(outDir, { recursive: true, force: true });
  await fs.mkdir(outDir, { recursive: true });

  console.error(`[fig-dump] extracting canvas.fig -> ${rawDir}`);
  await extractRaw(figPath, rawDir);

  const canvasPath = path.join(rawDir, 'canvas.fig');
  console.error('[fig-dump] parsing canvas.fig');
  const canvasBuffer = await fs.readFile(canvasPath);
  const parsed = parseFigBinary(canvasBuffer);

  if (!parsed?.nodeMap || !parsed?.childrenMap || !Array.isArray(parsed.nodes)) {
    throw new Error('Parsed .fig shape is unsupported: missing nodes/nodeMap/childrenMap');
  }

  const canvases = getCanvases(parsed);
  await writeJson(path.join(outDir, 'canvases.json'), canvases);

  const figStat = await fs.stat(figPath);
  const meta = await readMeta(rawDir);

  if (args.listCanvases) {
    const manifest = {
      source: figPath,
      sourceBytes: figStat.size,
      sourceSha256: await hashFile(figPath),
      fileName: meta?.name || path.basename(figPath, path.extname(figPath)),
      figHeader: parsed.header,
      totalNodes: parsed.nodes.length,
      totalCanvases: canvases.length,
      paths: {
        outDir,
        canvases: path.join(outDir, 'canvases.json'),
      },
    };
    await writeJson(path.join(outDir, 'manifest.json'), manifest);
    console.log(stableJson({ outDir, canvases }));
    return;
  }

  const target = parsed.nodeMap.get(args.node);
  if (!target) {
    const hint = canvases.map(canvas => `${canvas.id} ${canvas.name}`).join('\n');
    throw new Error(`Target node not found: ${args.node}\nKnown canvases:\n${hint}`);
  }

  if (!args.skipAllIndex) {
    console.error('[fig-dump] writing all node index');
    await writeJsonl(path.join(outDir, 'all-nodes-index.jsonl'), allNodeSummaries(parsed));
  }

  console.error('[fig-dump] collecting target data');
  const targetData = collectTargetData(parsed, args.node);

  console.error('[fig-dump] writing target flat index');
  await writeJsonl(path.join(outDir, 'target-subtree.flat.jsonl'), flatSubtreeRows(parsed, args.node));
  await writeJson(path.join(outDir, 'target-texts.json'), targetData.texts);
  await writeJson(path.join(outDir, 'target-image-refs.json'), targetData.imageRefs);

  if (!args.skipFullTree) {
    console.error('[fig-dump] writing target full tree');
    await writeJson(path.join(outDir, 'target-subtree.full.json'), buildSubtree(parsed, args.node));
  }

  let images = [];
  if (!args.skipImages) {
    const targetImageHashes = args.targetImagesOnly
      ? new Set(targetData.imageRefs.map(ref => ref.hash).filter(Boolean))
      : null;
    console.error(args.targetImagesOnly
      ? `[fig-dump] extracting target referenced images (${targetImageHashes.size} unique hashes)`
      : '[fig-dump] extracting embedded images');
    images = await extractImages(figPath, outDir, targetImageHashes);
  }

  let blobStats = null;
  if (!args.skipBlobs) {
    console.error('[fig-dump] exporting raw blobs');
    blobStats = await exportBlobs(parsed, outDir);
  }

  const manifest = {
    source: figPath,
    sourceBytes: figStat.size,
    sourceSha256: await hashFile(figPath),
    fileName: meta?.name || path.basename(figPath, path.extname(figPath)),
    targetId: args.node,
    targetName: target.name || '',
    targetType: target.type || null,
    figHeader: parsed.header,
    totalNodes: parsed.nodes.length,
    totalCanvases: canvases.length,
    targetNodes: targetData.nodeCount,
    targetMaxDepth: targetData.maxDepth,
    targetTypes: targetData.typeCounts,
    textNodes: targetData.texts.length,
    imageRefs: targetData.imageRefs.length,
    images: args.skipImages ? null : images.length,
    blobs: args.skipBlobs ? null : blobStats,
    paths: {
      outDir,
      rawCanvas: canvasPath,
      canvases: path.join(outDir, 'canvases.json'),
      allNodesIndex: args.skipAllIndex ? null : path.join(outDir, 'all-nodes-index.jsonl'),
      targetFullTree: args.skipFullTree ? null : path.join(outDir, 'target-subtree.full.json'),
      targetFlatJsonl: path.join(outDir, 'target-subtree.flat.jsonl'),
      targetTexts: path.join(outDir, 'target-texts.json'),
      targetImageRefs: path.join(outDir, 'target-image-refs.json'),
      imagesIndex: args.skipImages ? null : path.join(outDir, 'images-index.json'),
      blobsIndex: args.skipBlobs ? null : path.join(outDir, 'blobs-index.json'),
    },
  };

  await writeJson(path.join(outDir, 'manifest.json'), manifest);
  console.log(stableJson({
    outDir,
    target: {
      id: manifest.targetId,
      name: manifest.targetName,
      type: manifest.targetType,
    },
    counts: {
      totalNodes: manifest.totalNodes,
      targetNodes: manifest.targetNodes,
      textNodes: manifest.textNodes,
      imageRefs: manifest.imageRefs,
      images: manifest.images,
      blobs: manifest.blobs?.count ?? null,
    },
    paths: manifest.paths,
  }));
}

main().catch(error => {
  console.error(`[fig-dump] ${error.stack || error.message}`);
  process.exit(1);
});
