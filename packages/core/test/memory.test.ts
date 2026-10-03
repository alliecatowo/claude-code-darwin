import { afterEach, beforeEach, describe, expect, test } from "bun:test"
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import {
  Store,
  appendEntry,
  detectType,
  ensureMemorySkeleton,
  globalMemoryPath,
  projectId,
  projectMemoryPath,
  readHead,
  reconcile,
  sessionNotesPath,
} from "../src/index.ts"
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

describe("detectType", () => {
  test.each([
    ["global/MEMORY.md", "memory"],
    ["projects/abc/checkpoint-3.md", "checkpoint"],
    ["sessions/s1/notes.md", "notes"],
    ["projects/abc/tasks/progress.md", "progress"],
    ["projects/abc/archive.md", "archive"],
    ["projects/abc/handoff.json", "handoff"],
    ["projects/abc/random.md", "free"],
  ])("%s -> %s", (rel, type) => expect(detectType(rel)).toBe(type))
})

describe("projectId", () => {
  test("is a stable 12-hex-char hash of the directory", () => {
    expect(projectId("/work/a")).toMatch(/^[0-9a-f]{12}$/)
    expect(projectId("/work/a")).toBe(projectId("/work/a"))
    expect(projectId("/work/a")).not.toBe(projectId("/work/b"))
  })
})

describe("appendEntry and readHead", () => {
  test("creates the file with a heading and appends dated bullets", () => {
    const file = join(home.dir, "deep", "dir", "MEMORY.md")
    appendEntry(file, "Notes", "first")
    appendEntry(file, "Notes", "second")
    const text = readFileSync(file, "utf8")
    expect(text.startsWith("# Notes\n")).toBe(true)
    expect(text).toMatch(/- first \[\d{4}-\d{2}-\d{2}\]\n\n?- second \[\d{4}-\d{2}-\d{2}\]\n$/)
  })

  test("readHead returns null for a missing file, whole text when short, truncates when long", () => {
    const file = join(home.dir, "long.md")
    expect(readHead(file)).toBeNull()
    writeFileSync(file, "a\nb\nc")
    expect(readHead(file, 5)).toBe("a\nb\nc")
    writeFileSync(file, Array.from({ length: 10 }, (_, i) => `line${i}`).join("\n"))
    const head = readHead(file, 3)!
    expect(head.split("\n").slice(0, 3)).toEqual(["line0", "line1", "line2"])
    expect(head).toContain("7 more lines")
  })
})

describe("ensureMemorySkeleton", () => {
  test("creates project and global memory once and never overwrites", () => {
    ensureMemorySkeleton(home.p, "/work/a")
    const proj = projectMemoryPath(home.p, "/work/a")
    expect(readFileSync(proj, "utf8")).toContain("# Project memory")
    expect(readFileSync(globalMemoryPath(home.p), "utf8")).toBe("# Global memory\n")
    writeFileSync(proj, "custom")
    ensureMemorySkeleton(home.p, "/work/a")
    expect(readFileSync(proj, "utf8")).toBe("custom")
  })

  test("sessionNotesPath creates the session directory", () => {
    const notes = sessionNotesPath(home.p, "sess-1")
    writeFileSync(notes, "ok")
    expect(readFileSync(notes, "utf8")).toBe("ok")
  })
})

describe("reconcile", () => {
  test("returns zeros when there is no memory directory", () => {
    expect(reconcile(store, home.p)).toEqual({ indexed: 0, pruned: 0 })
  })

  test("indexes markdown with the right scope, ignores non-markdown, prunes vanished files", () => {
    mkdirSync(home.p.globalMemory, { recursive: true })
    writeFileSync(join(home.p.globalMemory, "MEMORY.md"), "global rule about pineapples")
    const proj = home.p.projectMemory("/work/a")
    mkdirSync(proj, { recursive: true })
    writeFileSync(join(proj, "MEMORY.md"), "project rule about pineapples")
    writeFileSync(join(proj, "ignored.txt"), "pineapples in a txt file")
    const sess = home.p.sessionMemory("s9")
    mkdirSync(sess, { recursive: true })
    writeFileSync(join(sess, "notes.md"), "session note about pineapples")

    expect(reconcile(store, home.p)).toEqual({ indexed: 3, pruned: 0 })
    const hits = store.search("pineapples")
    expect(hits.map((h) => h.scope).sort()).toEqual(["global", "projects", "sessions"])
    expect(store.search("pineapples", { scope: "sessions" })[0].scope_id).toBe("s9")
    expect(store.search("pineapples", { type: "notes" }).length).toBe(1)

    rmSync(join(sess, "notes.md"))
    expect(reconcile(store, home.p)).toEqual({ indexed: 2, pruned: 1 })
    expect(store.search("pineapples").length).toBe(2)
  })
})
