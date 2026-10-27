"""Talking to a language model: Groq in the cloud first, Ollama on the laptop as the fallback.

Groq: GROQ_API_KEY + GROQ_MODEL in .env, 5 s timeout + one retry. Skipped when OFFLINE_MODE=true.
Ollama: OLLAMA_URL (e.g. http://127.0.0.1:11434) + OLLAMA_MODEL (default llama3.2:3b).
If neither answers, LlmUnavailable is raised and every caller has a non-LLM fallback
(keyword parser, keyword intents, best matching SOP paragraph), so nothing stops working.

The model is only ever asked for small JSON objects or short text. It never writes SQL and
never decides anything on its own: our code checks every value it returns.
"""

import json
import logging
import time

import httpx

from app.config import get_settings

log = logging.getLogger("safetyai.llm")

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
ATTEMPTS = 2


class LlmUnavailable(Exception):
    pass


def groq_ready() -> bool:
    s = get_settings()
    return bool(s.groq_api_key and s.groq_model and not s.offline_mode)


def ollama_ready() -> bool:
    return bool(get_settings().ollama_url)


def status() -> str:
    """For /health: ok (Groq set up), ollama_only, or not_checked (nothing set up)."""
    if groq_ready():
        return "ok"
    return "ollama_only" if ollama_ready() else "not_checked"


def _groq(messages: list[dict], json_mode: bool, tools: list[dict] | None = None) -> dict:
    s = get_settings()
    body: dict = {"model": s.groq_model, "messages": messages, "temperature": 0}
    if s.groq_model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = "low"  # these models think before answering; short thinking keeps calls fast
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    last = ""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            response = httpx.post(GROQ_CHAT_URL, json=body, timeout=s.llm_timeout_s,
                                  headers={"Authorization": f"Bearer {s.groq_api_key}"})
        except httpx.TransportError as exc:
            last = str(exc)
            log.warning("groq attempt=%d failed: %s", attempt, exc)
            continue
        if response.status_code in (429, 500, 502, 503, 504):
            last = f"groq {response.status_code}"
            log.warning("groq attempt=%d failed: %s", attempt, last)
            continue
        if response.status_code >= 400:
            raise LlmUnavailable(f"groq {response.status_code}: {response.text[:200]}")
        return response.json()["choices"][0]["message"]
    raise LlmUnavailable(last)


def _ollama(messages: list[dict], json_mode: bool) -> str:
    s = get_settings()
    body = {"model": s.ollama_model or s.ollama_default_model, "messages": messages, "stream": False,
            "options": {"temperature": 0}}
    if json_mode:
        body["format"] = "json"
    try:
        response = httpx.post(f"{s.ollama_url.rstrip('/')}/api/chat", json=body, timeout=s.ollama_timeout_s)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise LlmUnavailable(f"ollama: {exc}")
    return response.json()["message"]["content"]


def chat(messages: list[dict], json_mode: bool = False) -> tuple[str, str]:
    """Returns (text, source) where source is 'groq' or 'ollama'."""
    t0 = time.perf_counter()
    if groq_ready():
        try:
            text = _groq(messages, json_mode).get("content") or ""
            log.info("step=llm source=groq ms=%.0f", (time.perf_counter() - t0) * 1000)
            return text, "groq"
        except LlmUnavailable as exc:
            log.warning("groq unavailable, trying ollama: %s", exc)
    if ollama_ready():
        text = _ollama(messages, json_mode)
        log.info("step=llm source=ollama ms=%.0f", (time.perf_counter() - t0) * 1000)
        return text, "ollama"
    raise LlmUnavailable("no language model configured or reachable")


def chat_json(messages: list[dict]) -> tuple[dict, str]:
    text, source = chat(messages, json_mode=True)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LlmUnavailable(f"{source} returned something that is not JSON") from exc
    if not isinstance(value, dict):
        raise LlmUnavailable(f"{source} returned JSON that is not an object")
    return value, source


def choose_tool(messages: list[dict], tools: list[dict]) -> tuple[str, dict] | None:
    """Function calling (Groq): which of our tools, with which arguments. None if it picked none."""
    if not groq_ready():
        return None
    try:
        message = _groq(messages, json_mode=False, tools=tools)
    except LlmUnavailable as exc:
        log.warning("tool choice unavailable: %s", exc)
        return None
    tool_calls = message.get("tool_calls") or []
    if not tool_calls:
        return None
    function = tool_calls[0]["function"]
    try:
        arguments = json.loads(function.get("arguments") or "{}")
    except json.JSONDecodeError:
        arguments = {}
    return function["name"], arguments if isinstance(arguments, dict) else {}


def classify_intent(text: str, intents: list[str]) -> str | None:
    """Map unclear speech on a call to one of a few intents. Any problem -> None (the guard is asked again)."""
    prompt = (f"A security guard answered an alert call and said: {text!r}. Which one intent matches best: "
              f"{', '.join(intents)}, or none? Reply as JSON: {{\"intent\": \"...\"}}")
    try:
        value, _ = chat_json([{"role": "user", "content": prompt}])
    except LlmUnavailable:
        return None
    intent = value.get("intent")
    return intent if intent in intents else None
