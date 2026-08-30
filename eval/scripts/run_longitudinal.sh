#!/usr/bin/env bash
# run_longitudinal.sh — does darwin's self-learning improve results over time?
#
# Design: N ROUNDS over the SAME K tasks, both conditions.
#   - darwin: one persistent DARWIN_HOME across rounds (memory/dream accumulate).
#             Between rounds, optionally run /dream consolidation.
#   - vanilla: identical runs, no plugin, no memory — round-over-round should be FLAT.
# Signal: darwin round-over-round slope vs vanilla flat line.
#   - darwin slope > 0  => self-learning helps
#   - darwin slope < 0  => memory pollution hurts
#   - darwin slope = 0  => does nothing (or tasks too easy/hard for memory to matter)
#
# Usage: ./eval/scripts/run_longitudinal.sh [--rounds 3] [--tasks 5] [--model M] [--timeout 900]
set -euo pipefail

ROUNDS=3
TASKS=5
MODEL="llmgateway/deepseek-v4-flash"
TIMEOUT=900
DATASET="lite_50"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rounds) ROUNDS="$2"; shift 2 ;;
    --tasks) TASKS="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --dataset) DATASET="$2"; shift 2 ;;
    *) echo "unknown arg $1"; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_ROOT="$SCRIPT_DIR/../results/longitudinal"
mkdir -p "$OUT_ROOT"
LONG_HOME="$SCRIPT_DIR/../workdir/darwin-longitudinal"

echo "== longitudinal: $ROUNDS rounds × $TASKS tasks × both conditions =="
echo "model: $MODEL   darwin home (persistent across rounds): $LONG_HOME"

for ROUND in $(seq 1 "$ROUNDS"); do
  for COND in vanilla darwin; do
    OUT="$OUT_ROOT/round${ROUND}_${COND}"
    mkdir -p "$OUT"
    echo "--- round $ROUND / $COND ---"
    if [[ "$COND" == "darwin" ]]; then
      # Persistent memory across rounds = the self-learning treatment
      export DARWIN_HOME="$LONG_HOME"
    else
      # Fresh empty home each round = no cross-round learning
      export DARWIN_HOME="$OUT/.darwin-tmp"
    fi
    rm -rf "$OUT/.darwin-tmp"; mkdir -p "$OUT/.darwin-tmp"
    python3 "$SCRIPT_DIR/../harness/run_task.py" >/dev/null 2>&1 || true
    # Run each task through run_task.py (single invocations keep conditions symmetric)
    INSTANCE_IDS=$(python3 -c "
import json
d = json.load(open('$SCRIPT_DIR/../datasets/${DATASET}.json'))
print(' '.join(r['instance_id'] for r in d[:$TASKS]))
")
    PRED="$OUT/predictions.jsonl"; : > "$PRED"
    for IID in $INSTANCE_IDS; do
      EXTRA=()
      [[ "$COND" == "darwin" ]] && EXTRA+=(--darwin)
      python3 "$SCRIPT_DIR/../harness/run_task.py" \
        --instance-id "$IID" --model "$MODEL" --workdir "$SCRIPT_DIR/../workdir" \
        --output "$PRED" --dataset "$SCRIPT_DIR/../datasets/${DATASET}.json" \
        --timeout "$TIMEOUT" "${EXTRA[@]}" 2>&1 | tail -2
    done
    # Grade this round (degraded applies-check; swebench when available)
    python3 "$SCRIPT_DIR/../harness/eval_patch.py" \
      --predictions "$PRED" --dataset "$DATASET" \
      --output "$OUT/report.json" --workdir "$SCRIPT_DIR/../workdir" 2>&1 | tail -4
    unset DARWIN_HOME
  done
done

# Slope report
python3 - << 'PY'
import json, glob, os
root = os.path.join(os.path.dirname(__file__), "..", "results", "longitudinal")
print("\n== longitudinal results ==")
print(f"{'round':>5} {'vanilla':>16} {'darwin':>16}")
for r in sorted(glob.glob(os.path.join(root, "round*"))):
    n = os.path.basename(r).replace("round", "")
    cells = {}
    for cond in ("vanilla", "darwin"):
        rep = os.path.join(r, f"{cond}", "report.json")
        cells[cond] = "?"
        if os.path.exists(rep):
            j = json.load(open(rep))
            cells[cond] = f"{j.get('resolved',0)}/{j.get('total','?')} ({100*j.get('resolved_rate',0):.0f}%)"
    print(f"{n:>5} {cells['vanilla']:>16} {cells['darwin']:>16}")
print("\nInterpretation: darwin column should TREND UP if self-learning helps;")
print("vanilla should stay flat. Flat darwin = no effect; declining = pollution.")
PY
