"""Площадка советников — три СЛОТА, каждый приводит свой модуль к единому «мнению»
(mind.opinion). Разные модули дают разный выход; слот переводит его в баллы [0..1]
на варианты, чтобы движок совещания (mind.deliberate) мог их взвесить.

Контракт слота:
    advisor.name                              — 'ask_llm' | 'orchestra' | 'rank_ideas'
    advisor.opine(question, options, context) — mind.opinion(...) | None (воздержался)

Воздержание (None) — НЕ ошибка, а штатный режим: модуль не подключён, нет ключа/сети,
или вопрос не по адресу. Движок перераспределит вес на живых. Так киборг думает и
в неполной комплектации (автономность без Claude/без части ключей).

Что подключено СЕЙЧАС и что ждёт провода — см. build_council() внизу и
.brain/design/mind-council.md. Боевое включение в живой цикл — гейт юзера.
"""
import json
import os
import subprocess
import sys

import mind

# --- пути к внешним модулям-органам (провод к другим проектам) ---------------
_ASK_LLM_JS = os.environ.get("KIBORG_ASK_LLM_JS", "M:/projects/DarBench/organ.js")
_ORCHESTRA_PY = os.environ.get("KIBORG_ORCHESTRA_PY", "M:/projects/Claude Code API Dual Mode/organ.py")
_NODE_EXE = os.environ.get("KIBORG_NODE_EXE", "node")


_TEXT_FIELDS = ("title", "text", "value", "name")


def _opt_text(o):
    """Короткое текстовое представление варианта для промптов советникам."""
    if not isinstance(o, dict):
        return str(o)
    for f in _TEXT_FIELDS:
        if o.get(f):
            t = str(o[f])
            why = str(o.get("why") or o.get("reason") or "")
            return (t + " — " + why[:120]) if why else t
    return str(o.get("id"))


def _has_text(o):
    """Есть ли у варианта содержательный текст (то же определение, что у _opt_text)."""
    return isinstance(o, dict) and any(o.get(f) for f in _TEXT_FIELDS)


# =============================================================================
# 1. rank_ideas (вес 0.41) — АРБИТР. Уже живой орган киборга. ПОДКЛЮЧЁН.
# =============================================================================
class RankIdeasAdvisor:
    """Оборачивает idea_engine/organs/rank_ideas: судья ранжирует варианты по рубрике.
    Ранг -> балл: лучший вариант получает 1.0, дальше линейно вниз, неотобранные -> 0.
    Применим, когда варианты похожи на идеи (есть title/why). Иначе воздерживается.
    """
    name = "rank_ideas"

    def __init__(self, rank_run=None):
        self._run = rank_run                       # inputs,env -> {'ideas_best':[...]}; None -> ленивый импорт

    def _load(self):
        if self._run is None:
            _idea = "M:/projects/kiborg/idea_engine"
            if _idea not in sys.path:
                sys.path.insert(0, _idea)
            from organs import rank_ideas          # noqa
            self._run = rank_ideas.run
        return self._run

    def opine(self, question, options, context):
        ctx = context or {}
        # арбитр судит СОДЕРЖАТЕЛЬНЫЕ варианты (идеи/предложения), не служебные развилки.
        # Набор полей — тот же, что видят ask_llm/orchestra (_has_text), чтобы самый весомый
        # советник не выпадал молча там, где двое других вариант прекрасно оценивают.
        if not options or not all(_has_text(o) for o in options):
            return None
        llm = ctx.get("content_llm") or ctx.get("llm")
        ideas = [{"title": _opt_text(o), "why": str(o.get("why", "")), "_id": o["id"]} for o in options]
        # keep=len-1, НЕ len: rank_ideas при keep>=len возвращает как есть БЕЗ вызова модели
        # (rank_ideas.py:58). Чтобы арбитр реально судил живой моделью, оставляем один хвост
        # за бортом (он получит балл 0). Компромисс площадки: судья не отдаёт полный порядок.
        # Исключение: при 1 варианте keep=1=len — модель не зовётся, но единственный вариант
        # тривиально получает 1.0, судить нечего.
        keep = max(1, len(ideas) - 1)
        try:
            run = self._load()
            out = run({"ideas": ideas}, {"keep": keep, **({"llm": llm} if callable(llm) else {})})
        except Exception:
            return None
        best = out.get("ideas_best") or []
        if not best:
            return None
        n = len(best)
        scores, order = {}, []
        for rank, idea in enumerate(best):
            oid = idea.get("_id")
            scores[oid] = 1.0 if n == 1 else (n - 1 - rank) / (n - 1)   # 1.0 .. 0.0 по рангу
            order.append(oid)
        for o in options:                           # варианты, не попавшие в ранжирование -> 0
            scores.setdefault(o["id"], 0.0)
        judged = "llm" if callable(llm) else "fallback(порядок)"
        return mind.opinion(scores, rationale=f"рубрика/{judged}: топ {order[:3]}", raw=out)


# =============================================================================
# 2. ask_llm (вес 0.39) — ИНТУИЦИЯ. Цепочка DarBench organ.js. СЛОТ (нужен env.chain).
# =============================================================================
class AskLlmAdvisor:
    """Оборачивает DarBench/organ.js: одна модель из цепочки-с-фолбэком оценивает варианты.
    Просит модель вернуть JSON баллов 0..100 по вариантам; нормализует к 0..1.
    Воздерживается, если в context нет 'llm_chain' (ключей нет) или ответ не распарсился.
    """
    name = "ask_llm"

    def __init__(self, organ_js=None, node_exe=None):
        self._js = organ_js or _ASK_LLM_JS
        self._node = node_exe or _NODE_EXE

    def _ask(self, chain, prompt, budget_ms):
        """Один прогон органа через subprocess-json (его штатный режим). Текст ответа | None.
        budget_ms — СУММАРНЫЙ бюджет на весь орган. organ.js трактует env.timeout_ms как
        per-provider (organ.js:19), а цепочка длинная → делим бюджет на число провайдеров,
        иначе фолбэк по 3-4 провайдеру не успел бы. Подпроцессу даём весь бюджет + запас."""
        if not os.path.exists(self._js):
            return None
        n = max(1, len(chain))
        per_provider_ms = max(3000, budget_ms // n)     # чтобы медленный провайдер не съел весь бюджет
        payload = {"inputs": {"prompt": prompt, "max_tokens": 256, "temperature": 0.2},
                   "env": {"chain": chain, "timeout_ms": per_provider_ms}}
        try:
            proc = subprocess.run([self._node, self._js], input=json.dumps(payload),
                                  capture_output=True, text=True, encoding="utf-8",
                                  timeout=max(5, budget_ms // 1000 + 5))  # весь бюджет цепочки + запас
        except Exception:
            return None
        if proc.returncode != 0 and not proc.stdout.strip():
            return None
        try:
            res = json.loads(proc.stdout.strip().splitlines()[-1])
        except Exception:
            return None
        return res.get("text") if res.get("ok") else None

    def opine(self, question, options, context):
        ctx = context or {}
        chain = ctx.get("llm_chain")                # список провайдеров с ключами — приносит вызыватель
        if not chain or not options:                # ключей/вариантов нет -> воздержание БЕЗ похода в сеть
            return None
        listing = "\n".join(f'- id "{o["id"]}": {_opt_text(o)}' for o in options)
        prompt = (
            f"Задача: {question}\n\nВарианты:\n{listing}\n\n"
            'Оцени КАЖДЫЙ вариант от 0 до 100 (насколько он подходит под задачу). '
            'Верни РОВНО одну строку JSON и ничего больше: '
            '{"scores":{"<id>":<0-100>, ...}}'
        )
        text = self._ask(chain, prompt, int(ctx.get("llm_timeout_ms", 60000)))
        if not text:
            return None
        raw = text.strip()
        if raw.startswith("```"):
            raw = "\n".join(ln for ln in raw.splitlines() if not ln.strip().startswith("```")).strip()
        parsed = None
        for cand in [raw] + raw.splitlines():
            cand = cand.strip()
            if '"scores"' not in cand:
                continue
            try:
                parsed = json.loads(cand)
                break
            except Exception:
                continue
        if not parsed or not isinstance(parsed.get("scores"), dict):
            return None
        ids = {str(o["id"]) for o in options}
        scores = {}
        for k, v in parsed["scores"].items():
            if str(k) in ids:
                try:
                    scores[_match_id(options, k)] = float(v) / 100.0
                except (TypeError, ValueError):
                    continue
        if not scores:
            return None
        # ЭСКАЛАЦИЯ: интуиция САМА решает звать ли совет (think()). Эвристика — разброс:
        # два лучших варианта близки => интуиция не уверена => поднять флаг. Порог из ctx.
        gap = float(ctx.get("escalate_gap", 0.15))
        top = sorted(scores.values(), reverse=True)
        escalate = len(top) >= 2 and (top[0] - top[1]) < gap
        return mind.opinion(scores, rationale=f"модель оценила {len(scores)} вар.",
                            raw=text, escalate=escalate)


def _match_id(options, key):
    """Ключ из ответа модели (str) -> реальный id варианта (мог быть int)."""
    for o in options:
        if str(o["id"]) == str(key):
            return o["id"]
    return key


# =============================================================================
# 3. orchestra (вес 0.20) — СОВЕТ. N рецензентов, Dual Mode organ.py. СЛОТ (дорогой, off).
# =============================================================================
class OrchestraAdvisor:
    """Оборачивает 'Claude Code API Dual Mode'/organ.py (review_content): N моделей выносят
    вердикт по варианту-как-контенту. verdict -> балл: approve=1.0, changes_requested=0.5,
    blocked=0.0. Самый дорогой советник (N вызовов × модели), потому по умолчанию ВЫКЛЮЧЕН:
    воздерживается, пока вызыватель явно не даст в context 'orchestra' = {models, chat|gateway}.
    """
    name = "orchestra"
    _VERDICT_SCORE = {"approve": 1.0, "changes_requested": 0.5, "blocked": 0.0}

    def __init__(self, organ_py=None):
        self._py = organ_py or _ORCHESTRA_PY
        self._run = None

    def _load(self):
        if self._run is None:
            import importlib.util
            spec = importlib.util.spec_from_file_location("orchestra_organ", self._py)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self._run = mod.run
        return self._run

    def opine(self, question, options, context):
        ctx = context or {}
        cfg = ctx.get("orchestra")                  # {models:[...], chat|darbench_gateway:...}
        if not cfg or not cfg.get("models") or not os.path.exists(self._py):
            return None                             # выключен -> воздержание (штатно)
        env = {k: cfg[k] for k in ("chat", "darbench_gateway", "node_exe", "max_workers") if k in cfg}
        if not (env.get("chat") or env.get("darbench_gateway")):
            return None
        try:
            run = self._load()
        except Exception:
            return None
        scores = {}
        for o in options:                           # по варианту — свод вердиктов рецензентов
            try:
                out = run({"task": question, "content": _opt_text(o),
                           "models": list(cfg["models"]),
                           "focus": cfg.get("focus") or ["польза", "риски", "выполнимость"],
                           "timeout_sec": int(cfg.get("timeout_sec", 180))}, env)
            except Exception:
                continue
            verdicts = [r.get("verdict") for r in (out.get("reviewers") or []) if r.get("status") == "ok"]
            if not verdicts:
                continue
            vals = [self._VERDICT_SCORE.get(str(v), 0.5) for v in verdicts]
            scores[o["id"]] = sum(vals) / len(vals)     # средний вердикт совета по варианту
        if not scores:
            return None
        return mind.opinion(scores, rationale=f"совет оценил {len(scores)} вар.", raw=None)


# =============================================================================
# Сборка совета
# =============================================================================
def build_council(context=None):
    """Три советника в фиксированном порядке весов. Все всегда в совете; кто не подключён —
    сам воздержится в opine(). Вызыватель управляет проводом через context:
      context['content_llm'] / ['llm'] — оживляет rank_ideas (арбитр) живой моделью;
      context['llm_chain']             — оживляет ask_llm (цепочка провайдеров с ключами);
      context['orchestra']             — включает orchestra (models + chat|darbench_gateway).
    Без всего этого живёт только rank_ideas на детерминированном фолбэке — киборг всё равно думает.
    """
    return [AskLlmAdvisor(), OrchestraAdvisor(), RankIdeasAdvisor()]


if __name__ == "__main__":
    # Смоук: без ключей/сети живым остаётся только арбитр (rank_ideas, фолбэк по порядку).
    opts = [{"id": "A", "title": "оффлайн-трекер привычек", "why": "нет зависимостей"},
            {"id": "B", "title": "ещё один симулятор сети", "why": "таких уже много"}]
    verdict = mind.deliberate("какая идея оригинальнее и полезнее?", opts, build_council(), {})
    print("live:", verdict["live"], "| abstained:", [a["name"] for a in verdict["abstained"]])
    print("choice:", verdict["choice_id"], "| why:", verdict["why"])
