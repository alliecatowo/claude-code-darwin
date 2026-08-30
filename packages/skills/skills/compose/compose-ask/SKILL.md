---
name: compose-ask
description: "Use whenever you need a decision, clarification, or approval from the user — covers how to ask the user (one question at a time), and how to resolve the decision yourself when no user is available (unattended run, or a [Never-Ask] response)"
---

<!-- Derived from XiaomiMiMo/MiMo-Code (MIT) -->

# Asking the User

## The Rule

Every time you need the user to decide, clarify, or approve something, ask the user (one question at a time). **Never** stop the loop with an unfocused natural-language question ("Does this look right?", "Should I proceed?", "Which would you prefer?" dropped in passing). A vague, buried question ends your turn without settling anything; one clearly posed question does not.

This means: **the loop only ends when the task is actually complete** — never because you paused to ask vaguely in prose.

## How to Ask

- **Structured options** — when the decision has known choices, present them as a list of options (each with a short label and a description).
- **Open-ended** — when you can't enumerate good options, ask a **free-text question**. The user types whatever they want. So anything you'd normally ask in prose can be asked as one clear open question instead.
- **One question per concern** — don't bundle unrelated decisions; ask them as separate questions (in separate turns).
- **State the question once, clearly** — don't repeat it elsewhere in the response.

```
Which auth strategy should I use? (Auth)
- Session cookies — server-side sessions, simplest
- JWT — stateless, good for multiple services
```

## When No User Is Available

There are two situations where you won't get a human answer. **The decision behavior is identical in both** — you pick the best option for unattended/headless execution yourself and keep going. They differ only in *whether the question is asked this turn*:

1. **No user present** (e.g. unattended `run`/eval) — there is no one to answer. You never ask; decide and proceed directly.
2. **`[Never-Ask]` response** (never-ask is on) — you *do* ask, but instead of a user answer the response comes back as a `[Never-Ask]` directive. Re-pick from the options you proposed, **explicitly state your choice and reasoning in your response text**, and continue.

### How to decide autonomously

The best option for a human is often **not** the best option for headless execution. Re-evaluate your own options through this lens:

- Prefer **text-only** over visual/interactive paths (e.g. don't launch a GUI companion, don't open a browser — produce the same content as text).
- Prefer **non-interactive** over anything needing the user present.
- Prefer the **minimal-scope** path — don't expand the work to cover speculative edge cases just because no one is gating it.
- When approval is the only thing being requested, treat it as **granted** and proceed to implementation.
- **Exception — destructive, irreversible actions** (deleting a branch/commits, dropping data, force-pushing) never auto-approve. When you can't get explicit confirmation, choose the non-destructive path (keep things as-is) and continue with the rest of the task.

### Only this turn — keep asking later

Autonomous resolution applies **only to the current question**. Do **not** infer "I should stop asking from now on." never-ask can be turned off at any moment, and the user may return — at the next decision point, ask normally again. The user may have returned.

## Why this skill exists

This is the single source of truth for asking and autonomous fallback. Other compose skills reference it at their decision points instead of repeating fallback text, so the rules stay consistent and the prompts stay small.
