"""ask_llm — речевой центр генератора идей. Идёт по ТОЙ ЖЕ цепочке, что интуиция мозга —
её держит keychain (_SPEC): цепочка closerouter (muse-spark → deepseek-v4-pro → nemotron-3-ultra),
реш. юзера 2026-07-20. Все три модели на одном ключе CLOSEROUTER_API_KEY (build_chain берёт
keys[key] per-entry — один ключ на несколько моделей поддержан).

История: сперва отдельный провод к Gemini (ключ gemini.md); 2026-07-13 сведён с интуицией на
closerouter-цепочку (deepseek→glm5→muse→codex); 2026-07-16 — гибрид gemini→muse-spark; gemini
оказался geoblocked с сети юзера → 2026-07-20 заменён на 3-модельную цепочку closerouter
(см. keychain._SPEC). Транспорт — DarBench/organ.js (node), тот же, что у интуиции.

Контракт для органов НЕ изменился: env['llm'] = callable(prompt:str) -> str. При любой
ошибке (нет ключа / сеть / пустой ответ) -> "" -> вызыватель (ideate) честно падает на stub.
Значение ключа НИКОГДА не логируем и не возвращаем — оно уходит только в chain -> organ.js.
Только stdlib (subprocess/json) + keychain.
"""
import json
import os
import subprocess

import keychain  # цепочка интуиции (та же, что кормит совет) — единый источник ключей

_NODE_EXE = os.environ.get("KIBORG_NODE_EXE", "node")
_ORGAN_JS = os.environ.get("KIBORG_ASK_LLM_JS", "M:/projects/DarBench/organ.js")
_TIMEOUT_MS = int(os.environ.get("KIBORG_ASK_LLM_TIMEOUT_MS", "120000"))

# Ярлык для пульта/логов (serve.py, harvest.py, run.py читают ask_llm._MODEL). Реальная
# модель — первая живая в цепочке; тут статичное человекочитаемое имя провайдера.
# 2026-07-20: интуиция — цепочка closerouter (muse-spark → deepseek-v4-pro → nemotron-3-ultra).
_MODEL = "muse→deepseek→nemotron (closerouter)"

# Какой провайдер РЕАЛЬНО ответил в последнем ask() — id из organ.js result.provider
# (muse-spark / deepseek / nemotron). Диагностика: показывает, какая модель в цепочке реально
# сработала (фолбэк ли, или первичная). "" до первого вызова / при сбое. Ставит _run_chain;
# читают harvest/panel (опц., для логов).
last_provider = ""


def _chain():
    """Цепочка интуиции из keychain (muse→deepseek→nemotron на closerouter, см. keychain._SPEC).
    Пусто -> []."""
    return keychain.build_chain()


def available():
    """Жив ли генератор — есть ли ключ цепочки (ровно тот же, что у интуиции)."""
    return len(_chain()) > 0


def _strip_fence(t):
    """Снять обёртку ```json ... ``` — ideate парсит по строкам, заборчик ему мешает."""
    t = (t or "").strip()
    if t.startswith("```"):
        t = "\n".join(ln for ln in t.splitlines() if not ln.strip().startswith("```")).strip()
    return t


def _run_chain(chain, prompt, timeout_ms, temperature=0.9):
    """Один прогон DarBench/organ.js по цепочке (тот же транспорт, что интуиция). Текст | "".
    max_tokens НЕ шлём — reasoning-модели (deepseek) при малом лимите тратят его на обдумывание
    и молчат; берут свой дефолт-бюджет (organ.js: 8192). temperature по умолчанию 0.9 —
    генерация; СУДЕЙСКИЕ вызовы (оценка читаемости) передают низкую (~0.2), чтобы балл всегда
    парсился (на 0.9 рассуждающая модель изредка не отдаёт чистый JSON — та же болячка судьи).

    Побочно: ставит модульный last_provider — id провайдера, чей ответ РЕАЛЬНО взят (из organ.js
    result.provider). Цепочка 3-model closerouter делает это диагностически полезным: видно, какая
    из muse-spark/deepseek/nemotron реально ответила (muse-spark — первичная, остальные — фолбэк при
    её отлёте). last_provider опционально читают harvest/panel для логов; контракт callable(prompt)
    ->str НЕ меняется (атрибут, не return)."""
    global last_provider
    if not chain or not os.path.exists(_ORGAN_JS):
        return ""
    n = max(1, len(chain))
    per_provider_ms = max(3000, timeout_ms // n)     # медленный провайдер не съедает весь бюджет
    payload = {"inputs": {"prompt": prompt, "temperature": temperature},
               "env": {"chain": chain, "timeout_ms": per_provider_ms}}
    try:
        # Windows bug: subprocess.run(input=...) не посылает EOF в stdin → organ.js
        # висит ждёт end event. Рабочий вариант — Popen + communicate() (явно закрывает stdin).
        proc = subprocess.Popen([_NODE_EXE, _ORGAN_JS], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8")
        stdout, stderr = proc.communicate(input=json.dumps(payload),
                                          timeout=max(5, timeout_ms // 1000 + 5))
    except Exception:
        return ""                                    # node/сеть упали -> "" -> вызыватель на stub
    if proc.returncode != 0 and not stdout.strip():
        return ""
    try:
        res = json.loads(stdout.strip().splitlines()[-1])
    except Exception:
        return ""
    if res.get("ok"):
        last_provider = res.get("provider") or ""   # кто РЕАЛЬНО ответил (muse-spark / deepseek / nemotron)
        return _strip_fence(res.get("text") or "")
    last_provider = ""
    return ""


def ask(prompt, timeout_ms=None, temperature=0.9):
    """prompt -> text по цепочке интуиции. "" при любом сбое (вызыватель уйдёт на stub).
    temperature по умолчанию 0.9 (генерация); судейские вызовы шлют низкую (~0.2) для
    стабильного парса балла — контракт органов callable(prompt)->str не меняется (kwarg опционален)."""
    chain = _chain()
    if not chain:
        return ""
    return _run_chain(chain, prompt, timeout_ms or _TIMEOUT_MS, temperature)


if __name__ == "__main__":
    if not available():
        print("SMOKE SKIP: цепочки нет (llm_keys.env / CLOSEROUTER_API_KEY)")
    else:
        out = ask('Верни РОВНО одну строку JSON и ничего больше: {"ok":true}')
        print("SMOKE", "OK" if '"ok"' in out or "ok" in out.lower() else "FAIL", "|", repr(out[:160]))
