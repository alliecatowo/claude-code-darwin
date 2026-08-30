#!/usr/bin/env bash
# run_claude_pilot.sh — ONE-COMMAND pilot runner for darwin vs vanilla via Claude Code
#
# Mirrors run_pilot.sh but for Claude Code harness (run_task_claude.py).
#
# Usage: ./eval/scripts/run_claude_pilot.sh [--model MODEL] [--tasks N] [--darwin|--vanilla|--both] [--dataset PATH] [--timeout SECS] [--workdir PATH] [--output-dir PATH]
#
# Defaults: model=sonnet, tasks=50, both conditions
#   Runs run_task_claude.py for each task × condition, collects predictions, runs eval_patch.py, prints comparison table.
#
# Examples:
#   ./eval/scripts/run_claude_pilot.sh                          # 50 tasks, both, sonnet
#   ./eval/scripts/run_claude_pilot.sh --model sonnet --tasks 10 --vanilla
#   ./eval/scripts/run_claude_pilot.sh --model opus --tasks 300 --both
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
EVAL_DIR="${REPO_ROOT}/eval"
HARNESS_DIR="${EVAL_DIR}/harness"
DATASETS_DIR="${EVAL_DIR}/datasets"

# Defaults
MODEL="sonnet"
TASKS=50
MODE="both"  # darwin | vanilla | both
DATASET="${DATASETS_DIR}/lite_50.json"
TIMEOUT=600
WORKDIR="${EVAL_DIR}/workdir"
OUTPUT_DIR="${EVAL_DIR}/results"
KEEP_WORKTREE=0

usage() {
  cat <<EOF
Usage: $0 [--model MODEL] [--tasks N] [--darwin|--vanilla|--both] [options]

  --model MODEL      claude model id (default: ${MODEL})
  --tasks N          number of tasks from dataset (default: ${TASKS}; max for lite_50 is 50)
  --darwin           run only darwin condition
  --vanilla          run only vanilla condition
  --both             run both (default)
  --dataset PATH     dataset JSON (default: ${DATASET})
  --timeout SECS     per-task timeout seconds (default: ${TIMEOUT})
  --workdir PATH     workdir for repos/worktrees (default: ${WORKDIR})
  --output-dir PATH  output dir for predictions/reports (default: ${OUTPUT_DIR})
  --keep-worktree    keep worktrees after run (debugging)
  --help             show this help

Compares darwin off vs on using Claude Code harness (run_task_claude.py + eval_patch.py).
Needs: python3, git, claude. Works degraded without docker/swebench.

Pipeline per condition:
  1) For each task instance_id: python3 run_task_claude.py --instance-id ... --model ... [--darwin]
  2) Collect predictions JSONL: predictions_{vanilla,darwin}.jsonl (harness=claude)
  3) Evaluate: python3 eval_patch.py --predictions ... --dataset ...
  4) Print comparison table

Output:
  \${OUTPUT_DIR}/predictions_vanilla.jsonl
  \${OUTPUT_DIR}/predictions_darwin.jsonl
  \${OUTPUT_DIR}/report_vanilla.json
  \${OUTPUT_DIR}/report_darwin.json
  \${OUTPUT_DIR}/comparison.md

EOF
}

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL="$2"; shift 2;;
    --tasks) TASKS="$2"; shift 2;;
    --darwin) MODE="darwin"; shift;;
    --vanilla) MODE="vanilla"; shift;;
    --both) MODE="both"; shift;;
    --dataset) DATASET="$2"; shift 2;;
    --timeout) TIMEOUT="$2"; shift 2;;
    --workdir) WORKDIR="$2"; shift 2;;
    --output-dir) OUTPUT_DIR="$2"; shift 2;;
    --keep-worktree) KEEP_WORKTREE=1; shift;;
    --help|-h) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

# Validate
if [[ ! -f "${DATASET}" ]]; then
  echo "error: dataset not found at ${DATASET}" >&2
  echo "hint: check eval/datasets/lite_50.json or pass --dataset" >&2
  exit 2
fi
if ! command -v python3 >/dev/null 2>&1; then echo "error: python3 not found" >&2; exit 1; fi
if ! command -v git >/dev/null 2>&1; then echo "error: git not found" >&2; exit 1; fi
if [[ ! -f "${HARNESS_DIR}/run_task_claude.py" ]]; then echo "error: run_task_claude.py not found at ${HARNESS_DIR}" >&2; exit 2; fi
if [[ ! -f "${HARNESS_DIR}/eval_patch.py" ]]; then echo "error: eval_patch.py not found at ${HARNESS_DIR}" >&2; exit 2; fi
if ! command -v claude >/dev/null 2>&1; then
  echo "warn: claude not found in PATH — runs will produce error_no_claude records (still writes predictions)" >&2
fi

mkdir -p "${WORKDIR}" "${OUTPUT_DIR}"

# Resolve task list: first N from dataset (stratified sample is already ordered)
INSTANCE_IDS=$(python3 -c "
import json, sys
data=json.load(open('${DATASET}'))
ids=[r['instance_id'] for r in data[:${TASKS}]]
print(' '.join(ids))
")
if [[ -z "${INSTANCE_IDS}" ]]; then
  echo "error: no instances found in dataset (TASKS=${TASKS})" >&2
  exit 2
fi
NUM_TASKS=$(echo "${INSTANCE_IDS}" | wc -w | tr -d ' ')
echo "== darwin pilot (claude) =="
echo "model:      ${MODEL}"
echo "harness:    claude"
echo "tasks:      ${NUM_TASKS} (requested ${TASKS})"
echo "mode:       ${MODE}"
echo "dataset:    ${DATASET}"
echo "timeout:    ${TIMEOUT}s per task"
echo "workdir:    ${WORKDIR}"
echo "output:     ${OUTPUT_DIR}"
echo "instances:  ${INSTANCE_IDS}"
echo ""

# Helper: run one condition
run_condition() {
  local cond="$1"  # vanilla or darwin
  local darwin_flag=""
  local pred_file="${OUTPUT_DIR}/predictions_${cond}.jsonl"
  local log_file="${OUTPUT_DIR}/run_${cond}.log"

  if [[ "${cond}" == "darwin" ]]; then
    darwin_flag="--darwin"
  fi

  # Fresh file
  : > "${pred_file}"
  : > "${log_file}"

  echo "--- running condition: ${cond} (model=${MODEL} harness=claude darwin=${cond}) ---" | tee -a "${log_file}"
  echo "predictions -> ${pred_file}" | tee -a "${log_file}"

  local idx=0
  local failed=0
  for iid in ${INSTANCE_IDS}; do
    idx=$((idx+1))
    echo "" | tee -a "${log_file}"
    echo "[$idx/${NUM_TASKS}] ${iid} (${cond})…" | tee -a "${log_file}"
    RUN_ARGS=( \
      --instance-id "${iid}" \
      --model "${MODEL}" \
      --workdir "${WORKDIR}" \
      --output "${pred_file}" \
      --dataset "${DATASET}" \
      --timeout "${TIMEOUT}" \
    )
    if [[ -n "${darwin_flag}" ]]; then
      RUN_ARGS+=(--darwin)
    fi
    if [[ ${KEEP_WORKTREE} -eq 1 ]]; then
      RUN_ARGS+=(--keep-worktree)
    fi

    set +e
    python3 "${HARNESS_DIR}/run_task_claude.py" "${RUN_ARGS[@]}" 2>&1 | tee -a "${log_file}"
    RC=${PIPESTATUS[0]}
    set -e
    if [[ ${RC} -ne 0 ]]; then
      echo "  warn: run_task_claude for ${iid} exited ${RC} (still recording prediction if written)" | tee -a "${log_file}"
      if ! grep -q "\"instance_id\": \"${iid}\"" "${pred_file}" 2>/dev/null; then
        python3 -c "
import json
rec={'instance_id':'${iid}','model':'${MODEL}','model_name_or_path':'${MODEL}','harness':'claude','darwin':$( [[ \"${cond}\" == \"darwin\" ]] && echo true || echo false ),'patch':'','model_patch':'','cost':None,'tokens':None,'duration_s':0,'status':'error_no_output'}
open('${pred_file}','a').write(json.dumps(rec)+'\n')
print('wrote fallback error record for ${iid}')
" 2>&1 | tee -a "${log_file}" || true
        failed=$((failed+1))
      fi
    fi
    sleep 1
  done

  echo "" | tee -a "${log_file}"
  echo "condition ${cond} done: $(wc -l < "${pred_file}" | tr -d ' ') predictions, ${failed} fallback errors" | tee -a "${log_file}"
  echo "log: ${log_file}" | tee -a "${log_file}"
}

# Run conditions
if [[ "${MODE}" == "both" ]]; then
  run_condition vanilla
  run_condition darwin
elif [[ "${MODE}" == "vanilla" ]]; then
  run_condition vanilla
elif [[ "${MODE}" == "darwin" ]]; then
  run_condition darwin
else
  echo "unknown MODE=${MODE}" >&2; exit 2
fi

# Evaluate each predictions file
echo ""
echo "== evaluation =="

eval_condition() {
  local cond="$1"
  local pred_file="${OUTPUT_DIR}/predictions_${cond}.jsonl"
  local report_file="${OUTPUT_DIR}/report_${cond}.json"
  if [[ ! -f "${pred_file}" ]]; then
    echo "skip eval ${cond}: no predictions file at ${pred_file}" >&2
    return
  fi
  if [[ ! -s "${pred_file}" ]]; then
    echo "skip eval ${cond}: predictions file empty" >&2
    return
  fi
  echo "--- eval ${cond} ---"
  python3 "${HARNESS_DIR}/eval_patch.py" \
    --predictions "${pred_file}" \
    --dataset "${DATASET}" \
    --output "${report_file}" \
    --workdir "${WORKDIR}" || echo "eval_patch for ${cond} failed (see above)" >&2
  echo "report -> ${report_file}"
  echo ""
}

if [[ "${MODE}" == "both" ]]; then
  eval_condition vanilla
  eval_condition darwin
elif [[ "${MODE}" == "vanilla" ]]; then
  eval_condition vanilla
elif [[ "${MODE}" == "darwin" ]]; then
  eval_condition darwin
fi

# Comparison table
echo "== comparison =="

COMPARISON_MD="${OUTPUT_DIR}/comparison.md"
python3 - << PY
import json, pathlib, sys
output_dir = pathlib.Path("${OUTPUT_DIR}")
mode = "${MODE}"

def load_report(cond):
    p = output_dir / f"report_{cond}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception as e:
        print(f"warn: failed to load {p}: {e}", file=sys.stderr)
        return None

reports = {}
for cond in ("vanilla","darwin"):
    r = load_report(cond)
    if r is not None:
        reports[cond]=r

if not reports:
    print("No reports to compare (predictions may be empty or eval failed).")
    sys.exit(0)

lines=[]
lines.append("| Condition | Harness | Resolved | Total | Rate | Applies | Cost (avg) | Tokens (avg) |")
lines.append("|---|---|---|---|---|---|---|---|---|")
for cond in ("vanilla","darwin"):
    if cond not in reports:
        continue
    r=reports[cond]
    total=r.get("total",0)
    resolved=r.get("resolved",0)
    applies=r.get("applies",resolved)
    rate=r.get("resolved_rate",0)*100 if total else 0
    costs=[x.get("cost") for x in r.get("results",[]) if x.get("cost") is not None]
    toks=[x.get("tokens") for x in r.get("results",[]) if x.get("tokens") is not None]
    avg_cost = sum(costs)/len(costs) if costs else 0
    avg_tok = sum(toks)/len(toks) if toks else 0
    cost_s = f"\${avg_cost:.4f}" if costs else "n/a"
    tok_s = f"{avg_tok:.0f}" if toks else "n/a"
    lines.append(f"| {cond:9s} | claude | {resolved:8d} | {total:5d} | {rate:5.1f}% | {applies:7d} | {cost_s:10s} | {tok_s:12s} |")

if "vanilla" in reports and "darwin" in reports:
    v=reports["vanilla"]
    d=reports["darwin"]
    vt=v.get("total",1); dt=d.get("total",1)
    vr=v.get("resolved",0)/vt*100 if vt else 0
    dr=d.get("resolved",0)/dt*100 if dt else 0
    delta=dr-vr
    lines.append("")
    lines.append(f"**Delta (darwin − vanilla): {delta:+.1f} points** ({d.get('resolved',0)}/{dt} vs {v.get('resolved',0)}/{vt}) harness=claude")
    if reports["vanilla"].get("degraded") or reports["darwin"].get("degraded"):
        lines.append("")
        lines.append("> Degraded eval: 'resolved' == 'patch applies cleanly' (docker/swebench not available).")
        lines.append("> For real test execution: docker info must work and pip install swebench.")
    lines.append("")
    lines.append(f"Model: ${MODEL}  ·  Tasks: {vt}  ·  Dataset: ${DATASET}  ·  Harness: claude")

text="\n".join(lines)
print(text)
out = output_dir / "comparison.md"
out.write_text("# Pilot comparison (claude)\n\n" + text + "\n")

summary = {
    "model": "${MODEL}",
    "harness": "claude",
    "tasks": reports.get("vanilla",{}).get("total") or reports.get("darwin",{}).get("total"),
    "mode": mode,
    "reports": reports,
}
if "vanilla" in reports and "darwin" in reports:
    summary["delta_points"] = (reports["darwin"]["resolved"]/reports["darwin"]["total"]*100 if reports["darwin"]["total"] else 0) - (reports["vanilla"]["resolved"]/reports["vanilla"]["total"]*100 if reports["vanilla"]["total"] else 0)
(pathlib.Path(output_dir)/"summary.json").write_text(json.dumps(summary, indent=2))

PY

echo ""
echo "comparison -> ${COMPARISON_MD}"
cat "${COMPARISON_MD}" 2>/dev/null || true
echo ""
echo "summary -> ${OUTPUT_DIR}/summary.json"
echo "predictions -> ${OUTPUT_DIR}/predictions_{vanilla,darwin}.jsonl (harness=claude)"
echo "reports -> ${OUTPUT_DIR}/report_{vanilla,darwin}.json"
echo ""
if [[ "${MODE}" == "both" ]]; then
  echo "Done. Both conditions evaluated (claude). Check comparison.md for delta."
else
  echo "Done. Single condition (${MODE}) evaluated (claude)."
fi
echo ""
echo "Next: inspect ${OUTPUT_DIR}/comparison.md and compare with opencode pilot via eval/scripts/compare.py (see docs/EVALUATION.md)."
