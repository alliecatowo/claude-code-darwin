import { describe, expect, test } from "bun:test"
import { MAX_REENTRIES, REMINDER, judgePrompt, parseVerdict } from "../src/index.ts"

describe("parseVerdict", () => {
  test("parses the final VERDICT line", () => {
    const reply = 'Tests ran and passed.\nVERDICT: {"ok": true, "impossible": false, "reason": "bun test exited 0"}'
    expect(parseVerdict(reply)).toEqual({ ok: true, impossible: false, reason: "bun test exited 0" })
  })
  test("uses the last VERDICT line and tolerates leading text on it", () => {
    const reply = 'VERDICT: {"ok": true, "reason": "early"}\nthinking again\nFinal -> VERDICT: {"ok": false, "reason": "not yet"}'
    expect(parseVerdict(reply)).toEqual({ ok: false, reason: "not yet" })
  })
  test("returns null (fail open) for missing, malformed or wrongly typed verdicts", () => {
    expect(parseVerdict("no verdict here")).toBeNull()
    expect(parseVerdict("VERDICT: {nope")).toBeNull()
    expect(parseVerdict('VERDICT: {"ok": "yes", "reason": "x"}')).toBeNull()
  })
})

describe("prompts", () => {
  test("judgePrompt embeds both the condition and the transcript", () => {
    const p = judgePrompt("all tests pass", "ran bun test")
    expect(p).toContain("all tests pass")
    expect(p).toContain("ran bun test")
  })
  test("REMINDER pluralizes the remaining auto-continues", () => {
    expect(REMINDER("g", "r", 1)).toContain("1 auto-continue remain")
    expect(REMINDER("g", "r", 3)).toContain("3 auto-continues remain")
    expect(MAX_REENTRIES).toBeGreaterThan(0)
  })
})
