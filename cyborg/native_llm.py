"""Нативные OpenAI-совместимые fallback-провайдеры для интуиции.

Пробуем mistral → openrouter → groq (при наличии ключей). Только stdlib (urllib/json).
Контракт: ask(prompt, timeout_ms=None, max_tokens=8192, temperature=0.9) -> text | "".
"""

import json
import os
import urllib.request

import config
import keychain

_TIMEOUT = int(os.environ.get(config.NATIVE_LLM_TIMEOUT_MS_ENV, str(config.DEFAULT_LLM_TIMEOUT_MS)))

# Порядок = приоритет. Только провайдеры, НЕ входящие в closerouter-цепочку.
# Endpoint/model берём из cyborg.config, чтобы не дублировать с keychain.py.
_NATIVE_SPEC = [
    ("mistral",) + config.LLM_PROVIDER_MISTRAL,
    ("openrouter",) + config.LLM_PROVIDER_OPENROUTER,
    ("groq",) + config.LLM_PROVIDER_GROQ,
]


def _api_key(key_name):
    env = os.environ.get(key_name, "")
    if env:
        return env
    return keychain.load_keys().get(key_name, "")


def available():
    return any(_api_key(spec[1]) for spec in _NATIVE_SPEC)


def _call(spec, prompt, timeout_ms, max_tokens, temperature):
    pid, key_name, url, model = spec
    key = _api_key(key_name)
    if not key:
        return ""
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
    ).encode(config.HTTP_CHARSET_UTF8)
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            config.HTTP_HEADER_CONTENT_TYPE: config.HTTP_MEDIA_TYPE_JSON,
            config.HTTP_HEADER_AUTHORIZATION: config.HTTP_HEADER_AUTHORIZATION_BEARER_PREFIX + key,
        },
        method=config.HTTP_METHOD_POST,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_ms / 1000) as r:
            data = json.loads(r.read().decode(config.HTTP_CHARSET_UTF8, config.HTTP_DECODE_ERRORS_REPLACE))
        return data["choices"][0]["message"]["content"] or ""
    except Exception:
        return ""


def ask(prompt, timeout_ms=None, max_tokens=config.INTUITION_MAX_TOKENS, temperature=config.INTUITION_TEMPERATURE):
    """Пройти по нативным провайдерам, вернуть первый непустой ответ."""
    timeout_ms = timeout_ms or _TIMEOUT
    per_provider_ms = max(config.NATIVE_LLM_MIN_PER_PROVIDER_MS, timeout_ms // len(_NATIVE_SPEC))
    for spec in _NATIVE_SPEC:
        out = _call(spec, prompt, per_provider_ms, max_tokens, temperature)
        if out:
            return out
    return ""


if __name__ == "__main__":
    if not available():
        print("SMOKE SKIP: нет нативных ключей")
    else:
        out = ask('Верни РОВНО одну строку JSON: {"ok":true}')
        print("SMOKE", "OK" if '"ok"' in out else "FAIL", "|", repr(out[: config.NATIVE_LLM_SMOKE_MAX_CHARS]))
