# Darwin Evaluation Strategy

How to measure whether darwin (the self-evolution layer) actually helps on
long-horizon tasks, and what it costs to find out.

> **Core principle — consistent model strategy.** Same model, same benchmark,
> same harness version, different plugin state. The only independent variable is
> `darwin on vs. darwin off`. This isolates harness effect from model effect and
> makes every comparison falsifiable.

---

## 1. Long-Horizon Benchmark Survey

### 1.1 Comparison table

| Benchmark | Tasks | What it tests | Long-horizon signal | Infra | Source |
|---|---|---|---|---|---|
| **SWE-bench Verified** | 500 | Real GitHub issues, human-verified solvable, Python repos | High — multi-file edits, full repo context, avg gold patch ~3 hunks, 1.2 files | Docker per instance, `swebench` CLI or `swebench.harness` Python | `princeton-nlp/SWE-bench_Verified` on HF; `github.com/SWE-bench/SWE-bench` |
| **SWE-bench Lite** | 300 | Curated subset of Verified, filtered for self-contained fixes (≤1 file, ≤3 hunks, no images/links) | Lower — deliberately filters toward smaller patches; faster/cheaper iteration cut | Same Docker harness | `princeton-nlp/SWE-bench_Lite` |
| **SWE-bench Pro** | 1,865 (public 11 repos + held-out 12 + commercial 18) | Enterprise-grade multi-file patches, avg 107 lines across 4.1 files, ≥10 LOC change, GPL + private repos for decontamination | **Highest** — multi-file, multi-hundreds LOC, designed for hours-to-days human effort | SWE-Agent scaffold, 50 turn cap, Docker; commercial split is private | `arxiv 2509.16941` |
| **Terminal-Bench 2.1** | 89 | Containerized terminal tasks (protein assembly, async debugging, security vulns, legacy config) | High — domain-diverse, interdependent shell actions, outcome-driven tests | Harbor harness (`harbor run`), Docker or Modal, tagged releases on Harbor Hub | `github.com/harbor-framework/terminal-bench` / `tbench.ai` |
| **Terminal-Bench 3.0** | Growing (community-contributed, ~74 in-flight) | Same terminal framing, 7 domains / 31 subdomains, adversarial hardening loop | High — continuous, cheat-trial hardened | Harbor, same infra as 2.1 | Harbor Hub |
| **SWE-Lancer Diamond** | 237 IC SWE + 265 Manager (502 total, $501K payout) | Freelance Upwork tasks from Expensify repo, end-to-end Playwright tests + proposal-selection | High — end-to-end user flows, browser automation, weeks-long tasks ($50–$32K) | Unified Docker image, Playwright, user-tool browser sim | `github.com/openai/SWELancer-Benchmark` → `openai/preparedness` |
| **DeepSWE** | 91 repos × 5 langs (TS/Go/Python/JS/Rust) | Repo-level, prompts ~½ SWE-Pro length but 5.5× more LOC in solutions, 2× more output tokens | Very high — largest solutions per prompt length | Native harness + mini-SWE scaffold | `arxiv 2607.07946` |
| **GAIA** | 466 (166 dev + 300 held-out) | General-assistant Q&A: reasoning, web browsing, tool use, multimodal (PDFs, sheets, images) | Medium — 6–17 min human time, not coding-specific | Zero-shot prompt + attached files, no Docker | `gaia-benchmark` on HF |
| **SWE-bench Multimodal** | 517 | Like Verified but with visual elements (screenshots, diagrams in issues) | Medium — adds vision requirement | Same Docker harness | `SWE-bench/SWE-bench_Multimodal` |

### 1.2 Which benchmark for darwin?

Darwin's thesis is **long-horizon coding via memory + reflection** — the value
prop grows with context length, multi-step dependency, and cross-session
learning.

**Primary recommendation: SWE-bench Verified (500) or Lite (300).**

- **Why Verified over Lite for darwin:** Lite filters *against* the long-horizon
  signal (single-file, ≤3 hunks, ≤1 file). Darwin's checkpoint/memory story
  matters most on tasks that spill across files and require sustained context.
  Verified preserves that. Lite is fine for cheap pilot screening but do not
  treat Lite numbers as a long-horizon claim.
- **Why Verified over Pro/Terminal-Bench/SWE-Lancer for v1:** Verified has the
  largest public leaderboard, the best-documented Docker harness, the lowest
  setup friction, and the most transferable pricing/token data. Pro is better
  *in principle* for long-horizon but (a) its commercial split is private,
  (b) the public split is still stabilizing, (c) it requires SWE-Agent scaffold
  specifics that add a confound. Terminal-Bench is the second-best long-horizon
  pick but its tasks are *terminal-CLI diverse* rather than *repo-coding
  sustained* — it tests breadth of tool use more than depth of codebase
  reasoning. SWE-Lancer is excellent but Expensify-only and Playwright-heavy;
  harder to attribute harness effects.
- **GAIA is not recommended for darwin v1.** It tests general-assistant
  breadth (web search, multimodal), not coding harness depth. Darwin's memory
  and dream loops are not exercised.

**Tiered plan:**

| Tier | Benchmark | Purpose | When |
|---|---|---|---|
| **Pilot** | SWE-bench Lite 50-task stratified sample | Smoke test harness wiring, cost calibration, variance check | First run, <1 day |
| **Main** | SWE-bench Verified 500 (or Lite 300 if budget-constrained) | Publishable harness comparison | Primary evaluation |
| **Stretch** | Terminal-Bench 2.1 (89 tasks) | Orthogonal long-horizon signal (terminal vs. repo) | Only after Verified shows a signal |
| **Deferred** | SWE-bench Pro public split, DeepSWE, SWE-Lancer Diamond | Deeper long-horizon claims when darwin matures | Post-v1 |

### 1.3 Benchmark requirements and setup friction

| Requirement | SWE-bench Verified/Lite | Terminal-Bench 2.1 | SWE-Lancer | Pro |
|---|---|---|---|---|
| Docker | Yes (per-instance image, ~120 GB at `cache_level=env`) | Yes (Harbor, Modal or local Docker) | Yes (unified image + Playwright) | Yes |
| API keys | Your model provider only | Your model provider only | Your model provider only | Your model provider only |
| Disk | ≥120 GB free, 16 GB RAM, 8 cores recommended | Similar | Similar | Similar |
| ARM/Mac | Experimental (`--namespace ''` to build locally) | Supported | Supported | Varies |
| Evaluation command | `swebench eval verified -p preds.jsonl --run-id <id> -j 8` | `harbor run -d terminal-bench/terminal-bench@latest --agent <agent> --model <model>` | `python -m swelancer` harness | `pip install swebench && ... --dataset_name SWE-bench_Pro` |
| Result | `evaluation_results/<run_id>/report.json` + per-instance `run_instance.log` | Harbor leaderboard upload | Dollar earnings + pass@1 | Pass@1 |
| Gold check | `swebench eval verified --gold -i sympy__sympy-20590 --run-id validate-gold` | `harbor run --agent oracle` | N/A | N/A |

---

## 2. Model Survey

### 2.1 opencode free models (Zen) — catalog as of 2026-08

> All Zen free models are **exclusive to the opencode CLI/Desktop** via the
> Zen gateway (`https://opencode.ai/zen/...`), require an opencode account
> (`opencode auth login`), and are **rate-limited, limited-time offers**.
> Data may be used to improve the model. Endpoints are `opencode/*` provider.

| Model | Provider ID | Context | Pricing | SWE-bench Verified (if known) | Notes |
|---|---|---|---|---|---|
| **Big Pickle** (stealth) | `opencode/big-pickle` | 200K | Free (limited) | ~72% (community) | Stealth eval model; not stable for longitudinal comparisons |
| **MiMo-V2.5 Free** | `opencode/mimo-v2.5-free` | ~256K–1M | Free (limited) | — | Xiaomi in-house; same family as `mimo-v2.5` |
| **Hy3 Free** | `opencode/hy3-free` | 256K | Free (limited) | — | Tencent Hy; `hy3` paid is ~78% SWE-bench Verified at $0.14/$0.58 |
| **MiMo V2 Pro Free** | `opencode/mimo-v2-pro-free` | 1M | Free (limited) | ~78% (paid `mimo-v2.5-pro` is 78.9%) | Best free candidate if stable |
| **MiMo V2 Flash Free** | `opencode/mimo-v2-flash-free` | 256K | Free (limited) | ~73% (paid flash 73.4%) | Cheaper/faster tier |
| **Nemotron 3 Super/Ultra Free** | `opencode/nemotron-3-*` | 128K–1M | Free (NVIDIA trial) | Super ~52%, Ultra higher | NVIDIA trial — may expire quickly |
| **North Mini Code Free** | `opencode/north-mini-code-free` | 256K | Free (limited) | — | Smaller code model |
| **MiniMax M2.5 Free** (derivative) | via `opencode/minimax-m2.5-free` | 205K | Free (derivative) | Paid M2.5 is 80.2% — free derivative likely lower | Derivative — scores not transferable |
| **Trinity Large Preview Free** | `opencode/trinity-large-preview` | 131K | Free (limited) | ~62% | Lower tier |

**Full free listing:** `opencode models list` or `GET https://opencode.ai/zen/v1/models`
(and Zen docs at `opencode.ai/docs/zen/`). The catalog rotates — always confirm
at run time.

### 2.2 opencode Go models (paid, cheap) — the realistic comparison tier

Go models are also on the Zen gateway but **billed** (still cheap) via
`opencode-go/*` provider at `https://opencode.ai/zen/go/v1/...`.

| Model | Provider ID | Context | Input / 1M | Output / 1M | SWE-bench Verified | Output cap | Best for |
|---|---|---|---|---|---|---|---|
| **DeepSeek V4 Flash** | `opencode-go/deepseek-v4-flash` | 1M | $0.14 | $0.28 | 79.0% (Max) / 73.7% (non-think) | 384K | **Recommended comparison model** |
| **DeepSeek V4 Pro** | `opencode-go/deepseek-v4-pro` | 1M | $1.32–$1.74* | $3.48–$3.96* | 80.6% (Max) | 384K | Frontier reference (expensive) |
| **Hy3** (paid) | `opencode-go/hy3` | 256K | $0.14 | $0.58 | ~78% | 64K | Budget alt (but smaller context) |
| **Hy4 preview** | `opencode-go/hy4-preview` | 1M | $0.83 | $2.50 | — | 64K | Newer Tencent |
| **MiMo-V2.5** | `opencode-go/mimo-v2.5` | 1M | $0.14 | $0.28 | ~78.9% (pro) | 131K | Same price as Flash, less proven |
| **GLM-5 / 5.1 / 5.2 / 5.3** | `opencode-go/glm-*` | 200K–1M | $1.00–$1.40 | $3.20–$4.40 | GLM-5.2 ~78.7% | 131K | Strong but 7–15× Flash on cost |
| **Qwen3.x** | `opencode-go/qwen3.*` | 262K | varies | varies | 76–85% range | 32K | Competitive but pricier |

*DeepSeek peak vs. off-peak. Off-peak is ~50% discount (Beijing night).

### 2.3 DeepSeek deep dive — is Flash viable as the comparison model?

**Short answer: yes — DeepSeek V4 Flash is the single best choice for the
consistent-model harness comparison.**

| Property | DeepSeek V4 Flash | DeepSeek V4 Pro | DeepSeek V3 / R1 (older) |
|---|---|---|---|
| Architecture | 284B MoE, 13B active | 1.6T MoE, 49B active | V3 671B/37B, R1 reasoning |
| Context / output | 1M / 384K | 1M / 384K | 164K / 32K (R1), 164K / 16K (V3) |
| SWE-bench Verified | **79.0% Max**, 78.6% High, 73.7% non-think | 80.6% Max | V3 ~24% (old harness), not comparable |
| SWE-bench Pro | 52.6% Max | 55.4% Max | — |
| Terminal-Bench 2.1 | 82.7 (0731 post-train) | 67.9 | — |
| Input $/M (miss / hit) | **$0.14 / $0.003** | $1.74 / $0.145 | V3 $0.27/$0.027, R1 $0.55/$0.055 |
| Output $/M | **$0.28** | $3.48 | V3 $1.10, R1 $2.19 (incl. thinking) |
| Open weights | Yes (MIT) | Yes (MIT) | Yes |
| Reasoning | Native, variants `high`/`max` | Same | R1 visible CoT |
| Opencode ID | `opencode-go/deepseek-v4-flash` | `opencode-go/deepseek-v4-pro` | `deepseek/*` via other providers |

**Why Flash specifically:**

1. **Cheapest frontier-adjacent model available.** At $0.14/$0.28 it is
   90–107× cheaper than Claude Opus on output, ~12× cheaper than Pro, and
   7–15× cheaper than GLM-5/Qwen. For a 300-task SWE-bench run this is the
   difference between ~$65 and ~$850 at the harness level.
2. **SWE-bench Verified gap to Pro is tiny.** Flash Max 79.0% vs. Pro Max
   80.6% — 1.6 points. The non-think gap is also small (73.7 vs 73.6). For a
   *harness comparison* the absolute score matters less than having enough
   headroom to detect a harness delta; 79% leaves ample headroom but is not
   so saturated that harness effects are masked (counterpoint: Opus 5 at 96%
   would be saturated — do not use a saturated model for harness A/B).
3. **1M context, 384K output** — tolerates long-horizon SWE-bench tasks
   without truncation.
4. **Stable API + open weights** — can pin version, can self-host if needed,
   MIT license.
5. **Variants `high`/`max`** selectable via `opencode --variant max` — use
   `max` for the agentic SWE-bench harness (long loops), `high` for quicker
   iteration. Pick one and hold it constant across both harness conditions.

**Caveats:**

- Flash is **not** a free Zen model — `deepseek-v4-flash-free` may exist
  ephemerally but do not rely on it. Budget for paid Go pricing (still trivial).
- The July 0731 post-train **07→82.7 Terminal-Bench jump** is vendor-reported on
  a non-public harness (DeepSeek Harness, minimal mode). Reproduction data is
  not yet independent — treat Terminal-Bench numbers as indicative, not gospel.
- Cache-hit pricing ($0.003/M input) requires caching to actually work end to
  end (system prompt stability, provider support). Do not budget assuming
  98% cache-hit rates unless you verify.

### 2.4 Can free models give conclusive harness comparisons?

**For smoke tests: yes. For publishable claims: no — use Flash (paid) as the
anchor.**

| Question | Answer |
|---|---|
| Can I wire up the harness A/B and see *any* delta on free models? | Yes. Even a 65–72% SWE-bench free model (Big Pickle, etc.) will show whether darwin's memory/goal loops change pass rate or token efficiency. Use a 25–50 task pilot on a free model to shake out infra before spending. |
| Can I make a *conclusive* harness claim on free models? | No, for three reasons: (1) free catalog rotates without notice — your "same model" may disappear mid-experiment; (2) free models have aggressive rate limits and opaque routing that adds variance unrelated to the harness; (3) free-model SWE-bench scores are often unreported or stale, so you cannot contextualize your delta against the model's known capability. |
| Does a free-model pilot still have value? | Yes — as a **cost-zero rehearsal** of the Docker harness, prediction format, and statistical power. But promote to a paid model (Flash) for any result you intend to cite. |
| What about GLM/Hy/MiMo free derivatives? | Avoid for the main comparison. Derivatives are fine-tunes with unknown deltas to the base model; you cannot claim "same model" if the model itself is a moving target. |

**Recommended split:**

- **Pilot (free):** 25–50 tasks on any stable free model that supports tools
  (Big Pickle or MiMo-V2.5-Free if available) — just to validate harness wiring
  and measure token variance.
- **Main (paid):** 300 (Lite) or 500 (Verified) on `opencode-go/deepseek-v4-flash`
  with `--variant max` pinned — this is the citable result.

---

## 3. Token Budget & Cost Model

### 3.1 What the literature says (Bai et al. 2026 — 8 frontier models × 500 SWE-bench Verified × 4 runs via OpenHands)

| Metric | Value |
|---|---|
| **Avg total tokens per agentic task** | **4.17M tokens** (all types summed; input dominates) |
| **Input-to-output ratio** | **~154:1** |
| **Per-task variance** | **Up to 30×** across runs on the same task |
| **Avg cost per task (frontier pricing)** | **$1.86** (mixed frontier pricing at time of study) |
| **Token-efficient vs. token-hungry models** | Kimi-K2 / Sonnet 4.5 ≈ +1.5M tokens over GPT-5 on identical tasks |
| **Accuracy vs. cost** | Peaks at intermediate spend; max-cost runs are *worse* (thrashing signal) |
| **Model prediction of own spend** | r = 0.05–0.39, systematic underestimate |

**HAL Mini (50-task) observed totals** (no cache accounting, SWE-Agent scaffold):

| Model | Total cost for 50 tasks | Per-task |
|---|---|---|
| Gemini 2.0 Flash | $4.72 | $0.09 |
| DeepSeek V3 | $11.77 | $0.24 |
| GPT-5 Medium | $162.93 | $3.26 |
| Claude Sonnet 4.5 (Sep 2025) | $505.92 | $10.12 |
| Claude Opus 4.1 | ~$1,351 | ~$27 |

These span cheap → frontier; Flash at opencode-go pricing sits near the
low end despite its 79–80% SWE-bench score.

### 3.2 Cost estimates for darwin's evaluation

**Per-task input assumptions for budgeting:**

- Agentic SWE-bench tasks are *input-dominated* (154:1). Budget on input tokens.
- Empirical: ~1.5–3M input tokens/task is a reasonable planning anchor for
  opencode-style harnesses (consistent with 4.17M total, mostly input). Lite
  tasks may skew lower (single-file, fewer turns); Verified and long-horizon
  tasks skew higher.
- Cache-hit input ($0.003/M for Flash) can in theory collapse the bill, but
  **do not budget assuming >50% cache-hit rates** unless you have measured them
  on your harness. Use cache-miss pricing for conservative estimates, note
  cache-hit as upside.

**Single pass, cache-miss pricing (conservative):**

| Benchmark | Tasks | Model | Input $/M | Output $/M | Est. per-task (2M in + 0.1M out) | Suite total (1 pass) |
|---|---|---|---|---|---|---|
| **SWE-bench Lite (pilot)** | 50 | Free (Big Pickle etc.) | Free | Free | $0.00 | **$0.00** + Docker infra |
| **SWE-bench Lite** | 300 | Flash `$0.14/$0.28` | $0.14 | $0.28 | ~$0.31 | **~$93** |
| **SWE-bench Verified** | 500 | Flash `$0.14/$0.28` | $0.14 | $0.28 | ~$0.31 | **~$155** |
| **SWE-bench Lite** | 300 | Pro `$1.74/$3.48` | $1.74 | $3.48 | ~$3.83 | **~$1,149** |
| **SWE-bench Verified** | 500 | Pro `$1.74/$3.48` | $1.74 | $3.48 | ~$3.83 | **~$1,915** |
| **SWE-bench Verified** | 500 | Sonnet 4.5 `$3/$15` | $3.00 | $15.00 | ~$7.50 | **~$3,750** |
| **Terminal-Bench 2.1** | 89 | Flash | $0.14 | $0.28 | ~$0.31 | **~$28** |

*Per-task estimate uses 2M input + 0.1M output as a planning anchor. Actual will
vary with scaffold verbosity and cache behavior. The 4.17M-token average from
Bai et al. would put Flash at ~$0.60/task and Sonnet at ~$12.60/task — the
2M-anchor is intentionally the lean end; see variance note below.*

**With cache hits (optimistic, 70% of input cached):**

Flash effective per-task at 70% cached (1.4M cached @ $0.003 + 0.6M miss @ $0.14):
~$0.09 input + $0.03 output ≈ **$0.12/task**. Lite 300 → **~$36**. Verified 500 →
**~$60**. This is why Flash is the right anchor — even your conservative and
optimistic bounds are both cheap.

### 3.3 Statistical power — how many runs?

Token variance is **30× per task across runs** (Bai et al.). A single pass
per condition is noisy. Plan for:

| Design | Runs per condition | Tasks | Total model calls (both conditions) | What it buys |
|---|---|---|---|---|
| **Minimal publishable** | 1 | 300 (Lite) | 600 | Point estimate;Wilson CI width ~±5.5% at 50% resolve |
| **Recommended** | 3 | 300 (Lite) | 1,800 | Median ± IQR per task, harness delta has a real CI; matches Terminal-Bench's k=5 norm |
| **Strong** | 3 | 500 (Verified) | 3,000 | Tighter CI (±3.5% at 50%), more repos covered |
| **Lite pilot** | 1 | 50 (stratified) | 50–100 (both conditions) | Infra shakedown, variance calibration |

**Budget for the recommended (Lite 300 × 3 runs) on Flash:**

- 1,800 task-attempts × $0.31/task ≈ **$558** (cache-miss anchor)
- With 70% cache-hit ≈ **$216**
- Either way, well under $1K for a properly powered harness comparison.

**Equivalent on Sonnet 4.5:** the same design is **~$13,500** (3 × 300 × $7.50
× 2 conditions = 1,800 × $7.50). Flash is **~24× cheaper** than Sonnet for the
same statistical power.

### 3.4 Rule of thumb for grant/budget proposals

> **"Lite 300 × 3 runs × 2 conditions on DeepSeek V4 Flash ≈ $220–$560.
> Verified 500 × 3 runs × 2 conditions ≈ $370–$930.
> Add $200 headroom for reruns and Docker egress. Total ask: <$1.5K for a
> publishable harness A/B. Pilot on free models: $0 (infra only)."**

---

## 4. Evaluation Matrix

### 4.1 Recommended matrix (the thing to actually run)

```
Model (constant)          : opencode-go/deepseek-v4-flash --variant max
Benchmark (constant)      : SWE-bench Lite 300 (pilot 50 → main 300)
                              + optionally SWE-bench Verified 500 if budget allows
Harness (independent var) : opencode vanilla  vs.  opencode + darwin plugin
Runs per cell             : 3 (median ± IQR)
Metric (primary)          : % Resolved (pass@1 per instance, official swebench harness)
Metrics (secondary)       : $/resolved-task, tokens/resolved-task, steps/resolved
```

| Cell | Benchmark | Harness | Model | Runs | Tasks | Est. cost (Flash, cache-miss) |
|---|---|---|---|---|---|---|
| A1 (pilot) | Lite-50 | vanilla | Flash | 1 | 50 | ~$16 |
| A2 (pilot) | Lite-50 | darwin | Flash | 1 | 50 | ~$16 |
| B1 | Lite-300 | vanilla | Flash | 3 | 300 | ~$279 |
| B2 | Lite-300 | darwin | Flash | 3 | 300 | ~$279 |
| C1 | Verified-500 | vanilla | Flash | 3 | 500 | ~$465 |
| C2 | Verified-500 | darwin | Flash | 3 | 500 | ~$465 |
| D1 | TB 2.1-89 | vanilla | Flash | 3 | 89 | ~$83 |
| D2 | TB 2.1-89 | darwin | Flash | 3 | 89 | ~$83 |

**Execution order:**

1. **A1+A2 pilot** (free model or Flash, 50 tasks) — validate harness, measure
   variance, confirm prediction format.
2. **B1+B2 main** (Lite 300, Flash, 3 runs) — the **publishable** harness A/B.
   If darwin shows a signal here, continue.
3. **C1+C2** (Verified 500) — only if B shows a signal worth scaling or if Lite's
   filtered nature is a concern for the claim.
4. **D1+D2** (Terminal-Bench) — orthogonal validation, only if the repo-coding
   signal is clean.

**Total for the publishable core (B1+B2): ~$560 cache-miss / ~$220 cached.**
**Total for full Lite+Verified (B+C): ~$1,490 / ~$600.** Well within a small
research budget.

### 4.2 What to hold constant, what to vary

| Hold constant | Why |
|---|---|
| Model ID + variant (`deepseek-v4-flash`, `max`) | Isolates harness effect |
| Benchmark split + instance list | Same tasks, fair comparison |
| Harness version (`swebench` CLI version, Docker `namespace`, `cache_level`) | Reproducibility |
| `max_workers`, timeout, `cache_level` | Avoid infra confounds |
| System prompt / scaffold | opencode's own prompt is the scaffold — don't swap scaffolds mid-experiment (SWE-Agent vs. opencode is a different question) |

| Vary (one at a time) | Purpose |
|---|---|
| `darwin off` vs. `darwin on` | The harness question |
| Later: darwin sub-features (memory only, goal only, full) | Ablation — which loop matters |

### 4.3 Grading — how to score

- **Primary:** official `swebench` harness pass/fail per instance
  (`evaluation_results/<run_id>/report.json` → `resolved` boolean). Aggregate
  as **% Resolved** with **Wilson 95% CI** (use `statsmodels` or the
  `analysis/get_results.py` from `SWE-bench`).
- **Secondary (per resolved task):** `$` (via proxy or provider usage),
  `input_tokens`, `output_tokens`, `steps` (tool calls). Report **median ± IQR**
  over the 3 runs, not mean (distribution is heavy-tailed).
- **Comparison statistic:** delta in % Resolved (darwin − vanilla) with
  **paired** analysis (McNemar or paired bootstrap per task) where possible —
  same tasks, same model, so pairing is valid and more powerful than unpaired.
- **Do not** compare darwin-vs-vanilla by mixing models or benchmarks in one
  number.

---

## 5. How to Run Benchmarks with opencode: Vanilla vs. Darwin

### 5.1 Prerequisite: opencode model fallback config

opencode now has a **native** fallback system (no plugin needed) — use it so
both harness conditions share the same resilience story.

**Native fallbacks** (`opencode.json`, supported natively as of `anomalyco/opencode`
PR #26292 — check your pinned version):

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "model": "opencode-go/deepseek-v4-flash",
  // Top-level fallback chain — tried in order on 429/5xx/overload
  "fallbacks": ["opencode-go/deepseek-v4-flash", "opencode-go/hy3", "opencode/mimo-v2.5-free"],
  "cooldown_seconds": 300,                // per-provider cooldown after failure
  // Optional: per-agent override
  "agents": {
    "build": {
      "model": "opencode-go/deepseek-v4-flash",
      "fallbacks": ["opencode-go/hy3"]
    }
  }
}
```

**Behavior:** on rate-limit / 5xx, opencode marks the failed provider on cooldown
(300s default, 6h for quota limits, or `retry-after` header) and streams from
the next model in the chain. Stream errors (`200` with `{type:"error"}`) also
trigger fallback. Success clears the winner's cooldown.

> **If your opencode pin predates native fallbacks:** use the community plugin
> `opencode-model-fallback` (`@razroo/opencode-model-fallback` /
> `Agent-Pattern-Labs/opencode-model-fallback`):
>
> ```json
> { "plugin": ["@razroo/opencode-model-fallback"] }
> ```
>
> Then per-agent:
> ```json
> { "agents": { "build": { "model": "opencode-go/deepseek-v4-flash", "fallback_models": ["opencode-go/hy3", "opencode/mimo-v2.5-free"] } } }
> ```
>
> Or global in `.opencode/opencode-model-fallback.jsonc`:
> ```jsonc
> { "fallback_models": ["opencode-go/hy3", "opencode/mimo-v2.5-free"], "cooldown_seconds": 60 }
> ```
>
> Native `fallbacks` and plugin `fallback_models` are **not** the same key —
> check `opencode --help` / `opencode.json` schema at your version.

**Recommendation for the evaluation:**

```jsonc
// For the harness comparison, use a Go-heavy chain so both conditions
// stay on the same model unless the primary is truly unavailable.
// Do NOT mix in a free model as second fallback unless you want to
// deliberately measure degraded-model behavior.
{
  "model": "opencode-go/deepseek-v4-flash",
  "fallbacks": ["opencode-go/deepseek-v4-flash", "opencode-go/hy3"],
  "cooldown_seconds": 300
}
```

Pin the **exact** model string (including provider prefix) and keep it
identical in both conditions. Log which model actually handled each task
(opencode now propagates `usedFallback` attribution).

### 5.2 Vanilla opencode harness — SWE-bench via opencode

There are two viable paths. **Path A** (AlphaDiana-style) is cleaner for a
harness comparison because it runs `opencode run` directly inside the official
SWE-bench container, so the only delta between conditions is the plugin list.

#### Path A: `opencode run` inside `swebench_container` (recommended)

Adapted from `github.com/tmlr-group/AlphaDiana`'s
`swe_bench_verified_opencode_qwen35_27b.yaml` pattern. The idea: each SWE-bench
task's official Docker image is the sandbox; `opencode` runs inside it.

**Prereqs:**

```bash
git clone https://github.com/SWE-bench/SWE-bench && cd SWE-bench && pip install -e .
docker info  # must be running; ≥120 GB free
opencode --version  # pin this version and record it
```

**Vanilla opencode config for the container** (written at task start):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "opencode-go/deepseek-v4-flash",
  "fallbacks": ["opencode-go/hy3"]
}
```

**Invocation per task** (inside the container, after `swebench` has checked out
the repo at `base_commit`):

```bash
# Vanilla
opencode run --model opencode-go/deepseek-v4-flash --variant max \
  "Fix the following issue in this repository. The fix must pass all existing tests.

<issue text: $PROBLEM_STATEMENT>

Repository is at: $(pwd)
When done, ensure changes are committed or at least staged so 'git diff HEAD' captures the patch."
# Patch to grade:
git diff HEAD > /tmp/patch.diff
# Then hand /tmp/patch.diff to the swebench evaluator:
# swebench eval ... OR the AlphaDiana scorer extracts it automatically.
```

**Darwin variant** — identical command, but the container's `opencode.json`
additionally loads the plugin:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "opencode-go/deepseek-v4-flash",
  "fallbacks": ["opencode-go/hy3"],
  "plugin": ["@darwin/opencode-plugin"]
}
```

No other change. The prompt, model, timeout, and sandbox are byte-identical.
That is what makes the comparison fair.

**Orchestrating the matrix** (AlphaDiana provides a ready-made runner that does
the container lifecycle + patch extraction + `swe_bench` scoring):

```yaml
# swe_bench_verified_opencode_flash.yaml (AlphaDiana-style)
benchmark:
  name: swe_bench
  config:
    dataset: princeton-nlp/SWE-bench_Verified  # or SWE-bench_Lite for pilot
    split: test
    include_hints: false
    max_tasks: 50          # 50 for pilot, 300/500 for main
sandbox:
  name: swebench_container
  config:
    namespace: swebench
    keep_container: false
    keep_logs: true
scorer:
  name: swe_bench
  config:
    timeout: 1800
```

```bash
# Pilot — vanilla
python -m alphadiana.cli run swe_bench_verified_opencode_flash.yaml \
  -o run_id=vanilla-flash-lite50 -o benchmark.config.max_tasks=50

# Pilot — darwin (same config plus plugin injection via container setup)
# variant: inject "plugin": ["@darwin/opencode-plugin"] into opencode.json
# before 'opencode run' (via sandbox setup script or config overlay)
python -m alphadiana.cli run swe_bench_verified_opencode_flash_darwin.yaml \
  -o run_id=darwin-flash-lite50 -o benchmark.config.max_tasks=50
```

> If not using AlphaDiana, the same loop can be scripted directly: for each
> `instance_id`, `docker run --rm -v $(pwd):/workspace swebench/<instance_id>`
> → run `opencode run` inside → `git diff HEAD` → append `{instance_id,
> model_patch, model_name_or_path}` to `predictions.jsonl` → finally
> `swebench eval verified -p predictions.jsonl --run-id <id> -j 8`.

#### Path B: local harness, predictions file only

Simpler to start but **less isolated** (no per-task container during solving):

1. For each task, checkout the repo at `base_commit` locally (or use
   `swebench`'s `prepare_images` to pull the image and `docker run` it).
2. Run `opencode run` (vanilla vs. darwin) with the issue prompt.
3. Capture `git diff HEAD` as `model_patch`.
4. Write `predictions.jsonl` in the swebench format:

   ```jsonl
   {"instance_id": "django__django-11019", "model_name_or_path": "opencode-go/deepseek-v4-flash", "model_patch": "diff --git a/..."}
   ```

5. Grade:

   ```bash
   swebench eval verified -p predictions.jsonl --run-id vanilla-flash-v1 -j 8
   swebench eval verified -p predictions-darwin.jsonl --run-id darwin-flash-v1 -j 8
   python -m analysis.get_results evaluation/verified/2026*/  # compare
   ```

Path B is fine for the 50-task pilot; use Path A for the 300/500 publishable
run (isolation + reproducibility).

### 5.3 Terminal-Bench via Harbor (optional second benchmark)

```bash
uv tool install 'harbor[modal]'   # or pipx
# Vanilla
uv run harbor run -d terminal-bench/terminal-bench@latest \
  --agent opencode --model opencode-go/deepseek-v4-flash \
  --n-concurrent 8 --env docker

# Darwin — same, but the opencode image/config includes the darwin plugin
# (bake it into the agent image or mount opencode.json with the plugin entry)
```

Grade via Harbor's outcome-driven tests (container final-state checks). Report
resolution rate + tokens ± IQR.

### 5.4 What to log for every task (both conditions)

For reproducibility and cost analysis, capture per task:

- `instance_id`, `model` (requested vs. actually used via `usedFallback`),
  `variant`, `harness` (`vanilla`/`darwin`), `run_id`, `run_index` (1..3)
- `resolved` (from `report.json`), `patch` (`git diff HEAD`)
- Token usage: `input_tokens`, `cached_input_tokens`, `output_tokens`,
  `reasoning_tokens` (if R1-style), `total_tokens` — from provider `usage`
  or a recording proxy (see `spendbench/proxy.py` pattern)
- `wall_time_seconds`, `steps` (tool calls)
- `darwin_events` (if darwin): dream/distill triggers, memory hits

A local recording proxy between opencode and the provider (forwarding SSE
verbatim and logging `usage`) is the most reliable way to get provider-
independent token counts. See `github.com/ajvikram/spendbench` for a reference
implementation.

---

## 6. Free vs. Paid: What You Can Conclusively Test

### 6.1 What free models can do

| Test | Conclusive on free? | Notes |
|---|---|---|
| Harness wiring & Docker plumbing | **Yes** | Zero-cost rehearsal |
| Token/counting pipeline & prediction format | **Yes** | Validate `predictions.jsonl` + `swebench eval` |
| Whether darwin changes behavior at all | **Weakly** | A delta on a 65%-capable free model suggests the harness does *something*, but not that it helps a frontier model |
| Magnitude of harness delta on SWE-bench | **No** | Free model capability is the bottleneck; harness effect is masked or inflated. Catalog rotation adds variance. |
| $/resolved and tokens/resolved | **No** | Free = $0 by definition; not a useful economic measurement |
| Long-horizon claim (>200 steps, cross-file) | **No** | Free models rarely sustain long trajectories; you need a model that can actually do long-horizon work to measure whether darwin helps it. |
| Publishable / citable number | **No** | Reviewers will rightly ask for a stable, frontier-adjacent model |

### 6.2 What needs paid models (and which to use)

| Need | Model | Why | Cost to prove it |
|---|---|---|---|
| Publishable harness A/B on SWE-bench | **DeepSeek V4 Flash** (`opencode-go/deepseek-v4-flash`, `max`) | Cheapest frontier-adjacent (79% SWE-bench), 1M context, stable API, open weights, 1.6-point gap to Pro | Lite 300 × 3 runs × 2 conditions ≈ **$220–$560** |
| Show darwin scales beyond Lite's single-file tasks | Same Flash on **Verified 500** | Lite filters long-horizon signal; Verified preserves it | +~$310–$620 for Verified 500 × 3 × 2 |
| Orthogonal terminal-bench validation | Same Flash on **TB 2.1** | Tests breadth of tool use vs. depth of repo reasoning | ~$170 for 89 × 3 × 2 |
| Frontier reference (is darwin competitive with labs?) | Flash vs. **V4 Pro** or **Sonnet 4.6** at same harness | Proves the harness, not the model, is the differentiator | Pro: ~$2.3K for Lite 300 × 3; Sonnet: ~$13.5K — do only if funded |
| Ablation (which darwin loop matters?) | Flash, Lite 300 | Memory-only vs. goal-only vs. full | Same budget per ablation arm as main |

### 6.3 Budget summary (all numbers cache-miss, Flash pricing)

| Scope | Design | Suite cost (Flash) | Citable? |
|---|---|---|---|
| **Pilot** | Lite 50 × 1 run × 2 conditions | ~$32 (or $0 on free) | No — shakedown only |
| **Main (recommended)** | Lite 300 × 3 runs × 2 conditions | **~$560** | **Yes** |
| Main + Verified | + Verified 500 × 3 × 2 | ~$1,490 | Yes, stronger claim |
| Main + TB 2.1 | + TB 2.1 89 × 3 × 2 | ~$730 | Yes, broader claim |
| Full (Lite+Verified+TB) | All of the above | ~$1,660 | Strongest story |

With 70% cache-hit, divide by ~2.5. **Even the full story is <$700 in the
optimistic case.** This is why Flash is the right anchor — statistical power
is cheap.

---

## 7. Interpreting Results — What Counts as a Win

### 7.1 Primary outcome

**Delta in % Resolved (darwin − vanilla) on the same model and benchmark,
with a 95% Wilson CI that excludes 0.** On Lite 300, a **+5 point** delta
(e.g., 45% → 50%) has a Wilson CI of roughly ±5.5% per arm — you need
~+7 points to be clearly separated at n=300, or pool across 3 runs for
tighter inference. On Verified 500, ±3.5% per arm — +5 points is already
interesting.

Use **paired** analysis (McNemar on per-task 2×2 of vanilla/darwin outcomes)
for more power — the pairing is valid because the tasks and model are fixed.

### 7.2 Secondary outcomes (even if % Resolved is flat)

Darwin could be valuable even without a pass-rate bump:

- **$/resolved-task down** at same pass rate = cheaper long-horizon work
  (memory avoids re-reading files; goal loop avoids thrashing).
- **Tokens/resolved down** — same mechanism, provider-independent.
- **Success on long-horizon slice** — stratify by patch size (≥3 files,
  ≥100 LOC) or by steps (>50). Darwin's edge should concentrate there per
  the MiMo blog claim (~50% win rate <200 steps, >65% above 200 steps).
- **Variance down** — IQR of tokens/resolved narrower under darwin suggests
  more predictable spend.

### 7.3 What to do if darwin is flat or worse

- Check that darwin's memory/goal loops actually triggered (logs).
- Check that fallback did not silently swap models (attribution).
- Check per-slice: if darwin helps >200-step tasks but hurts short tasks, that
  is still a coherent long-horizon story — report the slice.
- Consider that Lite's filtered distribution may suppress the signal; rerun the
  same model on Verified 500 before concluding.

---

## 8. Reproducibility Checklist

- [ ] `opencode --version` and `darwin` plugin version pinned and logged
- [ ] `swebench` / `harbor` harness version pinned
- [ ] Model string pinned (`opencode-go/deepseek-v4-flash`, variant `max`)
- [ ] `opencode.json` (both conditions) committed alongside results
- [ ] `fallbacks` / plugin list is the only diff between conditions (verify with `diff`)
- [ ] Docker `namespace`, `cache_level`, `max_workers`, timeout recorded
- [ ] `predictions.jsonl` + `evaluation_results/<run_id>/` + per-task logs archived
- [ ] Token usage captured at API boundary (proxy or provider `usage`), not harness self-report
- [ ] 3 runs per cell, median ± IQR reported, Wilson CI for % Resolved
- [ ] Paired per-task comparison (McNemar / paired bootstrap) for the headline delta

---

## 9. Quick Start

```bash
# 0. Install harness
git clone https://github.com/SWE-bench/SWE-bench && cd SWE-bench && pip install -e .
docker info  # need ≥120 GB free

# 1. Pilot on free model (optional, $0)
cat > /tmp/opencode-vanilla.json <<'JSON'
{"$schema":"https://opencode.ai/config.json","model":"opencode/big-pickle"}
JSON
# Run 25 tasks (adapt to your runner — see §5.2 Path A/B)
# → predictions-vanilla-lite50.jsonl → swebench eval verified -p ... --run-id vanilla-pilot

# 2. Same 25 tasks with darwin
cat > /tmp/opencode-darwin.json <<'JSON'
{"$schema":"https://opencode.ai/config.json","model":"opencode/big-pickle","plugin":["@darwin/opencode-plugin"]}
JSON
# → predictions-darwin-lite50.jsonl → swebench eval verified -p ... --run-id darwin-pilot
# → compare: python -m analysis.get_results evaluation/...

# 3. Promote to paid Flash for citable result
#    In both JSONs replace "opencode/big-pickle" with "opencode-go/deepseek-v4-flash"
#    and add "--variant max" to opencode run invocations.
#    Set fallbacks:
cat > opencode.json <<'JSON'
{
  "$schema": "https://opencode.ai/config.json",
  "model": "opencode-go/deepseek-v4-flash",
  "fallbacks": ["opencode-go/hy3"],
  "cooldown_seconds": 300
}
JSON
# darwin variant adds: "plugin": ["@darwin/opencode-plugin"]

# 4. Main run — Lite 300 × 3 (AlphaDiana or loop script)
for run in 1 2 3; do
  # vanilla
  python -m alphadiana.cli run swe_bench_verified_opencode_flash.yaml \
    -o run_id=vanilla-lite300-r$run -o benchmark.config.max_tasks=300
  # darwin
  python -m alphadiana.cli run swe_bench_verified_opencode_flash_darwin.yaml \
    -o run_id=darwin-lite300-r$run -o benchmark.config.max_tasks=300
done
# Grade + compare (see §5.2)
```

---

## 10. Sources & Further Reading

- SWE-bench: `swebench.com` / `github.com/SWE-bench/SWE-bench` / Jimenez et al. 2024 (ICLR)
- SWE-bench Verified: `swebench.com/verified` / OpenAI collaboration report (2024-08-13)
- SWE-bench Lite: `swebench.com/lite.html`
- SWE-bench Pro: Deng et al. 2025 (`arxiv 2509.16941`) — the long-horizon benchmark to watch
- Terminal-Bench 2.1: `tbench.ai` / `github.com/harbor-framework/terminal-bench` / Merrill et al. 2026
- Harbor: `harborframework.com/docs` / `hub.harborframework.com`
- SWE-Lancer: `openai.com/index/swe-lancer` / `github.com/openai/SWELancer-Benchmark` / Miserendino et al. 2025
- DeepSWE: `arxiv 2607.07946`
- Token/cost study: Bai et al. 2026 — *How Do AI Agents Spend Your Money?* (`arxiv 2604.22750`) — 4.17M tokens/task, 154:1 input dominance, 30× variance; companion `nilenso/swe-bench-pro-cost-token-time-analysis`
- SpendBench: `github.com/ajvikram/spendbench` — $/solved-task harness, recording proxy pattern
- AlphaDiana: `github.com/tmlr-group/AlphaDiana` — opencode-in-swebench-container pattern
- DeepSeek V4 Flash: `huggingface.co/deepseek-ai/DeepSeek-V4-Flash` / `api-docs.deepseek.com` / `developersdigest.tech/blog/deepseek-v4-flash-0731`
- opencode Zen: `opencode.ai/docs/zen` / `opencode.ai/zen/v1/models` (live catalog)
- opencode Go: `opencode.ai/go` / `opencode.ai/docs/go`
- opencode fallback PR: `github.com/anomalyco/opencode/pull/26292` + `opencode-model-fallback` (`Agent-Pattern-Labs/opencode-model-fallback`)
- models.dev catalog: `models.dev` / `models.opencode.ai`
- BenchLM tracking: `benchlm.ai/benchmarks/swe-bench-verified` + `anotherwrapper.com/tools/llm-pricing/evals/swe-bench-verified`
