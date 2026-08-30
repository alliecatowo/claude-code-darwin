import { existsSync, readdirSync, statSync } from "node:fs"
import { join } from "node:path"
import { Database } from "bun:sqlite"
import { log } from "./env.ts"

/**
 * Read-only access to the HOST's trajectory database (opencode flavors).
 * Schema is internal and version-dependent: every query is guarded by
 * sqlite_master checks and degrades to an honest error, never a crash,
 * never a write.
 */

export type HostHistory = {
  dbPath: string
  close(): void
  hasTable(name: string): boolean
  recentSessions(limit: number): { id: string; title: string | null; directory: string | null; time: string | null }[]
  searchMessages(like: string, limit: number): { session_id: string; role: string | null; preview: string }[]
  sessionParts(sessionID: string, limit: number): { kind: string; tool: string | null; preview: string }[]
}

function tables(db: InstanceType<typeof Database>): Set<string> {
  const rows = db
    .query("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
    .all() as { name: string }[]
  return new Set(rows.map((r) => r.name))
}

export function discoverHostDb(): string | null {
  const candidates: string[] = []
  const push = (dir: string) => {
    try {
      if (!existsSync(dir)) return
      for (const f of readdirSync(dir)) {
        if (f.endsWith(".db") || f.endsWith(".sqlite") || f.endsWith(".sqlite3"))
          candidates.push(join(dir, f))
      }
    } catch {
      /* ignore */
    }
  }
  if (process.env.OPENCODE_DB) candidates.push(process.env.OPENCODE_DB)
  const dataHome = process.env.XDG_DATA_HOME ?? join(process.env.HOME ?? ".", ".local/share")
  push(join(dataHome, "opencode"))
  push(join(process.env.HOME ?? ".", ".local/share/opencode"))
  if (candidates.length === 0) return null
  // newest wins
  let best = candidates[0]
  let bestMtime = 0
  for (const c of candidates) {
    try {
      const mtime = Math.trunc(statSync(c).mtimeMs)
      if (mtime > bestMtime) ((bestMtime = mtime), (best = c))
    } catch {
      /* ignore */
    }
  }
  return best
}

export function openHostHistory(dbPath?: string): HostHistory | null {
  const path = dbPath ?? discoverHostDb()
  if (!path || !existsSync(path)) return null
  let db: InstanceType<typeof Database>
  try {
    db = new Database(path, { readonly: true } as any)
  } catch {
    try {
      db = new Database(path)
    } catch (err) {
      log.warn("host history db unavailable", path, err)
      return null
    }
  }
  const t = tables(db)
  const sessionTable = t.has("session") ? "session" : t.has("sessions") ? "sessions" : null
  const messageTable = t.has("message") ? "message" : t.has("messages") ? "messages" : null
  const partTable = t.has("part") ? "part" : t.has("parts") ? "parts" : null
  return {
    dbPath: path,
    hasTable: (n) => t.has(n),
    close: () => db.close(),
    recentSessions(limit) {
      if (!sessionTable) return []
      try {
        const rows = db
          .query(`SELECT * FROM ${sessionTable} ORDER BY rowid DESC LIMIT ?`)
          .all(limit) as Record<string, unknown>[]
        return rows.map((r) => ({
          id: String(r.id ?? r.session_id ?? ""),
          title: r.title == null ? null : String(r.title),
          directory: r.directory == null ? null : String(r.directory),
          time: r.time_created == null ? null : String(r.time_created),
        }))
      } catch {
        return []
      }
    },
    searchMessages(like, limit) {
      if (!messageTable) return []
      try {
        const rows = db
          .query(
            `SELECT session_id, data FROM ${messageTable} WHERE CAST(data AS TEXT) LIKE ? ORDER BY rowid DESC LIMIT ?`,
          )
          .all(`%${like}%`, limit) as { session_id: string; data: unknown }[]
        return rows.map((r) => {
          const d = (typeof r.data === "string" ? JSON.parse(r.data) : (r.data ?? {})) as {
            role?: string
          }
          const text = JSON.stringify(d)
          return { session_id: r.session_id, role: d.role ?? null, preview: text.slice(0, 200) }
        })
      } catch {
        return []
      }
    },
    sessionParts(sessionID, limit) {
      if (!partTable || !messageTable) return []
      try {
        const rows = db
          .query(
            `SELECT p.data FROM ${partTable} p JOIN ${messageTable} m ON p.message_id = m.id
             WHERE m.session_id = ? ORDER BY p.rowid DESC LIMIT ?`,
          )
          .all(sessionID, limit) as { data: unknown }[]
        return rows
          .reverse()
          .map((r) => {
            const d = (typeof r.data === "string" ? JSON.parse(r.data) : (r.data ?? {})) as {
              type?: string
              tool?: string
              text?: string
            }
            const preview =
              d.text ??
              JSON.stringify(d)
                .replace(/\s+/g, " ")
                .slice(0, 200)
            return { kind: d.type ?? "?", tool: d.tool ?? null, preview }
          })
      } catch {
        return []
      }
    },
  }
}
