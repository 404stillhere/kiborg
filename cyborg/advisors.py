# fmt: off
# Замороженное ядро (гейт человека, см. README): площадка советников, правит параллельная
# сессия. Black/ruff НЕ форматируют — стабильность важнее единообразия. # fmt: off = гарантия.
"""Площадка советников — три СЛОТА, каждый приводит свой модуль к единому «мнению»
(mind.opinion). Разные модули дают разный выход; слот переводит его в баллы [0..1]
на варианты, чтобы движок совещания (mind.deliberate) мог их взвесить.

Контракт слота:
    advisor.name                              — 'ask_llm' | 'orchestra' | 'rank_ideas'
    advisor.opine(question, options, context) — mind.opinion(...); пустые scores = воздержался

Воздержание (opinion с пустыми scores; старый None тоже поддержан) — НЕ ошибка, а
штатный режим: модуль не подключён, нет ключа/сети или вопрос не по адресу. Движок
перераспределит вес на живых. Так киборг думает и в неполной комплектации.

Что подключено СЕЙЧАС и что ждёт провода — см. build_council() внизу и
.brain/design/mind-council.md. Боевое включение в живой цикл — гейт юзера.
"""
import json
import os
import subprocess
import sys

import config
import mind

# --- пути к внешним модулям-органам (провод к другим проектам) ---------------
_ASK_LLM_JS = os.environ.get(config.ASK_LLM_JS_ENV, config.DEFAULT_ASK_LLM_JS)
_ORCHESTRA_PY = os.environ.get(config.ORCHESTRA_PY_ENV, config.DEFAULT_ORCHESTRA_PY)
_NODE_EXE = os.environ.get(config.NODE_EXE_ENV, "node")


_TEXT_FIELDS = ("title", "text", "value", "name")


def _opt_text(o):
    """Короткое текстовое представление варианта для промптов советникам."""
    if not isinstance(o, dict):
        return str(o)
    for f in _TEXT_FIELDS:
        if o.get(f):
            t = str(o[f])
            why = str(o.get("why") or o.get("reason") or "")
            parts = [t + (" — " + why[: config.RANK_IDEAS_WHY_MAX_CHARS] if why else "")]
            verification = str(o.get("verification") or "").strip()
            if verification:
                parts.append("Проверка: " + verification[: config.RANK_IDEAS_VERIFICATION_MAX_CHARS])
            refs = o.get("source_refs")
            if isinstance(refs, list) and refs:
                evidence = ", ".join(
                    str(ref.get("title") or ref.get("path") or ref.get("id") or "")[: config.RANK_IDEAS_REF_TITLE_MAX_CHARS]
                    for ref in refs[: config.RANK_IDEAS_MAX_REFS]
                    if isinstance(ref, dict)
                )
                if evidence:
                    parts.append("Основание: " + evidence)
            return " | ".join(parts)
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
            _idea = config.IDEA_ENGINE_DIR
            if _idea not in sys.path:
                sys.path.insert(0, _idea)
            from organs import rank_ideas          # noqa
            self._run = rank_ideas.run
        return self._run

    def opine(self, question, options, context):
        import council_config
        if not council_config.is_enabled(self.name):
            return mind.opinion(None, reason_code="disabled")
        ctx = context or {}
        # арбитр судит СОДЕРЖАТЕЛЬНЫЕ варианты (идеи/предложения), не служебные развилки.
        # Набор полей — тот же, что видят ask_llm/orchestra (_has_text), чтобы самый весомый
        # советник не выпадал молча там, где двое других вариант прекрасно оценивают.
        if not options or not all(_has_text(o) for o in options):
            return mind.opinion(None, reason_code="not_applicable")
        llm = ctx.get("content_llm") or ctx.get("llm")
        ideas = [{"title": _opt_text(o), "why": str(o.get("why", "")), "_id": o["id"]} for o in options]
        # rank_ideas теперь вызывает модель даже при len==keep (fix #8).
        # Раньше нужен был keep=len-1 workaround — теперь убран, худшая идея получает честный балл.
        keep = len(ideas)
        rank_env = {"keep": keep}
        if callable(llm):
            rank_env["llm"] = llm
        if ctx.get("direction"):                    # руль темы долетает и до арбитра (рубрика rank_ideas)
            rank_env["direction"] = ctx["direction"]
        try:
            run = self._load()
            out = run({"ideas": ideas}, rank_env)
        except Exception:
            return mind.opinion(None, reason_code="exception")
        best = out.get("ideas_best") or []
        if not best:
            return mind.opinion(None, reason_code="parse_fail")
        if callable(llm):
            # Для совета нужен ПОЛНЫЙ порядок. rank_ideas добирает пропущенные моделью
            # элементы с judged="fill"; это страховка органа от потери карточек, но не
            # суждение модели. Такой порядок нельзя выдавать за полноценный голос.
            returned_ids = [idea.get("_id") for idea in best]
            expected_ids = [idea["_id"] for idea in ideas]
            if (
                len(best) != len(ideas)
                or any(idea.get("judged") != "llm" for idea in best)
                or len(set(returned_ids)) != len(returned_ids)
                or set(returned_ids) != set(expected_ids)
            ):
                return mind.opinion(None, reason_code="incomplete", raw=out)
        n = len(best)
        scores, order = {}, []
        # Ранг — порядковый сигнал, не вероятность качества. Переводим его в прозрачный
        # percentile/Borda score: доля остальных вариантов, стоящих ниже. Так мы не
        # выдумываем фиксированную "уверенность" decay=0.7.
        for rank, idea in enumerate(best):
            oid = idea.get("_id")
            if n == 1:
                scores[oid] = 1.0
            else:
                scores[oid] = (n - 1 - rank) / (n - 1)
            order.append(oid)
        for o in options:                           # варианты, не попавшие в ранжирование -> 0
            scores.setdefault(o["id"], 0.0)
        # честный статус судьи из ВЫВОДА органа, а не «был ли передан llm»: rank_ideas метит
        # карточки judged=llm/fill (живой суд) или fallback (стр.74/77). Нет ни одной метки llm =
        # живой вызов не сработал (502/непарс) ИЛИ llm не было → это ПОРЯДОК, не суждение. Раньше
        # мерили по callable(llm) → при мёртвой сети рапортовали «llm», хотя по факту фолбэк (root #1).
        was_live = all(b.get("judged") == "llm" for b in best)
        # Ключ БЫЛ (llm передан), но живой суд не сработал (502/непарс → rank_ideas на порядок-фолбэк):
        # это НЕ суждение арбитра, а порядок. ВОЗДЕРЖИВАЕМСЯ (None) — иначе совет зачтёт фолбэк как
        # полноценный голос (вес 0.41) и рапортует «арбитр судил» на мусоре (audit medium, часть-b).
        # mind.deliberate воздержание обрабатывает: если и другие молчат — degraded → плоский откат.
        # БЕЗ ключа (offline) порядок-фолбэк — ШТАТНЫЙ детерминированный судья → голосуем как раньше.
        if callable(llm) and not was_live:
            return mind.opinion(None, reason_code="parse_fail", raw=out)
        judged = "llm" if was_live else "fallback(порядок)"
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
    _MAX_TOKENS = config.ASK_LLM_ADVISOR_MAX_TOKENS
    _TEMPERATURE = config.ASK_LLM_ADVISOR_TEMPERATURE

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
        per_provider_ms = max(config.ASK_LLM_MIN_PER_PROVIDER_MS, budget_ms // n)  # медленный не съест весь бюджет
        inputs = {"prompt": prompt, "temperature": self._TEMPERATURE}
        if self._MAX_TOKENS is not None:                # None (напр. _IntuitionNoCap) → ключ не кладём
            inputs["max_tokens"] = self._MAX_TOKENS
        payload = {"inputs": inputs,
                   "env": {"chain": chain, "timeout_ms": per_provider_ms}}
        try:
            proc = subprocess.run(
                [self._node, self._js], input=json.dumps(payload),
                capture_output=True, text=True, encoding="utf-8",
                timeout=max(config.ASK_LLM_SUBPROCESS_TIMEOUT_PAD_SEC, budget_ms // 1000 + config.ASK_LLM_SUBPROCESS_TIMEOUT_PAD_SEC),
            )  # весь бюджет цепочки + запас
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
        import council_config
        if not council_config.is_enabled(self.name):
            return mind.opinion(None, reason_code="disabled")
        ctx = context or {}
        chain = ctx.get("llm_chain")                # список провайдеров с ключами — приносит вызыватель
        if not chain:                               # ключей нет -> воздержание БЕЗ похода в сеть
            return mind.opinion(None, reason_code="no_keys")
        if not options:
            return mind.opinion(None, reason_code="not_applicable")
        listing = "\n".join(f'- id "{o["id"]}": {_opt_text(o)}' for o in options)
        prompt = (
            f"Задача: {question}\n\nВарианты:\n{listing}\n\n"
            'Оцени КАЖДЫЙ вариант от 0 до 100 (насколько он подходит под задачу). '
            'Верни РОВНО одну строку JSON и ничего больше: '
            '{"scores":{"<id>":<0-100>, ...}}'
        )
        text = self._ask(chain, prompt, int(ctx.get("llm_timeout_ms", config.ASK_LLM_ADVISOR_TIMEOUT_MS)))
        if not text:
            return mind.opinion(None, reason_code="provider_fail")
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
            return mind.opinion(None, reason_code="parse_fail")
        ids = {str(o["id"]) for o in options}
        scores = {}
        for k, v in parsed["scores"].items():
            if str(k) in ids:
                try:
                    scores[_match_id(options, k)] = float(v) / 100.0
                except (TypeError, ValueError):
                    continue
        if not scores:
            return mind.opinion(None, reason_code="parse_fail")
        if len(scores) != len(options):
            # Пропуск id — ошибка контракта ответа, а не отрицательная оценка идеи.
            # Не даём частичному ответу голосовать и выглядеть "уверенным".
            return mind.opinion(None, reason_code="incomplete", raw=text)
        # ЭСКАЛАЦИЯ: интуиция САМА решает звать ли совет (think()). Эвристика — разброс:
        # два лучших варианта близки => интуиция не уверена => поднять флаг. Порог из ctx.
        # Считаем на ФАКТИЧЕСКИХ баллах (реальный разброс мнения), БЕЗ импутации.
        gap = float(ctx.get("escalate_gap", config.ASK_LLM_ESCALATE_GAP))
        top = sorted(scores.values(), reverse=True)
        escalate = len(top) >= 2 and (top[0] - top[1]) < gap
        return mind.opinion(scores, rationale=f"модель оценила {len(options)}/{len(options)} вар.",
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
    _BLOCKING_SEVERITIES = {"critical", "high"}

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

    @staticmethod
    def _score_verdicts(verdicts):
        """Средний балл известных verdict без evidence-aware veto."""
        vals = [
            OrchestraAdvisor._VERDICT_SCORE[key]
            for v in verdicts
            if (key := str(v).strip().lower()) in OrchestraAdvisor._VERDICT_SCORE
        ]
        return sum(vals) / len(vals) if vals else 0.0

    @staticmethod
    def _score_reviewers(reviewers, findings):
        """Отделить настоящий блокер от неразобранного ответа модели.

        Внешний organ кодирует битый JSON как status=ok/verdict=blocked. Поэтому blocked
        становится veto только вместе с high/critical finding того же reviewer. Иначе
        это abstention. None означает, что содержательных голосов не осталось.
        """
        blocking_models = set()
        for finding in findings or []:
            severity = str(finding.get("severity", "")).strip().lower()
            if severity not in OrchestraAdvisor._BLOCKING_SEVERITIES:
                continue
            mentioned = finding.get("mentioned_by") or []
            if isinstance(mentioned, str):
                mentioned = [mentioned]
            blocking_models.update(str(model) for model in mentioned)

        values = []
        for reviewer in reviewers or []:
            if reviewer.get("status") != "ok":
                continue
            verdict = str(reviewer.get("verdict", "")).strip().lower()
            if verdict == "blocked":
                identities = {
                    str(reviewer.get("model", "")),
                    str(reviewer.get("requested_model", "")),
                }
                if identities & blocking_models:
                    return 0.0
                continue
            if verdict in {"approve", "changes_requested"}:
                values.append(OrchestraAdvisor._VERDICT_SCORE[verdict])
        return (sum(values) / len(values)) if values else None

    def opine(self, question, options, context):
        import council_config
        if not council_config.is_enabled(self.name):
            return mind.opinion(None, reason_code="disabled")
        ctx = context or {}
        cfg = ctx.get("orchestra")                  # {models:[...], chat|darbench_gateway:...}
        if not cfg or not cfg.get("models") or not os.path.exists(self._py):
            return mind.opinion(None, reason_code="no_keys")  # нет конфига/орга → как нет ключей
        env = {k: cfg[k] for k in ("chat", "darbench_gateway", "node_exe", "max_workers") if k in cfg}
        if not (env.get("chat") or env.get("darbench_gateway")):
            return mind.opinion(None, reason_code="no_keys")  # нет gateway → не из чего звать
        try:
            run = self._load()
        except Exception:
            return mind.opinion(None, reason_code="exception")
        scores = {}
        for o in options:                           # по варианту — свод вердиктов рецензентов
            try:
                out = run({"task": question, "content": _opt_text(o),
                           "models": list(cfg["models"]),
                           "focus": cfg.get("focus") or ["польза", "риски", "выполнимость"],
                           "timeout_sec": int(cfg.get("timeout_sec", config.ORCHESTRA_TIMEOUT_SEC))}, env)
            except Exception:
                continue
            score = self._score_reviewers(out.get("reviewers") or [], out.get("findings") or [])
            if score is None:
                continue
            scores[o["id"]] = score
        if not scores:
            return mind.opinion(None, reason_code="parse_fail")  # никто не ответил → не удалось disaggreg
        if len(scores) != len(options):
            return mind.opinion(None, reason_code="incomplete")
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
