/**
 * Economics — cost-aware self-optimization as a facet of reflection.
 * Non-prescriptive: the agent weighs economics alongside correctness.
 * Sources: models.dev catalog + host message cost/tokens + goal verdicts.
 */

export type PriceTier = { input: number; output: number; cacheRead: number; cacheWrite?: number }
export type ModelPrice = {
  provider: string
  model: string
  price: PriceTier
  free: boolean
  contextLimit: number
}

export type TurnRecord = {
  sessionID: string
  modelID: string
  providerID: string
  cost: number
  tokens: { input: number; output: number; cacheRead: number; cacheWrite: number }
  ts: number
}

export type EconomicsReport = {
  window: string
  totalCost: number
  totalTurns: number
  totalTokens: number
  cacheHitRate: number
  byModel: { model: string; turns: number; cost: number; avgCostPerTurn: number; successRate?: number }[]
  pricePerSuccess?: number
  projectedSessionCost?: number
  cheapestCapable?: string
  advisories: string[]
}

// --- Pricing catalog (offline-first, models.dev shape) ---------------------------

export function loadPricesFromModelsDev(raw: Record<string, any>): Map<string, ModelPrice> {
  const m = new Map<string, ModelPrice>()
  for (const [provider, pdata] of Object.entries(raw)) {
    const models = (pdata as any).models ?? {}
    for (const [modelId, mdata] of Object.entries(models as Record<string, any>)) {
      const cost = (mdata as any).cost ?? {}
      const key = `${provider}/${modelId}`
      m.set(key, {
        provider,
        model: modelId,
        price: {
          input: cost.input ?? 0,
          output: cost.output ?? 0,
          cacheRead: cost.cache_read ?? cost.cacheRead ?? 0,
          cacheWrite: cost.cache_write ?? cost.cacheWrite,
        },
        free: (cost.input ?? 0) === 0 && (cost.output ?? 0) === 0,
        contextLimit: (mdata as any).limit?.context ?? 0,
      })
    }
  }
  return m
}

export function effectivePrice(
  modelKey: string,
  prices: Map<string, ModelPrice>,
  authType: "api" | "subscription" | "free" | "unknown" = "unknown",
): PriceTier & { effectiveZero: boolean } {
  if (authType === "subscription" || authType === "free") return { input: 0, output: 0, cacheRead: 0, effectiveZero: true }
  const p = prices.get(modelKey)
  if (!p) return { input: 0, output: 0, cacheRead: 0, effectiveZero: false }
  return { ...p.price, effectiveZero: p.free }
}

// --- Turn rollups (pure, no DB) ------------------------------------------------

export function summarizeTurns(turns: TurnRecord[]): EconomicsReport {
  const totalCost = turns.reduce((s, t) => s + t.cost, 0)
  const totalTokens = turns.reduce((s, t) => s + t.tokens.input + t.tokens.output, 0)
  const cacheRead = turns.reduce((s, t) => s + t.tokens.cacheRead, 0)
  const cacheWrite = turns.reduce((s, t) => s + t.tokens.cacheWrite, 0)
  const cacheHitRate = cacheRead + cacheWrite > 0 ? cacheRead / (cacheRead + cacheWrite + turns.reduce((s, t) => s + t.tokens.input, 0)) : 0

  const byModel = new Map<string, { turns: number; cost: number }>()
  for (const t of turns) {
    const k = `${t.providerID}/${t.modelID}`
    const cur = byModel.get(k) ?? { turns: 0, cost: 0 }
    cur.turns++
    cur.cost += t.cost
    byModel.set(k, cur)
  }

  const byModelArr = [...byModel.entries()]
    .map(([model, v]) => ({ model, ...v, avgCostPerTurn: v.turns ? v.cost / v.turns : 0 }))
    .sort((a, b) => b.cost - a.cost)

  const advisories: string[] = []
  if (cacheHitRate < 0.3 && turns.length > 5) advisories.push("cache hit rate low — keep prefix byte-stable (tools→system→history) and avoid reflowing early context")
  if (byModelArr.length > 1) {
    const cheapest = [...byModelArr].sort((a, b) => a.avgCostPerTurn - b.avgCostPerTurn)[0]
    if (cheapest.avgCostPerTurn < byModelArr[0].avgCostPerTurn * 0.5)
      advisories.push(`cheapest model ${cheapest.model} is >2× cheaper per turn — consider it for known workflows`)
  }

  return {
    window: `${turns.length} turns`,
    totalCost: Number(totalCost.toFixed(4)),
    totalTurns: turns.length,
    totalTokens,
    cacheHitRate: Number(cacheHitRate.toFixed(3)),
    byModel: byModelArr,
    advisories,
  }
}

export function formatReport(r: EconomicsReport): string {
  const lines = [
    `spend: $${r.totalCost.toFixed(4)} over ${r.totalTurns} turns (${r.totalTokens} tokens)`,
    `cache hit: ${(r.cacheHitRate * 100).toFixed(1)}%`,
  ]
  for (const m of r.byModel.slice(0, 5)) lines.push(`  ${m.model}: ${m.turns} turns, $${m.cost.toFixed(4)} (avg $${m.avgCostPerTurn.toFixed(4)}/turn)`)
  if (r.pricePerSuccess !== undefined) lines.push(`price per successful task: $${r.pricePerSuccess.toFixed(4)}`)
  if (r.projectedSessionCost !== undefined) lines.push(`projected session cost: $${r.projectedSessionCost.toFixed(4)}`)
  if (r.advisories.length) lines.push("advisories:", ...r.advisories.map((a) => `  - ${a}`))
  return lines.join("\n")
}
