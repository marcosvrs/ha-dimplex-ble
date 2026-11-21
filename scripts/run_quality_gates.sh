#!/usr/bin/env bash
set -euo pipefail

if [[ ! -x .venv/bin/pre-commit ]]; then
  echo "Install test dependencies first: uv pip install --python .venv/bin/python -r requirements-test.txt" >&2
  exit 1
fi

.venv/bin/pre-commit run --all-files
.venv/bin/pytest \
  --cov=custom_components/dimplex_ble \
  --cov-report=term-missing \
  --cov-report=json:coverage.json
.venv/bin/python scripts/check_function_coverage.py coverage.json
if .venv/bin/mutmut run; then
  :
else
  mutation_status=$?
  if (( mutation_status != 1 )); then
    exit "$mutation_status"
  fi
fi
.venv/bin/python scripts/check_mutation_score.py
