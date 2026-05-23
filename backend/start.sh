#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="backend/.deps:backend"
python3 backend/run.py
