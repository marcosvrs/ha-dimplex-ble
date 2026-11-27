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
if [[ "${SKIP_MUTATION:-0}" == "1" ]]; then
    if [[ "${REQUIRE_MUTATION_SCORE:-0}" == "1" ]]; then
        echo "SKIP_MUTATION=1 can't be combined with REQUIRE_MUTATION_SCORE=1." >&2
        exit 1
    fi
    echo "Mutation testing skipped (SKIP_MUTATION=1); it runs on main." >&2
    exit 0
fi
# A stale mutants/ copy made mutmut test old code with new tests (every run
# failed with "failed to collect stats", November 2026): always start fresh.
rm -rf mutants
if .venv/bin/mutmut run; then
    :
else
    mutation_status=$?
    if (( mutation_status != 1 )); then
        exit "$mutation_status"
    fi
fi
if [[ "${REQUIRE_MUTATION_SCORE:-0}" == "1" ]]; then
    .venv/bin/python scripts/check_mutation_score.py
elif ! .venv/bin/python scripts/check_mutation_score.py; then
    echo "Mutation score is advisory; set REQUIRE_MUTATION_SCORE=1 to enforce it." >&2
fi
