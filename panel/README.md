# panel — пульт киборга

Живой UI над киборгом. Две версии:
- **v1** (`panel/index.html` + `panel/bodies.js`) — рыцарь-облик, источники, инбокс.
- **v2** (`panel/v2/`) — основной интерфейс: идеи, органы, журнал, **Oracle**, настройки.

Пульт только ЧИТАЕТ код киборга + шлёт действия; `cyborg/`/`idea_engine/` не меняет.

## Запуск

```bash
python panel/serve.py   # → http://127.0.0.1:8737
```

Правки `serve.py` требуют перезапуска; статика `v2/` подхватывается обновлением страницы.

## Файлы

| Файл/папка | Назначение |
|---|---|
| `serve.py` | сервер (`ThreadingHTTPServer`, порт 8737): API + статика + автоцикл |
| `v2/index.html`, `v2/app.js`, `v2/style.css` | основной UI |
| `index.html`, `bodies.js` | legacy v1 UI |

## API

| Метод, путь | Что делает |
|---|---|
| `GET /api/state` | инбокс, журнал, источники, органы, настройки, **список Oracle-планов** |
| `POST /api/run` | «принеси идеи» — прогон `run.py` |
| `POST /api/observe` | обход источников от первого лица |
| `POST /api/auto` | включить/выключить автономность |
| `POST /api/direction` | руль темы идей |
| `POST /api/feeds` | ленты-источник |
| `POST /api/folders` | папки-источник |
| `POST /api/idea` | `take` / `later` / `trash` для идеи |
| `POST /api/oracle` | запустить Oracle-режим: `{project, goal}` |
| `POST /api/stop` | оборвать текущий прогон |

Прогоны запускаются отдельным подпроцессом, stdout стримится в консоль страницы.

## Проверка

```bash
python ../run_tests.py   # 89 тестов panel
```
