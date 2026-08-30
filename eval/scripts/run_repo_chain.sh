#!/usr/bin/env bash
# run_repo_chain.sh — THE longitudinal self-learning eval.
# One repo (django chain), sequential tasks against a FIXED worktree per arm
# (stable darwin project memory), dream every DREAM_EVERY tasks in the same
# worktree. Arms run in parallel (independent worktrees/homes).
#
# Signal: darwin's rolling resolved-rate / cost-per-task vs vanilla as the
# chain progresses. Real swebench grading at the end (gold-proven path).
#
# Usage: ./eval/scripts/run_repo_chain.sh [--tasks 30] [--model M] [--timeout 1500] [--smoke]
set -euo pipefail

TASKS=30
MODEL="llmgateway/deepseek-v4-flash"
TIMEOUT=1500
DATASET="django_chain"
SMOKE=0
DREAM_EVERY=5

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tasks) TASKS="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --dataset) DATASET="$2"; shift 2 ;;
    --dream-every) DREAM_EVERY="$2"; shift 2 ;;
    --smoke) SMOKE=1; TASKS=3; shift ;;
    *) echo "unknown arg $1"; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR/../.."
DATASET_FILE="$SCRIPT_DIR/../datasets/${DATASET}.json"
OUT_ROOT="$SCRIPT_DIR/../results/chain-$(date +%H%M%S)"
[[ $SMOKE == 1 ]] && OUT_ROOT="$SCRIPT_DIR/../results/chain-smoke"
mkdir -p "$OUT_ROOT"
echo "== repo chain: $TASKS tasks × 2 arms | model=$MODEL | dream every $DREAM_EVERY ==" | tee "$OUT_ROOT/run.log"

run_arm () {
  local COND="$1"
  local WT="$ROOT/eval/workdir/chain-$COND/django"
  local HOME_DIR="$ROOT/eval/workdir/chain-$COND/darwin-home"
  local PRED="$OUT_ROOT/predictions_${COND}.jsonl"
  : > "$PRED"
  mkdir -p "$(dirname "$WT")"
  export DARWIN_HOME="$HOME_DIR"
  if [[ "$COND" == "vanilla" ]]; then rm -rf "$HOME_DIR"; mkdir -p "$HOME_DIR"; fi
  local IDS=$(python3 -c "
import json; d=json.load(open('$DATASET_FILE')); print(' '.join(r['instance_id'] for r in d[:$TASKS]))")
  local i=0
  for IID in $IDS; do
    i=$((i+1))
    EXTRA=()
    [[ "$COND" == "darwin" ]] && EXTRA+=(--darwin)
    python3 "$SCRIPT_DIR/../harness/run_task.py" \
      --instance-id "$IID" --model "$MODEL" \
      --workdir "$ROOT/eval/workdir" --output "$PRED" \
      --dataset "$DATASET_FILE" --timeout "$TIMEOUT" \
      --worktree-path "$WT" "${EXTRA[@]}" 2>&1 | grep -E "^\[run_task\] (done|warn|reason|chain)" | tail -3
    # quick inline applies-grade of this task (patch present?)
    python3 - "$PRED" "$IID" << 'PY'
import json, sys
pred, iid = sys.argv[1], sys.argv[2]
last = None
for l in open(pred):
    d = json.loads(l)
    if d["instance_id"] == iid: last = d
if last:
    c = int(bool(last.get("patch")))
    cost = last.get("cost") or 0
    print(f"    [chain] {iid}: patch={'yes' if c else 'NO '} cost=${cost:.4f}", flush=True)
PY
    # darwin: dream in the SAME worktree (project memory = this repo)
    if [[ "$COND" == "darwin" && $((i % DREAM_EVERY)) == 0 && $i -lt $TASKS ]]; then
      echo "    [chain] dream #$((i/DREAM_EVERY)) after task $i" | tee -a "$OUT_ROOT/run.log"
      timeout 600 opencode run --dir "$WT" --model "$MODEL" \
        "Consolidate your project memory now (dream): review what you learned from the last tasks in this repo, merge duplicates, promote repeated patterns, file locations, conventions and gotchas into durable memory. Use the darwin_memory tool. Keep the whole MEMORY.md under 120 lines. Reply with a 3-line summary." \
        2>&1 | tail -3 | sed 's/^/    [dream] /'
    fi
  done
  unset DARWIN_HOME
}

# Arms in parallel (independent)
if [[ $SMOKE == 1 ]]; then
  run_arm darwin   # smoke: darwin only, sequential, fast
else
  run_arm darwin &
  DARWIN_PID=$!
  run_arm vanilla &
  VANILLA_PID=$!
  wait $DARWIN_PID $VANILLA_PID
fi

# Final: applies-grade each arm + slope table
for COND in darwin vanilla; do
  [[ -e "$OUT_ROOT/predictions_${COND}.jsonl" ]] || continue
  python3 "$SCRIPT_DIR/../harness/eval_patch.py" \
    --predictions "$OUT_ROOT/predictions_${COND}.jsonl" --dataset "$DATASET" \
    --output "$OUT_ROOT/report_${COND}.json" --workdir "$ROOT/eval/workdir" 2>&1 | tail -3
done

python3 - "$OUT_ROOT" << 'PY'
import json, os, sys
root = sys.argv[1]
print("\n== chain results: rolling patched-rate & cost by task index ==")
print(f"{'idx':>4} {'vanilla':>22} {'darwin':>22}")
series = {}
for cond in ("vanilla", "darwin"):
    f = os.path.join(root, f"predictions_{cond}.jsonl")
    if not os.path.exists(f): continue
    series[cond] = [(json.loads(l)["instance_id"], bool(json.loads(l).get("patch")), json.loads(l).get("cost") or 0, json.loads(l).get("duration_s") or 0) for l in open(f)]
n = max((len(v) for v in series.values()), default=0)
for i in range(n):
    row = f"{i+1:>4}"
    for cond in ("vanilla", "darwin"):
        s = series.get(cond, [])
        if i < len(s):
            iid, p, c, d = s[i]
            win = s[max(0,i-4):i+1]
            rate = sum(1 for x in win if x[1])/len(win)
            avgc = sum(x[2] for x in win)/len(win)
            row += f"  {('#' if p else '.')} {rate*100:>3.0f}% ${avgc:.3f}".rjust(22)
        else:
            row += f"{'—':>22}"
    print(row)
print("\n#/# = patched this task; % = rolling 5-task patched-rate; $ = rolling avg cost")
print("CLAIM HOLDS IF: darwin rolling-rate trends up / cost trends down vs vanilla flat.")
PY
echo "CHAIN DONE $(date)" >> "$OUT_ROOT/run.log"
