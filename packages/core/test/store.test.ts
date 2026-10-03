import { afterEach, beforeEach, describe, expect, test } from "bun:test"
import { Store, ftsQuery } from "../src/index.ts"
import { tempHome } from "./helpers.ts"

let home: ReturnType<typeof tempHome>
let store: Store

beforeEach(() => {
  home = tempHome()
  store = new Store(home.p.db)
})
afterEach(() => {
  store.close()
  home.cleanup()
})

const row = (path: string, fingerprint = "1-1", scope: "global" | "projects" | "sessions" = "projects", scope_id = "p1") => ({
  path,
  scope,
  scope_id,
  type: "free",
  fingerprint,
})

describe("ftsQuery", () => {
  test("drops stop words and short tokens, OR-joins quoted phrases", () => {
    expect(ftsQuery("How do the scheduler locks work?")).toBe('"scheduler" OR "locks" OR "work"')
  })
  test("returns null when nothing is searchable", () => {
    expect(ftsQuery("the a of")).toBeNull()
    expect(ftsQuery("   ")).toBeNull()
  })
})

describe("Store memory", () => {
  test("upsert then search finds the note with a snippet", () => {
    store.upsertMemory(row("/m/a.md"), "The scheduler uses a cross-process lock file")
    store.upsertMemory(row("/m/b.md"), "Completely unrelated pancake recipe")
    const hits = store.search("scheduler lock")
    expect(hits.map((h) => h.path)).toEqual(["/m/a.md"])
    expect(hits[0].score).toBeGreaterThan(0)
    expect(hits[0].snippet.length).toBeGreaterThan(0)
  })

  test("unchanged fingerprint does not reindex; changed fingerprint replaces the body", () => {
    store.upsertMemory(row("/m/a.md", "1-1"), "alpha content")
    store.upsertMemory(row("/m/a.md", "1-1"), "ignored because the fingerprint matches")
    expect(store.search("alpha").length).toBe(1)
    expect(store.search("ignored").length).toBe(0)
    store.upsertMemory(row("/m/a.md", "2-2"), "bravo content")
    expect(store.search("alpha").length).toBe(0)
    expect(store.search("bravo").length).toBe(1)
    expect(store.listPaths()).toEqual([{ path: "/m/a.md", fingerprint: "2-2" }])
  })

  test("scope, scopeId and type filters narrow results", () => {
    store.upsertMemory(row("/m/g.md", "1", "global", ""), "shared widget knowledge")
    store.upsertMemory(row("/m/p1.md", "1", "projects", "p1"), "shared widget knowledge")
    store.upsertMemory(row("/m/p2.md", "1", "projects", "p2"), "shared widget knowledge")
    expect(store.search("widget").length).toBe(3)
    expect(store.search("widget", { scope: "global" }).map((h) => h.path)).toEqual(["/m/g.md"])
    expect(store.search("widget", { scope: "projects", scopeId: "p2" }).map((h) => h.path)).toEqual(["/m/p2.md"])
    expect(store.search("widget", { type: "memory" })).toEqual([])
  })

  test("empty query and stop-word-only query return nothing", () => {
    store.upsertMemory(row("/m/a.md"), "anything at all")
    expect(store.search("")).toEqual([])
    expect(store.search("the of a")).toEqual([])
  })

  test("limit caps the number of hits", () => {
    for (let i = 0; i < 5; i++) store.upsertMemory(row(`/m/${i}.md`, String(i)), `gadget note number ${i}`)
    expect(store.search("gadget", { limit: 2 }).length).toBeLessThanOrEqual(2)
  })

  test("removeMemoryByPrefix deletes matching rows and their index entries", () => {
    store.upsertMemory(row("/m/proj/a.md"), "keepme findable text")
    store.upsertMemory(row("/m/proj/b.md"), "dropme findable text")
    store.upsertMemory(row("/m/other/c.md"), "otherwise findable text")
    expect(store.removeMemoryByPrefix("/m/proj/")).toBe(2)
    expect(store.listPaths().map((r) => r.path)).toEqual(["/m/other/c.md"])
    expect(store.search("findable").map((h) => h.path)).toEqual(["/m/other/c.md"])
  })
})

describe("Store kv", () => {
  test("get returns undefined for a missing key, set overwrites", () => {
    expect(store.kvGet("k")).toBeUndefined()
    store.kvSet("k", "one")
    store.kvSet("k", "two")
    expect(store.kvGet("k")).toBe("two")
  })
})

describe("Store goal", () => {
  test("lifecycle: set, bump, clear, and reset on re-set", () => {
    expect(store.getGoal("s1")).toBeUndefined()
    store.setGoal("s1", "tests pass")
    expect(store.getGoal("s1")).toMatchObject({ condition: "tests pass", reentries: 0, active: 1, last_verdict: null })
    store.bumpGoal("s1", "not yet")
    store.bumpGoal("s1", "still not")
    expect(store.getGoal("s1")).toMatchObject({ reentries: 2, last_verdict: "still not" })
    store.clearGoal("s1", "done")
    expect(store.getGoal("s1")).toMatchObject({ active: 0, last_verdict: "done" })
    store.setGoal("s1", "new goal")
    expect(store.getGoal("s1")).toMatchObject({ condition: "new goal", reentries: 0, active: 1, last_verdict: null })
  })
})

test("a second Store on the same file sees committed data", () => {
  store.kvSet("shared", "yes")
  const other = new Store(home.p.db)
  expect(other.kvGet("shared")).toBe("yes")
  other.close()
})
