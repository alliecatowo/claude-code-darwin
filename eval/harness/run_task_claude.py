#!/usr/bin/env python3
"""
run_task_claude.py — run a single SWE-bench task with Claude Code (vanilla or darwin).

Mirrors eval/harness/run_task.py but for Claude Code.

Usage:
  python3 eval/harness/run_task_claude.py \\
    --instance-id django__django-11019 \\
    --model sonnet \\
    --workdir ./eval/workdir \\
    --output ./eval/predictions.jsonl
  # with darwin:
  python3 eval/harness/run_task_claude.py --instance-id ... --darwin --model sonnet --workdir ... --output ...

Creates a temp worktree from the SWE-bench instance's repo at base_commit,
writes .claude/settings.json (vanilla or darwin), runs:
  claude -p --model <model> --dangerously-skip-permissions --output-format stream-json "$PROMPT"
where prompt = problem_statement + "Fix the issue. Run tests to verify."

Captures patch (git diff), tokens/cost if available, wall time.
Writes one JSONL line to --output with: instance_id, model, darwin, patch,
cost, tokens, duration_s, status. Handles timeouts (10 min) and errors.

Requires: python3, git, claude. Works degraded without swebench package —
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
DEFAULT_MODEL = "sonnet"

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
        print(f"[run_task_claude] warn: failed to load {path}: {e}", file=sys.stderr)
    return {}

def fetch_via_hf_api(instance_id: str) -> dict | None:
    """Fallback: fetch single instance from HF datasets-server."""
    import urllib.request
    import urllib.parse
    cached_candidates = [
        Path("/tmp/swe_lite.json"),
        find_repo_root() / "eval" / "datasets" / "swe_lite_full.json",
    ]
    for p in cached_candidates:
        if p.exists():
            try:
                data = json.loads(p.read_text())
                for row in data if isinstance(data, list) else []:
                    rec = row.get("row", row) if "row" in row else row
                    if rec.get("instance_id") == instance_id:
                        return rec
            except Exception:
                continue
    try:
        url = "https://datasets-server.huggingface.co/rows?dataset=princeton-nlp%2FSWE-bench_Lite&config=default&split=test&offset=0&length=100"
        for offset in (0, 100, 200):
            u = f"https://datasets-server.huggingface.co/rows?dataset=princeton-nlp%2FSWE-bench_Lite&config=default&split=test&offset={offset}&length=100"
            with urllib.request.urlopen(u, timeout=15) as r:
                payload = json.loads(r.read().decode())
                for row in payload.get("rows", []):
                    rec = row.get("row", {})
                    if rec.get("instance_id") == instance_id:
                        return rec
    except Exception as e:
        print(f"[run_task_claude] hf fetch failed for {instance_id}: {e}", file=sys.stderr)
    return None

def load_instance(instance_id: str, dataset_path: Path | None) -> dict:
    """Load instance record with repo, base_commit, problem_statement.

    Tries: explicit dataset_path → repo lite_50.json → cached full → HF.
    Returns at least {instance_id, repo, base_commit, problem_statement}.
    """
    if dataset_path and dataset_path.exists():
        m = load_json_dataset(dataset_path)
        if instance_id in m:
            rec = m[instance_id]
            if rec.get("problem_statement"):
                return rec
            enriched = fetch_via_hf_api(instance_id)
            if enriched:
                return enriched
            rec = dict(rec)
            rec.setdefault("base_commit", rec.get("base_commit", "HEAD"))
            rec.setdefault("problem_statement", f"Fix issue {instance_id} in {rec.get('repo','')}.")
            return rec

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
                enriched = fetch_via_hf_api(instance_id)
                if enriched:
                    return enriched
                rec = dict(rec)
                rec.setdefault("problem_statement", f"Fix issue {instance_id} in {rec.get('repo','')}.")
                rec.setdefault("base_commit", rec.get("base_commit", "HEAD"))
                return rec

    try:
        from datasets import load_dataset  # type: ignore
        ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
        for row in ds:
            if row["instance_id"] == instance_id:
                return dict(row)
    except Exception:
        pass

    rec = fetch_via_hf_api(instance_id)
    if rec:
        return rec

    print(f"[run_task_claude] warn: instance {instance_id} not found in any dataset; using placeholder", file=sys.stderr)
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
        print(f"[run_task_claude] cloning {repo} -> {dest}", file=sys.stderr)
        try:
            subprocess.run(["git", "clone", url, str(dest)], check=True, timeout=120,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as e:
            print(f"[run_task_claude] git clone failed for {repo}: {e}", file=sys.stderr)
            return Path()
        except subprocess.TimeoutExpired:
            print(f"[run_task_claude] git clone timeout for {repo}", file=sys.stderr)
            return Path()
    else:
        try:
            subprocess.run(["git", "-C", str(dest), "fetch", "--depth", "50", "origin"], timeout=60,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception:
            pass

    if base_commit and base_commit != "HEAD":
        try:
            r = subprocess.run(["git", "-C", str(dest), "cat-file", "-e", base_commit],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            if r.returncode != 0:
                subprocess.run(["git", "-C", str(dest), "fetch", "origin", base_commit, "--depth", "1"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
                r2 = subprocess.run(["git", "-C", str(dest), "cat-file", "-e", base_commit],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
                if r2.returncode != 0:
                    subprocess.run(["git", "-C", str(dest), "fetch", "--unshallow"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            subprocess.run(["git", "-C", str(dest), "checkout", "-f", base_commit],
                           check=True, timeout=30, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "-C", str(dest), "clean", "-fdx"],
                           timeout=20, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as e:
            print(f"[run_task_claude] git checkout {base_commit} failed: {e}; using current HEAD", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print(f"[run_task_claude] git checkout timeout for {base_commit}", file=sys.stderr)

    return dest

def create_worktree(cached_repo: Path, workdir: Path, instance_id: str) -> Path:
    """Create isolated worktree for this task.

    Strategy: git clone --local from cached repo if available; else temp empty dir with git init.
    Returns path to worktree.
    """
    tmp_root = workdir / "worktrees"
    tmp_root.mkdir(parents=True, exist_ok=True)
    worktree = Path(tempfile.mkdtemp(prefix=f"{instance_id}__", dir=str(tmp_root)))

    if cached_repo and cached_repo.exists() and (cached_repo / ".git").exists():
        try:
            worktree.rmdir()
        except OSError:
            shutil.rmtree(worktree, ignore_errors=True)
        try:
            subprocess.run(["git", "clone", "--local", str(cached_repo), str(worktree)],
                           check=True, timeout=60, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "-C", str(worktree), "clean", "-fdx"], timeout=20,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "-C", str(worktree), "config", "user.email", "darwin-eval@example.com"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            subprocess.run(["git", "-C", str(worktree), "config", "user.name", "darwin-eval"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            return worktree
        except Exception as e:
            print(f"[run_task_claude] worktree clone failed: {e}, falling back to copy", file=sys.stderr)
            worktree.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copytree(cached_repo, worktree, dirs_exist_ok=True, symlinks=True,
                                ignore=shutil.ignore_patterns(".git"))
                if (cached_repo / ".git").exists():
                    shutil.copytree(cached_repo / ".git", worktree / ".git", dirs_exist_ok=True, symlinks=True)
            except Exception as e2:
                print(f"[run_task_claude] copytree fallback failed: {e2}", file=sys.stderr)
            return worktree
    else:
        print(f"[run_task_claude] no cached repo, creating empty worktree at {worktree}", file=sys.stderr)
        try:
            subprocess.run(["git", "init"], cwd=str(worktree), timeout=10,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "config", "user.email", "darwin-eval@example.com"], cwd=str(worktree),
                           timeout=5, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "config", "user.name", "darwin-eval"], cwd=str(worktree),
                           timeout=5, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            (worktree / "README.md").write_text(f"# placeholder for {instance_id}\n")
            subprocess.run(["git", "-C", str(worktree), "add", "."], timeout=10,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "-C", str(worktree), "commit", "-m", "init placeholder", "--allow-empty"],
                           timeout=10, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception as e:
            print(f"[run_task_claude] empty worktree init failed: {e}", file=sys.stderr)
        return worktree

def write_claude_config(
    worktree: Path,
    model: str,
    darwin: bool,
    fallbacks: list[str] | None = None,
    claude_json_overlay: Path | None = None,
    opencode_json_overlay: Path | None = None,
    economics_routing: bool = False,
    routing_policy: str | None = None,
    budget_usd: float | None = None,
) -> None:
    """Write .claude/settings.json in worktree.

    For --darwin: include plugin enablement (enabledPlugins).
    Without darwin: vanilla config (no plugin).
    Supports fallback chain (recorded as trace) and optional overlay
    file (as produced by eval/configs via run_matrix.sh).
    economics_routing is darwin-side routing (recorded as trace).
    """
    claude_dir = worktree / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = claude_dir / "settings.json"

    # Resolve overlay: preference to claude_json, fallback to opencode_json for compatibility
    overlay_path = claude_json_overlay or opencode_json_overlay
    if overlay_path and overlay_path.exists():
        try:
            overlay = json.loads(overlay_path.read_text())
            # Normalize: if overlay looks like opencode.json (has "plugin"), translate
            if "plugin" in overlay and "enabledPlugins" not in overlay:
                # Translate opencode plugin entry to claude enabledPlugins
                plugins = overlay.get("plugin", [])
                has_darwin = any("darwin" in str(p) for p in plugins)
                if darwin or has_darwin:
                    overlay_enabled = overlay.get("enabledPlugins", {})
                    if isinstance(overlay_enabled, dict):
                        overlay_enabled["darwin@darwin"] = True
                    else:
                        overlay_enabled = {"darwin@darwin": True}
                    overlay["enabledPlugins"] = overlay_enabled
                # Remove opencode-specific keys that confuse claude
                overlay.pop("plugin", None)
                overlay.pop("fallbacks", None)
                overlay.pop("cooldown_seconds", None)
            if darwin:
                enabled = overlay.get("enabledPlugins", {})
                if isinstance(enabled, dict):
                    if "darwin@darwin" not in enabled:
                        enabled["darwin@darwin"] = True
                    overlay["enabledPlugins"] = enabled
                elif isinstance(enabled, list):
                    if "darwin@darwin" not in enabled:
                        enabled.append("darwin@darwin")
                    overlay["enabledPlugins"] = enabled
                else:
                    overlay["enabledPlugins"] = {"darwin@darwin": True}
                # Ensure permissions allow darwin operations
                overlay.setdefault("permissions", {})
            else:
                # vanilla: ensure no darwin plugin
                ep = overlay.get("enabledPlugins")
                if isinstance(ep, dict):
                    ep.pop("darwin@darwin", None)
                    ep.pop("@darwin/claude-plugin", None)
                elif isinstance(ep, list):
                    overlay["enabledPlugins"] = [p for p in ep if "darwin" not in str(p)]
            if fallbacks:
                overlay["_darwin_fallbacks"] = fallbacks
            if economics_routing:
                overlay["_darwin_economics"] = {
                    "routing": True,
                    "policy": routing_policy or "judge-fail-then-cheapest-capable",
                    "budget_usd": budget_usd,
                }
            # Keep model trace
            overlay["_darwin_model"] = model
            overlay["_darwin_harness"] = "claude"
            cfg_path.write_text(json.dumps(overlay, indent=2))
            print(f"[run_task_claude] wrote overlay config to {cfg_path}: {overlay}", file=sys.stderr)
            return
        except Exception as e:
            print(f"[run_task_claude] warn: failed to use overlay {overlay_path}: {e}, falling back to generated config", file=sys.stderr)

    # Generated config (minimal)
    if darwin:
        cfg: dict = {
            "permissions": {"defaultMode": "acceptEdits"},
            "enabledPlugins": {"darwin@darwin": True},
            "_darwin_harness": "claude",
            "_darwin_model": model,
        }
    else:
        cfg = {
            "permissions": {"defaultMode": "acceptEdits"},
            "_darwin_harness": "claude",
            "_darwin_model": model,
        }
    if fallbacks:
        cfg["_darwin_fallbacks"] = fallbacks
    if economics_routing:
        cfg["_darwin_economics"] = {
            "routing": True,
            "policy": routing_policy or "judge-fail-then-cheapest-capable",
            "budget_usd": budget_usd,
        }
    cfg_path.write_text(json.dumps(cfg, indent=2))
    print(f"[run_task_claude] wrote {'darwin' if darwin else 'vanilla'} config to {cfg_path}: {cfg}", file=sys.stderr)

# ---------------------------------------------------------------------------
# claude execution
# ---------------------------------------------------------------------------

def build_prompt(problem_statement: str) -> str:
    ps = problem_statement.strip()
    tail = "Fix the issue. Run tests to verify."
    if tail.lower() not in ps.lower()[-200:]:
        return f"{ps}\n\n{tail}"
    return ps

def run_claude(worktree: Path, model: str, prompt: str, timeout_s: int) -> tuple[str, str, int, float]:
    """Run `claude -p --model <model> --dangerously-skip-permissions --output-format stream-json "$PROMPT"`.

    Returns (stdout, stderr, returncode, duration_s). Handles timeout.
    """
    start = time.time()
    base_cmd = ["claude", "-p", "--model", model, "--dangerously-skip-permissions", "--output-format", "stream-json", prompt]
    print(f"[run_task_claude] running: claude -p --model {model} --dangerously-skip-permissions --output-format stream-json ... (prompt {len(prompt)} chars, timeout {timeout_s}s)", file=sys.stderr)

    if shutil.which("claude") is None:
        msg = "claude binary not found in PATH"
        print(f"[run_task_claude] {msg}", file=sys.stderr)
        return "", msg, 127, 0.0

    try:
        proc = subprocess.run(
            base_cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=str(worktree),
        )
        duration = time.time() - start
        return proc.stdout, proc.stderr, proc.returncode, duration
    except subprocess.TimeoutExpired as e:
        duration = time.time() - start
        stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        print(f"[run_task_claude] timeout after {duration:.1f}s", file=sys.stderr)
        return stdout, (stderr + f"\n[TIMEOUT after {timeout_s}s]"), 124, duration
    except FileNotFoundError as e:
        duration = time.time() - start
        return "", str(e), 127, duration
    except Exception as e:
        duration = time.time() - start
        return "", str(e), 1, duration

def collect_patch(worktree: Path) -> str:
    """Collect git diff patch from worktree, excluding .claude/.opencode harness config.

    Tries: git diff HEAD (excluding harness dirs), then staged, then untracked.
    Handles new files via `git add -A` + `git diff --cached`. The harness
    directories are never included — they are harness config, not a model patch.
    """
    exclude_args = ["--", ".", ":!.claude", ":!.claude/**", ":!.opencode", ":!.opencode/**"]
    for base in [
        ["git", "-C", str(worktree), "diff", "HEAD"],
        ["git", "-C", str(worktree), "diff"],
        ["git", "-C", str(worktree), "diff", "--cached"],
    ]:
        try:
            r = subprocess.run(base + exclude_args, capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
            r2 = subprocess.run(base, capture_output=True, text=True, timeout=10)
            if r2.returncode == 0 and r2.stdout.strip():
                filtered = _filter_harness_hunks(r2.stdout)
                if filtered.strip():
                    return filtered
                if _only_harness_changed(r2.stdout):
                    return ""
                return r2.stdout
        except Exception:
            continue

    try:
        r = subprocess.run(["git", "-C", str(worktree), "status", "--porcelain", "--", ".", ":!.claude", ":!.opencode"], capture_output=True, text=True, timeout=10)
        status = (r.stdout or "").strip()
        if status:
            r2 = subprocess.run(["git", "-C", str(worktree), "diff", "--binary", "HEAD", "--", ".", ":!.claude", ":!.opencode"], capture_output=True, text=True, timeout=10)
            if r2.returncode == 0 and r2.stdout.strip():
                return r2.stdout
            try:
                subprocess.run(["git", "-C", str(worktree), "add", "-A", "--", ".", ":!.claude", ":!.opencode"], capture_output=True, timeout=10)
                r3 = subprocess.run(["git", "-C", str(worktree), "diff", "--cached", "HEAD"], capture_output=True, text=True, timeout=10)
                if r3.returncode == 0 and r3.stdout.strip():
                    filtered = _filter_harness_hunks(r3.stdout)
                    if filtered.strip():
                        return filtered
                r4 = subprocess.run(["git", "-C", str(worktree), "diff", "HEAD", "--", ".", ":!.claude", ":!.opencode"], capture_output=True, text=True, timeout=10)
                if r4.returncode == 0 and r4.stdout.strip():
                    return r4.stdout
            except Exception:
                pass
            print(f"[run_task_claude] git status shows changes but diff empty: {status[:500]}", file=sys.stderr)
    except Exception:
        pass
    return ""


def _filter_harness_hunks(patch: str) -> str:
    """Remove .claude/.opencode hunks from a patch string."""
    if ".claude" not in patch and ".opencode" not in patch:
        return patch
    parts = re.split(r"(?=^diff --git)", patch, flags=re.MULTILINE)
    kept = [p for p in parts if ".claude" not in p[:400] and ".opencode" not in p[:400]]
    return "".join(kept)


def _only_harness_changed(patch: str) -> bool:
    """Return True if patch only touches harness dirs."""
    if not patch.strip():
        return False
    parts = re.split(r"(?=^diff --git)", patch, flags=re.MULTILINE)
    non_harness = [p for p in parts if p.strip() and ".claude" not in p[:400] and ".opencode" not in p[:400]]
    return len(non_harness) == 0

def parse_tokens_cost(output: str) -> tuple[int | None, float | None]:
    """Attempt to parse tokens/cost from claude output.

    claude --output-format stream-json emits JSONL events; look for usage fields.
    Fallback: regex for tokens/cost in plain text.
    Handles claude-specific usage shapes:
      usage: {input_tokens, output_tokens, cache_read_input_tokens, ...}
      tokens: {input, output, ...}
    """
    tokens = None
    cost = None
    try:
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("{") and ("token" in line.lower() or "cost" in line.lower() or "usage" in line.lower()):
                try:
                    obj = json.loads(line)
                    def search(o):
                        nonlocal tokens, cost
                        if isinstance(o, dict):
                            if "tokens" in o and isinstance(o["tokens"], dict):
                                t = o["tokens"]
                                try:
                                    ti = int(t.get("input", 0))
                                    to = int(t.get("output", 0))
                                    # handle alternative keys
                                    if ti == 0 and "input_tokens" in t:
                                        ti = int(t.get("input_tokens", 0))
                                    if to == 0 and "output_tokens" in t:
                                        to = int(t.get("output_tokens", 0))
                                    tokens = (tokens or 0) + ti + to
                                except Exception:
                                    pass
                            if "usage" in o and isinstance(o["usage"], dict):
                                u = o["usage"]
                                try:
                                    ti = int(u.get("input_tokens", u.get("input", 0)) or 0)
                                    to = int(u.get("output_tokens", u.get("output", 0)) or 0)
                                    if ti or to:
                                        tokens = (tokens or 0) + ti + to
                                except Exception:
                                    pass
                            if "total_tokens" in o:
                                try:
                                    tokens = int(o["total_tokens"])
                                except Exception:
                                    pass
                            if "input_tokens" in o or "output_tokens" in o:
                                # top-level usage
                                try:
                                    ti = int(o.get("input_tokens", 0))
                                    to = int(o.get("output_tokens", 0))
                                    # avoid double-count if already counted via nested usage
                                    # only count if not already from usage dict
                                    if "usage" not in o:
                                        tokens = (tokens or 0) + ti + to
                                except Exception:
                                    pass
                            if "cost" in o:
                                try:
                                    c = float(o["cost"])
                                    cost = (cost or 0.0) + c
                                except Exception:
                                    pass
                            if "total_cost" in o:
                                try:
                                    c = float(o["total_cost"])
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
    parser = argparse.ArgumentParser(description="Run single SWE-bench task with Claude Code")
    parser.add_argument("--instance-id", required=True, help="SWE-bench instance_id (e.g. django__django-11019)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="claude model id (e.g. sonnet, opus)")
    parser.add_argument("--darwin", action="store_true", help="enable darwin plugin (vs vanilla)")
    parser.add_argument("--workdir", default=None, help="base workdir for repos cache and worktrees (default: ./eval/workdir)")
    parser.add_argument("--output", required=True, help="output JSONL file (one line per prediction)")
    parser.add_argument("--dataset", default=None, help="path to dataset JSON (default: eval/datasets/lite_50.json)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="timeout per task in seconds (default 600)")
    parser.add_argument("--keep-worktree", action="store_true", help="keep worktree after run (for debugging)")
    # Matrix / fallback support (from eval/configs/experiments.yaml via run_matrix.sh)
    parser.add_argument("--fallbacks", nargs="*", default=None, help="fallback model ids (recorded as trace, not native to claude)")
    parser.add_argument("--fallback", dest="fallbacks", nargs="*", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--opencode-json", dest="opencode_json", default=None, help="explicit opencode.json overlay to translate into claude settings (compatibility)")
    parser.add_argument("--claude-json", dest="claude_json", default=None, help="explicit claude settings.json overlay to copy into worktree")
    parser.add_argument("--economics-routing", action="store_true", help="economics-routed mixture (darwin picks model when stuck)")
    parser.add_argument("--routing-policy", default=None, help="routing policy when economics_routing is on")
    parser.add_argument("--budget", type=float, default=None, dest="budget_usd", help="budget USD for darwin economics guard")
    parser.add_argument("--budget-usd", type=float, default=None, dest="budget_usd", help=argparse.SUPPRESS)
    args = parser.parse_args()

    instance_id = args.instance_id
    model = args.model
    darwin = bool(args.darwin)
    timeout = int(args.timeout)
    output_path = Path(args.output)

    if args.workdir:
        workdir = Path(args.workdir)
    else:
        repo_root = find_repo_root()
        workdir = repo_root / "eval" / "workdir"
        if not repo_root.exists() or str(repo_root) == "/":
            workdir = Path(tempfile.gettempdir()) / "darwin-eval"
    workdir.mkdir(parents=True, exist_ok=True)

    dataset_path = Path(args.dataset) if args.dataset else None
    if dataset_path and not dataset_path.exists():
        print(f"[run_task_claude] dataset not found at {dataset_path}, will search defaults", file=sys.stderr)
        dataset_path = None
    if dataset_path is None:
        repo_root = find_repo_root()
        cand = repo_root / "eval" / "datasets" / "lite_50.json"
        if cand.exists():
            dataset_path = cand

    t0 = time.time()
    try:
        inst = load_instance(instance_id, dataset_path)
    except Exception as e:
        print(f"[run_task_claude] failed to load instance {instance_id}: {e}", file=sys.stderr)
        inst = {
            "instance_id": instance_id,
            "repo": "unknown/repo",
            "base_commit": "HEAD",
            "problem_statement": f"Fix issue {instance_id}.",
        }

    repo = inst.get("repo", "unknown/repo")
    base_commit = inst.get("base_commit", "HEAD")
    problem_statement = inst.get("problem_statement", inst.get("problemStatement", "")) or f"Fix issue {instance_id} in {repo}."

    print(f"[run_task_claude] instance={instance_id} repo={repo} commit={base_commit[:8] if base_commit!='HEAD' else 'HEAD'} model={model} darwin={darwin} harness=claude", file=sys.stderr)

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
        _overlay_claude = Path(args.claude_json) if getattr(args, "claude_json", None) else None
        _overlay_opencode = Path(args.opencode_json) if getattr(args, "opencode_json", None) else None
        write_claude_config(
            worktree,
            model,
            darwin,
            fallbacks=_fallbacks,
            claude_json_overlay=_overlay_claude,
            opencode_json_overlay=_overlay_opencode,
            economics_routing=bool(getattr(args, "economics_routing", False)),
            routing_policy=getattr(args, "routing_policy", None),
            budget_usd=getattr(args, "budget_usd", None),
        )

        prompt = build_prompt(problem_statement)

        stdout, stderr, rc, duration_s = run_claude(worktree, model, prompt, timeout)
        if rc == 124:
            status = "timeout"
        elif rc == 127:
            status = "error_no_claude"
        elif rc != 0:
            status = "error"
        else:
            status = "success"

        combined_output = stdout + "\n" + stderr
        tokens, cost = parse_tokens_cost(combined_output)

        patch = collect_patch(worktree) if worktree else ""
        if not patch.strip():
            if status == "success":
                status = "no_patch"

        print(f"[run_task_claude] done instance={instance_id} status={status} patch_lines={len(patch.splitlines())} duration={duration_s:.1f}s", file=sys.stderr)
        if not patch.strip():
            print(f"[run_task_claude] warn: empty patch for {instance_id} (stdout {len(stdout)} chars, stderr {len(stderr)} chars)", file=sys.stderr)

    except Exception as e:
        import traceback
        duration_s = time.time() - t0
        status = "error_exception"
        stderr += f"\nException in harness: {e}\n{traceback.format_exc()}"
        print(f"[run_task_claude] exception for {instance_id}: {e}", file=sys.stderr)
        traceback.print_exc()
    finally:
        if worktree and worktree.exists() and not args.keep_worktree:
            try:
                shutil.rmtree(worktree, ignore_errors=True)
            except Exception as e:
                print(f"[run_task_claude] failed to clean worktree {worktree}: {e}", file=sys.stderr)

        duration_s = duration_s or (time.time() - t0)
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
        record = {
            "instance_id": instance_id,
            "model": model,
            "model_name_or_path": model,
            "harness": "claude",
            "darwin": darwin,
            "patch": patch,
            "model_patch": patch,
            "cost": cost,
            "tokens": tokens,
            "duration_s": round(duration_s, 2),
            "status": status,
            "fallbacks": _rec_fallbacks,
            "economics_routing": bool(getattr(args, "economics_routing", False)),
            "routing_policy": getattr(args, "routing_policy", None),
            "budget_usd": getattr(args, "budget_usd", None),
            "repo": repo,
            "base_commit": base_commit,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("a") as f:
            f.write(json.dumps(record) + "\n")
        print(json.dumps(record))

if __name__ == "__main__":
    main()
