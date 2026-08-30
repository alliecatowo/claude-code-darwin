import { existsSync, readFileSync, writeFileSync, renameSync, mkdirSync } from "node:fs"
import { join } from "node:path"
import { log } from "./env.ts"

/**
 * Phoenix handoff — generation-numbered continuation notes.
 * Design per research 2026-08-29: idempotent, generation-checked, guard-railed,
 * because CC's supervisor may re-dispatch and oc's `--continue` may double-fire.
 */

export type Handoff = {
  generation: number
  createdAt: number
  note: string
  spawn?: { kind: "sigusr2" | "opencode-run" | "claude-bg"; target?: string }
}

export const MAX_GENERATIONS = 10
export const MIN_INTERVAL_MS = 5 * 60_000

export function writeHandoff(sessionDir: string, note: string, force = false): Handoff | { error: string } {
  const prev = readHandoff(sessionDir)
  if (prev) {
    if (!force && prev.generation >= MAX_GENERATIONS)
      return { error: `generation cap reached (${MAX_GENERATIONS}); refusing to spawn another successor` }
    if (!force && Date.now() - prev.createdAt < MIN_INTERVAL_MS)
      return { error: "phoenix interval guard: too soon since last restart (use force to override)" }
  }
  const h: Handoff = { generation: (prev?.generation ?? 0) + 1, createdAt: Date.now(), note }
  mkdirSync(sessionDir, { recursive: true })
  const tmp = join(sessionDir, "handoff.json.tmp")
  writeFileSync(tmp, JSON.stringify(h, null, 2))
  renameSync(tmp, join(sessionDir, "handoff.json"))
  log.info(`phoenix: wrote handoff generation ${h.generation}`)
  return h
}

export function readHandoff(sessionDir: string): Handoff | null {
  const p = join(sessionDir, "handoff.json")
  if (!existsSync(p)) return null
  try {
    return JSON.parse(readFileSync(p, "utf8")) as Handoff
  } catch {
    return null
  }
}

export function phoenixBootstrapPrompt(sessionDir: string, task: string): string {
  const h = readHandoff(sessionDir)
  const gen = h?.generation ?? 0
  return [
    `You are darwin generation ${gen}. A previous session handed off to you.`,
    `Read the handoff note at ${join(sessionDir, "handoff.json")} and follow it. It is authoritative for continuing prior work.`,
    `Task: ${task}`,
    `Guards: if you have already processed this handoff generation (check for duplicate effects before acting), stop and report instead of repeating work. Do not write a new handoff unless the task requires another restart; never exceed ${MAX_GENERATIONS} generations without human approval.`,
  ].join("\n\n")
}
