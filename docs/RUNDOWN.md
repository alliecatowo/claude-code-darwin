# Darwin Rundown: every MiMo-Code capability, its limits, and a verdict

For you to adjudicate before we build. Statuses: **KEEP** (build in darwin) · **REPLACE-native**
(use the host's own feature, don't build) · **TRIM** (build a reduced version) · **CONSOLIDATE**
(merge with another darwin piece) · **TOSS** (cut) · **DEFER** (later, not v1). "oc" = opencode
v1.18.25 plugin · "cc" = Claude Code plugin.

## 0. Corrections to earlier claims

- **Prompt caching:** opencode sorts tools alphabetically, collapses system to one stable
  message, and puts Anthropic breakpoints on the last-2 messages (AI-SDK path) or
  tools/system/latest-user (native path); OpenAI gets `promptCacheKey = sessionID` + `store:false`
  + stripped volatile IDs. A darwin child session that replays the parent's history with the
  same system bytes, same tool set (same agent + permission ruleset!), and same model **gets
  deep whole-history cache reads**. The old "no prefix-cache fork" concern is wrong in practice.
- **Goals:** Claude Code ships native `/goal` (session-scoped condition, small-model judge,
  auto-continue, restores on resume; judge is **tool-less**; **no budgets**). opencode v1.18.25
  has **no** goal system anywhere in-tree. So: goal is a *darwin feature on opencode*, a
  *native feature on Claude Code* (with one augmentation lane, §A4).
- **Fan-out/subagents:** both hosts now have solid native subagents (oc: task tool + child
  sessions + background flag; cc: Agent tool, fork-mode background default, 20 concurrent /
  depth 3). MiMo's custom actor system is redundant on both.

## 1. The coherent core (identity)

Darwin = **the self-evolution layer**, nothing else. Three loops around whatever host it's
plugged into:

1. **Remember** — persistent project/global memory + trajectory index, injected into context.
2. **Reflect** — dream (consolidate memory against raw history), distill (package repeated work
   into skills/commands/agents), evolve (standing instruction: notice repetition → write the
   extension).
3. **Act better** — the learned skill corpus + fast skill retrieval, and (host-dependent) the
   goal loop and workflows that give reflection long runs to learn from.

Everything MiMo-Code ships that isn't in service of those loops gets tossed or replaced with the
host's native version. That single filter makes the whole spec coherent.

## 2. Master decision table

### A. Self-evolution core — the point of darwin

| # | Capability | Limits as a plugin | Verdict |
|---|---|---|---|
| A1 | **Persistent memory** (FTS5 store, project/global/session layers, `memory` search tool, reconcile, injection on resume) | oc: greenfield — upstream has nothing (verified); fully plugin-buildable (~95%). cc: **native auto-memory exists** (`~/.claude/projects/<p>/memory/`, first 200 lines/25KB auto-loaded, typed notes) — don't duplicate the store | **KEEP on oc. On cc: UNIFY with native** — reuse native memory files as the store, darwin adds FTS search + dream-quality consolidation + cross-project global layer |
| A2 | **Trajectory history** (`history` tool: FTS over sessions/messages/parts, search + around) | oc: read-only queries against host SQLite — solid, but schema is undocumented/internal (pin + defensive queries). cc: transcripts are markdown/JSONL on disk — indexable read-only | **KEEP both** (trim: index-on-demand, not realtime) |
| A3 | **Dream** (consolidation agent: verify memory vs raw trajectory, merge/dedupe/prune, 200-line/10KB budget, provenance `[ses_xxx]`) | Expressible on both: oc hidden agent + `/dream` command; cc SessionEnd/PreCompact hooks + headless `claude -p` | **KEEP** — port prompt verbatim (it's excellent) |
| A4 | **Distill** (mine repeated workflows → skills/commands/agents; auto 30d) | Same mechanics as dream; cc additionally has the official skill-creator plugin's eval-driven iteration to steal ideas from | **KEEP** |
| A5 | **Evolve** (standing self-modification instruction + file conventions `.mimocode/{tools,hooks,skills,workflows}` + hot-reload next-turn) | The **real limitation**: upstream oc loads plugins/tools at startup (no next-turn hot-reload; MiMo added that in fork core). cc: no plugin code loading at all beyond MCP | **KEEP, redesigned**: darwin ships ONE gateway tool (`darwin_ext`) + interpreter; extensions in `.darwin/` are dispatched through it at runtime — hot-add without host reload. Hooks can't be hot-added on oc (limitation, documented); skills CAN (native scan dirs) |
| A6 | **Auto dream/distill cadence** (7d/30d, session-start trigger, excluded system sessions) | oc: no native scheduler → darwin keeps a tiny internal one. cc: native cron/routines/`/loop` exist | **KEEP** (oc: internal; cc: REPLACE-native cron, darwin just registers the jobs) |
| A7 | **Goal / stop-condition judge** (`/goal`, judge = same session model, evidence-based verdict, ≤12 re-entries, fail-open) | oc: idle-event → judge call → `prompt_async` reminder; near-parity, proven pattern (opencode-goal-plugin). cc: **native `/goal`** — but its judge can't run tools/read files and has no budgets | **KEEP on oc** (the only goal system it'll have). **cc: REPLACE-native + TRIM augmentation**: optional `agent`-type Stop hook for tool-using verification — the one thing native can't do |
| A8 | **Checkpoint system** (threshold triggers, writer subagent, 11-section checkpoint.md, watermark, rebuild boundary, microcompact, cycles) | ~80% parity via `messages.transform` + watermark store; custom `checkpoint` part type impossible (store watermark in darwin DB); no runtime compaction-threshold hook | **TRIM hard → DEFER**: keep only (a) memory injection on session start/resume (cheap, via system transform), (b) `notes.md` scratch convention. Full writer/rebuild machinery parked until the core loops ship. cc: REPLACE-native entirely (native compaction re-injects memory/CLAUDE.md post-compact; checkpoints/rewind native) |
| A9 | **`/context-limit` / `compaction.max_context`** | No runtime threshold hook on oc (only approximation: usage-watch → force `summarize`). cc: native `/autocompact <tokens>` | **TOSS on oc** (with A8 deferred it has no consumer). **cc: REPLACE-native** |

### B. Knowledge & skills

| # | Capability | Limits | Verdict |
|---|---|---|---|
| B1 | **Skill search/auto-load** (BM25 + exact/alias 1.0, auto-load ≥0.85, `/name` content injection, MAX 3, multi-skill orchestration plan) | Self-contained math, fully portable. oc native skills lack search/auto-load (manual/tool invocation only). cc native skills have `when_to_use` + auto-discovery | **KEEP on oc** (thin layer over native skill corpus; trim: drop zh/zht localized aliases + Han bigram unless you need them). **cc: REPLACE-native**, keep only the multi-skill orchestration-plan reminder if native feels worse |
| B2 | **Builtin skill corpus (27)** | Just content — portable anywhere | **TRIM 27 → ~6**: keep `skill-creator` (distill companion), `memory-search` (A1/A2 companion), `mimocode-docs`→`darwin-docs`, `evolve` (A5), `super-research` (only if workflows kept), `compose-next` (if D2 kept). **TOSS**: office suite (docx/pdf/pptx/xlsx/html-to-video), sales, product-design, data-analytics, learn-everything, arxiv, research-paper-writing, frontend-design, design-blueprint, grok-build, playwright, mate, loop, claude-code/codex delegation skills (darwin is the harness, not a delegator) |
| B3 | **Compose: legacy agent + 14 `compose:*` skills** | Portable (agent def + skills). Explicitly aimed at weaker models; MiMo themselves deprecate it | **TOSS** (frontier models + single contract beat the 14-step curriculum) |
| B4 | **`/compose-next`** (grill→spec→worktree→implement→verify→review→finalize) | One skill + docs-dir convention; needs question/task/subagent-ish tools — native equivalents exist on both hosts | **KEEP** (trimmed to native tools) — it's the spec-driven delivery discipline, cheap to carry |
| B5 | **Skills compat scan** (.claude/.codex/.agents/.opencode dirs) | oc native already scans `~/.claude/skills` + `.agents`; darwin adds the rest via config. cc: native | **REPLACE-native** + tiny config glue on oc |
| B6 | **Document-skill auto-suggest** | Tied to tossed office skills | **TOSS** |

### C. Orchestration & long runs

| # | Capability | Limits | Verdict |
|---|---|---|---|
| C1 | **Actor/subagent system** (`actor` tool, registry, waiter, inbox, watchdog, shared-session slicing) | Both hosts have native subagents; **background is the parity crux**: oc strips `background:true` from the task tool unless the env flag is on (§4b); CC has it native/default (fork mode, 20 concurrent, depth 3, `/tasks`, resumable) | **REPLACE-native, flag-gated**: model-facing subagents = native task tool (oc: requires the one darwin env flag; darwin raises `subagent_depth`→3 via config hook); darwin-internal orchestration = `client` child sessions (always background, flag-free). Toss the actor tool entirely. Result-injection parity: oc `<task state>` injection ≈ CC background-turn results |
| C2 | **Task tree** (T1/T1.1, events, archive, sidebar) | oc native todo is flat (no hierarchy); darwin tree is buildable (~90%). cc native TodoWrite | **TRIM → DEFER**: use native todo on both for v1; the tree only earns its keep with checkpoints (deferred). Revisit with A8 |
| C3 | **Workflows engine** (QuickJS sandbox, agent()/phase/log/file hooks, journal+resume, 4 builtins) | oc: no native equivalent — engine is self-contained (~95% parity; agent() maps to child sessions; the known MiMo schema-envelope bug #2253 we do NOT port). cc: **native dynamic workflows** (16 concurrent/1000 agents/resumable, plugins can ship `workflows/`) | **KEEP engine on oc** (trim builtins: keep `deep-research`; `fact-check`/`research-experiment` optional). **cc: REPLACE-native** — darwin ships its workflows as native `workflows/` scripts |
| C4 | **Cron/loops tool** | cc: native cron (50 tasks, restore on resume) + `/loop` + monitors. oc: none native | **TRIM on oc** to an internal scheduler feeding A6 (not a model-facing tool). **cc: REPLACE-native** |
| C5 | **Max Mode** (N parallel propose-only candidates + judge + replay) | Not implementable as plugin on either host (no multi-candidate request hook) — confirmed on oc; cc has nothing equivalent either (ultracode is the native analog direction) | **TOSS** (documented; revisit if either host grows a request-level hook) |
| C6 | **Try-best / Mix-of-Harness handoff** (stall detection → offer Codex/Claude CLI handoff) | Detector portable (oc: `tool.execute.after` heuristics; cc: PostToolUse); but it's about *leaving* darwin's host | **TOSS** |
| C7 | **Orchestrator mode / `session` tool / fleets** | Experimental in MiMo; native subagents + workflows cover the ground | **TOSS** |

### D. Context & memory-adjacent

| # | Capability | Limits | Verdict |
|---|---|---|---|
| D1 | **Memory instructions in system prompt** (layout teaching + "active recall protocol") | Trivial via `system.transform` (oc) / CLAUDE.md+memory (cc) | **KEEP** (part of A1) |
| D2 | **notes.md scratch convention** (main-agent scratchpad, drained by dream) | Just a file + prompt convention | **KEEP** (the one checkpoint-adjacent piece worth keeping) |
| D3 | **`/rebuild` (manual context reconstruction)** | Depends on A8 | **DEFER** with A8 |
| D4 | **Compaction customizations** (reserved/preserve budgets, compacting hook prompt/context) | oc native compaction config + `session.compacting` hook covers most; cc native | **REPLACE-native** |

### E. Host glue

| # | Capability | Limits | Verdict |
|---|---|---|---|
| E1 | **MiMo OAuth / xiaomi provider / X-Mimo-Source / import-from-Claude-Code / sunset+ToS dialogs** | — | **TOSS** (your call: no MiMo anything). Upstream provider/auth systems (models.dev catalog, custom OpenAI-compatible config, plugin auth hooks) cover the rest → REPLACE-native |
| E2 | **Voice input** (TenVAD + ASR + voice-control) | oc: TUI-plugin feasible (~80%) but big surface; ASR needs some provider. cc: no audio-input hook at all | **TOSS** for v1 (revisit if you actually want it — it's the single biggest TUI effort for the least evolution value) |
| E3 | **llm-server** (`/v1` OpenAI-compat, audio endpoints, tokens) | Not plugin-extensible on oc | **TOSS** |
| E4 | **exec/tool-script, GPT/Codex reduced ABI, apply_patch profile** | Serves Codex-style models; upstream handles model quirks itself | **TOSS** |
| E5 | **Session importers (claude/codex/opencode/external)** | Host concern | **TOSS** |
| E6 | **`darwin` CLI subcommands** (models/serve/attach/db/debug…) | Host CLIs exist | **TOSS** (maybe a tiny `darwin doctor` later: DEFER) |
| E7 | **edit exact-match/fuzzy, bash token-efficient pipelines, invocation_style (json/xml), mcp_tool_search, question-tool gating** | Overriding built-in tools by name works on oc but is a per-version maintenance treadmill; upstream tools are good now | **TOSS** all except: **TRIM keep** `bash` irreversible-delete guard (cheap `tool.execute.before`, real safety) |
| E8 | **Permissions extras** (`/skip-permissions` runtime toggle, 60s auto-reject, `bash_delete`) | Native permission systems + config cover both hosts | **REPLACE-native** (+ the E7 delete guard) |

### F. TUI / UX

| # | Capability | Limits | Verdict |
|---|---|---|---|
| F1 | Vivid visuals, starry bg, sounds, logo, themes | Decoration | **TOSS** |
| F2 | Prompt footer context-usage bar (`33.0K/260K↓`) | oc native sidebar already shows context %/cost; TUI slot possible | **TOSS** (native display is enough) |
| F3 | Ghost-text next-prompt prediction | Approximation only on oc; nothing on cc | **TOSS** |
| F4 | Model picker extras (frecency/favorites/variants), mode-lock, /force-agent | Native pickers fine | **TOSS** |
| F5 | `/workflows` TUI dialog + run tree | Only if C3-oc ships; small TUI-plugin surface | **DEFER** (CLI/status tool first) |
| F6 | Timeline, fork-from-timeline, subagent footer/nav, i18n (7 locales) | Native on both hosts now | **TOSS** (i18n: toss unless you need zh — you don't seem to) |

### G. Config & env — the "90 env vars" answer

MiMo's ~90 env vars are **internal dev kill-switches and tuners**, not user surface. A plugin
needs almost none because feature toggling moves into darwin's options object
(`["darwin", { … }]` on oc; plugin `userConfig` on cc). Darwin keeps **at most five**:

| Env var | Why |
|---|---|
| `DARWIN_HOME` | data dir override (memory/db location) |
| `DARWIN_DISABLE` | kill-switch: all darwin behavior off (debugging/emergency) |
| `DARWIN_DISABLE_AUTO` | keep manual /dream //distill, disable auto cadence |
| `DARWIN_LOG_LEVEL` | plugin log verbosity |
| `DARWIN_DB` | sqlite path override (rare; could fold into HOME) |

Everything else (skill-search tuners, checkpoint knobs, experimental gates) either dies with its
feature or becomes an options field with a documented default. Config surface: one options
object, JSON-schema'd, ~15 keys total after the cuts.

## 3. darwin v1 after the cuts

**Both hosts, shared core package:** memory (A1) + history index (A2) + dream (A3) + distill
(A4) + evolve-via-gateway (A5) + auto cadence (A6) + trimmed skill corpus incl. compose-next
(B2/B4) + multi-skill reminder (B1-oc).

**opencode plugin adds:** goal loop (A7) · workflows engine + deep-research (C3) · internal
scheduler (A6) · skill auto-load layer (B1) · bash-delete guard (E7) · memory injection on
resume (A8-lite).

**Claude Code plugin adds:** almost nothing extra — native gives it /goal, workflows, cron,
subagents, auto-memory; darwin wraps them (UNIFY with native memory, optional tool-using Stop
judge, native workflows scripts, SessionEnd dream triggers).

Rough size signal: core ~3-4k LoC, oc plugin ~2k, cc plugin ~800, skills content ~1.5k.
MiMo-Code by contrast is a ~500k LoC product — that's the coherence dividend.

## 4. Open judgment calls — RULINGS (2026-08-29)

1. **Voice** — **TOSSED**.
2. **Workflows on oc** — **KEPT** (engine + `deep-research`; long-horizon is core).
3. **compose-next** — **KEPT** in v1 (+ 4 compose sub-skills: ask/review/verify/tdd).
4. **Task tree + full checkpoints** — **PARKED** (revisit post-v1 via §5 lane).
5. **Skill corpus** — **27→9**: compose-next, skill-creator, memory-search, evolve,
   super-research, compose-{ask,review,verify,tdd}.
6. **zh/zht aliases + Han tokenization** — **DROPPED**.
7. **Background flag policy** — **ACCEPTED**: one env line
   (`OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=1`) + doctor nag + degraded mode.

New locked direction — **phoenix**: darwin can reload itself without user intervention.
oc: TUI embeds server in a worker; `SIGUSR2` to the TUI process = in-place config+plugin reload
(`tui.ts:219`, `worker.ts:63-71`); fallback = detached `opencode run` fresh process via `$`
(fresh world guaranteed: config cached forever per process/instance). CC: `claude --bg
--name darwin-gen<N> "<bootstrap>"` spawns a supervised background session (fresh process picks
up new plugin files); `/loop` tasks carry over into backgrounded sessions (durable local
daemon). Handoff = generation-numbered JSON + note in darwin memory; guards: max generations,
min interval, single-fire lock, idempotent handoff (CC supervisor may re-dispatch).

Resolved by research (no longer open):

- **`remember`** — NOT Anthropic's: community plugin (Digital-Process-Tools) accepted into the
  official catalog; **proprietary "Community License" (no modification/redistribution) → cannot
  vendor or build on it.** Not better than native for facts (native auto memory covers typed
  user/feedback/project/reference notes, free, on by default); remember's value is *episodic*
  recency-tiered logs (now→today→recent-7d→archive, Haiku-compressed, injected at SessionStart,
  separate store, needs Python+jq+cents/day). **Verdict: no dependency, no vendoring** — darwin's
  memory+dream already covers episodic consolidation with local FTS and zero deps. Steal the
  *idea*: recency-tiered memory layout (recent window + archive) fits dream's output naturally.
  If a user runs both: no file conflicts, but double SessionStart injection cost — doctor notes it.
- **CC plugin pairings** — official catalog is Apache-2.0; plugin `dependencies` auto-install but
  cross-marketplace deps are blocked by default (darwin ships its own marketplace entry), so:
  **skill-creator** (eval-driven skill iteration — parallel with/without-skill runs, assertions,
  benchmark deltas) → *borrow its grader/analyzer/comparator patterns* (Apache-2.0, attribution)
  into distill; soft-pair in docs ("works great alongside"). ralph-loop = superseded by darwin
  goal; claude-md-management = superseded by darwin memory; hookify = a distill *target* (emit
  guardrails into it); plugin-dev = useful at build time, not a runtime dep.

## 4b. Experimental settings: can the plugin self-enable them?

- **Config-shaped (`experimental.*`, `subagent_depth`, `compaction.*` in opencode.json schema): YES.** Plugin `config` hook mutates the live merged config at init, before any consumer reads it.
- **Env-shaped (`OPENCODE_EXPERIMENTAL_*` RuntimeFlags): NO.** Flags resolve via `ConfigProvider.fromEnv()` and the service is memoized before external plugins load (`plugin/index.ts:132`); a plugin factory runs too late. Exceptions: flags read from `process.env` at call time (rare) — classify per-flag.
- **Design rule (revised): darwin depends on exactly ONE env flag.**
  - **`OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=1`** (or umbrella `OPENCODE_EXPERIMENTAL=1`) is **required for subagent parity**. Why it can't be worked around: without it, opencode *strips the `background` param from the task tool's JSON schema* (`task.ts:362`) — the model literally cannot choose background; every model-facing subagent blocks the main turn (MiMo's actor `spawn` is background-by-default, and CC's fork mode makes background the default there — without the flag, oc-darwin and cc-darwin diverge hard).
  - **Detection:** darwin reads `process.env` at load AND probes the live `task` tool schema via `tool.definition` — definitive on/off check at runtime.
  - **Setup UX:** one line in shell profile + `darwin doctor` verifies and nags (with the exact line to paste). No installer hacks (writing to shell rc files is invasive).
  - **Degraded mode (flag off):** darwin-internal orchestration (workflows/dream/distill) still runs fully background via `client.session.create` + `promptAsync` — only *model-facing* subagents degrade to blocking. Doctor explains; skills instruct the model to prefer few/large subagents when degraded. No custom replacement tool in v1.
  - **`subagent_depth`: raised to 3 via config hook** (config-shaped; matches CC's default depth; oc default is 1).
- Claude Code: plugin `settings.json` supports only `agent`/`subagentStatusLine` (no env) — darwin needs no CC flags (background subagents are native/default there).

## 4c. opencode ecosystem: pair / borrow / avoid

Nothing justifies a hard dependency (all are plugin-factories, not libraries; darwin standardizes
on `@opencode-ai/plugin` + `zod` only). Rules and standouts:

- **Interop mechanics darwin uses:** detect co-installed plugins via the `config` hook (resolved
  config includes the `plugin` array); observe foreign tools via `tool.definition`; namespace
  everything `darwin_*` (tool registry is silent last-writer-wins on collisions); hooks run
  sequentially with shared output objects — always try/catch, push (never replace) in
  `chat.message`, use `experimental.session.compacting` for context-*push* only.
- **Compaction is the #1 multi-plugin conflict** (supermemory triggers at 80%, micode
  auto-compacts at 50%, magic-context *replaces* compaction, OMO hooks it 3×). Darwin: never
  replace the compaction prompt; gate compaction-adjacent behavior on the conflict scan; ship
  documented coexistence switches.
- **@cortexkit/opencode-magic-context** — closest existing thing to darwin (local embeddings +
  FTS fallback, "dreamer" overnight consolidation, replaces compaction). It's (a) the design
  benchmark and (b) the #1 collision: darwin detects it and defers memory (keeps
  skills/distill/goal) — reciprocating its own self-disable pattern.
- **opencode-goal-plugin** — borrow its hardening wholesale for darwin's goal loop (in-place
  command-parts mutation, idle-supersede rechecks, durable per-turn claims, no-tool-call/
  no-progress heuristics, fail-closed child-session verifier, shard+ledger+lease persistence,
  untrusted `<goal_objective>` wrapping). Never run two auto-continue loops: if detected,
  darwin's goal defaults off. Watch its `<2` engines pin — experimental hooks churn.
- **opencode-supermemory** — hosted embeddings vs darwin's local FTS: interoperate, don't
  compete. Adopt its `learned-pattern` taxonomy + `<private>` redaction; doctor warns on double
  recall-injection.
- **Optional pairs (document, don't depend):** opencode-scheduler (dream-on-cron),
  opencode-background-agents / daytona (isolated dream/distill runs), opencode-pty,
  opencode-telemetry (metrics).
- **Avoid code from:** @openspoon/subtask2 (PolyForm Noncommercial), oh-my-opencode (SUL-1.0
  custom license; historically crash-prone) — ideas fine, code not. opencode-skillful is archived
  (superseded by native skills, which darwin targets anyway).
- **Differentiation check:** 15+ memory plugins exist; none combine local FTS/BM25 + dream
  consolidation + distill packaging + evolving skill corpus + goal loop in one package. Darwin's
  identity stays defensible; "just memory" would not be.

## 5. Lane: opencode extension points beyond the plugin API (standing investigation, not a pivot)

Known mechanisms short of forking, to be mapped exhaustively later:

- **File-based tools**: `.opencode/{tool,tools}/*.ts` — loaded without a plugin entry; darwin's
  gateway tool could alternatively live here per-project.
- **TUI plugin API** (`@opencode-ai/plugin/tui`): keymaps, routes, slots, dialogs, themes —
  darwin's entire possible UI surface.
- **Workspace adapters**: `experimental_workspace.register(type, adapter)` in PluginInput —
  control-plane workspaces; unknown ceiling, worth probing.
- **Runtime env flags**: `OPENCODE_EXPERIMENTAL_*` (background subagents, native LLM path,
  plan mode…), `OPENCODE_EXPERIMENTAL_NATIVE_LLM` cache-policy differences — some unlock
  behavior plugins can't request; full flag inventory pending.
- **MCP at runtime** (`POST /mcp`) — dynamic server add from a plugin.
- **v2 plugin system** (`@opencode-ai/plugin/v2/*`, effect-based, domain transforms incl.
  agent/catalog/command/skill + aisdk hooks) already in-tree — if/when it becomes the default,
  darwin should migrate; its domain-transform shape may make A5's hot-reload limits disappear.
  **This is the most promising beyond-plugin lane.**
- **Embedding via SDK/server**: run opencode server headless under darwin's control (ACP, sdk-next
  HttpApi host) — heavy, but sidesteps every hook limitation if ever needed.
- Open questions for the lane: can plugins register server routes (no, today)? codemode package
  capabilities? does `tool.definition` allow per-agent tool gating sufficient for evolve-style
  toolset changes without reload?
