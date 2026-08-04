"""ask_llm — речевой центр генератора идей.

Интуиция идёт по трём уровням fallback:
1. z.ai (Anthropic endpoint /api/anthropic/v1/messages, glm-5.2, ZAI_API_KEY).
2. Нативные OpenAI-совместимые провайдеры: mistral → openrouter → groq.
3. DarBench/organ.js-цепочка closerouter (muse-spark → deepseek-v4-pro → nemotron-3-ultra).

Контракт для органов НЕ изменился: env['llm'] = callable(prompt:str) -> str. При любой
ошибке (нет ключа / сеть / пустой ответ) -> "" -> вызыватель (ideate) честно падает on stub.
Значение ключа НИКОГДА не логируем и не возвращаем.
Только stdlib (subprocess/json/urllib) + keychain + zai_ask + native_llm.
"""

import json
import os
import subprocess
import time

import config
import keychain
import native_llm
import zai_ask

_NODE_EXE = os.environ.get(config.NODE_EXE_ENV, "node")
_ORGAN_JS = os.environ.get(config.ASK_LLM_JS_ENV, config.DEFAULT_ASK_LLM_JS)
_TIMEOUT_MS = int(os.environ.get(config.ASK_LLM_TIMEOUT_MS_ENV, str(config.DEFAULT_LLM_TIMEOUT_MS)))

# Ярлык для пульта/логов (serve.py, harvest.py, run.py читают ask_llm._MODEL). Реальная
# модель — первая живая в цепочке; тут статичное человекочитаемое имя провайдера.
# 2026-08-03: интуиция — z.ai glm-5.2 → closerouter (muse-spark → deepseek-v4-pro → nemotron-3-ultra).
_MODEL = "zai glm-5.2 → mistral/openrouter/groq → closerouter"

# Какой провайдер РЕАЛЬНО ответил в последнем ask() — id из organ.js result.provider
# или "zai" для z.ai. Диагностика: видно, кто сработал (первичная или фолбэк).
# "" до первого вызова / при сбое. Читают harvest/panel (опц., для логов).
# Дублируется в файл config.LAST_PROVIDER_FILE, потому что panel/serve.py живёт в другом
# процессе и не видит module-global из ask_llm.
last_provider = ""


def _load_provider():
    """Прочитать сохранённого провайдера из файла (используется при старте)."""
    try:
        with open(config.LAST_PROVIDER_FILE, encoding="utf-8") as f:
            return json.load(f).get("provider", "") or ""
    except Exception:
        return ""


def _save_provider(provider):
    """Атомарно сохранить провайдера в файл, чтобы panel/serve.py в другом процессе видел."""
    path = config.LAST_PROVIDER_FILE
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"provider": provider or "", "ts": time.time()}, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


# При импорте восстанавливаем последнее известное значение (после рестарта процесса).
last_provider = _load_provider()


def _chain():
    """Цепочка fallback из keychain (closerouter), если z.ai не справится."""
    return keychain.build_chain()


def available():
    """Жив ли генератор — есть ли ключ z.ai / нативный / closerouter."""
    return zai_ask.available() or native_llm.available() or len(_chain()) > 0


def _strip_fence(t):
    """Снять обёртку ```json ... ``` — ideate парсит по строкам, заборчик ему мешает."""
    t = (t or "").strip()
    if t.startswith("```"):
        t = "\n".join(ln for ln in t.splitlines() if not ln.strip().startswith("```")).strip()
    return t


def _run_chain(chain, prompt, timeout_ms, temperature=config.INTUITION_TEMPERATURE):
    """Один прогон DarBench/organ.js по fallback-цепочке. Текст | ""."""
    global last_provider
    if not chain or not os.path.exists(_ORGAN_JS):
        return ""
    n = max(1, len(chain))
    per_provider_ms = max(config.ASK_LLM_MIN_PER_PROVIDER_MS, timeout_ms // n)
    payload = {
        "inputs": {"prompt": prompt, "temperature": temperature},
        "env": {"chain": chain, "timeout_ms": per_provider_ms},
    }
    try:
        proc = subprocess.Popen(
            [_NODE_EXE, _ORGAN_JS],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        stdout, stderr = proc.communicate(
            input=json.dumps(payload),
            timeout=max(
                config.ASK_LLM_SUBPROCESS_TIMEOUT_PAD_SEC,
                timeout_ms // 1000 + config.ASK_LLM_SUBPROCESS_TIMEOUT_PAD_SEC,
            ),
        )
    except Exception:
        return ""
    if proc.returncode != 0 and not stdout.strip():
        return ""
    try:
        res = json.loads(stdout.strip().splitlines()[-1])
    except Exception:
        return ""
    if res.get("ok"):
        last_provider = res.get("provider") or ""
        _save_provider(last_provider)
        return _strip_fence(res.get("text") or "")
    last_provider = ""
    _save_provider("")
    return ""


def ask(prompt, timeout_ms=None, temperature=config.INTUITION_TEMPERATURE):
    """prompt -> text. Сначала z.ai, потом fallback-цепочка. "" при любом сбое."""
    global last_provider
    timeout_ms = timeout_ms or _TIMEOUT_MS

    # 1. Пробуем z.ai первым (дешевле/быстрее/приоритет юзера).
    if zai_ask.available():
        out = zai_ask.ask(prompt, timeout_ms=timeout_ms, temperature=temperature)
        if out:
            last_provider = "zai"
            _save_provider("zai")
            return _strip_fence(out)
        last_provider = ""
        _save_provider("")

    # 2. Нативные OpenAI-совместимые провайдеры (Mistral/OpenRouter/Groq).
    if native_llm.available():
        out = native_llm.ask(prompt, timeout_ms=timeout_ms, temperature=temperature)
        if out:
            last_provider = "native"
            _save_provider("native")
            return _strip_fence(out)
        last_provider = ""
        _save_provider("")

    # 3. Fallback на closerouter-цепочку.
    chain = _chain()
    if not chain:
        last_provider = ""
        _save_provider("")
        return ""
    return _run_chain(chain, prompt, timeout_ms, temperature)


if __name__ == "__main__":
    if not available():
        print("SMOKE SKIP: цепочки нет (llm_keys.env / ZAI_API_KEY / CLOSEROUTER_API_KEY)")
    else:
        out = ask('Верни РОВНО одну строку JSON и ничего больше: {"ok":true}')
        print("SMOKE", "OK" if '"ok"' in out or "ok" in out.lower() else "FAIL", "|", repr(out[:160]))
