#!/usr/bin/env bash
# Parallel smoke: all 4 cells (2 tasks × 2 conditions) simultaneously.
# Per-cell output files merged at the end; autopsy-friendly (keep worktrees,
# full stdout in predictions).
set -u
cd /home/allie/develop/claude-code-darwin
export OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=1
export DOCKER_HOST=unix:///run/user/1000/podman/podman.sock

MODEL="${MODEL:-llmgateway/deepseek-v4-flash}"
TIMEOUT="${TIMEOUT:-2400}"
OUT=eval/results/parallel-smoke
rm -rf "$OUT"; mkdir -p "$OUT"
LOG="$OUT/progress.log"; : > "$LOG"

INSTANCES=$(python3 -c "import json; d=json.load(open('eval/datasets/lite_50.json')); print(' '.join(r['instance_id'] for r in d[:2]))")

pids=()
for COND in vanilla darwin; do
  for IID in $INSTANCES; do
    EXTRA=()
    if [[ "$COND" == "darwin" ]]; then
      EXTRA+=(--darwin)
      export DARWIN_HOME="$PWD/$OUT/darwin-home"
    fi
    python3 eval/harness/run_task.py \
      --instance-id "$IID" --model "$MODEL" \
      --workdir eval/workdir --output "$OUT/pred_${COND}_${IID}.jsonl" \
      --dataset eval/datasets/lite_50.json --timeout "$TIMEOUT" \
      --keep-worktree "${EXTRA[@]}" >> "$LOG" 2>&1 &
    pids+=($!)
    echo "[$(date +%H:%M:%S)] launched $COND $IID (pid $!)" >> "$LOG"
  done
  unset DARWIN_HOME
done

echo "[$(date +%H:%M:%S)] waiting for ${#pids[@]} parallel cells…" >> "$LOG"
for p in "${pids[@]}"; do wait "$p"; echo "[$(date +%H:%M:%S)] pid $p exited" >> "$LOG"; done

# Merge per-condition
for COND in vanilla darwin; do
  : > "$OUT/predictions_${COND}.jsonl"
  for IID in $INSTANCES; do
    cat "$OUT/pred_${COND}_${IID}.jsonl" >> "$OUT/predictions_${COND}.jsonl" 2>/dev/null
  done
  python3 eval/harness/eval_patch.py --predictions "$OUT/predictions_${COND}.jsonl" \
    --dataset lite_50 --output "$OUT/report_${COND}.json" --workdir eval/workdir >> "$LOG" 2>&1
done
echo "PARALLEL SMOKE DONE $(date)" >> "$LOG"
