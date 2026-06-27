#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "Usage: $0 <knowledge-root> <core-root> <release-id>"
  exit 1
fi

python -m jvto_agent_runtime build-release \
  --knowledge-root "$1" \
  --core-root "$2" \
  --release-id "$3"
