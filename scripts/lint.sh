#!/bin/bash
# Check code quality without modifying files (CI-style).
# Fails if formatting is not applied or flake8 finds issues.
# Run from the repo root. On Windows, use Git Bash.
set -e

cd "$(dirname "$0")/.."

echo "==> isort --check-only"
uv run isort --check-only --diff backend tests main.py

echo "==> black --check"
uv run black --check --diff backend tests main.py

echo "==> flake8"
uv run flake8 backend tests main.py

echo "==> All checks passed."
