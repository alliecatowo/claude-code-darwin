/**
 * Defensive SDK-call helpers. The legacy @opencode-ai/sdk client generated
 * shapes have drifted across releases; try the known shapes, log, and let
 * callers fail open (goal judge, cadence) rather than crash the host.
 */
import { log } from "@darwin/core"

type AnyClient = Record<string, any>

async function tryShapes<T>(label: string, fn: () => Promise<T>): Promise<T | null> {
  try {
    return await fn()
  } catch (err) {
    log.warn(`sdk:${label} failed`, err instanceof Error ? err.message : err)
    return null
  }
}

export async function createSession(
  client: AnyClient,
  body: { parentID?: string; title?: string; agent?: string; model?: string },
): Promise<string | null> {
  const created: any = await tryShapes("session.create", () => client.session.create({ body }))
  const id = created?.info?.id ?? (created as any)?.id
  return typeof id === "string" ? id : null
}

export async function promptAsync(
  client: AnyClient,
  sessionID: string,
  text: string,
  agent?: string,
): Promise<boolean> {
  const body = agent ? { agent, message: text } : { message: text }
  for (const shape of [
    () => client.session.promptAsync({ path: { id: sessionID }, body }),
    () => client.session.promptAsync({ id: sessionID, ...body }),
  ]) {
    const ok = await tryShapes("session.promptAsync", shape)
    if (ok !== null) return true
  }
  return false
}

export type Hist = { role: string; text: string }[]

export async function listMessages(client: AnyClient, sessionID: string): Promise<Hist> {
  for (const shape of [
    () => client.session.messages({ path: { id: sessionID } }),
    () => client.session.messages({ id: sessionID }),
    () => client.session.listMessages({ path: { id: sessionID } }),
  ]) {
    const res = await tryShapes("session.messages", shape)
    const arr = Array.isArray(res) ? res : ((res as any)?.messages ?? (res as any)?.data)
    if (!Array.isArray(arr)) continue
    return arr.flatMap((m: any) => {
      const role = m?.info?.role ?? m?.role
      const parts = m?.parts ?? []
      return parts
        .filter((p: any) => p?.type === "text" && typeof p.text === "string")
        .map((p: any) => ({ role: String(role ?? "user"), text: p.text as string }))
    })
  }
  return []
}

export async function abortSession(client: AnyClient, sessionID: string): Promise<boolean> {
  for (const shape of [
    () => client.session.abort({ path: { id: sessionID } }),
    () => client.session.abort({ id: sessionID }),
  ]) {
    const ok = await tryShapes("session.abort", shape)
    if (ok !== null) return true
  }
  return false
}

/** Compact transcript for the judge: last N text turns, tool results elided. */
export function compactTranscript(hist: Hist, maxChars = 12_000): string {
  const tail = hist.slice(-40)
  let out = ""
  for (const h of tail) out += `${h.role.toUpperCase()}: ${h.text.slice(0, 800)}\n`
  if (out.length > maxChars) out = out.slice(-maxChars)
  return out
}
