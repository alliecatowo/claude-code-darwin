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

**Results (2026-08-30, deepseek-v4-flash, REAL swebench test-execution grading, n=30/arm):**

| metric | vanilla | darwin | delta |
|---|---|---|---|
| **resolved (real tests)** | **22/30 (73%)** | **22/30 (73%)** | **parity** |
| avg tokens | 52,423 | 39,880 | **-24%** |
| avg duration | 169s | 96s | **-43%** |
| total cost | $0.2952 | $0.2160 | **-27%** |
| infra failures | 0 | 0 | grading trustworthy |

**Verified claim: darwin produces identical resolution quality (22/30 both arms, real test
execution) while using 24% fewer tokens, 43% less wall time, and 27% less money.**

Overlap analysis: 19 tasks resolved by both; 3 darwin-only (11564, 11630, 11742) vs 3
vanilla-only (11019, 11620, 12184) — same count, DIFFERENT sets: the arms genuinely diverge
strategy-wise (and note: 11019/11564/11620 are the exact tasks the dream wrote knowledge for —
darwin won 2 of those 3, lost 1). No upward learning slope; at 73%-resolve the chain has
headroom for a resolution claim on harder tasks (Pro-tier) or longer chains (114 django total).

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

## Night matrix (2026-08-31) — repo-dependent signal, 576 tasks

**Pooled:** darwin +138% cost, +99% tokens, -8% time (Wilcoxon p≈0, all paired n=288) — cost/token bloat from 300-line head dump, time still slightly faster. Hidden by pooling heterogeneous repos; per-repo tells the truth:

| repo | SKIP (darwin fails, van wins) | WIN (darwin wins) | Verdict |
|---|---|---|---|
| django | 8 | 1 | darwin hurts |
| matplotlib | 3 | 8 | **darwin helps** |
| scikit-learn | 8 | 7 | wash |
| sympy | 1 | 13 | **darwin helps a lot** |

**Single-issue vs same-codebase signal holds:** Django fixes are independent (StaticURL ≠ GroupBy …) — head noise hurts. Sympy fixes share symbolic patterns — head happens to be in-family. The digest is the root cause: `project 120 + global 150 + notes 30 = 300 lines` injected blindly every turn (see `packages/opencode/src/index.ts:644-662`). BM25 store (`store.ts:114`) exists but is never called for injection. Night pooled +138% is the bloat; solo chain -27% cost was clean.

## Planned / next (in PLAN_MASSIVE.md)

- **D1 (highest impact):** query-scoped BM25 injection (project hits, floor 0.25, ≤4 snippets + 40-line skeleton) replacing head dump.
- **D2:** suppress global in eval (threshold 0.35, or `DARWIN_DISABLE_GLOBAL=1`).
- **B1:** vanilla fully parallel (embarrassingly parallel, -4-7× wall time); darwin stays sequential.
- Ceiling arms, Claude Code cross-harness, single-long-session eval — same as before.

## Night matrix operational log (2026-08-30/31)

- **OOM**: 10 concurrent chain jobs × full clones × opencode processes killed the box →
  topology now: shared repo cache (`--shared` clones), pair-level jobs (darwin→vanilla
  sequential), 3 slots, 2.5GB memory guard, 20s stagger.
- **Disk quota**: swebench per-instance images accumulate (~22GB) → `podman image prune -a`
  after grading batches; /tmp tmpfs also fills (venvs, e2e artifacts).
- **Bash stdin bug**: background jobs inherit the launcher's stdin (the joblist file) and
  consume it → launcher exits early. Fix: `... < /dev/null &` on every background job.
- **Orphan processes**: killing a runner orphans in-flight run_tasks (they keep writing to
  the old results dir). Kill by exact PID tree; stats pair by `_chain_idx` with
  last-write-wins so interleaved files self-heal.
- **Runners create their own timestamped OUT dir per invocation** — "resume" requires the
  resume guard (skip complete cells), not directory reuse. Monitor must target the newest dir.
- Budget: all-time eval spend ~$2.60 through 22:26; night matrix projected ~$6 total.
