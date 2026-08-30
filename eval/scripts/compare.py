#!/usr/bin/env python3
"""
compare.py — comparison report for darwin experiment shapes.

Takes multiple prediction JSONL files (swebench format, optionally enriched
with cost/tokens/turns/model fields) and produces:

- Per-experiment: resolved rate (+ Wilson 95% CI), avg cost, cost/resolved,
  turn stats, cache hit rate, model distribution (for mixture shapes)
- Cross-experiment: where mixture landed vs best single, $0 vs $50 spend,
  darwin delta per model pair

Inputs:
  positional: one or more prediction JSONL paths
              id is inferred from parent dir basename (eval/results/<id>/predictions.jsonl)
              or from --names override.

  Optional: --results-dir DIR to locate per-experiment report.json / meta.json
            alongside each predictions.jsonl (written by run_matrix.sh).
            If a sibling report.json or meta.json exists, resolved and cost
            are enriched from there.

Outputs:
  - Markdown table to stdout and optionally --out-md
  - JSON summary to stdout (with --json) or --out-json
  - Both files when called by run_matrix.sh (eval/results/comparison.md/.json)

No external dependencies beyond stdlib.

Field tolerance (per-record or per-report):
  resolved:  entry.resolved | entry.pass | entry.success | report.resolved_ids
  cost:      entry.cost | entry.metrics.cost | entry.usage.cost | entry.total_cost
  tokens:    entry.tokens.{input,output,cacheRead,cacheWrite}
             | entry.input_tokens / entry.output_tokens / entry.cached_input_tokens
             | entry.usage.{input_tokens,output_tokens,cached_tokens}
  turns:     entry.turns | entry.steps | entry.num_turns | entry.tool_calls | entry.iterations
  model:     entry.model | entry.modelID | entry.providerID/entry.modelID | entry.model_name_or_path
  cacheHit:  cached / (cached + input) or entry.cache_hit_rate
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------

def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open() as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"warn: {path}:{i} skip bad JSON: {e}", file=sys.stderr)
    return rows

def load_report(report_path: Path) -> Optional[Dict[str, Any]]:
    if not report_path.exists():
        return None
    try:
        with report_path.open() as f:
            return json.load(f)
    except Exception as e:
        print(f"warn: failed to load report {report_path}: {e}", file=sys.stderr)
        return None

def load_meta(meta_path: Path) -> Optional[Dict[str, Any]]:
    if not meta_path.exists():
        return None
    try:
        with meta_path.open() as f:
            return json.load(f)
    except Exception as e:
        print(f"warn: failed to load meta {meta_path}: {e}", file=sys.stderr)
        return None

def get_nested(d: Dict[str, Any], *keys: str, default=None):
    cur: Any = d
    for k in keys:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return default
    return cur

def extract_resolved(entry: Dict[str, Any]) -> Optional[bool]:
    for k in ("resolved", "pass", "success", "is_resolved", "resolved_bool"):
        if k in entry:
            v = entry[k]
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return bool(v)
            if isinstance(v, str):
                if v.lower() in ("true", "1", "yes", "pass"): return True
                if v.lower() in ("false", "0", "no", "fail"): return False
    # swebench report style: status == "resolved"
    for k in ("status", "result"):
        if k in entry and isinstance(entry[k], str) and entry[k].lower() == "resolved":
            return True
    return None

def extract_cost(entry: Dict[str, Any]) -> Optional[float]:
    for k in ("cost", "total_cost", "totalCost"):
        if k in entry and isinstance(entry[k], (int, float)):
            return float(entry[k])
    for outer in ("metrics", "usage", "stats"):
        if outer in entry and isinstance(entry[outer], dict):
            for k in ("cost", "total_cost", "totalCost"):
                if k in entry[outer] and isinstance(entry[outer][k], (int, float)):
                    return float(entry[outer][k])
    # tokens-based cost not computed here; left to report
    return None

def extract_tokens(entry: Dict[str, Any]) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
    # returns (input, output, cache_read, cache_write)
    # try tokens dict
    tok = entry.get("tokens") if isinstance(entry.get("tokens"), dict) else None
    if tok:
        inp = tok.get("input")
        out = tok.get("output")
        cr = tok.get("cacheRead", tok.get("cache_read", tok.get("cached_input_tokens", tok.get("cache_read_tokens"))))
        cw = tok.get("cacheWrite", tok.get("cache_write"))
        # handle nested cache object
        if isinstance(tok.get("cache"), dict):
            cr = cr or tok["cache"].get("read", tok["cache"].get("hit"))
            cw = cw or tok["cache"].get("write")
        return (
            int(inp) if isinstance(inp, (int, float)) else None,
            int(out) if isinstance(out, (int, float)) else None,
            int(cr) if isinstance(cr, (int, float)) else None,
            int(cw) if isinstance(cw, (int, float)) else None,
        )
    # flat fields
    inp = entry.get("input_tokens", entry.get("inputTokens", entry.get("prompt_tokens")))
    out = entry.get("output_tokens", entry.get("outputTokens", entry.get("completion_tokens")))
    cr = entry.get("cached_input_tokens", entry.get("cache_read_tokens", entry.get("cacheRead")))
    cw = entry.get("cache_write_tokens", entry.get("cacheWrite"))
    # usage nested
    if inp is None and isinstance(entry.get("usage"), dict):
        u = entry["usage"]
        inp = u.get("input_tokens", u.get("prompt_tokens"))
        out = u.get("output_tokens", u.get("completion_tokens"))
        cr = cr or u.get("cached_tokens", u.get("cache_read_tokens"))
    return (
        int(inp) if isinstance(inp, (int, float)) else None,
        int(out) if isinstance(out, (int, float)) else None,
        int(cr) if isinstance(cr, (int, float)) else None,
        int(cw) if isinstance(cw, (int, float)) else None,
    )

def extract_turns(entry: Dict[str, Any]) -> Optional[int]:
    for k in ("turns", "steps", "num_turns", "tool_calls", "iterations", "num_steps"):
        if k in entry and isinstance(entry[k], (int, float)):
            return int(entry[k])
    for outer in ("metrics", "usage", "stats"):
        if outer in entry and isinstance(entry[outer], dict):
            for k in ("turns", "steps", "tool_calls"):
                if k in entry[outer] and isinstance(entry[outer][k], (int, float)):
                    return int(entry[outer][k])
    return None

def extract_model(entry: Dict[str, Any]) -> Optional[str]:
    for k in ("model", "modelID", "model_id"):
        if k in entry and isinstance(entry[k], str) and entry[k].strip():
            # if providerID also present, compose
            prov = entry.get("providerID", entry.get("provider_id", ""))
            if prov and isinstance(prov, str) and prov.strip() and "/" not in entry[k]:
                return f"{prov}/{entry[k]}"
            return entry[k]
    if "model_name_or_path" in entry and isinstance(entry["model_name_or_path"], str):
        return entry["model_name_or_path"]
    if isinstance(entry.get("model_name_or_path"), str):
        return entry["model_name_or_path"]
    # provider/model composition from separate fields
    pid = entry.get("providerID") or entry.get("provider_id")
    mid = entry.get("modelID") or entry.get("model_id")
    if isinstance(pid, str) and isinstance(mid, str) and pid and mid:
        return f"{pid}/{mid}"
    return None

def extract_cache_hit(entry: Dict[str, Any]) -> Optional[float]:
    for k in ("cache_hit_rate", "cacheHitRate", "cache_hit"):
        if k in entry and isinstance(entry[k], (int, float)):
            v = float(entry[k])
            return v if v <= 1 else v / 100.0
    # derive from tokens if available
    inp, _out, cr, cw = extract_tokens(entry)
    if cr is not None and inp is not None:
        denom = (cr or 0) + inp + (cw or 0)
        # more accurate: cr / (cr + cw + input) if cw present
        if denom > 0:
            # if cw present, include; else just cr/(cr+inp)
            total = cr + (cw or 0) + inp
            return cr / total if total > 0 else None
    return None

def extract_harness(entry: Dict[str, Any]) -> Optional[str]:
    for k in ("harness", "harness_name", "harness_type"):
        if k in entry and isinstance(entry[k], str) and entry[k].strip():
            v = entry[k].strip().lower()
            if v in ("opencode", "claude", "both"):
                return v
            return v
    # check meta nested
    for outer in ("meta", "info", "attributes"):
        if outer in entry and isinstance(entry[outer], dict):
            h = entry[outer].get("harness") or entry[outer].get("harness_name")
            if isinstance(h, str) and h.strip():
                return h.strip().lower()
    # heuristics: model name contains claude hint? not reliable
    return None

def wilson_ci(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    lo = (center - margin) / denom
    hi = (center + margin) / denom
    return (max(0.0, lo), min(1.0, hi))

# ---------------------------------------------------------------------------

def summarize_experiment(
    exp_id: str,
    rows: List[Dict[str, Any]],
    report: Optional[Dict[str, Any]],
    meta: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    n = len(rows)

    # --- resolved: prefer report if present (official swebench grading), else rows ---
    resolved_ids: Optional[set] = None
    report_resolved = None
    report_unresolved = None
    if report is not None:
        # swebench report.json shapes:
        # { "resolved": 42, "unresolved": 258, "resolved_ids": [...], "results": {id: {resolved: bool}} }
        # or { "report": { ... } } or flat
        # Try multiple placements
        candidates = [report]
        # sometimes nested under "report" or "results"
        for key in ("report", "results", "evaluation"):
            if isinstance(report.get(key), dict):
                candidates.append(report[key])
        # Look for resolved_ids / resolved count
        for c in candidates:
            if isinstance(c.get("resolved_ids"), list):
                resolved_ids = set(c["resolved_ids"])
                report_resolved = len(resolved_ids)
                # try to infer total from report if available
                if "total" in c and isinstance(c["total"], int):
                    n_report = c["total"]
                    # use report total if rows total mismatches? keep rows n but note
                    pass
                break
            if isinstance(c.get("resolved"), int) and isinstance(c.get("total"), int):
                report_resolved = c["resolved"]
                report_unresolved = c.get("unresolved")
                break
            if isinstance(c.get("resolved"), list):
                resolved_ids = set(c["resolved"])
                report_resolved = len(resolved_ids)
                break
            # dict of instance_id -> {resolved: bool}
            if any(isinstance(v, dict) and "resolved" in v for v in c.values()):
                # treat c as instance->result
                resolved_ids = {k for k, v in c.items() if isinstance(v, dict) and v.get("resolved")}
                report_resolved = len(resolved_ids)
                # count total as number of instance entries
                n_report = len([k for k in c.keys() if "__" in k or "-" in k])
                if n_report > 0:
                    n = max(n, n_report)  # prefer report n if larger
                break

    # Fall back to per-row resolved field if report didn't give us counts
    per_row_resolved: List[Optional[bool]] = [extract_resolved(r) for r in rows]
    if resolved_ids is not None:
        # Use report's resolved_ids to label rows
        k_resolved = sum(1 for r in rows if r.get("instance_id") in resolved_ids)
        # If row instance_ids missing, fall back to report count
        if k_resolved == 0 and report_resolved is not None:
            k_resolved = report_resolved
    elif report_resolved is not None and report_unresolved is not None:
        k_resolved = report_resolved
        # n already set from report or rows
        if isinstance(report.get("total"), int):
            n = report["total"]
        elif isinstance(report.get("report", {}).get("total"), int):
            n = report["report"]["total"]
        else:
            n = report_resolved + report_unresolved
    else:
        # count from rows
        if any(v is not None for v in per_row_resolved):
            k_resolved = sum(1 for v in per_row_resolved if v)
            # treat None as unresolved / not graded
            # n stays as len(rows)
        else:
            # no resolved signal at all — unknown
            k_resolved = 0
            # mark as unknown by setting resolved_rate None later if n==0 or no signal
            # we keep k=0 but will surface warning
            pass

    # If we still have no resolved signal and rows lack it, k_resolved stays 0
    # but we record has_resolved_signal flag
    has_resolved_signal = (resolved_ids is not None) or (report_resolved is not None) or any(v is not None for v in per_row_resolved)

    resolved_rate = (k_resolved / n) if n > 0 else 0.0
    lo, hi = wilson_ci(k_resolved, n) if n > 0 else (0.0, 0.0)

    # --- cost ---
    costs: List[float] = []
    for r in rows:
        c = extract_cost(r)
        if c is not None:
            costs.append(c)
    # also try report-level cost aggregates
    report_total_cost: Optional[float] = None
    if report is not None:
        for key in ("total_cost", "totalCost", "cost"):
            if key in report and isinstance(report[key], (int, float)):
                report_total_cost = float(report[key])
                break
        if report_total_cost is None and isinstance(report.get("report"), dict):
            for key in ("total_cost", "cost"):
                if key in report["report"] and isinstance(report["report"][key], (int, float)):
                    report_total_cost = float(report["report"][key])
                    break

    total_cost: Optional[float] = None
    avg_cost: Optional[float] = None
    if costs:
        total_cost = sum(costs)
        avg_cost = total_cost / len(costs) if costs else None
    elif report_total_cost is not None:
        total_cost = report_total_cost
        avg_cost = total_cost / n if n else None
    # avg_cost may remain None if no cost data

    cost_per_resolved: Optional[float] = None
    if total_cost is not None and k_resolved and k_resolved > 0:
        cost_per_resolved = total_cost / k_resolved
    elif avg_cost is not None and resolved_rate and resolved_rate > 0:
        # avg_cost is per-task; cost_per_resolved = avg_cost / resolved_rate
        cost_per_resolved = avg_cost / resolved_rate if resolved_rate else None

    # --- turns / steps ---
    turns_vals: List[int] = [t for t in (extract_turns(r) for r in rows) if t is not None]
    turn_stats: Dict[str, Any] = {}
    if turns_vals:
        turn_stats = {
            "mean": round(mean(turns_vals), 1),
            "median": median(turns_vals),
            "min": min(turns_vals),
            "max": max(turns_vals),
            "n": len(turns_vals),
        }
        # p90 if enough samples
        if len(turns_vals) >= 10:
            s = sorted(turns_vals)
            turn_stats["p90"] = s[int(0.9 * len(s))]
    else:
        turn_stats = {"mean": None, "median": None, "min": None, "max": None, "n": 0}

    # --- cache hit ---
    cache_hits: List[float] = [h for h in (extract_cache_hit(r) for r in rows) if h is not None]
    cache_hit_rate: Optional[float] = None
    if cache_hits:
        cache_hit_rate = round(mean(cache_hits), 3)
    else:
        # try aggregate from report
        if report is not None:
            for k in ("cache_hit_rate", "cacheHitRate"):
                if k in report and isinstance(report[k], (int, float)):
                    v = float(report[k])
                    cache_hit_rate = v if v <= 1 else v / 100
                    break
    # also compute derived from tokens totals if individual hits missing but tokens present
    if cache_hit_rate is None:
        # aggregate tokens across rows
        total_in = total_cr = total_cw = 0
        has_any = False
        for r in rows:
            inp, _out, cr, cw = extract_tokens(r)
            if inp is not None or cr is not None:
                has_any = True
                total_in += inp or 0
                total_cr += cr or 0
                total_cw += cw or 0
        if has_any and (total_cr + total_in + total_cw) > 0:
            cache_hit_rate = round(total_cr / (total_cr + total_in + total_cw), 3)

    # --- model distribution ---
    models: List[str] = [m for m in (extract_model(r) for r in rows) if m]
    model_counts = Counter(models)
    model_dist = [{"model": m, "count": c, "share": round(c / len(models), 3) if models else 0} for m, c in model_counts.most_common()]
    # also consider report-level model distribution (e.g., usedFallback attribution)
    # if per-row models missing, try report
    if not model_dist and report is not None:
        # look for byModel or model_counts in report
        for key in ("byModel", "model_distribution", "modelDistribution"):
            if key in report and isinstance(report[key], (list, dict)):
                # normalize
                if isinstance(report[key], dict):
                    model_dist = [{"model": k, "count": v, "share": 0} for k, v in report[key].items()]
                else:
                    model_dist = report[key]  # assume already shaped
                break

    # --- harness distribution ---
    harnesses: List[str] = [h for h in (extract_harness(r) for r in rows) if h]
    # also consider meta harness if rows lack it
    if not harnesses and meta is not None and isinstance(meta.get("harness"), str):
        harnesses = [meta["harness"].lower()]
    harness_counts = Counter(harnesses)
    harness_dist = [{"harness": h, "count": c, "share": round(c / len(harnesses), 3) if harnesses else 0} for h, c in harness_counts.most_common()]
    primary_harness = harness_counts.most_common(1)[0][0] if harness_counts else (meta.get("harness").lower() if meta and isinstance(meta.get("harness"), str) else None)
    # also try to infer from exp_id suffix like "-claude" / "-opencode"
    if primary_harness is None:
        if exp_id.endswith("-claude"):
            primary_harness = "claude"
        elif exp_id.endswith("-opencode"):
            primary_harness = "opencode"
    # dataset / meta enrichment
    dataset = None
    if meta is not None:
        dataset = meta.get("dataset") or meta.get("hf_dataset")

    return {
        "id": exp_id,
        "n": n,
        "k_resolved": k_resolved,
        "resolved_rate": round(resolved_rate, 4) if n else 0.0,
        "resolved_pct": round(resolved_rate * 100, 1) if n else 0.0,
        "wilson_lo": round(lo * 100, 1) if n else 0.0,
        "wilson_hi": round(hi * 100, 1) if n else 0.0,
        "has_resolved_signal": has_resolved_signal,
        "total_cost": round(total_cost, 4) if total_cost is not None else None,
        "avg_cost": round(avg_cost, 4) if avg_cost is not None else None,
        "cost_per_resolved": round(cost_per_resolved, 4) if cost_per_resolved is not None else None,
        "turns": turn_stats,
        "cache_hit_rate": cache_hit_rate,
        "model_distribution": model_dist,
        "models_used": len(model_dist),
        "is_mixture": len(model_dist) > 1,
        "harness": primary_harness,
        "harness_distribution": harness_dist,
        "harnesses_used": len(harness_dist),
        "is_cross_harness": len(harness_dist) > 1,
        "meta": meta,
        "report_path": str(report)[:0] if False else None,
    }

# ---------------------------------------------------------------------------
# Cross-experiment analysis
# ---------------------------------------------------------------------------

def cross_analysis(per_exp: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    ids = list(per_exp.keys())
    out: Dict[str, Any] = {}

    # --- darwin deltas: pair vanilla vs darwin sharing prefix ---
    # e.g. A-vanilla ↔ A-darwin, B-vanilla ↔ B-darwin, ceiling-sonnet ↔ ceiling-sonnet-darwin
    deltas: List[Dict[str, Any]] = []
    # Build prefix map: strip trailing -vanilla / -darwin / -baseline
    def base_prefix(exp_id: str) -> str:
        for suffix in ("-vanilla", "-darwin", "-baseline"):
            if exp_id.endswith(suffix):
                return exp_id[: -len(suffix)]
        # also handle "ceiling-sonnet" vs "ceiling-sonnet-darwin": base is "ceiling-sonnet"
        if exp_id.endswith("-darwin"):
            return exp_id[: -len("-darwin")]
        return exp_id

    # Group by base
    groups: Dict[str, List[str]] = defaultdict(list)
    for eid in ids:
        # For cebaseline: "ceiling-sonnet" and "ceiling-sonnet-darwin" share base "ceiling-sonnet"
        # For A/B: "A-vanilla" and "A-darwin" share base "A"
        # Use logic: if "-vanilla" or "-darwin", strip that; else keep full
        if eid.endswith("-vanilla"):
            base = eid[: -len("-vanilla")]
        elif eid.endswith("-darwin"):
            base = eid[: -len("-darwin")]
        else:
            base = eid
        groups[base].append(eid)

    for base, members in groups.items():
        # look for pair vanilla/darwin or darwin off/on
        vanilla = next((m for m in members if m.endswith("-vanilla")), None)
        darwin = next((m for m in members if m.endswith("-darwin")), None)
        # fallback: if base is "ceiling-sonnet", members are ["ceiling-sonnet", "ceiling-sonnet-darwin"]
        if vanilla is None and darwin is not None:
            # try non-suffix vanilla: group base == darwin stripped, so vanilla is base itself if present
            if base in members and base != darwin:
                vanilla = base
        if vanilla and darwin and vanilla in per_exp and darwin in per_exp:
            v = per_exp[vanilla]
            d = per_exp[darwin]
            delta_pct = round(d["resolved_pct"] - v["resolved_pct"], 1)
            delta_k = d["k_resolved"] - v["k_resolved"]
            # cost delta
            cost_delta = None
            if v["avg_cost"] is not None and d["avg_cost"] is not None:
                cost_delta = round(d["avg_cost"] - v["avg_cost"], 4)
            cpr_delta = None
            if v["cost_per_resolved"] is not None and d["cost_per_resolved"] is not None:
                cpr_delta = round(d["cost_per_resolved"] - v["cost_per_resolved"], 4)
            deltas.append({
                "pair": f"{vanilla} → {darwin}",
                "base": base,
                "vanilla": {"id": vanilla, "pct": v["resolved_pct"], "k": v["k_resolved"], "n": v["n"]},
                "darwin": {"id": darwin, "pct": d["resolved_pct"], "k": d["k_resolved"], "n": d["n"]},
                "delta_pct": delta_pct,
                "delta_k": delta_k,
                "delta_avg_cost": cost_delta,
                "delta_cost_per_resolved": cpr_delta,
                "darwin_better": delta_pct > 0,
            })

    deltas.sort(key=lambda x: x["delta_pct"], reverse=True)
    out["darwin_deltas"] = deltas

    # --- $0 vs $50: C-mixture vs C-mixture-budget50 ---
    budget_cmp: Optional[Dict[str, Any]] = None
    if "C-mixture" in per_exp and "C-mixture-budget50" in per_exp:
        a = per_exp["C-mixture"]
        b = per_exp["C-mixture-budget50"]
        budget_cmp = {
            "free_only": {"id": "C-mixture", "pct": a["resolved_pct"], "k": a["k_resolved"], "n": a["n"], "total_cost": a["total_cost"], "cpr": a["cost_per_resolved"]},
            "with_budget": {"id": "C-mixture-budget50", "pct": b["resolved_pct"], "k": b["k_resolved"], "n": b["n"], "total_cost": b["total_cost"], "cpr": b["cost_per_resolved"]},
            "delta_pct": round(b["resolved_pct"] - a["resolved_pct"], 1),
            "delta_k": b["k_resolved"] - a["k_resolved"],
            "extra_cost": round((b["total_cost"] or 0) - (a["total_cost"] or 0), 4) if a["total_cost"] is not None and b["total_cost"] is not None else None,
        }
    out["budget_50_vs_0"] = budget_cmp

    # --- mixture vs best single ---
    mixture_ids = [eid for eid in ids if eid.startswith("C-mixture")]
    # "best single" = max resolved among A-* and B-* vanilla/darwin single-pool shapes, excluding C*
    single_pool_ids = [eid for eid in ids if eid not in mixture_ids and (
        eid.startswith("A-") or eid.startswith("B-") or eid.startswith("ceiling-")
    )]
    # Among those, consider only single-model shapes for "best single free"
    free_single_ids = [eid for eid in single_pool_ids if eid.startswith("A-") or eid.startswith("B-")]
    mixture_vs_best: Optional[Dict[str, Any]] = None
    if mixture_ids and free_single_ids:
        best_single_id = max(free_single_ids, key=lambda eid: per_exp[eid]["resolved_pct"])
        best_single = per_exp[best_single_id]
        for mid in mixture_ids:
            m = per_exp[mid]
            mixture_vs_best = {
                "mixture": {"id": mid, "pct": m["resolved_pct"], "k": m["k_resolved"], "n": m["n"]},
                "best_single_free": {"id": best_single_id, "pct": best_single["resolved_pct"], "k": best_single["k_resolved"], "n": best_single["n"]},
                "delta_pct": round(m["resolved_pct"] - best_single["resolved_pct"], 1),
                "delta_k": m["k_resolved"] - best_single["k_resolved"],
                "note": "Mixture should match or exceed best single if economics routing can pick the winner per task; negative delta suggests routing overhead or insufficient Judge signal.",
            }
            # only report for first mixture (C-mixture) to avoid duplication; include budget50 separately if present
            break
    # also compare mixture vs ceiling (headroom)
    ceiling_ids = [eid for eid in ids if eid.startswith("ceiling-")]
    ceiling_cmp = None
    if mixture_ids and ceiling_ids:
        best_ceiling_id = max(ceiling_ids, key=lambda eid: per_exp[eid]["resolved_pct"])
        best_ceiling = per_exp[best_ceiling_id]
        m = per_exp[mixture_ids[0]]
        ceiling_cmp = {
            "mixture": {"id": mixture_ids[0], "pct": m["resolved_pct"]},
            "best_ceiling": {"id": best_ceiling_id, "pct": best_ceiling["resolved_pct"]},
            "gap_to_ceiling": round(best_ceiling["resolved_pct"] - m["resolved_pct"], 1),
        }
    out["mixture_vs_best_single"] = mixture_vs_best
    out["mixture_vs_ceiling"] = ceiling_cmp

    # --- ranking ---
    ranking = sorted(ids, key=lambda eid: per_exp[eid]["resolved_pct"], reverse=True)
    out["ranking"] = [{"rank": i+1, "id": eid, "pct": per_exp[eid]["resolved_pct"], "k": per_exp[eid]["k_resolved"], "n": per_exp[eid]["n"]} for i, eid in enumerate(ranking)]

    # --- cost efficiency ranking (cost per resolved, lower is better) ---
    with_cpr = [(eid, per_exp[eid]["cost_per_resolved"]) for eid in ids if per_exp[eid]["cost_per_resolved"] is not None]
    with_cpr.sort(key=lambda x: x[1])
    out["cost_efficiency_ranking"] = [{"id": eid, "cost_per_resolved": cpr} for eid, cpr in with_cpr]

    # --- harness comparison (opencode vs claude) ---
    # Group experiments that share the same base id but differ by harness suffix, or have different harness fields but same core id
    def strip_harness_suffix(eid: str) -> str:
        for suf in ("-opencode", "-claude"):
            if eid.endswith(suf):
                return eid[: -len(suf)]
        return eid

    harness_groups: Dict[str, List[str]] = defaultdict(list)
    for eid in ids:
        base = strip_harness_suffix(eid)
        # also consider harness field inside per_exp: if exp id doesn't have suffix but has harness field, still group by base
        # To handle "A-vanilla" run with both harnesses, we'd have A-vanilla-opencode and A-vanilla-claude -> base A-vanilla
        harness_groups[base].append(eid)

    # Also handle case where same exp_id was run with mixed harness rows inside single file (is_cross_harness)
    # For those, we need to split stats per harness—not possible from aggregated per_exp; we note it
    harness_deltas: List[Dict[str, Any]] = []
    for base, members in harness_groups.items():
        if len(members) < 2:
            continue
        # Find opencode and claude variants
        opencode_ids = [m for m in members if per_exp[m].get("harness") == "opencode" or m.endswith("-opencode")]
        claude_ids = [m for m in members if per_exp[m].get("harness") == "claude" or m.endswith("-claude")]
        # Fallback: if harness not set, use suffix heuristics
        if not opencode_ids:
            opencode_ids = [m for m in members if "opencode" in m]
        if not claude_ids:
            claude_ids = [m for m in members if "claude" in m]
        # If still ambiguous, treat first as opencode, second as claude
        if not opencode_ids and not claude_ids and len(members) == 2:
            opencode_ids = [members[0]]
            claude_ids = [members[1]]
        for oid in opencode_ids:
            for cid in claude_ids:
                if oid not in per_exp or cid not in per_exp:
                    continue
                o = per_exp[oid]
                c = per_exp[cid]
                delta_pct = round(c["resolved_pct"] - o["resolved_pct"], 1)
                delta_k = c["k_resolved"] - o["k_resolved"]
                cost_delta = None
                if o["avg_cost"] is not None and c["avg_cost"] is not None:
                    cost_delta = round(c["avg_cost"] - o["avg_cost"], 4)
                harness_deltas.append({
                    "pair": f"{oid} (opencode) ↔ {cid} (claude)",
                    "base": base,
                    "opencode": {"id": oid, "pct": o["resolved_pct"], "k": o["k_resolved"], "n": o["n"]},
                    "claude": {"id": cid, "pct": c["resolved_pct"], "k": c["k_resolved"], "n": c["n"]},
                    "delta_pct": delta_pct,
                    "delta_k": delta_k,
                    "delta_avg_cost": cost_delta,
                    "claude_better": delta_pct > 0,
                })
    # Also detect cross-harness within single experiment (is_cross_harness)
    cross_within = [eid for eid in ids if per_exp[eid].get("is_cross_harness")]
    if cross_within:
        for eid in cross_within:
            s = per_exp[eid]
            harness_deltas.append({
                "pair": f"{eid} (mixed harnesses within)",
                "base": eid,
                "note": f"single file contains multiple harnesses: {s.get('harness_distribution')}; split predictions by harness for clean delta",
                "delta_pct": None,
            })
    harness_deltas.sort(key=lambda x: x.get("delta_pct") or 0, reverse=True)
    out["harness_deltas"] = harness_deltas
    # harness summary
    harness_summary = Counter()
    for eid in ids:
        h = per_exp[eid].get("harness")
        if h:
            harness_summary[h] += 1
    out["harness_summary"] = dict(harness_summary)

    return out

# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def fmt_cost(v: Optional[float]) -> str:
    if v is None: return "—"
    if v == 0: return "$0.00"
    if abs(v) < 0.01: return f"${v:.4f}"
    return f"${v:.2f}"

def fmt_pct(v: float, lo: float, hi: float) -> str:
    return f"{v:.1f}% [{lo:.1f}–{hi:.1f}]"

def render_markdown(per_exp: Dict[str, Dict[str, Any]], cross: Dict[str, Any], id_order: List[str]) -> str:
    lines: List[str] = []
    lines.append("# Darwin experiment comparison")
    lines.append("")
    # If any experiment lacks resolved signal, warn
    if any(not per_exp[eid].get("has_resolved_signal") for eid in id_order):
        lines.append("> ⚠ Some predictions had no `resolved` field and no sibling `report.json` was found — resolved % is 0 until grading runs (`swebench eval`). Place `report.json` next to each `predictions.jsonl` or add `resolved` to each record.")
        lines.append("")

    lines.append("## Per-experiment")
    lines.append("")
    lines.append("| Experiment | Harness | n | Resolved | % Resolved (95% Wilson) | Avg cost | Cost / resolved | Turns / task (median) | Cache hit | Models |")
    lines.append("|---|:---:|---:|---|---:|---:|---|---:|---|---|")
    for eid in id_order:
        s = per_exp[eid]
        n = s["n"]
        k = s["k_resolved"]
        pct = s["resolved_pct"]
        lo = s["wilson_lo"]
        hi = s["wilson_hi"]
        resolved_cell = f"{k}/{n}" if s["has_resolved_signal"] else f"{k}/{n} *"
        pct_cell = fmt_pct(pct, lo, hi)
        avg = fmt_cost(s["avg_cost"])
        cpr = fmt_cost(s["cost_per_resolved"])
        turns = s["turns"]
        if turns.get("n", 0) and turns.get("median") is not None:
            turns_cell = f"{turns['mean']} (med {turns['median']})"
        else:
            turns_cell = "—"
        ch = f"{s['cache_hit_rate']*100:.1f}%" if s["cache_hit_rate"] is not None else "—"
        md = s["model_distribution"]
        if not md:
            model_cell = "—"
        elif len(md) == 1:
            model_cell = md[0]["model"].split("/")[-1][:22]
        else:
            tops = md[:2]
            model_cell = ", ".join(f"{m['model'].split('/')[-1][:18]} {m['share']*100:.0f}%" for m in tops)
            if len(md) > 2:
                model_cell += f" +{len(md)-2}"
        h = s.get("harness") or "—"
        # normalize harness display
        if h not in ("opencode", "claude", "—"):
            h = h[:10]
        lines.append(f"| {eid} | {h} | {n} | {resolved_cell} | {pct_cell} | {avg} | {cpr} | {turns_cell} | {ch} | {model_cell} |")

    lines.append("")
    if any(not per_exp[eid].get("has_resolved_signal") for eid in id_order):
        lines.append("* `*` — no resolved signal; run `swebench eval` or enrich predictions with `resolved` to populate this column.")
        lines.append("")

    # Model distribution detail for mixtures
    mixture_exp = [eid for eid in id_order if per_exp[eid].get("is_mixture") or eid.startswith("C-mixture")]
    if mixture_exp:
        lines.append("### Model distribution (mixture shapes)")
        lines.append("")
        for eid in mixture_exp:
            md = per_exp[eid]["model_distribution"]
            if not md:
                lines.append(f"- **{eid}**: no model attribution in predictions (add `model` / `providerID` per record or ensure `usedFallback` propagation).")
            else:
                parts = ", ".join(f"`{m['model']}`: {m['count']} ({m['share']*100:.0f}%)" for m in md)
                lines.append(f"- **{eid}**: {parts}")
        lines.append("")

    lines.append("## Cross-experiment")
    lines.append("")

    # Darwin deltas
    deltas = cross.get("darwin_deltas") or []
    if deltas:
        lines.append("### Darwin delta per model (paired, same tasks & model)")
        lines.append("")
        lines.append("| Pair | Vanilla | Darwin | Δ resolved | Δ avg cost | Δ cost/resolved | Verdict |")
        lines.append("|---|---|---|---:|---:|---:|---|")
        for d in deltas:
            v = d["vanilla"]; dw = d["darwin"]
            dp = d["delta_pct"]
            arrow = "↑" if dp > 0 else ("↓" if dp < 0 else "→")
            verdict = "darwin helps" if dp > 2 else ("darwin hurts" if dp < -2 else "flat / noisy")
            # Consider Wilson overlap: if |Δ| < 3pp on n=50, likely noise — note
            dac = fmt_cost(d["delta_avg_cost"]) if d["delta_avg_cost"] is not None else "—"
            dcpr = fmt_cost(d["delta_cost_per_resolved"]) if d["delta_cost_per_resolved"] is not None else "—"
            lines.append(f"| {d['pair']} | {v['pct']:.1f}% ({v['k']}/{v['n']}) | {dw['pct']:.1f}% ({dw['k']}/{dw['n']}) | {dp:+.1f}pp {arrow} | {dac} | {dcpr} | {verdict} |")
        lines.append("")
        # Summary sentence
        helps = [d for d in deltas if d["delta_pct"] > 0]
        lines.append(f"Summary: darwin improved {len(helps)}/{len(deltas)} pairs. Long-horizon signal should concentrate on >200-step or multi-file slices (see EVALUATION.md §7.2) — stratify by patch size / steps before concluding.")
        lines.append("")
    else:
        lines.append("_No vanilla↔darwin pairs detected (expected pairs like `A-vanilla`↔`A-darwin`). Check that both sides were run._")
        lines.append("")

    # Harness deltas (opencode vs claude)
    hd = cross.get("harness_deltas") or []
    hs = cross.get("harness_summary") or {}
    if hs:
        lines.append("### Harness summary")
        lines.append("")
        for hk, cnt in sorted(hs.items()):
            lines.append(f"- **{hk}**: {cnt} experiment(s)")
        lines.append("")
    if hd:
        lines.append("### Harness delta (opencode vs claude, same tasks & model)")
        lines.append("")
        lines.append("| Pair | Opencode | Claude | Δ resolved | Δ avg cost | Verdict |")
        lines.append("|---|---|---|---:|---:|---|")
        for h in hd:
            if h.get("delta_pct") is None:
                lines.append(f"| {h['pair']} | — | — | — | — | {h.get('note','mixed')} |")
                continue
            o = h["opencode"]; c = h["claude"]
            dp = h["delta_pct"]
            arrow = "↑" if dp > 0 else ("↓" if dp < 0 else "→")
            verdict = "claude helps" if dp > 2 else ("opencode helps" if dp < -2 else "flat / noisy")
            dac = fmt_cost(h["delta_avg_cost"]) if h["delta_avg_cost"] is not None else "—"
            lines.append(f"| {h['pair']} | {o['pct']:.1f}% ({o['k']}/{o['n']}) | {c['pct']:.1f}% ({c['k']}/{c['n']}) | {dp:+.1f}pp {arrow} | {dac} | {verdict} |")
        lines.append("")
        helps = [d for d in hd if d.get("delta_pct") and d["delta_pct"] > 0]
        if hd:
            lines.append(f"Summary: claude improved {len(helps)}/{len([d for d in hd if d.get('delta_pct') is not None])} harness pairs. Compare harness overhead vs model capability; isolate plugin effect with --harness both on same model.")
            lines.append("")
    else:
        # Only show hint if we have both harnesses present but no pairs
        has_both = len(hs) > 1 or any("claude" in eid or "opencode" in eid for eid in id_order)
        if has_both or len([e for e in id_order if per_exp[e].get('harness')]) > 1:
            lines.append("_No opencode↔claude pairs detected (expected suffixed ids like `A-vanilla-opencode` ↔ `A-vanilla-claude` or predictions with `harness` field)._")
            lines.append("")

    # Budget $0 vs $50
    bc = cross.get("budget_50_vs_0")
    if bc:
        lines.append("### $0 vs $50 spend (C-mixture vs C-mixture-budget50)")
        lines.append("")
        a = bc["free_only"]; b = bc["with_budget"]
        lines.append(f"- **C-mixture** (free only): {a['pct']:.1f}% ({a['k']}/{a['n']})  total {fmt_cost(a['total_cost'])}  cost/resolved {fmt_cost(a['cpr'])}")
        lines.append(f"- **C-mixture-budget50** (+ DeepSeek Flash): {b['pct']:.1f}% ({b['k']}/{b['n']})  total {fmt_cost(b['total_cost'])}  cost/resolved {fmt_cost(b['cpr'])}")
        lines.append(f"- **Δ**: {bc['delta_pct']:+.1f}pp ({bc['delta_k']:+d} tasks)  extra spend {fmt_cost(bc['extra_cost'])}")
        if bc["delta_pct"] > 0 and bc["extra_cost"] is not None and bc["extra_cost"] > 0:
            cpp = bc["extra_cost"] / max(1, bc["delta_k"]) if bc["delta_k"] else None
            if cpp is not None:
                lines.append(f"  → Cost per *additional* resolved: {fmt_cost(cpp)}")
        lines.append("")
        # Interpretation helper
        lines.append("> Interpretation: if Δ>0 at modest extra spend, the mixture escaped free-model capability traps via the paid fallback — compare against the “budget ceiling” (ceiling-deepseek) to see if free+routing approaches the paid-only frontier.")
        lines.append("")
    else:
        lines.append("_$0 vs $50 comparison requires both `C-mixture` and `C-mixture-budget50` shapes._")
        lines.append("")

    # Mixture vs best single
    mbs = cross.get("mixture_vs_best_single")
    if mbs:
        lines.append("### Mixture vs best single (economics routing)")
        lines.append("")
        lines.append(f"- **{mbs['mixture']['id']}**: {mbs['mixture']['pct']:.1f}%")
        lines.append(f"- **Best single free** ({mbs['best_single_free']['id']}): {mbs['best_single_free']['pct']:.1f}%")
        lines.append(f"- **Δ**: {mbs['delta_pct']:+.1f}pp")
        if mbs["delta_pct"] < -1:
            lines.append("- Verdict: mixture underperformed the best single — routing may be adding overhead without picking the winner. Check per-task fallback attribution.")
        elif mbs["delta_pct"] > 1:
            lines.append("- Verdict: mixture beat the best single — routing found complementary strengths across the pool.")
        else:
            lines.append("- Verdict: mixture ≈ best single — pool is not complementary on this slice, or Judge signal is not discriminative.")
        lines.append("")
    # Ceiling gap
    cc = cross.get("mixture_vs_ceiling")
    if cc:
        lines.append(f"_Gap to ceiling ({cc['best_ceiling']['id']} {cc['best_ceiling']['pct']:.1f}%): {cc['gap_to_ceiling']:+.1f}pp._")
        lines.append("")

    # Ranking
    rank = cross.get("ranking") or []
    if rank:
        lines.append("### Ranking (by % resolved)")
        lines.append("")
        for r in rank:
            lines.append(f"{r['rank']}. **{r['id']}** — {r['pct']:.1f}% ({r['k']}/{r['n']})")
        lines.append("")

    # Cost efficiency ranking
    cer = cross.get("cost_efficiency_ranking") or []
    if cer:
        lines.append("### Cost efficiency (cost / resolved, lower is better)")
        lines.append("")
        for e in cer:
            lines.append(f"- **{e['id']}**: {fmt_cost(e['cost_per_resolved'])} per resolved")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"_Generated by `eval/scripts/compare.py` — darwin comparison; see `docs/EVALUATION.md` for Wilson CI and cost model._")
    return "\n".join(lines)

def build_json_summary(per_exp: Dict[str, Dict[str, Any]], cross: Dict[str, Any], id_order: List[str]) -> Dict[str, Any]:
    # Strip report_path placeholder, keep compact
    per = {}
    for eid in id_order:
        s = per_exp[eid]
        per[eid] = {k: v for k, v in s.items() if k not in ("report_path",)}
    return {
        "per_experiment": per,
        "cross": cross,
        "order": id_order,
        "generated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    }

# ---------------------------------------------------------------------------

def infer_id_for_file(p: Path, override: Optional[str] = None) -> str:
    if override:
        return override
    # run_matrix layout: eval/results/<id>/predictions.jsonl  → id is parent dir name
    # fallback: file stem without extension
    parent = p.parent.name
    if parent and parent not in (".", "results", "eval"):
        # heuristic: parent looks like an experiment id (contains - or starts with ceiling/C/A/B)
        if re.match(r'^[A-Za-z0-9][A-Za-z0-9\-_]*$', parent):
            return parent
    return p.stem

def main() -> None:
    ap = argparse.ArgumentParser(description="Compare darwin experiment predictions (see module docstring for field tolerance).")
    ap.add_argument("predictions", nargs="+", help="Prediction JSONL files (one per experiment).")
    ap.add_argument("--names", help="Comma-separated experiment ids overriding inferred ids (order must match predictions args).")
    ap.add_argument("--results-dir", help="Results root to locate sibling report.json / meta.json (default: parent of first predictions file's grandparent, or eval/results).")
    ap.add_argument("--reports", nargs="*", help="Explicit report.json paths (order must match predictions), overrides sibling lookup.")
    ap.add_argument("--out-md", dest="out_md", help="Write markdown to this path (default: stdout + <results-dir>/comparison.md if --results-dir given).")
    ap.add_argument("--out-json", dest="out_json", help="Write JSON summary to this path (default: stdout JSON to stderr if --out-md used, or <results-dir>/comparison.json).")
    ap.add_argument("--json", action="store_true", help="Also emit JSON summary to stdout (alongside markdown).")
    args = ap.parse_args()

    pred_paths = [Path(p) for p in args.predictions]
    for pp in pred_paths:
        if not pp.exists():
            print(f"error: predictions file not found: {pp}", file=sys.stderr)
            sys.exit(1)

    # Resolve ids
    if args.names:
        names = [n.strip() for n in args.names.split(",")]
        if len(names) != len(pred_paths):
            print("error: --names count must match predictions count", file=sys.stderr)
            sys.exit(1)
        ids = names
    else:
        ids = [infer_id_for_file(p) for p in pred_paths]

    # Resolve results dir
    results_dir: Optional[Path] = Path(args.results_dir) if args.results_dir else None
    if results_dir is None:
        # infer from first pred path: .../eval/results/<id>/predictions.jsonl → results is parents[2]
        try:
            # pred parent is exp dir, its parent is results
            results_dir = pred_paths[0].parent.parent
            if not results_dir.exists():
                results_dir = None
        except Exception:
            results_dir = None

    # Load each experiment
    per_exp: Dict[str, Dict[str, Any]] = {}
    id_order: List[str] = ids[:]  # preserve CLI order
    for eid, pp in zip(ids, pred_paths):
        rows = load_jsonl(pp)
        # sibling lookup: <exp_dir>/report.json and meta.json
        exp_dir = pp.parent
        report = None
        meta = None
        # explicit --reports override
        if args.reports:
            try:
                idx = pred_paths.index(pp)
                if idx < len(args.reports) and args.reports[idx]:
                    rp = Path(args.reports[idx])
                    report = load_report(rp)
            except Exception:
                pass
        # sibling report.json
        if report is None:
            for cand in [exp_dir / "report.json", exp_dir / "evaluation_report.json"]:
                if cand.exists():
                    report = load_report(cand)
                    break
        # also try results_dir/<id>/report.json
        if report is None and results_dir is not None:
            cand = results_dir / eid / "report.json"
            if cand.exists():
                report = load_report(cand)
        # meta
        for cand in [exp_dir / "meta.json", exp_dir / "meta.json"]:
            if cand.exists():
                meta = load_meta(cand)
                break
        if meta is None and results_dir is not None:
            cand = results_dir / eid / "meta.json"
            if cand.exists():
                meta = load_meta(cand)

        per_exp[eid] = summarize_experiment(eid, rows, report, meta)

    cross = cross_analysis(per_exp)
    md = render_markdown(per_exp, cross, id_order)
    js = build_json_summary(per_exp, cross, id_order)

    # Write outputs
    out_md = Path(args.out_md) if args.out_md else None
    out_json = Path(args.out_json) if args.out_json else None

    # Default file outputs when results_dir known and no explicit paths given but run_matrix expects them
    if out_md is None and results_dir is not None and (results_dir / "comparison.md").parent.exists():
        # Only auto-write if caller is run_matrix (it always passes --out-md); avoid surprise for ad-hoc calls
        pass

    if out_md:
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(md)
        print(f"wrote {out_md}", file=sys.stderr)
    if out_json:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(js, indent=2) + "\n")
        print(f"wrote {out_json}", file=sys.stderr)

    # Stdout behavior:
    # - always print markdown to stdout
    # - if --json, also print JSON after a separator (or if no --out-json and --json, JSON goes to stdout after markdown)
    sys.stdout.write(md)
    sys.stdout.write("\n")
    if args.json:
        sys.stdout.write("\n---\n")
        json.dump(js, sys.stdout, indent=2)
        sys.stdout.write("\n")
    elif out_json is None and out_md is None:
        # ad-hoc: also hint where JSON would go
        print("\n# JSON summary (also available via --out-json):", file=sys.stderr)
        print(json.dumps(js, indent=2), file=sys.stderr)

if __name__ == "__main__":
    main()
