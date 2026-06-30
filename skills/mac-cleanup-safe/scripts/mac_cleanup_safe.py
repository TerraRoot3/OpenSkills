#!/usr/bin/env python3
"""Dry-run-first macOS cleanup helper for developer machines.

The script is intentionally conservative: it reports review items but only
cleans low-risk candidates by default. It uses only the Python standard library
and local shell tools.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


HOME = Path.home()
LOG_PATH = HOME / ".codex" / "mac-cleanup-safe-last.log"
DEFAULT_KEEP_IOS = "18.7.8"
OLD_TMP_SECONDS = 12 * 60 * 60
OLD_CODEX_ARCHIVE_DAYS = 60
DEFAULT_PROJECT_DEPTH = 5
DEFAULT_PROJECT_MAX_DIRS = 6000
DEFAULT_PROJECT_MAX_RESULTS = 30
PROJECT_ARTIFACTS = {
    "node_modules": "Node.js dependencies",
    "target": "Rust/Maven build output",
    ".build": "Swift build output",
    "build": "Gradle/Android build output",
    ".dart_tool": "Flutter/Dart tool state",
    ".terraform": "Terraform providers/modules",
    ".next": "Next.js build output/cache",
    ".nuxt": "Nuxt build output/cache",
    "dist": "Bundled output",
    "vendor": "PHP/Ruby dependencies",
}
PROJECT_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".cache",
    ".Trash",
    "Applications",
    "Desktop",
    "Documents",
    "Downloads",
    "Library",
    "Movies",
    "Music",
    "Pictures",
}


@dataclass
class ProcessIndex:
    args: str

    @classmethod
    def load(cls) -> "ProcessIndex":
        try:
            out = subprocess.check_output(["ps", "-axo", "args="], text=True, stderr=subprocess.DEVNULL)
        except Exception:
            out = ""
        return cls(out.lower())

    def any(self, needles: Iterable[str]) -> bool:
        return any(n.lower() in self.args for n in needles)


@dataclass
class Candidate:
    cid: str
    risk: str
    title: str
    size_kb: int = 0
    action: str = "none"  # none, delete_paths, delete_children, commands
    paths: list[Path] = field(default_factory=list)
    commands: list[list[str]] = field(default_factory=list)
    consequence: str = ""
    reason: str = ""
    blocked: str = ""
    cleanable: bool = True
    reclaimable: bool = True
    details: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.blocked:
            return "blocked"
        if not self.cleanable or self.action == "none":
            return "list-only"
        return "ready"


def run_output(cmd: list[str], timeout: int = 10) -> str:
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=timeout).strip()
    except Exception:
        return ""


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def du_path_kb(path: Path) -> int:
    if not path.exists() and not path.is_symlink():
        return 0
    try:
        out = subprocess.check_output(["du", "-sk", str(path)], text=True, stderr=subprocess.DEVNULL, timeout=60)
        return int(out.split()[0])
    except Exception:
        return 0


def du_paths_kb(paths: Iterable[Path]) -> int:
    return sum(du_path_kb(p) for p in paths)


def human_size(kb: int) -> str:
    if kb <= 0:
        return "0B"
    size = float(kb) * 1024.0
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0 or unit == "TB":
            if unit in {"B", "KB"}:
                return f"{size:.0f}{unit}"
            return f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{kb}KB"


def existing(paths: Iterable[Path]) -> list[Path]:
    return [p for p in paths if p.exists() or p.is_symlink()]


def child_paths(parent: Path) -> list[Path]:
    if not parent.exists() or not parent.is_dir():
        return []
    try:
        return list(parent.iterdir())
    except Exception:
        return []


def old_children(parent: Path, older_than_seconds: int) -> list[Path]:
    now = time.time()
    out: list[Path] = []
    for child in child_paths(parent):
        try:
            if now - child.stat().st_mtime >= older_than_seconds:
                out.append(child)
        except Exception:
            continue
    return out


def glob_existing(pattern: str) -> list[Path]:
    return [p for p in Path("/").glob(pattern[1:]) if p.exists() or p.is_symlink()] if pattern.startswith("/") else []


def go_env(key: str) -> Path | None:
    if not command_exists("go"):
        return None
    value = run_output(["go", "env", key])
    return Path(value).expanduser() if value else None


def pnpm_store_path() -> Path | None:
    if not command_exists("pnpm"):
        return None
    value = run_output(["pnpm", "store", "path"], timeout=15)
    return Path(value).expanduser() if value else None


def uv_cache_dir() -> Path | None:
    if not command_exists("uv"):
        return None
    value = run_output(["uv", "cache", "dir"], timeout=15)
    return Path(value).expanduser() if value else None


def is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def unique_paths(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            resolved = str(path.expanduser().resolve())
        except Exception:
            resolved = str(path.expanduser().absolute())
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(Path(resolved))
    return out


def project_scan_roots(args: argparse.Namespace) -> list[Path]:
    roots = [Path.cwd()]
    roots.extend(Path(p).expanduser() for p in args.project_root)
    for path in [HOME / "workspace"]:
        if path.exists():
            roots.append(path)
    candidates = [p for p in unique_paths(roots) if p.exists() and p.is_dir()]
    trimmed: list[Path] = []
    for path in sorted(candidates, key=lambda p: len(p.parts)):
        if any(is_inside(path, existing_root) for existing_root in trimmed):
            continue
        trimmed.append(path)
    return trimmed


def scan_project_artifacts(args: argparse.Namespace) -> Candidate | None:
    if args.no_project_scan:
        return None
    matches: list[tuple[Path, str]] = []
    visited = 0
    roots = project_scan_roots(args)
    for root in roots:
        for dirpath, dirnames, _filenames in os.walk(root, topdown=True, followlinks=False):
            visited += 1
            if visited > args.max_project_dirs or len(matches) >= args.max_project_results:
                break
            current = Path(dirpath)
            try:
                depth = len(current.relative_to(root).parts)
            except Exception:
                depth = 0
            keep_dirs: list[str] = []
            for name in dirnames:
                path = current / name
                if name in PROJECT_ARTIFACTS:
                    matches.append((path, PROJECT_ARTIFACTS[name]))
                    continue
                if name in PROJECT_SKIP_DIRS or name.startswith(".") and name not in {".build", ".dart_tool", ".terraform", ".next", ".nuxt"}:
                    continue
                if depth >= args.max_project_depth:
                    continue
                keep_dirs.append(name)
            dirnames[:] = keep_dirs
        if visited > args.max_project_dirs or len(matches) >= args.max_project_results:
            break

    if not matches:
        return None
    sized: list[tuple[int, Path, str]] = []
    for path, kind in matches:
        size = du_path_kb(path)
        if size > 0:
            sized.append((size, path, kind))
    sized.sort(reverse=True, key=lambda item: item[0])
    details = [f"{human_size(size):>8}  {path}  ({kind})" for size, path, kind in sized[: args.max_project_results]]
    total = sum(size for size, _path, _kind in sized)
    if not details:
        return None
    return Candidate(
        cid="project-artifacts",
        risk="REVIEW",
        title="Project-local generated artifacts",
        size_kb=total,
        action="none",
        consequence="These may be reinstallable or rebuildable, but deleting them can break or slow active projects.",
        reason="ClearDisk-style project artifact scan; repo-by-repo manual decision only",
        cleanable=False,
        details=details,
    )


def assert_cleanup_path(path: Path, keep_ios: str) -> None:
    path = path.expanduser()
    dangerous = {
        Path("/"),
        HOME,
        HOME / "Documents",
        HOME / "Desktop",
        HOME / "Downloads",
        HOME / "workspace",
        Path("/Users"),
        Path("/Applications"),
        Path("/Library"),
        Path("/System"),
    }
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path.absolute()
    if resolved in dangerous:
        raise RuntimeError(f"refusing broad cleanup path: {path}")
    if "iOS DeviceSupport" in str(path) and keep_ios and keep_ios in path.name:
        raise RuntimeError(f"refusing protected iOS DeviceSupport: {path}")


def remove_path(path: Path, keep_ios: str, log: list[str]) -> None:
    assert_cleanup_path(path, keep_ios)
    if not path.exists() and not path.is_symlink():
        return
    log.append(f"delete {path}")
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def cleanup_candidate(candidate: Candidate, keep_ios: str, execute: bool, log: list[str]) -> None:
    if candidate.action == "delete_paths":
        for path in candidate.paths:
            if execute:
                remove_path(path, keep_ios, log)
            else:
                log.append(f"would delete {path}")
    elif candidate.action == "delete_children":
        for parent in candidate.paths:
            for path in child_paths(parent):
                if execute:
                    remove_path(path, keep_ios, log)
                else:
                    log.append(f"would delete {path}")
    elif candidate.action == "commands":
        for cmd in candidate.commands:
            log.append(("run " if execute else "would run ") + " ".join(cmd))
            if execute:
                subprocess.run(cmd, check=False)


def active_summary(proc: ProcessIndex) -> dict[str, bool]:
    return {
        "chrome": proc.any(["google chrome", "/chrome.app/", "chrome helper"]),
        "lark": proc.any(["larkshell", "lark helper", "/lark.app/"]),
        "android_gradle": proc.any(["android studio", "gradle", "kotlin compile daemon", "gradledaemon"]),
        "xcode": proc.any(["/xcode.app/", "xcodebuild", "xcbbuildservice", "xcbuild"]),
        "go_tools": proc.any(["gopls", " go ", "/go test", "/go build", "/go run", " dlv "]),
        "simulator": proc.any(["simulator.app", "coresimulator", "simctl"]),
        "node_package_tools": proc.any([" pnpm ", " npm ", " yarn ", " npx "]),
        "python_tools": proc.any([" python ", "/python", " pip ", " poetry ", " conda ", " uv "]),
        "rust_tools": proc.any([" cargo ", " rustc ", " rust-analyzer "]),
        "java_build_tools": proc.any([" mvn ", "maven", " gradle", "java "]),
        "vscode": proc.any(["visual studio code", "/code.app/", "code helper"]),
        "jetbrains": proc.any(["/intellij idea.app/", "/webstorm.app/", "/android studio.app/", "jetbrains"]),
        "docker": proc.any(["docker desktop", "com.docker", "dockerd"]),
    }


def add_path_candidate(
    candidates: list[Candidate],
    cid: str,
    risk: str,
    title: str,
    paths: Iterable[Path],
    action: str,
    consequence: str,
    reason: str = "",
    blocked: str = "",
    cleanable: bool = True,
    details: list[str] | None = None,
) -> None:
    found = existing(paths)
    size = du_paths_kb(found)
    if size <= 0 and not details and not blocked:
        return
    candidates.append(
        Candidate(
            cid=cid,
            risk=risk,
            title=title,
            size_kb=size,
            action=action,
            paths=found,
            consequence=consequence,
            reason=reason,
            blocked=blocked,
            cleanable=cleanable,
            details=details or [],
        )
    )


def add_command_candidate(
    candidates: list[Candidate],
    cid: str,
    risk: str,
    title: str,
    size_path: Path | None,
    commands: list[list[str]],
    consequence: str,
    reason: str = "",
    blocked: str = "",
    cleanable: bool = True,
    reclaimable: bool = True,
    details: list[str] | None = None,
) -> None:
    size = du_path_kb(size_path) if size_path else 0
    if size <= 0 and not details and not blocked:
        return
    candidates.append(
        Candidate(
            cid=cid,
            risk=risk,
            title=title,
            size_kb=size,
            action="commands",
            paths=[size_path] if size_path else [],
            commands=commands,
            consequence=consequence,
            reason=reason,
            blocked=blocked,
            cleanable=cleanable,
            reclaimable=reclaimable,
            details=details or [],
        )
    )


def build_candidates(args: argparse.Namespace) -> tuple[list[Candidate], dict[str, bool], list[str]]:
    proc = ProcessIndex.load()
    active = active_summary(proc)
    candidates: list[Candidate] = []
    protected: list[str] = [
        str(HOME / "workspace") + " (never scanned or deleted)",
        str(HOME / "Downloads") + " (reported only)",
        "Codex live sessions and log databases (reported only)",
    ]

    add_path_candidate(
        candidates,
        "codex-generated-images",
        "SAFE",
        "Codex generated image outputs",
        [HOME / ".codex" / "generated_images"],
        "delete_children",
        "Generated image artifacts are removed; prompts and sessions are not touched.",
    )
    add_path_candidate(
        candidates,
        "codex-temp",
        "SAFE",
        "Codex temporary files",
        [HOME / ".codex" / ".tmp", HOME / ".codex" / "tmp"],
        "delete_children",
        "Temporary files are removed.",
    )
    add_path_candidate(
        candidates,
        "codex-figma-dumps",
        "SAFE",
        "Codex Figma dump cache",
        [HOME / ".codex" / "figma-dumps"],
        "delete_children",
        "Figma dumps are removed and can be regenerated.",
    )

    xcode_block = "Xcode/xcodebuild appears active" if active["xcode"] else ""
    add_path_candidate(
        candidates,
        "xcode-derived-data",
        "SAFE",
        "Xcode DerivedData",
        [HOME / "Library" / "Developer" / "Xcode" / "DerivedData"],
        "delete_children",
        "Xcode will rebuild indexes and intermediates.",
        blocked=xcode_block,
    )

    gocache = go_env("GOCACHE")
    if gocache and command_exists("go"):
        add_command_candidate(
            candidates,
            "go-build-cache",
            "SAFE",
            "Go build and test cache",
            gocache,
            [["go", "clean", "-cache", "-testcache"]],
            "Go rebuilds packages on the next build; module downloads are kept.",
            blocked="Go tooling appears active" if active["go_tools"] else "",
        )

    add_path_candidate(
        candidates,
        "homebrew-download-cache",
        "SAFE",
        "Homebrew download cache",
        [HOME / "Library" / "Caches" / "Homebrew"],
        "delete_children",
        "Homebrew may redownload formula bottles later.",
    )
    add_path_candidate(
        candidates,
        "gvm-archives",
        "SAFE",
        "GVM downloaded Go archives",
        [HOME / ".gvm" / "archive"],
        "delete_children",
        "Old GVM source/archive downloads are removed.",
    )
    add_command_candidate(
        candidates,
        "npm-cache",
        "SAFE",
        "npm package cache",
        HOME / ".npm" / "_cacache",
        [["npm", "cache", "clean", "--force"]] if command_exists("npm") else [],
        "npm may redownload packages later.",
        blocked="npm/npx appears active" if active["node_package_tools"] else "",
        cleanable=command_exists("npm"),
    )
    pnpm_store = pnpm_store_path()
    if pnpm_store and command_exists("pnpm"):
        add_command_candidate(
            candidates,
            "pnpm-store-prune",
            "SAFE",
            "pnpm unreferenced store entries",
            pnpm_store,
            [["pnpm", "store", "prune"]],
            "pnpm removes unreferenced packages; referenced packages stay. Displayed size is total store size, not guaranteed reclaimed space.",
            blocked="pnpm/npm/npx appears active" if active["node_package_tools"] else "",
            reclaimable=False,
        )
    uv_dir = uv_cache_dir()
    if uv_dir and command_exists("uv"):
        add_command_candidate(
            candidates,
            "uv-cache-prune",
            "SAFE",
            "uv unused cache entries",
            uv_dir,
            [["uv", "cache", "prune"]],
            "uv removes unused cached packages. Displayed size is total cache size, not guaranteed reclaimed space.",
            reclaimable=False,
        )

    node_block = "Node package tooling appears active" if active["node_package_tools"] else ""
    add_path_candidate(
        candidates,
        "yarn-cache",
        "REVIEW",
        "Yarn package cache",
        [HOME / "Library" / "Caches" / "Yarn", HOME / ".cache" / "yarn"],
        "delete_children",
        "Yarn may redownload packages later.",
        reason="package cache; confirm if offline installs matter",
        blocked=node_block,
    )
    add_path_candidate(
        candidates,
        "bun-cache",
        "REVIEW",
        "Bun install cache",
        [HOME / ".bun" / "install" / "cache"],
        "delete_children",
        "Bun may redownload packages later.",
        reason="package cache; confirm if offline installs matter",
        blocked=node_block,
    )
    add_path_candidate(
        candidates,
        "deno-cache",
        "REVIEW",
        "Deno cache",
        [HOME / "Library" / "Caches" / "deno", HOME / ".cache" / "deno"],
        "delete_children",
        "Deno may redownload dependencies later.",
        reason="package/runtime cache; confirm project impact",
        blocked=node_block,
    )
    add_path_candidate(
        candidates,
        "playwright-cache",
        "REVIEW",
        "Playwright browser cache",
        [HOME / "Library" / "Caches" / "ms-playwright"],
        "delete_children",
        "Playwright browsers are re-downloaded on demand.",
        reason="large browser downloads; confirm before clearing",
        blocked=node_block,
    )
    add_path_candidate(
        candidates,
        "puppeteer-cache",
        "REVIEW",
        "Puppeteer browser cache",
        [HOME / "Library" / "Caches" / "puppeteer"],
        "delete_children",
        "Puppeteer browsers are re-downloaded on demand.",
        reason="large browser downloads; confirm before clearing",
        blocked=node_block,
    )

    python_block = "Python package tooling appears active" if active["python_tools"] else ""
    add_path_candidate(
        candidates,
        "python-package-caches",
        "REVIEW",
        "Python package and tool caches",
        [
            HOME / "Library" / "Caches" / "pip",
            HOME / ".cache" / "pip",
            HOME / "Library" / "Caches" / "pypoetry",
            HOME / ".cache" / "pypoetry",
            HOME / ".cache" / "pytest",
            HOME / ".cache" / "mypy",
            HOME / ".cache" / "ruff",
        ],
        "delete_children",
        "Python tooling may redownload packages and rebuild metadata.",
        reason="developer cache; confirm no active Python install/build",
        blocked=python_block,
    )
    add_path_candidate(
        candidates,
        "conda-package-cache",
        "REVIEW",
        "Conda package cache",
        [HOME / "miniconda3" / "pkgs", HOME / "anaconda3" / "pkgs", HOME / "mambaforge" / "pkgs"],
        "delete_children",
        "Conda may redownload packages; existing environments should remain but offline rollback gets harder.",
        reason="package cache with larger environment impact",
        blocked=python_block,
    )

    add_path_candidate(
        candidates,
        "swiftpm-cache",
        "REVIEW",
        "Swift Package Manager cache",
        [HOME / "Library" / "Caches" / "org.swift.swiftpm", HOME / ".swiftpm" / "cache"],
        "delete_children",
        "SwiftPM may redownload packages and rebuild metadata.",
        reason="confirm no Xcode or Swift build is active",
        blocked=xcode_block,
    )
    add_path_candidate(
        candidates,
        "cocoapods-cache",
        "REVIEW",
        "CocoaPods cache",
        [HOME / "Library" / "Caches" / "CocoaPods"],
        "delete_children",
        "CocoaPods may redownload pods later.",
        reason="iOS dependency cache; confirm project impact",
        blocked=xcode_block,
    )

    add_path_candidate(
        candidates,
        "maven-cache",
        "REVIEW",
        "Maven local repository",
        [HOME / ".m2" / "repository"],
        "none",
        "Maven/Java dependencies would be redownloaded and some local-only artifacts may be lost.",
        reason="dependency repository, list only",
        blocked="Java/Maven/Gradle tooling appears active" if active["java_build_tools"] else "",
        cleanable=False,
    )
    add_path_candidate(
        candidates,
        "cargo-cache",
        "REVIEW",
        "Cargo registry and git cache",
        [HOME / ".cargo" / "registry" / "cache", HOME / ".cargo" / "git" / "db"],
        "delete_children",
        "Cargo may redownload crates and git dependencies.",
        reason="developer dependency cache; confirm no Rust build is active",
        blocked="Rust tooling appears active" if active["rust_tools"] else "",
    )

    npx_children = child_paths(HOME / ".npm" / "_npx")
    if npx_children:
        add_path_candidate(
            candidates,
            "old-npx-cache",
            "SAFE",
            "npx temporary package installs",
            npx_children,
            "delete_paths",
            "npx may reinstall packages on next use.",
            blocked="npm/npx appears active" if active["node_package_tools"] else "",
        )

    tmpdir = Path(os.environ.get("TMPDIR", "/tmp"))
    tmp_candidates: list[Path] = []
    for prefix in ["go-build", "XcodeDistPipeline.", "ResultBundle_", "Runner_"]:
        try:
            tmp_candidates.extend(p for p in tmpdir.iterdir() if p.name.startswith(prefix))
        except Exception:
            pass
    tmp_candidates = [p for p in tmp_candidates if p in old_children(tmpdir, OLD_TMP_SECONDS)]
    add_path_candidate(
        candidates,
        "old-build-temp",
        "SAFE",
        "Old temp build artifacts",
        tmp_candidates,
        "delete_paths",
        "Old temporary build outputs are removed.",
    )

    gomod = go_env("GOMODCACHE")
    if gomod and command_exists("go"):
        add_command_candidate(
            candidates,
            "go-mod-cache",
            "REVIEW",
            "Go module download cache",
            gomod,
            [["go", "clean", "-modcache"]],
            "All modules are redownloaded on demand; first rebuild can be slow/offline-unfriendly.",
            reason="large but useful for active Go development",
            blocked="Go tooling appears active" if active["go_tools"] else "",
        )

    gradle_active = active["android_gradle"]
    gradle_versions = list((HOME / ".gradle" / "caches").glob("*/transforms"))
    add_path_candidate(
        candidates,
        "gradle-transforms",
        "REVIEW",
        "Gradle transform caches",
        gradle_versions,
        "delete_paths",
        "Android/Gradle rebuilds transforms; first build can be slow.",
        reason="avoid while Android Studio or Gradle is running",
        blocked="Android Studio/Gradle appears active" if gradle_active else "",
    )
    add_path_candidate(
        candidates,
        "gradle-modules",
        "REVIEW",
        "Gradle module dependency cache",
        [HOME / ".gradle" / "caches" / "modules-2"],
        "delete_children",
        "Gradle redownloads dependencies; first build can be slow/offline-unfriendly.",
        reason="dependency cache, not a temp build output",
        blocked="Android Studio/Gradle appears active" if gradle_active else "",
    )
    add_path_candidate(
        candidates,
        "gradle-wrapper-dists",
        "REVIEW",
        "Gradle wrapper distributions",
        [HOME / ".gradle" / "wrapper" / "dists"],
        "delete_children",
        "Gradle redownloads wrapper distributions.",
        reason="can slow first Gradle run after cleanup",
        blocked="Android Studio/Gradle appears active" if gradle_active else "",
    )

    chrome_active = active["chrome"]
    chrome_base = HOME / "Library" / "Application Support" / "Google" / "Chrome"
    add_path_candidate(
        candidates,
        "chrome-cache",
        "REVIEW",
        "Chrome HTTP cache",
        [HOME / "Library" / "Caches" / "Google" / "Chrome", HOME / "Library" / "Caches" / "com.google.Chrome"],
        "delete_children",
        "Web cache is removed; sites redownload assets.",
        reason="close Chrome first",
        blocked="Chrome appears active" if chrome_active else "",
    )
    add_path_candidate(
        candidates,
        "chrome-on-device-models",
        "REVIEW",
        "Chrome on-device optimization models",
        [
            chrome_base / "OptGuideOnDeviceModel",
            chrome_base / "OptimizationGuidePredictionModels",
            chrome_base / "OnDeviceModel",
        ],
        "delete_children",
        "Chrome may redownload on-device model assets.",
        reason="application support data; close Chrome first",
        blocked="Chrome appears active" if chrome_active else "",
    )
    add_path_candidate(
        candidates,
        "chrome-service-worker",
        "REVIEW",
        "Chrome default profile service workers",
        [chrome_base / "Default" / "Service Worker"],
        "none",
        "May sign out or reset offline site state.",
        reason="site data, not plain cache",
        cleanable=False,
    )

    lark_active = active["lark"]
    lark_base = HOME / "Library" / "Application Support" / "LarkShell"
    add_path_candidate(
        candidates,
        "lark-cache",
        "REVIEW",
        "Lark cache",
        [HOME / "Library" / "Caches" / "LarkShell"],
        "delete_children",
        "Lark redownloads cached assets.",
        reason="close Lark first",
        blocked="Lark appears active" if lark_active else "",
    )
    add_path_candidate(
        candidates,
        "lark-aha-storage",
        "REVIEW",
        "Lark aha storage",
        [lark_base / "aha"],
        "none",
        "May affect Lark app data and offline state.",
        reason="application support data, manual review only",
        cleanable=False,
    )
    add_path_candidate(
        candidates,
        "lark-sdk-storage",
        "REVIEW",
        "Lark SDK storage",
        [lark_base / "sdk_storage"],
        "none",
        "May affect Lark app state.",
        reason="application support data, manual review only",
        cleanable=False,
    )

    add_path_candidate(
        candidates,
        "vscode-caches",
        "REVIEW",
        "VS Code caches",
        [
            HOME / "Library" / "Application Support" / "Code" / "Cache",
            HOME / "Library" / "Application Support" / "Code" / "CachedData",
            HOME / "Library" / "Application Support" / "Code" / "GPUCache",
            HOME / "Library" / "Application Support" / "Code" / "Service Worker" / "CacheStorage",
            HOME / "Library" / "Caches" / "com.microsoft.VSCode",
        ],
        "delete_children",
        "VS Code may redownload extension/webview assets and rebuild caches.",
        reason="close VS Code first",
        blocked="VS Code appears active" if active["vscode"] else "",
    )
    add_path_candidate(
        candidates,
        "jetbrains-caches",
        "REVIEW",
        "JetBrains IDE caches",
        [HOME / "Library" / "Caches" / "JetBrains"],
        "delete_children",
        "JetBrains IDEs rebuild indexes and caches.",
        reason="close JetBrains IDEs first",
        blocked="JetBrains IDE appears active" if active["jetbrains"] else "",
    )

    simulator_active = active["simulator"]
    add_path_candidate(
        candidates,
        "coresimulator-dyld-cache",
        "REVIEW",
        "CoreSimulator dyld cache",
        [Path("/Library/Developer/CoreSimulator/Caches/dyld")],
        "none",
        "May require admin rights and will be regenerated by Simulator/Xcode.",
        reason="system-level simulator cache; manual review only",
        blocked="Simulator/CoreSimulator appears active" if simulator_active else "",
        cleanable=False,
    )
    add_path_candidate(
        candidates,
        "coresimulator-volumes",
        "REVIEW",
        "CoreSimulator runtime volumes",
        [Path("/Library/Developer/CoreSimulator/Volumes")],
        "none",
        "These are installed simulator runtimes, not throwaway cache.",
        reason="remove only through Xcode/Simulator runtime management",
        blocked="Simulator/CoreSimulator appears active" if simulator_active else "",
        cleanable=False,
    )
    add_path_candidate(
        candidates,
        "coresimulator-cryptex",
        "REVIEW",
        "CoreSimulator cryptex data",
        [Path("/Library/Developer/CoreSimulator/Cryptex")],
        "none",
        "System-level simulator support data.",
        reason="manual review only",
        blocked="Simulator/CoreSimulator appears active" if simulator_active else "",
        cleanable=False,
    )

    device_support = HOME / "Library" / "Developer" / "Xcode" / "iOS DeviceSupport"
    protected_device_support = []
    other_device_support = []
    for item in child_paths(device_support):
        if args.keep_ios_device_support and args.keep_ios_device_support in item.name:
            protected_device_support.append(item)
        else:
            other_device_support.append(item)
    for item in protected_device_support:
        protected.append(f"{item} (kept by --keep-ios-device-support {args.keep_ios_device_support})")
    add_path_candidate(
        candidates,
        "xcode-device-support-other",
        "REVIEW",
        "Xcode iOS DeviceSupport except kept version",
        other_device_support,
        "delete_paths",
        "Removed device symbols/support can be re-downloaded when connecting devices.",
        reason=f"keeps any path containing {args.keep_ios_device_support!r}",
    )

    old_archive_paths = old_children(HOME / ".codex" / "archived_sessions", args.older_than_days * 24 * 60 * 60)
    add_path_candidate(
        candidates,
        "codex-archived-sessions-old",
        "REVIEW",
        f"Codex archived sessions older than {args.older_than_days} days",
        old_archive_paths,
        "delete_paths",
        "Old archived conversation files are removed; live sessions and logs are not touched.",
        reason="conversation history; confirm exact retention window",
    )
    add_path_candidate(
        candidates,
        "codex-sessions",
        "REVIEW",
        "Codex live session history",
        [HOME / ".codex" / "sessions"],
        "none",
        "Would remove active/local conversation history.",
        reason="live Codex state, list only",
        cleanable=False,
    )
    add_path_candidate(
        candidates,
        "codex-log-databases",
        "REVIEW",
        "Codex log databases",
        [HOME / ".codex" / "logs_2.sqlite", HOME / ".codex" / "logs.sqlite"],
        "none",
        "Would remove local Codex logs.",
        reason="database state, list only",
        cleanable=False,
    )

    downloads = HOME / "Downloads"
    large_downloads: list[str] = []
    total_downloads_kb = 0
    if downloads.exists():
        files = []
        for item in child_paths(downloads):
            if item.is_file():
                size = du_path_kb(item)
                if size >= 300 * 1024:
                    files.append((size, item))
        files.sort(reverse=True, key=lambda x: x[0])
        for size, item in files[:10]:
            total_downloads_kb += size
            large_downloads.append(f"{human_size(size):>8}  {item}")
    if large_downloads:
        candidates.append(
            Candidate(
                cid="downloads-large-files",
                risk="REVIEW",
                title="Large files in Downloads",
                size_kb=total_downloads_kb,
                action="none",
                consequence="Personal files are never deleted automatically.",
                reason="manual user decision required",
                cleanable=False,
                details=large_downloads,
            )
        )

    project_artifacts = scan_project_artifacts(args)
    if project_artifacts:
        candidates.append(project_artifacts)

    add_path_candidate(
        candidates,
        "docker-data",
        "RISKY",
        "Docker Desktop data",
        [HOME / "Library" / "Containers" / "com.docker.docker", HOME / ".docker"],
        "none",
        "May remove containers, images, volumes, credentials, or Docker Desktop state.",
        reason="never clean automatically; use Docker tools and explicit manual review",
        blocked="Docker appears active" if active["docker"] else "",
        cleanable=False,
    )
    add_path_candidate(
        candidates,
        "local-ai-models",
        "RISKY",
        "Local AI model caches",
        [
            HOME / ".ollama" / "models",
            HOME / ".cache" / "huggingface",
            HOME / ".cache" / "torch",
            HOME / ".cache" / "modelscope",
            HOME / ".lmstudio" / "models",
        ],
        "none",
        "May remove large downloaded models that are expensive to restore.",
        reason="model assets, list only",
        cleanable=False,
    )
    add_path_candidate(
        candidates,
        "language-version-managers",
        "RISKY",
        "Language runtime manager installs",
        [
            HOME / ".nvm" / "versions",
            HOME / ".pyenv" / "versions",
            HOME / ".local" / "share" / "mise" / "installs",
            HOME / ".asdf" / "installs",
        ],
        "none",
        "May remove installed Node/Python/other runtimes used by projects.",
        reason="installed runtimes, not cache",
        cleanable=False,
    )
    add_path_candidate(
        candidates,
        "aws-auth-caches",
        "REVIEW",
        "AWS CLI SSO/cache files",
        [HOME / ".aws" / "sso" / "cache", HOME / ".aws" / "cli" / "cache"],
        "none",
        "May force AWS re-authentication and disrupt CLI sessions.",
        reason="auth/session cache, list only",
        cleanable=False,
    )

    return candidates, active, protected


def print_report(candidates: list[Candidate], active: dict[str, bool], protected: list[str]) -> None:
    print("Mac Cleanup Safe scan")
    print()
    print("Active process guards:")
    for key, is_active in active.items():
        print(f"  {key:20} {'active' if is_active else 'inactive'}")
    print()

    for risk in ["SAFE", "REVIEW", "RISKY"]:
        rows = [c for c in candidates if c.risk == risk and (c.size_kb > 0 or c.details or c.blocked)]
        print(f"{risk} candidates:")
        if not rows:
            print("  none")
            print()
            continue
        for c in sorted(rows, key=lambda item: item.size_kb, reverse=True):
            print(f"  {c.cid:30} {human_size(c.size_kb):>9}  {c.status:9}  {c.title}")
            if c.reason:
                print(f"      why: {c.reason}")
            if c.blocked:
                print(f"      blocked: {c.blocked}")
            if c.consequence:
                print(f"      effect: {c.consequence}")
            for detail in c.details[:10]:
                print(f"      {detail}")
        print()

    safe_ready = sum(c.size_kb for c in candidates if c.risk == "SAFE" and c.status == "ready" and c.reclaimable)
    review_ready = sum(c.size_kb for c in candidates if c.risk == "REVIEW" and c.status == "ready" and c.reclaimable)
    risky_total = sum(c.size_kb for c in candidates if c.risk == "RISKY")
    print(f"Estimated default safe cleanup: {human_size(safe_ready)}")
    print(f"Review-only cleanable after explicit include: {human_size(review_ready)}")
    print(f"Risky/list-only footprint: {human_size(risky_total)}")
    print()
    print("Protected:")
    for item in protected:
        print(f"  {item}")


def select_targets(candidates: list[Candidate], args: argparse.Namespace) -> list[Candidate]:
    includes = set(args.include or [])
    known = {c.cid for c in candidates}
    missing = sorted(includes - known)
    for cid in missing:
        print(f"warning: include id not found: {cid}", file=sys.stderr)

    targets: list[Candidate] = []
    for c in candidates:
        selected = False
        if c.risk == "SAFE" and args.scope in {"safe", "all"}:
            selected = True
        if c.risk == "REVIEW" and c.cid in includes:
            selected = True
        if c.risk == "REVIEW" and args.allow_review_all and args.scope in {"review", "all"}:
            selected = True
        if not selected:
            continue
        if c.status != "ready":
            print(f"skip {c.cid}: {c.status} {c.blocked or c.reason}", file=sys.stderr)
            continue
        targets.append(c)
    return targets


def write_log(lines: list[str]) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"warning: could not write log {LOG_PATH}: {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run-first macOS cleanup helper")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--keep-ios-device-support", default=DEFAULT_KEEP_IOS)
        p.add_argument("--older-than-days", type=int, default=OLD_CODEX_ARCHIVE_DAYS)
        p.add_argument("--project-root", action="append", default=[], help="extra project root for artifact reporting")
        p.add_argument("--no-project-scan", action="store_true", help="skip project artifact reporting")
        p.add_argument("--max-project-depth", type=int, default=DEFAULT_PROJECT_DEPTH)
        p.add_argument("--max-project-dirs", type=int, default=DEFAULT_PROJECT_MAX_DIRS)
        p.add_argument("--max-project-results", type=int, default=DEFAULT_PROJECT_MAX_RESULTS)

    scan = sub.add_parser("scan", help="show cleanup candidates")
    add_common(scan)

    clean = sub.add_parser("clean", help="clean selected candidates; dry-run unless --execute is passed")
    add_common(clean)
    clean.add_argument("--scope", choices=["safe", "review", "all"], default="safe")
    clean.add_argument("--include", action="append", default=[], help="explicitly include a REVIEW candidate id")
    clean.add_argument("--allow-review-all", action="store_true", help="allow --scope review/all to clean all cleanable REVIEW candidates")
    clean.add_argument("--execute", action="store_true", help="actually delete or run cleanup commands")

    args = parser.parse_args()
    candidates, active, protected = build_candidates(args)

    if args.command == "scan":
        print_report(candidates, active, protected)
        return 0

    print_report(candidates, active, protected)
    targets = select_targets(candidates, args)
    print()
    if not targets:
        print("No ready cleanup targets selected.")
        return 0

    mode = "EXECUTE" if args.execute else "DRY RUN"
    print(f"{mode}: selected cleanup targets")
    for c in targets:
        print(f"  {c.cid:30} {human_size(c.size_kb):>9}  {c.title}")

    log = [f"mode={mode}", f"scope={args.scope}", f"keep_ios_device_support={args.keep_ios_device_support}"]
    for c in targets:
        cleanup_candidate(c, args.keep_ios_device_support, args.execute, log)
    write_log(log)
    print()
    if args.execute:
        print(f"Cleanup attempted. Log: {LOG_PATH}")
    else:
        print("Dry run only. Add --execute to apply selected cleanup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
