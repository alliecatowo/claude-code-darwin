import { existsSync, mkdirSync } from "node:fs"
import { join } from "node:path"
import { createHash } from "node:crypto"

export type DarwinEnv = {
  home: string
  db: string
  disable: boolean
  disableAuto: boolean
  logLevel: "debug" | "info" | "warn" | "error"
}

function xdg(sub: string, fallback: string): string {
  const env = process.env[`XDG_${sub}_HOME`]
  if (env && env.length > 0) return env
  return join(process.env.HOME ?? ".", fallback)
}

export function readEnv(): DarwinEnv {
  const home = process.env.DARWIN_HOME?.length
    ? process.env.DARWIN_HOME
    : join(xdg("DATA", ".local/share"), "darwin")
  return {
    home,
    db: process.env.DARWIN_DB?.length ? process.env.DARWIN_DB : join(home, "darwin.db"),
    disable: !!process.env.DARWIN_DISABLE,
    disableAuto: !!process.env.DARWIN_DISABLE_AUTO,
    logLevel: (process.env.DARWIN_LOG_LEVEL as DarwinEnv["logLevel"]) ?? "info",
  }
}

export type Paths = {
  root: string
  db: string
  memory: string
  globalMemory: string
  projectMemory: (projectDir: string) => string
  sessionMemory: (sessionID: string) => string
  lock: string
  tasks: string
}

export function paths(env: DarwinEnv = readEnv()): Paths {
  const root = env.home
  const memory = join(root, "memory")
  return {
    root,
    db: env.db,
    memory,
    globalMemory: join(memory, "global"),
    projectMemory: (dir) => join(memory, "projects", projectId(dir)),
    sessionMemory: (sessionID) => join(memory, "sessions", sessionID),
    lock: join(root, "scheduler.lock"),
    tasks: join(root, "tasks.json"),
  }
}

export function projectId(projectDir: string): string {
  // sha256 of absolute path, first 12 hex chars (MiMo-Code parity)
  return createHash("sha256").update(projectDir).digest("hex").slice(0, 12)
}

export function ensureDir(path: string): string {
  if (!existsSync(path)) mkdirSync(path, { recursive: true })
  return path
}

export const log = {
  debug: (...a: unknown[]) => maybe("debug", ...a),
  info: (...a: unknown[]) => maybe("info", ...a),
  warn: (...a: unknown[]) => maybe("warn", ...a),
  error: (...a: unknown[]) => maybe("error", ...a),
}
const levels = { debug: 10, info: 20, warn: 30, error: 40 }
function maybe(level: keyof typeof levels, ...a: unknown[]) {
  const conf = readEnv()
  if (levels[level] < levels[conf.logLevel] || conf.disable) return
  console.error(`[darwin:${level}]`, ...a)
}
