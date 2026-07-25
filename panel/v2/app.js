/* ════════════════════════════════════════════════════════════════════════
   kiborg — пульт v2 / app.js
   Ванильный ES6. Классы: API, State, Renderer, UIController, Knight,
   PollingManager, Toasts. Init в конце файла.

   Контракт данных = реальный serve.py (panel/serve.py::_api_state), НЕ
   абстрактный набросок. Структура /api/state:
     S = {
       now, running, run_goal, key:{present,model}, organs:[{name,purpose,role,...}],
       inbox:{cap,tick,ideas:[{id,title,score,why,brain,source,effort,status,...}],finish,seen_count,error},
       sources:{checked_at, sources:{<name>:{ok,error,items,...}}}|null,
       auto:{on,interval_min}, runs:[{ts,goal,chain,deliverable,value,council,degraded}],
       registry:{total,by_status,by_project,cards:[...],error},
       lab:{exists,locked,features,needs_manual},
       direction:{current,presets}, folders:{folders:[{path,on}],paths},
       feeds:{all,enabled}, council:{all,enabled}, rejected
     }
   /api/run = {running,goal,lines,rc,error}
   ════════════════════════════════════════════════════════════════════════ */

'use strict';

/* ────────────────────────────────────────────────────────────────────────
   1. API — тонкий клиент к серверу. AbortController на каждом запросе —
      чтобы смена вкладки/прогон не копила висящие fetch'и.
   ──────────────────────────────────────────────────────────────────────── */
class API {
  static BASE = '';  // тот же origin
  static TIMEOUT_MS = 8000;

  static async _request(method, path, body) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), API.TIMEOUT_MS);
    try {
      const opts = {
        method,
        headers: { 'Content-Type': 'application/json' },
        signal: ctrl.signal,
      };
      if (body !== undefined) opts.body = JSON.stringify(body);
      const r = await fetch(API.BASE + path, opts);
      if (!r.ok) {
        // 4xx/5xx — пробуем вытащить msg из JSON, иначе текст
        let msg = `HTTP ${r.status}`;
        try { const j = await r.json(); if (j && j.msg) msg = j.msg; } catch (_) {}
        return { ok: false, msg, _status: r.status };
      }
      return await r.json();
    } catch (e) {
      const aborted = e.name === 'AbortError';
      return { ok: false, msg: aborted ? 'таймаут — сервер молчит' : 'сеть: ' + e.message };
    } finally {
      clearTimeout(timer);
    }
  }

  static state()              { return API._request('GET',  '/api/state'); }
  static run()                { return API._request('GET',  '/api/run'); }
  static startRun(goal)       { return API._request('POST', '/api/run',       { goal }); }
  static stopRun()            { return API._request('POST', '/api/stop',      {}); }
  static observe()            { return API._request('POST', '/api/observe',   {}); }
  static setIdea(id, status)  { return API._request('POST', '/api/idea',      { id, status }); }
  static purge(threshold)     { return API._request('POST', '/api/ideas/purge', { threshold }); }
  static setAuto(on, interval){ return API._request('POST', '/api/auto',      { on, interval_min: interval }); }
  static setDirection(p)      { return API._request('POST', '/api/direction', p); }
  static setFolders(folders)  { return API._request('POST', '/api/folders',   { folders }); }
  static setFeeds(enabled)    { return API._request('POST', '/api/feeds',     { enabled }); }
  static setCouncil(enabled)  { return API._request('POST', '/api/council',   { enabled }); }
  static genparams()          { return API._request('GET',  '/api/genparams'); }
  static setGenparams(p)      { return API._request('POST', '/api/genparams', p); }
  static probeFolders()       { return API._request('GET',  '/api/folders/probe'); }
}

/* ────────────────────────────────────────────────────────────────────────
   2. State — единое хранилище + Observer. Подписчики дёргаются на update,
      Renderer решает что перерисовать по diff'у (не по факту вызова).
   ──────────────────────────────────────────────────────────────────────── */
class State {
  constructor() {
    this.data = null;          // последний ответ /api/state (S)
    this.run  = null;          // последний ответ /api/run
    this._subs = new Map();    // event -> Set<callback>
    this._loading = true;      // первая загрузка ещё не пришла
  }

  subscribe(event, cb) {
    if (!this._subs.has(event)) this._subs.set(event, new Set());
    this._subs.get(event).add(cb);
    return () => this._subs.get(event).delete(cb);  // unsubscribe
  }

  notify(event, payload) {
    const cbs = this._subs.get(event);
    if (cbs) for (const cb of cbs) {
      try { cb(payload); } catch (e) { console.error('[State.notify]', event, e); }
    }
  }

  get loading() { return this._loading; }

  updateState(data) {
    this._loading = false;
    const prev = this.data;
    this.data = data;
    this.notify('state', { prev, next: data });
    // run-status тоже мог измениться (фон. прогон начался/кончился) — кидаем
    // отдельное событие, чтобы PollingManager переключил интервалы
    const wasRunning = prev && prev.running;
    const nowRunning = data && data.running;
    if (!!wasRunning !== !!nowRunning) this.notify('running-toggle', !!nowRunning);
  }

  updateRun(run) {
    const prev = this.run;
    this.run = run;
    this.notify('run', { prev, next: run });
  }

  setError() {
    this._loading = false;
    this.notify('error');
  }
}

/* ────────────────────────────────────────────────────────────────────────
   3. Knight — режимы рыцаря. alive/working/error = CSS-классы на #knight-svg.
      Частота сердцебиения зависит от заполненности инбокса (из v1).
   ──────────────────────────────────────────────────────────────────────── */
class Knight {
  constructor(svg) {
    this.svg = svg;
    this.body = svg ? svg.querySelector('.breath') : null;
    this.heart = svg ? svg.querySelector('.heartbeat') : null;
    this._mode = 'alive';
  }

  setMode(mode) {
    if (!this.svg || this._mode === mode) return;
    this.svg.classList.remove('knight-alive', 'knight-working', 'knight-error');
    this.svg.classList.add('knight-' + mode);
    this._mode = mode;
    // breath: pause только в working, иначе running. Повторяем инлайн-стилем
    // помимо CSS-класса — bodies.js ничего не знает про наши классы, а нам важна
    // надёжная смена состояния (иначе после прогона рыцарь остался бы замороженным).
    if (this.body) {
      this.body.style.animationPlayState = mode === 'working' ? 'paused' : 'running';
    }
  }

  // частота пульса от наполненности инбокса: 10+ идей = быстро, 0 = медленно
  setHeartbeatByOpenCount(openCount) {
    if (!this.heart) return;
    const fill = Math.min(openCount / 10, 1);
    const dur = (1.6 - fill * 0.9).toFixed(2);
    this.heart.style.setProperty('--hb', dur + 's');
  }
}

/* ────────────────────────────────────────────────────────────────────────
   4. Renderer — подписан на State, перерисовывает только изменившиеся блоки.
      Использует document.createElement (никаких строк-HTML для пользовательских
      данных — защита от XSS через idea.title/why и т.п.).
   ──────────────────────────────────────────────────────────────────────── */
class Renderer {
  constructor(state, knight) {
    this.state = state;
    this.knight = knight;
    // Кеши часто обновляемых листов, чтобы сравнивать массивы, а не текст
    this._lastIdeasSig = null;  // null = «ещё не рендерили», '' = легитимная сигнатура пустого списка
    this._lastOrgansSig = null;  // null = «ещё не рендерили» (см. фикс ideas — пустая сигнатура '' легитимна)
    this._lastRunsSig = null;
    this._lastRunLines = -1;
    this._foldProbe = {};        // путь папки → {exists, files, capped} из /api/folders/probe
    this._probeInFlight = false; // анти-спам: один параллельный запрос probe за раз
    // Подписки
    state.subscribe('state', ({ prev, next }) => this._onState(prev, next));
    state.subscribe('run',   ({ prev, next }) => this._onRun(prev, next));
    state.subscribe('error', () => this._onError());
  }

  // ── helpers ──
  static $(sel, root) { return (root || document).querySelector(sel); }
  static $$(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

  static esc(s) {
    return String(s ?? '').replace(/[&<>"']/g,
      c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  static el(tag, opts = {}, children) {
    const e = document.createElement(tag);
    if (opts.cls) { if (Array.isArray(opts.cls)) opts.cls.forEach(c => c && e.classList.add(c)); else e.classList.add(opts.cls); }
    if (opts.text != null) e.textContent = opts.text;
    if (opts.attrs) for (const k in opts.attrs) e.setAttribute(k, opts.attrs[k]);
    if (opts.title) e.title = opts.title;
    if (opts.onclick) e.onclick = opts.onclick;
    if (opts.dataset) for (const k in opts.dataset) e.dataset[k] = opts.dataset[k];
    if (children) (Array.isArray(children) ? children : [children]).forEach(c => {
      if (c == null) return;
      e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return e;
  }

  static agoText(ts) {
    if (!ts) return '';
    const d = new Date(String(ts).replace(' ', 'T'));
    if (isNaN(+d)) return '';
    let s = Math.floor((Date.now() - d.getTime()) / 1000);
    if (s < 0) s = 0;
    if (s < 60)    return s + ' сек назад';
    if (s < 3600)  return Math.floor(s / 60) + ' мин назад';
    if (s < 86400) return Math.floor(s / 3600) + ' ч назад';
    return Math.floor(s / 86400) + ' дн назад';
  }

  static plural(n, one, few, many) {
    const m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
    return many;
  }

  static scoreClass(score) {
    if (score == null) return 'low';
    const n = parseFloat(score);
    if (isNaN(n)) return 'low';
    if (n >= 8) return 'high';
    if (n >= 7) return 'good';
    if (n >= 6) return 'medium';
    return 'low';
  }

  // ── приход нового state ──
  _onState(prev, next) {
    this._renderHeader(next);
    this._renderSources(next);
    this._renderDirection(next);
    this._renderAuto(next);
    this._renderGenparams(next);
    this._renderIdeas(next);
    this._renderOrgans(next);
    this._renderJournal(next);
    this._renderRegistryStrip(next);  // сводка тела/реестра в табах
    this._renderDock(next);
    this._renderEmptyState(next);
    // Knight режим по running
    this.knight.setMode(next.running ? 'working' : 'alive');
    const open = (next.inbox && next.inbox.ideas || []).filter(i => i.status === 'open').length;
    this.knight.setHeartbeatByOpenCount(open);
    // Фоновое обновление probe папок (для settings). Не блокирует рендер, один запрос за раз.
    if (!this._probeInFlight) {
      this._probeInFlight = true;
      API.probeFolders().then(r => {
        if (r && r.probe) this._foldProbe = r.probe;
      }).finally(() => { this._probeInFlight = false; });
    }
  }

  _onRun(prev, next) {
    this._renderRunStatus(next);
    this._renderConsole(next);
  }

  _onError() {
    this.knight.setMode('error');
    const pulse = Renderer.$('#h-pulse');
    if (pulse) pulse.className = 'h-pulse error';
    const pst = Renderer.$('.h-pstate');
    if (pst) { pst.textContent = 'молчит'; pst.className = 'h-pstate error'; }
    const now = Renderer.$('#m-fresh');
    if (now) { now.textContent = '—'; now.classList.add('text-bad'); }
  }

  // ── HEADER ──
  _renderHeader(S) {
    const ideas = (S.inbox && S.inbox.ideas) || [];
    const open = ideas.filter(i => i.status === 'open').length;
    const seen = (S.inbox && S.inbox.seen_count) || 0;

    Renderer.$('#m-inbox').textContent = open;
    Renderer.$('#m-memory').textContent = seen;

    // свежесть — последний прогон
    const fresh = Renderer.$('#m-fresh');
    fresh.classList.remove('text-bad');
    const runs = S.runs || [];
    const last = runs.length ? runs[runs.length - 1] : null;
    fresh.textContent = last ? Renderer.agoText(last.ts) : '—';
    fresh.title = last ? 'последний: ' + last.ts : 'прогонов не было';

    // ключи
    const keyEl = Renderer.$('#m-keys');
    const k = S.key || {};
    keyEl.textContent = k.present ? k.model : 'нет';
    keyEl.className = 'h-metric-value ' + (k.present ? 'good' : 'warn');

    // пульс
    const pulse = Renderer.$('#h-pulse');
    if (pulse) pulse.className = 'h-pulse' + (S.running ? ' working' : '');
    const pst = Renderer.$('.h-pstate');
    if (pst) { pst.textContent = S.running ? 'работает' : 'жив'; pst.className = 'h-pstate ' + (S.running ? 'working' : 'alive'); }

    // Кнопка Стоп: активна ТОЛЬКО когда идёт прогон. Раньше disabled был хардкод в
    // index.html и НИКОГДА не снимался → юзер не мог остановить прогон, а триаж был
    // заблокирован busy-защитой (serve.py /api/idea: «идёт прогон — разбор отложен»).
    // Симметрия с Принести (та disabled при running=true). После клика stopRun() шлёт
    // POST /api/stop → RUN.running=False → следующий /api/state снимет disabled обратно.
    const stopBtn = Renderer.$('.actions-list .btn-action.danger');
    if (stopBtn) stopBtn.disabled = !S.running;

    // авто в шапке
    const at = Renderer.$('#auto-toggle');
    const al = Renderer.$('#auto-label');
    if (at && al) {
      const auto = S.auto || { on: false, interval_min: 30 };
      at.classList.toggle('on', !!auto.on);
      al.textContent = auto.on ? 'авто ' + auto.interval_min + 'м' : 'авто выкл';
    }
  }

  // ── ИСТОЧНИКИ (чипы в левой панели) ──
  _renderSources(S) {
    const box = Renderer.$('.sources-chips');
    if (!box) return;
    const s = S.sources;
    if (!s || !s.sources) { box.textContent = 'нет данных'; return; }
    // Покажем только активные источники (включённые ленты + files если есть папки)
    const activeFeeds = (S.feeds && S.feeds.enabled) || [];
    const filesOn = ((S.folders && S.folders.paths) || []).length > 0;
    const active = new Set([...activeFeeds, ...(filesOn ? ['files'] : [])]);
    const NM = { hn: 'HN', reddit: 'Reddit', lobsters: 'Lobsters', gh_trending: 'GitHub', telegram: 'Telegram', files: 'Папки' };
    const keys = Object.keys(s.sources).filter(k => active.has(k));
    if (!keys.length) { box.textContent = 'источники выключены'; return; }
    box.textContent = '';
    keys.forEach(k => {
      const v = s.sources[k];
      const ok = v && v.ok;
      const chip = Renderer.el('span', { cls: ['src-chip', ok ? 'ok' : 'bad'], title: v && v.error ? v.error : '' });
      chip.appendChild(Renderer.el('span', { cls: ['src-dot', ok ? 'ok' : 'bad'] }));
      chip.appendChild(document.createTextNode(NM[k] || k));
      if (ok && v.items) chip.appendChild(Renderer.el('span', { cls: 'src-count', text: String(v.items) }));
      box.appendChild(chip);
    });
  }

  // ── НАПРАВЛЕНИЕ ──
  _renderDirection(S) {
    const d = S.direction || { current: '', presets: [] };
    const box = Renderer.$('#left .dir-current');
    const presetsEl = Renderer.$('#left .dir-presets');
    if (box) {
      box.textContent = '';
      const cur = (d.current || '').trim();
      if (cur) {
        box.appendChild(document.createTextNode('→ «' + cur + '»'));
        const clear = Renderer.el('span', { cls: 'dir-clear', text: '✕', title: 'Сбросить направление' });
        clear.onclick = () => UIController.instance && UIController.instance.clearDirection();
        box.appendChild(clear);
      } else {
        const span = Renderer.el('span', { cls: 'dir-none', text: 'без направления' });
        box.appendChild(span);
      }
    }
    if (presetsEl) {
      presetsEl.textContent = '';
      const cur = (d.current || '').toLowerCase();
      (d.presets || []).forEach((p, i) => {
        const on = p.toLowerCase() === cur;
        const pill = Renderer.el('span', { cls: ['dir-preset', on ? 'on' : ''], text: p });
        pill.onclick = () => UIController.instance && UIController.instance.applyPreset(i);
        const x = Renderer.el('span', { cls: 'dp-x', text: '×' });
        x.onclick = (e) => { e.stopPropagation(); UIController.instance && UIController.instance.removePreset(i); };
        pill.appendChild(x);
        presetsEl.appendChild(pill);
      });
    }
  }

  // ── АВТО (в настройках) ──
  _renderAuto(S) {
    const auto = S.auto || { on: false, interval_min: 30 };
    const tgl = Renderer.$('#settings-body .toggle[data-toggle="auto"]');
    if (tgl) tgl.classList.toggle('on', !!auto.on);
    const iv = Renderer.$('#settings-body input[type="number"]');
    if (iv && document.activeElement !== iv) iv.value = auto.interval_min;
  }

  // ── ПАРАМЕТРЫ ГЕНЕРАЦИИ (drawer «Настройки») ──
  _renderGenparams(S) {
    const gp = S.genparams || {};
    // Для каждого параметра: синхронизируем value из state в range-инпут + текстовое значение.
    // keep_min_score в env хранится 0..1, в UI показывается 0..10 (×10) — пересчёт здесь.
    // НЕ затираем инпут, который юзер прямо сейчас правит (activeElement), иначе ползунок
    // дёргается под пальцем на каждом poll /api/state (5 сек).
    for (const [key, spec] of Object.entries(gp)) {
      const input = Renderer.$('#p-' + key);
      if (!input) continue;
      const isScore = key.endsWith('_score');
      let min = spec.min;
      let max = spec.max;
      let v = spec.value;
      if (key === 'keep_min_score') {
        min *= 10;
        max *= 10;
        v *= 10;  // UI 0..10, env 0..1
      }
      input.min = String(min);
      input.max = String(max);
      v = isScore ? Number(v).toFixed(1) : Math.round(v);
      if (document.activeElement !== input) input.value = v;
      const valEl = Renderer.$('#pv-' + key);
      if (valEl) valEl.textContent = isScore ? Number(v).toFixed(1) : v;
    }
  }

  // ── ИДЕИ ──
  _renderIdeas(S) {
    const panel = Renderer.$('#tab-ideas');
    if (!panel) return;
    const ideas = (S.inbox && S.inbox.ideas) || [];
    const open = ideas.filter(i => i.status === 'open');
    // «Разобранные» теперь живут в отдельных мастер-файлах (taken.json/later.json), а не в
    // ideas[] — ideas[] содержит только open (мастер-разделение 2026-07-22). Собираем done из
    // inbox.taken + inbox.later; каждая идея уже несёт status и triaged_ts. Сортируем по убыванию
    // времени разбора (если есть triaged_ts), иначе по id — самые свежие действия сверху.
    const taken = ((S.inbox && S.inbox.taken) || []).map(i => ({ ...i, status: 'take' }));
    const later = ((S.inbox && S.inbox.later) || []).map(i => ({ ...i, status: 'later' }));
    const done = [...taken, ...later].sort((a, b) => {
      const ta = a.triaged_ts || '', tb = b.triaged_ts || '';
      if (ta !== tb) return ta < tb ? 1 : -1;  // свежие сначала
      return (b.id || 0) - (a.id || 0);  // при равенстве — по id
    });

    // счётчики
    Renderer.$('#ideas-counter').textContent = open.length + ' открыто';
    // #ideas-panel-count — бейдж в заголовке вкладки «Открытые идеи» (раньше был хардкод
    // «12» в HTML без JS-обновления → зависал, вводя в заблуждение).
    const panelCount = Renderer.$('#ideas-panel-count');
    if (panelCount) panelCount.textContent = open.length;

    // список открытых идей — вставляем между finish-slot (или .panel-title)
    // и .done-toggle. Контейнер: #ideas-open.
    let list = Renderer.$('#ideas-open');
    if (!list) {
      list = Renderer.el('div', { attrs: { id: 'ideas-open' } });
      // Надёжный fallback: вставляем после finish-slot, иначе после .panel-title,
      // иначе в конец #tab-ideas. Раньше был только первый вариант — и если
      // #finish-slot отсутствовал (как в раннем каркасе v2), список просто
      // не появлялся в DOM → реальные идеи некуда было рендерить.
      const finishSlot = Renderer.$('#finish-slot');
      const title = Renderer.$('#tab-ideas > .panel-title');
      if (finishSlot) finishSlot.insertAdjacentElement('afterend', list);
      else if (title) title.insertAdjacentElement('afterend', list);
      else panel.appendChild(list);
    }

    const sig = open.map(i => i.id + ':' + i.status + ':' + i.score).join('|');
    if (sig !== this._lastIdeasSig) {
      this._lastIdeasSig = sig;
      list.textContent = '';
      if (open.length === 0 && !(S.inbox && S.inbox.finish)) {
        // пустой список — empty state покажет отдельный метод
      } else {
        open.forEach(idea => list.appendChild(this._ideaCard(idea)));
      }
    }

    // finish slot — если контейнера нет, создаём его перед #ideas-open (или после
    // .panel-title). Раньше код только читал #finish-slot и тихо пропускал, если
    // его не было → слот «доделать» просто не показывался.
    let finishSlot = Renderer.$('#finish-slot');
    if (!finishSlot) {
      finishSlot = Renderer.el('div', { attrs: { id: 'finish-slot' } });
      const title = Renderer.$('#tab-ideas > .panel-title');
      if (title) title.insertAdjacentElement('afterend', finishSlot);
      else panel.insertBefore(finishSlot, panel.firstChild);
    }
    finishSlot.textContent = '';
    const f = S.inbox && S.inbox.finish;
    if (f) finishSlot.appendChild(this._finishCard(f));

    // разобранные
    this._renderDone(done);
  }

  _ideaCard(idea) {
    const sc = idea.score != null ? parseFloat(idea.score).toFixed(1) : null;
    const card = Renderer.el('div', { cls: 'idea-card', attrs: { id: 'idea-' + idea.id } });

    // head: title + score badge
    const head = Renderer.el('div', { cls: 'idea-head' });
    head.appendChild(Renderer.el('div', { cls: 'idea-title', text: idea.title || '(без заголовка)' }));
    if (sc != null) {
      head.appendChild(Renderer.el('div', {
        cls: ['score-badge', Renderer.scoreClass(parseFloat(idea.score))],
        text: sc, title: 'оценка совета'
      }));
    }
    card.appendChild(head);

    // why
    if (idea.why) card.appendChild(Renderer.el('div', { cls: 'idea-why', text: idea.why }));

    // meta
    const meta = Renderer.el('div', { cls: 'idea-meta' });
    if (idea.source) {
      meta.appendChild(Renderer.el('span', { cls: ['idea-tag', 'src'], text: idea.source }));
    }
    const isLLM = idea.brain === 'llm';
    meta.appendChild(Renderer.el('span', { cls: ['idea-tag', isLLM ? 'llm' : ''], text: isLLM ? 'нейронка' : 'болванка' }));
    if (idea.effort) meta.appendChild(Renderer.el('span', { cls: 'idea-tag', text: 'сложность: ' + idea.effort }));
    meta.appendChild(Renderer.el('span', { cls: 'idea-tag', text: '#' + idea.id }));
    card.appendChild(meta);

    // actions
    const acts = Renderer.el('div', { cls: 'idea-actions' });
    acts.appendChild(Renderer.el('button', {
      cls: ['act-btn', 'take'], text: '✓ Взять',
      onclick: () => UIController.instance && UIController.instance.ideaAct(idea.id, 'take')
    }));
    acts.appendChild(Renderer.el('button', {
      cls: ['act-btn', 'later'], text: '⏳ Позже',
      onclick: () => UIController.instance && UIController.instance.ideaAct(idea.id, 'later')
    }));
    acts.appendChild(Renderer.el('button', {
      cls: ['act-btn', 'trash'], text: '✕ Мусор',
      onclick: () => UIController.instance && UIController.instance.ideaAct(idea.id, 'trash')
    }));
    card.appendChild(acts);
    return card;
  }

  _finishCard(f) {
    const card = Renderer.el('div', { cls: 'idea-card' });
    card.style.borderColor = 'rgba(6,182,212,0.25)';
    const head = Renderer.el('div', { cls: 'idea-head' });
    head.appendChild(Renderer.el('div', { cls: 'idea-title', text: '🔧 Слот «доделать»: ' + (f.title || '') }));
    card.appendChild(head);
    const why = Renderer.el('div', { cls: 'idea-why' });
    why.textContent = f.why || '';
    if (f.folder) why.textContent += ' · ' + f.folder;
    card.appendChild(why);
    return card;
  }

  _renderDone(done) {
    const toggle = Renderer.$('#done-toggle');
    const list = Renderer.$('#done-list');
    const countEl = Renderer.$('#done-count');
    if (countEl) countEl.textContent = String(done.length);
    if (toggle) toggle.style.display = done.length ? '' : 'none';
    if (!list) return;
    list.textContent = '';
    done.forEach(i => {
      const row = Renderer.el('div', { cls: 'done-row' });
      const st = Renderer.el('span', { cls: ['done-status', i.status], text: { take: 'взял', later: 'позже', trash: 'мусор' }[i.status] || i.status });
      row.appendChild(st);
      row.appendChild(Renderer.el('span', { cls: 'done-title', text: i.title || '' }));
      row.appendChild(Renderer.el('span', { cls: 'done-id', text: '#' + i.id }));
      list.appendChild(row);
    });
  }

  _renderEmptyState(S) {
    const tab = Renderer.$('#tab-ideas');
    if (!tab) return;
    const ideas = (S.inbox && S.inbox.ideas) || [];
    const open = ideas.filter(i => i.status === 'open');
    const hasFinish = !!(S.inbox && S.inbox.finish);
    let es = Renderer.$('#empty-state');
    if (open.length > 0 || hasFinish) {
      if (es) es.remove();
      return;
    }
    if (es) return;  // уже есть
    es = Renderer.el('div', { cls: 'empty-state', attrs: { id: 'empty-state' } });
    es.appendChild(Renderer.el('div', { cls: 'empty-icon', text: '🌱' }));

    const runs = S.runs || [];
    const last = runs.length ? runs[runs.length - 1] : null;
    const brought = runs.filter(r => /^\d+$/.test(r.value)).reduce((s, r) => s + parseInt(r.value), 0);
    const busy = !!S.running;
    const auto = S.auto || {};

    es.appendChild(Renderer.el('div', { cls: 'empty-title', text: busy ? '⚙ Киборг работает…' : '✅ Киборг жив, отдыхает' }));
    const sub = Renderer.el('div', { cls: 'empty-sub' });
    if (last) {
      const isNum = /^\d+$/.test(last.value);
      const got = isNum ? `принёс ${parseInt(last.value)} ${Renderer.plural(parseInt(last.value), 'идею', 'идеи', 'идей')}` : last.value;
      sub.textContent = `Прошлый сбор: ${got}. ${Renderer.agoText(last.ts) || 'только что'}.`;
    } else {
      sub.textContent = 'Сборов ещё не было.';
    }
    const hint = Renderer.el('div', { cls: 'empty-sub' });
    hint.textContent = busy ? 'идёт сбор — скоро появятся, можно уйти'
      : auto.on ? `Сам сходит за новыми каждые ~${auto.interval_min} мин`
      : 'Авто выкл — можно нажать кнопку.';
    hint.style.color = 'var(--text-tertiary)';
    es.appendChild(sub);
    es.appendChild(hint);

    // статистика
    const stats = Renderer.el('div', { cls: 'empty-stats' });
    const reg = S.registry || {};
    const extracted = (reg.by_status && reg.by_status.extracted) || 0;
    const nOrg = (S.organs || []).length;
    const rej = S.rejected || 0;
    stats.appendChild(this._statCell(String(runs.length), 'прогонов'));
    stats.appendChild(this._statCell(String(brought), 'принёс'));
    stats.appendChild(this._statCell(String(extracted), 'органов добыто'));
    stats.appendChild(this._statCell(String(nOrg), 'впаяно'));
    if (rej) stats.appendChild(this._statCell(String(rej), 'отклонил'));
    es.appendChild(stats);

    // кнопка «принести»
    const btn = Renderer.el('button', {
      cls: ['btn-action', 'primary'], text: busy ? '⚙ идёт сбор…' : '⚡ Принести сейчас',
      attrs: busy ? { disabled: 'disabled' } : {}
    });
    if (!busy) btn.onclick = () => UIController.instance && UIController.instance.runGoal('приноси свежие идеи');
    es.appendChild(btn);

    // вставляем в начало tab-ideas (до finish-slot если есть)
    const title = Renderer.$('#tab-ideas > .panel-title');
    if (title) title.insertAdjacentElement('afterend', es);
  }

  _statCell(val, lbl) {
    const c = Renderer.el('div', { cls: 'empty-stat' });
    c.appendChild(Renderer.el('div', { cls: 'empty-stat-val', text: val }));
    c.appendChild(Renderer.el('div', { cls: 'empty-stat-lbl', text: lbl }));
    return c;
  }

  // Статический построитель строки папки для settings. Раньше был инстанс-методом
  // _folderItem, но зовётся из UIController._renderFoldersList → this указывал на
  // UIController и падал с "_folderItem is not a function" (баг 2026-07-22 при
  // открытии настроек). Перенёс в static, probe передаётся параметром явно.
  static _folderItem(f, probeMap) {
    const item = Renderer.el('div', { cls: 'list-item' });
    item.appendChild(Renderer.el('div', {
      cls: ['toggle', f.on ? 'on' : ''],
      attrs: { 'data-toggle': 'folder' }
    }));
    const path = Renderer.el('span', {
      cls: ['list-item-path', f.on ? '' : 'off'],
      text: f.path,
      title: f.path
    });
    item.appendChild(path);
    const probe = (probeMap || {})[f.path];
    if (probe) {
      let note = '', cls = '';
      if (!probe.exists) { note = 'не найден'; cls = 'bad'; }
      else if (probe.files === 0) { note = '0 файлов'; cls = 'warn'; }
      else { note = probe.files + (probe.capped ? '+' : '') + ' файл.'; cls = 'ok'; }
      item.appendChild(Renderer.el('span', { cls: ['list-item-note', cls], text: note }));
    }
    item.appendChild(Renderer.el('button', { cls: 'list-item-rm', text: '×' }));
    return item;
  }

  // ── ОРГАНЫ ──
  _renderOrgans(S) {
    const panel = Renderer.$('#tab-organs');
    if (!panel) return;
    const organs = S.organs || [];
    let list = Renderer.$('#organs-list');
    if (!list) {
      // Первая отрисовка — вычистим статичные примеры и создадим контейнер
      panel.querySelectorAll('.organ-row').forEach(r => r.remove());
      list = Renderer.el('div', { attrs: { id: 'organs-list' } });
      const title = Renderer.$('#tab-organs > .panel-title');
      if (title) title.insertAdjacentElement('afterend', list);
      else panel.appendChild(list);
    }
    const sig = organs.map(o => o.name + ':' + o.role).join('|');
    if (sig === this._lastOrgansSig) return;
    this._lastOrgansSig = sig;
    list.textContent = '';
    if (!organs.length) {
      list.appendChild(Renderer.el('div', { text: 'органы не подключены', attrs: { style: 'font-size:12px;color:var(--text-tertiary)' } }));
      return;
    }
    organs.forEach(o => list.appendChild(this._organRow(o)));
  }

  _organRow(o) {
    const row = Renderer.el('div', { cls: ['organ-row', 'role-' + (o.role || 'transform')] });
    const main = Renderer.el('div', { cls: 'organ-main' });
    const l1 = Renderer.el('div', { cls: 'organ-line1' });
    l1.appendChild(Renderer.el('span', { cls: 'organ-name', text: o.name || '?' }));
    const cons = (o.consumes || []).join(', ');
    const prod = (o.produces || []).join(', ');
    if (cons || prod) l1.appendChild(Renderer.el('span', { cls: 'organ-flow', text: cons + ' → ' + prod }));
    const needs = o.needs || {};
    const badges = (needs.key ? '🔑' : '') + (needs.network ? '🌐' : '');
    if (badges) l1.appendChild(Renderer.el('span', { cls: 'organ-badges', text: badges }));
    main.appendChild(l1);
    if (o.purpose) main.appendChild(Renderer.el('div', { cls: 'organ-purpose', text: o.purpose }));
    row.appendChild(main);
    return row;
  }

  // ── ЖУРНАЛ (таблица) ──
  _renderJournal(S) {
    const panel = Renderer.$('#tab-journal');
    if (!panel) return;
    const runs = S.runs || [];
    let tbody = Renderer.$('#tab-journal tbody');
    if (!tbody) return;  // таблица в каркасе уже есть с примерами — чистим при первой реальной отрисовке
    const sig = runs.map(r => r.ts + ':' + r.value).join('|');
    if (sig === this._lastRunsSig) return;
    this._lastRunsSig = sig;
    tbody.textContent = '';
    if (!runs.length) {
      const tr = Renderer.el('tr');
      const td = Renderer.el('td', { text: 'прогонов ещё не было', attrs: { colspan: '5', style: 'color:var(--text-tertiary);text-align:center;padding:24px' } });
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }
    runs.slice().reverse().slice(0, 20).forEach(r => tbody.appendChild(this._journalRow(r)));
  }

  _journalRow(r) {
    const tr = Renderer.el('tr');
    tr.appendChild(Renderer.el('td', { cls: 'run-ts', text: r.ts || '' }));
    tr.appendChild(Renderer.el('td', { cls: 'run-goal', text: '«' + (r.goal || '') + '»' }));
    // результат
    let cls = 'zero', val = '0';
    if (/^\d+$/.test(r.value || '')) {
      const n = parseInt(r.value);
      cls = n > 0 ? 'ok' : 'zero';
      val = n > 0 ? '+' + n : '0';
    } else { val = r.value || ''; cls = ''; }
    tr.appendChild(Renderer.el('td', {}, [Renderer.el('span', { cls: ['run-result', cls], text: val })]));
    tr.appendChild(Renderer.el('td', { cls: 'run-duration', text: '—' }));
    const ok = !r.degraded || /дубликат|stub/.test(r.degraded);
    const pill = Renderer.el('span', { cls: ['run-status-pill', ok ? 'ok' : 'fail'], text: ok ? 'ok' : '⚠' });
    tr.appendChild(Renderer.el('td', {}, [pill]));
    return tr;
  }

  // ── СВОДКА В ТАБ-БАРЕ ──
  _renderRegistryStrip(S) {
    // не реализован отдельный catalog-strip в каркасе v2 — счётчик идей уже есть,
    // здесь можно было бы добавить «N органов / M в реестре». Пока ничего.
  }

  // ── DOCK (последние 3 прогона) ──
  _renderDock(S) {
    const body = Renderer.$('#journal-dock .dock-body');
    if (!body) return;
    const runs = S.runs || [];
    const last3 = runs.slice().reverse().slice(0, 3);
    body.textContent = '';
    last3.forEach(r => {
      const row = Renderer.el('div', { cls: 'dock-row' });
      row.appendChild(Renderer.el('span', { cls: 'dock-ts', text: String(r.ts || '').slice(11, 16) }));
      row.appendChild(Renderer.el('span', { cls: 'dock-goal', text: '«' + (r.goal || '') + '»' }));
      const isNum = /^\d+$/.test(r.value || '');
      const qty = Renderer.el('span', { cls: ['dock-qty', isNum && parseInt(r.value) === 0 ? 'zero' : ''], text: isNum ? '+' + r.value : (r.value || '') });
      row.appendChild(qty);
      body.appendChild(row);
    });
  }

  // ── RUN STATUS (секция в левой панели при прогоне) ──
  _renderRunStatus(run) {
    const section = Renderer.$('#run-section');
    if (!section) return;
    const active = !!(run && run.running);
    section.classList.toggle('visible', active);
    if (!active) { this._runStartTime = null; return; }
    // Фиксируем старт при первом заходе в режим. started_at с сервера надёжнее
    // клиентских часов (прогон мог начаться кроном), но serve.py его не отдаёт —
    // поэтому используем время первого наблюдения running=true.
    if (!this._runStartTime) this._runStartTime = Date.now();
    const header = Renderer.$('#run-section .run-header');
    if (header) {
      header.textContent = '';
      header.appendChild(Renderer.el('span', { cls: 'run-spinner' }));
      // определяем текущий орган по строкам
      const lines = (run.lines || []).join('\n');
      const m = [...lines.matchAll(/иду: ([a-z_]+)/g)];
      const cur = m.length ? m[m.length - 1][1] : null;
      const NM = { collect_source: 'Сбор', ideate: 'Генерация', rank_ideas: 'Арбитр', readability_gate: 'Читаемость', scrub_secrets: 'Очистка', deliver: 'Доставка' };
      header.appendChild(document.createTextNode('работает · ' + (NM[cur] || cur || '…')));
      // таймер по started
      const timer = Renderer.el('span', { cls: 'run-timer' });
      header.appendChild(timer);
      this._runStartTime = this._runStartTime || Date.now();
      this._tickRunTimer(timer);
    }
    // pipe-chips
    const prog = Renderer.$('#run-section .run-progress');
    if (prog) {
      prog.textContent = '';
      const lines = (run.lines || []).join('\n');
      const order = ['collect_source', 'ideate', 'rank_ideas', 'readability_gate', 'scrub_secrets', 'deliver'];
      const NM = { collect_source: 'Сбор', ideate: 'Генерация', rank_ideas: 'Арбитр', readability_gate: 'Читаемость', scrub_secrets: 'Очистка', deliver: 'Доставка' };
      const cur = (lines.match(/иду: ([a-z_]+)/g) || []).slice(-1)[0];
      const curName = cur && cur.match(/иду: ([a-z_]+)/)[1];
      const done = new Set([...lines.matchAll(/✓ готов: ([a-z_]+)/g)].map(m => m[1]));
      const mentioned = [...lines.matchAll(/(иду|готов|шаг \d+):\s*([a-z_]+)/g)].map(m => m[2]);
      const seen = [];
      for (const s of mentioned) { if (!seen.includes(s)) seen.push(s); }
      const toShow = seen.length ? seen : order.slice(0, 3);
      toShow.forEach(s => {
        const cls = s === curName ? 'active' : (done.has(s) ? 'done' : '');
        prog.appendChild(Renderer.el('span', { cls: ['pipe-chip', cls], text: NM[s] || s }));
      });
    }
  }

  _tickRunTimer(el) {
    if (!el) return;
    const sec = Math.max(0, Math.floor((Date.now() - (this._runStartTime || Date.now())) / 1000));
    el.textContent = Math.floor(sec / 60) + ':' + String(sec % 60).padStart(2, '0');
  }

  // ── КОНСОЛЬ ──
  _renderConsole(run) {
    const body = Renderer.$('#console-body');
    if (!body) return;
    const lines = (run && run.lines) || [];
    if (lines.length === this._lastRunLines) return;
    this._lastRunLines = lines.length;
    body.textContent = lines.join('\n');
    // автоскролл вниз, если не на паузе и не придержан вручную
    if (!UIController.instance || !UIController.instance.consolePaused) {
      if (!UIController.instance || !UIController.instance.consoleStickDown) {
        body.scrollTop = body.scrollHeight;
      }
    }
  }
}

/* ────────────────────────────────────────────────────────────────────────
   5. Toasts — простая очередь: success/warn/error/info, авто-скрытие 3 сек.
   ──────────────────────────────────────────────────────────────────────── */
class Toasts {
  constructor(container) {
    this.container = container;
    this._icons = { success: '✓', warn: '⚠', error: '✕', info: 'ℹ' };
  }

  show(msg, type = 'info', ms = 3000) {
    if (!this.container) return;
    const t = Renderer.el('div', { cls: ['toast', type] });
    t.appendChild(Renderer.el('span', { cls: 'toast-icon', text: this._icons[type] || 'ℹ' }));
    t.appendChild(Renderer.el('span', { cls: 'toast-msg', text: msg }));
    t.onclick = () => this._hide(t);
    this.container.appendChild(t);
    setTimeout(() => this._hide(t), ms);
  }

  _hide(t) {
    if (!t || !t.parentNode) return;
    t.style.transition = 'opacity .25s, transform .25s';
    t.style.opacity = '0';
    t.style.transform = 'translateX(40px)';
    setTimeout(() => t.remove(), 250);
  }
}

/* ────────────────────────────────────────────────────────────────────────
   6. UIController — обработчики DOM-событий. Держит на себе действия
      пользователя, дёргает API, обновляет State (или сразу Toasts).
   ──────────────────────────────────────────────────────────────────────── */
class UIController {
  constructor(state, api, knight, toasts) {
    this.state = state;
    this.api = api;
    this.knight = knight;
    this.toasts = toasts;
    UIController.instance = this;

    // Состояние UI
    this.consolePaused = false;
    this.consoleStickDown = true;  // прижат вниз, пока юзер не прокрутил вверх
    this._runStartTime = null;
    this._foldProbe = {};          // кэш probe папок для settings (обновляется через API.probeFolders)
    this._probeInFlight = false;   // анти-спам probe (один параллельный запрос за раз)

    this._bindHeader();
    this._bindTabs();
    this._bindConsole();
    this._bindSettings();
    this._bindGenparams();
    this._bindDock();
    this._bindIdeasPanelActions();
    this._bindLeftActions();
  }

  // ── HEADER ──
  _bindHeader() {
    const bring = Renderer.$('#btn-bring');
    if (bring) bring.onclick = () => this.runGoal('приноси свежие идеи');

    const at = Renderer.$('#auto-toggle');
    if (at) at.onclick = () => {
      const cur = this.state.data && this.state.data.auto || { on: false, interval_min: 30 };
      const next = !cur.on;
      const ivEl = Renderer.$('#settings-body input[type="number"]');
      const iv = ivEl ? (parseInt(ivEl.value, 10) || cur.interval_min) : cur.interval_min;
      this.api.setAuto(next, iv).then(r => {
        if (r.ok) this.toasts.show(next ? 'Автоцикл включён' : 'Автоцикл выключен', 'success');
        else this.toasts.show('Авто: ' + (r.msg || 'не вышло'), 'error');
        this._refreshSoon();
      });
    };

    const set = Renderer.$('#btn-settings');
    if (set) set.onclick = () => this.openSettings();
  }

  // ── TABS ──
  _bindTabs() {
    const buttons = Renderer.$$('.seg-btn');
    const indicator = Renderer.$('#seg-indicator');
    const move = (btn) => {
      if (!btn || !indicator) return;
      const r = btn.getBoundingClientRect();
      const pr = btn.parentElement.getBoundingClientRect();
      indicator.style.width = r.width + 'px';
      indicator.style.transform = 'translateX(' + (r.left - pr.left) + 'px)';
    };
    buttons.forEach(b => {
      b.onclick = () => {
        buttons.forEach(x => x.classList.toggle('active', x === b));
        const name = b.dataset.tab;
        Renderer.$$('.tab-panel').forEach(p => p.classList.toggle('active', p.id === 'tab-' + name));
        move(b);
      };
    });
    window.addEventListener('resize', () => {
      const a = Renderer.$('.seg-btn.active');
      if (a) move(a);
    });
    // первичное позиционирование
    window.addEventListener('load', () => {
      const a = Renderer.$('.seg-btn.active');
      if (a) move(a);
    });
  }

  // ── КОНСОЛЬ ──
  _bindConsole() {
    const header = Renderer.$('#console-area .console-header');
    const body = Renderer.$('#console-body');
    const area = Renderer.$('#console-area');
    if (header) header.onclick = (e) => {
      if (e.target.closest('.console-btn')) return;
      if (area.classList.contains('expanded')) {
        area.classList.remove('expanded');
        area.classList.toggle('collapsed');
      } else {
        area.classList.remove('collapsed');
        area.classList.toggle('expanded');
      }
    };
    // кнопки управления
    const btns = Renderer.$$('.console-btn');
    if (btns[0]) btns[0].onclick = (e) => {
      e.stopPropagation();
      this.consolePaused = !this.consolePaused;
      btns[0].textContent = this.consolePaused ? '▶' : '⏸';
      btns[0].classList.toggle('on', this.consolePaused);
    };
    if (btns[1]) btns[1].onclick = (e) => {
      e.stopPropagation();
      if (body) body.textContent = '';
    };
    if (btns[2]) btns[2].onclick = (e) => {
      e.stopPropagation();
      area.classList.toggle('expanded');
      area.classList.remove('collapsed');
    };
    // scroll-snap: если юзер прокрутил вверх — отлипаем от низа
    if (body) {
      body.addEventListener('scroll', () => {
        const atBottom = body.scrollTop + body.clientHeight >= body.scrollHeight - 4;
        this.consoleStickDown = atBottom;
      });
    }
  }

  // ── НАСТРОЙКИ DRAWER ──
  _bindSettings() {
    const overlay = Renderer.$('#settings-overlay');
    const drawer = Renderer.$('#settings-drawer');
    const close = Renderer.$('#settings-close');
    if (close) close.onclick = () => this.closeSettings();
    if (overlay) overlay.onclick = () => this.closeSettings();
    // toggle'ы и check'и — делегирование
    const body = Renderer.$('#settings-body');
    if (body) {
      body.addEventListener('click', (e) => {
        const tgl = e.target.closest('.toggle');
        const chk = e.target.closest('.check');
        const rm = e.target.closest('.list-item-rm');
        const addBtn = e.target.closest('.list-add-row .btn-mini');
        if (tgl) this._onSettingsToggle(tgl);
        else if (chk) this._onCouncilToggle(chk);
        else if (rm) this._onRemoveListItem(rm);
        else if (addBtn) this._onAddFolder(addBtn);
      });
      // интервал авто — save on blur
      const iv = Renderer.$('#settings-body input[type="number"]');
      if (iv) iv.onblur = () => this._saveAutoInterval();
    }
    // кнопка «Сохранить» в футере
    const save = Renderer.$('.settings-footer .btn-primary');
    if (save) save.onclick = () => {
      this._saveAllSettings();
      this.closeSettings();
      this.toasts.show('Настройки сохранены', 'success');
    };
    const cancel = Renderer.$('.settings-footer .btn-mini');
    if (cancel) cancel.onclick = () => this.closeSettings();
  }

  _onSettingsToggle(tgl) {
    const what = tgl.dataset.toggle;
    if (what === 'auto') {
      const cur = this.state.data && this.state.data.auto || { on: false, interval_min: 30 };
      const ivEl = Renderer.$('#settings-body input[type="number"]');
      const iv = ivEl ? (parseInt(ivEl.value, 10) || cur.interval_min) : cur.interval_min;
      // оптимистичный UI — мгновенный отклик
      tgl.classList.toggle('on');
      this.api.setAuto(!cur.on, iv).then(r => {
        if (!r.ok) {
          tgl.classList.toggle('on');  // откат
          this.toasts.show('Авто: ' + (r.msg || 'не вышло'), 'error');
        }
        this._refreshSoon();
      });
    } else if (what === 'theme') {
      const cur = document.documentElement.getAttribute('data-theme') || 'risograph';
      const next = cur === 'risograph' ? 'industrial' : 'risograph';
      // Сохраняем и применяем
      localStorage.setItem('kiborg-theme', next);
      document.documentElement.setAttribute('data-theme', next);
      tgl.classList.toggle('on', next === 'risograph');
      const nm = Renderer.$('#theme-name');
      if (nm) nm.textContent = next === 'risograph' ? 'Risograph' : 'Industrial';
      this.toasts.show('Тема: ' + (next === 'risograph' ? 'Risograph' : 'Industrial'), 'success');
    } else if (what === 'feed') {
      // какой фид переключили — найдём по пути в DOM
      const row = tgl.closest('.list-item');
      const section = row && row.closest('.settings-section');
      if (!section) return;
      // получаем все list-item в этой секции (кроме add-row)
      const items = section.querySelectorAll('.list-item');
      const idx = Array.from(items).indexOf(row);
      const all = (this.state.data && this.state.data.feeds && this.state.data.feeds.all) || [];
      const enabled = new Set((this.state.data && this.state.data.feeds && this.state.data.feeds.enabled) || []);
      const name = all[idx];
      if (!name) return;
      // оптимистичный UI: сразу переключаем класс, не ждём round-trip
      tgl.classList.toggle('on');
      if (enabled.has(name)) enabled.delete(name); else enabled.add(name);
      this.api.setFeeds(Array.from(enabled)).then(r => {
        if (!r.ok) {
          tgl.classList.toggle('on');  // откат
          this.toasts.show('Лента: ' + (r.msg || 'не вышло'), 'error');
        }
        this._refreshSoon();
      });
    } else if (what === 'folder') {
      // тумблер вкл/выкл конкретной папки
      const row = tgl.closest('.list-item');
      const section = row && row.closest('.settings-section');
      if (!section) return;
      const items = section.querySelectorAll('.list-item');
      const idx = Array.from(items).indexOf(row);
      const folders = ((this.state.data && this.state.data.folders && this.state.data.folders.folders) || [])
        .map(f => ({ path: f.path, on: f.on }));
      if (folders[idx]) {
        // оптимистичный UI + сразу меняем path-класс (off)
        tgl.classList.toggle('on');
        const pathEl = row && row.querySelector('.list-item-path');
        if (pathEl) pathEl.classList.toggle('off');
        folders[idx].on = !folders[idx].on;
        this.api.setFolders(folders).then(r => {
          if (!r.ok) {
            tgl.classList.toggle('on');
            if (pathEl) pathEl.classList.toggle('off');
            this.toasts.show('Папка: ' + (r.msg || 'не вышло'), 'error');
          }
          this._refreshSoon();
        });
      }
    }
  }

  _onCouncilToggle(chk) {
    // Чекбокс участника совета. Раньше был чисто декоративным (только toggle CSS-класса,
    // без отправки на сервер — баг 2026-07-22: юзер ждал, что «Оркестр» включится, а
    // ничего не уходило). Теперь отправляем обновлённый список включённых на /api/council.
    const row = chk.closest('.list-item');
    if (!row) return;
    const section = row.closest('.settings-section');
    if (!section) return;
    // считаем индекс только среди .list-item в секции, иначе title/add-row
    // дадут смещение (баг 2026-07-22: Оркестр был idx=3 из-за settings-section-title,
    // а all[3] = undefined → тихий return → ничего не отправлялось)
    const items = section.querySelectorAll('.list-item');
    const idx = Array.from(items).indexOf(row);
    const all = (this.state.data && this.state.data.council && this.state.data.council.all) || [];
    const enabled = new Set((this.state.data && this.state.data.council && this.state.data.council.enabled) || []);
    const name = all[idx];
    if (!name) return;
    // оптимистично переключаем класс, чтобы UI отзывался сразу
    chk.classList.toggle('on');
    if (enabled.has(name)) enabled.delete(name); else enabled.add(name);
    this.api.setCouncil(Array.from(enabled)).then(r => {
      if (!r.ok) {
        // откатываем визуальное переключение при ошибке
        chk.classList.toggle('on');
        this.toasts.show('Совет: ' + (r.msg || 'не вышло'), 'error');
      }
      this._refreshSoon();
    });
  }

  _onRemoveListItem(rm) {
    const row = rm.closest('.list-item');
    // определяем секцию по ближайшему .settings-section
    let section = rm.closest('.settings-section');
    if (!section || !row) return;
    // считаем индекс только среди .list-item в секции (title/add-row иначе смещают)
    const items = section.querySelectorAll('.list-item');
    const idx = Array.from(items).indexOf(row);
    const title = section.querySelector('.settings-section-title');
    if (!title) return;
    const secName = title.textContent.trim();
    if (secName.startsWith('Папки')) {
      const folders = ((this.state.data && this.state.data.folders && this.state.data.folders.folders) || [])
        .map(f => ({ path: f.path, on: f.on }));
      folders.splice(idx, 1);
      this.api.setFolders(folders).then(() => this._refreshSoon());
    }
  }

  _onAddFolder(btn) {
    const addRow = btn.closest('.list-add-row');
    if (!addRow) return;
    const input = addRow.querySelector('input');
    if (!input) return;
    const path = input.value.trim();
    if (!path) {
      this.toasts.show('Введите путь к папке', 'warn');
      return;
    }
    input.value = '';
    const folders = ((this.state.data && this.state.data.folders && this.state.data.folders.folders) || [])
      .map(f => ({ path: f.path, on: f.on }));
    // проверяем на дубликаты
    if (folders.some(f => f.path === path)) {
      this.toasts.show('Эта папка уже добавлена', 'warn');
      return;
    }
    folders.push({ path, on: true });
    this.api.setFolders(folders).then(r => {
      if (!r.ok) {
        this.toasts.show('Добавление папки: ' + (r.msg || 'не вышло'), 'error');
      } else {
        this.toasts.show('Папка добавлена: ' + path, 'success');
        this._refreshSoon();
      }
    });
  }

  _saveAutoInterval() {
    const ivEl = Renderer.$('#settings-body input[type="number"]');
    if (!ivEl) return;
    const iv = Math.max(5, Math.min(240, parseInt(ivEl.value, 10) || 30));
    const cur = this.state.data && this.state.data.auto || { on: false };
    this.api.setAuto(cur.on, iv).then(() => this._refreshSoon());
  }

  _saveAllSettings() {
    // авто
    this._saveAutoInterval();
    // соберём council из UI
    const checks = Renderer.$$('#settings-body .check[data-check="council"]');
    const all = (this.state.data && this.state.data.council && this.state.data.council.all) || [];
    const enabled = [];
    checks.forEach((c) => {
      if (c.classList.contains('on')) {
        const row = c.closest('.list-item');
        const pathEl = row && row.querySelector('.list-item-path');
        const text = pathEl ? pathEl.textContent.trim() : '';
        // маппим текст обратно в имя council
        const name = Object.keys({
          rank_ideas: 'Арбитр (rank_ideas)',
          ask_llm: 'Интуиция (ask_llm)',
          orchestra: 'Оркестр (orchestra)'
        }).find(k => text.includes(k)) || text;
        if (all.includes(name)) enabled.push(name);
      }
    });
    this.api.setCouncil(enabled);
    // соберём feeds из UI
    const feedsSection = this._findSection(Renderer.$('#settings-body'), 'Ленты');
    if (feedsSection) {
      const allFeeds = (this.state.data && this.state.data.feeds && this.state.data.feeds.all) || [];
      const enabledFeeds = new Set();
      const feedItems = feedsSection.querySelectorAll('.list-item');
      feedItems.forEach((row, idx) => {
        const tgl = row.querySelector('.toggle');
        const pathEl = row.querySelector('.list-item-path');
        if (tgl && tgl.classList.contains('on') && pathEl && allFeeds[idx]) {
          enabledFeeds.add(allFeeds[idx]);
        }
      });
      this.api.setFeeds(Array.from(enabledFeeds));
    }
  }

  // ── ПАРАМЕТРЫ ГЕНЕРАЦИИ (drawer «Настройки», секция «Параметры генерации») ──
  // Ползунки range для gen_k/rank_keep/source_n/read_min_score/keep_min_score.
  // Применение: мгновенно по change (как Direction/Feeds), но с debounce 300ms, чтобы
  // протащить ползунок 8→12 одним движением = ОДИН POST, а не 5. Загоняем все 5 значений
  // одним запросом — атомарно, state.json не дёргаем.
  _bindGenparams() {
    this._gpDebounce = null;
    const inputs = Renderer.$$('#settings-body input[data-gp]');
    inputs.forEach(inp => {
      // input event — обновляем только текстовое значение (живой отклик под пальцем),
      // POST уходит по change (когда юзер отпустил ползунок) + debounce.
      inp.addEventListener('input', () => this._updateParamValLabel(inp));
      inp.addEventListener('change', () => this._scheduleGenparamsSave());
    });
    const reset = Renderer.$('#btn-genparams-reset');
    if (reset) reset.onclick = () => {
      if (!confirm('Сбросить ВСЕ параметры генерации к значениям по умолчанию?')) return;
      this.api.setGenparams({ reset: true }).then(r => {
        if (r && r.ok) {
          this.toasts.show('Параметры сброшены к умолчаниям', 'success');
          this._refreshSoon();
        } else {
          this.toasts.show('Сброс: ' + ((r && r.msg) || 'не вышло'), 'error');
        }
      });
    };
  }

  _updateParamValLabel(inp) {
    const key = inp.dataset.gp;
    const valEl = Renderer.$('#pv-' + key);
    if (!valEl) return;
    const v = parseFloat(inp.value);
    valEl.textContent = key.endsWith('_score') ? v.toFixed(1) : String(Math.round(v));
    if (key === 'gen_k') {
      const keep = Renderer.$('#p-rank_keep');
      const keepLabel = Renderer.$('#pv-rank_keep');
      if (keep) {
        const keepMax = Math.min(8, Math.round(v));
        keep.max = String(keepMax);
        if (Number(keep.value) > keepMax) keep.value = String(keepMax);
        if (keepLabel) keepLabel.textContent = String(Math.round(Number(keep.value)));
      }
    }
  }

  _scheduleGenparamsSave() {
    if (this._gpDebounce) clearTimeout(this._gpDebounce);
    this._gpDebounce = setTimeout(() => {
      this._gpDebounce = null;
      this._saveGenparams();
    }, 300);
  }

  _saveGenparams() {
    // Собираем все 5 значений из DOM. keep_min_score: UI 0..10 → env 0..1 (делим на 10).
    // Остальные уходят как есть — clamp на стороне genparams.save по диапазонам.
    const inputs = Renderer.$$('#settings-body input[data-gp]');
    const payload = {};
    inputs.forEach(inp => {
      const key = inp.dataset.gp;
      let v = parseFloat(inp.value);
      if (isNaN(v)) return;
      if (key === 'keep_min_score') v = v / 10;  // UI 0..10 → env 0..1
      payload[key] = v;
    });
    this.api.setGenparams(payload).then(r => {
      if (!r || !r.ok) this.toasts.show('Параметры: ' + ((r && r.msg) || 'не вышло'), 'error');
      else this._refreshSoon();
    });
  }

  openSettings() {
    Renderer.$('#settings-overlay').classList.add('open');
    Renderer.$('#settings-drawer').classList.add('open');
    this._renderSettingsLists();
  }
  closeSettings() {
    Renderer.$('#settings-overlay').classList.remove('open');
    Renderer.$('#settings-drawer').classList.remove('open');
  }

  _renderSettingsLists() {
    const S = this.state.data;
    if (!S) return;
    // Папки
    const folderHost = Renderer.$('#settings-body .settings-section:nth-of-type(2) .list-item');
    // Простой подход: перерисуем секции папок/лент/совета из state
    const body = Renderer.$('#settings-body');
    if (!body) return;
    // Папки
    const foldersSection = this._findSection(body, 'Папки');
    if (foldersSection) this._renderFoldersList(foldersSection, S.folders);
    // Ленты
    const feedsSection = this._findSection(body, 'Ленты');
    if (feedsSection) this._renderFeedsList(feedsSection, S.feeds);
    // Совет (поиск регистрочувствителен: заголовок секции «Участники совета» — строчная «с»)
    const councilSection = this._findSection(body, 'совет');
    if (councilSection) this._renderCouncilList(councilSection, S.council);

    // Синхронизация тумблера темы с текущей темой.
    // Условный язык: on = risograph (по умолчанию), off = industrial.
    const themeToggle = body.querySelector('.toggle[data-toggle="theme"]');
    if (themeToggle) {
      const cur = document.documentElement.getAttribute('data-theme') || 'risograph';
      themeToggle.classList.toggle('on', cur === 'risograph');
      const nm = body.querySelector('#theme-name');
      if (nm) nm.textContent = cur === 'risograph' ? 'Risograph' : 'Industrial';
    }
  }

  _findSection(body, titleSub) {
    const sections = Renderer.$$('.settings-section', body);
    return sections.find(s => {
      const t = s.querySelector('.settings-section-title');
      return t && t.textContent.includes(titleSub);
    });
  }

  _renderFoldersList(section, foldersData) {
    const items = section.querySelectorAll('.list-item');
    items.forEach(i => i.remove());
    const addRow = section.querySelector('.list-add-row');
    const folders = (foldersData && foldersData.folders) || [];
    // probe берём из API (UIController сам отвечает за settings-данные; renderer
    // здесь ни при чём). Кешируем на экземпляре — не дёргаем сервер на каждый рендер.
    const probeMap = this._foldProbe || {};
    folders.forEach(f => {
      section.insertBefore(Renderer._folderItem(f, probeMap), addRow);
    });
    // Фоновый poll probe — обновит заметки при следующем открытии drawer.
    if (!this._probeInFlight) {
      this._probeInFlight = true;
      API.probeFolders().then(r => {
        if (r && r.probe) {
          this._foldProbe = r.probe;
          // перерисуем заметки прямо сейчас, если drawer ещё открыт
          if (Renderer.$('#settings-drawer.open')) this._renderFoldersList(section, foldersData);
        }
      }).catch(() => {}).finally(() => { this._probeInFlight = false; });
    }
  }

  _renderFeedsList(section, feedsData) {
    const items = section.querySelectorAll('.list-item');
    items.forEach(i => i.remove());
    const all = (feedsData && feedsData.all) || [];
    const enabled = new Set((feedsData && feedsData.enabled) || []);
    const NM = { hn: 'Hacker News', reddit: 'Reddit', lobsters: 'Lobsters', gh_trending: 'GitHub Trending', telegram: 'Telegram' };
    all.forEach(name => {
      const item = Renderer.el('div', { cls: 'list-item' });
      item.appendChild(Renderer.el('div', { cls: ['toggle', enabled.has(name) ? 'on' : ''], attrs: { 'data-toggle': 'feed' } }));
      const path = Renderer.el('span', { cls: 'list-item-path', text: NM[name] || name });
      path.style.fontFamily = 'var(--font-sans)';
      item.appendChild(path);
      section.appendChild(item);
    });
  }

  _renderCouncilList(section, councilData) {
    const items = section.querySelectorAll('.list-item');
    items.forEach(i => i.remove());
    const all = (councilData && councilData.all) || [];
    const enabled = new Set((councilData && councilData.enabled) || []);
    const NM = { rank_ideas: 'Арбитр (rank_ideas)', ask_llm: 'Интуиция (ask_llm)', orchestra: 'Оркестр (orchestra)' };
    all.forEach(name => {
      const item = Renderer.el('div', { cls: 'list-item' });
      item.appendChild(Renderer.el('div', { cls: ['check', enabled.has(name) ? 'on' : ''], attrs: { 'data-check': 'council' } }));
      const path = Renderer.el('span', { cls: 'list-item-path', text: NM[name] || name });
      path.style.fontFamily = 'var(--font-sans)';
      item.appendChild(path);
      section.appendChild(item);
    });
  }

  // ── DOCK ──
  _bindDock() {
    const dock = Renderer.$('#journal-dock');
    const header = Renderer.$('#dock-header');
    if (header) header.onclick = () => dock.classList.toggle('open');
  }

  // ── ДЕЙСТВИЯ В ЛЕВОЙ ПАНЕЛИ ──
  _bindLeftActions() {
    const actions = Renderer.$$('.actions-list .btn-action');
    if (actions[0]) actions[0].onclick = () => this.runGoal('приноси свежие идеи');
    if (actions[1]) actions[1].onclick = () => this.runGoal('доделать существующие проекты');
    if (actions[2]) actions[2].onclick = () => this.stopRun();
    // направление
    const applyBtn = Renderer.$('#left .dir-input-row .btn-mini[title="Применить"]');
    const input = Renderer.$('#left .dir-input-row input');
    if (applyBtn && input) applyBtn.onclick = () => this.applyDirText();
    if (input) input.onkeydown = (e) => { if (e.key === 'Enter') this.applyDirText(); };
    const addPresetBtn = Renderer.$('#left .dir-input-row .btn-mini[title="Сохранить в пресеты"]');
    if (addPresetBtn && input) addPresetBtn.onclick = () => this.addPreset();
  }

  applyDirText() {
    const input = Renderer.$('#left .dir-input-row input');
    if (!input) return;
    const t = input.value.trim();
    if (!t) return;
    input.value = '';
    this.api.setDirection({ current: t }).then(() => this._refreshSoon());
  }

  addPreset() {
    const input = Renderer.$('#left .dir-input-row input');
    if (!input) return;
    const t = input.value.trim();
    if (!t) return;
    input.value = '';
    const presets = ((this.state.data && this.state.data.direction && this.state.data.direction.presets) || []).slice();
    if (!presets.some(p => p.toLowerCase() === t.toLowerCase())) presets.push(t);
    this.api.setDirection({ current: t, presets }).then(() => this._refreshSoon());
  }

  applyPreset(i) {
    const p = ((this.state.data && this.state.data.direction && this.state.data.direction.presets) || [])[i];
    if (p !== undefined) this.api.setDirection({ current: p }).then(() => this._refreshSoon());
  }

  removePreset(i) {
    const presets = ((this.state.data && this.state.data.direction && this.state.data.direction.presets) || []).slice();
    const removed = presets.splice(i, 1)[0];
    const cur = ((this.state.data && this.state.data.direction && this.state.data.direction.current) || '').toLowerCase();
    const payload = { presets };
    if (removed && removed.toLowerCase() === cur) payload.current = '';
    this.api.setDirection(payload).then(() => this._refreshSoon());
  }

  clearDirection() {
    this.api.setDirection({ current: '' }).then(() => this._refreshSoon());
  }

  // ── ДЕЙСТВИЯ В ПАНЕЛИ ИДЕЙ ──
  _bindIdeasPanelActions() {
    const purge = Renderer.$('#btn-purge');
    if (purge) purge.onclick = () => this.purgeLowScore();

    const doneToggle = Renderer.$('#btn-done-toggle');
    const doneToggle2 = Renderer.$('#done-toggle');
    const handler = () => {
      const list = Renderer.$('#done-list');
      if (!list) return;
      const open = list.classList.toggle('open');
      if (doneToggle2) doneToggle2.classList.toggle('open', open);
    };
    if (doneToggle) doneToggle.onclick = handler;
    if (doneToggle2) doneToggle2.onclick = handler;
  }

  // ── ОСНОВНЫЕ ДЕЙСТВИЯ ──
  async runGoal(goal) {
    if (this.state.run && this.state.run.running) {
      this.toasts.show('Прогон уже идёт', 'warn');
      return;
    }
    const r = await this.api.startRun(goal);
    if (!r.ok) {
      this.toasts.show('Запуск: ' + (r.msg || 'не вышло'), 'error');
      return;
    }
    this._runStartTime = Date.now();
    this.toasts.show('Прогон запущен: «' + goal + '»', 'info');
    // поднять консоль
    const area = Renderer.$('#console-area');
    if (area) { area.classList.remove('collapsed'); area.classList.add('expanded'); }
    // PollingManager подхватит по /api/state (running=true)
  }

  async stopRun() {
    const r = await this.api.stopRun();
    if (!r.ok) this.toasts.show('Стоп: ' + (r.msg || 'нечего'), 'warn');
    else this.toasts.show('Прогон остановлен', 'info');
  }

  async observe() {
    const r = await this.api.observe();
    if (!r.ok) this.toasts.show('Наблюдение: ' + (r.msg || 'не вышло'), 'error');
    else {
      this._runStartTime = Date.now();
      const area = Renderer.$('#console-area');
      if (area) { area.classList.remove('collapsed'); area.classList.add('expanded'); }
    }
  }

  async ideaAct(id, status) {
    const card = Renderer.$('#idea-' + id);
    if (card) card.classList.add('removing');
    const r = await this.api.setIdea(id, status);
    if (!r.ok) {
      if (card) card.classList.remove('removing');
      this.toasts.show('Идея #' + id + ': ' + (r.msg || 'не вышло'), 'error');
      return;
    }
    // через 300 мс (анимация) — карточку уберёт следующий refresh
    setTimeout(() => this._refreshSoon(), 280);
    const verb = { take: 'взял', later: 'позже', trash: 'мусор' }[status] || status;
    this.toasts.show('#' + id + ' → ' + verb, 'success', 2000);
  }

  async purgeLowScore() {
    const ideas = (this.state.data && this.state.data.inbox && this.state.data.inbox.ideas) || [];
    const low = ideas.filter(i => i.status === 'open' && i.score != null && parseFloat(i.score) < 8.0);
    if (!low.length) {
      this.toasts.show('Нет идей с оценкой < 8.0', 'info');
      return;
    }
    const sample = low.slice(0, 5).map(i => `  #${i.id} (${parseFloat(i.score).toFixed(1)}): ${(i.title || '').slice(0, 50)}`).join('\n');
    const more = low.length > 5 ? `\n  ...и ещё ${low.length - 5}` : '';
    if (!confirm(`Отправить в мусор ВСЕ открытые идеи с оценкой < 8.0?\n\nНайдено: ${low.length} шт.\n\n${sample}${more}\n\nЭто нельзя отменить.`)) return;
    const btn = Renderer.$('#btn-purge');
    const orig = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = '⏳ очищаю…'; }
    try {
      const r = await this.api.purge(8.0);
      if (r.ok) {
        const msg = `Зачищено ${r.purged} идей с оценкой < 8.0` + (r.failed ? ` · ошибок: ${r.failed}` : '');
        this.toasts.show(msg, r.failed ? 'warn' : 'success', 4000);
        if (r.failed) console.log('[purge] partial', r.failed_details);
      } else {
        this.toasts.show('Очистка: ' + (r.msg || 'не вышло'), 'error');
      }
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = orig; }
      this._refreshSoon();
    }
  }

  _refreshSoon() {
    if (this._refreshTimer) return;
    this._refreshTimer = setTimeout(() => {
      this._refreshTimer = null;
      if (window.__kiborgPoller) window.__kiborgPoller.kickState();
    }, 200);
  }
}

/* ────────────────────────────────────────────────────────────────────────
   7. PollingManager — опрашивает сервер по интервалам.
      Состояние покоя: /api/state каждые 5 сек.
      Прогон идёт: /api/run каждые 900 мс, /api/state приостановлен (его
      обновит событие завершения прогона — PollingManager вернётся к 5 сек).
   ──────────────────────────────────────────────────────────────────────── */
class PollingManager {
  constructor(state, api) {
    this.state = state;
    this.api = api;
    this._stateTimer = null;
    this._runTimer = null;
    this._runActive = false;
    this._runningSince = null;
    window.__kiborgPoller = this;

    // Когда state.running переключается — меняем режим
    state.subscribe('running-toggle', (running) => {
      if (running) this._enterRunMode();
      else this._exitRunMode();
    });
  }

  start() {
    this._scheduleState(0);  // сразу
  }

  kickState() {
    this._fetchState();
  }

  _scheduleState(delay) {
    if (this._stateTimer) clearTimeout(this._stateTimer);
    this._stateTimer = setTimeout(async () => {
      this._stateTimer = null;
      await this._fetchState();
      if (!this._runActive) this._scheduleState(5000);
    }, delay);
  }

  async _fetchState() {
    const r = await this.api.state();
    if (r.error || (!r.now && !r.inbox)) {
      // сервер молчит
      this.state.setError();
    } else {
      this.state.updateState(r);
    }
  }

  _enterRunMode() {
    if (this._runActive) return;
    this._runActive = true;
    this._runningSince = Date.now();
    // погасить 5-сек опрос state — он будет мешаться и дублировать load
    if (this._stateTimer) { clearTimeout(this._stateTimer); this._stateTimer = null; }
    this._scheduleRun(0);
  }

  _scheduleRun(delay) {
    if (this._runTimer) clearTimeout(this._runTimer);
    this._runTimer = setTimeout(async () => {
      this._runTimer = null;
      const r = await this.api.run();
      this.state.updateRun(r);
      if (r && r.running) {
        this._scheduleRun(900);
      } else {
        // прогон завершился — забрать финальный state и выйти из режима
        await this._fetchState();
        this._exitRunMode();
      }
    }, delay);
  }

  _exitRunMode() {
    if (!this._runActive) return;
    this._runActive = false;
    if (this._runTimer) { clearTimeout(this._runTimer); this._runTimer = null; }
    this._runningSince = null;
    // финальная чистка run-секции + возврат к 5-сек циклу
    const section = Renderer.$('#run-section');
    if (section) section.classList.remove('visible');
    this._scheduleState(0);
  }
}

/* ────────────────────────────────────────────────────────────────────────
   8. INIT — связываем всё вместе после загрузки DOM.

   Глобальные обработчики ошибок вешаем ДО init: если что-то упадёт в конструкторе
   State/Renderer/UIController/Poller (например, селектор не найден), ошибка не
   должна молча поглотиться. Выводим её в консоль + показываем красный тост, чтобы
   пользователь сразу видел, что пульт не ожил (а не думал, что интерфейс «старый»).
   ──────────────────────────────────────────────────────────────────────── */
function __showFatalError(where, err) {
  console.error('[kiborg] FATAL в ' + where + ':', err);
  const container = document.getElementById('toasts');
  if (container) {
    const t = document.createElement('div');
    t.className = 'toast error';
    t.style.cssText = 'position:fixed;top:20px;left:50%;transform:translateX(-50%);max-width:90vw;z-index:9999';
    const msg = (err && err.message) ? err.message : String(err);
    t.textContent = '⚠ Пульт v2 упал: ' + where + ' — ' + msg + ' (см. консоль)';
    container.appendChild(t);
  }
}

window.addEventListener('error', (e) => {
  __showFatalError('window.onerror', e.error || e.message);
});
window.addEventListener('unhandledrejection', (e) => {
  __showFatalError('Promise', e.reason);
});

function __initKiborgPanel() {
  try {
    const state = new State();
    const api = API;
    const knight = new Knight(Renderer.$('#knight-svg'));
    const toasts = new Toasts(Renderer.$('#toasts'));

    // Очистим демо-тосты и демо-карточки (каркас содержал примеры)
    const demoToasts = Renderer.$$('#toasts .toast');
    demoToasts.forEach(t => t.remove());

    // Применяем сохранённую тему (по умолчанию — risograph)
    const savedTheme = localStorage.getItem('kiborg-theme') || 'risograph';
    document.documentElement.setAttribute('data-theme', savedTheme);

    const renderer = new Renderer(state, knight);
    const ui = new UIController(state, api, knight, toasts);
    const poller = new PollingManager(state, api);

    poller.start();
    console.log('[kiborg] пульт v2 запущен, поллинг /api/state каждые 5 сек');
  } catch (e) {
    __showFatalError('__initKiborgPanel', e);
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', __initKiborgPanel);
} else {
  __initKiborgPanel();
}
