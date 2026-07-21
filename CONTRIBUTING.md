# CONTRIBUTING — как вносить изменения в kiborg

Проект — автономный агент «приносит идеи». Перед правками прочитайте `README.md`
(общая архитектура: `idea_engine/` → `cyborg/` → `panel/`) и секцию «Заморожено» ниже.

## 🟢 CI enforced

Любой `push` в `master` и любой pull request автоматически запускают GitHub Actions
(см. `.github/workflows/ci.yml`, job `test`):

| Шаг | Что | Команда |
|-----|-----|---------|
| 1 | Checkout кода | `actions/checkout@v4` |
| 2 | Python 3.9 (минимальная заявленная версия) | `actions/setup-python@v5` |
| 3 | Установка зависимостей | `pip install -r requirements.txt` |
| 4 | Прогон всех тестов | `python run_tests.py` (452 теста + 1 skipped) |
| 5 | Линтер | `ruff check .` |
| 6 | Форматирование | `black --check .` |

Статус прогона виден в интерфейсе GitHub: зелёная галочка / красный крест.
Падение любого шага = красный PR.

### Branch protection — текущий статус

На момент последнего обновления (2026-07-21): репозиторий **PUBLIC**, branch protection
**ВКЛЮЧЁН**. Прямой пуш в master заблокирован сервером (даже для владельца).

| Механизм | Статус |
|----------|--------|
| CI на каждый push/PR | ✅ работает |
| Прямой пуш в master заблокирован сервером | ✅ включено |
| Обязательный зелёный CI для merge | ✅ `test` job required |
| Force-push в master заблокирован | ✅ `allow_force_pushes=false` |
| Админ не может обойти правила | ✅ `enforce_admins=true` |

**Правила защиты:**
- `required_status_checks`: strict mode, context `test` (зелёный CI обязателен)
- `enforce_admins`: true (владелец тоже обязан делать PR)
- `allow_force_pushes`: false (историю master нельзя переписать)
- `required_pull_request_reviews`: 1 approval (настроился автоматом при включении защиты)

Для изменения или выключения защиты (не рекомендуется без веской причины):

```bash
gh api repos/404stillhere/kiborg/branches/master/protection
gh api -X DELETE repos/404stillhere/kiborg/branches/master/protection  # выключить
```

## TL;DR

```bash
# 1. Создать ветку от master (прямой пуш в master запрещён сервером — branch protection)
git checkout master && git pull && git checkout -b feat/my-change

# 2. Локально проверить перед коммитом
pip install -r requirements.txt
python run_tests.py        # 453 теста, должны быть ALL GREEN
ruff check .               # стиль
black --check .            # форматирование (или black . чтобы применить)

# 3. Запушить ветку и открыть PR
git push -u origin feat/my-change
gh pr create --base master

# 4. Дождаться зелёного CI (job `test` в .github/workflows/ci.yml)
# 5. Получить ревью, squash-merge
```

---

## Локальный запуск тестов

### Зависимости

Runtime — **чистый Python stdlib**, без внешних пакетов. Для разработки нужны только:

```bash
pip install -r requirements.txt
# pytest>=8,<10  ruff>=0.6,<0.8  black>=24.0,<26.0
```

### Почему НЕ голый pytest

Тесты запускаются **только** через `run_tests.py`, а не `pytest` напрямую:

```bash
python run_tests.py            # все пакеты (cyborg + idea_engine + panel)
python run_tests.py cyborg     # только указанные
```

Причина — коллизия имён модулей. `cyborg/` и `idea_engine/` содержат **одинаково названные**
модули (`run.py`, `store.py`, …). Тесты каждого пакета кладут свою папку в `sys.path` и
делают `import run`. При едином прогоне pytest первый импортированный `run` кэшируется в
`sys.modules`, и тесты второго пакета получают ЧУЖОЙ модуль → ложные падения вида
`module 'run' has no attribute 'collect_source'`. Это НЕ баг кода: по пакетам-раздельно
все зелёные. Раздельные процессы дают каждому пакету свежий `sys.modules` → коллизии нет.

`run_tests.py` запускает каждый пакет в отдельном subprocess и возвращает ненулевой
exit code при ЛЮБОЙ аномалии: падения, ошибки, `pytest` не установлен (rc=1),
ничего не собрано (rc=5). Это ловит ложное «0 тестов = OK» для CI.

### Локальные проверки перед PR

```bash
ruff check .                  # конфиг в pyproject.toml
black --check .               # конфиг в pyproject.toml
```

Если ruff/black ругаются — применить авто-исправления:

```bash
ruff check . --fix            # авто-фикс (безопасные правила)
black .                       # переформатировать
```

---

## Процесс внесения изменений

1. **Создать ветку от свежего master:**
   ```bash
   git checkout master
   git pull origin master
   git checkout -b feat/короткое-описание   # или fix/, docs/, style/, refactor/
   ```

2. **Сделать изменения.** Коммиты — мелкие, с понятными сообщениями (см. «Стиль коммитов» ниже).

3. **Убедиться локально, что всё зелёное:**
   ```bash
   python run_tests.py && ruff check . && black --check .
   ```

4. **Запушить ветку и открыть PR:**
   ```bash
   git push -u origin feat/короткое-описание
   gh pr create --base master --title "feat: что сделал" --body "Зачем и как"
   ```

5. **Дождаться CI.** В `.github/workflows/ci.yml` определена обязательная job `test`:
   - `python run_tests.py` — 453 теста
   - `ruff check .` — стиль
   - `black --check .` — форматирование

   **Слияние невозможно, пока CI красный** (branch protection требует зелёного `test`).

6. **Запросить ревью** (если работаете в команде). Для solo-разработки достаточно зелёного CI.

7. **Squash-merge** PR в master. История master остаётся линейной, один коммит = одна фича.

---

## Стиль коммитов

Сообщения в существующей истории проекта — на русском, с префиксом области:

```
feat: добавить X для Y
fix: X падал при Y, теперь Z
docs: обновить README про X
style: ruff/black auto-fix
refactor: вынести X в отдельный модуль
test: покрыть X сценарием Y
chore: поднять версию зависимости X
```

Область (cyborg/idea_engine/panel/wiring/harvest/...) — при необходимости в скобках:
```
feat(cyborg): добавить орган X
fix(harvest): гонка в Y
```

---

## Заморожено (бЕз явного разрешения не трогать)

Эти файлы помечены `# fmt: off` в шапке — black/ruff их НЕ форматируют. Логика стабильно
работает, и косметические правки здесь опаснее, чем разношёрстность стиля:

| Файл | Что это | Почему заморожено |
|------|---------|-------------------|
| `idea_engine/store.py` | ядро двух дорожек (A/B), потолок, дедуп | чистая логика, любая правка меняет семантику |
| `idea_engine/organs/collect_source.py` | орган сбора сырья | гейт человека (сбор/маршрутизация) |
| `cyborg/mind.py` | движок взвешенного совещания | гейт человека (правит параллельная сессия) |
| `cyborg/advisors.py` | площадка советников | гейт человека (правит параллельная сессия) |
| `cyborg/keychain.py` | цепочка провайдеров и совета | секреты: значения ключей не логируются |

**Секреты:** `cyborg/llm_keys.env` (ключи LLM-цепочки/совета) — в `.gitignore`, значения
не логируются и не коммитятся. Никогда не коммитьте `.feature-lab/`, `.brain/`, `handoffs/`.

---

## Архитектура — где что искать

| Пакет | Назначение |
|-------|------------|
| `idea_engine/` | ядро-«первый срез»: органы + `store.py` (чистая логика). См. `idea_engine/README.md` |
| `cyborg/` | платформа поверх органов: `wiring.py` (живая цепочка), `harvest.py` (автосбор), `mind.py`+`advisors.py` (совет). См. `cyborg/README.md` |
| `panel/` | веб-пульт: `serve.py` (http) + `index.html`. См. README корня |

Слои чистые: `cyborg` → `idea_engine` (в одну сторону), `idea_engine` самодостаточен.

Живая цепочка одного прогона:
```
collect_source → ideate → rank_ideas → readability_gate → scrub_secrets → deliver
```

---

## Если CI красный

1. Откройте лог job `test` в GitHub Actions.
2. Ищите первую ошибку сверху (последующие часто cascading).
3. Воспроизведите локально:
   ```bash
   git checkout <ваша-ветка>
   python run_tests.py    # или ruff/black — смотря что упало
   ```
4. Если падают тесты — поищите в `cyborg/tests/` или `idea_engine/tests/` нужный файл.
5. Если упал `ruff`/`black` — прогоните `ruff check . --fix` / `black .` локально и закоммитьте.

Если CI красный на коде, который ВЫ не меняли (что-то в master сломалось) — это баг,
откройте issue или напишите в чат проекта.
