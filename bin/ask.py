"""The LLM client for this project: one chat call, with retries and accounting.

Five modules import it - `segment.py`, `name_speakers.py`, `redact.py`,
`web/agent.py` and `web/server.py` - and they all want the same four things:
an API key read from the environment, a call that retries the failures worth
retrying, a raw variant that hands back the whole reply so `tool_calls` is
visible, and a running total of what the run has spent.

**The fixed question-answering pipeline that used to live here is deleted.**
It was PLAN -> RETRIEVE -> READ -> ANSWER: one call turned the question into
several queries, hybrid search ran them, batches of passages went to the model
in parallel to be kept or discarded, and a last call wrote the answer. D9
replaced it, and the reason is worth keeping even though the code is not: the
planner emitted its queries once and the rest executed them blindly. A vote
passage contains no topic words, so the planner's own wording put the
school-zone vote at rank 33-58 while READ only ever saw the top 30 - and
`retrieve.decisions_in_play()` was a hard-coded patch over that single case.

What replaced it decides what to look at next instead of being wired to:
`web/tools.py` is the surface, `web/agent.py` is the loop, and slice 4 put it
behind `/api/ask`. The agent reaches that same vote by choosing to call
`get_item` once a search puts the item in play.

The pipeline outlived its last real caller by a while. `bin/eval_agent.py
--agent` still ran it, which meant the project's pass/fail check for "can we
find the moment the board decided" was measuring a code path no reader could
reach. That eval runs `web/agent.py` now.

TOKEN ACCOUNTING lives here rather than in the callers because it is the only
place this project measures what it spends: `usage_report()` counts prompt
tokens split by cache hit and miss, and completion tokens including the
reasoning the caller never sees. Output bills several times higher than input,
so a report that omits it is not an estimate, it is wrong.
"""
import http.client
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.request


API_BASE = os.environ.get("INFERENCE_API_BASE") or "https://api.deepseek.com"
MODEL = os.environ.get("LLM_MODEL") or "deepseek-v4-flash"
MODEL_HEAVY = os.environ.get("LLM_MODEL_HEAVY") or "deepseek-v4-flash"


class MissingKey(RuntimeError):
    """No inference key in the environment.

    A RuntimeError and NOT SystemExit, which is what this raised for months.
    SystemExit does not inherit from Exception, so `except Exception` - the
    guard around every request in web/server.py - does not catch it. In a
    ThreadingHTTPServer it unwound the request thread silently instead: the
    SSE connection stayed open, the reader watched "thinking" for ever, and
    the server logged nothing. A library imported by a server may not decide
    to exit the process; it reports, and the caller decides.
    """


def api_key():
    for k in ("LLM_API_KEY", "INFERENCE_API_KEY", "DEEPSEEK_API_KEY"):
        if os.environ.get(k):
            return os.environ[k]
    raise MissingKey(
        "No inference key. Start this process with the archive's env file "
        "sourced:  source env.local.sh  (or run it through bin/_env.sh), "
        "which defines LLM_API_KEY.")


# Prefix-cache accounting. Cache hits are an order of magnitude cheaper, but
# only when the prefix is byte-identical, so this is tracked rather than
# assumed - a stray timestamp in a system prompt silently costs full price on
# every call and nothing in the output would reveal it.
USAGE = {"calls": 0, "cache_hit": 0, "cache_miss": 0, "completion": 0,
         "reasoning": 0}


# The response is not streamed, so the socket sits silent for the whole
# generation and `timeout` is really "how long may one call take". A whole-day
# segmentation prompt is ~35k tokens in and a long structured plan out: the
# MEDIAN measured call is 158s. The old 180s default was therefore tuned to the
# happy path - half of all meeting-days timed out, retried three times at 180s
# each, and failed after nine minutes having thrown away three paid-for
# completions. Short calls pass a smaller timeout rather than the reverse.
TIMEOUT = 600
_USAGE_LOCK = threading.Lock()
SLOW_CALL = 240   # log anything slower, so a creeping model never hides again

# Everything that means "the network let us down", which is worth another
# attempt. The list is wider than it looks it should be because the body is
# read lazily by json.load(): a connection dropped mid-response surfaces from
# deep inside http.client as IncompleteRead or ChunkedEncodingError, neither of
# which is a URLError. Catching only URLError killed a whole name_speakers run
# on one truncated response.
RETRYABLE = (urllib.error.URLError, TimeoutError, ConnectionError, OSError,
             http.client.HTTPException, json.JSONDecodeError, KeyError)


def chat(messages, model=MODEL, temperature=0.2, as_json=False, retries=3,
         timeout=TIMEOUT, effort=None):
    """The text of one reply. Four callers depend on this exact signature."""
    return chat_raw(messages, model, temperature, as_json, retries,
                    timeout, effort=effort).get("content") or ""


def chat_raw(messages, model=MODEL, temperature=0.2, as_json=False, retries=3,
             timeout=TIMEOUT, tools=None, tool_choice=None, effort=None):
    """The whole reply MESSAGE, so a caller can see `tool_calls`.

    Added for the agent (web/agent.py): a tool-calling loop needs the message
    back, not the string, because the interesting turns have no content at all.
    `chat()` stays as it was - segment.py and name_speakers.py call it several
    thousand times a run and neither wants a dict.

    `effort` is 'none' | 'low' | 'medium' | 'high', or None to send nothing and
    let the model do what it did before this argument existed. It is the only
    control here that touches WALL CLOCK, because it is the only one that
    changes how much gets generated: measured on one /ask question, 93% of
    every token this project generated was reasoning nobody reads, and
    generation is serial where a re-sent prompt is a 92% cache hit. Prompt
    size is not what makes an answer slow. This is.
    """
    body = {"model": model, "messages": messages, "temperature": temperature}
    if effort:
        body["reasoning_effort"] = effort
    if as_json:
        body["response_format"] = {"type": "json_object"}
    if tools:
        body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice
    payload = json.dumps(body).encode()
    last = None
    for attempt in range(retries):
        # Rebuild per attempt: a Request that has already been opened carries
        # host/redirect state, and reusing it across retries is not defined.
        req = urllib.request.Request(
            f"{API_BASE}/v1/chat/completions", data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key()}"})
        began = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.load(r)
            u = d.get("usage") or {}
            with _USAGE_LOCK:      # 12 segment threads share this dict
                USAGE["calls"] += 1
                USAGE["cache_hit"] += u.get("prompt_cache_hit_tokens", 0)
                USAGE["cache_miss"] += u.get("prompt_cache_miss_tokens", 0)
                USAGE["completion"] += u.get("completion_tokens", 0)
                USAGE["reasoning"] += (
                    (u.get("completion_tokens_details") or {})
                    .get("reasoning_tokens", 0))
            spent = time.monotonic() - began
            if spent > SLOW_CALL:
                print(f"  slow LLM call: {spent:.0f}s  "
                      f"{u.get('completion_tokens', 0)} completion tok",
                      file=sys.stderr, flush=True)
            return d["choices"][0]["message"]
        except urllib.error.HTTPError as e:
            # A 4xx is a statement about the request, so retrying sends the
            # identical bytes and gets the identical refusal. Only 429 and the
            # 5xx range are worth another attempt. The body says WHY - the old
            # code discarded it and reported a bare "LLM call failed".
            detail = e.read(2000).decode("utf-8", "replace")
            last = f"HTTP {e.code}: {detail}"
            if e.code != 429 and e.code < 500:
                raise RuntimeError(f"LLM call refused: {last}") from None
        except RETRYABLE as e:
            last = f"{type(e).__name__}: {e} after {time.monotonic()-began:.0f}s"
        if attempt < retries - 1:
            # Backoff with jitter: without it, 12 threads that hit the same
            # rate limit retry in lockstep and trip it again together.
            time.sleep(min(30, 2 ** attempt * 5) * (0.5 + random.random()))
    raise RuntimeError(f"LLM call failed after {retries} attempts: {last}")


def usage_report():
    """What the run actually cost, both sides of it.

    This reported PROMPT TOKENS ONLY, and `completion` was counted into USAGE
    and then never printed. On a reasoning model that is the wrong half to
    watch: output is billed several times higher than input, and most of it is
    reasoning the caller never sees. A whole-archive estimate built on the
    printed number was low by the entire output cost.
    """
    hit, miss = USAGE["cache_hit"], USAGE["cache_miss"]
    tot = hit + miss
    if not tot and not USAGE["completion"]:
        return f"{USAGE['calls']} calls"
    out = USAGE["completion"]
    think = USAGE["reasoning"]
    return (f"{USAGE['calls']} calls · prompt {tot:,} tok "
            f"({hit/tot*100:.0f}% cached) · completion {out:,} tok"
            + (f" (of which {think:,} reasoning, "
               f"{think/out*100:.0f}%)" if think else ""))



# The fixed retrieval pipeline that used to live below this line - PLAN_SYS,
# READ_SYS, ANSWER_SYS, plan(), gather(), who(), official_record(), LENSES,
# read_batch(), ask() and a CLI - is deleted. D9 replaced it with tools the
# model calls (web/tools.py) driven by a loop that decides what to call
# next (web/agent.py), and slice 4 put that behind /api/ask. The pipeline
# served nothing after that: its last caller was bin/eval_agent.py --agent,
# a check that was therefore reporting on a code path no reader could
# reach. That eval now runs web/agent.py.
#
# What remains is the CHAT CLIENT, which is a different thing and is used by
# five modules: segment.py, name_speakers.py, redact.py, web/agent.py and
# web/server.py. Keep api_key/chat/chat_raw/usage_report together - the
# usage accounting is the only place this project measures what it spends.
