#!/usr/bin/env python3
"""
run_task.py — run a single SWE-bench task with opencode (vanilla or darwin).

Usage:
  python3 eval/harness/run_task.py \
    --instance-id django__django-11019 \
    --model opencode/hy3-free \
    --workdir ./eval/workdir \
    --output ./eval/predictions.jsonl
  # with darwin:
  python3 eval/harness/run_task.py --instance-id ... --darwin --model opencode/hy3-free --workdir ... --output ...

Creates a temp worktree from the SWE-bench instance's repo at base_commit,
writes .opencode/opencode.json (vanilla or darwin), runs:
  opencode run --dir <worktree> --model <model> "<prompt>"
where prompt = problem_statement + "Fix the issue. Run tests to verify."

Captures patch (git diff), tokens/cost if available, wall time.
Writes one JSONL line to --output with: instance_id, model, darwin, patch,
cost, tokens, duration_s, status. Handles timeouts (10 min) and errors.

Requires: python3, git, opencode. Works degraded without swebench package —
falls back to cached dataset or HF fetch for problem_statement, and to
git-based patch capture.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_TIMEOUT_S = 600  # 10 min per task
DEFAULT_MODEL = "opencode/hy3-free"

# ---------------------------------------------------------------------------
# Dataset helpers
# ---------------------------------------------------------------------------

def find_repo_root() -> Path:
    """Find darwin repo root (contains eval/datasets)."""
    cur = Path(__file__).resolve()
    for parent in cur.parents:
        if (parent / "eval" / "datasets" / "lite_50.json").exists() or (parent / "packages" / "core").exists():
            return parent
        if (parent / ".opencode" / "opencode.json").exists() and (parent / "package.json").exists():
            return parent
    return Path.cwd()

def load_json_dataset(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return {r["instance_id"]: r for r in data if "instance_id" in r}
        if isinstance(data, dict):
            return data
    except Exception as e:
        print(f"[run_task] warn: failed to load {path}: {e}", file=sys.stderr)
    return {}

def fetch_via_hf_api(instance_id: str) -> dict | None:
    """Fallback: fetch single instance from HF datasets-server."""
    import urllib.request
    import urllib.parse
    # Try datasets-server search? Simpler: scan cached file if present
    # If not cached, try to query HF parquet via datasets-server rows filter
    # We use the cached /tmp/swe_lite.json if available (populated by setup)
    cached_candidates = [
        Path("/tmp/swe_lite.json"),
        find_repo_root() / "eval" / "datasets" / "swe_lite_full.json",
    ]
    for p in cached_candidates:
        if p.exists():
            try:
                data = json.loads(p.read_text())
                # data is list
                for row in data if isinstance(data, list) else []:
                    rec = row.get("row", row) if "row" in row else row
                    if rec.get("instance_id") == instance_id:
                        return rec
            except Exception:
                continue
    # Live fetch: iterate datasets-server rows (expensive fallback)
    # We attempt a single fetch of all 300 and filter; caller should cache
    try:
        url = "https://datasets-server.huggingface.co/rows?dataset=princeton-nlp%2FSWE-bench_Lite&config=default&split=test&offset=0&length=100"
        # Try up to 3 pages (300 rows) — only if needed and network available
        for offset in (0, 100, 200):
            u = f"https://datasets-server.huggingface.co/rows?dataset=princeton-nlp%2FSWE-bench_Lite&config=default&split=test&offset={offset}&length=100"
            with urllib.request.urlopen(u, timeout=15) as r:
                payload = json.loads(r.read().decode())
                for row in payload.get("rows", []):
                    rec = row.get("row", {})
                    if rec.get("instance_id") == instance_id:
                        return rec
    except Exception as e:
        print(f"[run_task] hf fetch failed for {instance_id}: {e}", file=sys.stderr)
    return None

def load_instance(instance_id: str, dataset_path: Path | None) -> dict:
    """Load instance record with repo, base_commit, problem_statement.

    Tries: explicit dataset_path → repo lite_50.json → cached full → HF.
    Returns at least {instance_id, repo, base_commit, problem_statement}.
    """
    # 1. Explicit dataset
    if dataset_path and dataset_path.exists():
        m = load_json_dataset(dataset_path)
        if instance_id in m:
            rec = m[instance_id]
            # If record has full fields, return
            if rec.get("problem_statement"):
                return rec
            # need to enrich
            enriched = fetch_via_hf_api(instance_id)
            if enriched:
                return enriched
            # fallback: placeholder problem_statement
            rec = dict(rec)
            rec.setdefault("base_commit", rec.get("base_commit", "HEAD"))
            rec.setdefault("problem_statement", f"Fix issue {instance_id} in {rec.get('repo','')}.")
            return rec

    # 2. Default repo datasets
    repo_root = find_repo_root()
    for cand in [
        repo_root / "eval" / "datasets" / "lite_50.json",
        repo_root / "eval" / "datasets" / "lite.json",
        Path("/tmp/swe_lite.json"),
    ]:
        if cand.exists():
            m = load_json_dataset(cand)
            if instance_id in m:
                rec = m[instance_id]
                if rec.get("problem_statement"):
                    return rec
                # Enrich from full cache if possible
                enriched = fetch_via_hf_api(instance_id)
                if enriched:
                    return enriched
                rec = dict(rec)
                rec.setdefault("problem_statement", f"Fix issue {instance_id} in {rec.get('repo','')}.")
                rec.setdefault("base_commit", rec.get("base_commit", "HEAD"))
                return rec

    # 3. Try swebench package locally
    try:
        from datasets import load_dataset  # type: ignore
        ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
        for row in ds:
            if row["instance_id"] == instance_id:
                return dict(row)
    except Exception:
        pass

    # 4. HF API
    rec = fetch_via_hf_api(instance_id)
    if rec:
        return rec

    # 5. Placeholder (degraded but allows harness smoke-test)
    print(f"[run_task] warn: instance {instance_id} not found in any dataset; using placeholder", file=sys.stderr)
    return {
        "instance_id": instance_id,
        "repo": "unknown/repo",
        "base_commit": "HEAD",
        "problem_statement": f"Placeholder for {instance_id}. Fix the issue. Run tests to verify.",
    }

# ---------------------------------------------------------------------------
# Repo / worktree helpers
# ---------------------------------------------------------------------------

def sanitize_repo(repo: str) -> str:
    return repo.replace("/", "__")

def ensure_repo_cached(repo: str, base_commit: str, cache_root: Path) -> Path:
    """Ensure repo is cloned at cache_root/repos/<sanitized> and checked out at base_commit.

    Returns path to cached repo dir. If clone fails (offline), returns empty Path
    and caller should create empty worktree and warn.
    """
    if repo in ("unknown/repo", "", None):
        return Path()
    repos_dir = cache_root / "repos"
    repos_dir.mkdir(parents=True, exist_ok=True)
    sanitized = sanitize_repo(repo)
    dest = repos_dir / sanitized
    url = f"https://github.com/{repo}.git"

    if not dest.exists():
        print(f"[run_task] cloning {repo} -> {dest}", file=sys.stderr)
        try:
            subprocess.run(["git", "clone", url, str(dest)], check=True, timeout=120,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as e:
            print(f"[run_task] git clone failed for {repo}: {e}", file=sys.stderr)
            return Path()
        except subprocess.TimeoutExpired:
            print(f"[run_task] git clone timeout for {repo}", file=sys.stderr)
            return Path()
    else:
        # Ensure remote url correct and fetch
        try:
            subprocess.run(["git", "-C", str(dest), "fetch", "--depth", "50", "origin"], timeout=60,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception:
            pass  # offline or shallow; continue

    # Checkout base_commit if specified and not HEAD
    if base_commit and base_commit != "HEAD":
        try:
            # Try to resolve base_commit locally, else fetch
            r = subprocess.run(["git", "-C", str(dest), "cat-file", "-e", base_commit],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            if r.returncode != 0:
                # Fetch full history for that commit depth
                subprocess.run(["git", "-C", str(dest), "fetch", "origin", base_commit, "--depth", "1"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
                # If still not found, try unshallow
                r2 = subprocess.run(["git", "-C", str(dest), "cat-file", "-e", base_commit],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
                if r2.returncode != 0:
                    subprocess.run(["git", "-C", str(dest), "fetch", "--unshallow"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            subprocess.run(["git", "-C", str(dest), "checkout", "-f", base_commit],
                           check=True, timeout=30, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            # Ensure clean
            subprocess.run(["git", "-C", str(dest), "clean", "-fdx"],
                           timeout=20, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as e:
            print(f"[run_task] git checkout {base_commit} failed: {e}; using current HEAD", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print(f"[run_task] git checkout timeout for {base_commit}", file=sys.stderr)

    return dest

def create_worktree(cached_repo: Path, workdir: Path, instance_id: str) -> Path:
    """Create isolated worktree for this task.

    Strategy: git clone --local from cached repo if available; else temp empty dir with git init.
    Returns path to worktree.
    """
    tmp_root = workdir / "worktrees"
    tmp_root.mkdir(parents=True, exist_ok=True)
    # Use mkdtemp for uniqueness
    worktree = Path(tempfile.mkdtemp(prefix=f"{instance_id}__", dir=str(tmp_root)))

    if cached_repo and cached_repo.exists() and (cached_repo / ".git").exists():
        # Clone from cached repo (local, fast). Remove the empty tmp dir first.
        # We created via mkdtemp, so rmdir and clone in its place.
        try:
            worktree.rmdir()
        except OSError:
            shutil.rmtree(worktree, ignore_errors=True)
        try:
            subprocess.run(["git", "clone", "--local", str(cached_repo), str(worktree)],
                           check=True, timeout=60, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            # Ensure worktree is at base_commit (cached repo already at base_commit, but double-check)
            # Clean any leftover
            subprocess.run(["git", "-C", str(worktree), "clean", "-fdx"], timeout=20,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            # Configure user for commits (some repos require)
            subprocess.run(["git", "-C", str(worktree), "config", "user.email", "darwin-eval@example.com"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            subprocess.run(["git", "-C", str(worktree), "config", "user.name", "darwin-eval"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            return worktree
        except Exception as e:
            print(f"[run_task] worktree clone failed: {e}, falling back to copy", file=sys.stderr)
            # Fallback: copy tree
            worktree.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copytree(cached_repo, worktree, dirs_exist_ok=True, symlinks=True,
                                ignore=shutil.ignore_patterns(".git"))
                # Copy .git directory properly
                if (cached_repo / ".git").exists():
                    shutil.copytree(cached_repo / ".git", worktree / ".git", dirs_exist_ok=True, symlinks=True)
            except Exception as e2:
                print(f"[run_task] copytree fallback failed: {e2}", file=sys.stderr)
            return worktree
    else:
        # Degraded: no cached repo (offline or unknown). Init empty git repo so patch capture works (will be empty).
        print(f"[run_task] no cached repo, creating empty worktree at {worktree}", file=sys.stderr)
        try:
            subprocess.run(["git", "init"], cwd=str(worktree), timeout=10,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "config", "user.email", "darwin-eval@example.com"], cwd=str(worktree),
                           timeout=5, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "config", "user.name", "darwin-eval"], cwd=str(worktree),
                           timeout=5, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            # Create a placeholder file so repo is not empty
            (worktree / "README.md").write_text(f"# placeholder for {instance_id}\n")
            subprocess.run(["git", "-C", str(worktree), "add", "."], timeout=10,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "-C", str(worktree), "commit", "-m", "init placeholder", "--allow-empty"],
                           timeout=10, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception as e:
            print(f"[run_task] empty worktree init failed: {e}", file=sys.stderr)
        return worktree

def write_opencode_config(
    worktree: Path,
    model: str,
    darwin: bool,
    fallbacks: list[str] | None = None,
    opencode_json_overlay: Path | None = None,
    economics_routing: bool = False,
    routing_policy: str | None = None,
    budget_usd: float | None = None,
) -> None:
    """Write .opencode/opencode.json in worktree.

    For --darwin: include plugin and subagent_depth 3.
    Without darwin: vanilla config (model only).
    Supports fallback chain (native opencode `fallbacks`) and optional overlay
    file (as produced by eval/configs/experiments.yaml via run_matrix.sh).
    economics_routing is darwin-side routing (non-native); recorded as comment
    in meta for traceability.
    """
    oc_dir = worktree / ".opencode"
    oc_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = oc_dir / "opencode.json"

    # If an explicit overlay file is provided (single source of truth from
    # experiments.yaml), copy it verbatim — it already contains model/fallbacks/plugin.
    if opencode_json_overlay and opencode_json_overlay.exists():
        try:
            overlay = json.loads(opencode_json_overlay.read_text())
            # Ensure subagent_depth 3 for darwin parity even if overlay is minimal
            if darwin:
                overlay["subagent_depth"] = max(overlay.get("subagent_depth", 1), 3)
                # Ensure darwin plugin present when darwin=true
                plugins = overlay.get("plugin", [])
                if "@darwin/opencode-plugin" not in plugins:
                    plugins = list(plugins) + ["@darwin/opencode-plugin"]
                    overlay["plugin"] = plugins
            # Ensure model matches requested (overlay should already, but enforce)
            overlay["model"] = model
            # Fallbacks: if overlay lacks fallbacks but CLI provided them, inject
            if fallbacks and not overlay.get("fallbacks"):
                overlay["fallbacks"] = fallbacks
                overlay["cooldown_seconds"] = 300
            # Record economics routing as metadata comment (JSON doesn't support comments,
            # so store under a trace key that opencode ignores but eval can read)
            if economics_routing:
                overlay["_darwin_economics"] = {
                    "routing": True,
                    "policy": routing_policy or "judge-fail-then-cheapest-capable",
                    "budget_usd": budget_usd,
                }
            cfg_path.write_text(json.dumps(overlay, indent=2))
            print(f"[run_task] wrote overlay config to {cfg_path}: {overlay}", file=sys.stderr)
            return
        except Exception as e:
            print(f"[run_task] warn: failed to use overlay {opencode_json_overlay}: {e}, falling back to generated config", file=sys.stderr)

    # Generated config (backwards compatible)
    if darwin:
        cfg: dict = {
            "$schema": "https://opencode.ai/config.json",
            "model": model,
            "plugin": ["@darwin/opencode-plugin"],
            "subagent_depth": 3,
        }
    else:
        cfg = {
            "$schema": "https://opencode.ai/config.json",
            "model": model,
            "subagent_depth": 3,
        }
    if fallbacks:
        cfg["fallbacks"] = fallbacks
        cfg["cooldown_seconds"] = 300
    if economics_routing:
        cfg["_darwin_economics"] = {
            "routing": True,
            "policy": routing_policy or "judge-fail-then-cheapest-capable",
            "budget_usd": budget_usd,
        }
    # vanilla without fallbacks should not have stale keys
    cfg_path.write_text(json.dumps(cfg, indent=2))
    print(f"[run_task] wrote {'darwin' if darwin else 'vanilla'} config to {cfg_path}: {cfg}", file=sys.stderr)

# ---------------------------------------------------------------------------
# opencode execution
# ---------------------------------------------------------------------------

def build_prompt(problem_statement: str) -> str:
    # Spec: problem_statement + "Fix the issue. Run tests to verify."
    ps = problem_statement.strip()
    # Avoid double-adding if already contains instruction
    tail = "Fix the issue. Run tests to verify."
    if tail.lower() not in ps.lower()[-200:]:
        return f"{ps}\n\n{tail}"
    return ps

def _is_rate_limited(stdout: str, stderr: str, returncode: int) -> bool:
    """Heuristic: did this run fail due to rate-limit / quota / overload?"""
    combined = (stdout + "\n" + stderr).lower()
    signals = ["429", "rate limit", "rate_limit", "too many requests", "quota", "overloaded", "at capacity", "insufficient_quota", "free.*limit", "try again"]
    return any(s in combined for s in signals) or returncode == 429


def run_opencode(worktree: Path, model: str, prompt: str, timeout_s: int, fallbacks: list[str] | None = None) -> tuple[str, str, int, float, str]:
    """Run `opencode run --dir <worktree> --model <model> "<prompt>"`.

    Returns (stdout, stderr, returncode, duration_s, effective_model). Handles timeout
    and, when fallbacks are provided, retries harness-level on rate-limit (stable
    opencode has no native fallbacks — PR #26292 is fork-only).
    """
    candidates = [model] + (fallbacks or [])
    last_out: tuple[str, str, int, float] = ("", "no candidates", 1, 0.0)
    for idx, candidate in enumerate(candidates):
        start = time.time()
        base_cmd = ["opencode", "run", "--dir", str(worktree), "--model", candidate, "--format", "json", prompt]
        if idx > 0:
            print(f"[run_task] fallback {idx}/{len(candidates)-1}: trying {candidate} (previous rate-limited)", file=sys.stderr)
        else:
            print(f"[run_task] running: {' '.join(base_cmd[:6])} ... (prompt {len(prompt)} chars, timeout {timeout_s}s)", file=sys.stderr)

        if shutil.which("opencode") is None:
            msg = "opencode binary not found in PATH"
            print(f"[run_task] {msg}", file=sys.stderr)
            return "", msg, 127, 0.0, candidate

        try:
            proc = subprocess.run(base_cmd, capture_output=True, text=True, timeout=timeout_s)
            duration = time.time() - start
            last_out = (proc.stdout, proc.stderr, proc.returncode, duration)
        except subprocess.TimeoutExpired as e:
            duration = time.time() - start
            stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
            if isinstance(stdout, bytes): stdout = stdout.decode(errors="replace")
            if isinstance(stderr, bytes): stderr = stderr.decode(errors="replace")
            print(f"[run_task] timeout after {duration:.1f}s", file=sys.stderr)
            last_out = (stdout, (stderr + f"\n[TIMEOUT after {timeout_s}s]"), 124, duration)
        except FileNotFoundError as e:
            duration = time.time() - start
            last_out = ("", str(e), 127, duration)
        except Exception as e:
            duration = time.time() - start
            last_out = ("", str(e), 1, duration)

        stdout, stderr, rc, dur = last_out
        if _is_rate_limited(stdout, stderr, rc) and idx + 1 < len(candidates):
            print(f"[run_task] rate-limited on {candidate}, trying next fallback", file=sys.stderr)
            continue
        return stdout, stderr, rc, dur, candidate

    # All candidates exhausted (all rate-limited)
    stdout, stderr, rc, dur = last_out
    return stdout, stderr, rc, dur, candidates[-1] if candidates else model

def collect_patch(worktree: Path) -> str:
    """Collect git diff patch from worktree, excluding .opencode harness config.

    Tries: git diff HEAD (excluding .opencode), then staged, then untracked.
    Handles new files via `git add -A` + `git diff --cached`. The .opencode
    directory is never included — it is harness config, not a model patch.
    """
    # Primary: diff excluding .opencode (harness config)
    exclude_args = ["--", ".", ":!.opencode", ":!.opencode/**"]
    for base in [
        ["git", "-C", str(worktree), "diff", "HEAD"],
        ["git", "-C", str(worktree), "diff"],
        ["git", "-C", str(worktree), "diff", "--cached"],
    ]:
        try:
            # Try with exclude first (git >=2.16 supports :! syntax)
            r = subprocess.run(base + exclude_args, capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
            # Fallback without exclude if that failed
            r2 = subprocess.run(base, capture_output=True, text=True, timeout=10)
            # Filter out .opencode hunks manually if present
            if r2.returncode == 0 and r2.stdout.strip():
                # Strip .opencode hunks if they slipped through
                filtered = _filter_opencode_hunks(r2.stdout)
                if filtered.strip():
                    return filtered
                # If only opencode changed, treat as empty (no real patch)
                if _only_opencode_changed(r2.stdout):
                    return ""
                return r2.stdout
        except Exception:
            continue

    # Check for untracked / unstaged changes that diff HEAD missed (e.g., new files)
    try:
        r = subprocess.run(["git", "-C", str(worktree), "status", "--porcelain", "--", ".", ":!.opencode"], capture_output=True, text=True, timeout=10)
        status = (r.stdout or "").strip()
        if status:
            # Try binary diff excluding opencode
            r2 = subprocess.run(["git", "-C", str(worktree), "diff", "--binary", "HEAD", "--", ".", ":!.opencode"], capture_output=True, text=True, timeout=10)
            if r2.returncode == 0 and r2.stdout.strip():
                return r2.stdout
            # Stage everything (excluding .opencode via .gitignore? we filter post)
            try:
                # Add all, but then diff will include opencode unless filtered; so add with exclude
                subprocess.run(["git", "-C", str(worktree), "add", "-A", "--", ".", ":!.opencode"], capture_output=True, timeout=10)
                r3 = subprocess.run(["git", "-C", str(worktree), "diff", "--cached", "HEAD"], capture_output=True, text=True, timeout=10)
                if r3.returncode == 0 and r3.stdout.strip():
                    filtered = _filter_opencode_hunks(r3.stdout)
                    if filtered.strip():
                        return filtered
                r4 = subprocess.run(["git", "-C", str(worktree), "diff", "HEAD", "--", ".", ":!.opencode"], capture_output=True, text=True, timeout=10)
                if r4.returncode == 0 and r4.stdout.strip():
                    return r4.stdout
            except Exception:
                pass
            print(f"[run_task] git status shows changes but diff empty: {status[:500]}", file=sys.stderr)
    except Exception:
        pass
    return ""


def _filter_opencode_hunks(patch: str) -> str:
    """Remove .opencode hunks from a patch string."""
    if ".opencode" not in patch:
        return patch
    # Split by diff header and filter
    parts = re.split(r"(?=^diff --git)", patch, flags=re.MULTILINE)
    kept = [p for p in parts if ".opencode" not in p[:300]]
    return "".join(kept)


def _only_opencode_changed(patch: str) -> bool:
    """Return True if patch only touches .opencode."""
    if not patch.strip():
        return False
    parts = re.split(r"(?=^diff --git)", patch, flags=re.MULTILINE)
    non_opencode = [p for p in parts if p.strip() and ".opencode" not in p[:300]]
    return len(non_opencode) == 0

def parse_tokens_cost(output: str) -> tuple[int | None, float | None]:
    """Attempt to parse tokens/cost from opencode output.

    opencode --format json emits JSONL events; look for usage fields.
    Fallback: regex for tokens/cost in plain text.
    """
    tokens = None
    cost = None
    # Try JSONL parse
    try:
        # Output may be JSONL or a single JSON object or plain text
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            # Quick heuristic: line starts with { and contains tokens/cost
            if line.startswith("{") and ("token" in line.lower() or "cost" in line.lower() or "usage" in line.lower()):
                try:
                    obj = json.loads(line)
                    # Recursively search for tokens/cost
                    def search(o):
                        nonlocal tokens, cost
                        if isinstance(o, dict):
                            # common keys: tokens, usage, cost, total_tokens, input_tokens
                            if "tokens" in o and isinstance(o["tokens"], dict):
                                # tokens: {input, output, ...}
                                t = o["tokens"]
                                try:
                                    ti = int(t.get("input", 0))
                                    to = int(t.get("output", 0))
                                    tokens = (tokens or 0) + ti + to
                                except Exception:
                                    pass
                            if "total_tokens" in o:
                                try:
                                    tokens = int(o["total_tokens"])
                                except Exception:
                                    pass
                            if "input_tokens" in o or "output_tokens" in o:
                                try:
                                    ti = int(o.get("input_tokens", 0))
                                    to = int(o.get("output_tokens", 0))
                                    tokens = (tokens or 0) + ti + to
                                except Exception:
                                    pass
                            if "cost" in o:
                                try:
                                    c = float(o["cost"])
                                    cost = (cost or 0.0) + c
                                except Exception:
                                    pass
                            for v in o.values():
                                search(v)
                        elif isinstance(o, list):
                            for v in o:
                                search(v)
                    search(obj)
                except Exception:
                    continue
    except Exception:
        pass

    # Regex fallback for plain text like "tokens: 123" or "cost: $0.01"
    if tokens is None:
        m = re.search(r"tokens\D*(\d[\d,]*)", output, re.IGNORECASE)
        if m:
            try:
                tokens = int(m.group(1).replace(",", ""))
            except Exception:
                pass
    if cost is None:
        m = re.search(r"cost\D*\$?\s*([0-9]*\.[0-9]+)", output, re.IGNORECASE)
        if m:
            try:
                cost = float(m.group(1))
            except Exception:
                pass
        else:
            m = re.search(r"\$\s*([0-9]*\.[0-9]+)", output)
            if m:
                try:
                    cost = float(m.group(1))
                except Exception:
                    pass

    return tokens, cost

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Run single SWE-bench task with opencode")
    parser.add_argument("--instance-id", required=True, help="SWE-bench instance_id (e.g. django__django-11019)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="opencode model id (e.g. opencode/hy3-free)")
    parser.add_argument("--darwin", action="store_true", help="enable darwin plugin (vs vanilla)")
    parser.add_argument("--workdir", default=None, help="base workdir for repos cache and worktrees (default: ./eval/workdir)")
    parser.add_argument("--output", required=True, help="output JSONL file (one line per prediction)")
    parser.add_argument("--dataset", default=None, help="path to dataset JSON (default: eval/datasets/lite_50.json)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="timeout per task in seconds (default 600)")
    parser.add_argument("--keep-worktree", action="store_true", help="keep worktree after run (for debugging)")
    # Matrix / fallback support (from eval/configs/experiments.yaml via run_matrix.sh)
    parser.add_argument("--fallbacks", nargs="*", default=None, help="fallback model ids (native opencode fallbacks, tried on 429/5xx)")
    parser.add_argument("--fallback", dest="fallbacks", nargs="*", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--opencode-json", dest="opencode_json", default=None, help="explicit opencode.json overlay to copy into worktree (single source of truth from experiments.yaml)")
    parser.add_argument("--economics-routing", action="store_true", help="economics-routed mixture (darwin picks model when stuck)")
    parser.add_argument("--routing-policy", default=None, help="routing policy when economics_routing is on (e.g. judge-fail-then-cheapest-capable)")
    parser.add_argument("--budget", type=float, default=None, dest="budget_usd", help="budget USD for darwin economics guard (e.g. 50)")
    parser.add_argument("--budget-usd", type=float, default=None, dest="budget_usd", help=argparse.SUPPRESS)
    args = parser.parse_args()

    instance_id = args.instance_id
    model = args.model
    darwin = bool(args.darwin)
    timeout = int(args.timeout)
    output_path = Path(args.output)

    # Resolve workdir
    if args.workdir:
        workdir = Path(args.workdir)
    else:
        # Default: ./eval/workdir relative to repo root, else /tmp/darwin-eval
        repo_root = find_repo_root()
        workdir = repo_root / "eval" / "workdir"
        if not repo_root.exists() or str(repo_root) == "/":
            workdir = Path(tempfile.gettempdir()) / "darwin-eval"
    workdir.mkdir(parents=True, exist_ok=True)

    # Resolve dataset path
    dataset_path = Path(args.dataset) if args.dataset else None
    if dataset_path and not dataset_path.exists():
        print(f"[run_task] dataset not found at {dataset_path}, will search defaults", file=sys.stderr)
        dataset_path = None
    if dataset_path is None:
        repo_root = find_repo_root()
        cand = repo_root / "eval" / "datasets" / "lite_50.json"
        if cand.exists():
            dataset_path = cand

    # Load instance
    t0 = time.time()
    try:
        inst = load_instance(instance_id, dataset_path)
    except Exception as e:
        print(f"[run_task] failed to load instance {instance_id}: {e}", file=sys.stderr)
        inst = {
            "instance_id": instance_id,
            "repo": "unknown/repo",
            "base_commit": "HEAD",
            "problem_statement": f"Fix issue {instance_id}.",
        }

    repo = inst.get("repo", "unknown/repo")
    base_commit = inst.get("base_commit", "HEAD")
    problem_statement = inst.get("problem_statement", inst.get("problemStatement", "")) or f"Fix issue {instance_id} in {repo}."

    print(f"[run_task] instance={instance_id} repo={repo} commit={base_commit[:8] if base_commit!='HEAD' else 'HEAD'} model={model} darwin={darwin}", file=sys.stderr)

    # Prepare worktree
    worktree: Path | None = None
    status = "success"
    patch = ""
    stdout = ""
    stderr = ""
    duration_s = 0.0
    tokens = None
    cost = None

    try:
        cached = ensure_repo_cached(repo, base_commit, workdir)
        worktree = create_worktree(cached, workdir, instance_id)
        # Parse fallbacks: support both space-separated and comma-separated (from run_matrix.sh)
        _fallbacks: list[str] | None = None
        if getattr(args, "fallbacks", None):
            raw = list(args.fallbacks) if isinstance(args.fallbacks, list) else [str(args.fallbacks)]
            expanded: list[str] = []
            for tok in raw:
                if "," in tok:
                    expanded.extend([s.strip() for s in tok.split(",") if s.strip()])
                elif tok.strip():
                    expanded.append(tok.strip())
            _fallbacks = expanded or None
        _overlay = Path(args.opencode_json) if getattr(args, "opencode_json", None) else None
        write_opencode_config(
            worktree,
            model,
            darwin,
            fallbacks=_fallbacks,
            opencode_json_overlay=_overlay,
            economics_routing=bool(getattr(args, "economics_routing", False)),
            routing_policy=getattr(args, "routing_policy", None),
            budget_usd=getattr(args, "budget_usd", None),
        )

        prompt = build_prompt(problem_statement)

        # Run opencode (harness-level fallback on 429 — stable opencode has no native fallbacks)
        stdout, stderr, rc, duration_s, effective_model = run_opencode(worktree, model, prompt, timeout, fallbacks=_fallbacks)
        # Determine status from rc
        if rc == 124:
            status = "timeout"
        elif rc == 127:
            status = "error_no_opencode"
        elif rc != 0:
            # opencode may return non-zero on failure but still produce patch
            status = "error"
            # Keep as error but still collect patch
        else:
            status = "success"

        # Parse tokens/cost
        combined_output = stdout + "\n" + stderr
        tokens, cost = parse_tokens_cost(combined_output)

        # Collect patch
        patch = collect_patch(worktree) if worktree else ""
        if not patch.strip():
            # Patch empty is common on failure/timeout; mark status accordingly but not overwrite timeout/error
            if status == "success":
                status = "no_patch"

        # Optional: also capture git log for debugging
        print(f"[run_task] done instance={instance_id} status={status} patch_lines={len(patch.splitlines())} duration={duration_s:.1f}s", file=sys.stderr)
        if not patch.strip():
            print(f"[run_task] warn: empty patch for {instance_id} (stdout {len(stdout)} chars, stderr {len(stderr)} chars)", file=sys.stderr)
            if stdout.strip():
                print(f"[run_task] stdout preview: {stdout[:500]}", file=sys.stderr)
            if stderr.strip():
                print(f"[run_task] stderr preview: {stderr[:500]}", file=sys.stderr)

    except Exception as e:
        import traceback
        duration_s = time.time() - t0
        status = "error_exception"
        stderr += f"\nException in harness: {e}\n{traceback.format_exc()}"
        print(f"[run_task] exception for {instance_id}: {e}", file=sys.stderr)
        traceback.print_exc()
    finally:
        # Cleanup worktree unless keep flag
        if worktree and worktree.exists() and not args.keep_worktree:
            # Keep for debugging on failure? We respect flag only; else always clean
            # For timeout/error we could keep, but spec says temp worktree — clean up
            try:
                shutil.rmtree(worktree, ignore_errors=True)
            except Exception as e:
                print(f"[run_task] failed to clean worktree {worktree}: {e}", file=sys.stderr)

        # Write JSONL line — always, even on error
        duration_s = duration_s or (time.time() - t0)
        # Preserve fallbacks/routing for compare.py model distribution & cross analysis
        _rec_fallbacks = None
        if getattr(args, "fallbacks", None):
            raw = list(args.fallbacks) if isinstance(args.fallbacks, list) else [str(args.fallbacks)]
            exp: list[str] = []
            for tok in raw:
                if "," in tok:
                    exp.extend([s.strip() for s in tok.split(",") if s.strip()])
                elif tok.strip():
                    exp.append(tok.strip())
            _rec_fallbacks = exp or None
        _eff = locals().get("effective_model") or model
        record = {
            "instance_id": instance_id,
            "model": _eff,
            "model_name_or_path": _eff,
            "requested_model": model,
            "darwin": darwin,
            "patch": patch,
            "model_patch": patch,  # alternate key for swebench eval
            "cost": cost,
            "tokens": tokens,
            "duration_s": round(duration_s, 2),
            "status": status,
            # Traceability for matrix comparison (fallbacks & economics routing)
            "fallbacks": _rec_fallbacks,
            "economics_routing": bool(getattr(args, "economics_routing", False)),
            "routing_policy": getattr(args, "routing_policy", None),
            "budget_usd": getattr(args, "budget_usd", None),
            # Extra for debugging (not required but useful)
            "repo": repo,
            "base_commit": base_commit,
        }
        # Ensure output dir exists
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("a") as f:
            f.write(json.dumps(record) + "\n")
        # Also print to stdout for visibility
        print(json.dumps(record))

if __name__ == "__main__":
    main()
