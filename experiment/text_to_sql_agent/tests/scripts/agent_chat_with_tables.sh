#!/bin/bash
set -euo pipefail

usage() {
  printf 'Usage: %s\n' "$0"
  printf '  Sends tests/test_doc/agent_chat_with_tables.json to /agent/chat using curl and jq.\n'
  printf '  BASE_URL    Agent server URL (default: http://localhost:31068)\n'
  printf '  OUTPUT_DIR  Response directory (default: tests/results/with_tables relative to the Agent project root)\n'
}

if [[ $# -eq 1 && ( "$1" == '-h' || "$1" == '--help' ) ]]; then
  usage
  exit 0
fi
if [[ $# -ne 0 ]]; then
  usage >&2
  exit 1
fi

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
base_url="${BASE_URL:-http://localhost:31068}"
output_dir="${OUTPUT_DIR:-${project_dir}/tests/results/with_tables}"
endpoint="${base_url%/}/agent/chat"
raw_file="${output_dir%/}/agent_chat_raw_1.json"
response_file="${output_dir%/}/agent_chat_response_1.json"

AGENT_CHAT_ENDPOINT="$endpoint" AGENT_CHAT_RAW_FILE="$raw_file" AGENT_CHAT_RESPONSE_FILE="$response_file" \
  exec bash "${project_dir}/tests/scripts/agent_chat_script.sh" \
    "${project_dir}/tests/test_doc/agent_chat_with_tables.json"
