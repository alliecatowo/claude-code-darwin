# Darwin Plan (locked 2026-08-29)

Identity: darwin = the **self-evolution layer** as a plugin. Three loops: **Remember** (memory +
history index) · **Reflect** (dream / distill / evolve) · **Act better** (skill corpus + search,
goal loop, workflows, compose-next). Rulings in docs/RUNDOWN.md §4.

## Layout

```
packages/core        @darwin/core      — store (SQLite FTS5), memory engine, scheduler,
                                         goal judge, handoff (phoenix), history reader
packages/opencode    @darwin/opencode  — opencode v1 server plugin
packages/skills      bundled de-branded corpus (9 skills)
packages/claude      phase 2 — Claude Code plugin (.claude-plugin, hooks, MCP shim)
```

## v1 scope (opencode)

1. Core: env/paths (`DARWIN_HOME`), SQLite store (memory + FTS5 + kv + goals + cadence),
   reconcile (size-mtime fingerprints), BM25 search w/ score floor, recency-tiered MEMORY.md,
   durable scheduler (file tasks + lock + missed catch-up), goal judge prompt/verdict,
   phoenix handoff (generation JSON), read-only host trajectory reader.
2. Plugin: config hook (subagent_depth→3, hidden agents darwin-dream/distill/judge, commands
   /dream /distill /goal /darwin, skills.paths += corpus + `.darwin/skills`); tools
   `darwin_memory` `darwin_history` `darwin_doctor`; system-transform memory instructions
   (cache-stable); first-message memory digest (chat.message); goal loop (idle → judge child
   session → auto-continue ≤12, fail-open); auto dream/distill cadence (7d/30d) via hidden
   child sessions; phoenix restart (SIGUSR2 to TUI, fallback detached `opencode run`).
3. Setup contract: one env line `OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=1` (doctor-checked,
   degraded mode without it); everything else in `["darwin", {...}]` options.

## v1 scope (Claude Code, phase 2)

UNIFY with native auto memory (reuse native files as store + darwin FTS/search/consolidation);
REPLACE-native: /goal, workflows (ship as native `workflows/`), cron/routines, subagents;
SessionEnd/PreCompact hooks → dream/distill; MCP server (`mcp__darwin_*`) for memory/history;
phoenix via `claude --bg` + generation handoff; compose-next + corpus as plugin skills.

## Post-v1 candidates

Workflows engine (QuickJS) on oc if deep-research proves needed natively · task tree +
checkpoint-lite (revisit) · darwin_ext gateway (hot tools) · CC agents-screen deep integration.
