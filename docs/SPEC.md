# MiMo-Code Reference Specification

**Project Darwin** — authoritative specification of XiaomiMiMo/MiMo-Code, to be treated as the
reference implementation. Darwin ports this feature set (100% parity, nothing invented) to
(1) opencode v1 as a plugin, then (2) Claude Code as a plugin.

## Provenance

| Item | Value |
|---|---|
| Source repo | https://github.com/XiaomiMiMo/MiMo-Code |
| Vendored at | `vendor/mimo-code` (gitignored; clone locally to re-derive) |
| Vendored commit | `092e42fefbeecf0ddde522c346663a286df6a609` (2026-08-29 +0800, PR #2287) |
| Package version | 0.1.13 (`@mimo-ai/cli`, npm, published 2026-08-19) |
| License | MIT + [USE_RESTRICTIONS.md](https://github.com/XiaomiMiMo/MiMo-Code/blob/main/USE_RESTRICTIONS.md) (applies to "derivatives thereof") |
| Spec sources | Vendored source tree; README(.zh)/README_npm; docs site https://mimo.xiaomi.com/mimocode/start; blog "MiMo Code: Scaling Coding Agents to Long-Horizon Tasks" (2026-06-10); GitHub Releases v0.1.4–v0.1.13 |
| Upstream | Fork of opencode (github.com/anomalyco/opencode, formerly sst/opencode); upstream now at v1.18.25 with diverged architecture |

> Docs-site pages lag the binary (e.g. the slash-commands page omits `/dream`, `/distill`,
> `/rebuild`, `/voice`, `/compose-next`). Where docs and source/releases disagree, **source +
> release notes win**. The MiMo docs `/mimocode/plugins` page 404s — plugin support is inherited
> from opencode and undocumented by MiMo.

---

## 1. Identity & Architecture

- Terminal-native AI coding assistant; binary `mimo`; core engine in `packages/opencode/src`
  (package `@mimo-ai/cli`), TUI in `packages/opencode/src/cli/cmd/tui/`. Uses the older
  single-`packages/opencode` opencode monorepo layout (upstream has since split into
  `packages/{core,server,tui,llm,protocol,...}`).
- Companion npm packages (all 0.1.13): `@mimo-ai/plugin` (plugin SDK), `@mimo-ai/sdk`,
  12 platform-binary packages under the `mimo-research` org.
- Stack: Bun, Effect 4.0.0-beta, ai 6.x (AI SDK), OpenTUI (solid), Drizzle + SQLite
  (`mimocode.db`), QuickJS-emscripten (workflows/tool-script), models.dev catalog.
- Pitch (blog): long-horizon coding via **Compute** (Max Mode, Goal), **Memory** (4-layer memory,
  checkpoints, cycles), **Evolution** (dream/distill). Eval claims: beats Claude Code + Sonnet 4.6
  on 3 benchmarks; blind AB (576 devs): ~50% win rate <200 steps, >65% above 200 steps.

## 2. CLI Surface (`packages/opencode/src/index.ts`, yargs `scriptName("mimo")`)

Global flags: `--print-logs`, `--log-level`, `--pure` (sets `MIMOCODE_PURE=1`), `--version`, `--completion`.

| Command | Purpose / notable flags |
|---|---|
| `mimo [project]` | TUI. `-m/--model`, `-c/--continue`, `-s/--session`, `--fork`, `--prompt`, `--agent`, `--never-ask`, `--trust`, `--dangerously-skip-permissions` (alias `--yolo`) |
| `mimo attach <url>` | Connect TUI to remote server; `--password` / `MIMOCODE_SERVER_PASSWORD` |
| `mimo run [msg..]` | Headless. `--format default\|json`, `--file`, `--attach`, `--title`, `--variant`, `--thinking`, `--role`, `--command`, `--share`, `--dangerously-skip-permissions`. Denies `question`/`plan_exit` perms by default; drains checkpoint writers ≤120s at exit |
| `mimo serve` | `--port/--hostname/--mdns/--no-auth`; refuses non-loopback without password |
| `mimo models [provider]` | `--verbose/--refresh`; prints resolved window + compaction budget per model |
| `mimo llm-server` | Local OpenAI-compatible `/v1` (chat, `/v1/audio/speech`, `/v1/audio/transcriptions`); token `issue/list/revoke` |
| `mimo agent create/list` | Agent scaffolding/listing |
| `mimo providers list/login/logout/whoami` | |
| `mimo session import-claude/delete/list` | Claude Code session import (also auto-runs at startup: claude/codex/opencode/external importers) |
| `mimo mcp list/auth/add/logout/debug` | |
| `mimo export` / `mimo import` | Import accepts `https://opncd.ai/s/...` share links (upstream opencode infra) |
| `mimo github install/run`, `mimo pr <n>` | |
| `mimo db [query]/path/migrate` | Shared `mimocode.db`; one-time JSON→SQLite migration |
| `mimo plugin <module>`, `mimo upgrade/uninstall/stats/generate/acp/console` | `web` disabled |
| `mimo debug …` | config, paths, file, rg, lsp, agent, skill, scrap, snapshot subcommands |

**`--dangerously-skip-permissions`:** TUI red confirmation gate (skipped without TTY) → sets
`MIMOCODE_DANGEROUSLY_SKIP_PERMISSIONS=1` → config injects allow-all base *under* user rules
(last-match-wins preserved; `deny` still blocks; leftover `ask` rules still prompt).
Runtime counterpart: `/skip-permissions`.

## 3. Environment Variables (`packages/opencode/src/flag/flag.ts`)

Paths/config: `MIMOCODE_HOME`, `MIMOCODE_CONFIG`, `MIMOCODE_CONFIG_CONTENT`, `MIMOCODE_CONFIG_DIR`,
`MIMOCODE_TUI_CONFIG`, `MIMOCODE_DB`, `MIMOCODE_SKIP_MIGRATIONS`, `MIMOCODE_STRICT_CONFIG_DEPS`,
`MIMOCODE_PURE`, `MIMOCODE_CLIENT`, `MIMOCODE_PLUGIN_META_FILE`,
`MIMOCODE_DISABLE_DEFAULT_PLUGINS`, `MIMOCODE_DISABLE_PROJECT_CONFIG`.

Context engine: `MIMOCODE_DISABLE_AUTOCOMPACT`, `MIMOCODE_DISABLE_PRUNE`,
`MIMOCODE_DISABLE_CHECKPOINT`, `MIMOCODE_COMPACTION_MAX_CONTEXT`,
`MIMOCODE_COMPACTION_TRIGGER_RATIO`.

Permissions: `MIMOCODE_DANGEROUSLY_SKIP_PERMISSIONS`, `MIMOCODE_PERMISSION`,
`MIMOCODE_AUTO_APPROVE_DELETE`.

Skills: `MIMOCODE_DISABLE_BUILTIN_SKILLS`, `MIMOCODE_DISABLE_OFFICIAL_SKILLS` (docx/pdf/pptx/xlsx-official + html-to-video-pipeline), `MIMOCODE_DISABLE_COMPOSE_SKILLS`, `MIMOCODE_DISABLE_SLASH_SKILLS` (TUI autocomplete only), `MIMOCODE_DISABLE_EXTERNAL_SKILLS`, `MIMOCODE_DISABLE_CLAUDE_CODE[_MCP|_PROMPT|_COMMANDS|_SKILLS]`, `MIMOCODE_DISABLE_AGENTS_SKILLS`, `MIMOCODE_DISABLE_CODEX_SKILLS`, `MIMOCODE_DISABLE_OPENCODE_SKILLS`, plus `MIMOCODE_SKILL_SEARCH_{EXACT_SCORE,BM25_K1,BM25_LENGTH_NORMALIZATION,BM25_IDF_SMOOTHING,BM25_SCORE_WEIGHT,QUERY_COVERAGE_WEIGHT,AUTO_LOAD_THRESHOLD,SCORE_PRECISION,MAX_RESULTS,STEM_MIN_LENGTH,FILE_SAMPLE_LIMIT}`.

Models/providers: `MIMOCODE_DISABLE_MODELS_FETCH`, `MIMOCODE_MODELS_URL`, `MIMOCODE_MODELS_PATH`,
`MIMOCODE_ENABLE_EXPERIMENTAL_MODELS`, `MIMOCODE_MIMO_ONLY`, `MIMOCODE_DISABLE_PROVIDER_ENV`,
`MIMO_PLATFORM_URL`.

Experimental: `MIMOCODE_CODEX_MODE`, `MIMOCODE_ENABLE_EXEC_TOOL`, `MIMOCODE_EXPERIMENTAL{FILEWATCHER,ICON_DISCOVERY,BASH_DEFAULT_TIMEOUT_MS,TOKEN_EFFICIENCY[_HEURISTIC+3],OUTPUT_TOKEN_MAX,OXFMT,LSP_TY,LSP_TOOL,MCP_TOOL_SEARCH,ORCHESTRATOR,WORKFLOW_TOOL,CRON,MARKDOWN,HTTPAPI,WORKSPACES,EXA}`, `MIMOCODE_DISABLE_COPY_ON_SELECT`.

Loop/robustness: `MIMOCODE_ENABLE_TRY_BEST_HANDOFF`, `MIMOCODE_ENABLE_DYNAMIC_SYSTEM_PROMPT`,
`MIMOCODE_DISABLE_INSTRUCTIONS`, `MIMOCODE_ENABLE_FUZZY_EDIT`,
`MIMOCODE_ENABLE_QUESTION_TOOL`, `MIMOCODE_LOOP_KEEPALIVE_BUDGET`,
`MIMOCODE_LOOP_KEEPALIVE_DELAY_S`, `MIMOCODE_TEXT_NGRAM_N`, `MIMOCODE_TEXT_REPEAT_THRESHOLD`,
`MIMOCODE_TEXT_WINDOW_TOKENS`, `MIMOCODE_OUTPUT_LENGTH_CONTINUATION_LIMIT`,
`MIMOCODE_INVALID_OUTPUT_CONTINUATION_LIMIT`, `MIMOCODE_TEXT_TOOL_CALL_RETRY_LIMIT`,
`MIMOCODE_FORCE_ANTHROPIC_REASONING_CONTENT`, `MIMOCODE_MAX_PROMPT_IMAGES`,
`MIMOCODE_MAX_PROMPT_IMAGE_SIZE`.

Server/TUI/system: `MIMOCODE_SERVER_PASSWORD[_USERNAME|_SUPPLIED]`, `MIMOCODE_DISABLE_MOUSE`,
`MIMOCODE_DISABLE_LOG_ROTATION`, `MIMOCODE_ENABLE_ANALYSIS`,
`MIMOCODE_DISABLE_TERMINAL_TITLE`, `MIMOCODE_SHOW_TTFD`, `MIMOCODE_AUTO_SHARE`,
`MIMOCODE_AUTO_HEAP_SNAPSHOT`, `MIMOCODE_GIT_BASH_PATH`, `MIMOCODE_DISABLE_GIT`,
`MIMOCODE_FAKE_VCS`, `MIMOCODE_DISABLE_LSP_DOWNLOAD`,
`MIMOCODE_DISABLE_EMBEDDED_WEB_UI`, `MIMOCODE_DISABLE_AUTOUPDATE`,
`MIMOCODE_ALWAYS_NOTIFY_UPDATE`, `MIMOCODE_WORKSPACE_ID`, `MIMOCODE_DISABLE_CRON`,
`MIMOCODE_ENABLE_EXA`, `MIMOCODE_BIN_PATH`, `MIMOCODE_INSTALL_SCRIPT_URL`.

## 4. Config Schema (MiMo additions over opencode; `src/config/config.ts`, schema at `https://mimo.xiaomi.com/mimocode/config.json`, `$schema` auto-injected)

| Key | Type / default | Notes |
|---|---|---|
| `skills.paths` / `skills.urls` | `string[]` | Extra skill dirs / URL indexes |
| `compose.docs` / `compose.docs_absolute` | `"docs/compose"` / bool | Where compose artifacts live |
| `compaction.max_context` | `number \| "300K"/"1M"/"50%" \| Record<pattern, qty>` | Per-model compaction budget; wildcards (`anthropic/*`), longest pattern wins; clamped ≤ real window; `0` = model window |
| `compaction.auto/prune/tail_turns(2)/preserve_recent_tokens/reserved(≤33000, output-capped 20K)` | | `session/overflow.ts` math |
| `checkpoint.thresholds` | `string[]` | Default by window: ≤200K → 20/40/60/80%; ≤500K → 10%×9; >500K → 5%×18 |
| `checkpoint.reserved` | 20000 (docs; 13000 in prune code) | |
| `checkpoint.fork` | `true` | Prefix-cache fork for writer |
| `checkpoint.push_caps` | per-section caps (tokens) | `tasks_ledger` 2000, `focus_task` 4000, `actor_ledger` 500, `memory_titles` 500, `global` 6000, `checkpoint` 11000, `memory` 10000, `notes` 6000, `design_decisions` 3000, `open_notes` 800, `recent_user` 16000, `recent_user_per_msg` 2000 |
| `checkpoint.task_archive_days` | 7 | (alias deprecated `task_cleanup_days`) |
| `checkpoint.memory_reconcile_on_search` / `memory_search_score_floor` | `true` / `0.15` | |
| `memory.disable_write` | `false` | Stop-write-not-read; memory NOT auto-loaded while on |
| `memory.cc_index` | `false` | Index Claude Code memory under scope `cc` |
| `history.kinds` | `(user_text\|assistant_text\|tool_input\|tool_error\|reasoning\|tool_output)[]` | Trajectory FTS index |
| `dream.auto` / `dream.interval_days` | `false` / 7 | |
| `distill.auto` / `distill.interval_days` | `false` / 30 | |
| `voice.asr_model` / `voice.control_model` | `xiaomi/mimo-v2.5-asr` / `xiaomi/mimo-v2.5` | Any OpenAI-compatible provider |
| `workflow.maxConcurrentAgents` | `min(16, 2×cores)` | |
| `workflow.maxDepth` / `maxLifecycleAgents` / `scriptDeadlineMs` | 8 / 1000 / 12h | |
| `experimental.maxMode.candidates` | 5 | Max Mode |
| `experimental.try_best` | `edit_window` 12, `edit_similarity` 0.8, `edit_matches` 2, `action_streak` 4 | Try-best detector |
| `experimental.loop_streak_recovery` | `trigger_count` 3, `max_span` 64 | |
| `experimental.predict_next_prompt` | on | Ghost-text next-prompt prediction |
| `experimental.primary_tools`, `batch_tool`, `continue_loop_on_deny`, `disable_paste_summary` | | |
| `vision_model`, `model_groups` | | capability tiers usable anywhere `provider/model` accepted |
| `tool.invocation_style` / `invocation_style_by_tool` | `"json"\|"xml"` | Tool-call syntax per tool |

Files: project `.mimocode/mimocode.jsonc|.json`; global `~/.config/mimocode/mimocode.jsonc|.json`
(legacy TOML auto-migrated). TUI config split out to `tui.json` (`theme`, `keybinds`, `plugin`,
`plugin_enabled`, `scroll_speed`, `scroll_acceleration`, `diff_style`, `mouse`; schema
`https://mimo.xiaomi.com/mimocode/tui.json`). Variable substitution `{env:VAR}`, `{file:path}`.
`.mimocode/.gitignore` auto-written (`config/gitignore.ts`).

## 5. Data Layout (XDG; `MIMOCODE_HOME` overrides; Windows `%LOCALAPPDATA%\mimocode\`)

| Path | Contents |
|---|---|
| `~/.local/share/mimocode/` | `mimocode.db` (SQLite: sessions/messages/parts/tasks/actors/FTS/workflow), `auth.json`, `memory/`, logs, extracted skill bundles, compose skills, workflow journals |
| `~/.local/state/mimocode/` | TUI `kv.json`, recent `model.json` |
| `~/.cache/mimocode/` | LSPs, model catalog, skills URL cache |
| `<data>/memory/<scope>[/<scope_id>]/<key>.md` | scopes `global \| projects \| sessions \| cc`; project id = sha256(abs path)[:12] |

Memory file keys: `MEMORY.md` (project/global), `checkpoint.md`, `notes.md`,
`tasks/<TID>/progress.md`, `tasks/<TID>/notes.md`. Type detection via filename regexes; CC memory
(`~/.claude/projects/<slug>/memory/*.md`, YAML frontmatter `metadata.type`) optionally indexed.

## 6. Agents (`agent/agent.ts`)

| Agent | Mode | Notes |
|---|---|---|
| `build` | primary (default) | Full tools |
| `plan` | primary | `edit` denied except plan files (`.mimocode/plans` / `<data>/plans/<id>.md`); plan-mode reminders; write-back via plan file |
| `compose` | primary | *Deprecated*; orchestrates 14 `compose:*` skills; purple #a7a3d8; `skill: compose:* = allow` |
| `max` | primary | Only when `experimental.maxMode` configured (§15) |
| `orchestrator` | primary | Experimental; peer child sessions; roster injection; `session` tool |
| `general`, `explore` | subagent | `explore` read-only allowlist (grep/glob/list/bash/exec/webfetch/websearch/codesearch/read) |
| hidden: `title` (lite model, t 0.5), `summary`, `compaction` | | opencode parity |
| hidden: `checkpoint-writer` | | Forked writer (§11); no own prompt — parent prefix captured |
| hidden: `dream`, `distill` | | §17; tool allowlist read/write/apply_patch/view_image/glob/grep/memory/bash/exec |

`SYSTEM_SPAWNED_AGENT_TYPES = {checkpoint-writer, dream, distill}` — their permission asks
auto-fail non-interactively. Mode lock: after first message, switching restricted to
`[build, plan]`; compose/orchestrator isolate; `/force-agent` bypasses. Config agents via
`agent.*` (`disable/model/prompt/mode/steps/tool_allowlist/permission`).

## 7. System Prompt Assembly (`session/llm.ts` `buildSystemArray`)

Per-model base prompt from a multi-vendor zoo (`session/prompt/{anthropic,gpt,gemini,glm,deepseek,kimi,minimax,trinity,beast,copilot-gpt-5,default}.txt`)
→ `buildMemoryInstructions` (main/peer actors only; teaches memory layout + "Active recall
protocol": don't re-Read files already dumped) → orchestrator roster → plugin transform hook →
skills catalog (verbose XML) → instruction files (AGENTS.md/CLAUDE.md) → environment block
(behind `MIMOCODE_ENABLE_DYNAMIC_SYSTEM_PROMPT`) → collapsed to ONE system message (cache-stable).

Per-user-message synthetic reminders (`insertReminders`, `session/prompt.ts`): plan reminders,
compose preamble, auto-worktree notice (once/session), document-skill suggestions, skill
auto-load injections + multi-skill orchestration plan, goal re-entry (`<system-reminder>`),
autonomous-loop/stop/tool-result reminders, text-loop recovery.

## 8. Tools (`tool/registry.ts`; GPT/Codex-style models get reduced ABI: bash, apply_patch, view_image, exec)

**Added vs opencode:** `task` (tree work-items), `memory` (FTS5 search), `history` (trajectory
FTS search/around), `skill` + `skill_search` (BM25), `actor` (subagents: spawn/run/status/wait/
cancel/send; options `subagent_type, description, prompt, model, task_id, timeout_ms, command,
context: none|state|full, output_schema`), `workflow` (run/status/wait/cancel/resume;
experimental), `cron` (scheduled prompts; experimental, on by default), `session` (orchestrator
child-session mgmt; experimental), `exec`/tool-script (QuickJS composition of host tools for GPT
models), `mcp_tool_search` (experimental).

**Changed:** `edit` (exact-match default, fuzzy behind `MIMOCODE_ENABLE_FUZZY_EDIT`), `bash`
(irreversible-delete interception → `bash_delete` permission unless `MIMOCODE_AUTO_APPROVE_DELETE`;
token-efficient pipelines; Windows UTF-8), `websearch` (MiMo plugin backend for xiaomi/opencode
+ exa), `codesearch` (Exa), `apply_patch`/`view_image` exposed. Present: `question` (gated),
`plan_exit`, `lsp` (experimental), `notebook-edit`, `multiedit`.

Support: `memory-path-guard.ts` (write authority for writer agents), `checkpoint-description.ts`
(swaps actor/task descriptions in checkpoint mode), `invocation-style.ts` (json/xml per tool),
`isolated-git-guard.ts`, `conflict-detection.ts`, `truncate.ts`.

## 9. Slash Commands

**Server-side** (`command/index.ts`): `/init`, `/review` (commit|branch|pr, subtask), `/dream`,
`/distill`, `/goal <condition>|clear|reset`, `/rebuild` (rebuild context from checkpoint now),
`/deep-research` (when workflow tool on), `/loops` (cron list/cancel), config commands
(`.mimocode/command/*.md` + `~/.claude/commands` compat), MCP prompts, and **every skill** as a
slash command.

**TUI-local:** session: `/sessions` (resume, continue), `/workflows`, `/new` (clear), `/recover`,
`/share`, `/unshare`, `/rename`, `/timeline`, `/fork`, `/compact` (summarize), `/btw` (read-only
side question), `/undo`, `/redo`, `/copy`, `/export`, `/timestamps`, `/thinking`.
Agent/model: `/models`, `/agents`, `/force-agent`, `/modalities`, `/context-limit`
(200K/300K/500K/1M/custom → `compaction.max_context[provider/model]`, `0`=default; refuses while
busy), `/never-ask`, `/skip-permissions`, `/permission-timeout`, `/mcps`, `/variants`.
Provider: `/login`, `/connect`, `/logout`, `/org`. System: `/status` (ctrl+x s), `/worktree` (wt),
`/themes`, `/background`, `/logo`, `/vivid`, `/dark`, `/light`, `/help`, `/doc` (docs),
`/exit`, `/language` (lang). Prompt: `/editor`, `/skills`, `/revoke-consent`, `/voice`,
`/voice-send`, `/voice-control`. Hidden: command palette `ctrl+p`, Tab/Shift+Tab agent cycle,
F2 model cycle, stash push/pop/list, etc. Keybind table: `src/config/keybinds.ts` (leader ctrl+x).

## 10. Persistent Memory (`src/memory/`)

- SQLite FTS5: `memory_fts` (id, path UNIQUE, scope, scope_id, type, body, fingerprint
  `size-mtimeMs`, last_indexed_at) + `memory_fts_idx` virtual table.
- `reconcile.ts`: disk walk → fingerprint upsert/prune (lazy before search, or on search when
  `memory_reconcile_on_search`).
- `service.ts`: OR-joined phrase query (`fts-query.ts`), `snippet()` + `bm25()`, relative score
  floor 0.15, 3× over-fetch, scope/type filters.
- 4 layers (blog): Session (`checkpoint.md`), Project (`MEMORY.md`), Global, History (full
  trajectory SQLite — `history` tool fallback). Single-writer invariant per file; main agent
  read-only on structured files **except `notes.md`** (scratchpad; writer drains/routes it each
  checkpoint).
- `memory` tool exposes `search` to the model; `history` tool does raw trajectory search.

## 11. Checkpoint & Context Management (`session/checkpoint*.ts`, `prune.ts`, `overflow.ts`, `boundary.ts`, `budgeted-read.ts`, `tail-digest.ts`)

- **Triggers:** every runLoop iteration (`session/prompt.ts` ~3909) using last assistant token
  usage vs thresholds (§4 defaults; blog states ~20/45/70% of budget); per-session crossed set;
  main/peer actors only; `MIMOCODE_DISABLE_CHECKPOINT`; writer lock (1-slot pending, newest
  wins); failure recovery re-arms one threshold higher.
- **Writer:** forks hidden `checkpoint-writer` via `Actor.spawn`. Fork mode (default) captures
  parent's full LLM request prefix (system+tools+messages-to-watermark) for provider prefix-cache
  reuse. Prompt: absolute paths (CHECKPOINT_PATH/MEMORY_PATH/TASK_MEM_DIR/NOTES_PATH) + progress
  diff + `checkpoint-writer.txt` + section budget table. Writer edits checkpoint.md §1–§11 &
  MEMORY.md in place, runs `task list`, resets notes.md to template, replies `CHECKPOINT_COMPLETE`.
  Tools whitelist: read/write/edit/glob/grep/task/bash/exec; `memory-path-guard` is write
  authority. checkpoint.md = 11 sections (~11K budget): active intent, next action, constraints,
  task tree, current work, files involved, cross-task findings, errors & fixes, runtime state,
  design decisions, misc/open notes. MEMORY.md = 4 sections (~10K).
- **Reconstruction:** on overflow (pressure levels 0–3 at 50/70/85%) or `/rebuild`: wait for
  in-flight writer (30s w/ watermark, 5min first) → `renderRebuildContext` ordered sections:
  header anchor → tasks ledger → session checkpoint (section-aware budget) → active actors →
  recent user input (verbatim FIFO, 16K cap, 2K/msg head-tail truncation) → project MEMORY.md →
  global memory → notes → memory keys index (FTS paths minus pushed) → recent-activity tail
  digest → seam framing + system-reminder (continue/stop-check/process-results). Total bounded
  ~65K tokens (blog). Persisted as synthetic user message with a `checkpoint` part
  (`coveredUpTo` watermark [+ `digestUpTo`]); then **microcompact** of post-boundary completed
  tool_results for compactable tools. Boundary selection: token-budgeted tail (TAIL_MIN 10K /
  TAIL_MAX 20K / ≥5 text messages), aligned to user messages, adjusted for API invariants
  (tool_use/result pairing, thinking atomicity). Watermark column `session.last_checkpoint_message_id`.
  Fallback when no checkpoint / writer failed / memory-write off / checkpoint off: classic compaction.
- Checkpointed+rebuilt turns = one **cycle**; cycles chain unboundedly.

## 12. Task Tracking (`task/`)

Tables `task` (PK session_id+id; parent_task_id, status, summary, owner, timestamps,
cleanup_after) and `task_event` (created/started/unstarted/blocked/unblocked/done/abandoned/renamed).
IDs `^T\d+(\.\d+)*$` (T1, T1.1, …); statuses open/in_progress/blocked/done/abandoned; terminal
tasks archived after 7d (filtered, not deleted). Model-facing `task` tool: create/list/get/start/
block/unblock/done/abandon/rename (+batch in writer prompt). Checkpoint §4 renders from DB;
subagent `task_id` binding writes `tasks/<TID>/progress.md` (postStop gives one extra chance);
writer reconciles via progress diff. TUI sidebar (+todo parts).

## 13. Actor / Subagent System (`tool/actor.ts`, `actor/`)

`actor` tool ops: `spawn` (background → actor_id), `run` (blocking, rare), `status`, `wait`
(default 10 min), `cancel` (graceful, idempotent), `send` (inbox message, wakes actor,
`<inbox from=…>` wrapper). Options incl. `context: none|state|full` ("full" shares parent
conversation), `output_schema`, `isolation` worktree (workflow). Model: status
pending/running/idle; outcome success/failure/cancelled; lifecycle ephemeral|persistent;
SpawnMode peer|subagent|main; liveness stall 6min/abandon 10min; watchdog 45s scan →
`ActorStuck`/`ActorStalled` events. Subagents share parent **sessionID** (messages sliced per
agentID); registry in `actor_registry` table; roster injected into rebuild context + orchestrator.
Subagents cannot spawn subagents (except SYSTEM_SPAWNED types). Return contract
`**Status/Summary/Files touched/…` (`return-header.ts`). TUI: subagent footer, child-session
navigation keybinds, subagent dialog.

## 14. Goal / Stop Condition (`session/goal.ts`; gate in `session/prompt.ts` ~3241; `MAX_GOAL_REACT` 12)

`/goal <condition>` sets per-session goal (condition text is also the turn prompt so work starts
immediately). On every main-agent stop attempt: judge = **separate call to the session's own
model**, temperature 0, `generateObject` schema `{ok: boolean, impossible?: boolean, reason:
string}`, input = full main-thread transcript as real ModelMessages (tool calls/results/images
preserved) + quotes-evidence judge system prompt ("insufficient evidence" default; strict
`impossible` rules). `ok`/`impossible` → goal cleared, stop allowed, `session.goal` event
(TUI indicator + per-turn verdicts). Not satisfied → synthetic user `<system-reminder>`
("goal not satisfied: … judge reported: … keep working") and loop re-entry; ≤12 re-entries,
then stop allowed; judge **errors fail-open**. Blog: false-block rate > miss rate; dead-loop <0.5%.

## 15. Max Mode (`session/max-mode.ts`; agent `max`)

`experimental.maxMode.candidates` (default 5). Per step: N parallel **propose-only** candidates
(tools stripped of `execute` — no side effects), n-gram repeat detection + transient retry per
candidate; survivors → **judge** call (same model, free-text integer index, fallback 0); winner
replayed/committed (`handle.replay`); loser + judge usage accounted as `overhead` (never into
`tokens` — compaction math stays honest); all-failed → single normal pass. UI: "thinking — N
candidates" / "judging". Temp 1.0 sampling. Blog: +10–20% SWE-Bench Pro for ~4–5× tokens.

## 16. Compose Mode

- **compose agent** (deprecated path): 14 built-in `compose:*` skills (bundle
  `src/skill/compose/.bundle/` → extracted `<data>/compose/<version>/skills/`):
  `ask, brainstorm, debug, execute, feedback, merge, parallel, plan, report, review, subagent,
  tdd, verify, worktree`. Ported from `obra/superpowers` (LICENSE-superpowers, LICENSE-karpathy
  in-tree). Hidden from BM25 search; `skill: compose:* = deny` for non-compose agents. Compose
  system prompt injected as synthetic part on first compose user message with
  `{{compose_docs_dir}}` from `config.compose.docs`.
- **`/compose-next`** (recommended, frontier models, **build** agent): single-contract skill:
  Step 0 Orient → Grill (question tool, one decision axis per turn, Never-Ask self-resolution) →
  Spec (`docs/compose/spec/<feature>.md`, frontmatter feature/status/updated/branch/commits;
  [S1] Problem / [S2] Design / [S3] Out of Scope / Tasks with acceptance+covers+depends) →
  Workspace (linked worktree `.worktrees/<slug>`, never main w/o consent) → Implement (TDD where
  reproducible; parallel subagents w/ disjoint file sets; `task` tool) → Verify (fresh command
  evidence; PRE-EXISTING marking; strictly sequential before review) → Review (fresh subagent
  reviewer; spec-compliance/correctness/consistency verdicts; fix-rerun loop w/ impasse exit) →
  Finalize (status delivered; Report = built/verification/journey) → Finish (no auto-finish;
  merge/PR/push options). Checkpoint-recovery instructions embedded.

## 17. Dream & Distill

- `/dream` (hidden `dream` agent): consolidate project memory from memory files + **raw
  trajectory SQLite** (read-only; SQL templates + keyword searches). Phases: Locate → Orient →
  Gather → Verify vs trajectory → Consolidate (Rules/Architecture decisions/Discovered
  knowledge/Patterns/Gotchas; ≤200 lines/10KB; dedupe; absolute dates; `[ses_xxx]` provenance) →
  Prune. Defers workflow packaging to /distill.
- `/distill` (hidden `distill` agent): scan recent sessions (past month for auto runs) for
  repeated manual workflows; inventory existing skills/agents/commands; create high-confidence
  skills/subagents/commands.
- Auto: `dream.auto` (7d) / `distill.auto` (30d) on new session start; gated by memory-write;
  10s spawn gap; project-age check. "Auto Dream"/"Auto Distill" system sessions excluded from
  `-c` resume and agent lists. Writes confined to memory + `.mimocode` (v0.1.8).

## 18. Workflows Engine (`workflow/`)

- **Sandbox:** QuickJS-emscripten WASM; memory limit, wall-clock deadline + active-time budget
  interrupt; deterministic mode (delete `Date`, seed `Math.random` from runID hash, delete
  WeakRef/FinalizationRegistry); guest prelude `parallel()`, `pipeline()`, minimal URL.
- **Guest API:** `agent(prompt, {agentType?, model?, tools?, schema?, retry?{attempts,baseMs,maxMs},
  timeoutMs?, isolation?:"worktree", label?, phase?})` — never throws (null on failure); schema →
  structured output (`json_schema`, retryCount 2); worktree isolation spawns actor in fresh git
  worktree (kept only when changed; returns `{_worktree:{branch,directory,changed}, …}`);
  `phase(title)`, `log(msg)`, `workflow(nameOrScript, args, opts)` (child run; cycle/depth guards);
  `readFile/writeFile/glob/exists` jailed to workspace root; `args` JSON.
- **Persistence:** `workflow_run` table + per-run `<data>/workflow/<runID>.jsonl` journal; resume
  replays journaled agent results keyed by prompt+opts+occurrence. `meta.permissions` manifest.
- **Limits:** concurrency semaphore + `maxConcurrentAgents` (min(16, 2×cores)); ≤1000 agents/run;
  depth 8; 12h script deadline.
- **Builtins** (`workflow/builtin/*.js`):
  - `compose` — Brainstorm → Design → Implement (topo batches, per-task worktrees, TDD ≤3
    attempts) → Verify → Review (≤2 fix rounds) → Report → Merge. Args: task/type/feature_name/
    skip flags/maxConcurrent.
  - `deep-research` — Brief → Plan angles (3/5/8 by depth quick/standard/deep) → parallel
    research subagents (findings/F#.md with quotes+urls+confidence) → Reflect (delta angles) →
    single-writer REPORT.md → cold review → fix. File checkpoints → convergent/resumable.
  - `fact-check` — Plan → Search per line → Extract (≤15 sources) → Group dupes → Crosscheck
    (3-juror adversarial vote, reject quorum 2, ≤25 facts) → Report.
  - `research-experiment` — Baseline (git commit + results.tsv) → Loop (hypothesize → implement
    → eval → keep/revert; 3-fail REFINE / 5-fail PIVOT / 3-pivot STOP ladder) → Audit (metric
    gaming) → Report. Requires fixed-budget eval command + explicit editable-file scope.
- **Invocation:** `workflow` tool (run/status/wait/cancel/resume; gated), TUI `/workflows` dialog
  (run tree), `/deep-research`, `runAsync`. Custom: `.mimocode/workflows/*.js` or
  `.claude/workflows/*.js` (walked up to worktree root; same name overrides builtin).

## 19. Skills System (`skill/`)

- **27 builtin skills** (bundle `src/skill/builtin/.bundle/`, extracted to
  `<data>/builtin_skills/<version>/skills/`): `arxiv`, `claude-code`* (when `claude` binary
  present), `codex`* (when `codex` present), `compose-next`, `data-analytics`, `deep-research`,
  `design-blueprint`, `docx-official`†, `evolve`, `frontend-design`, `grok-build`,
  `html-to-video-pipeline`†, `learn-everything`, `loop`, `mate`, `memory-search`,
  `mimocode-docs`, `modern-python-toolchain`, `pdf-official`†, `playwright`, `pptx-official`†,
  `product-design`, `research-paper-writing`, `sales`, `skill-creator`, `super-research`,
  `xlsx-official`†. († = "official" disable set.) Plus 14 compose skills (§16).
- **Scan order:** builtin bundle → compose bundle → global `~/.{claude,agents,codex,opencode}/skills/**/SKILL.md`
  → project same (cwd→worktree walk) → `.mimocode/{skill,skills}/**` → `skills.paths` →
  `skills.urls` (index.json discovery, cached). Later user skills **override** same-name builtins.
- **Frontmatter:** `name`, `description`, `aliases`, `disable-model-invocation`
  (agentskills.io-compatible), `hidden`.
- **Matching** (`skill/search.ts`): exact name/alias/localized-alias = 1.0; BM25 (k1 1.5, b 0.75,
  idf smoothing 0.5) blended 0.55 + query coverage 0.35, ceiling 0.90; auto-load ≥0.85; max 3;
  Han bigram tokenization; English stem ≥3 chars; zh/zht localized aliases (论文搜索, 深度研究…).
  `skill_search` tool exposes search to model (auto-loads top hit ≥0.85).
- **Slash auto-load:** `/name` mentions inject `<skill_content name=…>` synthetic parts
  (MAX_AUTOLOAD 3; overflow listed for manual load). ≥2 mentioned skills → **multi-skill
  orchestration plan** reminder (classify pipeline/parallel/overlay, interface contracts,
  conflict resolution, phased plan — docs/harness design doc).
- Skills catalog injected into system tail (verbose XML) with snapshot versioning. Document-skill
  auto-suggest when matching files attached. Permissions: `permission.skill` glob patterns.
- TUI: `/` autocomplete (unless `MIMOCODE_DISABLE_SLASH_SKILLS`), `/skills` picker.

## 20. Voice Input (`cli/cmd/tui/util/voice.ts`, `vad.ts`)

`/voice`: recorder via sox/rec/arecord (16kHz mono s16le PCM) → TenVAD WASM (`ten_vad.wasm`,
bundled + license) pause segmentation → WAV → POST `<baseURL>/chat/completions`, model
`mimo-v2.5-asr`, proprietary body (`input_audio` data-URI, `asr_options:{language:"auto"}`),
header `X-Mimo-Source: mimocode-cli`; streamed segments transcribed incrementally into input.
`/voice-control`: multimodal `mimo-v2.5` gets {current_text, agent, available_agents,
send_enabled} + audio → `{"actions":[{action:"edit"|"send"|"agent",…}]}`. `/voice-send` gates
voice-triggered submits. MiMo-login-gated by default; any OpenAI-compatible provider via
`voice.*` config. Requires sox; WSLg/SSH pulseaudio recipes documented.

## 21. Auth & Providers

- **MiMo OAuth** (`plugin/mimo.ts` — literally an opencode plugin in-tree): X25519 keypair;
  authorize at `platform.xiaomimimo.com/authorize` (kn=mimocode); localhost callback server or
  manual code paste; payload = ECDH+SHA-256+AES-256-GCM `{sk,uid,url}`; stores provider `xiaomi`
  auth; adds `X-Mimo-Source` header on xiaomi calls.
- **Codex (ChatGPT) OAuth:** `/connect` → `provider.oauth.authorize {providerID:"openai"}`.
- **Import from Claude Code:** reads `~/.claude/settings{,.local,_local}.json` env
  (ANTHROPIC_API_KEY/BASE_URL/DEFAULT_OPUS|SONNET_MODEL), strips `[1m]` suffixes, registers model.
- **Catalog:** models.dev-based; adds `xiaomi` (`https://api.xiaomimimo.com/v1`); keeps
  opencode subscription tiers; HTTP-Referer `https://mimo.xiaomi.com/coder/`; in-house vision
  auto-pick. **Custom OpenAI-compatible:** `provider.<id>.options.{baseURL,apiKey}` + `models`
  + `only_configured_models`; `/modalities` multi-select persists input modalities.
- Extras: free-API sunset dialogs, token-plan dialog, ToS agreement, MiMo websearch backend +
  quota messages, `AnthropicProxyPlugin` (SSE message_stop trimming).

## 22. TUI Features

Vivid/minimal visuals (`/vivid`, starry background, bg pulse, sound cues, home tips); command
palette `ctrl+p` (categories, slash aliases, keybinds, suggested); Tab agent cycle + mode lock +
`/force-agent`; prompt footer context usage `33.0K/260K↓ (13%)` (`↓` = config budget in force;
`—/960K` stale placeholder after rebuild) + cumulative cost; model picker (search, favorites,
frecency, recents, variants ctrl+t, model groups); ghost-text next-prompt prediction; i18n
(en/zh/zht/ja/fr/es/ru); timeline + fork-from-timeline; permission dialog (60s auto-reject under
skip-permissions); workflow tree dialog; subagent footer + child-session navigation; workspace
trust prompt; paste summaries; image protocol; heap snapshots; Windows UTF-8/CJK; try-best
handoff dialog (→ Codex/Claude Code CLI skills); auto-worktree notice; isolated-git guard.

**Try-best / Mix-of-Harness** (`session/try-best-detector.ts`, `experimental.try_best`): detects
edit-repeat (Jaccard ≥0.8 within window 12, ≥2 matches), bash-retry, action-streak (4); pauses
turn; dialog: hand off to Codex/Claude Code CLI skill or continue-with-different-strategy
(injects synthetic prompt). Default off.

## 23. Permissions

opencode-parity model (allow/ask/deny, last-match-wins, per-tool objects, per-agent override) +
MiMo additions: `bash_delete` (irreversible delete interception; `MIMOCODE_AUTO_APPROVE_DELETE`),
`skill` (glob per-skill, `compose:*` gating), `permission.task` (actor/task subagent control),
`external_directory` (incl. memory dirs for writer/dream/distill agents), forced-ask ops
auto-reject after 60s under `/skip-permissions`, plan-mode edit denial with plan-file exception,
`--dangerously-skip-permissions` layering (§2).

## 24. Known Open Issues (port-relevant; 2026-08-29)

- #2253 `workflow` tool rejects all invocations (top-level discriminated-union schema vs
  operation-envelope convention); Windows `memory` FTS never populates (forward-slash path regex).
- #2254 skill-catalog injection into every user message → perf regression (suggested fix: move to
  system prompt).
- #2272 `summary.diffs` SQLite OOM on session load.
- #2276 DeepSeek thinking 400s (tracks upstream opencode #24104/#24114/#24124…).
- #1914 opencode/mimo install coexistence conflicts.

## 25. Release Timeline (feature trajectory)

v0.1.4 builtin compose workflow, DeepSeek/GLM/MiniMax prompts, CJK fixes · v0.1.5 builtin skills
(14), evolve, deep-research+fact-check, orchestrator, cron+loop, Windows installer, edit
exact-match · v0.1.6 slash skills + multi-skill orchestration, `/skip-permissions`, mode lock,
`/modalities`, `/rebuild`, hot-reload hooks · v0.1.7 tool_script (QuickJS), `skill_search` BM25,
try-best handoff, xAI OAuth, new skill bundles · v0.1.8 dream/distill write confinement ·
v0.1.9 free-trial sunset, plan continuation · v0.1.10 orchestrator roster, per-model early
compaction (`/context-limit`), manual `/rebuild`, Vivid/Minimal, exec+MCP dispatch, MCP sampling,
Playwright/Grok skills · v0.1.11 `memory.disable_write`, `memory-search` skill, MCP single-client ·
v0.1.12 `--yolo`, Compose Next model-invocable · v0.1.13 checkpoint disable switch,
`MIMOCODE_AUTO_APPROVE_DELETE`, 64-bit IDs, dynamic system prompt flag, skills dedupe.

## 26. Web References

- Repo: https://github.com/XiaomiMiMo/MiMo-Code (releases, issues)
- Docs: https://mimo.xiaomi.com/mimocode/start · slash-commands · sessions · config-files ·
  skills · agents · custom-tools · modes · keybinds · cli-subcommands · env-vars · troubleshooting
- Blog: https://mimo.xiaomi.com/zh/blog/mimo-code-long-horizon (EN: /blog/mimo-code-long-horizon)
- npm: https://www.npmjs.com/package/@mimo-ai/cli · /@mimo-ai/plugin · /@mimo-ai/sdk
- Upstream opencode: https://github.com/anomalyco/opencode · https://opencode.ai/docs/plugins ·
  /docs/ecosystem · @opencode-ai/plugin@1.18.25
