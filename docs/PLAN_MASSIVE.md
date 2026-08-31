# Darwin + Bench — Massive Improvement Plan

Synthesized from 4 parallel research agents (2026-08-31): bench speed audit, darwin memory
internals, harness self-learning survey, and deep transcript dive on the night run
(576 tasks, darwin 259/288 vs vanilla 250/288 patched, darwin -24% tokens/-43% time/-27% cost in solo chain, but +138% cost under 3-way concurrency).

---

## Diagnosis: what's actually happening

### The SKIP/WIN story (paired, n=30 django chain)

Before the big matrix, the cleanest paired comparison was the initial django chain (30 django,
same tasks, deepseek, real swebench 22/30 both). Zooming into *per-task* wins:

| repo | SKIP (darwin fails, vanilla succeeds) | WIN (darwin succeeds, vanilla fails) | Verdict |
|---|---|---|---|
| django | 8 | 1 | darwin hurts |
| matplotlib | 3 | 8 | darwin helps |
| scikit-learn | 8 | 7 | wash |
| sympy | 1 | 13 | darwin helps a lot |

Harness is doing exactly what memory should: helping where knowledge transfers, hurting where
it doesn't. Django fixes are independent (StaticURL ≠ GroupBy ≠ Serializer ≠ Autoreload ≠ Choices)
— memory about sqlmigrate is noise for a static-URL task. Sympy fixes share symbolic-math
patterns — memory transfers.

**Transcripts confirm: no "I know this fix -> skip" deliberate bail.** Skips are:
- Distraction / webfetch commit hunting (12125, 11797)
- Tool-use failure: 7× `edit` with wrong oldString → all fail, then bash replace with identical old==new (11422)
- Permission block: `external_directory (/tmp/*); auto-rejecting` in 3/7 SKIPs
- Early stop with no edit (11905, 15s)
- False "fix already applied" (11019 — git log shows ancestor commit, model stops)

### The digest disaster (root cause)

`packages/opencode/src/index.ts:644-662` `chat.message` does ONE dynamic injection:
`project 120 + global 150 + notes 30 = 300 lines (~2k tokens)` as `<darwin_memory_digest>`
prepended to every turn. It's a **head dump** — first N lines of MEMORY.md.

- Cost: +1-2k tokens per turn even when global is empty template. Night pooled +138% cost, +99% tokens = this bloat under concurrency/rate-limit churn.
- Relevance: django's 25 tasks span ORM/migrations/admin/views/serializers — head contains whatever dream wrote first (sqlmigrate), injected into static/enums tasks as noise → overconfidence / distraction.
- Sympy narrow (matrices/polys) → head happens to be in-family, so it helps.
- The fix (query-scoped BM25) already exists in `packages/core/src/store.ts:114-170` + `query.ts` but is **never called** for injection — only on `darwin_memory search`.

### Memory isolation — already fixed, needs to be documented as such

Per-(repo,seed,arm) `DARWIN_HOME` (`eval/workdir/night/<slug>-s<seed>-<arm>/darwin-home`) means:
- **Between repos: already isolated** — django global doesn't leak to sympy.
- **Within a repo chain: deliberately shared** — fixed worktree path → stable project hash → memory accumulates for THIS repo (the treatment).
- Global memory within a chain accumulates cross-task drift (django ORM trivia polluting admin tasks).
- The nightly's darwin arms used 3 dream fires (tasks 5/10/15/20/25) writing to the correct project memory, but the chain's global grew alongside.

---

## Changes — Darwin side

### D1: Query-scoped BM25 injection (HIGHEST IMPACT) — do first

**What:** In `chat.message` (`index.ts:644-662`), replace head dump with:
1. Small anchor: `project 40 + notes 15` only.
2. On first user message, `reconcile(s, state.p)` then `s.search(problem_statement, {scope:"projects", scopeId: projectId(input.directory), limit:4, floor:0.25})` → formatHits (snippet 24-token windows from store).
3. Fall back to head if hits empty. Suppress global entirely for eval (set to 0), or gate global hits at floor 0.35.

**Files:** `index.ts:649-652`, `store.ts:114-155`, `query.ts:8`, `memory.ts:63`

**Expected:** night pooled cost 138% → <0%; django discordant 8:1 neutralizes; sympy +13 retained ≥+8. Needs query text at injection — move from `chat.message` (often empty messageID) to `experimental.chat.system.transform` with `input` carrying prompt, or inject as follow-up message part.

### D2: Relevance threshold + suppress global in eval

- Global digest 150 → 0 for chains. Global hits floor 0.35-0.40, project floor 0.20-0.25.
- `DARWIN_DISABLE_GLOBAL=1` or gate by `DARWIN_HOME` purpose.
- Files: `index.ts:650`, `store.ts:152`, `env.ts:20`

### D3: Wire `memoryDigestLines` + budgeted injection

- Split budget `total = opts.memoryDigestLines ?? 60` → e.g. project 40, global 0, notes 20 or project 40 + hits 4.
- Cap total digest ~800-1k tokens, not 300 lines. Enforce dream keeps ≤120.

### D4: Dream quality — full `dreamPrompt(p)` + verification

- Replace truncated night prompt (`run_night_matrix.sh:89-91`) with `opencode run --agent darwin-dream` which carries full `dreamPrompt(p)` + economics facet + Locate→Orient→Gather→Verify→Consolidate→Prune→Archive.
- Dedupe + cap 20 lines, one entry per concept, archive.md rotation, `[ses_xxx]` provenance.
- Frequency: 5 → 10 for heterogeneous django (25 tasks → 2 dreams vs 5) or size-triggered (>80 lines).

### D5: Reconcile before injection + section-aware head

- `reconcile(s, state.p)` at top of `chat.message` — cheap size-mtime walk.
- Structured slice: `## Rules` + `## Patterns` fully, truncate `## Discovered durable knowledge`.

### D6: Tool-permission + edit robustness

- Allow `/tmp/opencode/*` for darwin worktrees (blocked `distutils` shim in 11564/11964).
- Edit fallback to `write` after 2 failures.

---

## Changes — Bench side

### B1: Vanilla parallel (BIGGEST SPEED WIN)

- Darwin chains stay sequential (treatment). Vanilla: isolated mkdtemp worktrees, no DARWIN_HOME persistence → embarrassingly parallel `max_workers=8` via `xargs -P`.
- Current: 30-task chain ~169s×30 ≈ 85 min serial vanilla. Parallel 8 → ~11 min.
- Night matrix 12 pairs currently 6-9h → 2-3h with vanilla parallelized.
- Valid: vanilla is the flat control, order shouldn't matter. Paired by `_chain_idx` already.

### B2: Grading parallel under-utilized

- `max_workers` documented 8, scripts use 6/4. Unify to 8, shard `sweb_*.jsonl` into 3-4 chunks if needed. Image cache hygiene (`podman image prune -a` after batches).

### B3: Repo caching (keep as is)

- Shared `--shared` clones under `eval/workdir/night/shared/<slug>` + pair-sequential within slot + memory guard — already halves peak memory vs full clones. Pre-warm images via `swebench prepare_images`.

### B4: Model-level parallelism

- Flash `max` variant has higher rate limits — allows max_workers 8 without 429 fallback stalls. Keep `subagent_depth 3`.

### B5: Dream batching (optional)

- Background dream (spawn + reconcile async) rather than blocking, or reduce frequency DREAM_EVERY 5→10.

---

## Experiment plan for the next run

1. **Smoke (3 tasks, 1 repo, both arms, full darwin):** verify query-scoped injection boots, darwin memory digest now ~40+4 snippets not 300 lines, cost down.
2. **Full night v2:** repos≤8 seeds=3 jobs=8 with D1+D2+D5 + B1 (vanilla parallel). Same 30-task django chain + 23-task others.
3. **Stats:** same `night_stats.py` — primary: Δ resolved McNemar, secondary: cost/tokens Wilcoxon. Expect heterogeneous repo to cross threshold >0.25 for global.
4. **Hold Pro / Verified hard tier** until Lite generalizes (Pro is gated 401, Verified 500 available as hard tier via same SWE-bench/Verified dataset).

---

## What to do right now (before bed)

- Land D1+D2 (query-scoped injection, global suppressed) — highest impact, fixes django.
- Wire `memoryDigestLines` (D3) if trivial.
- Smoke the 3-task darwin+vanilla chain — confirm darwin still patches, cost < vanilla, and per-repo memory isolation holds.
- Then launch the full night v2 overnight.
