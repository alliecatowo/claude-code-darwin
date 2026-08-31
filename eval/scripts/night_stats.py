#!/usr/bin/env python3
"""Night-matrix stats: paired darwin-vs-vanilla per (repo, index, seed).
Pure python: Wilcoxon signed-rank (normal approx), bootstrap CI on mean delta,
McNemar exact (binomial) on resolved discordants. Reads swebench reports +
predictions; emits markdown + JSON."""
import json, glob, math, os, random, sys

ROOT = sys.argv[1] if len(sys.argv) > 1 else "eval/results/night-latest"

def load():
    cells = {}  # (repo_slug, seed, arm) -> list[(idx, instance_id, cost, tokens, dur, patch)]
    for arm in ("darwin", "vanilla"):
        for f in glob.glob(os.path.join(ROOT, f"predictions_{arm}_*.jsonl")):
            parts = os.path.basename(f)[len(f"predictions_{arm}_") : -len(".jsonl")].split("__seed")
            slug, seed = parts[0], parts[1] if len(parts) > 1 else "1"
            rows = [json.loads(l) for l in open(f)]
            rows.sort(key=lambda r: r.get("_chain_idx", 0))
            cells[(slug, seed, arm)] = [
                (i, r["instance_id"], r.get("cost") or 0, r.get("tokens") or 0,
                 r.get("duration_s") or 0, bool(r.get("patch")))
                for i, r in enumerate(rows)
            ]
    return cells

def wilcoxon(deltas):
    d = sorted((x for x in deltas if x != 0), key=abs)
    n = len(d)
    if n < 5:
        return None
    ranks = {}
    i = 0
    while i < n:
        j = i
        while j < n and abs(d[j]) == abs(d[i]):
            j += 1
        r = (i + 1 + j) / 2
        for k in range(i, j):
            ranks[d[k]] = r
        i = j
    wpos = sum(r for r in ranks.values() if r > 0)  # rank of positive deltas
    mu, sigma = n * (n + 1) / 4, math.sqrt(n * (n + 1) * (2 * n + 1) / 24)
    z = (wpos - mu) / sigma if sigma else 0
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return {"n": n, "W+": wpos, "z": round(z, 2), "p": round(p, 5)}

def boot_ci(deltas, iters=5000):
    random.seed(42)
    n = len(deltas)
    if n == 0:
        return None
    means = []
    for _ in range(iters):
        means.append(sum(random.choice(deltas) for _ in range(n)) / n)
    means.sort()
    lo, hi = means[int(0.025 * iters)], means[int(0.975 * iters)]
    return {"mean": round(sum(deltas) / n, 5), "ci95": [round(lo, 5), round(hi, 5)]}

def mcnemar(b, c):
    # b = darwin-only wins, c = vanilla-only wins (resolved discordants)
    n = b + c
    if n == 0:
        return {"b": 0, "c": 0, "p": 1.0}
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(0, k + 1)) / 2**n * 2
    return {"b": b, "c": c, "p": round(min(p, 1.0), 5)}

cells = load()
if not cells:
    print("no predictions found under", ROOT)
    sys.exit(1)

pairs = {}  # (slug, seed) -> {idx: (dar_row, van_row)}
for (slug, seed, arm), rows in cells.items():
    pairs.setdefault((slug, seed), {})[arm] = rows

metrics = {"cost": 2, "tokens": 3, "duration": 4}
out = {"per_repo": {}, "pooled": {}}
pooled = {"cost": [], "tokens": [], "duration": []}
print(f"# Night matrix: paired darwin vs vanilla ({len(pairs)} repo×seed cells)\n")
print(f"| repo×seed | n | cost Δ% | time Δ% | tok Δ% | Wilcoxon p (cost) |")
print(f"|---|---|---|---|---|---|")
for (slug, seed), arms in sorted(pairs.items()):
    d, v = arms.get("darwin"), arms.get("vanilla")
    if not d or not v:
        continue
    n = min(len(d), len(v))
    rc = {"cost": [], "tokens": [], "duration": []}
    for i in range(n):
        for m, k in metrics.items():
            if v[i][k]:
                rc[m].append((d[i][k] - v[i][k]) / v[i][k])
    out["per_repo"][f"{slug}__seed{seed}"] = {
        m: {"mean_delta_pct": round(100 * sum(x) / len(x), 1) if x else None,
            "boot": boot_ci(x)} for m, x in rc.items()
    }
    for m in metrics:
        pooled[m] += rc[m]
    w = wilcoxon(rc["cost"])
    cp = out["per_repo"][f"{slug}__seed{seed}"]
    print(f"| {slug} s{seed} | {n} | {cp['cost']['mean_delta_pct']}% | "
          f"{cp['duration']['mean_delta_pct']}% | {cp['tokens']['mean_delta_pct']}% | "
          f"{w['p'] if w else '—'} |")

out["pooled"] = {
    m: {"mean_delta_pct": round(100 * sum(x) / len(x), 1) if x else None,
        "wilcoxon": wilcoxon(x), "boot": boot_ci(x)}
    for m, x in pooled.items()
}
print(f"\n## Pooled (n={len(pooled['cost'])} paired tasks)")
for m in ("cost", "tokens", "duration"):
    p = out["pooled"][m]
    print(f"- {m}: {p['mean_delta_pct']}%  Wilcoxon p={p['wilcoxon']['p'] if p['wilcoxon'] else '—'}"
          f"  boot95={p['boot']['ci95'] if p['boot'] else '—'}")

# McNemar on real-grading reports if present
dr = set(); vr = set()
for f in glob.glob(os.path.join(ROOT, "swebench", "*darwin*.json")):
    j = json.load(open(f)); dr |= set(j.get("resolved_ids", []) or [])
for f in glob.glob(os.path.join(ROOT, "swebench", "*vanilla*.json")):
    j = json.load(open(f)); vr |= set(j.get("resolved_ids", []) or [])
if dr or vr:
    b = len([x for x in (dr - vr)])
    c = len([x for x in (vr - dr)])
    out["pooled"]["mcnemar_resolved"] = mcnemar(b, c)
    print(f"\n## Resolved (real grading): darwin {len(dr)} | vanilla {len(vr)} | "
          f"discordant d+{b}/v+{c} | McNemar p={out['pooled']['mcnemar_resolved']['p']}")

json.dump(out, open(os.path.join(ROOT, "stats.json"), "w"), indent=1)
print(f"\nwritten {ROOT}/stats.json")
