import { join, dirname } from "node:path"
import { existsSync, readFileSync } from "node:fs"
import { spawn } from "node:child_process"
import { fileURLToPath } from "node:url"
import { tool, type Plugin } from "@opencode-ai/plugin"
import { z } from "zod"
import {
  Store,
  readEnv,
  paths as darwinPaths,
  ensureDir,
  log,
  reconcile,
  readHead,
  appendEntry,
  projectMemoryPath,
  globalMemoryPath,
  sessionNotesPath,
  ensureMemorySkeleton,
  projectId,
  JUDGE_SYSTEM,
  judgePrompt,
  parseVerdict,
  REMINDER,
  MAX_REENTRIES,
  Scheduler,
  writeHandoff,
  readHandoff,
  phoenixBootstrapPrompt,
  openHostHistory,
  summarizeTurns,
  formatReport,
  loadPricesFromModelsDev,
  type TurnRecord,
} from "@darwin/core"
import { createSession, promptAsync, listMessages, compactTranscript } from "./sdk.ts"
import { dreamPrompt, distillPrompt, judgePromptText, GOAL_TEMPLATE, GOAL_CLEAR_TEMPLATE, DARWIN_TEMPLATE, RESTART_TEMPLATE } from "./prompts.ts"

type Options = {
  dream?: { auto?: boolean; intervalDays?: number }
  distill?: { auto?: boolean; intervalDays?: number }
  memoryDigestLines?: number
  attachSkills?: boolean
}

const CORPUS_DIR = join(
  ((import.meta as any).dir as string | undefined) ??
    (() => {
      try {
        return dirname(fileURLToPath(import.meta.url))
      } catch {
        return new URL(".", import.meta.url).pathname
      }
    })(),
  "..",
  "..",
  "skills",
  "skills",
)

const CONFLICT_PLUGINS: Record<string, string> = {
  "opencode-supermemory": "memory: supermemory also injects recall — expect double injection; disable one",
  "@cortexkit/opencode-magic-context": "memory: magic-context owns memory+compaction — darwin defers memory features to it",
  "opencode-goal-plugin": "goal: two auto-continue loops detected — darwin goal stays off while this is installed",
  "oh-my-opencode": "oh-my-opencode hooks compaction/todo — review its disabled_hooks before relying on darwin loops",
}

const state = {
  store: undefined as Store | undefined,
  p: darwinPaths(readEnv()),
  opts: {} as Options,
  client: undefined as any,
  directory: "",
  subagentDepth: 1,
  conflicts: [] as string[],
  backgroundCapable: false,
  injected: new Set<string>(),
  pendingJudges: new Map<string, { parent: string; condition: string; startedAt: number }>(),
  spawned: new Set<string>(),
  scheduler: undefined as Scheduler | undefined,
  pendingJudgeTimeouts: new Map<string, ReturnType<typeof setTimeout>>(),
}

function store(): Store {
  if (!state.store) {
    ensureDir(state.p.root)
    state.store = new Store(state.p.db)
  }
  return state.store
}

function due(key: string, intervalDays: number): boolean {
  const s = store()
  const last = Number(s.kvGet(key) ?? 0)
  return Date.now() - last > intervalDays * 24 * 3600_000
}

/* -------------------------------- economics -------------------------------- */

const ECONOMICS_TURNS_KEY = "economics:turns"
const ECONOMICS_BUDGET_KEY = "economics:budgetUsd"
const ECONOMICS_PRICES_CACHE_KEY = "economics:pricesCache"
const ECONOMICS_TURNS_CAP = 200

function readTurns(): TurnRecord[] {
  try {
    const raw = store().kvGet(ECONOMICS_TURNS_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as TurnRecord[]
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function writeTurns(turns: TurnRecord[]): void {
  const capped = turns.slice(-ECONOMICS_TURNS_CAP)
  store().kvSet(ECONOMICS_TURNS_KEY, JSON.stringify(capped))
}

function checkEconomicsBudget(sessionID: string, turns: TurnRecord[]): void {
  const raw = store().kvGet(ECONOMICS_BUDGET_KEY)
  if (!raw) return
  const budget = Number(raw)
  if (!Number.isFinite(budget) || budget <= 0) return
  const total = turns.reduce((s, t) => s + (Number(t.cost) || 0), 0)
  const ratio = total / budget
  if (ratio >= 1) {
    log.warn(`economics: budget exceeded $${total.toFixed(4)} / $${budget.toFixed(2)} (100%+)`)
    const note = `economics: budget $${budget.toFixed(2)} exceeded (spend $${total.toFixed(4)} over ${turns.length} turns) — consider switching to cheaper capable model; check darwin_economics report/prices for advisories.`
    try {
      const dir = state.p.sessionMemory(sessionID)
      const res = writeHandoff(dir, note)
      if (!("error" in res)) log.warn(`economics: handoff gen ${res.generation} written for budget breach`)
    } catch (err) {
      log.warn("economics: failed to write budget handoff", err instanceof Error ? err.message : String(err))
    }
  } else if (ratio >= 0.8) {
    log.warn(`economics: budget warning $${total.toFixed(4)} / $${budget.toFixed(2)} (${(ratio * 100).toFixed(0)}% — approaching limit)`)
  }
}

function handleEconomicsMessage(props: Record<string, any>): void {
  const info: Record<string, any> | undefined = props.info ?? props.message ?? props.data
  if (!info || typeof info !== "object") return
  if (info.role && info.role !== "assistant") return
  const costRaw = (info as any).cost
  if (costRaw == null) return
  const cost = Number(costRaw)
  if (!Number.isFinite(cost)) return
  const tokens: any = (info as any).tokens
  if (!tokens || typeof tokens.input !== "number" || typeof tokens.output !== "number") return
  const rec: TurnRecord = {
    sessionID: String((info as any).sessionID ?? props.sessionID ?? ""),
    modelID: String((info as any).modelID ?? ""),
    providerID: String((info as any).providerID ?? ""),
    cost,
    tokens: {
      input: Number(tokens.input) || 0,
      output: Number(tokens.output) || 0,
      cacheRead: Number(tokens.cache?.read ?? tokens.cacheRead ?? 0) || 0,
      cacheWrite: Number(tokens.cache?.write ?? tokens.cacheWrite ?? 0) || 0,
    },
    ts: Date.now(),
  }
  try {
    const turns = readTurns()
    turns.push(rec)
    writeTurns(turns)
    const sid = rec.sessionID || String(props.sessionID ?? "")
    if (sid) checkEconomicsBudget(sid, turns)
    else checkEconomicsBudget("", turns)
  } catch (err) {
    log.warn("economics: failed to append turn", err instanceof Error ? err.message : String(err))
  }
}

function economicsDreamPrompt(p: ReturnType<typeof darwinPaths>): string {
  return dreamPrompt(p) + "\n\nEconomics facet (non-prescriptive): weigh cost, cache hit rate, and model price as one facet alongside correctness when consolidating — note cheap-vs-capable tradeoffs and caching heuristics, without prescribing a model choice."
}

/* ---------------------------------- goal loop ---------------------------------- */

async function handleIdle(sessionID: string) {
  const s = store()
  const goal = s.getGoal(sessionID)
  if (!goal || !goal.active) return
  const lastCheck = Number(s.kvGet(`goal:${sessionID}:lastcheck`) ?? 0)
  if (Date.now() - lastCheck < 10_000) return
  s.kvSet(`goal:${sessionID}:lastcheck`, String(Date.now()))

  const hist = await listMessages(state.client, sessionID)
  const lastUser = [...hist].reverse().find((h) => h.role === "user" && !h.text.startsWith("<"))
  const lastText = [...hist].reverse().find((h) => h.role === "assistant")?.text ?? ""
  if (!lastText.trim()) return // nothing to judge yet

  const child = await createSession(state.client, {
    parentID: sessionID,
    title: "darwin goal judge",
    agent: "darwin-judge",
  })
  if (!child) {
    // fail-open (MiMo parity: judge errors allow stop)
    s.clearGoal(sessionID, "judge-spawn-error (fail-open)")
    log.warn("goal: judge spawn failed; goal released (fail-open)")
    return
  }
  state.spawned.add(child)
  state.pendingJudges.set(child, { parent: sessionID, condition: goal.condition, startedAt: Date.now() })
  const transcript =
    (lastUser ? `LAST USER REQUEST: ${lastUser.text.slice(0, 2000)}\n\n` : "") + compactTranscript(hist)
  await promptAsync(state.client, child, `${judgePromptText()}\n\n${JUDGE_SYSTEM}\n\n${judgePrompt(goal.condition, transcript)}`)
  const t = setTimeout(() => judgeTimeout(child), 180_000)
  state.pendingJudgeTimeouts.set(child, t)
}

async function judgeTimeout(childID: string) {
  const pending = state.pendingJudges.get(childID)
  if (!pending) return
  state.pendingJudges.delete(childID)
  const t = state.pendingJudgeTimeouts.get(childID)
  if (t) {
    clearTimeout(t)
    state.pendingJudgeTimeouts.delete(childID)
  }
  store().clearGoal(pending.parent, "judge-timeout (fail-open)")
  log.warn("goal: judge timed out; goal released (fail-open)")
}

async function judgeFinished(childID: string) {
  const pending = state.pendingJudges.get(childID)
  if (!pending) return
  state.pendingJudges.delete(childID)
  const t = state.pendingJudgeTimeouts.get(childID)
  if (t) {
    clearTimeout(t)
    state.pendingJudgeTimeouts.delete(childID)
  }
  const s = store()
  const hist = await listMessages(state.client, childID)
  const reply = [...hist].reverse().find((h) => h.role === "assistant")?.text ?? ""
  const verdict = parseVerdict(reply)
  if (!verdict) {
    s.clearGoal(pending.parent, "judge-unparseable (fail-open)")
    return
  }
  if (verdict.ok || verdict.impossible) {
    s.clearGoal(pending.parent, JSON.stringify(verdict))
    log.info(`goal: satisfied (${verdict.impossible ? "impossible" : "ok"}) — ${verdict.reason}`)
    return
  }
  const goal = s.getGoal(pending.parent)
  const reentries = goal?.reentries ?? 0
  if (reentries >= MAX_REENTRIES) {
    s.clearGoal(pending.parent, JSON.stringify({ ...verdict, cap: true }))
    log.warn("goal: re-entry cap reached; goal released")
    return
  }
  s.bumpGoal(pending.parent, verdict.reason)
  await promptAsync(state.client, pending.parent, REMINDER(pending.condition, verdict.reason, MAX_REENTRIES - reentries))
}

/* ---------------------------------- cadence ---------------------------------- */

async function runCadence(_parentless = false) {
  const env = readEnv()
  if (env.disable || env.disableAuto) return
  const s = store()
  const dreamAuto = state.opts.dream?.auto ?? false
  const distillAuto = state.opts.distill?.auto ?? false
  if (dreamAuto && due("dream:last", state.opts.dream?.intervalDays ?? 7)) {
    s.kvSet("dream:last", String(Date.now()))
    const id = await createSession(state.client, {
      title: "Darwin Auto Dream",
      agent: "darwin-dream",
    })
    if (id) {
      state.spawned.add(id)
      await promptAsync(state.client, id, "Run the dream consolidation now for this project. Follow your instructions exactly and report the summary.")
    }
  }
  if (distillAuto && due("distill:last", state.opts.distill?.intervalDays ?? 30)) {
    s.kvSet("distill:last", String(Date.now()))
    const id = await createSession(state.client, { title: "Darwin Auto Distill", agent: "darwin-distill" })
    if (id) {
      state.spawned.add(id)
      await promptAsync(state.client, id, "Run distill now for this project. Follow your instructions exactly and report the summary.")
    }
  }
}

/* ---------------------------------- phoenix ---------------------------------- */

function tuiProcessPid(): number | null {
  const ppid = process.ppid
  if (ppid <= 1) return null
  try {
    const cmdline = readFileSync(join("/proc", String(ppid), "cmdline"), "utf8")
    if (cmdline.includes("opencode")) return ppid
  } catch {
    /* not linux / no proc */
  }
  return null
}

function phoenixRestart(sessionID: string, note: string, force: boolean): string {
  const sessionDir = state.p.sessionMemory(sessionID)
  const wrote = writeHandoff(sessionDir, note, force)
  if ("error" in wrote) return `phoenix: refused — ${wrote.error}`
  const bootstrap = phoenixBootstrapPrompt(sessionDir, note || "continue the prior task")
  const tui = tuiProcessPid()
  if (tui) {
    try {
      process.kill(tui, "SIGUSR2")
      return `phoenix: handoff generation ${wrote.generation} written; SIGUSR2 sent to TUI (pid ${tui}) — config+plugins reload in place; the next session picks up the handoff at ${join(sessionDir, "handoff.json")}`
    } catch (err) {
      log.warn("phoenix: SIGUSR2 failed, falling back to detached spawn", err)
    }
  }
  try {
    const child = spawn(
      "opencode",
      ["run", "--format", "json", "--dir", state.directory, bootstrap],
      { detached: true, stdio: "ignore", cwd: state.directory },
    )
    child.unref()
    return `phoenix: handoff generation ${wrote.generation} written; fresh \`opencode run\` process spawned (pid ${child.pid}) — it reads the handoff and continues; this session should stop now.`
  } catch (err) {
    return `phoenix: handoff generation ${wrote.generation} written, but no reload path succeeded (${err instanceof Error ? err.message : String(err)}). Restart the host manually and continue from the handoff.`
  }
}

/* ---------------------------------- tools ---------------------------------- */

const memoryTool = tool({
  description:
    "Search and manage darwin persistent memory (project/global MEMORY.md, session notes). Operations: search (BM25 over the memory index), add (append durable entry), get (read a memory file head), stats, reconcile (rescan disk).",
  args: {
    operation: z.enum(["search", "add", "get", "stats", "reconcile"]),
    query: z.string().optional().describe("search query or note text (for add)"),
    type: z.string().optional().describe("memory type filter (memory/notes/checkpoint/…)"),
    scope: z.enum(["project", "global"]).optional().describe("write scope for add"),
    path: z.string().optional().describe("absolute file path for get"),
  },
  async execute(args, ctx) {
    const s = store()
    const dir = (ctx as any).directory ?? state.directory
    ensureMemorySkeleton(state.p, dir)
    switch (args.operation) {
      case "search": {
        if (!args.query) return "error: query required"
        reconcile(s, state.p)
        const hits = s.search(args.query, { type: args.type, limit: 8 })
        if (hits.length === 0) return `no memory hits for "${args.query}"`
        return hits
          .map((h) => `[${h.score}] ${h.type} ${h.path}\n  ${h.snippet.slice(0, 200)}`)
          .join("\n")
      }
      case "add": {
        if (!args.query) return "error: note text required"
        const file =
          args.scope === "global"
            ? globalMemoryPath(state.p)
            : projectMemoryPath(state.p, dir)
        appendEntry(file, args.scope === "global" ? "Global memory" : "Project memory", args.query)
        return `appended to ${file}`
      }
      case "get": {
        if (!args.path) return "error: path required"
        return readHead(args.path, 80) ?? "file not found"
      }
      case "stats": {
        const r = reconcile(s, state.p)
        return `indexed=${r.indexed} pruned=${r.pruned} fts=${s.hasFts()}`
      }
      default:
        return JSON.stringify(reconcile(s, state.p))
    }
  },
})

const historyTool = tool({
  description:
    "Read-only search over the HOST's raw session trajectory (all sessions, messages, tool calls) — use for verifying memory against what actually happened, or finding past sessions. Operations: recentSessions, searchMessages, sessionParts.",
  args: {
    operation: z.enum(["recentSessions", "searchMessages", "sessionParts"]),
    query: z.string().optional().describe("substring to search in messages"),
    sessionId: z.string().optional(),
    limit: z.number().optional(),
  },
  async execute(args) {
    const h = openHostHistory()
    if (!h) return "host trajectory database not found (unsupported schema or path) — ask the user where the opencode db lives"
    try {
      if (args.operation === "recentSessions")
        return h
          .recentSessions(args.limit ?? 10)
          .map((s) => `${s.id} ${s.time ?? ""} ${s.directory ?? ""} — ${s.title ?? ""}`)
          .join("\n") || "no sessions"
      if (args.operation === "searchMessages") {
        if (!args.query) return "error: query required"
        return h
          .searchMessages(args.query, args.limit ?? 10)
          .map((m) => `[${m.role}] ${m.session_id}: ${m.preview}`)
          .join("\n") || "no hits"
      }
      if (!args.sessionId) return "error: sessionId required"
      return h
        .sessionParts(args.sessionId, args.limit ?? 40)
        .map((p) => `${p.kind}${p.tool ? `(${p.tool})` : ""}: ${p.preview}`)
        .join("\n") || "no parts"
    } finally {
      h.close()
    }
  },
})

const doctorTool = tool({
  description:
    "darwin self-diagnostics: memory index health, background-subagent capability, conflicts with other plugins, goal/cadence status, phoenix handoff state. Operations: report, restart (phoenix).",
  args: {
    operation: z.enum(["report", "restart"]).default("report"),
    note: z.string().optional().describe("handoff note for restart"),
    force: z.boolean().optional(),
  },
  async execute(args, ctx) {
    if (args.operation === "restart") return phoenixRestart(ctx.sessionID, args.note ?? "", args.force ?? false)
    const s = store()
    const r = reconcile(s, state.p)
    const handoff = readHandoff(state.p.sessionMemory(ctx.sessionID))
    const lines = [
      `darwin home        ${state.p.root}`,
      `db                 ${state.p.db} (fts=${s.hasFts() ? "fts5" : "LIKE-fallback"})`,
      `memory index       ${r.indexed} files (pruned ${r.pruned})`,
      `corpus             ${CORPUS_DIR}${existsSync(CORPUS_DIR) ? "" : " (MISSING)"}`,
      `background subs    ${state.backgroundCapable ? "available" : "NOT available — set OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=1 in your shell profile and restart; model-facing subagents are blocking until then"}`,
      `subagent_depth     ${state.subagentDepth}`,
      `conflicts          ${state.conflicts.length ? state.conflicts.join("; ") : "none detected"}`,
      `goal               ${(() => {
        const g = s.getGoal(ctx.sessionID)
        return g && g.active ? `ACTIVE (${g.reentries}/${MAX_REENTRIES} re-entries) — ${g.condition.slice(0, 80)}` : "none"
      })()}`,
      `cadence            dream auto=${state.opts.dream?.auto ?? false} last=${s.kvGet("dream:last") ?? "never"} · distill auto=${state.opts.distill?.auto ?? false} last=${s.kvGet("distill:last") ?? "never"}`,
      `phoenix            ${handoff ? `generation ${handoff.generation} @ ${new Date(handoff.createdAt).toISOString()}` : "no handoff"}`,
    ]
    return lines.join("\n")
  },
})

const economicsTool = tool({
  description:
    "Darwin economics (non-prescriptive facet): cost/cache-aware reflection. Operations: report (spend/tokens/cache hit over recent turns) and prices (models.dev catalog, offline-first). Economics is one facet alongside correctness — not prescriptive.",
  args: {
    operation: z.enum(["report", "prices"]),
    window: z.coerce.number().int().positive().optional().describe("number of recent turns to include in report (default all)"),
  },
  async execute(args) {
    const s = store()
    if (args.operation === "report") {
      let turns: TurnRecord[] = []
      try {
        const raw = s.kvGet(ECONOMICS_TURNS_KEY)
        turns = raw ? (JSON.parse(raw) as TurnRecord[]) : []
        if (!Array.isArray(turns)) turns = []
      } catch {
        turns = []
      }
      if (turns.length === 0) {
        return [
          "no turn data yet — economics tracking starts after first darwin session",
          "advisories:",
          "  - keep prefix byte-stable (tools→system→history) for cache hits — cache hit <30% with >5 turns suggests reflowing early context",
          "  - prefer cheaper capable models for known workflows when cheapest is >2× cheaper per turn",
          "  - budget guard: set economics:budgetUsd in KV to enable 80%/100% advisories (e.g. via direct KV or future budget op)",
          "  - prices: run darwin_economics with operation prices to view models.dev catalog (cached/offline-first)",
        ].join("\n")
      }
      const win = args.window && args.window > 0 ? Math.min(args.window, turns.length) : turns.length
      const slice = turns.slice(-win)
      const report = summarizeTurns(slice)
      const budgetRaw = s.kvGet(ECONOMICS_BUDGET_KEY)
      if (budgetRaw) {
        const budget = Number(budgetRaw)
        if (Number.isFinite(budget) && budget > 0) {
          const pct = (report.totalCost / budget) * 100
          if (pct >= 100)
            report.advisories.push(
              `budget exceeded: $${report.totalCost.toFixed(4)} / $${budget.toFixed(2)} (100%+) — consider cheaper model; handoff suggested`,
            )
          else if (pct >= 80)
            report.advisories.push(
              `budget warning: $${report.totalCost.toFixed(4)} / $${budget.toFixed(2)} (${pct.toFixed(0)}%) — approaching limit`,
            )
        }
      }
      return formatReport(report)
    }
    // prices
    const cached = s.kvGet(ECONOMICS_PRICES_CACHE_KEY)
    if (cached) {
      try {
        const raw = JSON.parse(cached) as Record<string, any>
        const m = loadPricesFromModelsDev(raw)
        const cheapest = [...m.values()]
          .filter((v) => !v.free)
          .sort((a, b) => a.price.input - b.price.input)
          .slice(0, 5)
        const lines = [
          `models.dev catalog (cached): ${m.size} models`,
          ...cheapest.map(
            (v) => `  ${v.provider}/${v.model}: $${v.price.input}/$${v.price.output} per MTok (cacheRead $${v.price.cacheRead})${v.free ? " free" : ""}`,
          ),
        ]
        if (cheapest.length === 0) lines.push("  (no priced models in cache — all free or empty)")
        return lines.join("\n")
      } catch {
        // fall through to fetch
      }
    }
    try {
      const controller = new AbortController()
      const tid = setTimeout(() => controller.abort(), 5000)
      const res = await fetch("https://models.dev/api.json", { signal: controller.signal } as any)
      clearTimeout(tid)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const raw = (await res.json()) as Record<string, any>
      try {
        s.kvSet(ECONOMICS_PRICES_CACHE_KEY, JSON.stringify(raw))
      } catch {}
      const m = loadPricesFromModelsDev(raw)
      const cheapest = [...m.values()]
        .filter((v) => !v.free)
        .sort((a, b) => a.price.input - b.price.input)
        .slice(0, 5)
      const lines = [
        `models.dev catalog (fetched): ${m.size} models`,
        ...cheapest.map((v) => `  ${v.provider}/${v.model}: $${v.price.input}/$${v.price.output} per MTok`),
      ]
      if (cheapest.length === 0) lines.push("  (no priced models)")
      return lines.join("\n")
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      return `prices catalog unavailable offline — ${msg}; cached catalog empty; will populate when online. General heuristic: prefer cheaper capable models for known workflows; keep prefix stable for cache.`
    }
  },
})

/* ---------------------------------- plugin ---------------------------------- */

const plugin: Plugin = async (input, optionsRaw) => {
  const options = (optionsRaw ?? {}) as Options
  const env = readEnv()
  if (env.disable) {
    log.info("DARWIN_DISABLE set — inert")
    return {}
  }
  state.p = darwinPaths(env)
  state.opts = options
  state.client = input.client
  state.directory = input.directory
  ensureMemorySkeleton(state.p, input.directory)
  store()

  state.scheduler = new Scheduler(state.p, async (t) => {
    log.debug("scheduler fired", t.name)
    if (t.name === "cadence") await runCadence(true)
  })
  state.scheduler.start()

  return {
    config: async (cfg) => {
      // Mutate the live merged config; cast is deliberate — the shipped Config
      // type is narrower than the runtime object (subagent_depth, skills, agent
      // details all exist at runtime).
      const c = cfg as any
      c.subagent_depth = Math.max(c.subagent_depth ?? 1, 3)
      state.subagentDepth = c.subagent_depth
      c.agent ??= {}
      c.agent["darwin-dream"] = {
        description: "Memory consolidation (dream)",
        mode: "subagent",
        hidden: true,
        prompt: economicsDreamPrompt(state.p),
        permission: { edit: "deny", bash: "allow", external_directory: { [`${state.p.memory}/**`]: "allow" } },
      }
      c.agent["darwin-distill"] = {
        description: "Workflow packaging (distill)",
        mode: "subagent",
        hidden: true,
        prompt: distillPrompt(state.p),
        permission: { edit: "deny", bash: "allow", external_directory: { [`${state.p.memory}/**`]: "allow" } },
      }
      c.agent["darwin-judge"] = {
        description: "Goal verifier",
        mode: "subagent",
        hidden: true,
        prompt: judgePromptText(),
        permission: { edit: "deny", bash: "deny" },
      }
      c.command ??= {}
      c.command.goal = { description: "Set a session goal darwin verifies on every stop", template: GOAL_TEMPLATE }
      c.command["goal-clear"] = { description: "Clear the session goal", template: GOAL_CLEAR_TEMPLATE }
      c.command.dream = { description: "Consolidate memory now", template: "Run the dream consolidation now. Follow your instructions exactly.", agent: "darwin-dream" }
      c.command.distill = { description: "Package repeated workflows into skills", template: "Run distill now. Follow your instructions exactly.", agent: "darwin-distill" }
      c.command.darwin = { description: "darwin diagnostics", template: DARWIN_TEMPLATE }
      c.command["darwin-restart"] = { description: "Phoenix: handoff + reload host", template: RESTART_TEMPLATE }
      if (state.opts.attachSkills !== false) {
        c.skills ??= { paths: [], urls: [] }
        const paths = new Set<string>(c.skills.paths ?? [])
        if (existsSync(CORPUS_DIR)) paths.add(CORPUS_DIR)
        paths.add(join(input.directory, ".darwin", "skills"))
        c.skills.paths = [...paths]
      }
      const installed: unknown[] = c.plugin ?? []
      const seen = new Set(
        installed.flatMap((e) => {
          const name = Array.isArray(e) ? e[0] : e
          return typeof name === "string" ? [name] : []
        }),
      )
      for (const [name, note] of Object.entries(CONFLICT_PLUGINS))
        if ([...seen].some((s) => s === name || s.startsWith(`${name}@`))) state.conflicts.push(`${name}: ${note}`)
    },

    "tool.definition": async (input2, output) => {
      if (input2.toolID === "task" && output.parameters) {
        const has = "background" in (output.parameters as object) || JSON.stringify(output.parameters).includes('"background"')
        if (has && !state.backgroundCapable) {
          state.backgroundCapable = true
          log.info("background subagents detected as available")
        }
      }
    },

    "experimental.chat.system.transform": async (_input2, output) => {
      output.system.push(
        [
          "## darwin memory",
          "Persistent memory lives in markdown files: project MEMORY.md (rules, architecture decisions, durable knowledge, patterns, gotchas), global MEMORY.md (cross-project preferences), session notes. A digest is injected at session start; recall more with the darwin_memory tool (BM25 search).",
          "Active recall protocol: before re-reading project files or re-deriving facts, search darwin_memory. Do not re-read files already summarized in the injected digest. Record durable discoveries with darwin_memory add; keep it dense (merge, don't duplicate).",
        ].join("\n"),
      )
    },

    "chat.message": async (input2, output) => {
      const sid = input2.sessionID
      if (state.injected.has(sid) || state.spawned.has(sid)) return
      state.injected.add(sid)
      const isEval = !!process.env.DARWIN_EVAL || (process.env.DARWIN_HOME ?? "").includes("night")
      const rawQuery =
        (input2 as any).text ??
        (input2 as any).prompt ??
        (output as any).message?.text ??
        (output.parts.find((p: any) => p?.type === "text" && typeof p?.text === "string") as any)?.text ??
        ""
      const query = typeof rawQuery === "string" ? rawQuery : String(rawQuery ?? "")
      const projectLines = state.opts.memoryDigestLines ? Math.max(10, state.opts.memoryDigestLines - 15) : 40
      const notesLines = 15
      const dir = (input2 as any).directory ?? state.directory
      const project = readHead(projectMemoryPath(state.p, dir), projectLines)
      const notes = readHead(sessionNotesPath(state.p, sid), notesLines)
      let hits: { snippet: string; type: string; path: string; score: number }[] = []
      try {
        const s = store()
        reconcile(s, state.p)
        if (query.trim()) {
          const pid = projectId(dir)
          hits = s.search(query, { scope: "projects", scopeId: pid, limit: 4, floor: 0.25 })
        }
      } catch (err) {
        log.warn("darwin: BM25 search failed", err instanceof Error ? err.message : String(err))
      }
      // Always include Patterns/Gotchas (working style) — these transfer even when specific fixes don't
      const patterns = (() => {
        try {
          const full = readHead(projectMemoryPath(state.p, dir), 500) ?? ""
          const sections = full.split(/^## /m)
          const keep = sections.filter(s => s.startsWith("Patterns") || s.startsWith("Gotchas"))
          if (!keep.length) return ""
          // Trim to ~15 lines max for Patterns/Gotchas
          const text = ("## " + keep.join("## ")).split("\n").slice(0, 15).join("\n")
          // Avoid duplicating if already in project head
          if (project && text.split("\n").every(l => !l.trim() || project.includes(l.trim().slice(0, 40)))) return ""
          return text
        } catch { return "" }
      })()
      const digestParts: string[] = []
      if (project) digestParts.push(`### Project memory\n${project}`)
      if (patterns) digestParts.push(patterns)
      if (!isEval) {
        // global suppressed for eval; keep suppressed entirely to avoid +138% cost bloat.
        // Intentionally no global head injection here. If needed outside eval, use BM25 hits which already cover global via search scope if desired.
        void globalMemoryPath
      }
      if (notes) digestParts.push(`### Session notes (continuation)\n${notes}`)
      if (hits.length > 0) {
        const snippets = hits
          .slice(0, 4)
          .map((h) => `- ${h.snippet} [${h.type}:${h.path.split("/").pop()}]`)
          .join("\n")
        digestParts.push(`### Relevant memory\n${snippets}`)
      }
      const digest = digestParts.filter(Boolean).join("\n\n")
      if (digest)
        output.parts.unshift({
          id: `prt_${crypto.randomUUID().replace(/-/g, "").slice(0, 24)}`,
          sessionID: sid,
          messageID: (input2 as any).messageID ?? (output as any).message?.id ?? `msg_${crypto.randomUUID().replace(/-/g, "").slice(0, 24)}`,
          type: "text",
          text: `<darwin_memory_digest>\n${digest}\n</darwin_memory_digest>`,
        } as never)
      await runCadence()
    },

    "command.execute.before": async (input2) => {
      if (input2.command === "goal") {
        const cond = (input2.arguments ?? "").trim()
        if (cond) store().setGoal(input2.sessionID, cond)
        else store().clearGoal(input2.sessionID, "cleared via /goal (empty)")
      }
      if (input2.command === "goal-clear") store().clearGoal(input2.sessionID, "cleared via /goal-clear")
    },

    event: async ({ event }) => {
      const type = event.type
      const props = event.properties as Record<string, any>
      if (type === "session.status" && props?.sessionID && props.status?.type === "idle") {
        if (state.pendingJudges.has(props.sessionID)) await judgeFinished(props.sessionID)
        else if (!state.spawned.has(props.sessionID)) await handleIdle(props.sessionID)
      } else if (type === "session.idle" && props?.sessionID) {
        if (state.pendingJudges.has(props.sessionID)) await judgeFinished(props.sessionID)
        else if (!state.spawned.has(props.sessionID)) await handleIdle(props.sessionID)
      } else if (type === "session.created" && props?.info?.id) {
        const title = String(props.info.title ?? "")
        if (title.startsWith("Darwin")) state.spawned.add(props.info.id)
      } else if (type === "message.updated") {
        try {
          handleEconomicsMessage(props)
        } catch (err) {
          log.warn("economics: message.updated handling failed", err instanceof Error ? err.message : String(err))
        }
      } else if (type === "message.part.updated") {
        // stream events: ignore
      }
    },

    tool: {
      darwin_memory: memoryTool,
      darwin_history: historyTool,
      darwin_doctor: doctorTool,
      darwin_economics: economicsTool,
    },

    dispose: async () => {
      for (const t of state.pendingJudgeTimeouts.values()) clearTimeout(t)
      state.pendingJudgeTimeouts.clear()
      state.pendingJudges.clear()
      state.scheduler?.stop()
      state.scheduler = undefined
      try {
        state.store?.close()
      } catch {}
      state.store = undefined
      state.injected.clear()
      state.spawned.clear()
      state.conflicts.length = 0
    },
  }
}

export default plugin satisfies Plugin
