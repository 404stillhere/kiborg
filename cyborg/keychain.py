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
# Реш. юзера 2026-07-16: ГИБРИД — две модели на РАЗНЫХ ключах/endpoint'ах (build_chain берёт
# keys[key] per-entry, поэтому смешанные endpoint/ключи поддержаны).
#   1) gemini-2.5-flash-lite через НАТИВНЫЙ ключ Google-подписки (первичная, дёшево — подписка).
#   2) muse-spark через closerouter (фолбэк при отлёте gemini — доказанно рабочая, тянула
#      генерацию, пока её душили таймаутами мёртвых моделей).
# Корень болванок был в гонке таймаутов старой 4-модельной closerouter-цепочки (deepseek-pro
# регулярно таймаутил, glm5 — 503, бюджет сжирался впустую). Гибрид решает это: быстрая
# первичная, и надёжный фолбэк, когда нативный gemini с этой сети провисает на TLS
# (интермиттент, та же причина по которой киборг уходил с gemini в 2026-07-13 — но теперь
# он не один в цепочке). Порядок = приоритет. Прочие провайдеры — в СОВЕТ.
_CR_URL = "https://api.closerouter.dev/v1/chat/completions"
_GEM_NATIVE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
_SPEC = [
    ("gemini", "GEMINI_API_KEY", _GEM_NATIVE_URL, "gemini-2.5-flash-lite"),
    ("muse-spark", "CLOSEROUTER_API_KEY", _CR_URL, "meta/muse-spark-1.1"),
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


def chain_summary(path=None):
    """БЕЗОПАСНАЯ строка цепочки для логов/пульта/отладки: только id + model, БЕЗ apiKey/baseUrl.

    Защита от класса косяка (инцидент 2026-07-16): `print(chain)` / `print(_chain())` утёк
    ЗНАЧЕНИЯМИ ключей в вывод. В отладке/логах/пульте печатать ТОЛЬКО chain_summary —
    `apiKey`/`baseUrl` несут секрет (closerouter/gemini-ключи), id+model достаточно для диагноза
    «какая модель ответила / сколько в цепочке». Пусто -> '' (без секретов даже при пустой цепи)."""
    return ", ".join(f"{c['id']}({c['model']})" for c in build_chain(path)) if build_chain(path) else ""



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


_COUNCIL_DEADLINE = 50   # жёсткий wall-clock потолок на один вызов рецензента (см. _with_deadline)


def _with_deadline(fn, deadline=_COUNCIL_DEADLINE):
    """Выполнить fn() под ЖЁСТКИМ wall-clock потолком. Сокет-таймаут urllib НЕ ловит slow-loris
    (эндпоинт принял TCP и сыплет байты по капле — таймаут на recv не срабатывает, вызов висит).
    Гоняем fn в демон-потоке и бросаем TimeoutError, если не уложился. Брошенный поток дотикает в
    фоне (демон, умрёт с процессом; его добьёт сокет-таймаут), но СОВЕТ идёт дальше — контракт
    review: рецензент, бросивший исключение, просто выпадает из голосования."""
    import threading
    box = {}

    def worker():
        try:
            box["r"] = fn()
        except BaseException as e:   # noqa: BLE001 — любую ошибку донесём вызывающему как раньше
            box["e"] = e

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(deadline)
    if t.is_alive():
        raise TimeoutError(f"council reviewer exceeded {deadline}s wall-clock (эндпоинт молчит/сыплет по капле)")
    if "e" in box:
        raise box["e"]
    return box.get("r", "")


def _openai_chat(url, key, model, system, prompt, timeout=40):
    """Один OpenAI-совместимый вызов chat/completions. Текст ответа. Бросает при сбое
    (контракт review_content: chat должен бросать, чтобы рецензент ушёл в фолбэк).
    timeout=40с — сокет-таймаут (эндпоинт, что вообще молчит, падает тут). Slow-loris (сыплет по
    капле) сокет не ловит — его добивает жёсткий _with_deadline в make_council_chat (2026-07-14)."""
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
        # Жёсткий wall-clock потолок: slow-loris эндпоинт не заморозит совет (сокет-таймаут его
        # не ловит). Бросок → рецензент выпадает из голосования, совет судит остальными.
        return _with_deadline(lambda: _openai_chat(url, keys[key_name], real_model, system, prompt))

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
    # max_workers = число рецензентов → ВСЕ идут одной волной (advisors прокидывает ключ в
    # orchestra env, organ.py берёт min(len, max_workers)). Иначе дефолт organ.py = 4: при 7
    # рецензентах выходит 2 волны, и мёртвый эндпоинт во 2-й волне удваивал зависание (60с×2).
    # Одна волна + wall-clock потолок _COUNCIL_DEADLINE (50с) в _with_deadline → совет ограничен
    # ~50с даже если все молчат/сыплют по капле (сокет-таймаут _openai_chat 40с — вторая линия).
    return {"models": models, "chat": chat, "max_workers": len(models)}


if __name__ == "__main__":
    summary = chain_summary()
    if not summary:
        print("SMOKE: ключей нет — впиши хотя бы один в llm_keys.env, интуиция пока воздержится")
    else:
        # печатаем БЕЗ ключей — только id и модели (chain_summary; инцидент 2026-07-16: print(chain) утёк секретом)
        print("SMOKE OK: цепочка из", len(build_chain()), "провайдеров:", summary)
