#!/usr/bin/env bash
# setup.sh — verify and prepare darwin evaluation harness environment
# Checks: docker, python, opencode, installs swebench pip package if needed,
# pulls dataset, verifies darwin plugin builds.
#
# Usage: ./eval/scripts/setup.sh [--check-only]
#   --check-only  only check, don't install anything
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
EVAL_DIR="${REPO_ROOT}/eval"
DATASET_FILE="${EVAL_DIR}/datasets/lite_50.json"
FULL_CACHE="/tmp/swe_lite.json"

CHECK_ONLY=0
if [[ "${1:-}" == "--check-only" ]]; then
  CHECK_ONLY=1
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC} $*"; }
fail() { echo -e "${RED}✗${NC} $*"; }

echo "== darwin eval setup =="
echo "repo root: ${REPO_ROOT}"
echo ""

# 1. python
echo "-- python --"
if command -v python3 >/dev/null 2>&1; then
  PY_VER="$(python3 --version 2>&1)"
  pass "${PY_VER} at $(command -v python3)"
  # Check python -c
  if python3 -c "import sys; print(sys.version)" >/dev/null 2>&1; then
    pass "python3 -c works"
  else
    fail "python3 -c failed"
  fi
else
  fail "python3 not found (required)"
  if [[ ${CHECK_ONLY} -eq 1 ]]; then exit 1; fi
fi

# 2. docker (optional but recommended)
echo ""
echo "-- docker --"
if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    DOCKER_VER="$(docker --version 2>&1)"
    pass "${DOCKER_VER} (daemon running)"
    # Check disk
    if command -v df >/dev/null 2>&1; then
      FREE_GB=$(df -BG "${REPO_ROOT}" 2>/dev/null | awk 'NR==2{gsub(/G/,"",$4); print $4}' || echo "?")
      echo "  disk free at repo: ${FREE_GB}G (≥120G recommended for full SWE-bench)"
      if [[ "${FREE_GB}" != "?" ]] && [[ "${FREE_GB}" -lt 20 ]]; then
        warn "low disk — full SWE-bench needs ≥120G; pilot (50 tasks) needs ~10G"
      fi
    fi
  else
    warn "docker installed but daemon not running (degraded eval will still work)"
    echo "  hint: systemctl start docker  or  open Docker Desktop"
  fi
else
  warn "docker not found — eval will use degraded 'patch applies' check (no test execution)"
  echo "  hint: https://docs.docker.com/engine/install/"
fi

# 3. opencode
echo ""
echo "-- opencode --"
if command -v opencode >/dev/null 2>&1; then
  OPENCODE_VER="$(opencode --version 2>&1 || echo 'unknown')"
  pass "opencode ${OPENCODE_VER} at $(command -v opencode)"
  # List models (non-fatal)
  if opencode models list >/dev/null 2>&1; then
    pass "opencode models list works"
  else
    warn "opencode models list failed (may need 'opencode auth login' for Zen models)"
  fi
else
  fail "opencode not found (required for harness)"
  echo "  install: curl -fsSL https://opencode.ai/install | bash"
  echo "  docs: https://opencode.ai/docs"
  if [[ ${CHECK_ONLY} -eq 1 ]]; then exit 1; fi
  exit 1
fi

# 4. swebench pip package (optional — enables real test execution)
echo ""
echo "-- swebench package --"
if python3 -c "import swebench" >/dev/null 2>&1; then
  SB_VER="$(python3 -c "import swebench; print(getattr(swebench,'__version__','unknown'))" 2>&1 || echo unknown)"
  pass "swebench ${SB_VER} already installed"
else
  warn "swebench not installed — real test execution unavailable (degraded mode)"
  if [[ ${CHECK_ONLY} -eq 0 ]]; then
    echo "  installing swebench (pip install swebench)…"
    # Use pip3 if available else python3 -m pip
    if command -v pip3 >/dev/null 2>&1; then
      PIP="pip3"
    elif python3 -m pip --version >/dev/null 2>&1; then
      PIP="python3 -m pip"
    else
      warn "pip not found — skipping swebench install (degraded mode)"
      PIP=""
    fi
    if [[ -n "${PIP}" ]]; then
      if ${PIP} install swebench --quiet 2>&1 | tail -20; then
        if python3 -c "import swebench" >/dev/null 2>&1; then
          pass "swebench installed successfully"
        else
          warn "pip install swebench finished but import still fails"
        fi
      else
        warn "pip install swebench failed — continuing in degraded mode"
      fi
    fi
  else
    echo "  (check-only: skipping install; run without --check-only to install)"
  fi
fi

# 5. dataset
echo ""
echo "-- dataset --"
if [[ -f "${DATASET_FILE}" ]]; then
  COUNT=$(python3 -c "import json; print(len(json.load(open('${DATASET_FILE}'))))" 2>&1 || echo "?")
  pass "lite_50.json exists (${COUNT} tasks) at ${DATASET_FILE}"
else
  fail "lite_50.json missing at ${DATASET_FILE}"
  echo "  hint: git pull or check eval/datasets/"
fi

# Try to fetch full SWE-bench Lite (for problem_statement enrichment) to /tmp cache
# This is optional — run_task.py can fetch per-instance via HF API, but caching speeds pilot
if [[ ! -f "${FULL_CACHE}" ]]; then
  echo "  fetching SWE-bench Lite full dataset to cache (for offline problem_statement)…"
  if python3 -c "
import urllib.request, json
try:
    all_rows=[]
    for offset in (0,100,200):
        url=f'https://datasets-server.huggingface.co/rows?dataset=princeton-nlp%2FSWE-bench_Lite&config=default&split=test&offset={offset}&length=100'
        import urllib.request, json
        with urllib.request.urlopen(url, timeout=15) as r:
            data=json.loads(r.read().decode())
            for row in data.get('rows', []):
                all_rows.append(row['row'])
    open('/tmp/swe_lite.json','w').write(json.dumps(all_rows))
    print(f'cached {len(all_rows)} instances to /tmp/swe_lite.json')
except Exception as e:
    print(f'cache fetch failed: {e}')
    raise SystemExit(1)
" 2>&1 | tail -5; then
    pass "cached full dataset to ${FULL_CACHE}"
  else
    warn "could not cache full dataset (offline?) — run_task will try per-instance HF fetch or use placeholder prompt"
  fi
else
  COUNT_FULL=$(python3 -c "import json; print(len(json.load(open('${FULL_CACHE}'))))" 2>&1 || echo "?")
  pass "full cache exists (${COUNT_FULL} instances) at ${FULL_CACHE}"
fi

# Verify darwin plugin builds
echo ""
echo "-- darwin plugin --"
if [[ -f "${REPO_ROOT}/package.json" ]]; then
  if command -v npm >/dev/null 2>&1; then
    # Quick typecheck if tsc available; else npm run typecheck
    if [[ -f "${REPO_ROOT}/packages/opencode/package.json" ]]; then
      if [[ -d "${REPO_ROOT}/packages/opencode/node_modules" ]]; then
        pass "opencode plugin node_modules exists"
      else
        warn "opencode plugin node_modules missing"
        if [[ ${CHECK_ONLY} -eq 0 ]]; then
          echo "  running npm install…"
          (cd "${REPO_ROOT}" && npm install --silent 2>&1 | tail -10 || warn "npm install failed")
        fi
      fi
      # Check that plugin entry exists
      if [[ -f "${REPO_ROOT}/packages/opencode/src/index.ts" ]]; then
        pass "darwin plugin source at packages/opencode/src/index.ts"
      fi
      if [[ -f "${REPO_ROOT}/packages/core/src/economics.ts" ]]; then
        pass "darwin core at packages/core/src/economics.ts"
      fi
      # Try typecheck (non-blocking)
      if command -v npx >/dev/null 2>&1; then
        if (cd "${REPO_ROOT}" && npx tsc -p tsconfig.json --noEmit 2>&1 | head -20); then
          pass "typescript typecheck passes"
        else
          warn "typescript typecheck warnings (see above)"
        fi
      fi
    fi
  else
    warn "npm not found — skipping plugin build check"
  fi
else
  warn "package.json not found at repo root"
fi

# 6. harness scripts executable
echo ""
echo "-- harness scripts --"
for f in "${EVAL_DIR}/harness/run_task.py" "${EVAL_DIR}/harness/eval_patch.py" "${EVAL_DIR}/scripts/run_pilot.sh" "${EVAL_DIR}/scripts/setup.sh"; do
  if [[ -f "${f}" ]]; then
    chmod +x "${f}" 2>/dev/null || true
    pass "$(basename "${f}") exists and executable"
    # Quick syntax check
    if [[ "${f}" == *.py ]]; then
      if python3 -m py_compile "${f}" 2>&1; then
        pass "  syntax ok"
      else
        fail "  syntax error in ${f}"
      fi
    elif [[ "${f}" == *.sh ]]; then
      if bash -n "${f}" 2>&1; then
        pass "  syntax ok"
      else
        fail "  syntax error in ${f}"
      fi
    fi
  else
    fail "${f} missing"
  fi
done

# 7. python3 -c checks (as required by spec: use python3 -c)
echo ""
echo "-- python3 -c sanity checks --"
if python3 -c "import json, pathlib, subprocess, tempfile; print('std deps ok')" 2>&1; then pass "stdlib deps ok"; else fail "stdlib deps failed"; fi
if python3 -c "import urllib.request; print('urllib ok')" 2>&1; then pass "urllib ok"; else warn "urllib failed"; fi

echo ""
echo "== setup complete =="
if docker info >/dev/null 2>&1 && python3 -c "import swebench" >/dev/null 2>&1; then
  echo -e "${GREEN}Full mode:${NC} docker + swebench available — real test execution enabled"
elif docker info >/dev/null 2>&1; then
  echo -e "${YELLOW}Degraded mode:${NC} docker yes, swebench no — install swebench for real tests (pip install swebench)"
else
  echo -e "${YELLOW}Degraded mode:${NC} docker not available — 'patch applies cleanly' check only"
  echo "  To enable full eval: start docker and 'pip install swebench'"
fi
echo ""
echo "Next: ./eval/scripts/run_pilot.sh --help  or  ./eval/scripts/run_pilot.sh --both"
