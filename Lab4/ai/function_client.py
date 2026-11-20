"""--demo executes fixed REST calls; --ollama uses a local tool-calling model."""
import argparse
import json
import os
from urllib.parse import urlsplit

import httpx

from time_tools import EMAIL, POLICY, TOOLS, GuardrailError, TimeTools, ToolChain, check_prompt, integer

DEFAULT_PROMPT = ("Find student5@example.com, read their current report, and create one "
                  "600-second session only if their session limit permits. Show the session status and projected total.")
DEFAULT_MODEL = 'qwen3:8b-q4_K_M'


class Budget:
    def __init__(self, total=20000, requests=4, output=512):
        self.total = integer(total, "total token budget")
        self.requests = integer(requests, "request budget", maximum=10)
        self.output = integer(output, "num_predict", minimum=32, maximum=2048)
        self.used = 0

    def reserve(self, payload, context=8192):
        # Conservative byte estimate; template/tokenizer overhead is not exact.
        estimated_input = len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 512
        if estimated_input + self.output > context:
            raise GuardrailError('Estimated input/output exceeds the context budget')
        if self.requests <= 0 or self.used + estimated_input + self.output > self.total:
            raise GuardrailError("Token/request budget exhausted before API request")
        self.requests -= 1

    def record(self, usage):
        self.used += integer(usage.get('prompt_eval_count'), 'prompt_eval_count', minimum=0)
        self.used += integer(usage.get('eval_count'), 'eval_count', minimum=0)
        if self.used > self.total:
            raise GuardrailError("Actual token usage exceeded the estimate; stopping")


def demo(tools):
    print("DETERMINISTIC DEMO: no LLM request; real REST tools are executed.")
    chain = ToolChain(tools)
    user = chain.call("find_user", {"email": EMAIL})
    print("find_user", json.dumps(user))
    report = chain.call("get_report", {"user_id": user["id"]})
    print("get_report", json.dumps(report))
    session = chain.call("create_session", {"user_id": user["id"], "duration_seconds": 600})
    print("create_session", json.dumps(session))
    return session


def local_server_url(value):
    url = urlsplit(value)
    if (url.scheme != 'http' or url.hostname not in {'localhost', '127.0.0.1', 'host.docker.internal'}
            or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment):
        raise GuardrailError('Ollama must use a local HTTP origin, without credentials or a path')
    return value.rstrip('/')


def ollama_json(client, method, path, body=None):
    with client.stream(method, path, json=body) as response:
        if response.status_code == 404:
            raise GuardrailError('Local Ollama endpoint/model not found; install the model with ollama pull yourself')
        response.raise_for_status()
        raw = bytearray()
        for chunk in response.iter_bytes():
            raw.extend(chunk)
            if len(raw) > 65536:
                raise GuardrailError('Ollama response exceeds 64 KiB')
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('error'):
        raise GuardrailError('Ollama returned an invalid response or an error')
    return data


def live(tools, prompt, model, budget, tool_limit=3, context=8192):
    check_prompt(prompt)
    if model != DEFAULT_MODEL:
        raise GuardrailError(f'This lab permits only the local model {DEFAULT_MODEL}')
    integer(context, 'num_ctx', minimum=2048, maximum=8192)
    origin = local_server_url(os.getenv('OLLAMA_BASE_URL', 'http://127.0.0.1:11434'))
    chain = ToolChain(tools, tool_limit)
    instruction = (POLICY + ' Call exactly one tool per response: find_user, then get_report, '
                   'then create_session. After all three calls, summarize their actual outputs; '
                   'do not call more tools.')
    history = [{'role': 'system', 'content': instruction}, {"role": "user", "content": prompt}]
    # No redirects, environment proxies, downloads, or automatic retries.
    with httpx.Client(base_url=origin, timeout=httpx.Timeout(180, connect=5),
                      follow_redirects=False, trust_env=False) as client:
        version = ollama_json(client, 'GET', '/api/version')
        installed = ollama_json(client, 'GET', '/api/tags').get('models', [])
        if not isinstance(installed, list) or not any(m.get('name') == model for m in installed if isinstance(m, dict)):
            raise GuardrailError(f'Model is not installed; run ollama pull {model} yourself')
        metadata = ollama_json(client, 'POST', '/api/show', {'model': model})
        if 'tools' not in metadata.get('capabilities', []):
            raise GuardrailError('Installed model does not advertise tool calling')
        print(json.dumps({'ollama_version': version.get('version'), 'model': model,
                          'server': origin, 'num_ctx': context, 'think': False}))
        while True:
            payload = {"model": model, 'messages': history, "tools": TOOLS,
                       'stream': False, 'think': False, 'keep_alive': '10m',
                       'options': {'num_ctx': context, 'num_predict': budget.output,
                                   'temperature': 0, 'seed': 5}}
            budget.reserve(payload, context)
            response = ollama_json(client, 'POST', '/api/chat', payload)
            budget.record(response)
            print(json.dumps({'model': model, 'prompt_eval_count': response.get('prompt_eval_count'),
                              'eval_count': response.get('eval_count'),
                              "cumulative_tokens": budget.used}))
            if response.get('done') is not True or response.get('done_reason') != 'stop':
                raise GuardrailError('Ollama generation was incomplete or hit num_predict; no tool will be executed')
            message = response.get('message')
            if not isinstance(message, dict) or message.get('role') != 'assistant':
                raise GuardrailError('Ollama response has no assistant message')
            content = message.get('content', '')
            if not isinstance(content, str):
                raise GuardrailError('Ollama assistant content must be text')
            calls = message.get('tool_calls', [])
            if not isinstance(calls, list):
                raise GuardrailError('Ollama tool_calls must be a list')
            history.append(message)
            if not calls:
                if chain.calls != 3 or not content.strip():
                    raise GuardrailError("Model did not complete the required tool chain")
                print(content)
                return content
            if len(calls) != 1:
                raise GuardrailError("Only one tool call per model response is permitted")
            function = calls[0].get('function') if isinstance(calls[0], dict) else None
            if not isinstance(function, dict) or not isinstance(function.get('name'), str):
                raise GuardrailError('Ollama tool call has no valid function')
            result = chain.call(function['name'], function.get('arguments'))
            print(function['name'], json.dumps(result))
            history.append({'role': 'tool', 'tool_name': function['name'],
                            'content': json.dumps(result)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true")
    mode.add_argument("--ollama", action="store_true")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=[DEFAULT_MODEL])
    parser.add_argument("--total-tokens", type=int, default=20000)
    parser.add_argument("--requests", type=int, default=4)
    parser.add_argument("--max-output-tokens", type=int, default=512)
    parser.add_argument("--tool-limit", type=int, default=3)
    parser.add_argument('--context', type=int, default=8192)
    args = parser.parse_args()
    tools = TimeTools(request_limit=12)
    try:
        if args.demo:
            demo(tools)
        else:
            live(tools, args.prompt, args.model,
                 Budget(args.total_tokens, args.requests, args.max_output_tokens), args.tool_limit, args.context)
    except (GuardrailError, ValueError, httpx.HTTPError) as error:
        parser.exit(2, f"Stopped: {error}\n")
    finally:
        tools.close()


if __name__ == "__main__":
    main()
