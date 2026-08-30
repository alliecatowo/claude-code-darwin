/**
 * Goal judge — semantics ported from MiMo-Code session/goal.ts:
 * evidence-based verdicts, "insufficient evidence" default, strict `impossible`,
 * judge errors fail OPEN (stop allowed).
 */

export const JUDGE_SYSTEM = `You are a strict verifier judging whether a coding agent has satisfied a stopping condition.

Rules:
- Base your verdict ONLY on evidence visible in the transcript. Quote the exact evidence when you find it.
- If the transcript does not contain explicit evidence that the condition is satisfied, the condition is NOT satisfied.
- Claimed completion without executed verification (tests run, commands succeeding, files written) is NOT evidence.
- Mark impossible=true ONLY when the transcript proves the condition cannot be met (contradiction, missing prerequisite, explicit user cancellation). Uncertainty is not impossibility.
- Reply with 2-6 sentences of reasoning, then a FINAL line in exactly this format:
VERDICT: {"ok": false, "impossible": false, "reason": "<one sentence>"}
The final line must be valid JSON and must be the last line of your reply.`

export type Verdict = { ok: boolean; impossible?: boolean; reason: string }

export function judgePrompt(condition: string, transcript: string): string {
  return `Stopping condition:\n"""\n${condition}\n"""\n\nTranscript (recent turns, tool calls and results preserved where available):\n"""\n${transcript}\n"""\n\nHas the stopping condition been satisfied?`
}

/** Lenient parse: last VERDICT: line containing JSON; any parse failure => null (fail-open upstream). */
export function parseVerdict(reply: string): Verdict | null {
  const lines = reply.trim().split("\n").reverse()
  for (const line of lines) {
    const at = line.indexOf("VERDICT:")
    if (at === -1) continue
    const raw = line.slice(at + "VERDICT:".length).trim()
    const candidates = [raw, raw.replace(/^[^{]*/, ""), raw.replace(/[^{].*?(?=\{)/, "")]
    for (const c of candidates) {
      try {
        const v = JSON.parse(c) as Verdict
        if (typeof v.ok === "boolean" && typeof v.reason === "string") return v
      } catch {
        /* try next */
      }
    }
  }
  return null
}

export const REMINDER = (condition: string, reason: string, left: number) =>
  `<system-reminder>Your goal is not yet satisfied. Goal: ${condition}\nThe verifier reported: ${reason}\nKeep working toward the goal. Do not stop until it is satisfied or provably impossible (${left} auto-continue${left === 1 ? "" : "s"} remain before the goal is released).</system-reminder>`

export const MAX_REENTRIES = 12
