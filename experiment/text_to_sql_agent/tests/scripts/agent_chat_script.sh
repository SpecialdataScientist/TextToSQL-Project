#!/bin/bash
set -euo pipefail

usage() {
  printf 'Usage: %s [request.json]\n' "$0"
  printf '  Default request: tests/test_doc/agent_chat.json relative to the Agent project root\n'
  printf '  BASE_URL    Agent server URL (default: http://localhost:31068)\n'
  printf '  OUTPUT_DIR  Response directory (default: tests/results relative to the Agent project root)\n'
  printf '  Requires curl and jq.\n'
}

if [[ $# -gt 1 ]]; then
  usage >&2
  exit 1
fi
if [[ $# -eq 1 && ( "$1" == '-h' || "$1" == '--help' ) ]]; then
  usage
  exit 0
fi

for executable in curl jq; do
  if ! command -v "$executable" >/dev/null 2>&1; then
    printf '필요한 명령어를 찾을 수 없습니다: %s\n' "$executable" >&2
    exit 1
  fi
done

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
input_file="${1:-${script_dir}/../test_doc/agent_chat.json}"
if [[ ! -f "$input_file" || ! -r "$input_file" ]]; then
  printf '요청 파일을 읽을 수 없습니다: %s\n' "$input_file" >&2
  exit 1
fi
if ! jq -e -s '
  length == 1 and (.[0] | type == "object" and
    (.query | type == "string" and test("[^[:space:]]")))
' < "$input_file" > /dev/null; then
  printf '요청 파일은 비어 있지 않은 query 문자열을 가진 JSON 객체 한 개여야 합니다: %s\n' "$input_file" >&2
  exit 1
fi

base_url="${BASE_URL:-http://localhost:31068}"
output_dir="${OUTPUT_DIR:-${script_dir}/../results}"
endpoint="${AGENT_CHAT_ENDPOINT:-${base_url%/}/agent/chat}"
raw_file="${AGENT_CHAT_RAW_FILE:-${output_dir%/}/agent_chat_raw_1.json}"
response_file="${AGENT_CHAT_RESPONSE_FILE:-${output_dir%/}/agent_chat_response_1.json}"

if [[ "$input_file" -ef "$raw_file" || "$input_file" -ef "$response_file" ]]; then
  printf '요청 파일과 응답 저장 파일의 경로가 겹칩니다: %s\n' "$input_file" >&2
  exit 1
fi
if [[ "$raw_file" == "$response_file" || "$raw_file" -ef "$response_file" ]]; then
  printf '응답 원문과 정리된 JSON의 저장 경로가 겹칩니다: %s\n' "$raw_file" >&2
  exit 1
fi
mkdir -p -- "$(dirname -- "$raw_file")" "$(dirname -- "$response_file")"
rm -f -- "$response_file"
: > "$raw_file"

printf '[1] 요청 파일: %s\n' "$input_file"
printf '[1] POST %s\n' "$endpoint"
if metrics=$(curl --disable --silent --show-error --globoff \
  --connect-timeout 10 --max-time 200 \
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
  printf '[1] 요청 전송 실패 (curl 종료 코드 %s). 응답 원문: %s\n' "$curl_status" "$raw_file" >&2
  exit "$curl_status"
fi

printf '[1] HTTP %s, 응답 소요 시간: %s초\n' "$http_status" "$request_time"
if ! jq -e -s '
  if length == 1 and (.[0] | type == "object") then .[0]
  else error("응답은 JSON 객체 한 개여야 합니다.") end
' < "$raw_file" > "$response_file"; then
  rm -f -- "$response_file"
  printf '[1] 응답 JSON 파싱 실패. 응답 원문: %s\n' "$raw_file" >&2
  exit 1
fi

printf '[1] 응답 저장: %s, %s\n' "$raw_file" "$response_file"
if [[ ! "$http_status" =~ ^2[0-9][0-9]$ ]]; then
  printf '[1] HTTP 오류: %s. 상세 응답: %s\n' "$http_status" "$response_file" >&2
  exit 1
fi
if ! jq -e '.status == "success"' < "$response_file" > /dev/null; then
  printf '[1] Agent 오류. 상세 응답: %s\n' "$response_file" >&2
  exit 1
fi

jq -er '
  .data
  | if (.answer | type) == "string" and (.tool_calls | type) == "array" then
      "[1] 답변:\n\(.answer)",
      "[1] 도구 호출: \(.tool_calls | length)건 (오류 \(.tool_calls | map(select(.is_error? == true)) | length)건)"
    else error("응답에 답변 문자열과 도구 호출 배열이 필요합니다.") end
' < "$response_file"
