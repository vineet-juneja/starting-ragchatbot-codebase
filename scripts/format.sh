#!/bin/bash
# Apply automatic code formatting: sort imports, then format with black.
# Run from the repo root. On Windows, use Git Bash.
set -e

cd "$(dirname "$0")/.."

echo "==> isort (sorting imports)"
uv run isort backend tests main.py

echo "==> black (formatting)"
uv run black backend tests main.py

echo "==> Done. Code formatted."
