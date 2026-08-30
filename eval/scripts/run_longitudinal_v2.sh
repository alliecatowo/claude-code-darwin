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
  # v2: darwin first + vanilla in parallel (independent conditions)
  VAN_PID=""
  (
    OUT="$OUT_ROOT/round${ROUND}_vanilla"; mkdir -p "$OUT"
    export DARWIN_HOME="$OUT/.darwin-tmp"; rm -rf "$OUT/.darwin-tmp"; mkdir -p "$OUT/.darwin-tmp"
    PRED="$OUT/predictions.jsonl"; : > "$PRED"
    for IID in $INSTANCE_IDS; do
      python3 "$SCRIPT_DIR/../harness/run_task.py" \
        --instance-id "$IID" --model "$MODEL" --workdir "$SCRIPT_DIR/../workdir" \
        --output "$PRED" --dataset "$SCRIPT_DIR/../datasets/${DATASET}.json" \
        --timeout "$TIMEOUT" 2>&1 | tail -2
    done
    python3 "$SCRIPT_DIR/../harness/eval_patch.py" --predictions "$PRED" --dataset "$DATASET" \
      --output "$OUT/report.json" --workdir "$SCRIPT_DIR/../workdir" 2>&1 | tail -4
  ) &
  VAN_PID=$!
  for COND in darwin; do
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
  wait $VAN_PID
  # EVOLVE BETWEEN ROUNDS: dream consolidation on the persistent darwin home.
  # This is the treatment — darwin reflects on the round's trajectory and
  # consolidates memory before the next round sees the same tasks again.
  if [[ "$ROUND" -lt "$ROUNDS" ]]; then
    echo "--- dream consolidation before round $((ROUND+1)) ---"
    DREAM_DIR="$SCRIPT_DIR/../workdir/dream-round${ROUND}"
    mkdir -p "$DREAM_DIR/.opencode/plugin"
    cp "$(find "$SCRIPT_DIR/../workdir/worktrees" -name darwin.ts -path "*round*" 2>/dev/null | head -1)" \
       "$DREAM_DIR/.opencode/plugin/darwin.ts" 2>/dev/null || true
    # Fall back to regenerating the shim from run_task's helper
    if [[ ! -s "$DREAM_DIR/.opencode/plugin/darwin.ts" ]]; then
      SRC="$SCRIPT_DIR/../../packages/opencode/src/index.ts"
      [[ -f "$SRC" ]] && echo "import plugin from \"$SRC\"
export default { id: \"darwin\", server: plugin }" > "$DREAM_DIR/.opencode/plugin/darwin.ts"
    fi
    DARWIN_HOME="$LONG_HOME" timeout 600 opencode run --dir "$DREAM_DIR" \
      --model "$MODEL" "Consolidate your project memory now (dream): review what you learned from the tasks you just attempted, merge duplicates, promote repeated patterns and gotchas into durable memory, prune stale entries. Use the darwin_memory tool. Keep it under 60 lines total. Reply with a 3-line summary." \
      2>&1 | tail -5
    # BRIDGE: task worktrees get fresh project hashes (mkdtemp paths), so the
    # dream dir's project memory never reaches them. Promote the dream's
    # consolidated knowledge into GLOBAL memory, which every task digest injects.
    python3 - "$LONG_HOME" "$DREAM_DIR" <<'PY'
import sys, pathlib
home, dream_dir = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
# find the dream dir's project memory (the populated one)
best = None
for f in (home / "memory" / "projects").glob("*/MEMORY.md"):
    body = f.read_text().strip()
    if len(body) > 100 and (best is None or len(body) > len(best[1])):
        best = (f, body)
if best:
    glob_mem = home / "memory" / "global" / "MEMORY.md"
    glob_mem.parent.mkdir(parents=True, exist_ok=True)
    existing = glob_mem.read_text() if glob_mem.exists() else "# Global memory\n"
    # append only new content (crude dedupe by line)
    new_lines = [l for l in best[1].splitlines() if l.strip() and l not in existing]
    if new_lines:
        glob_mem.write_text(existing.rstrip() + "\n" + "\n".join(new_lines) + "\n")
        print(f"[bridge] promoted {len(new_lines)} lines to global memory")
    else:
        print("[bridge] nothing new to promote")
else:
    print("[bridge] no populated dream memory found")
PY
  fi
done

# Slope report
python3 - << 'PY'
import json, glob, os
root = os.path.join(os.path.dirname(os.path.abspath(glob.glob("/proc/self/fd/1")[0] if False else __file__)), "..") if False else "eval/results/longitudinal"
print("\n== longitudinal results ==")
print(f"{'round':>5} {'vanilla':>16} {'darwin':>16}")
for r in range(1, 10):
    cells = {}
    any_dir = False
    for cond in ("vanilla", "darwin"):
        rep = os.path.join(root, f"round{r}_{cond}", "report.json")
        cells[cond] = "?"
        if os.path.exists(rep):
            any_dir = True
            j = json.load(open(rep))
            cells[cond] = f"{j.get('resolved',0)}/{j.get('total','?')} ({100*j.get('resolved_rate',0):.0f}%)"
    if not any_dir:
        break
    print(f"{r:>5} {cells['vanilla']:>16} {cells['darwin']:>16}")
print("\nInterpretation: darwin column should TREND UP if self-learning helps;")
print("vanilla should stay flat. Flat darwin = no effect; declining = pollution.")
PY
