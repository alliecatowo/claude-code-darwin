const STOP = new Set([
  "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "was", "were",
  "be", "been", "it", "its", "this", "that", "with", "as", "at", "by", "from", "how", "what",
  "why", "when", "do", "does", "did", "i", "we", "you", "my", "our", "me",
])

/** OR-joined quoted phrase query for FTS5 MATCH (MiMo-Code fts-query.ts parity). */
export function ftsQuery(query: string): string | null {
  const tokens = query
    .toLowerCase()
    .split(/[^a-z0-9_]+/)
    .filter((t) => t.length >= 2 && !STOP.has(t))
  if (tokens.length === 0) return null
  return tokens.map((t) => `"${t.replace(/"/g, "")}"`).join(" OR ")
}
