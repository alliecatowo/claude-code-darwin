# Handoff: Darwin × Polyphony × Our Own Harness

**Written:** 2026-08-30, session on architecture direction
**For:** whichever agent (or human) picks this up next — this is the state of the thinking, not a spec.

---

## Where this session started

Frustration with both available harnesses:

- **OpenCode**: open source but a weak base — no real backgrounding, core behavior (loops, compaction, subagents) not configurable enough, and the plugin boundary fights anything ambitious. (Also: who ships a coding agent in 2026 without first-class detached runs.)
- **Claude Code**: more sophisticated (teams, background subagents, checkpoints) but closed source and worse to extend in the places we care about.

Darwin-as-plugin is a port of MiMo-Code's self-evolution semantics onto OpenCode. It works, but it's a guest in someone else's house: hooks need host restarts, subagent semantics are the host's, background execution is an env flag, and the agent can't touch its own loop.

**Conclusion reached:** stop-gap the plugin (issues #1–#5, filed), and design the real thing: our own harness, built from the ground up, *optimized to be a worker inside the Polyphony system* — not another general-purpose CLI.

---

## The three pieces

### 1. Polyphony — the deterministic executor (exists, Elixir, `~/develop/polyphony`)

A GitHub-first implementation of the Symphony spec: polls GitHub Issues/Projects, computes eligible work from dependencies/state, spawns per-issue workspaces, runs a coding agent, retries with backoff, reconciles, handles stacked PRs, continuation turns, slice planning. ~3.5k-line orchestrator, deliberately stupid.

**Core principle: Polyphony stays stupid.** It never answers semantic questions. It answers exactly one: *"given current project state, what executable work is eligible right now?"* GitHub Projects is the blackboard. No LLM in the loop. No custom orchestration database.

### 2. The authority layer — the meta layer on top (concept brief, this session)

The big idea from the concept brief (preserved in full in the conversation, distilled here):

- **Two kinds of orchestration.** Semantic (what should the architecture be?) needs intelligence. Mechanical (is ticket B unblocked?) is deterministic. Never spend an LLM on mechanical.
- **Delegate authority, not tasks.** Sometimes the parent doesn't know what X should be — delegate *temporary semantic ownership* of a scope (issue/epic/milestone/project). The authority agent lives inside the problem, recursively delegates, implements the high-information blockers itself, then **compiles its understanding into durable project state** (issues, deps, milestones, stacked PRs, code, acceptance criteria) and *disappears*.
- **Authority follows uncertainty.** Worker/Senior/Lead are capability classes mapped to models via config (Worker→cheap high-throughput, Senior→strong impl/review, Lead→frontier semantic). Hierarchy is structural; authority is dynamic. Frontier intelligence returns only when new semantic uncertainty appears — detected deterministically (escalation-labeled issues, milestone-reviewable states, scheduled rituals).
- **The killer determinism insight** (the one to keep): *the agent finishes and applies a patch; a state machine does the commit; commits run validation hooks; on failure a brand-new cheap session gets "fix failing tests: [output]" with no prior context.* Both variants (with/without prior context) have merit, but the clean-session default:
  - kills the "did that pass? let me wait for tests? run it again?" agency-burn,
  - gives the fixer a fresh cheap context not bogged down by the previous reasoning,
  - makes validation a deterministic gate, not an LLM judgment.
- **Budget & throughput.** Someone with 10 unrelated projects should get max quality-tasks-per-token at constant throughput, with the ability to step in and reprioritize at any tick — instead of blowing the budget on whatever came first. Token budget gates dispatch; authority events gate expensive models; portfolio Lead (days/weeks cadence) reprioritizes across projects by mutating project state.
- **Darwin = the meta-meta loop.** It doesn't execute projects; it improves the *organization* (harness prompts/skills/context policy, model economy, delegation policy, Polyphony knobs). Crucially: **learning happens between frozen epochs, not by hot-editing the live harness.** Epoch N runs frozen harness Hn; agents surface friction as tickets/proposals; at retro, a retro-authority Lead reviews traces + costs + replay evals and approves Hn+1 (as a harness PR — git is the evolutionary lineage). Never mutate execution beneath itself.

### 3. Our own harness — the worker runtime (the decision this session)

**The reframe that matters:** we don't need a better Claude Code. We need a **worker optimized for the Polyphony contract**. Symphony assumed Codex app-server as worker. OpenCode was a tolerable stand-in. Both are wrong for this system because they're built to *be* the whole world — interactive-first, immortal sessions, agency spent on waiting for tests, context accumulating until compaction.

The worker we want has this contract:

```
Polyphony dispatches → fresh bounded session
  input:  work packet (issue, acceptance criteria, base branch, authority level)
  runs:   cheap model, explicit task, hot tools from .darwin/
  output: PATCH (diff + summary + evidence), never live repo truth
deterministic commit → validation hooks
  pass → PR / state advance (Polyphony)
  fail → NEW clean session: "fix failing tests: [output]" (no inherited context)
```

What that worker needs, ranked:

1. **Library-first / embeddable** — Polyphony (or its successor) spawns it per issue; startup must be milliseconds, sessions disposable, no REPL pretensions.
2. **Patch-native output** — the unit of work is a diff against the workspace, with evidence. The state machine owns commits. This single boundary eliminates most "agency tax."
3. **Fresh-session ergonomics** — cheap cold start, digest injection (darwin memory), bounded effective context (below nonlinear pricing tiers — encode economic ceilings per profile, e.g. Worker ~120–160K, not the advertised max).
4. **Hot tools/skills** (evolution surface) — but frozen per epoch at the org level; Darwin promotes changes between epochs, never mid-flight.
5. **Multi-provider** — model follows authority class; swap models per worker profile without touching project state.
6. **Orchestration primitives if needed** — Lead sessions may recursively delegate; peer messaging nice-to-have but the tracker already carries most coordination.

### Base candidates evaluated (2026-08 research)

| Option | Verdict for this system |
|---|---|
| **Cersei** (Rust SDK) | Strongest candidate. Library-first, 34ms/5.8MB, multi-provider, graph memory (Grafeo, 98µs recall), sub-agent orchestration, **AgentRL: a run→fail→trace→sandbox→promote→register self-evolution loop** — the closest existing thing to Darwin's evolution engine as a library. Risk: pre-1.0, vendor early. |
| **Pi** (`earendil-works/pi`, TS) | Best extension semantics anywhere: hot-reload extensions, agent can rewrite its own tools via SDK, session trees, RPC/SDK modes, ~350-token prompt proves minimalism works. Also: we already ported its philosophy into darwin's skills. Weakness for us: interactive-TUI-first design; we'd be the ones bolting on patch-native worker mode. |
| **Sagent** (Python) | Hot self-mutation (AgentSelf: model/context/thinking mid-session), Erlang-style agent mailboxes, recursive spawn, async REPL. Elegant, but Python (user's explicit "who runs Python") and no Rust deployability. |
| **Stay on OpenCode** | Rejected as end-state. Fine as the stop-gap host while issues #1–#5 land. |

**Leading plan (not yet committed): Rust port.** Either build on Cersei or take Pi's *architecture* (minimal loop + hot TS-style extensions) and implement it natively in Rust alongside a Rust rewrite of the Polyphony orchestrator — one language, one daemon family, library all the way down. Pi's monorepo layout (`pi-ai` / `pi-agent-core` / coding-agent / TUI as separate packages) is the structural template. Decision still open: **build on Cersei (faster, riskier) vs. clean-room Pi-style minimal core in Rust (slower, fully ours)**. Lean: start from Pi's design, steal Cersei's AgentRL pattern for the Darwin loop, keep the Polyphony contract as the north star.

---

## What exists today (inventory)

- **darwin plugin** (`~/develop/claude-code-darwin`): memory (MEMORY.md×3 scopes + SQLite FTS5/BM25), goal judge (evidence-based verdicts, 12 re-entries, fail-open), phoenix handoffs (generation-numbered, SIGUSR2 reload / detached spawn), scheduler (file-backed, cross-proc lock), economics (turn costs, cache-hit, models.dev, budget advisories), host-history reader, skills corpus (evolve, super-research ×8 modes, compose-*, skill-creator, memory-search), SWE-bench-lite longitudinal eval harness.
- **polyphony** (`~/develop/polyphony`): full Symphony spec in Elixir + stacked PRs, slice planner, delivery controller, webhooks, dashboard/HTTP API. Worker = Codex app-server (this is what we replace).
- **Filed this session:** darwin issues #1 (repetition detector → auto-propose tools), #2 (evolution persistence → memory/dream), #3 (research-mode phoenix checkpointing), #4 (background dispatcher + budget interlock), #5 (darwin_evolution observability tool).

## Open decisions (in order)

1. **Worker base: Cersei vs clean-room Rust vs Pi-SDK-embedded.** Criteria: patch-native worker mode, per-profile model/context economics, embed speed, how much of AgentRL we'd rewrite anyway.
2. **Language for the final system.** Rust-leaning (one family: orchestrator + workers + daemon). Elixir polyphony stays until the Rust orchestrator reaches parity; spec (`SPEC.md`) is language-agnostic on purpose — reimplement, don't port line-by-line.
3. **Validation state machine ownership** — lives in Polyphony (commit → hooks → fix-session dispatch) since it's deterministic orchestration; the worker only ever emits patches. Define the wire: patch format, evidence schema, fix-session prompt template.
4. **Epoch mechanics** — what exactly is frozen in Hn (prompts, skills, model map, escalation thresholds), where the harness PR lives (in darwin repo? per-project `.darwin/`?), how replay evals gate promotion.
5. **Authority-level encoding** — GitHub Projects field (`Authority: Worker/Senior/Lead`) + escalation as structured state (label or blocks-relationship), per the concept brief §9–13. Needs a Polyphony extension spec.

## Non-negotiables (from the brief, keep these)

- Plans live in projects, not planners.
- Project state orchestrates work; agents temporarily govern project state.
- Route *authority* to models, not tasks.
- Frontier intelligence compiles ambiguity into durable state, then disappears.
- Deterministic mechanisms wherever the decision is already encoded; model intelligence only where the decision is unknown.
- Darwin learns between epochs, never by mutating live execution beneath itself.
- No workflow-DSL framework, no agent-role zoo, no second orchestration database.

---

*Next session: start from Open decision #1. Read `~/develop/polyphony/SPEC.md` §7 (state machine) and §10 (agent runner contract) first — the worker harness is the replaceable half of §10, and the validation state machine is the extension to §7.*
