"""Трекер «уже видели» — ID СЫРЫХ items (заголовков/репо), а не сгенерированных идей.

Зачем отдельно от текстового дедупа ГОТОВЫХ идей (idea_engine/store.py, поле "seen" —
сигнатуры заголовков идей): тот дедупит уже ПОСЛЕ дорогого вызова ideate по ТЕКСТУ — если
LLM перефразирует старый заголовок чуть иначе, сигнатура может не поймать повтор, а деньги/
токены на генерацию уже потрачены. Этот модуль дедупит ДО ideate, по ID самого источника
(HN item id, reddit id, lobsters short_id, github owner/repo) — точнее и дешевле: не тратим
LLM на заголовок, который уже разбирали в прошлый раз.

Формат хранения (с 2026-07-21): dict[str, int] — ключ "source:id" → unix-ts ПОСЛЕДНЕГО
видения. Ts нужен для TTL: при каждом mark_seen файл заодно ЧИСТИТ себя от записей старше
TTL_DAYS (90) — иначе рос бы без огранички (263 записи за 2 месяца → десятки тысяч за год,
load() на каждом тике автосбора начал бы тормозить). Страховочный MAX_RECORDS (5000) — если
TTL не спасёт при массовом притоке, обрежем по самым свежим. Файл атомарен через .tmp+rename.

Стабилизация ключей: files:* хранит ХЕШ basename, а не абсолютный путь (M:\\projects\\kiborg\\
умирает при переносе проекта → ключ инвалидируется → тот же файл снова «свежий» → двойная
генерация). basename стабилен при перемещении; хеш — короткий и без спецсимволов в JSON.
Для источников со стабильным id (hn/lobsters/gh_trending/reddit) — id как есть, без хеша.

Персист: cyborg/data/seen_items.json.
"""

import hashlib
import json
import os
import re
import time

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PATH = os.path.join(DATA, "seen_items.json")

TTL_DAYS = 90  # запись старше 90 дней выкидывается при ближайшем mark_seen/_save
MAX_RECORDS = 5000  # жёсткий потолок размера (страховка, если TTL не справится)


def _item_key(it):
    if not isinstance(it, dict):
        return None
    iid = it.get("id")
    if iid in (None, ""):
        return None  # без id дедуп невозможен — пропускаем как «всегда свежий», не теряем сырьё
    src = it.get("source", "?")
    # files: id = абсолютный путь к файлу — НЕстабилен (перенос проекта инвалидирует все ключи
    # разом → весь архив снова «свежий» → двойная генерация). Хешируем BASENAME (только имя
    # файла, без каталогов) — стабилен при перемещении проекта. Риск коллизии: два файла с
    # одинаковым basename в разных папках дадут один ключ — редкий случай для нашей схемы
    # источников (папки тематические, имена файлов уникальны внутри). Для источников со
    # стабильным id (hn/lobsters/gh_trending/reddit/telegram) — id как есть, без хеша.
    if src == "files":
        # КРОСС-ПЛАТФОРМЕННАЯ СТАБИЛЬНОСТЬ: см. _normalize_key — Windows-пути с '\\' на Linux
        # дают другой basename. Нормализуем '\\' → '/' перед basename, чтобы хеш был одинаковый.
        iid_norm = str(iid).replace("\\", "/")
        iid = hashlib.sha1(os.path.basename(iid_norm).encode("utf-8")).hexdigest()[:12]
    return f"{src}:{iid}"


def _now():
    return int(time.time())


def _ttl_cutoff():
    return _now() - TTL_DAYS * 86400


def _normalize_key(k):
    """Перевести ключ в канонический вид. files:* в старом формате хранил ПОЛНЫЙ путь
    (files:M:\\projects\\kiborg\\README.md) — нестабильно при переносе проекта. Новый формат
    хеширует basename. Эту нормализацию надо применить и к СУЩЕСТВУЮЩИМ ключам при миграции
    (иначе старые files-ключи останутся с путями, а новые будут хеши — два формата в одном
    файле, дедуп ломается: один и тот же файл = два разных ключа)."""
    if isinstance(k, str) and k.startswith("files:"):
        # вытаскиваем путь после префикса; если это уже хеш (12 hexchar) — оставляем как есть
        rest = k[len("files:") :]
        if re.match(r"^[0-9a-f]{12}$", rest):
            return k  # уже нормализован
        # КРОСС-ПЛАТФОРМЕННАЯ СТАБИЛЬНОСТЬ (баг всплыл на CI 2026-07-21): os.path.basename
        # на Linux НЕ понимает обратные слеши (считает разделителем только '/') — для путей
        # с '\\' (Windows-прогон создал ключ) возвращает весь путь целиком, basename не
        # выделяется → хеш разный между платформами. Нормализуем '\\' → '/' ПЕРЕД basename,
        # тогда обе платформы дают один и тот же basename и тот же хеш.
        rest = rest.replace("\\", "/")
        return "files:" + hashlib.sha1(os.path.basename(rest).encode("utf-8")).hexdigest()[:12]
    return k


def _migrate(raw):
    """Принять ЛЮБОЙ старый/новый формат → dict[str, int] в каноническом виде. Старый list[str]
    (до 2026-07-21, без ts) мигрируется: все ключи получают ts=сейчас (чтобы не потерять защиту —
    иначе при первом запуске с TTL весь архив разом стал бы «просроченным» и выкинулся), а
    files:*-ключи перехешируются до basename (см. _normalize_key). dict уже в новом формате —
    пропускаем как есть, НО files-ключи нормализуем (на случай, если в файле ещё живы старые
    записи с полными путями — двухформатовое состояние)."""
    now = _now()
    if isinstance(raw, dict):
        out = {}
        for k, v in raw.items():
            nk = _normalize_key(k)
            if isinstance(v, (int, float)) and v > 0:
                out[nk] = int(v)
            else:
                out[nk] = now  # мусорное значение ts — обновим на сейчас
        return out
    if isinstance(raw, list):
        return {_normalize_key(str(k)): now for k in raw if isinstance(k, str) and k}
    return {}


def load():
    """dict[str, int] (ключ → ts последнего видения). Пустой dict при отсутствии/битом файле.
    НЕ чистит TTL (read-only) — чистка только в _save (write-path). count_fresh/filter_fresh
    читают без мутации, TTL-уборка им не нужна — она и так случится при ближайшей записи."""
    try:
        with open(PATH, encoding="utf-8") as f:
            return _migrate(json.load(f))
    except Exception:
        return {}


def _prune(seen):
    """Убрать просроченные (старше TTL_DAYS) и обрезать до MAX_RECORDS по свежим. Возвращает
    НОВЫЙ dict (не мутирует вход). Вызывается из _save перед персистом — файл сам себя чистит,
    без отдельного cron'а/процесса."""
    cutoff = _ttl_cutoff()
    live = {k: v for k, v in seen.items() if v >= cutoff}
    if len(live) > MAX_RECORDS:
        # оставляем самые свежие MAX_RECORDS (по ts desc); при равенстве ts — лексикальки
        live = dict(sorted(live.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_RECORDS])
    return live


def _save(seen):
    """Атомарный write-rename через .tmp. Перед персистом — TTL-чистка + cap (файл не растёт
    бесконтрольно, даже если mark_seen дёргают часто)."""
    os.makedirs(DATA, exist_ok=True)
    seen = _prune(seen)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, sort_keys=True)
    os.replace(tmp, PATH)


def count_fresh(items):
    """Дешёвый non-mutating подсчёт: сколько items ЕЩЁ не видели (для gate-проверки БЕЗ
    того, чтобы отмечать их виденными раньше времени — отметка идёт только в filter_fresh,
    когда items реально уходят на генерацию идей)."""
    seen = load()
    return sum(1 for it in items if _item_key(it) not in seen or _item_key(it) is None)


def filter_fresh(items, mark=True):
    """Возвращает items МИНУС уже виденные. По умолчанию (mark=True) СРАЗУ отмечает
    возвращенные (с id) виденными и персистит — прежнее поведение. mark=False: только
    фильтрует, файл НЕ трогает — пометку делает отдельный mark_seen ПОСЛЕ успешной генерации,
    чтобы транзиентная осечка ideate не сожгла сырьё безвозврата (см. wiring._run_ideate).
    Items без id (не должно случаться для наших источников, но на всякий) — всегда проходят:
    лучше лишний раз показать, чем молча потерять сырьё."""
    seen = load()
    original = dict(seen)
    fresh = []
    for it in items:
        key = _item_key(it)
        if key is None or key not in original:
            fresh.append(it)
        if mark and key is not None:
            seen[key] = _now()  # ОБНОВЛЯЕМ ts (повторное видение = «свежая» запись)
    if mark and seen != original:
        _save(seen)
    return fresh


def mark_seen(items):
    """Отметить items (с id) виденными и персистить. Вызывать ПОСЛЕ успешной генерации идей,
    чтобы транзиентный сбой ideate (осечка парса / обрыв сети → болванки) не сжёг сырьё:
    непомеченные посты пройдут filter_fresh на следующем тике и получат ещё один шанс.
    Побочно: _save чистит TTL/cap — файл сам себя обслуживает."""
    seen = load()
    original = dict(seen)
    now = _now()
    for it in items:
        key = _item_key(it)
        if key is not None:
            seen[key] = now
    if seen != original:
        _save(seen)


def _title_sig(t):
    """Нормализованная сигнатура заголовка для кросс-источникового дедупа: lower, только буквы/
    цифры, служебные знаки срезаны, пробелы схлопнуты. «SIMD Tricks!» и «simd tricks» → одна
    сигнатура. СТРОГАЯ (точное совпадение сигнатуры = дубль), не Jaccard — иначе «SIMD tricks»
    и «SIMD for collision» схлопнулись бы (это разные посты, похожие слова)."""
    return " ".join(re.findall(r"[a-zа-яё0-9]+", (t or "").lower()))


def cross_dedup(items):
    """Убрать кросс-источниковые дубли ВНУТРИ одного прогона (чистая функция, без персиста).

    Реальный кейс: один и тот же пост приходит с HN (item id) и Lobsters (short_id) → в
    seen_items это два разных ключа (hn:1 и lobsters:abc), оба проходят filter_fresh → LLM
    тратится на две похожие идеи. Здесь — убираем дубль ДО ideate, по нормализованному
    заголовку: первое вхождение выигрывает, мимо — дубли.

    СТРОГАЯ: только точное совпадение нормализованной сигнатуры (не Jaccard). «SIMD tricks» и
    «SIMD for collision» — РАЗНЫЕ посты, не схлопываются. Пустой title / без title — пропускаем
    как есть (не дедупим — лучше показать, чем потерять сырьё). Сохраняет порядок первого
    вхождения. Не-список → []. Не трогает seen_items.json (чистый read-only вычислитель)."""
    if not isinstance(items, list):
        return []
    out = []
    seen_sigs = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        sig = _title_sig(it.get("title"))
        # пустой title (только служебные слова/нет слов) — не дедупим, пропускаем как есть
        if not sig:
            out.append(it)
            continue
        if sig in seen_sigs:
            continue
        seen_sigs.add(sig)
        out.append(it)
    return out
