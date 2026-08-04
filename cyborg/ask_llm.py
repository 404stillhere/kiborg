"""ask_llm — речевой центр генератора идей.

Интуиция сначала пробует z.ai (Anthropic endpoint /api/anthropic/v1/messages,
модель glm-5.2, ключ ZAI_API_KEY) через лёгкий stdlib-транспорт zai_ask.py. Если
z.ai недоступен/сбой/нет ключа — падает на DarBench/organ.js-цепочку closerouter
(muse-spark → deepseek-v4-pro → nemotron-3-ultra), как было раньше.

Контракт для органов НЕ изменился: env['llm'] = callable(prompt:str) -> str. При любой
ошибке (нет ключа / сеть / пустой ответ) -> "" -> вызыватель (ideate) честно падает на stub.
Значение ключа НИКОГДА не логируем и не возвращаем.
Только stdlib (subprocess/json/urllib) + keychain + zai_ask.
"""

import json
import os
import subprocess

import keychain
import zai_ask

_NODE_EXE = os.environ.get("KIBORG_NODE_EXE", "node")
_ORGAN_JS = os.environ.get("KIBORG_ASK_LLM_JS", "M:/projects/DarBench/organ.js")
_TIMEOUT_MS = int(os.environ.get("KIBORG_ASK_LLM_TIMEOUT_MS", "120000"))

# Ярлык для пульта/логов (serve.py, harvest.py, run.py читают ask_llm._MODEL). Реальная
# модель — первая живая в цепочке; тут статичное человекочитаемое имя провайдера.
# 2026-08-03: интуиция — z.ai glm-5.2 → closerouter (muse-spark → deepseek-v4-pro → nemotron-3-ultra).
_MODEL = "zai glm-5.2 → muse→deepseek→nemotron (closerouter)"

# Какой провайдер РЕАЛЬНО ответил в последнем ask() — id из organ.js result.provider
# или "zai" для z.ai. Диагностика: видно, кто сработал (первичная или фолбэк).
# "" до первого вызова / при сбое. Читают harvest/panel (опц., для логов).
last_provider = ""


def _chain():
    """Цепочка fallback из keychain (closerouter), если z.ai не справится."""
    return keychain.build_chain()


def available():
    """Жив ли генератор — есть ли ключ z.ai ИЛИ хоть один провайдер fallback-цепочки."""
    return zai_ask.available() or len(_chain()) > 0


def _strip_fence(t):
    """Снять обёртку ```json ... ``` — ideate парсит по строкам, заборчик ему мешает."""
    t = (t or "").strip()
    if t.startswith("```"):
        t = "\n".join(ln for ln in t.splitlines() if not ln.strip().startswith("```")).strip()
    return t


def _run_chain(chain, prompt, timeout_ms, temperature=0.9):
    """Один прогон DarBench/organ.js по fallback-цепочке. Текст | ""."""
    global last_provider
    if not chain or not os.path.exists(_ORGAN_JS):
        return ""
    n = max(1, len(chain))
    per_provider_ms = max(3000, timeout_ms // n)
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
        stdout, stderr = proc.communicate(input=json.dumps(payload), timeout=max(5, timeout_ms // 1000 + 5))
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
        return _strip_fence(res.get("text") or "")
    last_provider = ""
    return ""


def ask(prompt, timeout_ms=None, temperature=0.9):
    """prompt -> text. Сначала z.ai, потом fallback-цепочка. "" при любом сбое."""
    global last_provider
    timeout_ms = timeout_ms or _TIMEOUT_MS

    # 1. Пробуем z.ai первым (дешевле/быстрее/приоритет юзера).
    if zai_ask.available():
        out = zai_ask.ask(prompt, timeout_ms=timeout_ms, temperature=temperature)
        if out:
            last_provider = "zai"
            return _strip_fence(out)
        last_provider = ""

    # 2. Fallback на closerouter-цепочку.
    chain = _chain()
    if not chain:
        return ""
    return _run_chain(chain, prompt, timeout_ms, temperature)


if __name__ == "__main__":
    if not available():
        print("SMOKE SKIP: цепочки нет (llm_keys.env / ZAI_API_KEY / CLOSEROUTER_API_KEY)")
    else:
        out = ask('Верни РОВНО одну строку JSON и ничего больше: {"ok":true}')
        print("SMOKE", "OK" if '"ok"' in out or "ok" in out.lower() else "FAIL", "|", repr(out[:160]))
