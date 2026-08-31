#!/usr/bin/env bash
# monitor_night.sh — real progress monitoring, not done-checking.
# Per-cell progress, spend, errors/timeouts, rate-limit hits, worker health.
ROOT_DIR=$(ls -dt eval/results/night-* 2>/dev/null | grep -v smoke | head -1)
[[ -z "$ROOT_DIR" ]] && echo "no night dir" && exit 1
echo "=== $(date +%H:%M) — $ROOT_DIR ==="

# 1. pair-job progress + workers
grep "pair-jobs done" /tmp/opencode/night_full2.log 2>/dev/null | tail -1
W=$(pgrep -fc "run_task.py" 2>/dev/null || echo 0)
echo "workers alive: $W (slots: 3)"

# 2. per-cell progress (arm lines / expected)
python3 - << 'PY'
import json, glob, os
root = sorted(glob.glob("eval/results/night-*"), key=os.path.getmtime)[-1]
cells = {}
for arm in ("darwin","vanilla"):
    for f in glob.glob(f"{root}/predictions_{arm}_*.jsonl"):
        base = os.path.basename(f).replace(f"predictions_{arm}_","")[:-6]
        try: rows = [json.loads(l) for l in open(f) if l.strip()]
        except: rows = []
        cells.setdefault(base, {})[arm] = rows
exp = {"django": 25, "sympy": 25, "matplotlib": 23, "scikit-learn": 23}
print(f"{'cell':<22} {'darwin':>10} {'vanilla':>10}  spend    status")
tot_d=tot_v=tot_c=0
for base in sorted(cells):
    slug = base.split("__")[0]
    d = cells[base].get("darwin", []); v = cells[base].get("vanilla", [])
    e = exp.get(slug, 25)
    cost = sum(x.get("cost") or 0 for x in d+v)
    tot_d+=len(d); tot_v+=len(v); tot_c+=cost
    st = "DONE" if len(d)>=e and len(v)>=e else ("darwin-arm" if len(d)>len(v) else "in-progress")
    # errors within
    errs = sum(1 for x in d+v if x.get("status") not in ("success","no_patch"))
    print(f"{base:<22} {len(d):>3}/{e:<6} {len(v):>3}/{e:<6}  ${cost:.3f}  {st}" + (f" ({errs} errs)" if errs else ""))
print(f"{'TOTAL':<22} {tot_d:>10} {tot_v:>10}  ${tot_c:.3f}")
print(f"overall: {tot_d+tot_v}/576 tasks, ${tot_c:.2f} spent")
PY

# 3. error scan in recent chain logs (last 15 min)
find "$ROOT_DIR" -name "chain_*.log" -newermt "15 minutes ago" 2>/dev/null | \
  xargs grep -l "timeout\|error\|429\|rate.limit" 2>/dev/null | head -3 | \
  while read f; do echo "recent issues in $(basename $f):"; grep -c "timeout" "$f" 2>/dev/null; done

# 4. resources
free -m | awk '/^Mem:/{printf "mem: %sMB avail  ", $7}'
df -h /tmp | tail -1 | awk '{printf "/tmp: %s free  ", $4}'
df -h /home | tail -1 | awk '{printf "/home: %s free\n", $4}'
