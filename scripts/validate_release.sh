#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <release-dir>"
  exit 1
fi

python -m jvto_agent_runtime validate-release --release-dir "$1"
