#!/usr/bin/env bash
# Reproducible phase-2 technical sample: two public 2022 physics papers,
# 28 multiple-choice questions, conservative 2s single-concurrency rate.
set -euo pipefail

cd "$(dirname "$0")/../.."

.venv/bin/python src/main.py run \
  --max-papers 2 \
  --max-questions 30 \
  --paper-id 1688525155 \
  --paper-id 1686470481
