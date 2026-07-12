"""ask_llm — адаптер к живой модели (Google Gemini) для генератора идей (и, при желании,
мозга). Контракт органов: env['llm'] = callable(prompt:str) -> str. Здесь эта функция.

Ключ НЕ в коде: читается из env-переменной (GEMINI_KEY / LLM_KEY) или из файла
(по умолчанию M:/projects/kiborg/gemini.md). Значение ключа НИКОГДА не логируем.
Только stdlib (urllib) — без внешних зависимостей.

Промпт и разбор ответа делает КАЖДЫЙ вызыватель сам (ideate строит свой промпт и парсит
JSON-строки идей; brain — планирующий JSON). ask() лишь гоняет prompt -> text и снимает
markdown-заборчик ```json```. При любой ошибке (нет ключа/сеть/HTTP/пустой ответ)
возвращает "" — вызыватель тогда честно падает на stub. Наружу утечь секрету неоткуда:
ключ уходит только в query-параметр запроса к Google, в возврат/лог не попадает.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

_KEY_FILE = os.environ.get("GEMINI_KEY_FILE", "M:/projects/kiborg/gemini.md")
_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"


def load_key():
    """Ключ из env или файла. Пусто -> "" (вызыватель уйдёт на stub). Значение не логируем."""
    k = os.environ.get("GEMINI_KEY") or os.environ.get("LLM_KEY")
    if k:
        return k.strip()
    try:
        with open(_KEY_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def available():
    return bool(load_key())


def _strip_fence(t):
    """Снять обёртку ```json ... ``` — вызыватели парсят по строкам, заборчик им мешает."""
    t = (t or "").strip()
    if t.startswith("```"):
        t = "\n".join(ln for ln in t.splitlines() if not ln.strip().startswith("```")).strip()
    return t


def _extract(j):
    cand = (j.get("candidates") or [{}])[0]
    parts = (cand.get("content") or {}).get("parts") or []
    return _strip_fence("".join(p.get("text", "") for p in parts))


def ask(prompt, key=None, model=None, timeout=60, max_tokens=2048, temperature=0.9):
    key = key or load_key()
    if not key:
        return ""
    url = _ENDPOINT.format(model=model or _MODEL, key=urllib.parse.quote(key))
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "thinkingConfig": {"thinkingBudget": 0},  # без «размышления»: дешевле/быстрее для генерации
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return _extract(json.loads(r.read().decode("utf-8", "replace")))
    except Exception:
        return ""  # сеть/HTTP/парс упали — вызыватель уйдёт на stub, цикл не роняем


if __name__ == "__main__":
    if not available():
        print("SMOKE SKIP: ключа нет (gemini.md / GEMINI_KEY)")
    else:
        out = ask('Верни РОВНО одну строку JSON и ничего больше: {"ok":true}')
        print("SMOKE", "OK" if '"ok"' in out or "ok" in out.lower() else "FAIL", "|", repr(out[:160]))
