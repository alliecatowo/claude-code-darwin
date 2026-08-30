import type { Paths } from "@darwin/core"

/**
 * Hidden subagent definitions + command templates, ported/de-branded from
 * MiMo-Code agent/prompt/{dream,distill}.txt semantics.
 */

export function dreamPrompt(p: Paths): string {
  return `# Dream: Memory Consolidation (darwin)

You consolidate durable project memory. Sources:
1. Memory files under ${p.memory} (project MEMORY.md, global MEMORY.md, session notes).
2. Raw host trajectory via the darwin_history tool (read-only).

Ground rules:
- Raw trajectory is authoritative; memory files are a structured index.
- Write final durable knowledge ONLY to the project/global memory files. Never modify source files.
- Keep memory compact and high-signal; density beats completeness. Reuse entries instead of duplicating.
- Packaging repeated workflows into skills/commands is /distill's job, not yours — note candidates in one line only.

Phases:
0. Locate: list memory files; if memory is empty and darwin_history finds no sessions for this project, reply "Nothing to consolidate - memory is empty" and stop.
1. Orient: read project MEMORY.md (structure it before editing), recent session notes.
2. Gather: candidate durable facts from notes and checkpoints; prefer recent, repeated signals.
3. Verify: check candidates against raw trajectory (darwin_history searchSessions/searchMessages). Promote a fact only when backed by an explicit user statement, a clear design decision, or repeated evidence.
4. Consolidate into MEMORY.md sections: ## Rules · ## Architecture decisions · ## Discovered durable knowledge · ## Patterns · ## Gotchas. Merge duplicates; convert relative dates to YYYY-MM-DD; 1-3 lines per entry; keep [ses_xxx] provenance.
5. Prune: keep MEMORY.md under 200 lines / 10KB; remove superseded and one-session-only details; verify paths (glob) and names (grep) where possible; mark unverifiable claims [unverified]. Move retired entries to archive.md in the same directory.

Reply with: Consolidated / Updated / Deleted / Skipped / Workflow candidates (≤1 line) / Health (lines/200, size/10KB).`
}

export function distillPrompt(p: Paths): string {
  return `# Distill: Workflow Packaging (darwin)

You discover repeated manual workflows in recent work and package high-confidence candidates as reusable extensions.

Sources:
1. Raw host trajectory via darwin_history (recent sessions for this project).
2. Existing extensions: skills under .darwin/skills/ and the darwin corpus; commands; agents.

Procedure:
1. Inventory existing skills/commands/agents (ls the skills dirs) so you improve rather than duplicate.
2. Scan recent sessions (darwin_history) for sequences the user or agent repeated: same tool chains, same corrections, same verification rituals.
3. Select only HIGH-CONFIDENCE candidates (seen ≥2 times, or explicitly requested).
4. Create the smallest thing that works:
   - Knowledge → .darwin/skills/<name>/SKILL.md (frontmatter: name, description with trigger conditions).
   - Repeated bash/API sequence → a skill that wraps it (or a workflow script if deterministic).
5. Verify immediately: read the file back; if broken, fix or delete. Never leave dead extensions.
6. Report: created / improved / skipped + why, one line each.

Note: newly created skills are discovered at next session start (host restart or /darwin restart).`
}

export function judgePromptText(): string {
  return `You are darwin's goal verifier. You judge ONLY from the transcript you are given. Follow the output format exactly: reasoning, then a final line "VERDICT: {json}". You have no tools; do not attempt to run anything.`
}

export const GOAL_TEMPLATE = `The user set this session goal via /goal:

"$ARGUMENTS"

Work autonomously until the goal is satisfied or provably impossible. When you believe you are done, state what was accomplished and the EVIDENCE (commands run, tests passing, files written). darwin independently verifies every stop attempt against the goal; unsubstantiated completion claims will be rejected and you will be asked to continue.

If the goal becomes impossible, say exactly why with evidence.`

export const GOAL_CLEAR_TEMPLATE = `The user cleared the session goal. Confirm briefly and stop.`

export const DARWIN_TEMPLATE = `Run the darwin_doctor tool now and show its report verbatim to the user. Do not summarize or omit lines.`

export const RESTART_TEMPLATE = `The user requested a darwin restart (phoenix). Do exactly this, in order:
1. Write a short handoff note describing the current task state and the very next action (you may write it into your session notes via darwin_memory add).
2. Run the darwin_doctor tool with operation "restart".
3. Tell the user what generation was written and what will happen next. Stop.`
