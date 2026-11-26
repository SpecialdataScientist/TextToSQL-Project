#!/bin/bash
set -euo pipefail

usage() {
  printf 'Usage: %s [request.json]\n' "$0"
  printf '  Default request: ../test_doc/embedding_based_schema.json relative to this script\n'
  printf '  BASE_URL    API server URL (default: http://localhost:31065)\n'
  printf '  OUTPUT_DIR  Response directory (default: tests/results relative to the project root)\n'
}

if [[ $# -gt 1 ]]; then
  usage >&2
  exit 1
fi
if [[ $# -eq 1 && ( "$1" == '-h' || "$1" == '--help' ) ]]; then
  usage
  exit 0
fi

for executable in curl jq date awk mkdir dirname; do
  if ! command -v "$executable" >/dev/null 2>&1; then
    printf 'Required command not found: %s\n' "$executable" >&2
    exit 1
  fi
done

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
input_file="${1:-${script_dir}/../test_doc/embedding_based_schema.json}"
if [[ ! -f "$input_file" ]]; then
  printf 'Input JSON file not found: %s\n' "$input_file" >&2
  exit 1
fi
if ! jq -e -s 'length == 1 and (.[0] | type == "object")' "$input_file" >/dev/null; then
  printf 'Input must contain exactly one JSON request object: %s\n' "$input_file" >&2
  exit 1
fi

base_url="${BASE_URL:-http://localhost:31065}"
output_dir="${OUTPUT_DIR:-${script_dir}/../results}"
endpoint="${base_url%/}/retrieve/embedding_based_schema"
raw_file="${output_dir%/}/embedding_based_schema_raw_1.json"
response_file="${output_dir%/}/embedding_based_schema_response_1.json"

mkdir -p -- "$output_dir"
: > "$raw_file"
: > "$response_file"

start=$(date +%s.%N)
finish() {
  local exit_status=$?
  local end runtime
  end=$(date +%s.%N)
  runtime=$(awk -v start="$start" -v end="$end" 'BEGIN {printf "%.3f", end - start}')
  printf '전체 요청 처리 시간: %s 초\n' "$runtime"
  exit "$exit_status"
}
trap finish EXIT

printf '[1] 요청 시각: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')"
printf '[1] 요청 파일: %s\n' "$input_file"
printf '[1] POST %s\n' "$endpoint"

if metrics=$(curl --silent --show-error \
  --connect-timeout 10 \
  --request POST \
  --header 'Content-Type: application/json' \
  --header 'Accept: application/json' \
  --data-binary "@$input_file" \
  --output "$raw_file" \
  --write-out '%{http_code} %{time_total}' \
  -- "$endpoint"); then
  read -r http_status request_time <<< "$metrics"
else
  curl_status=$?
  printf '[1] 요청 전송 실패 (curl exit %s). 원문: %s\n' "$curl_status" "$raw_file" >&2
  exit "$curl_status"
fi

printf '[1] HTTP %s, 응답 소요 시간: %ss\n' "$http_status" "$request_time"

if ! jq -e -s '
  if length == 1 and (.[0] | type == "object") then .[0]
  else error("Expected exactly one JSON response object") end
' "$raw_file" > "$response_file"; then
  printf '[1] 응답 JSON 파싱 실패. 원문: %s\n' "$raw_file" >&2
  exit 1
fi

printf '[1] 응답 저장: %s, %s\n' "$raw_file" "$response_file"
if [[ ! "$http_status" =~ ^2[0-9][0-9]$ ]]; then
  printf '[1] HTTP 오류: %s. 상세 응답: %s\n' "$http_status" "$response_file" >&2
  exit 1
fi
if ! jq -e '.status == "success"' "$response_file" >/dev/null; then
  printf '[1] API 오류. 상세 응답: %s\n' "$response_file" >&2
  exit 1
fi

if ! jq -e --slurpfile request "$input_file" '
  $request[0].top_k as $top_k | .data as $data |
  ($top_k | type == "number") and ($top_k >= 1) and ($top_k == ($top_k | floor))
  and ($data | type == "object")
  and ($data.table_name | type == "array")
  and ($data.schema | type == "array")
  and ($data.description | type == "array")
  and all(($data.table_name[], $data.schema[], $data.description[]); type == "string")
  and (($data.table_name | length) == ($data.schema | length))
  and (($data.table_name | length) == ($data.description | length))
  and (($data.table_name | length) <= $top_k)
' "$response_file" >/dev/null; then
  printf '[1] 검색 결과 형식 오류: 세 문자열 리스트의 길이와 top_k를 확인하세요. 상세 응답: %s\n' "$response_file" >&2
  exit 1
fi

jq -r --slurpfile request "$input_file" '
  "[1] 검색 성공: 요청 \($request[0].top_k)개, 반환 \(.data.table_name | length)개",
  (.data as $data | range(0; ($data.table_name | length)) as $i |
    "\n[\($i + 1)] table_name: \($data.table_name[$i])\ndescription: \($data.description[$i])\nschema:\n\($data.schema[$i])")
' "$response_file"
