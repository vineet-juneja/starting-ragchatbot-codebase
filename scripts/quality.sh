#!/bin/bash
# One-shot developer check: apply formatting, run lint checks, then the test suite.
# Run from the repo root. On Windows, use Git Bash.
set -e

cd "$(dirname "$0")/.."

./scripts/format.sh
echo
./scripts/lint.sh
echo
echo "==> pytest"
uv run pytest

echo
echo "==> Quality gate complete."
