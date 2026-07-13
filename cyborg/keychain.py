"""Ключи -> цепочка провайдеров для ask_llm (интуиции мозга).

Читает llm_keys.env (KEY=value), строит chain в формате DarBench/organ.js:
[{id, baseUrl (полный chat-completions URL), apiKey, model}]. Порядок — free-first
(канон DarBench). Берёт ТОЛЬКО заполненные ключи; пусто -> chain=[] (интуиция воздержится).

Значение ключа НИКОГДА не логируем и не возвращаем наружу, кроме самой chain.
Только stdlib. Файл ключей — в .gitignore.
"""
import os

_KEYS_FILE = os.environ.get("KIBORG_LLM_KEYS", "M:/projects/kiborg/llm_keys.env")

# Цепочка ИНТУИЦИИ (ask_llm): id, имя ключа, endpoint (полный chat-completions URL), модель.
# Реш. юзера 2026-07-13: интуиция — ТОЛЬКО closerouter, но ЦЕПОЧКА фолбэка из 4 моделей
# (кто ответит — того ответ). Порядок задан юзером: deepseek → glm5 → muse-spark → codex-spark.
# Все на одном ключе/endpoint closerouter, отличается только модель. Так перемежающийся 502
# по одной модели больше не глушит интуицию — есть запас. Прочие провайдеры — в СОВЕТ.
_CR_URL = "https://api.closerouter.dev/v1/chat/completions"
_SPEC = [
    ("deepseek", "CLOSEROUTER_API_KEY", _CR_URL, "deepseek/deepseek-v4-pro"),
    ("glm5", "CLOSEROUTER_API_KEY", _CR_URL, "z-ai/glm-5"),
    ("muse-spark", "CLOSEROUTER_API_KEY", _CR_URL, "meta/muse-spark-1.1"),
    ("codex-spark", "CLOSEROUTER_API_KEY", _CR_URL, "openai/gpt-5.3-codex-spark"),
]


def _read_env_file(fp):
    """KEY=value -> dict. Пустые значения и комментарии игнорируются. Кавычки снимаются."""
    out = {}
    try:
        with open(fp, encoding="utf-8") as f:
            raw = f.read()
    except Exception:
        return out
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        if k and v:                              # пустое значение = ключ не задан, пропускаем
            out[k] = v
    return out


def load_keys(path=None):
    """Ключи из файла, дополненные os.environ (env перебивает файл). Только заполненные."""
    resolved = dict(_read_env_file(path or _KEYS_FILE))
    for _, key, _, _ in _SPEC:
        if os.environ.get(key):
            resolved[key] = os.environ[key]
    return resolved


def build_chain(path=None):
    """Цепочка провайдеров с ключами (free-first) для context['llm_chain']. Пусто -> []."""
    keys = load_keys(path)
    return [{"id": pid, "baseUrl": url, "apiKey": keys[key], "model": model}
            for pid, key, url, model in _SPEC if keys.get(key)]


def available(path=None):
    """Есть ли хоть один провайдер (жива ли интуиция)."""
    return len(build_chain(path)) > 0


# --- СОВЕТ (orchestra): модели-рецензенты на ключах kiborg ---------------------
# Реш. юзера 2026-07-13: в совет — ВСЕ модели, кроме интуиции (closerouter). Все endpoint'ы
# OpenAI-совместимы (Bearer). id рецензента -> (имя ключа, endpoint, модель). Проверены живьём:
# ✅ sambanova, groq, mistral, openrouter, cohere, nvidia отвечают; ✅ gemini валиден (429 —
# лимит бесплатного тира); ❌ cerebras даёт 403 (ключ отклонён) — оставлен в списке, но
# воздержится, пока юзер не поправит ключ (рецензент падает → совет идёт с остальными).
_COUNCIL_SPEC = {
    "sambanova": ("SAMBANOVA_API_KEY", "https://api.sambanova.ai/v1/chat/completions", "DeepSeek-V3.2"),
    "groq": ("GROQ_API_KEY", "https://api.groq.com/openai/v1/chat/completions", "qwen/qwen3-32b"),
    "gemini": ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "gemini-2.5-flash"),
    "mistral": ("MISTRAL_API_KEY", "https://api.mistral.ai/v1/chat/completions", "mistral-small-latest"),
    "openrouter": ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1/chat/completions", "openrouter/free"),
    "cohere": ("COHERE_API_KEY", "https://api.cohere.ai/compatibility/v1/chat/completions", "command-a-03-2025"),
    "nvidia": ("NVIDIA_API_KEY", "https://integrate.api.nvidia.com/v1/chat/completions", "meta/llama-3.1-8b-instruct"),
    "cerebras": ("CEREBRAS_API_KEY", "https://api.cerebras.ai/v1/chat/completions", "llama-3.3-70b"),
}

# Рецензенты ОТКЛЮЧЕНЫ, но НЕ удалены (реш. юзера 2026-07-13): спека остаётся, из совета
# выпадают. Вернуть в строй = убрать id отсюда. cerebras — ключ отдаёт 403.
_COUNCIL_DISABLED = {"cerebras"}


def _openai_chat(url, key, model, system, prompt, timeout=60):
    """Один OpenAI-совместимый вызов chat/completions. Текст ответа. Бросает при сбое
    (контракт review_content: chat должен бросать, чтобы рецензент ушёл в фолбэк)."""
    import json as _json
    import urllib.request
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body = _json.dumps({"model": model, "messages": msgs, "max_tokens": 1024, "temperature": 0.3}).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = _json.loads(r.read().decode("utf-8", "replace"))
    return d["choices"][0]["message"]["content"] or ""


def make_council_chat(path=None):
    """Транспорт совета: chat(model, system, prompt) -> text. Резолвит id рецензента из
    _COUNCIL_SPEC по ключам kiborg. Неизвестная/без-ключа модель -> raise (рецензент падает,
    совет продолжает с остальными). None, если ни одного рецензента с ключом нет."""
    keys = load_keys(path)
    live = {rid: spec for rid, spec in _COUNCIL_SPEC.items()
            if keys.get(spec[0]) and rid not in _COUNCIL_DISABLED}
    if not live:
        return None

    def chat(model, system, prompt):
        rid = str(model).split(":")[0]                 # 'gemini' или 'gemini:gemini-2.5-flash'
        spec = live.get(rid)
        if not spec:
            raise RuntimeError("council: no key/endpoint for reviewer " + str(model))
        key_name, url, default_model = spec
        real_model = str(model).split(":", 1)[1] if ":" in str(model) else default_model
        return _openai_chat(url, keys[key_name], real_model, system, prompt)

    return chat


def council_models(path=None):
    """Имена рецензентов совета: есть ключ И не отключены (_COUNCIL_DISABLED)."""
    keys = load_keys(path)
    return [rid for rid, spec in _COUNCIL_SPEC.items()
            if keys.get(spec[0]) and rid not in _COUNCIL_DISABLED]


def orchestra_context(path=None):
    """Готовый блок для context['orchestra'] — модели + транспорт на ключах kiborg.
    None, если рецензентов нет. Совет ВСЁ РАВНО по умолчанию спит (advisors), пока
    интуиция его не позвала / вызыватель не включил — это лишь провод, не выключатель."""
    chat = make_council_chat(path)
    models = council_models(path)
    if not chat or not models:
        return None
    return {"models": models, "chat": chat}


if __name__ == "__main__":
    chain = build_chain()
    if not chain:
        print("SMOKE: ключей нет — впиши хотя бы один в llm_keys.env, интуиция пока воздержится")
    else:
        # печатаем БЕЗ ключей — только id и модели
        print("SMOKE OK: цепочка из", len(chain), "провайдеров:",
              ", ".join(f"{c['id']}({c['model']})" for c in chain))
