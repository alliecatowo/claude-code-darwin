# Darwin Benchmark Book

Every eval we've built, its exact protocol, and all results — so comparisons over time are
against recorded numbers, not re-runs. Newest/most important first.

---

## B1: Repo-Chain Longitudinal (THE self-learning test) — 2026-08-30

**Question:** does darwin's persistent memory + dream consolidation improve results on the SAME
codebase as tasks accumulate, vs vanilla re-learning each task?

**Spec** (all in-repo, reproducible):
- **Dataset:** `eval/datasets/django_chain.json` — first 30 SWE-bench Lite `django/django`
  instances ordered by `created_at` (oldest first), built by `eval/scripts/make_django_chain.py`.
- **Protocol:** `./eval/scripts/run_repo_chain.sh --tasks 30 --timeout 1500`
  - Two arms in **parallel**, both sequential over the chain:
    - **darwin**: fixed worktree `eval/workdir/chain-darwin/django` (stable project hash →
      memory accumulates for THIS repo), persistent `DARWIN_HOME`, plugin loaded via
      `.opencode/plugin/darwin.ts` shim. **Dream fires in-situ every 5 tasks** (task 5/10/15/20/25)
      in the same worktree, MEMORY.md ≤120 lines.
    - **vanilla**: same fixed-worktree mechanics, plugin off, fresh `DARWIN_HOME` every run.
  - Per task: `run_task.py --worktree-path` resets worktree to `base_commit`
    (`git checkout -f` + `clean -fdx -e .opencode -e .darwin`), prompt =
    problem_statement + anti-yak-shave tail ("Do NOT install dependencies… patch will be
    verified automatically"), `--format json`, 1500s timeout.
  - Model: `llmgateway/deepseek-v4-flash`. Grading: applies-check inline
    (`eval_patch.py`), **real swebench test-execution available** (gold-verified:
    `SWE-bench/SWE-bench_Lite` org + podman `DOCKER_HOST=unix:///run/user/1000/podman/podman.sock`,
    resolved 1/1 on gold patch).
- **Claim criteria:** darwin's rolling 5-task patched-rate trends up / cost-per-task trends
  down while vanilla stays flat; per-index PAIRED comparison (same task, both arms) is the
  primary signal — absolute segments vary with task difficulty.

**Results (2026-08-30, deepseek-v4-flash, applies-grading):**

| metric | vanilla | darwin | delta |
|---|---|---|---|
| patched | 30/30 | 29/30 | parity |
| avg tokens | 52,423 | 39,880 | **-24%** |
| avg duration | 169s | 96s | **-43%** |
| total cost | $0.2952 | $0.2160 | **-27%** |

Headline: at patch parity, darwin is -24% tokens / -43% wall time / -27% cost (n=30/arm).
Mechanism consistent with memory injection reducing exploratory turns. No upward patched-rate
slope (both arms ~saturated; grading headroom exhausted) — the learning claim at this scale is
EFFICIENCY, not resolution. One darwin miss (task 24, after dream #4) — autopsy pending
(recurring overconfidence pattern?). Real swebench test-execution grading running; correctness
delta is the open question (if darwin's patches also resolve more, both claims land).

**Gotchas learned (do not re-learn):**
- Fresh-mkdtemp worktrees break darwin (project hash = sha256(path)); chains need FIXED paths.
- Global-memory digest was capped at 20 lines (starved treatment) → now project 120 / global 150.
- "Run tests to verify" prompt causes native-repo build yak-shaves → anti-yak-shave tail.
- npm plugin spec `@darwin/opencode-plugin` is unpublished → opencode silently skips it;
  darwin arms MUST load via file shim (run_task writes it automatically).
- `princeton-nlp/SWE-bench_Lite` rows lack `image` col for swebench 5.x → use `SWE-bench/` org.

---

## B0: Round-based Longitudinal (superseded by B1)

Same 5 tasks re-run 3 rounds, both conditions. Two runs:
- `eval/results/longitudinal-no-bridge/`: treatment broken (memory trapped per-worktree).
  vanilla 1→2→2, darwin 2→2→2 (all /5).
- `eval/results/longitudinal/`: bridge fix (dream→global memory) confirmed knowledge flowed;
  still flat: vanilla 2/2/2, darwin 2/2/2 (/5). Verdict: **no effect on cold-start single-issue
  tasks** — motivating B1. darwin was -15% cost ($0.183 vs $0.214, n=15, noise-range).
- One pollution anecdote: round-2 darwin produced NO patch on astropy-12907 — the only run
  that had just received dream knowledge describing that exact fix. Watch for
  memory→overconfidence.

## Smoke suites (health checks, not claims)

- `eval/scripts/run_parallel_smoke.sh` — 2 tasks × 2 arms parallel; anti-yak-shave prompt era:
  4/4 patched, ~$0.003/task, ~1 min/task.
- Chain smoke: 3/3 patched, $0.014→$0.012→$0.009.

## Harness inventory

| File | Purpose |
|---|---|
| `eval/harness/run_task.py` | one task: worktree (temp or fixed-path chain mode), config/shim, run, patch, reasons |
| `eval/harness/run_task_claude.py` | same for Claude Code (`claude -p`) |
| `eval/harness/eval_patch.py` | grading: real swebench → degraded applies-check |
| `eval/scripts/run_repo_chain.sh` | B1 orchestrator |
| `eval/scripts/run_longitudinal.sh` / `_v2.sh` | B0 (v2: darwin first, vanilla parallel) |
| `eval/scripts/run_parallel_smoke.sh` | smoke |
| `eval/configs/experiments.yaml` | shape matrix (A/B/C/ceilings) incl. economics-routed mixture |
| `eval/scripts/run_matrix.sh` / `compare.py` | matrix runner + Wilson-CI comparison |
| `eval/Dockerfile` / `docker-compose.yml` | containerized runs (host-config isolation) |

## Planned / not yet run

- **Real swebench re-grade** of all chain predictions (free, container time only).
- **Ceiling arms** (`ceiling-sonnet*`, `ceiling-deepseek*` from experiments.yaml) when budget allows.
- **Claude Code cross-harness** (`run_task_claude.py`) once the CC plugin exists.
- **Single-long-session eval** (goal/phoenix/notes under one multi-hour task).
