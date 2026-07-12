"""Орган: collect_tg_news — собрать свежие посты из списка ТГ-каналов.

ВЕНДОРЕН (копия) из реестра: organ `collect_tg_news` (`M:/projects/_shared/organs.json`),
оригинал `M:/projects/darbot/organ.py` — извлечён из channels_worker.py (darbot) по контракту
EXTRACT_ORGAN.md: run(inputs, env) ничего не знает про channels.json/.env/пути проекта, всё
приходит аргументами. Оригинал НЕ трогаем — здесь автономная копия внутри kiborg.

Требует pyrogram (третьесторонняя либа, НЕ stdlib) — по этому kiborg НЕ импортирует этот файл
напрямую в своём (stdlib-only) интерпретаторе. Вместо этого `idea_engine/organs/collect_source.py`
дёргает его КАК ВНЕШНИЙ ПРОЦЕСС через venv darbot (там pyrogram уже стоит) в режиме --rpc:
JSON {"inputs":..., "env":...} на stdin -> JSON {"items":[...], "warnings":[...]} на stdout.
Импорт pyrogram — ленивый (внутри _make_client), поэтому сам файл грузится и БЕЗ pyrogram —
это нужно __main__ --offline и для тестов на FakeClient.

inputs:
    channels           list[str]  — каналы ("@topor" или "topor")
    since              str|None   — iso8601: брать посты не старше этой метки
    limit_per_channel  int        — потолок сообщений на канал (default 50)

env:
    TELEGRAM_API_ID    int|str    — api_id приложения Telegram
    TELEGRAM_API_HASH  str        — api_hash приложения Telegram
    TELEGRAM_SESSION   str        — путь к pyrogram-сессии (с .session или без)
    client             (опц.)     — готовый клиент вместо pyrogram (для тестов)

returns:
    {"items": [{channel, id, date, text, url}], "warnings": [str]}
    items отсортированы хронологически внутри каждого канала.
"""
import datetime
import os


def _make_client(env):
    """Собрать pyrogram-клиент из env. Единственное место, где нужен pyrogram."""
    from pyrogram import Client

    session = str(env["TELEGRAM_SESSION"])
    if session.endswith(".session"):
        session = session[: -len(".session")]
    workdir, name = os.path.split(session)
    return Client(
        name,
        api_id=int(env["TELEGRAM_API_ID"]),
        api_hash=env["TELEGRAM_API_HASH"],
        workdir=workdir or ".",
        no_updates=True,
    )


def _parse_since(since):
    if not since:
        return None
    cutoff = datetime.datetime.fromisoformat(since)
    if cutoff.tzinfo is None:
        cutoff = cutoff.astimezone()
    return cutoff


def _post_url(channel, msg_id):
    username = channel.lstrip("@")
    if username.lstrip("-").isdigit():  # приватный канал по числовому id — публичной ссылки нет
        return None
    return f"https://t.me/{username}/{msg_id}"


def run(inputs: dict, env: dict) -> dict:
    """Механизм. Тот же inputs + тот же env → то же поведение."""
    channels = inputs["channels"]
    cutoff = _parse_since(inputs.get("since"))
    limit = int(inputs.get("limit_per_channel", 50))

    items, warnings = [], []
    client = env.get("client") or _make_client(env)
    with client as app:
        for ch in channels:
            try:
                try:
                    app.get_chat(ch)  # резолв peer'а, как в оригинальном воркере
                except Exception:
                    pass
                posts = []
                for msg in app.get_chat_history(ch, limit=limit):
                    msg_date = msg.date.astimezone() if msg.date else None
                    if cutoff and msg_date and msg_date < cutoff:
                        break
                    text = msg.text or msg.caption
                    if text:
                        posts.append({
                            "channel": ch,
                            "id": msg.id,
                            "date": msg_date.isoformat() if msg_date else None,
                            "text": text,
                            "url": _post_url(ch, msg.id),
                        })
                items.extend(reversed(posts))
            except Exception as e:
                warnings.append(f"{ch}: {e}")
    return {"items": items, "warnings": warnings}


# ---------------------------------------------------------------------------
# Мосты для kiborg (НЕ было в оригинале darbot — добавлено при вендоринге)
# ---------------------------------------------------------------------------

def _rpc_main():
    """Режим --rpc: JSON {"inputs":..., "env":...} на stdin -> JSON run() на stdout.
    stdout — СТРОГО один JSON-объект (collect_source парсит его как есть); диагностика/варнинги
    pyrogram уходят в stderr (проверено: TgCrypto-warning не засоряет stdout).

    stdin/stdout читаются/пишутся байтами UTF-8 напрямую (.buffer), НЕ через print()/sys.stdin.read():
    при пайпе (не консоль) Windows молча кодирует текстовые стримы в ANSI-кодовую страницу
    (тут cp1251) — кириллица из постов канала калечится. Явный UTF-8 в обход локали — фикс."""
    import json
    import sys

    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    try:
        result = run(payload.get("inputs", {}), payload.get("env", {}))
    except Exception as e:
        result = {"items": [], "warnings": [f"rpc: {type(e).__name__}: {e}"]}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    import sys

    if "--rpc" in sys.argv:
        _rpc_main()
        sys.exit(0)

    # офлайн-смоук без сети и без pyrogram: фейковый клиент, проверяем только контракт run()
    class _FakeMsg:
        def __init__(self, mid, text):
            self.id, self.text, self.caption = mid, text, None
            self.date = datetime.datetime.now().astimezone()

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_chat(self, ch):
            pass

        def get_chat_history(self, ch, limit=50):
            return iter([_FakeMsg(2, "пост два"), _FakeMsg(1, "пост один")])

    import json as _json

    out = run({"channels": ["@smoke"]}, {"client": _FakeClient()})
    assert len(out["items"]) == 2 and out["items"][0]["id"] == 1
    print("offline smoke OK:", _json.dumps(out["items"][0], ensure_ascii=False))
