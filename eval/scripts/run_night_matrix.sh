#!/usr/bin/env bash
# run_night_matrix.sh — max-concurrency verified-read matrix.
#
# Topology: darwin chains are SEQUENTIAL per (repo, seed) — memory accumulates,
# that's the treatment — but (repo × seed) jobs run in PARALLEL up to J slots.
# Vanilla mirrors the same sequential shape per (repo, seed) for paired metrics
# (its order is irrelevant but keeps per-task duration comparable).
# Real swebench grading per arm batch at the end (containers in parallel).
#
# Usage: ./eval/scripts/run_night_matrix.sh [--repos N] [--seeds 3] [--jobs 8]
#          [--model M] [--timeout 1500] [--cap 25] [--smoke]
set -euo pipefail

REPOS=99; SEEDS=3; JOBS=8; MODEL="llmgateway/deepseek-v4-flash"; TIMEOUT=1500; CAP=25; SMOKE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repos) REPOS="$2"; shift 2 ;; --seeds) SEEDS="$2"; shift 2 ;;
    --jobs) JOBS="$2"; shift 2 ;; --model) MODEL="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;; --cap) CAP="$2"; shift 2 ;;
    --smoke) SMOKE=1; SEEDS=1; REPOS=2; CAP=3; TIMEOUT=600; shift ;;
    *) echo "? $1"; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR/../.."
CHAIN_DIR="$ROOT/eval/datasets/chains"
# Keep all temp/work on /home (not /tmp tmpfs) — avoids quota hammering
mkdir -p "$ROOT/eval/workdir/tmp"
export TMPDIR="$ROOT/eval/workdir/tmp"
OUT="$ROOT/eval/results/night-$(date +%m%d-%H%M)"
[[ $SMOKE == 1 ]] && OUT="$ROOT/eval/results/night-smoke"
mkdir -p "$OUT"
echo "== night matrix: repos≤$REPOS seeds=$SEEDS jobs=$JOBS model=$MODEL ==" | tee "$OUT/run.log"

python3 "$SCRIPT_DIR/make_repo_chains.py" 18 "$CAP" | tee -a "$OUT/run.log"

# Shared repo cache: clone each repo ONCE (staggered, memory-safe); arms
# --shared-clone from it (hardlinked objects ≈ free).
mkdir -p "$ROOT/eval/workdir/night/shared"
while IFS=$'\t' read -r SLUG SEED ARM DS; do :; done < /dev/null # noop for IFS scoping
for DS in $(ls "$CHAIN_DIR"/*.json | grep -v manifest); do
  SLUG=$(basename "$DS" .json)
  REPO=$(python3 -c "import json;print(json.load(open('$DS'))[0]['repo'])")
  if [[ ! -d "$ROOT/eval/workdir/night/shared/$SLUG/.git" ]]; then
    echo "cloning shared cache: $REPO" | tee -a "$OUT/run.log"
    git clone "https://github.com/$REPO" "$ROOT/eval/workdir/night/shared/$SLUG" 2>>"$OUT/run.log"
  fi
done

# job runner: one (repo, seed) job = darwin chain THEN vanilla chain (sequential
# within a job halves peak memory; repos×seeds run in parallel up to JOBS slots)
run_chain () {
  local SLUG="$1" SEED="$2" ARM="$3" DS="$4"
  local WT="$ROOT/eval/workdir/night/$SLUG-s$SEED-$ARM/repo"
  local HOME_DIR="$ROOT/eval/workdir/night/$SLUG-s$SEED-$ARM/darwin-home"
  local PRED="$OUT/predictions_${ARM}_${SLUG}__seed${SEED}.jsonl"
  : > "$PRED"
  mkdir -p "$(dirname "$WT")" "$HOME_DIR"
  export DARWIN_HOME="$HOME_DIR"
  export DARWIN_CHAIN_SOURCE="$ROOT/eval/workdir/night/shared/$SLUG"
  export WT_PATH="$WT"
  export IS_DARWIN=$([[ "$ARM" == "darwin" ]] && echo 1 || echo 0)
  [[ "$ARM" == "vanilla" ]] && rm -rf "$HOME_DIR" && mkdir -p "$HOME_DIR"
  local IDS=$(python3 -c "import json;print(' '.join(r['instance_id'] for r in json.load(open('$DS'))))")
  local i=0
  for IID in $IDS; do
    i=$((i+1))
    EXTRA=(); [[ "$ARM" == "darwin" ]] && EXTRA+=(--darwin)
    python3 - "$ROOT" "$SCRIPT_DIR" "$PRED" "$IID" "$i" << 'PY' >> "$OUT/chain_${SLUG}_s${SEED}_${ARM}.log" 2>&1
import json, subprocess, sys, os
root, sdir, pred, iid, idx = sys.argv[1:7]
ds = os.environ.get("DS_FILE")
cmd = ["python3", f"{sdir}/../harness/run_task.py", "--instance-id", iid,
       "--model", os.environ.get("MODEL_ID"), "--workdir", f"{root}/eval/workdir",
       "--output", pred, "--dataset", ds, "--timeout", os.environ.get("TASK_TIMEOUT"),
       "--worktree-path", os.environ.get("WT_PATH")]
if os.environ.get("IS_DARWIN") == "1": cmd.append("--darwin")
r = subprocess.run(cmd, capture_output=True, text=True)
print(r.stderr[-500:] if r.stderr else "", flush=True)
# tag this line with chain index for pairing
lines = open(pred).read().strip().splitlines()
if lines:
    last = json.loads(lines[-1]); last["_chain_idx"] = int(idx)
    lines[-1] = json.dumps(last)
    open(pred, "w").write("\n".join(lines) + "\n")
PY
    if [[ "$ARM" == "darwin" && $((i % 5)) == 0 && $i -lt $(python3 -c "import json;print(len(json.load(open('$DS'))))") ]]; then
      timeout 600 opencode run --dir "$WT" --model "$MODEL" \
        "Consolidate your project memory now (dream): merge duplicates, promote repeated patterns, file locations, conventions and gotchas from the last tasks into durable memory. Use the darwin_memory tool. Keep MEMORY.md under 120 lines. Reply in 3 lines." \
        >> "$OUT/chain_${SLUG}_s${SEED}_${ARM}.log" 2>&1 || true
    fi
  done
  unset DARWIN_HOME
}

# slot pool over (repo, seed) jobs; each job runs darwin→vanilla sequentially.
python3 - "$CHAIN_DIR/manifest.json" "$REPOS" "$SEEDS" << 'PY' > "$OUT/joblist.txt"
import json, sys
man = json.load(open(sys.argv[1]))[: int(sys.argv[2])]
seeds = range(1, int(sys.argv[3]) + 1)
for m in man:
    for s in seeds:
        print(f"{m['slug']}\t{s}\tpair\t{m['path']}")
PY
run_pair () {
  local SLUG="$1" SEED="$2" DS="$3"
  # RESUME GUARD: skip cells whose both arms are already complete
  local N=$(python3 -c "import json;print(len(json.load(open('$DS'))))")
  local PD="$OUT/predictions_darwin_${SLUG}__seed${SEED}.jsonl"
  local PV="$OUT/predictions_vanilla_${SLUG}__seed${SEED}.jsonl"
  local ND=$(grep -c . "$PD" 2>/dev/null || echo 0)
  local NV=$(grep -c . "$PV" 2>/dev/null || echo 0)
  if [[ "$ND" -ge "$N" && "$NV" -ge "$N" ]]; then
    echo "skip $SLUG s$SEED (complete: $ND/$NV of $N)" >> "$OUT/run.log"
    return 0
  fi
  run_chain "$SLUG" "$SEED" darwin "$DS"
  run_chain "$SLUG" "$SEED" vanilla "$DS"
}
TOTAL=$(wc -l < "$OUT/joblist.txt"); DONE=0
echo "0/$TOTAL pair-jobs" | tee -a "$OUT/run.log"
while IFS=$'\t' read -r SLUG SEED _ARM DS; do
  # memory guard: >=2500MB available before launching another job
  while (( $(free -m | awk '/^Mem:/{print $7}') < 2500 )); do sleep 30; done
  while [[ $(jobs -rp | wc -l) -ge $JOBS ]]; do wait -n; DONE=$((DONE+1)); echo "$DONE/$TOTAL pair-jobs done" >> "$OUT/run.log"; done
  DS_FILE="$DS" MODEL_ID="$MODEL" TASK_TIMEOUT="$TIMEOUT" \
    run_pair "$SLUG" "$SEED" "$DS" < /dev/null &
  sleep 20  # stagger: avoid simultaneous checkouts/first-turn spikes
done < "$OUT/joblist.txt"
wait
echo "$TOTAL/$TOTAL pair-jobs done" | tee -a "$OUT/run.log"

# real swebench grading (batched per arm)
export DOCKER_HOST=unix:///run/user/1000/podman/podman.sock
V=/tmp/opencode/swebench-venv/bin/python
for ARM in darwin vanilla; do
  cat "$OUT"/predictions_${ARM}_*.jsonl > "$OUT/sweb_${ARM}.jsonl" 2>/dev/null || true
  python3 - "$OUT" "$ARM" << 'PY'
import json, sys, os
root, arm = sys.argv[1], sys.argv[2]
rows = [json.loads(l) for l in open(os.path.join(root, f"sweb_{arm}.jsonl"))]
out = [{"instance_id": r["instance_id"], "model_name_or_path": r.get("model", "m"),
        "model_patch": r["patch"]} for r in rows]
with open(os.path.join(root, f"sweb_{arm}_fmt.jsonl"), "w") as f:
    for o in out:
        f.write(json.dumps(o) + "\n")
PY
  $V -m swebench.harness.run_evaluation --dataset_name SWE-bench/SWE-bench_Lite --split test \
    --predictions_path "$OUT/sweb_${ARM}_fmt.jsonl" --max_workers 6 \
    --run_id "night-$ARM" --report_dir "$OUT/swebench" >> "$OUT/grade.log" 2>&1 || true
done
mkdir -p "$OUT/swebench"
python3 "$SCRIPT_DIR/night_stats.py" "$OUT" | tee "$OUT/STATS.md"
echo "NIGHT DONE $(date)" | tee -a "$OUT/run.log"
