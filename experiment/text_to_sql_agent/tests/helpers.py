from copy import deepcopy
import json

import httpx
from openai import AsyncOpenAI


def tool_call(name="call_mcp_tool", arguments=None, call_id="call_1"):
    return {
        "id": call_id, "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments or {}, ensure_ascii=False),
        },
    }


def completion(content=None, calls=None, finish_reason=None, refusal=None):
    message = {"role": "assistant", "content": content, "refusal": refusal}
    if calls:
        message["tool_calls"] = calls
    return {
        "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "test-model",
        "choices": [{"index": 0, "finish_reason": finish_reason or ("tool_calls" if calls else "stop"),
                     "message": message}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
    }


def query_call(value="DLC001", *, call_id="query_1", limit=1):
    return tool_call(arguments={
        "name": "get_fclt_org_dlc_key_dlc_key",
        "arguments": {"filters": {"DLC_KEY": value}, "limit": limit},
    }, call_id=call_id)


class ScriptedLlm:
    """Exercise real OpenAI SDK serialization using an HTTP mock transport."""

    def __init__(self, responses):
        self.responses = responses
        self.requests = []
        self.clients = []

    async def respond(self, request):
        body = json.loads(request.content)
        self.requests.append({"body": deepcopy(body), "url": str(request.url), "auth": request.headers.get("authorization")})
        if callable(self.responses):
            response = await self.responses(body)
        else:
            response = self.responses.pop(0)
        return httpx.Response(200, json=response)

    def factory(self, settings, profile):
        http_client = httpx.AsyncClient(transport=httpx.MockTransport(self.respond))
        self.clients.append(http_client)
        return AsyncOpenAI(
            base_url=profile.base_url, api_key=profile.api_key, http_client=http_client,
            timeout=settings.llm_timeout_seconds, max_retries=0,
        )
