import { readdirSync, readFileSync, statSync, existsSync, writeFileSync, mkdirSync, renameSync } from "node:fs"
import { join, basename } from "node:path"
import type { Store } from "./store.ts"
import type { Paths } from "./env.ts"
import { ensureDir } from "./env.ts"

/** Type detection (MiMo-Code memory/paths.ts parity, trimmed). */
export function detectType(relPath: string): string {
  const name = basename(relPath).toLowerCase()
  if (name === "memory.md") return "memory"
  if (name.startsWith("checkpoint")) return "checkpoint"
  if (name === "notes.md") return "notes"
  if (relPath.includes("tasks/") && name === "progress.md") return "progress"
  if (name === "archive.md") return "archive"
  if (name === "handoff.json") return "handoff"
  return "free"
}

export function fingerprint(absPath: string): string {
  const st = statSync(absPath)
  return `${st.size}-${Math.trunc(st.mtimeMs)}`
}

function walkMarkdown(dir: string, base = dir): { abs: string; rel: string }[] {
  const out: { abs: string; rel: string }[] = []
  if (!existsSync(dir)) return out
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const abs = join(dir, entry.name)
    if (entry.isDirectory()) out.push(...walkMarkdown(abs, base))
    else if (entry.name.endsWith(".md")) out.push({ abs, rel: abs.slice(base.length + 1) })
  }
  return out
}

/** Walk the memory tree, upsert changed files, prune vanished ones. Returns counts. */
export function reconcile(store: Store, p: Paths): { indexed: number; pruned: number } {
  if (!existsSync(p.memory)) return { indexed: 0, pruned: 0 }
  const files = walkMarkdown(p.memory)
  const disk = new Map<string, string>()
  for (const f of files) {
    const rel = f.rel.replaceAll("\\", "/")
    const parts = rel.split("/")
    const scope = parts[0] === "global" ? "global" : parts[0] === "sessions" ? "sessions" : "projects"
    const scopeId = scope === "projects" ? (parts[1] ?? "") : scope === "sessions" ? (parts[1] ?? "") : ""
    const fp = fingerprint(f.abs)
    disk.set(f.abs, fp)
    store.upsertMemory(
      { path: f.abs, scope: scope as "global" | "projects" | "sessions", scope_id: scopeId, type: detectType(rel), fingerprint: fp },
      readFileSync(f.abs, "utf8"),
    )
  }
  let pruned = 0
  for (const row of store.listPaths()) {
    const fp = disk.get(row.path)
    if (fp === undefined) {
      store.removeMemoryByPrefix(row.path)
      pruned++
    }
  }
  return { indexed: disk.size, pruned }
}

/** First `maxLines` lines of a memory file, or null. Used for session-start digests. */
export function readHead(absPath: string, maxLines = 60): string | null {
  if (!existsSync(absPath)) return null
  const text = readFileSync(absPath, "utf8")
  const lines = text.split("\n")
  if (lines.length <= maxLines) return text
  return lines.slice(0, maxLines).join("\n") + `\n… (${lines.length - maxLines} more lines; use darwin_memory to search)`
}

/** Append a dated entry to a memory file, creating dirs/heading as needed. Atomic-ish write. */
export function appendEntry(absPath: string, heading: string, entry: string): void {
  const dir = join(absPath, "..")
  ensureDir(dir)
  const today = new Date().toISOString().slice(0, 10)
  const existing = existsSync(absPath) ? readFileSync(absPath, "utf8") : `# ${heading}\n`
  const block = `\n- ${entry} [${today}]\n`
  const tmp = absPath + ".tmp"
  writeFileSync(tmp, existing.trimEnd() + "\n" + block.trimEnd() + "\n")
  renameSync(tmp, absPath)
}

export function projectMemoryPath(p: Paths, projectDir: string): string {
  return join(p.projectMemory(projectDir), "MEMORY.md")
}

export function projectArchivePath(p: Paths, projectDir: string): string {
  return join(p.projectMemory(projectDir), "archive.md")
}

export function globalMemoryPath(p: Paths): string {
  return join(p.globalMemory, "MEMORY.md")
}

export function sessionNotesPath(p: Paths, sessionID: string): string {
  ensureDir(p.sessionMemory(sessionID))
  return join(p.sessionMemory(sessionID), "notes.md")
}

export function ensureMemorySkeleton(p: Paths, projectDir: string): void {
  ensureDir(p.projectMemory(projectDir))
  ensureDir(p.globalMemory)
  mkdirSync(p.root, { recursive: true })
  if (!existsSync(projectMemoryPath(p, projectDir)))
    writeFileSync(projectMemoryPath(p, projectDir), "# Project memory\n\n## Rules\n\n## Architecture decisions\n\n## Discovered durable knowledge\n\n## Patterns\n\n## Gotchas\n")
  if (!existsSync(globalMemoryPath(p))) writeFileSync(globalMemoryPath(p), "# Global memory\n")
}
