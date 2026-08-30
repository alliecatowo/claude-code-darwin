#!/usr/bin/env bash
set -euo pipefail

# run_matrix.sh — runs one or more experiment shapes from experiments.yaml
# Single source of truth: eval/configs/experiments.yaml
#
# Usage:
#   ./eval/scripts/run_matrix.sh [--ids A-vanilla,A-darwin] [--tasks 50] [--dataset lite_50] [--dry-run] [--out-dir eval/results]
#
# For each experiment id:
#   - reads model / fallbacks / darwin / economics_routing from experiments.yaml
#   - builds per-experiment opencode.json overlay (darwin vs vanilla base)
#   - calls run_pilot.sh or run_task.py with the right flags
#   - collects predictions.jsonl + eval report.json
#   - at end prints comparison matrix table (resolved %, cost, cost/resolved, turns/task, cache hit)
#
# See docs/EVALUATION.md §5 for harness paths (AlphaDiana / swebench_container).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
EXPERIMENTS_YAML="$REPO_ROOT/eval/configs/experiments.yaml"
VANILLA_JSON="$REPO_ROOT/eval/configs/opencode.vanilla.json"
DARWIN_JSON="$REPO_ROOT/eval/configs/opencode.darwin.json"
COMPARE_PY="$SCRIPT_DIR/compare.py"

# Defaults
IDS=""                # empty = all
TASKS="50"
DATASET="lite_50"
OUT_DIR="$REPO_ROOT/eval/results"
DRY_RUN="false"
HARNESS="opencode"    # opencode|claude|both (global default; per-experiment harness: field overrides)
RUNNER_EXTRA_ARGS=()

usage() {
  cat <<'USAGE'
Usage: ./eval/scripts/run_matrix.sh [options]

Options:
  --ids ID1,ID2       Comma-separated experiment ids (default: all from experiments.yaml)
                     e.g. --ids A-vanilla,A-darwin or --ids C-mixture,ceiling-deepseek-darwin
  --tasks N           Max tasks per experiment (default: 50). Passed to runner as --tasks / max_tasks.
  --dataset NAME      Dataset shorthand or HF id (default: lite_50)
                      Shorthands: lite_50, lite_300, verified_500, tb_2.1, verified
                      Full ids passed through: princeton-nlp/SWE-bench_Lite, princeton-nlp/SWE-bench_Verified, etc.
  --out-dir DIR       Results root (default: eval/results). Per-experiment dir: <out-dir>/<id>/
  --harness NAME      Harness to use: opencode|claude|both (default: opencode)
                      Selects eval/harness/run_task.py vs run_task_claude.py.
                      Per-experiment `harness:` in experiments.yaml overrides this global (see ceiling-*).
  --dry-run           Print commands without executing runners
  --help, -h          Show this help

Examples:
  ./eval/scripts/run_matrix.sh --ids A-vanilla,A-darwin --tasks 50 --dataset lite_50
  ./eval/scripts/run_matrix.sh --tasks 300 --dataset lite_300
  ./eval/scripts/run_matrix.sh --ids C-mixture --dry-run
  ./eval/scripts/run_matrix.sh --harness claude --ids ceiling-sonnet,ceiling-sonnet-darwin
  ./eval/scripts/run_matrix.sh --harness both --ids ceiling-deepseek-darwin

Output per experiment:
  <out-dir>/<id>/opencode.json        — overlay used for this shape (or settings.json for claude)
  <out-dir>/<id>/predictions.jsonl   — swebench-format patches (with harness field)
  <out-dir>/<id>/report.json         — swebench eval report (if harness produced it)
  <out-dir>/<id>/meta.json           — model/fallbacks/darwin/harness + invocation

Comparison:
  Prints markdown table to stdout and writes <out-dir>/comparison.md + comparison.json
  via eval/scripts/compare.py (falls back to simple table if compare.py missing).
  compare.py now handles cross-harness (opencode vs claude) deltas.

Env:
  OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=1  recommended for darwin subagent parity
USAGE
}

# ---- arg parse -------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ids) IDS="${2:-}"; shift 2 ;;
    --ids=*) IDS="${1#*=}"; shift ;;
    --tasks) TASKS="${2:-}"; shift 2 ;;
    --tasks=*) TASKS="${1#*=}"; shift ;;
    --dataset) DATASET="${2:-}"; shift 2 ;;
    --dataset=*) DATASET="${1#*=}"; shift ;;
    --out-dir) OUT_DIR="${2:-}"; shift 2 ;;
    --out-dir=*) OUT_DIR="${1#*=}"; shift ;;
    --harness) HARNESS="${2:-}"; shift 2 ;;
    --harness=*) HARNESS="${1#*=}"; shift ;;
    --dry-run) DRY_RUN="true"; shift ;;
    --help|-h) usage; exit 0 ;;
    --) shift; RUNNER_EXTRA_ARGS+=("$@"); break ;;
    *) echo "unknown arg: $1" >&2; usage >&2; exit 1 ;;
  esac
done

# Validate harness
case "$HARNESS" in
  opencode|claude|both) ;;
  *) echo "unknown --harness value: $HARNESS (expected opencode|claude|both)" >&2; exit 1 ;;
esac

# ---- helpers ---------------------------------------------------------------

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing required command: $1" >&2; exit 1; }; }
need python3

if [[ ! -f "$EXPERIMENTS_YAML" ]]; then
  echo "experiments.yaml not found at $EXPERIMENTS_YAML" >&2; exit 1
fi
if [[ ! -f "$VANILLA_JSON" ]]; then
  echo "vanilla base not found at $VANILLA_JSON" >&2; exit 1
fi

# Resolve dataset shorthand → HF id (used to document; runner receives both)
resolve_dataset() {
  case "$1" in
    lite_50|lite-50) echo "princeton-nlp/SWE-bench_Lite" ;;
    lite_300|lite-300|lite) echo "princeton-nlp/SWE-bench_Lite" ;;
    verified_500|verified-500|verified) echo "princeton-nlp/SWE-bench_Verified" ;;
    tb_2.1|tb2.1|terminal-bench|tb) echo "terminal-bench/terminal-bench" ;;
    *) echo "$1" ;;
  esac
}
HF_DATASET="$(resolve_dataset "$DATASET")"

# Resolve dataset shorthand → local file path (for harness/run_task.py which expects file)
resolve_dataset_file() {
  case "$1" in
    lite_50|lite-50) echo "$REPO_ROOT/eval/datasets/lite_50.json" ;;
    lite_300|lite-300|lite) echo "$REPO_ROOT/eval/datasets/lite_50.json" ;; # reuse sample truncated to TASKS
    verified_500|verified-500|verified) echo "$REPO_ROOT/eval/datasets/lite_50.json" ;; # fallback; real Verified needs HF
    *) 
      if [[ -f "$REPO_ROOT/eval/datasets/$1.json" ]]; then echo "$REPO_ROOT/eval/datasets/$1.json"
      elif [[ -f "$1" ]]; then echo "$1"
      else echo "$REPO_ROOT/eval/datasets/lite_50.json"
      fi
      ;;
  esac
}
DATASET_FILE="$(resolve_dataset_file "$DATASET")"

# Extract ids from experiments.yaml via python (PyYAML if available, else minimal parser)
list_ids_py() {
python3 - "$EXPERIMENTS_YAML" <<'PY'
import sys, json, re
path = sys.argv[1]
try:
    import yaml  # type: ignore
    with open(path) as f:
        data = yaml.safe_load(f)
    for e in data.get("experiments", []):
        print(e["id"])
except ImportError:
    with open(path) as f:
        for line in f:
            m = re.match(r'\s*-\s*id:\s*(\S+)', line)
            if m:
                print(m.group(1))
except Exception as e:
    print(f"failed to parse {path}: {e}", file=sys.stderr)
    sys.exit(1)
PY
}

# Return JSON object for a single experiment id (or exit 2 if not found)
get_experiment_json() {
  local target_id="$1"
python3 - "$EXPERIMENTS_YAML" "$target_id" <<'PY'
import sys, json, re
path, target = sys.argv[1], sys.argv[2]
try:
    import yaml
    with open(path) as f:
        data = yaml.safe_load(f)
    for e in data.get("experiments", []):
        if e.get("id") == target:
            print(json.dumps(e))
            sys.exit(0)
    sys.exit(2)
except ImportError:
    with open(path) as f:
        text = f.read()
    pat = re.compile(r'-\s*id:\s*' + re.escape(target) + r'\b(.*?)(?=\n\s*-\s*id:|\Z)', re.S)
    m = pat.search(text)
    if not m:
        sys.exit(2)
    block = m.group(0)
    def grab(key):
        mm = re.search(r'^\s*'+re.escape(key)+r'\s*:\s*(.+)$', block, re.M)
        return mm.group(1).strip() if mm else None
    def parse_val(v):
        if v is None:
            return None
        v = v.strip()
        if v.startswith('"') and v.endswith('"'):
            return v[1:-1]
        if v == 'true': return True
        if v == 'false': return False
        if v == '[]': return []
        if v.startswith('['):
            import ast
            try: return ast.literal_eval(v)
            except: return v
        try: return int(v)
        except: pass
        try: return float(v)
        except: pass
        return v
    exp = {"id": target}
    for k in ["description","model","fallbacks","darwin","economics_routing","routing_policy","budget_usd","harness"]:
        raw = grab(k)
        if raw is not None:
            exp[k] = parse_val(raw)
    if "fallbacks" not in exp:
        exp["fallbacks"] = []
    print(json.dumps(exp))
    sys.exit(0)
except SystemExit:
    raise
except Exception as e:
    print(f"failed to get {target}: {e}", file=sys.stderr)
    sys.exit(1)
PY
}

# Resolve IDS list
if [[ -z "$IDS" ]]; then
  mapfile -t ALL_IDS < <(list_ids_py)
  if [[ ${#ALL_IDS[@]} -eq 0 ]]; then echo "no experiments found in $EXPERIMENTS_YAML" >&2; exit 1; fi
else
  IFS=',' read -ra ALL_IDS <<< "$IDS"
  for i in "${!ALL_IDS[@]}"; do ALL_IDS[$i]="$(echo "${ALL_IDS[$i]}" | xargs)"; done
fi

echo "== darwin matrix =="
echo "experiments : ${ALL_IDS[*]}"
echo "dataset     : $DATASET  →  $HF_DATASET"
echo "dataset_file: $DATASET_FILE"
echo "tasks       : $TASKS"
echo "out_dir     : $OUT_DIR"
echo "harness     : $HARNESS"
echo "dry_run     : $DRY_RUN"
echo "yaml        : $EXPERIMENTS_YAML"
echo ""

mkdir -p "$OUT_DIR"

if [[ -z "${OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS:-}" && -z "${OPENCODE_EXPERIMENTAL:-}" ]]; then
  echo "note: OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS not set — model-facing darwin subagents will block (see docs/RUNDOWN.md §4b)" >&2
fi

# ---- find runner -----------------------------------------------------------
RUN_TASK_PY=""
if [[ -f "$REPO_ROOT/eval/harness/run_task.py" ]]; then RUN_TASK_PY="$REPO_ROOT/eval/harness/run_task.py"
elif [[ -f "$REPO_ROOT/eval/scripts/run_task.py" ]]; then RUN_TASK_PY="$REPO_ROOT/eval/scripts/run_task.py"
elif [[ -f "$SCRIPT_DIR/run_task.py" ]]; then RUN_TASK_PY="$SCRIPT_DIR/run_task.py"
elif [[ -f "$SCRIPT_DIR/../harness/run_task.py" ]]; then RUN_TASK_PY="$SCRIPT_DIR/../harness/run_task.py"
fi
RUN_TASK_CLAUDE_PY=""
if [[ -f "$REPO_ROOT/eval/harness/run_task_claude.py" ]]; then RUN_TASK_CLAUDE_PY="$REPO_ROOT/eval/harness/run_task_claude.py"
elif [[ -f "$REPO_ROOT/eval/scripts/run_task_claude.py" ]]; then RUN_TASK_CLAUDE_PY="$REPO_ROOT/eval/scripts/run_task_claude.py"
elif [[ -f "$SCRIPT_DIR/run_task_claude.py" ]]; then RUN_TASK_CLAUDE_PY="$SCRIPT_DIR/run_task_claude.py"
elif [[ -f "$SCRIPT_DIR/../harness/run_task_claude.py" ]]; then RUN_TASK_CLAUDE_PY="$SCRIPT_DIR/../harness/run_task_claude.py"
fi
RUN_PILOT_SH=""
if [[ -f "$REPO_ROOT/eval/scripts/run_pilot.sh" ]]; then RUN_PILOT_SH="$REPO_ROOT/eval/scripts/run_pilot.sh"
elif [[ -f "$SCRIPT_DIR/run_pilot.sh" ]]; then RUN_PILOT_SH="$SCRIPT_DIR/run_pilot.sh"
fi
RUN_CLAUDE_PILOT_SH=""
if [[ -f "$REPO_ROOT/eval/scripts/run_claude_pilot.sh" ]]; then RUN_CLAUDE_PILOT_SH="$REPO_ROOT/eval/scripts/run_claude_pilot.sh"
elif [[ -f "$SCRIPT_DIR/run_claude_pilot.sh" ]]; then RUN_CLAUDE_PILOT_SH="$SCRIPT_DIR/run_claude_pilot.sh"
fi
HARNESS_EVAL_PY=""
if [[ -f "$REPO_ROOT/eval/harness/eval_patch.py" ]]; then HARNESS_EVAL_PY="$REPO_ROOT/eval/harness/eval_patch.py"
fi

# Detect harness per-task mode (requires --instance-id)
IS_HARNESS_PERTASK="false"
if [[ -n "$RUN_TASK_PY" && -f "$RUN_TASK_PY" ]]; then
  if grep -q -- "--instance-id" "$RUN_TASK_PY"; then
    IS_HARNESS_PERTASK="true"
  fi
fi
IS_CLAUDE_PERTASK="false"
if [[ -n "$RUN_TASK_CLAUDE_PY" && -f "$RUN_TASK_CLAUDE_PY" ]]; then
  if grep -q -- "--instance-id" "$RUN_TASK_CLAUDE_PY"; then
    IS_CLAUDE_PERTASK="true"
  fi
fi

# ---- per-experiment loop ---------------------------------------------------
PREDICTIONS=()

for EXP_ID in "${ALL_IDS[@]}"; do
  echo "────────────────────────────────────────────────────────────"
  echo "▶ $EXP_ID"
  echo "────────────────────────────────────────────────────────────"

  set +e
  EXP_JSON="$(get_experiment_json "$EXP_ID")"
  RC=$?
  set -e
  if [[ $RC -eq 2 ]]; then
    echo "  ✗ unknown experiment id '$EXP_ID' — skipping (check $EXPERIMENTS_YAML)" >&2
    continue
  elif [[ $RC -ne 0 ]]; then
    echo "  ✗ failed to load experiment '$EXP_ID'" >&2
    continue
  fi

  mapfile -t _FIELDS < <(
python3 - "$EXP_JSON" <<'PY'
import json, sys
e = json.loads(sys.argv[1])
print(e.get("model",""))
import json as j
print(j.dumps(e.get("fallbacks",[])))
print(str(e.get("darwin", False)).lower())
print(str(e.get("economics_routing", False)).lower())
print(e.get("routing_policy","") or "")
print(e.get("budget_usd","") or "")
print(e.get("harness","") or "")
PY
  )
  MODEL="${_FIELDS[0]:-}"
  FALLBACKS_JSON="${_FIELDS[1]:-[]}"
  DARWIN="${_FIELDS[2]:-false}"
  ECON_ROUTING="${_FIELDS[3]:-false}"
  ROUTING_POLICY="${_FIELDS[4]:-}"
  BUDGET="${_FIELDS[5]:-}"
  EXP_HARNESS_RAW="${_FIELDS[6]:-}"

  FALLBACKS_STR="$(python3 -c 'import json,sys; arr=json.loads(sys.argv[1]); print(" ".join(arr))' "$FALLBACKS_JSON")"
  read -ra FALLBACKS_ARR <<< "$FALLBACKS_STR"

  # Resolve effective harness: per-experiment harness field > global --harness
  # "both" means this experiment is eligible for either harness; global --harness filters it
  if [[ -n "$EXP_HARNESS_RAW" && "$EXP_HARNESS_RAW" != "null" ]]; then
    if [[ "$EXP_HARNESS_RAW" == "both" && "$HARNESS" != "both" ]]; then
      EFFECTIVE_HARNESS="$HARNESS"
    else
      EFFECTIVE_HARNESS="$EXP_HARNESS_RAW"
    fi
  else
    EFFECTIVE_HARNESS="$HARNESS"
  fi
  # Normalize harness list
  case "$EFFECTIVE_HARNESS" in
    opencode|claude) HARNESS_LIST=("$EFFECTIVE_HARNESS") ;;
    both) HARNESS_LIST=("opencode" "claude") ;;
    *) HARNESS_LIST=("$HARNESS") ;;
  esac

  echo "  model              : $MODEL"
  echo "  fallbacks          : ${FALLBACKS_STR:-<none>}"
  echo "  darwin             : $DARWIN"
  echo "  economics_routing  : $ECON_ROUTING  ${ROUTING_POLICY:+policy=$ROUTING_POLICY}  ${BUDGET:+budget=\$$BUDGET}"
  echo "  harness            : $EFFECTIVE_HARNESS (resolved: ${HARNESS_LIST[*]})"

  # ---- per-harness loop (supports --harness opencode|claude|both and per-experiment harness: field) ----
  for CURRENT_HARNESS in "${HARNESS_LIST[@]}"; do
    if [[ ${#HARNESS_LIST[@]} -eq 1 ]]; then
      EXP_DIR_H="$OUT_DIR/$EXP_ID"
      HARNESS_SUFFIX=""
    else
      EXP_DIR_H="$OUT_DIR/${EXP_ID}-${CURRENT_HARNESS}"
      HARNESS_SUFFIX="-${CURRENT_HARNESS}"
    fi
    mkdir -p "$EXP_DIR_H"
    PRED_PATH="$EXP_DIR_H/predictions.jsonl"
    REPORT_PATH="$EXP_DIR_H/report.json"
    META_PATH="$EXP_DIR_H/meta.json"
    OPENCODE_OVERLAY="$EXP_DIR_H/opencode.json"
    CLAUDE_OVERLAY="$EXP_DIR_H/settings.json"

    if [[ "$CURRENT_HARNESS" == "claude" ]]; then
      python3 - "$VANILLA_JSON" "$DARWIN_JSON" "$MODEL" "$FALLBACKS_JSON" "$DARWIN" "$CLAUDE_OVERLAY" "$CURRENT_HARNESS" <<'PY'
import json, sys
vanilla_p, darwin_p, model, fallbacks_json, darwin_flag, out, harness = sys.argv[1:8]
darwin_on = darwin_flag.lower() == "true"
base_path = darwin_p if darwin_on else vanilla_p
with open(base_path) as f:
    cfg = json.load(f)
claude_cfg = {}
claude_cfg["permissions"] = {"defaultMode": "acceptEdits"}
if darwin_on:
    claude_cfg["enabledPlugins"] = {"darwin@darwin": True}
claude_cfg["_darwin_harness"] = harness
claude_cfg["_darwin_model"] = model
fallbacks = json.loads(fallbacks_json)
if fallbacks:
    claude_cfg["_darwin_fallbacks"] = fallbacks
claude_cfg["subagent_depth"] = max(cfg.get("subagent_depth", 1), 3)
with open(out, "w") as f:
    json.dump(claude_cfg, f, indent=2)
    f.write("\n")
print(f"wrote {out} (harness={harness} darwin={darwin_on} model={model})")
PY
      MAIN_OVERLAY="$CLAUDE_OVERLAY"
    else
      python3 - "$VANILLA_JSON" "$DARWIN_JSON" "$MODEL" "$FALLBACKS_JSON" "$DARWIN" "$OPENCODE_OVERLAY" <<'PY'
import json, sys
vanilla_p, darwin_p, model, fallbacks_json, darwin_flag, out = sys.argv[1:7]
darwin_on = darwin_flag.lower() == "true"
base_path = darwin_p if darwin_on else vanilla_p
with open(base_path) as f:
    cfg = json.load(f)
cfg["model"] = model
fallbacks = json.loads(fallbacks_json)
if fallbacks:
    cfg["fallbacks"] = fallbacks
    cfg["cooldown_seconds"] = 300
else:
    cfg.pop("fallbacks", None)
    cfg.pop("cooldown_seconds", None)
cfg["subagent_depth"] = max(cfg.get("subagent_depth", 1), 3)
with open(out, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
print(f"wrote {out} (darwin={darwin_on}, model={model}, fallbacks={fallbacks})")
PY
      MAIN_OVERLAY="$OPENCODE_OVERLAY"
    fi

    python3 - "$EXP_JSON" "$DATASET" "$HF_DATASET" "$TASKS" "$REPO_ROOT" "$EXP_ID" "$META_PATH" "$CURRENT_HARNESS" <<'PY'
import json, sys, subprocess, time, os
exp_json, dataset, hf_dataset, tasks, repo_root, exp_id, meta_path, harness = sys.argv[1:9]
exp = json.loads(exp_json)
meta = {
    "id": exp_id,
    "description": exp.get("description",""),
    "model": exp.get("model"),
    "fallbacks": exp.get("fallbacks",[]),
    "darwin": exp.get("darwin", False),
    "economics_routing": exp.get("economics_routing", False),
    "routing_policy": exp.get("routing_policy"),
    "budget_usd": exp.get("budget_usd"),
    "dataset": dataset,
    "hf_dataset": hf_dataset,
    "max_tasks": int(tasks),
    "harness": harness,
    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
for cmd, key in [(["opencode","--version"],"opencode_version"), (["claude","--version"],"claude_version"), (["swebench","--version"],"swebench_version")]:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        val = (out.stdout or out.stderr or "").strip().splitlines()[0] if (out.stdout or out.stderr) else ""
        if val: meta[key] = val
    except: pass
try:
    out = subprocess.run(["git","rev-parse","HEAD"], cwd=repo_root, capture_output=True, text=True, timeout=3)
    if out.returncode == 0: meta["git_sha"] = out.stdout.strip()
except: pass
with open(meta_path, "w") as f:
    json.dump(meta, f, indent=2)
    f.write("\n")
PY
    if [[ "$CURRENT_HARNESS" == "claude" ]]; then
      echo "  harness            : claude"
      echo "  overlay            : $CLAUDE_OVERLAY (claude settings.json)"
    else
      echo "  harness            : opencode"
      echo "  overlay            : $OPENCODE_OVERLAY"
    fi
    echo "  meta               : $META_PATH"

    # ---- execution per harness -------------------------------------------
    if [[ "$DRY_RUN" == "true" ]]; then
      echo "  (dry-run) [$CURRENT_HARNESS] skipping execution"
      if [[ ! -f "$PRED_PATH" ]]; then
        echo "  (dry-run) no predictions at $PRED_PATH — comparison will show n=0 for this shape"
      fi
      PREDICTIONS+=("$PRED_PATH")
      continue
    fi

    EXECUTED="false"

  if [[ "$CURRENT_HARNESS" == "claude" ]]; then
    if [[ "$IS_CLAUDE_PERTASK" == "true" && -n "$RUN_TASK_CLAUDE_PY" ]]; then
      echo "  harness            : $RUN_TASK_CLAUDE_PY (claude per-task, $TASKS tasks from $DATASET_FILE)"
      if [[ -f "$DATASET_FILE" ]]; then
        INSTANCE_IDS="$(python3 - "$DATASET_FILE" "$TASKS" <<'PY'
import json, sys
path, n = sys.argv[1], int(sys.argv[2])
data = json.loads(open(path).read())
ids = [r["instance_id"] for r in data[:n]]
print(" ".join(ids))
PY
)"
      else
        echo "  warn: dataset file not found at $DATASET_FILE — using synthetic instance_ids" >&2
        INSTANCE_IDS="$(python3 - "$TASKS" <<'PY'
import sys
n=int(sys.argv[1])
print(" ".join(f"synth__task-{i:03d}" for i in range(n)))
PY
)"
      fi
      NUM_IDS="$(echo "$INSTANCE_IDS" | wc -w | tr -d ' ')"
      echo "  instances          : $NUM_IDS"
      : > "$PRED_PATH"
      EXP_WORKDIR="$OUT_DIR/../workdir"
      mkdir -p "$EXP_WORKDIR"
      COMMON_ARGS=(
        --model "$MODEL"
        --dataset "$DATASET_FILE"
        --output "$PRED_PATH"
        --workdir "$EXP_WORKDIR"
      )
      if [[ "$DARWIN" == "true" ]]; then COMMON_ARGS+=(--darwin); fi
      if [[ ${#FALLBACKS_ARR[@]} -gt 0 ]]; then
        COMMON_ARGS+=(--fallbacks "${FALLBACKS_ARR[@]}")
      fi
      COMMON_ARGS+=(--claude-json "$CLAUDE_OVERLAY")
      if [[ "$ECON_ROUTING" == "true" ]]; then
        COMMON_ARGS+=(--economics-routing)
        [[ -n "$ROUTING_POLICY" ]] && COMMON_ARGS+=(--routing-policy "$ROUTING_POLICY")
        [[ -n "$BUDGET" ]] && COMMON_ARGS+=(--budget "$BUDGET")
      fi
      COMMON_ARGS+=("${RUNNER_EXTRA_ARGS[@]}")
      FAILS=0
      IDX=0
      for IID in $INSTANCE_IDS; do
        IDX=$((IDX+1))
        echo "  [$IDX/$NUM_IDS] $IID …"
        set +e
        python3 "$RUN_TASK_CLAUDE_PY" --instance-id "$IID" "${COMMON_ARGS[@]}" 2>&1 | sed 's/^/    | /'
        RC=${PIPESTATUS[0]}
        set -e
        if [[ $RC -ne 0 ]]; then
          echo "    warn: run_task_claude for $IID exited $RC" >&2
          FAILS=$((FAILS+1))
          if ! grep -q "\"instance_id\": \"$IID\"" "$PRED_PATH" 2>/dev/null; then
            python3 - "$PRED_PATH" "$IID" "$MODEL" "$DARWIN" <<'PY'
import json, sys
p, iid, model, darwin = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4].lower()=="true"
rec={"instance_id": iid, "model": model, "model_name_or_path": model, "harness": "claude", "darwin": darwin, "patch": "", "model_patch":"", "cost": None, "tokens": None, "duration_s": 0, "status":"error_no_output"}
open(p,"a").write(json.dumps(rec)+"\n")
PY
          fi
        fi
        sleep 1
      done
      echo "  predictions        : $PRED_PATH  ($(wc -l < "$PRED_PATH" 2>/dev/null | tr -d ' ') tasks, $FAILS failures)"
      EXECUTED="true"
    elif [[ -n "$RUN_TASK_CLAUDE_PY" ]]; then
      echo "  runner             : python3 $RUN_TASK_CLAUDE_PY (claude batch)"
      BATCH_ARGS=(--model "$MODEL" --dataset "$HF_DATASET" --tasks "$TASKS" --out "$PRED_PATH")
      if [[ ${#FALLBACKS_ARR[@]} -gt 0 ]]; then BATCH_ARGS+=(--fallbacks "${FALLBACKS_ARR[@]}"); fi
      if [[ "$DARWIN" == "true" ]]; then BATCH_ARGS+=(--darwin); else BATCH_ARGS+=(--no-darwin); fi
      if [[ "$ECON_ROUTING" == "true" ]]; then
        BATCH_ARGS+=(--economics-routing)
        [[ -n "$ROUTING_POLICY" ]] && BATCH_ARGS+=(--routing-policy "$ROUTING_POLICY")
        [[ -n "$BUDGET" ]] && BATCH_ARGS+=(--budget "$BUDGET")
      fi
      BATCH_ARGS+=(--claude-json "$CLAUDE_OVERLAY")
      BATCH_ARGS+=("${RUNNER_EXTRA_ARGS[@]}")
      echo "  args               : ${BATCH_ARGS[*]}"
      python3 "$RUN_TASK_CLAUDE_PY" "${BATCH_ARGS[@]}" || {
        echo "  ✗ claude batch runner failed for $EXP_ID" >&2
        PREDICTIONS+=("$PRED_PATH")
        continue
      }
      EXECUTED="true"
    elif [[ -n "$RUN_CLAUDE_PILOT_SH" ]]; then
      echo "  runner             : $RUN_CLAUDE_PILOT_SH"
      PILOT_ARGS=(--model "$MODEL" --dataset "$DATASET_FILE" --tasks "$TASKS" --output-dir "$EXP_DIR_H")
      if [[ "$DARWIN" == "true" ]]; then PILOT_ARGS+=(--darwin); else PILOT_ARGS+=(--vanilla); fi
      echo "  args               : ${PILOT_ARGS[*]}"
      "$RUN_CLAUDE_PILOT_SH" "${PILOT_ARGS[@]}" || echo "  ✗ run_claude_pilot.sh failed for $EXP_ID" >&2
      for cand in "$EXP_DIR_H/predictions_vanilla.jsonl" "$EXP_DIR_H/predictions_darwin.jsonl" "$EXP_DIR_H/predictions.jsonl"; do
        if [[ -f "$cand" && ! -f "$PRED_PATH" ]]; then ln -sf "$cand" "$PRED_PATH"; fi
      done
      EXECUTED="true"
    else
      echo "  no claude runner found — documenting intended claude invocation"
      echo "  [dry-run] claude -p --model $MODEL --dangerously-skip-permissions --output-format stream-json \"<prompt>\" (darwin=$DARWIN harness=claude) dataset=$HF_DATASET tasks=$TASKS → $PRED_PATH"
      EXECUTED="true"
    fi
  elif [[ "$IS_HARNESS_PERTASK" == "true" && -n "$RUN_TASK_PY" ]]; then
    # Harness per-task loop (eval/harness/run_task.py)
    echo "  harness            : $RUN_TASK_PY (per-task, $TASKS tasks from $DATASET_FILE)"

    # Resolve instance ids (first TASKS from dataset file, or fallback)
    if [[ -f "$DATASET_FILE" ]]; then
      INSTANCE_IDS="$(python3 - "$DATASET_FILE" "$TASKS" <<'PY'
import json, sys
path, n = sys.argv[1], int(sys.argv[2])
data = json.loads(open(path).read())
ids = [r["instance_id"] for r in data[:n]]
print(" ".join(ids))
PY
)"
    else
      echo "  warn: dataset file not found at $DATASET_FILE — using synthetic instance_ids" >&2
      # synthetic fallback
      INSTANCE_IDS="$(python3 - "$TASKS" <<'PY'
import sys
n=int(sys.argv[1])
print(" ".join(f"synth__task-{i:03d}" for i in range(n)))
PY
)"
    fi
    NUM_IDS="$(echo "$INSTANCE_IDS" | wc -w | tr -d ' ')"
    echo "  instances          : $NUM_IDS"
    # Fresh predictions file per experiment
    : > "$PRED_PATH"
    # Workdir for this experiment (isolated to avoid cross-contamination but share repo cache)
    EXP_WORKDIR="$OUT_DIR/../workdir"  # share cache under eval/workdir
    mkdir -p "$EXP_WORKDIR"
    # Build common args for run_task.py
    COMMON_ARGS=(
      --model "$MODEL"
      --dataset "$DATASET_FILE"
      --output "$PRED_PATH"
      --workdir "$EXP_WORKDIR"
    )
    if [[ "$DARWIN" == "true" ]]; then COMMON_ARGS+=(--darwin); fi
    if [[ ${#FALLBACKS_ARR[@]} -gt 0 ]]; then
      COMMON_ARGS+=(--fallbacks "${FALLBACKS_ARR[@]}")
    fi
    # Always pass overlay so worktree config is single source of truth
    COMMON_ARGS+=(--opencode-json "$OPENCODE_OVERLAY")
    if [[ "$ECON_ROUTING" == "true" ]]; then
      COMMON_ARGS+=(--economics-routing)
      [[ -n "$ROUTING_POLICY" ]] && COMMON_ARGS+=(--routing-policy "$ROUTING_POLICY")
      [[ -n "$BUDGET" ]] && COMMON_ARGS+=(--budget "$BUDGET")
    fi
    COMMON_ARGS+=("${RUNNER_EXTRA_ARGS[@]}")

    FAILS=0
    IDX=0
    for IID in $INSTANCE_IDS; do
      IDX=$((IDX+1))
      echo "  [$IDX/$NUM_IDS] $IID …"
      set +e
      python3 "$RUN_TASK_PY" --instance-id "$IID" "${COMMON_ARGS[@]}" 2>&1 | sed 's/^/    | /'
      RC=${PIPESTATUS[0]}
      set -e
      if [[ $RC -ne 0 ]]; then
        echo "    warn: run_task for $IID exited $RC" >&2
        FAILS=$((FAILS+1))
        # Ensure fallback record if missing (run_task.py already writes, but guard)
        if ! grep -q "\"instance_id\": \"$IID\"" "$PRED_PATH" 2>/dev/null; then
          python3 - "$PRED_PATH" "$IID" "$MODEL" "$DARWIN" <<'PY'
import json, sys
p, iid, model, darwin = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4].lower()=="true"
rec={"instance_id": iid, "model": model, "model_name_or_path": model, "darwin": darwin, "patch": "", "model_patch":"", "cost": None, "tokens": None, "duration_s": 0, "status":"error_no_output"}
open(p,"a").write(json.dumps(rec)+"\n")
PY
        fi
      fi
      sleep 1
    done
    echo "  predictions        : $PRED_PATH  ($(wc -l < "$PRED_PATH" 2>/dev/null | tr -d ' ') tasks, $FAILS failures)"
    EXECUTED="true"

  elif [[ -n "$RUN_TASK_PY" ]]; then
    # Batch runner (e.g., alphadiana) — single invocation
    echo "  runner             : python3 $RUN_TASK_PY (batch)"
    BATCH_ARGS=(--model "$MODEL" --dataset "$HF_DATASET" --tasks "$TASKS" --out "$PRED_PATH")
    if [[ ${#FALLBACKS_ARR[@]} -gt 0 ]]; then BATCH_ARGS+=(--fallbacks "${FALLBACKS_ARR[@]}"); fi
    if [[ "$DARWIN" == "true" ]]; then BATCH_ARGS+=(--darwin); else BATCH_ARGS+=(--no-darwin); fi
    if [[ "$ECON_ROUTING" == "true" ]]; then
      BATCH_ARGS+=(--economics-routing)
      [[ -n "$ROUTING_POLICY" ]] && BATCH_ARGS+=(--routing-policy "$ROUTING_POLICY")
      [[ -n "$BUDGET" ]] && BATCH_ARGS+=(--budget "$BUDGET")
    fi
    BATCH_ARGS+=(--opencode-json "$OPENCODE_OVERLAY")
    BATCH_ARGS+=("${RUNNER_EXTRA_ARGS[@]}")
    echo "  args               : ${BATCH_ARGS[*]}"
    OPENCODE_CONFIG="$OPENCODE_OVERLAY" python3 "$RUN_TASK_PY" "${BATCH_ARGS[@]}" || {
      echo "  ✗ batch runner failed for $EXP_ID" >&2
      PREDICTIONS+=("$PRED_PATH")
      continue
    }
    EXECUTED="true"

  elif [[ -n "$RUN_PILOT_SH" ]]; then
    echo "  runner             : $RUN_PILOT_SH"
    PILOT_ARGS=(--model "$MODEL" --dataset "$DATASET_FILE" --tasks "$TASKS" --output-dir "$EXP_DIR")
    if [[ "$DARWIN" == "true" ]]; then PILOT_ARGS+=(--darwin); else PILOT_ARGS+=(--vanilla); fi
    echo "  args               : ${PILOT_ARGS[*]}"
    "$RUN_PILOT_SH" "${PILOT_ARGS[@]}" || echo "  ✗ run_pilot.sh failed for $EXP_ID" >&2
    # run_pilot writes to predictions_{vanilla,darwin}.jsonl — symlink to expected path
    for cand in "$EXP_DIR/predictions_vanilla.jsonl" "$EXP_DIR/predictions_darwin.jsonl" "$EXP_DIR/predictions.jsonl"; do
      if [[ -f "$cand" && ! -f "$PRED_PATH" ]]; then ln -sf "$cand" "$PRED_PATH"; fi
    done
    EXECUTED="true"
  else
    echo "  no runner found — documenting intended opencode invocation"
    echo "  [dry-run] opencode run --model $MODEL --opencode-json $OPENCODE_OVERLAY (darwin=$DARWIN) dataset=$HF_DATASET tasks=$TASKS → $PRED_PATH"
  fi

  # Attempt grading (per harness)
  if [[ -f "$PRED_PATH" ]]; then
    echo "  predictions        : $PRED_PATH  ($(wc -l < "$PRED_PATH" 2>/dev/null | tr -d ' ') tasks)"
    if [[ -n "$HARNESS_EVAL_PY" && -f "$HARNESS_EVAL_PY" ]]; then
      echo "  grading via eval_patch.py…"
      python3 "$HARNESS_EVAL_PY" --predictions "$PRED_PATH" --dataset "$DATASET_FILE" --output "$REPORT_PATH" || echo "  (eval_patch failed)" >&2
      if [[ -f "$REPORT_PATH" ]]; then echo "  report             : $REPORT_PATH"; fi
    elif command -v swebench >/dev/null 2>&1; then
      echo "  grading with swebench CLI…"
      case "$HF_DATASET" in
        *Verified*) SWEB_CMD="swebench eval verified" ;;
        *Lite*)     SWEB_CMD="swebench eval lite" ;;
        *)          SWEB_CMD="swebench eval verified" ;;
      esac
      # shellcheck disable=SC2086
      $SWEB_CMD -p "$PRED_PATH" --run-id "${EXP_ID}${HARNESS_SUFFIX}" -j 8 || echo "  (swebench eval failed)" >&2
      for cand in "evaluation_results/${EXP_ID}${HARNESS_SUFFIX}/report.json" "evaluation/${EXP_ID}${HARNESS_SUFFIX}/report.json" "eval/results/${EXP_ID}${HARNESS_SUFFIX}/report.json"; do
        if [[ -f "$cand" && ! -f "$REPORT_PATH" ]]; then cp "$cand" "$REPORT_PATH"; echo "  report             : $REPORT_PATH (copied from $cand)"; break; fi
      done
    else
      echo "  (no grading harness found — place report at $REPORT_PATH manually)"
    fi
  else
    echo "  ⚠ no predictions at $PRED_PATH — runner may have failed or writes elsewhere" >&2
  fi

  PREDICTIONS+=("$PRED_PATH")
  if [[ ${#HARNESS_LIST[@]} -gt 1 ]]; then
    LEGACY_PRED="$OUT_DIR/$EXP_ID/predictions${HARNESS_SUFFIX}.jsonl"
    mkdir -p "$(dirname "$LEGACY_PRED")"
    if [[ -f "$PRED_PATH" && ! -f "$LEGACY_PRED" ]]; then
      cp "$PRED_PATH" "$LEGACY_PRED" 2>/dev/null || true
    fi
    if [[ "$CURRENT_HARNESS" == "opencode" && ! -f "$OUT_DIR/$EXP_ID/predictions.jsonl" ]]; then
      cp "$PRED_PATH" "$OUT_DIR/$EXP_ID/predictions.jsonl" 2>/dev/null || true
    fi
  fi
  done # end CURRENT_HARNESS loop

done

# ---- comparison matrix -----------------------------------------------------
echo ""
echo "════════════════════════════════════════════════════════════════"
echo " Comparison matrix"
echo "════════════════════════════════════════════════════════════════"

COMPARISON_MD="$OUT_DIR/comparison.md"
COMPARISON_JSON="$OUT_DIR/comparison.json"

EXISTING=()
for p in "${PREDICTIONS[@]}"; do
  if [[ -f "$p" && -s "$p" ]]; then EXISTING+=("$p")
  else echo "  (no data) $p" >&2
  fi
done

if [[ -f "$COMPARE_PY" ]]; then
  if [[ ${#EXISTING[@]} -eq 0 ]]; then
    echo "(no predictions found — writing placeholder comparison)"
    mkdir -p "$(dirname "$COMPARISON_MD")"
    {
      echo "# Darwin comparison — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
      echo ""
      echo "No predictions found. Run without --dry-run or place predictions at:"
      for p in "${PREDICTIONS[@]}"; do echo "- \`$p\`"; done
      echo ""
      echo "Then re-run:"
      echo '```bash'
      echo "python3 $COMPARE_PY ${PREDICTIONS[*]} --out-md $COMPARISON_MD --out-json $COMPARISON_JSON"
      echo '```'
    } > "$COMPARISON_MD"
    echo "{}" > "$COMPARISON_JSON"
    cat "$COMPARISON_MD"
  else
    echo "running: python3 $COMPARE_PY ${EXISTING[*]} --out-md $COMPARISON_MD --out-json $COMPARISON_JSON --results-dir $OUT_DIR"
    python3 "$COMPARE_PY" "${EXISTING[@]}" --out-md "$COMPARISON_MD" --out-json "$COMPARISON_JSON" --results-dir "$OUT_DIR" || {
      echo "compare.py failed — falling back to simple table" >&2
      printf "%-28s %6s %8s %10s %8s %10s\n" "Experiment" "n" "Resolved" "Cost" "C/Res" "CacheHit"
      for p in "${EXISTING[@]}"; do
        id="$(basename "$(dirname "$p")")"
        n="$(wc -l < "$p" 2>/dev/null | tr -d ' ')"
        printf "%-28s %6s %8s %10s %8s %10s\n" "$id" "$n" "—" "—" "—" "—"
      done
      exit 0
    }
    echo ""
    echo "Wrote $COMPARISON_MD and $COMPARISON_JSON"
    echo ""
    cat "$COMPARISON_MD"
  fi
else
  echo "compare.py not found at $COMPARE_PY — simple table from predictions:"
  printf "%-28s %6s %8s\n" "Experiment" "n" "Predictions"
  for p in "${PREDICTIONS[@]}"; do
    id="$(basename "$(dirname "$p")")"
    if [[ -f "$p" ]]; then n="$(wc -l < "$p" 2>/dev/null | tr -d ' ')"; else n="—"; fi
    printf "%-28s %6s %8s\n" "$id" "$n" "$p"
  done
fi

echo ""
echo "Cost guidance (EVALUATION.md §3.2): Flash ~\$0.31/task (2M in + 0.1M out), free pool \$0."
echo "Done. Results root: $OUT_DIR"
