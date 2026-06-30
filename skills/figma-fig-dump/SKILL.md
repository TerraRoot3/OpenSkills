---
name: figma-fig-dump
description: "Use when Codex needs complete design data from a local Figma .fig export file, including canvases, node subtrees, JSON/JSONL node trees, text nodes, image references, embedded images, or binary blobs, especially when Figma API is rate-limited or unavailable."
---

# Figma FIG Dump

Use this skill when the user provides a local `.fig` file or asks to inspect Figma design data without relying on official Figma API access.

## Workflow

1. Locate the `.fig` file on disk.
2. If the user gives a Figma node id like `22486-25570`, normalize it to `22486:25570`.
3. Run `scripts/dump-fig.mjs` from this skill.
4. Use the generated JSON/JSONL files and extracted images as the design source for UI implementation or review.

Do not use this skill for screenshots-only comparison. This skill is for extracting structured data and assets from a local `.fig` export.

## Commands

List canvases:

```bash
node scripts/dump-fig.mjs \
  --fig "/path/to/design.fig" \
  --list-canvases
```

Dump one node subtree:

```bash
node scripts/dump-fig.mjs \
  --fig "/path/to/design.fig" \
  --node 22486:25570
```

Use a custom output directory:

```bash
node scripts/dump-fig.mjs \
  --fig "/path/to/design.fig" \
  --node 22486:25570 \
  --out /tmp/figma-dump
```

For quick structural checks without large asset extraction:

```bash
node scripts/dump-fig.mjs \
  --fig "/path/to/design.fig" \
  --node 22486:25570 \
  --skip-images \
  --skip-blobs
```

## Output

The script writes these files when available:

- `manifest.json`: source file, target node, counts, and output paths.
- `canvases.json`: top-level canvas ids, names, and child counts.
- `all-nodes-index.jsonl`: lightweight index for every node in the file.
- `target-subtree.full.json`: complete target subtree with binary fields summarized.
- `target-subtree.flat.jsonl`: flattened target subtree for fast search.
- `target-texts.json`: text nodes with content, font, fills, size, and transform.
- `target-image-refs.json`: node-level references to image hashes.
- `images/` and `images-index.json`: embedded image files extracted from the `.fig` zip.
- `blobs/` and `blobs-index.json`: raw Figma binary blobs from `canvas.fig`.
- `raw/canvas.fig`, `raw/meta.json`, `raw/thumbnail.png`: original extracted internals.

## Notes

- `.fig` files can be large. Prefer `--skip-images` while exploring, then rerun without it when image assets are needed.
- The first run installs `openfig-core@0.3.7` into `~/.codex/cache/figma-fig-dump/`; later runs reuse it.
- The script uses local `unzip` and Node.js. If either is missing, install/fix the local environment before retrying.
- Treat extracted data as a local artifact. Do not commit large dumps or private design assets unless the user explicitly asks.
