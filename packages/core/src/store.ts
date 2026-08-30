import { Database } from "bun:sqlite"
import { ftsQuery } from "./query.ts"

export type MemoryRow = {
  path: string
  scope: "global" | "projects" | "sessions"
  scope_id: string
  type: string
  fingerprint: string
  last_indexed_at: number
}

export type SearchHit = {
  path: string
  scope: string
  scope_id: string
  type: string
  score: number
  snippet: string
}

export class Store {
  readonly db: InstanceType<typeof Database>
  readonly dbPath: string
  private fts: boolean

  constructor(dbPath: string) {
    this.dbPath = dbPath
    this.db = new Database(dbPath)
    this.db.exec("PRAGMA journal_mode = WAL")
    this.fts = true
    this.migrate()
  }

  private migrate() {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS memory (
        rowid INTEGER PRIMARY KEY AUTOINCREMENT,
        path TEXT UNIQUE NOT NULL,
        scope TEXT NOT NULL,
        scope_id TEXT NOT NULL DEFAULT '',
        type TEXT NOT NULL DEFAULT 'free',
        body TEXT NOT NULL DEFAULT '',
        fingerprint TEXT NOT NULL,
        last_indexed_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS goal (
        session_id TEXT PRIMARY KEY,
        condition TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        reentries INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        last_verdict TEXT
      );
      CREATE INDEX IF NOT EXISTS memory_scope ON memory(scope, scope_id);
    `)
    try {
      this.db.exec(
        `CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(body, tokenize='porter unicode61');`,
      )
    } catch {
      this.fts = false // runtime without FTS5: search degrades to LIKE over memory.body
    }
  }

  hasFts() {
    return this.fts
  }

  upsertMemory(row: Omit<MemoryRow, "last_indexed_at">, body: string): void {
    const existing = this.db
      .prepare("SELECT rowid, fingerprint FROM memory WHERE path = ?")
      .get(row.path) as { rowid: number; fingerprint: string } | undefined
    const now = Date.now()
    if (existing && existing.fingerprint === row.fingerprint) {
      this.db
        .prepare("UPDATE memory SET last_indexed_at = ? WHERE path = ?")
        .run(now, row.path)
      return
    }
    if (existing) {
      if (this.fts) this.db.query("DELETE FROM memory_fts WHERE rowid = ?").run(existing.rowid)
      this.db.query("DELETE FROM memory WHERE rowid = ?").run(existing.rowid)
    }
    const ins = this.db
      .prepare(
        "INSERT INTO memory (path, scope, scope_id, type, body, fingerprint, last_indexed_at) VALUES (?,?,?,?,?,?,?)",
      )
      .run(row.path, row.scope, row.scope_id, row.type, body, row.fingerprint, now)
    if (this.fts)
      this.db
        .prepare("INSERT INTO memory_fts (rowid, body) VALUES (?,?)")
        .run(Number(ins.lastInsertRowid), body)
  }

  removeMemoryByPrefix(pathPrefix: string): number {
    const rows = this.db
      .prepare("SELECT rowid FROM memory WHERE path LIKE ?")
      .all(`${pathPrefix}%`) as { rowid: number }[]
    for (const r of rows) {
      if (this.fts) this.db.query("DELETE FROM memory_fts WHERE rowid = ?").run(r.rowid)
      this.db.query("DELETE FROM memory WHERE rowid = ?").run(r.rowid)
    }
    return rows.length
  }

  listPaths(): { path: string; fingerprint: string }[] {
    return this.db
      .prepare("SELECT path, fingerprint FROM memory")
      .all() as { path: string; fingerprint: string }[]
  }

  search(
    query: string,
    opts: { scope?: string; scopeId?: string; type?: string; limit?: number; floor?: number } = {},
  ): SearchHit[] {
    const limit = opts.limit ?? 8
    const args: unknown[] = []
    let where = "1=1"
    if (opts.scope) (where += " AND scope = ?"), args.push(opts.scope)
    if (opts.scopeId) (where += " AND scope_id = ?"), args.push(opts.scopeId)
    if (opts.type) (where += " AND type = ?"), args.push(opts.type)
    const rows = this.db
      .prepare(`SELECT rowid, path, scope, scope_id, type, body FROM memory WHERE ${where}`)
      .all(...args) as {
      rowid: number
      path: string
      scope: string
      scope_id: string
      type: string
      body: string
    }[]
    if (rows.length === 0 || !query.trim()) return []

    const hits: SearchHit[] = []
    if (this.fts) {
      const q = ftsQuery(query)
      if (!q) return []
      const ftsRows = this.db
        .prepare(
          `SELECT rowid, bm25(memory_fts) AS rank, snippet(memory_fts, 0, '»', '«', ' … ', 24) AS snip
           FROM memory_fts WHERE memory_fts MATCH ? ORDER BY rank LIMIT ?`,
        )
        .all(q, limit * 3) as { rowid: number; rank: number; snip: string }[]
      const byId = new Map(rows.map((r) => [r.rowid, r]))
      const best = ftsRows.length ? Math.abs(ftsRows[0].rank) : 0
      for (const f of ftsRows) {
        const m = byId.get(f.rowid)
        if (!m) continue
        const score = best > 0 ? Math.abs(f.rank) / best : 0
        if (score < (opts.floor ?? 0.15)) continue
        hits.push({ path: m.path, scope: m.scope, scope_id: m.scope_id, type: m.type, score: Number(score.toFixed(3)), snippet: f.snip })
      }
    } else {
      const tokens = query.toLowerCase().split(/[^a-z0-9_]+/).filter((t) => t.length >= 2)
      for (const m of rows) {
        const lower = m.body.toLowerCase()
        let matched = 0
        for (const t of tokens) if (lower.includes(t)) matched++
        if (matched === 0) continue
        const score = matched / Math.max(tokens.length, 1)
        const at = lower.indexOf(tokens[0] ?? "")
        const snippet = m.body.slice(Math.max(0, at - 40), Math.max(0, at - 40) + 160).replace(/\s+/g, " ")
        hits.push({ path: m.path, scope: m.scope, scope_id: m.scope_id, type: m.type, score: Number(score.toFixed(3)), snippet })
      }
      hits.sort((a, b) => b.score - a.score)
    }
    return hits.slice(0, limit)
  }

  kvGet(key: string): string | undefined {
    const row = this.db.query("SELECT value FROM kv WHERE key = ?").get(key) as
      | { value: string }
      | undefined
    return row?.value
  }

  kvSet(key: string, value: string): void {
    this.db
      .prepare("INSERT INTO kv (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value = excluded.value")
      .run(key, value)
  }

  setGoal(sessionID: string, condition: string): void {
    this.db
      .prepare(
        "INSERT INTO goal (session_id, condition, created_at) VALUES (?,?,?) ON CONFLICT(session_id) DO UPDATE SET condition = excluded.condition, created_at = excluded.created_at, reentries = 0, active = 1, last_verdict = NULL",
      )
      .run(sessionID, condition, Date.now())
  }

  getGoal(
    sessionID: string,
  ):
    | {
        session_id: string
        condition: string
        reentries: number
        active: number
        last_verdict: string | null
      }
    | undefined {
    return this.db.query("SELECT * FROM goal WHERE session_id = ?").get(sessionID) as never
  }

  bumpGoal(sessionID: string, verdict: string): void {
    this.db
      .prepare("UPDATE goal SET reentries = reentries + 1, last_verdict = ? WHERE session_id = ?")
      .run(verdict, sessionID)
  }

  clearGoal(sessionID: string, verdict: string | null): void {
    this.db
      .prepare("UPDATE goal SET active = 0, last_verdict = ? WHERE session_id = ?")
      .run(verdict, sessionID)
  }

  close() {
    this.db.close()
  }
}
