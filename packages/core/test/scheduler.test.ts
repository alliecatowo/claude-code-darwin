import { afterEach, beforeEach, describe, expect, test } from "bun:test"
import { existsSync, readFileSync, utimesSync, writeFileSync } from "node:fs"
import { Lock, Scheduler, type Task } from "../src/index.ts"
import { tempHome } from "./helpers.ts"

let home: ReturnType<typeof tempHome>
beforeEach(() => {
  home = tempHome()
})
afterEach(() => home.cleanup())

describe("Lock", () => {
  test("is exclusive until released", () => {
    const a = Lock.acquire(home.p.lock)
    expect(a).not.toBeNull()
    expect(Lock.acquire(home.p.lock)).toBeNull()
    a!.release()
    expect(existsSync(home.p.lock)).toBe(false)
    expect(Lock.acquire(home.p.lock)).not.toBeNull()
  })

  test("a stale lock is taken over", () => {
    writeFileSync(home.p.lock, "99999 0")
    const old = new Date(Date.now() - 120_000)
    utimesSync(home.p.lock, old, old)
    expect(Lock.acquire(home.p.lock, 60_000)).not.toBeNull()
  })

  test("a fresh lock is respected", () => {
    writeFileSync(home.p.lock, "99999 0")
    expect(Lock.acquire(home.p.lock, 60_000)).toBeNull()
  })

  test("release is idempotent", () => {
    const l = Lock.acquire(home.p.lock)!
    l.release()
    expect(() => l.release()).not.toThrow()
  })
})

describe("Scheduler task storage", () => {
  const noop = async () => {}

  test("starts empty and survives a corrupt tasks file", () => {
    const s = new Scheduler(home.p, noop)
    expect(s.list()).toEqual([])
    writeFileSync(home.p.tasks, "{not json")
    expect(s.list()).toEqual([])
  })

  test("add replaces by id, remove reports whether anything was deleted", () => {
    const s = new Scheduler(home.p, noop)
    s.add({ id: "a", name: "one", everyMs: 1000 })
    s.add({ id: "b", name: "two", atMs: 5 })
    s.add({ id: "a", name: "one-renamed", everyMs: 2000 })
    expect(s.list().map((t) => [t.id, t.name])).toEqual([
      ["a", "one-renamed"],
      ["b", "two"],
    ])
    expect(s.remove("a")).toBe(true)
    expect(s.remove("a")).toBe(false)
    expect(JSON.parse(readFileSync(home.p.tasks, "utf8")).version).toBe(1)
  })
})

describe("Scheduler firing", () => {
  // tick() is private; drive it directly rather than waiting on real timers.
  const tick = (s: Scheduler) => (s as unknown as { tick(): Promise<void> }).tick()

  test("fires a due one-shot exactly once", async () => {
    const fired: string[] = []
    const s = new Scheduler(home.p, async (t) => void fired.push(t.id))
    s.add({ id: "once", name: "once", atMs: Date.now() - 10 })
    s.add({ id: "future", name: "future", atMs: Date.now() + 60_000 })
    await tick(s)
    await tick(s)
    expect(fired).toEqual(["once"])
    expect(s.list().find((t) => t.id === "once")?.fired).toBe(true)
  })

  test("fires a recurring task when its period has elapsed and records lastRunMs", async () => {
    const fired: string[] = []
    const s = new Scheduler(home.p, async (t) => void fired.push(t.id))
    s.add({ id: "tick", name: "tick", everyMs: 60_000 })
    await tick(s)
    expect(fired).toEqual(["tick"])
    const last = s.list()[0].lastRunMs!
    expect(last).toBeGreaterThan(0)
    await tick(s)
    expect(fired).toEqual(["tick"]) // not due again for another minute
  })

  test("a throwing task does not stop the others or leave the lock behind", async () => {
    const fired: string[] = []
    const s = new Scheduler(home.p, async (t: Task) => {
      if (t.id === "bad") throw new Error("boom")
      fired.push(t.id)
    })
    const prev = process.env.DARWIN_LOG_LEVEL
    process.env.DARWIN_LOG_LEVEL = "error"
    s.add({ id: "bad", name: "bad", atMs: 1 })
    s.add({ id: "good", name: "good", atMs: 1 })
    const origErr = console.error
    console.error = () => {}
    try {
      await tick(s)
    } finally {
      console.error = origErr
      if (prev === undefined) delete process.env.DARWIN_LOG_LEVEL
      else process.env.DARWIN_LOG_LEVEL = prev
    }
    expect(fired).toEqual(["good"])
    expect(existsSync(home.p.lock)).toBe(false)
  })

  test("does nothing while another process holds the lock", async () => {
    const fired: string[] = []
    const s = new Scheduler(home.p, async (t) => void fired.push(t.id))
    s.add({ id: "x", name: "x", atMs: 1 })
    const held = Lock.acquire(home.p.lock)!
    await tick(s)
    expect(fired).toEqual([])
    held.release()
    await tick(s)
    expect(fired).toEqual(["x"])
  })
})
