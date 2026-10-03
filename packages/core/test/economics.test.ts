import { describe, expect, test } from "bun:test"
import { effectivePrice, formatReport, loadPricesFromModelsDev, summarizeTurns, type TurnRecord } from "../src/index.ts"

const turn = (over: Partial<TurnRecord> = {}): TurnRecord => ({
  sessionID: "s",
  modelID: "m1",
  providerID: "p",
  cost: 0.01,
  tokens: { input: 100, output: 50, cacheRead: 0, cacheWrite: 0 },
  ts: 0,
  ...over,
})

const catalog = {
  anthropic: {
    models: {
      big: { cost: { input: 3, output: 15, cache_read: 0.3, cache_write: 3.75 }, limit: { context: 200000 } },
      local: { cost: {}, limit: {} },
    },
  },
  empty: {},
}

describe("loadPricesFromModelsDev", () => {
  test("maps providers/models into keyed prices, snake_case cache fields included", () => {
    const m = loadPricesFromModelsDev(catalog)
    expect([...m.keys()].sort()).toEqual(["anthropic/big", "anthropic/local"])
    expect(m.get("anthropic/big")).toMatchObject({
      price: { input: 3, output: 15, cacheRead: 0.3, cacheWrite: 3.75 },
      free: false,
      contextLimit: 200000,
    })
    expect(m.get("anthropic/local")).toMatchObject({ free: true, contextLimit: 0 })
  })
})

describe("effectivePrice", () => {
  const prices = loadPricesFromModelsDev(catalog)
  test("subscription and free auth zero the price", () => {
    expect(effectivePrice("anthropic/big", prices, "subscription")).toMatchObject({ input: 0, output: 0, effectiveZero: true })
    expect(effectivePrice("anthropic/big", prices, "free").effectiveZero).toBe(true)
  })
  test("api auth uses the catalog; unknown models price at zero but are not flagged free", () => {
    expect(effectivePrice("anthropic/big", prices, "api")).toMatchObject({ input: 3, output: 15, effectiveZero: false })
    expect(effectivePrice("nope/none", prices)).toMatchObject({ input: 0, effectiveZero: false })
    expect(effectivePrice("anthropic/local", prices).effectiveZero).toBe(true)
  })
})

describe("summarizeTurns", () => {
  test("empty input yields a zeroed report", () => {
    expect(summarizeTurns([])).toMatchObject({ totalCost: 0, totalTurns: 0, totalTokens: 0, cacheHitRate: 0, byModel: [], advisories: [] })
  })

  test("totals cost and tokens and ranks models by spend", () => {
    const r = summarizeTurns([turn({ cost: 0.5 }), turn({ cost: 0.25 }), turn({ modelID: "m2", cost: 1 })])
    expect(r.totalCost).toBe(1.75)
    expect(r.totalTurns).toBe(3)
    expect(r.totalTokens).toBe(450)
    expect(r.byModel.map((m) => [m.model, m.turns])).toEqual([
      ["p/m2", 1],
      ["p/m1", 2],
    ])
    expect(r.byModel[1].avgCostPerTurn).toBeCloseTo(0.375)
  })

  test("cache hit rate counts reads against reads, writes and fresh input", () => {
    const r = summarizeTurns([turn({ tokens: { input: 100, output: 0, cacheRead: 800, cacheWrite: 100 } })])
    expect(r.cacheHitRate).toBe(0.8)
  })

  test("warns about a low cache hit rate only after more than five turns", () => {
    const cold = (n: number) => summarizeTurns(Array.from({ length: n }, () => turn({ tokens: { input: 10, output: 1, cacheRead: 1, cacheWrite: 100 } })))
    expect(cold(5).advisories).toEqual([])
    expect(cold(6).advisories.join()).toContain("cache hit rate low")
  })

  test("suggests a much cheaper model when one exists", () => {
    const r = summarizeTurns([turn({ modelID: "pricey", cost: 1 }), turn({ modelID: "cheap", cost: 0.1 })])
    expect(r.advisories.join()).toContain("p/cheap")
  })
})

describe("formatReport", () => {
  test("renders spend, cache, top models and advisories", () => {
    const out = formatReport(summarizeTurns([turn({ modelID: "pricey", cost: 1 }), turn({ modelID: "cheap", cost: 0.1 })]))
    expect(out).toContain("spend: $1.1000 over 2 turns")
    expect(out).toContain("p/pricey: 1 turns, $1.0000")
    expect(out).toContain("advisories:")
  })
  test("includes optional lines only when present", () => {
    const base = summarizeTurns([turn()])
    expect(formatReport(base)).not.toContain("per successful task")
    expect(formatReport({ ...base, pricePerSuccess: 0.5, projectedSessionCost: 2 })).toContain("price per successful task: $0.5000")
  })
})
