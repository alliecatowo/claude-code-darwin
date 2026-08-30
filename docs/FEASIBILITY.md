# Darwin Feasibility: MiMo-Code as a Plugin

Verdict up front: **~85–90% of MiMo-Code is achievable as an opencode v1 plugin** (server plugin +
companion TUI plugin), with 3 honest exceptions (Max Mode, per-model compaction-trigger override,
prefix-cache-forked writer). On Claude Code the ceiling is lower (~60–70% functional parity) due
to its process-based hook model. Details and mitigations below.

## 1. opencode v1 plugin surface (what we build on)

Verified against upstream `anomalyco/opencode` @ `dc4449df` (v1.18.25, 2026-08-29) and
`@opencode-ai/plugin` npm.

- **Server plugin** (`Plugin = (input, options) => Promise<Hooks>`; input has `client` [SDK],
  `project`, `directory`, `worktree`, `$` [Bun shell], `serverUrl`). Hooks: `event` (all bus
  events), `config` (mutate live merged config), `tool` (custom tools; overrides built-ins by
  name), `auth`, `provider`, `chat.message`, `chat.params`, `chat.headers`,
  `tool.execute.before/after`, `tool.definition`, `command.execute.before`, `shell.env`,
  `experimental.chat.messages.transform` (every loop step — the context-injection point),
  `experimental.chat.system.transform`, `experimental.session.compacting`,
  `experimental.compaction.autocontinue`, `experimental.text.complete`, `dispose`.
- **TUI plugin** (`@opencode-ai/plugin/tui`, spec `packages/opencode/specs/tui-plugins.md`):
  keymap layers + palette/slash commands, routes, dialogs/toasts, host slots (sidebar,
  session_prompt_right, home_footer, …), theme install, full synced state, events, kv.
- **Client SDK from plugin:** create child sessions (`session.create` with `parentID`),
  `prompt_async`, message pagination, `summarize` (trigger compaction), abort, permissions reply,
  MCP add at runtime, TUI control plane (`/tui/*`), SSE events. This is enough to implement
  actors, workflows, dream/distill, cron as background orchestration.
- **Ship-as-config:** plugins mutate `cfg.agent`, `cfg.command`, `cfg.skills.paths` — so darwin
  can install its agents (compose, checkpoint-writer, dream, distill), commands, and its whole
  skill bundle at load time.
- Prior art proving each hard feature class: `opencode-supermemory` (memory),
  `opencode-goal-plugin` (goal loop w/ auto-continue), `opencode-background-agents`,
  `opencode-scheduler` (cron), `micode` (workflow + session continuity), `opencode-conductor`
  (spec→implement lifecycle).

## 2. Feature-by-feature mapping

| MiMo feature | opencode v1 mechanism | Parity | Notes |
|---|---|---|---|
| Builtin skills (27) + compose skills (14) + search/auto-load/multi-skill | `cfg.skills.paths` + bundled SKILL.md dir; `skill_search` custom tool (port BM25 verbatim); `chat.message` hook injects `<skill_content>` + orchestration plan | ~95% | Catalog injection point differs (we inject via message/system transform) |
| `/compose-next`, compose agent, compose docs dirs | skill bundle + `cfg.agent.compose` + prompt via agent def; compose docs dir in darwin options | ~95% | |
| Persistent memory (FTS5, 4 layers, memory/history tools, reconcile) | plugin-owned SQLite + files under its own data dir; `tool: {memory, history}`; `system.transform` injects memory instructions | ~95% | Index the *host's* session DB read-only for `history` (schema is upstream's, documented in vendored tree) |
| Checkpoint writer subagent | hidden darwin agent + child-session spawn via `client`; writer prompt ported verbatim; memory-path-guard reimplemented as `tool.execute.before` write guard on darwin's own tools + path checks in writer tools | ~85% | No prefix-cache fork (see §3.2) |
| Context rebuild + boundary + microcompact | `experimental.chat.messages.transform` (fires every step AND during compaction) — detect overflow ourselves from `message.part.updated` usage, assemble rebuild sections (ported budget table), inject synthetic user message with watermark metadata in our own store; trigger host compaction via `summarize` when fallback needed | ~80% | Can't persist a custom `checkpoint` part type — store watermark in plugin DB keyed by session+messageID |
| Task tree (task tool, T1/T1.1, events, sidebar) | `tool: {task}` + plugin SQLite; TUI plugin sidebar slot renders tree | ~90% | |
| Actor subagents | `client.session.create({parentID})` + `prompt_async` + `session.idle`/`message.part.updated` events; waiter/cancel via abort + inbox via `chat.message` injection; status/inbox tools | ~85% | Semantics differ: child sessions not agentID-slices of one session; `context: full` ≈ parent messages replay (we can pass transcript) |
| Goal / stop judge | `session.status`→idle / `session.idle` events; on idle with active goal: run judge call (ported prompt, `generateObject` via AI SDK or raw fetch), then `prompt_async` synthetic reminder (≤12) or clear | ~90% | Judge runs *after* stop rather than gating before — externally near-equivalent; proven by opencode-goal-plugin |
| Dream / distill (+auto) | hidden agents + `/dream` `/distill` commands via `cfg.command`; auto trigger on `session.created` event + interval state | ~95% | |
| Workflows engine (QuickJS sandbox, 4 builtins, journal/resume, /workflows) | fully self-contained in plugin: quickjs-emscripten dep, `tool: {workflow}`, TUI plugin dialog + run tree | ~95% | `agent()` maps to child-session spawn; `isolation:"worktree"` = `client` + `$` git worktree |
| Voice input | **TUI plugin**: keybind layer + `/voice` palette command, sox capture, TenVAD wasm, ASR fetch, prompt-box injection via `api.state`/tui control plane | ~80% | Needs asr/control model config in darwin options |
| Auth: MiMo OAuth, Claude import, custom providers | `auth` hook + `provider` hook + `chat.headers` (port `plugin/mimo.ts` nearly verbatim) | ~95% | MiMo's own OAuth *is* an opencode plugin |
| Vivid visuals, footer usage bar, model picker extras, ghost text, mode lock, /context-limit dialog | TUI plugin slots + palette commands; footer via `session_prompt_right` slot; mode lock via keymap layer filtering | ~70% | Can't restyle core screens; approximations only |
| Config schema additions | darwin options object (tuple form `["darwin", {...}]`) + `config` hook for host-native keys (compaction.*, permission.*) | ~75% | UX difference: users configure darwin in plugin options, not top-level opencode.json keys (unknown top-level keys rejected) |
| `/context-limit` / `compaction.max_context` | static per-model budget can't override host overflow threshold at runtime — **no hook** (math in `session/overflow.ts`). Mitigation: darwin's own transform can detect usage > budget and force `summarize` early; footer shows budget | ~60% | See §3.3 |
| Cron/loops | plugin background scheduler + `event` hook; `prompt_async` | ~90% | |
| Try-best / handoff | `tool.execute.after` + `message.part.updated` heuristics (ported detector); TUI dialog | ~85% | |
| Bash delete guard, edit exact-match, token-efficient bash, tool invocation_style | `tool.execute.before` (block/ask), **override builtin `edit`/`bash` by name** with ported logic, `tool.definition` rewrites | ~90% | Overriding built-ins by name is explicitly supported |
| Skills compat scan (.claude/.codex/.agents/.opencode) | upstream already scans `~/.claude/skills` + `.agents`; darwin adds remaining dirs via `cfg.skills.paths` mutation + own scan | ~90% | |
| llm-server (/v1), CLI subcommands (models/serve/attach), session importers | **not plugin-extensible** — darwin ships a small separate CLI (`darwin …`) for these | ~70% | Host already has serve/attach/run; `models`-style output via darwin CLI reading host config |

## 3. The honest concerns (why not literally 100%)

1. **Max Mode is impossible as a plugin.** It runs N parallel *propose-only* streams inside the
   processor (tools stripped of `execute`), then replays the winner through `handle.replay` and
   accounts judge/loser tokens as `overhead`. opencode's plugin API has no per-request
   multi-candidate stream, no replay primitive, and no cost-accounting seam. Options: (a) ship it
   disabled with docs ("requires MiMoCode"), (b) approximate with a "proposal jury" custom tool
   the model calls explicitly (weaker, not parity), or (c) upstream an opt-in multi-candidate
   hook to opencode. Recommend (a) + (c).
2. **No prefix-cache-forked writer.** MiMo's `checkpoint.fork` captures the parent's exact LLM
   request prefix so the writer subagent reuses provider prompt caching. A plugin spawns a child
   *session*, which assembles its own context — functionally equivalent output, but the writer
   pays more input tokens. Mitigation: keep the writer prompt minimal and stable; system prompt
   via the same `system.transform` text both threads see.
3. **No runtime compaction-threshold hook.** `/context-limit`'s per-model budget cannot move the
   host's overflow trigger (static `compaction.*` config only, and upstream has no `max_context`
   equivalent). Workaround above (~60% parity: early-force summarize works, prompt-footer
   denominator and automatic pruning at exact budget don't).
4. **`permission.ask` hook is typed but not wired** in v1.18.25 — permission automation must go
   through `permission.asked` event + reply endpoint (fine for darwin's writer auto-fail
   semantics, but it's async racing, slightly weaker).
5. **TUI ceiling:** core screens (home/session) aren't replaceable; vivid mode becomes
   slot-scoped decoration; ghost-text prediction and mode-lock are approximations via keymap
   layers. The *功能性* features (footer, dialogs, sidebar, palette entries) port well.
6. **Config UX:** opencode rejects unknown top-level keys, so `checkpoint.*`, `voice.*`,
   `dream.*`, etc. become `["darwin", { checkpoint: {...} }]` options. Schema-validated via the
   plugin's own JSON handling, but not in the host's config.json schema.
7. **Version churn risk:** upstream releases near-daily (11k+ plugin versions); MiMo features
   were built against an opencode-0.x-era base. Pin `engines.opencode` and test against LTS-ish
   points; hooks we depend on (`messages.transform`, `session.compacting`, TUI plugin API) are
   documented/current but young (TUI spec is a repo spec doc, not site docs).
8. **Licensing/trademark:** MiMo-Code is MIT but Xiaomi's USE_RESTRICTIONS purport to attach to
   "derivatives"; compose skills carry superpowers/karpathy licenses (keep attributions);
   "MiMo"/Xiaomi marks can't be used — hence "Darwin" (rename throughout; vendor dir is internal
   reference only, gitignored, not redistributed). MiMo OAuth endpoints are Xiaomi services —
   keep them optional/off by default and document that use of Xiaomi-hosted services is subject
   to MiMo ToS.
9. **Known MiMo bugs we inherit by parity:** workflow-tool schema envelope bug (#2253), Windows
   FTS path bug (#2253), skill-catalog per-message injection perf (#2254 — we'll inject via
   system transform per the suggested fix while keeping observable behavior). Parity means
   feature parity, not bug-for-bug.

## 4. Claude Code target (phase 2) — ceiling summary

Mechanisms: plugin dir with `.claude-plugin/plugin.json`; `skills/` (invoked
`/darwin:name`), `agents/`, `hooks/hooks.json` (SessionStart, UserPromptSubmit, PreToolUse,
PostToolUse, Stop, PreCompact/PostCompact, SubagentStart/Stop…; handlers = command/http/mcp/
prompt/agent), `.mcp.json` (bundle an MCP server!), `monitors/`, `bin/`, settings.json `agent`.

- Feasible: skills bundle (incl. compose-next), dream/distill as skills+agents, memory via MCP
  server + SessionStart/UserPromptSubmit context injection, task tool via MCP, goal via Stop hook
  (classic pattern — Stop can block premature stop), workflows engine inside a bundled MCP server
  (agent() → `claude -p` headless subagents), cron via monitors/external scheduler.
- Hard/impossible: in-process message transform (no equivalent of `chat.messages.transform` —
  checkpoint/rebuild degrades to PreCompact/PostCompact + SessionStart rehydration); context
  footer/usage UI; voice (no audio-input hook — external CLI only); Max Mode; per-model
  compaction budgets; multi-provider auth (Claude Code is Anthropic-centric; custom providers
  via env vars only).
- Consequence: phase-2 parity target is "behavioral parity on workflows, memory, skills, goal,
  tasks" with documented UI/context-mechanics differences. The darwin core (memory DB, task DB,
  workflows sandbox, skill corpus) should be a shared package consumed by both the opencode
  plugin and the Claude Code MCP/hooks shim.
