// desk.js: runs the REAL trading_desk graph from this page (POST /threads, POST /threads/{id}/runs/stream) and animates the events.
// Structure comes from GET /assistants/{id}/graph?xray=true (laid out by desk_layout.js); data comes from the stream
// (tasks / checkpoints / values). A camera (SVG viewBox) follows the action across the big virtual canvas.
//
//   ?mode=run    (default) a real run, paced for humans, camera follows
//   ?mode=tour   no run: the graph is revealed and toured, with concept callouts
//   ?mode=state  no run: which node writes to which state channel (reducers highlighted)
//   ?mode=norun  layout only (screenshots)      ?api=http://127.0.0.1:2024      ?speed=1
import { API, MONO, jpost, loadAssistants, loadGraph, createView, textW } from './lg.js';
import { layoutDesk } from './desk_layout.js';

const Q = new URLSearchParams(location.search);
const MODE = Q.get('mode') || 'run';
const SPEED = Math.max(0.2, parseFloat(Q.get('speed')) || 1);
const GRAPH = 'trading_desk';
const NSV = 'http://www.w3.org/2000/svg';
const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms / SPEED));
const T0 = performance.now();
const now = () => (performance.now() - T0) / 1000;
const mark = (name, data) => { try { if (window.rec && window.rec.mark) window.rec.mark(name, data); } catch (e) { /* not recording */ } };
window.__events = []; window.__log = []; window.__done = false; window.__state = {};

const PROMPT = 'Run our full 24-node institutional trading workflow across NVDA, AMD, MSFT, SPY, QQQ, and TLT: dispatch the 6-agent Intelligence Syndicate and single-ticker deep research workers, run the Bull vs Bear debate, execute all 4 Strategy Desks (Momentum pitching a $15M NVDA lead trade), run the Triad Risk Committee with selective desk re-dispatch until approved, then generate the Head of Trading blotter and the EMS child-order plan.';
const TICKERS = ['NVDA', 'AMD', 'MSFT', 'SPY', 'QQQ', 'TLT'];
const VAR_CAP = 1450000; // MANDATE_LIMITS["max_portfolio_var_99_1d_usd"] in src/trading_desk/limits.py

// ---------------------------------------------------------------------------------------------------- scenario
const DEC = { EXECUTE_ALL: 'execute all', EXECUTE_SCALED_AND_HEDGED: 'scaled+hedged', HALT: 'halt' };
const SC = {
  fan: { ticker_deep_research_worker: { cols: 6 } },
  groupFan: { strategy_desk: { n: 4 } },
  groupSub: { strategy_desk: '× 4 via Send', intelligence_syndicate: 'parallel', triad_risk_committee: 'auditors → CRO' },
  channels: [
    { k: 'cio_mandate', w: 1.05, fmt: (v) => (v.cio_mandate ? `${Math.round(v.cio_mandate.target_firm_gross_utilization_pct)}% gross` : null) },
    { k: 'intel_reports', w: 1.0, tag: 'merge_dicts', fmt: (v) => { const n = Object.keys(v.intel_reports || {}).length; return n ? `${n} / 6` : null; } },
    { k: 'ticker_dossiers', w: 1.1, tag: 'merge_dicts', fmt: (v, L) => { const c = Math.max(Object.keys(v.ticker_dossiers || {}).length, L.count.ticker_deep_research_worker || 0); return c ? `${c} / ${L.total.ticker_deep_research_worker || 6}` : null; } },
    { k: 'adversarial_debate', w: 1.25, label: 'adversarial_debate', tag: 'merge_dicts', fmt: (v) => { const d = v.adversarial_debate || {}; if (d.resolution) return String(d.resolution.winning_tilt || '').split('_').slice(0, 2).join(' ').toLowerCase(); const n = Object.keys(d).length; return n ? `${n} / 3` : null; } },
    { k: 'desk_proposals', w: 1.0, tag: 'merge_dicts', fmt: (v, L) => { const n = Object.keys(v.desk_proposals || {}).length; const c = L.count.strategy_desk; if (c !== undefined && c < (L.total.strategy_desk || 0)) return `${c} / ${L.total.strategy_desk}`; return n ? `${n} / 4` : null; } },
    { k: 'risk_sub_reports', w: 1.0, tag: 'merge_dicts', fmt: (v) => { const n = Object.keys(v.risk_sub_reports || {}).length; return n ? `${n} / 2` : null; } },
    { k: 'risk_assessment', w: 1.25, fmt: (v) => (v.risk_assessment ? v.risk_assessment.status : null) },
    { k: 'revision_count', w: 0.95, fmt: (v) => (v.revision_count !== undefined && v.revision_count !== null && STATE.ckpts > 1 ? `${v.revision_count} of ${v.max_revisions || 2}` : null) },
    { k: 'head_of_trading_decision', w: 1.2, label: 'head_of_trading', fmt: (v) => (v.head_of_trading_decision ? DEC[v.head_of_trading_decision.overall_decision] || v.head_of_trading_decision.overall_decision : null) },
    { k: 'execution_microstructure_plan', w: 1.15, label: 'execution_plan', fmt: (v) => (v.execution_microstructure_plan ? `${v.execution_microstructure_plan.total_parent_orders} orders` : null) },
  ],
};
// who writes which channel (used by ?mode=state)
const WRITERS = {
  cio_mandate: ['cio_regime_router'],
  intel_reports: ['premarket_microstructure', 'technical_analyst', 'macro_strategist', 'news_sentiment', 'vol_surface_analyst', 'factor_cointegration_analyst'],
  ticker_dossiers: ['ticker_deep_research_worker'],
  adversarial_debate: ['bull_thesis_advocate', 'bear_thesis_critic', 'adversarial_debate_arbiter'],
  desk_proposals: ['strategy_desk:desk_head_pm'],
  risk_sub_reports: ['factor_exposure_auditor', 'tail_var_monte_carlo'],
  risk_assessment: ['cro_risk_synthesis'],
  revision_count: ['cro_risk_synthesis'],
  head_of_trading_decision: ['head_of_trading'],
  execution_microstructure_plan: ['execution_microstructure_router'],
};
const EDGE_LABEL = {
  'adversarial_debate_arbiter>strategy_desk:desk_quant_modeler': { text: 'Send() × 4 desks', dx: 0, dy: 0 },
  'cro_risk_synthesis>strategy_desk:desk_quant_modeler': { text: 'REVISE_DESKS → Send() to the flagged desks only', dx: 0, dy: 0 },
};
const NODE_STAGE = {
  cio_regime_router: 'syndicate', premarket_microstructure: 'syndicate', technical_analyst: 'syndicate', macro_strategist: 'syndicate', news_sentiment: 'syndicate',
  vol_surface_analyst: 'syndicate', factor_cointegration_analyst: 'syndicate', ticker_deep_research_worker: 'syndicate',
  intelligence_aggregator: 'syndicate', bull_thesis_advocate: 'debate', bear_thesis_critic: 'debate', adversarial_debate_arbiter: 'debate',
  'strategy_desk:desk_quant_modeler': 'desks', 'strategy_desk:desk_head_pm': 'desks', 'g:strategy_desk': 'desks', factor_exposure_auditor: 'desks', tail_var_monte_carlo: 'desks', cro_risk_synthesis: 'desks',
  head_of_trading: 'execution', execution_microstructure_router: 'execution', halt_no_trade: 'execution',
};
const PACE = { edge: 700, startDwell: 520, endDwell: 380, chip: 90, chipLater: 40, intro: 2600, hold: 3200, minRun: 900 };
const LOOP_FACTOR = [1, 0.7, 0.5];
const loopFactor = (runs) => LOOP_FACTOR[Math.min(runs, LOOP_FACTOR.length - 1)];

let G, V, A, runToken = 0;
const LIVE = { count: {}, total: {} };
const STATE = { thread: null, ckpts: 0, values: {}, runs: {}, lastDone: ['__start__'] };
const nodeRec = (id) => (STATE.runs[id] = STATE.runs[id] || { active: 0, done: 0, total: 0, runs: 0, startedAt: 0, chipOf: new Map(), fanId: null });

// ---------------------------------------------------------------------------------------------------- camera
const VPW = 1232, VPH = 496;
const CAM = {
  cx: 1000, cy: 400, z: 0.5, name: null, token: 0,
  apply() { const w = VPW / this.z, h = VPH / this.z; $('g').setAttribute('viewBox', `${this.cx - w / 2} ${this.cy - h / 2} ${w} ${h}`); },
  fit(r) { return { cx: (r.x0 + r.x1) / 2, cy: (r.y0 + r.y1) / 2, z: Math.min(VPW / (r.x1 - r.x0), VPH / (r.y1 - r.y0)) }; },
  jump(name) { const t = this.fit(G.stages[name]); Object.assign(this, t, { name }); this.apply(); },
  go(name, ms = 1600) {
    if (this.name === name) return Promise.resolve();
    this.name = name;
    const to = this.fit(G.stages[name]);
    const from = { cx: this.cx, cy: this.cy, z: this.z };
    const my = ++this.token;
    mark('camera', { stage: name });
    return new Promise((resolve) => {
      const t0 = performance.now();
      const step = (tn) => {
        if (my !== this.token) return resolve();
        const t = Math.min(1, (tn - t0) / (ms / SPEED));
        const k = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
        this.z = Math.exp(Math.log(from.z) + (Math.log(to.z) - Math.log(from.z)) * k);
        this.cx = from.cx + (to.cx - from.cx) * k; this.cy = from.cy + (to.cy - from.cy) * k;
        this.apply();
        if (t < 1) requestAnimationFrame(step); else resolve();
      };
      requestAnimationFrame(step);
    });
  },
};

// ---------------------------------------------------------------------------------------------------- page chrome
function setStatus(kind, text) { const s = $('status'); s.className = `pill ${kind}`; s.textContent = text; }
function buildChrome() {
  $('legend').innerHTML = `<svg width="330" height="30" viewBox="0 0 330 30" style="display:block"><defs><marker id="lgm" viewBox="0 0 10 10" refX="9.5" refY="5" markerWidth="12" markerHeight="12" markerUnits="userSpaceOnUse" orient="auto"><path d="M0.5,0.8 L9.5,5 L0.5,9.2 L2.8,5 z" fill="#9aa7c0"/></marker></defs>
    <path d="M2 15 L40 15" stroke="#9aa7c0" stroke-width="3" marker-end="url(#lgm)" fill="none"/><text x="50" y="16" fill="#9aa7c0" font-size="16" font-weight="600" dominant-baseline="central">edge</text>
    <path d="M112 15 L156 15" stroke="#9aa7c0" stroke-width="3" stroke-dasharray="9 7" marker-end="url(#lgm)" fill="none"/><text x="170" y="16" fill="#9aa7c0" font-size="16" font-weight="600" dominant-baseline="central">conditional edge</text></svg>`;
}
function buildStrip() {
  const strip = $('strip'); strip.innerHTML = '';
  for (const c of SC.channels) {
    const d = document.createElement('div');
    d.className = 'card'; d.dataset.k = c.k; d.style.flex = `${c.w} 1 0`;
    d.innerHTML = `<div class="ck2">${c.label || c.k}</div><div class="cv dim">—</div>${c.tag ? `<div class="ct">${c.tag}</div>` : ''}`;
    strip.appendChild(d);
  }
}
function renderCards(values, flash = true) {
  for (const c of SC.channels) {
    const card = document.querySelector(`.card[data-k="${CSS.escape(c.k)}"]`);
    if (!card) continue;
    let t = null;
    try { t = c.fmt(values || {}, LIVE); } catch (e) { t = null; }
    const cv = card.querySelector('.cv');
    const txt = t === null || t === undefined ? '—' : String(t);
    if (cv.textContent !== txt) {
      cv.textContent = txt;
      const avail = card.clientWidth - 22;
      let fs = 21;
      while (fs > 14 && textW(txt, fs, 800) > avail) fs -= 1;
      cv.style.fontSize = fs + 'px';
      cv.classList.toggle('dim', t === null || t === undefined);
      if (flash && t !== null) { card.classList.remove('flash'); void card.offsetWidth; card.classList.add('flash'); }
    }
  }
}

// ---------------------------------------------------------------------------------------------------- overlays in graph coordinates
const svgEl = (tag, attrs = {}, parent) => { const n = document.createElementNS(NSV, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); if (parent) parent.appendChild(n); return n; };
/** Gold callout box (+ leader line to a target point), drawn in graph coordinates so it pans and zooms with the camera. */
function callout(text, sub, at, target, o = {}) {
  const g = svgEl('g', { class: 'co' }, V.gOver);
  const fs1 = o.size || 26, fs2 = 17;
  const w1 = textW(text, fs1, 800), w2 = sub ? textW(sub, fs2, 600) : 0;
  const w = Math.max(w1, w2) + 36, h = sub ? 66 : 46;
  const x = at.x - w / 2, y = at.y - h / 2;
  if (target) {
    const sx = Math.max(x + 26, Math.min(x + w - 26, target.x)), sy = target.y > at.y ? y + h : y;
    svgEl('path', { d: `M${sx} ${sy} C${sx} ${(sy + target.y) / 2} ${target.x} ${(sy + target.y) / 2} ${target.x} ${target.y}`, stroke: '#f4c95d', 'stroke-width': 3.4, fill: 'none', 'stroke-linecap': 'round' }, g);
    svgEl('circle', { cx: target.x, cy: target.y, r: 6.5, fill: '#f4c95d' }, g);
  }
  svgEl('rect', { x, y, width: w, height: h, rx: 13, fill: '#1b1606', stroke: '#f4c95d', 'stroke-width': 3 }, g);
  const t1 = svgEl('text', { x: at.x, y: y + (sub ? 25 : h / 2 + 1), 'text-anchor': 'middle', fill: '#f4c95d', 'font-size': fs1, 'font-weight': 800, 'dominant-baseline': 'central' }, g); t1.textContent = text;
  if (sub) { const t2 = svgEl('text', { x: at.x, y: y + 49, 'text-anchor': 'middle', fill: '#e8dfc4', 'font-size': fs2, 'font-weight': 600, 'dominant-baseline': 'central' }, g); t2.textContent = sub; }
  mark('callout', { text, sub });
  window.__log.push({ t: +now().toFixed(2), kind: 'callout', text: sub ? `${text}: ${sub}` : text });
  return g;
}
const nodeBox = (id) => G.nodes.get(id);
const PILLS = {};
function lightPill(status) { for (const [k, p] of Object.entries(PILLS)) { const on = k === status; p.r.setAttribute('fill', on ? (status === 'APPROVED' ? '#3fe0a0' : status === 'REJECTED' ? '#ff6b7a' : '#ffb938') : '#0f1626'); p.r.setAttribute('stroke', on ? 'none' : '#3a4763'); p.t.setAttribute('fill', on ? '#08130f' : '#9aa7c0'); } }
const P = (id, side = 'c') => { const n = nodeBox(id); return { x: n.cx + (side === 'r' ? n.w / 2 : side === 'l' ? -n.w / 2 : 0), y: n.cy + (side === 'b' ? n.h / 2 : side === 't' ? -n.h / 2 : 0) }; };
const E = (id, f = 0.5) => { const e = V.edges[id]; const p = e.path.getPointAtLength(e.len * f); return { x: p.x, y: p.y }; };
/** Small always-on labels on the three CRO exits (they explain the conditional edges before anything runs). */
function staticLabels() {
  const g = svgEl('g', { id: 'static-labels' }, V.gOver.parentNode); // above edges, below overlays
  V.gOver.parentNode.insertBefore(g, V.gOver);
  const add = (x, y, text) => { const w = textW(text, 15, 700, MONO) + 16; const r = svgEl('rect', { x: x - w / 2, y: y - 12, width: w, height: 24, rx: 12, fill: '#0f1626', stroke: '#3a4763', 'stroke-width': 1.6 }, g); const t = svgEl('text', { x, y: y + 1, 'text-anchor': 'middle', 'dominant-baseline': 'central', fill: '#9aa7c0', 'font-size': 15, 'font-weight': 700, style: `font-family:${MONO}` }, g); t.textContent = text; PILLS[text] = { r, t }; };
  const cro = nodeBox('cro_risk_synthesis'), hot = nodeBox('head_of_trading'), halt = nodeBox('halt_no_trade');
  add(E('cro_risk_synthesis>head_of_trading', 0.5).x, cro.cy - 26, 'APPROVED');
  add(cro.cx + 54, (cro.cy + cro.h / 2 + halt.cy - halt.h / 2) / 2, 'REJECTED');
  add(cro.cx - 34, cro.cy - cro.h / 2 - 24, 'REVISE_DESKS');
  void hot;
}

// ---------------------------------------------------------------------------------------------------- SSE + queue
async function* sseStream(url, body) {
  const res = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json', accept: 'text/event-stream' }, body: JSON.stringify(body) });
  if (!res.ok) throw new Error(`stream ${res.status}`);
  const rd = res.body.getReader(); const dec = new TextDecoder();
  let buf = '', ev = null, data = [];
  for (;;) {
    const { value, done } = await rd.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.search(/\r\n|\n|\r/)) >= 0) {
      const m = /^(\r\n|\n|\r)/.exec(buf.slice(i));
      const line = buf.slice(0, i); buf = buf.slice(i + m[0].length);
      if (line === '') { if (ev !== null || data.length) yield { event: ev || 'message', data: data.join('\n') }; ev = null; data = []; }
      else if (line.startsWith('event:')) ev = line.slice(6).trim();
      else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
    }
  }
}
function splitName(name) {
  const i = name.indexOf('|');
  if (i < 0) return { mode: name, ns: [] };
  return { mode: name.slice(0, i), ns: name.slice(i + 1).split('|').filter(Boolean).map((s) => { const j = s.indexOf(':'); return j < 0 ? { node: s, taskId: '' } : { node: s.slice(0, j), taskId: s.slice(j + 1) }; }) };
}
class Queue {
  constructor() { this.items = []; this.waiter = null; this.closed = false; }
  push(it) { this.items.push(it); if (this.waiter) { const w = this.waiter; this.waiter = null; w(); } }
  close() { this.closed = true; if (this.waiter) { const w = this.waiter; this.waiter = null; w(); } }
  async next() { while (!this.items.length) { if (this.closed) return null; await new Promise((r) => { this.waiter = r; }); } return this.items.shift(); }
}
/** Turns raw stream events into queue items (a burst of identical task starts becomes one 'start' with n tasks). */
function makeIngest(q) {
  let pend = null, timer = null;
  const flush = () => { if (timer) { clearTimeout(timer); timer = null; } if (pend) { q.push({ type: 'start', ...pend }); pend = null; } };
  const feed = (eventName, dataStr) => {
    const { mode, ns } = splitName(eventName);
    let d = null;
    try { d = dataStr ? JSON.parse(dataStr) : null; } catch (e) { d = dataStr; }
    window.__events.push({ mode, ns: ns.map((n) => n.node).join('|'), name: d && d.name, t: +now().toFixed(3) });
    if (mode === 'tasks' && d && d.name && !d.name.startsWith('__')) {
      const isEnd = 'result' in d || ('error' in d && !('input' in d));
      if (!isEnd) {
        const key = ns.map((n) => n.node).join('|') + '/' + d.name;
        if (pend && pend.key === key) { pend.n++; pend.taskIds.push(d.id); pend.inputs.push(d.input); }
        else { flush(); pend = { key, node: d.name, ns, n: 1, taskIds: [d.id], inputs: [d.input] }; }
        if (timer) clearTimeout(timer);
        timer = setTimeout(flush, 80); // a lone start must not wait for the next event (which may be 20 s away); a burst still coalesces
      } else { flush(); q.push({ type: 'end', node: d.name, ns, taskId: d.id, result: d.result, error: d.error }); }
      return;
    }
    if (ns.length) return;
    if (mode === 'updates' || mode === 'values' || mode === 'metadata') return;
    flush();
    if (mode === 'checkpoints' && d) q.push({ type: 'ckpt', next: d.next || [], values: d.values || {} });
    else if (mode === 'error') q.push({ type: 'error', message: typeof d === 'string' ? d : (d && (d.message || d.error)) || 'error' });
  };
  return { feed, flush };
}
async function readRun(tid, body, q, token) {
  const ing = makeIngest(q);
  const stream = sseStream(`${API}/threads/${tid}/runs/stream`, { assistant_id: A.id, stream_mode: ['values', 'updates', 'tasks', 'checkpoints'], stream_subgraphs: true, stream_resumable: true, on_disconnect: 'continue', multitask_strategy: 'enqueue', ...body });
  for await (const ev of stream) {
    if (token !== runToken) return;
    ing.feed(ev.event, ev.data);
  }
  ing.flush();
}
/** ?mode=replay&file=...jsonl: feed a previously captured real run (capture_run.py) through the same player, LLM waits compressed by ?rspeed (default 8). */
async function readReplay(file, q, token) {
  const ing = makeIngest(q);
  const rspeed = Math.max(1, parseFloat(Q.get('rspeed')) || 8);
  const lines = (await (await fetch(file, { cache: 'no-store' })).text()).split('\n').filter(Boolean);
  let prev = 0;
  for (const ln of lines) {
    if (token !== runToken) return;
    const r = JSON.parse(ln);
    const wait = Math.min(2500, ((r.t - prev) * 1000) / rspeed);
    if (wait > 8) await new Promise((res) => setTimeout(res, wait));
    prev = r.t;
    ing.feed(r.event, r.data);
  }
  ing.flush();
}

// ---------------------------------------------------------------------------------------------------- player
const nodeIdFor = (ns, name) => (ns.length && G.groups.has(ns[0].node) ? `${ns[0].node}:${name}` : name);
function findEdge(dstId, groupName) {
  const cand = G.edges.filter((e) => (groupName ? e.toGroup === groupName : e.dst === dstId && !e.toGroup) && e.d);
  for (const s of STATE.lastDone) { const e = cand.find((x) => x.src === s); if (e) return e; }
  return null;
}
function boxState(id, state) { if (V.groups[id]) V.setGroup(id, state); }
function pseudoBoxes(nid, phase) {
  // the two visual boxes light up while any member runs and turn green when every member is done
  for (const g of G.groups.values()) {
    if (!g.pseudo || !g.members.includes(nid)) continue;
    const recs = g.members.map((m) => STATE.runs[m]);
    if (phase === 'start') boxState(g.id, 'running');
    else if (recs.every((r) => r && r.runs && r.active === 0)) boxState(g.id, 'done');
  }
}
async function playStart(it) {
  const inner = it.ns.length > 0;
  const isGroupTask = !inner && G.groups.has(it.node) && !G.groups.get(it.node).pseudo;
  const gname = isGroupTask ? it.node : null;
  const nid = isGroupTask ? null : nodeIdFor(it.ns, it.node);
  const key = isGroupTask ? 'g:' + gname : nid;
  const rec = nodeRec(key);
  const lf = loopFactor(rec.runs);
  CAM.go(NODE_STAGE[key] || CAM.name || 'syndicate');
  if ((nid === 'head_of_trading' || nid === 'halt_no_trade') && verdictG) { verdictG.remove(); verdictG = null; }
  let edge = null;
  if (isGroupTask) edge = findEdge(null, gname);
  else if (inner) { const e = findEdge(nid, null); edge = e && STATE.lastDone.includes(e.src) ? e : null; }
  else edge = findEdge(nid, null);
  if (rec.runs && rec.done >= rec.total) { rec.active = 0; rec.done = 0; rec.total = 0; rec.chipOf.clear(); }
  rec.runs += 1;
  if (STATE.lastDone.length === 1 && STATE.lastDone[0] === '__start__') V.setNode('__start__', 'done');
  mark('node-start', { id: key, n: it.n, run: rec.runs, t: +now().toFixed(2) });
  if (edge && !(rec.active > 0)) {
    V.setEdge(edge.id, 'active');
    const lab = EDGE_LABEL[edge.id];
    if (lab && edge.cond) V.edgeLabel(edge.id, lab.text, { dx: lab.dx, dy: lab.dy, size: 19, h: 32 });
    await V.travel(edge.id, PACE.edge * lf * (inner ? 0.6 : 1));
    V.setEdge(edge.id, 'done');
  }
  rec.active += it.n; rec.total += it.n; rec.startedAt = performance.now();
  if (isGroupTask) {
    V.setGroup(gname, 'running');
    showGroupFan(gname, it.n, it.taskIds, rec.runs > 1);
  } else {
    V.setNode(nid, 'running');
    pseudoBoxes(nid, 'start');
  }
  const fanCfg = !isGroupTask && SC.fan[nid];
  if (!isGroupTask && it.n > 1) {
    V.badge(nid, 'n', `×${rec.total}`, { fill: '#f4c95d', size: 17, h: 26 });
    if (fanCfg) {
      const cols = Math.min(fanCfg.cols || it.n, it.n);
      V.setFan(nid, it.n, cols, {});
      rec.fanId = nid;
      it.taskIds.forEach((id, i) => rec.chipOf.set(id, i));
      for (let i = 0; i < it.n; i++) V.chipState(nid, i, 'run');
      V.fanFoot(nid, `0 / ${it.n} tickers`);
      LIVE.total[nid] = it.n; LIVE.count[nid] = 0;
      const lbl = nodeBox(nid); void lbl;
      renderCards(STATE.values, false);
    }
  }
  if (inner && it.n > 1) V.badge(nid, 'n', `×${it.n}`, { fill: '#f4c95d', size: 17, h: 26 });
  if (rec.runs >= 2 && !isGroupTask && !inner) V.badge(nid, 'loop', `↻ ${rec.runs}`, { slot: 'tl', fill: '#2a4a73', color: '#cfe6ff', size: 16, h: 24 });
  if (nid === 'intelligence_aggregator') V.tag(nid, 'merge', 'reducers merged 12 parallel writes', { below: true, size: 16, h: 28, fill: '#f4c95d', color: '#1a1304' });
  await sleep(PACE.startDwell * lf);
}
function showGroupFan(gname, n, taskIds, revising) {
  const gb = G.groups.get(gname).box;
  const id = '__grp_' + gname;
  V.clearFan(id);
  const cs = G.opt.chip, cg = G.opt.chipGap;
  const w = n * (cs + cg) - cg;
  const gEl = svgEl('g', { transform: `translate(${gb.x + 24},${gb.y + gb.h + 14})` }, V.gOver);
  const chips = [];
  for (let i = 0; i < n; i++) chips.push(svgEl('rect', { class: 'chip run', x: i * (cs + cg), y: 0, width: cs, height: cs, rx: 5 }, gEl));
  const t = svgEl('text', { x: w + 12, y: cs / 2 + 1, fill: revising ? '#f4c95d' : '#9aa7c0', 'font-size': 18, 'font-weight': 600, 'dominant-baseline': 'central', style: `font-family:${MONO}` }, gEl);
  t.textContent = revising ? `0 / ${n} flagged desk${n > 1 ? 's' : ''} (revision)` : `0 / ${n} desks`;
  V.fans[id] = { g: gEl, chips, foot: t, n, cols: n, done: 0, revising };
  const rec = nodeRec('g:' + gname);
  rec.fanId = id; rec.chipOf.clear();
  taskIds.forEach((tid, i) => rec.chipOf.set(tid, i));
  LIVE.total[gname] = n; LIVE.count[gname] = 0;
  renderCards(STATE.values, false);
}
const money = (x) => (Math.abs(x) >= 1e6 ? `$${(x / 1e6).toFixed(x % 1e6 === 0 ? 0 : 2).replace(/\.?0+$/, '')}M` : `$${Math.round(x / 1000)}k`);
let verdictG = null;
/** The deterministic engine's verdict, as a card in the empty space under the risk committee (leader line to the CRO). */
function verdictCard(status, lines) {
  if (verdictG) verdictG.remove();
  const cro = nodeBox('cro_risk_synthesis'), halt = nodeBox('halt_no_trade');
  const col = status === 'APPROVED' ? '#3fe0a0' : status === 'REJECTED' ? '#ff6b7a' : '#ffb938';
  const w = Math.max(textW(status, 26, 800, MONO) + 190 + 52, ...lines.map((l) => textW(l, 17, 650) + 36)), h = 104;
  const x = halt.cx - halt.w / 2 - 34 - w, y = cro.cy + 100;
  const g = svgEl('g', { class: 'co' }, V.gOver);
  svgEl('path', { d: `M${x + w - 40} ${y} C${x + w - 40} ${y - 36} ${cro.cx - 74} ${cro.cy + 70} ${cro.cx - 52} ${cro.cy + cro.h / 2 + 6}`, stroke: col, 'stroke-width': 3, fill: 'none', 'stroke-linecap': 'round' }, g);
  svgEl('rect', { x, y, width: w, height: h, rx: 14, fill: '#0e1626', stroke: col, 'stroke-width': 3 }, g);
  const t0 = svgEl('text', { x: x + 18, y: y + 27, fill: col, 'font-size': 26, 'font-weight': 800, 'dominant-baseline': 'central', style: `font-family:${MONO}` }, g); t0.textContent = status;
  const tag = svgEl('text', { x: x + w - 16, y: y + 27, fill: '#7f8db0', 'font-size': 13, 'font-weight': 700, 'text-anchor': 'end', 'dominant-baseline': 'central', style: `font-family:${MONO}` }, g); tag.textContent = 'deterministic engine';
  lines.forEach((l, i) => { const t = svgEl('text', { x: x + 18, y: y + 62 + i * 25, fill: i === 0 ? '#eef2fa' : '#b6c2dc', 'font-size': 17, 'font-weight': i === 0 ? 700 : 600, 'dominant-baseline': 'central' }, g); t.textContent = l; });
  verdictG = g;
}
function annotate(nid, result) {
  if (!result || typeof result !== 'object') return;
  try {
    if (nid === 'cio_regime_router' && result.focus_tickers) V.tag(nid, 'out', `Send() × ${result.focus_tickers.length} tickers`, { below: true, size: 16, h: 28, fill: '#f4c95d', color: '#1a1304' });
    if (nid === 'adversarial_debate_arbiter' && result.adversarial_debate && result.adversarial_debate.resolution) V.tag(nid, 'out', `tilt: ${String(result.adversarial_debate.resolution.winning_tilt).toLowerCase().replace(/_/g, ' ')}`, { below: true, size: 16, h: 28, fill: '#f4c95d', color: '#1a1304' });
    if (nid === 'cro_risk_synthesis' && result.risk_assessment) {
      const ra = result.risk_assessment;
      const flagged = (ra.desk_directives || []).filter((d) => d.must_revise).map((d) => d.desk_name.replace('_DESK', '').toLowerCase());
      const b = (ra.limit_breaches || [])[0];
      let bt = null;
      if (b) { const m = /\[(\w+)\]\s*(\w+) single-leg notional \((\$[\d,]+)\) exceeds cap of (\$[\d,]+)/.exec(b); bt = m ? `${m[2]} leg ${money(Number(m[3].replace(/[$,]/g, '')))} > ${money(Number(m[4].replace(/[$,]/g, '')))} cap` : b.replace(/^\[\w+\]\s*/, '').slice(0, 46); }
      const nb = (ra.limit_breaches || []).length;
      const lines = [bt || `VaR ${money(ra.portfolio_var_99_1d_usd)} of ${money(VAR_CAP)} cap`, nb ? `${nb} breach${nb > 1 ? 'es' : ''} · flagged: ${flagged.join(', ') || 'none'}` : `gross ${Math.round(ra.combined_gross_exposure_pct_nav)}% · net ${Math.round(ra.combined_net_exposure_pct_nav)}% of NAV`];
      verdictCard(ra.status, lines);
      lightPill(ra.status === 'REVISE_DESKS' ? 'REVISE_DESKS' : ra.status);
      window.__log.push({ t: +now().toFixed(2), kind: 'cro', status: ra.status, breaches: ra.limit_breaches, flagged, var: ra.portfolio_var_99_1d_usd, gross: ra.combined_gross_exposure_pct_nav });
    }
    if (nid === 'head_of_trading' && result.head_of_trading_decision) { const h = result.head_of_trading_decision; V.tag(nid, 'out', `${DEC[h.overall_decision] || h.overall_decision} · ${money(h.total_deployed_gross_usd)} gross`, { below: true, size: 16, h: 28, fill: '#3fe0a0', color: '#08130f' }); }
    if (nid === 'execution_microstructure_router' && result.execution_microstructure_plan) { const p = result.execution_microstructure_plan; V.tag(nid, 'out', `${p.total_parent_orders} parent orders · TCA ${money(p.total_expected_tca_cost_usd)}`, { below: true, size: 16, h: 28, fill: '#3fe0a0', color: '#08130f' }); }
  } catch (e) { console.error('annotate', nid, e); }
}
async function playEnd(it) {
  const inner = it.ns.length > 0;
  const isGroupTask = !inner && G.groups.has(it.node) && !G.groups.get(it.node).pseudo;
  const nid = isGroupTask ? null : nodeIdFor(it.ns, it.node);
  const key = isGroupTask ? 'g:' + it.node : nid;
  const rec = nodeRec(key);
  const lf = loopFactor(Math.max(0, rec.runs - 1));
  const el = performance.now() - rec.startedAt;
  const needed = (PACE.minRun * lf * (rec.fanId ? 0.25 : inner ? 0.5 : 1)) / SPEED;
  if (el < needed) await new Promise((r) => setTimeout(r, needed - el));
  rec.active = Math.max(0, rec.active - 1); rec.done += 1;
  const fanId = rec.fanId;
  if (fanId && V.fans[fanId]) {
    const idx = rec.chipOf.get(it.taskId);
    if (idx !== undefined) V.chipState(fanId, idx, 'done');
    const f = V.fans[fanId]; f.done++;
    V.fanFoot(fanId, f.revising ? `${f.done} / ${f.n} flagged desk${f.n > 1 ? 's' : ''} (revision)` : `${f.done} / ${f.n} ${isGroupTask ? 'desks' : 'tickers'}`, f.revising);
    LIVE.count[isGroupTask ? it.node : nid] = f.done;
    renderCards(STATE.values, false);
    await sleep(rec.runs <= 1 ? PACE.chip : PACE.chipLater);
  }
  if (it.error) { setStatus('red', 'ERROR'); return; }
  const mine = isGroupTask ? G.groups.get(it.node).members : [nid];
  STATE.lastDone = [...mine, ...STATE.lastDone.filter((x) => !mine.includes(x))].slice(0, 14);
  if (rec.active > 0) return;
  if (isGroupTask) { V.setGroup(it.node, 'done'); if (rec.runs === 1) V.edgeLabel('adversarial_debate_arbiter>strategy_desk:desk_quant_modeler', null); } else V.setNode(nid, 'done');
  if (!isGroupTask) pseudoBoxes(nid, 'end');
  mark('node-end', { id: key, runs: rec.runs, t: +now().toFixed(2) });
  if (!isGroupTask) annotate(nid, it.result);
  if (nid === 'ticker_deep_research_worker') {
    const eid = 'ticker_deep_research_worker>intelligence_aggregator';
    if (V.edges[eid]) { V.edgeLabel(eid, 'merge_dicts → ticker_dossiers', { dx: 118, dy: 138, size: 17 }); setTimeout(() => V.edgeLabel(eid, null), 4200 / SPEED); }
  }
  await sleep(PACE.endDwell * lf);
}
function ckptStep(it) {
  STATE.ckpts += 1;
  STATE.values = { ...STATE.values, ...it.values };
  $('ck').innerHTML = `<b>${STATE.ckpts}</b> checkpoints`;
  renderCards(STATE.values);
}
async function player(q) {
  for (;;) {
    const it = await q.next();
    if (!it) break;
    if (it.type === 'start') {
      await new Promise((r) => setTimeout(r, 160)); // let a whole burst of parallel task starts arrive, then animate them together
      const batch = [it];
      while (q.items.length && q.items[0].type === 'start') batch.push(q.items.shift());
      if (!STATE.debateHeld && batch.some((b) => !b.ns.length && NODE_STAGE[b.node] === 'debate')) { STATE.debateHeld = true; await sleep(2200); } // let the viewer read the reducer merge before the camera moves on
      await Promise.all(batch.map(playStart));
    } else if (it.type === 'end') await playEnd(it);
    else if (it.type === 'ckpt') { ckptStep(it); await sleep(60); }
    else if (it.type === 'error') { setStatus('red', 'ERROR'); window.__log.push({ t: +now().toFixed(2), kind: 'error', text: it.message }); }
    else if (it.type === 'finish') {
      const last = STATE.lastDone.find((s) => G.edges.some((e) => e.src === s && e.dst === '__end__'));
      const e = G.edges.find((x) => x.src === last && x.dst === '__end__');
      if (e) { V.setEdge(e.id, 'active'); await V.travel(e.id, PACE.edge * 0.8); V.setEdge(e.id, 'done'); }
      V.setNode('__end__', 'done');
      break;
    }
  }
}
function resetView() {
  V.reset();
  for (const g of G.groups.values()) V.setGroup(g.id, '');
  Object.assign(STATE, { thread: null, ckpts: 0, values: {}, runs: {}, lastDone: ['__start__'], debateHeld: false });
  Object.assign(LIVE, { count: {}, total: {} });
  if (verdictG) { verdictG.remove(); verdictG = null; }
  lightPill(null);
  window.__events.length = 0; window.__done = false;
  buildStrip(); $('ck').innerHTML = '<b>0</b> checkpoints';
}
async function runOnce() {
  const token = ++runToken;
  resetView();
  CAM.jump('overview');
  setStatus('grey', 'STARTING');
  await sleep(PACE.intro);
  const replay = MODE === 'replay' ? Q.get('file') : null;
  const thread = replay ? { thread_id: 'replay · ' + replay.split('/').pop() } : await jpost(`${API}/threads`, { metadata: { graph: GRAPH, source: 'viz' } });
  STATE.thread = thread.thread_id;
  $('tid').textContent = STATE.thread;
  setStatus('blue', 'RUNNING');
  mark('run-start', { thread: STATE.thread });
  const q = new Queue();
  const consumer = player(q);
  try {
    if (replay) await readReplay(replay, q, token);
    else await readRun(STATE.thread, { input: { messages: [{ type: 'human', content: PROMPT }], focus_tickers: TICKERS, revision_count: 0, max_revisions: 2 } }, q, token);
    if (token !== runToken) return;
    q.push({ type: 'finish' });
    q.close();
    await consumer;
    setStatus('green', 'DONE');
    mark('run-done', { checkpoints: STATE.ckpts });
    await sleep(PACE.hold / 2);
    await CAM.go('overview', 2200);
    await sleep(PACE.hold);
  } catch (e) {
    console.error(e);
    setStatus('red', 'ERROR');
    window.__error = String(e && e.message || e);
  }
  mark('run-end', { checkpoints: STATE.ckpts });
  window.__state = { thread: STATE.thread, checkpoints: STATE.ckpts };
  window.__done = true;
}

// ---------------------------------------------------------------------------------------------------- ?mode=tour (structure, nothing runs)
async function runTour() {
  const COMPACT = Q.get('compact') === '1', CALLOUTS = Q.get('callouts') !== '0';
  const co = (...args) => (CALLOUTS ? callout(...args) : { remove() {} });
  const D = COMPACT ? { stagger: 75, hold: 900, cam: 1100, syn: 2000, deb: 1700, desk: 2900, exe: 2000, end: 1500 } : { stagger: 150, hold: 1700, cam: 1800, syn: 6000, deb: 3400, desk: 7200, exe: 3600, end: 1800 };
  setStatus('grey', 'THE GRAPH'); $('gsub').textContent = 'LangGraph · structure';
  $('tid').textContent = 'not started';
  buildStrip(); CAM.jump('overview');
  const nodes = [...document.querySelectorAll('#g .node')], edges = [...document.querySelectorAll('#g .edge')], boxes = [...document.querySelectorAll('#g .group-box')];
  const tabs = [...document.querySelectorAll('#g g')].filter((g) => g.querySelector(':scope > rect[fill="#0f1626"]'));
  const hide = (list) => list.forEach((n) => { n.style.opacity = 0; n.style.transition = 'opacity .5s'; });
  hide(nodes); hide(edges); hide(boxes); hide(tabs);
  const sl = document.getElementById('static-labels'); sl.style.opacity = 0;
  await sleep(500);
  const order = ['__start__', 'cio_regime_router', ...G.ANALYSTS, G.WORKER, 'intelligence_aggregator', 'bull_thesis_advocate', 'bear_thesis_critic', 'adversarial_debate_arbiter',
    'strategy_desk:desk_quant_modeler', 'strategy_desk:desk_head_pm', 'factor_exposure_auditor', 'tail_var_monte_carlo', 'cro_risk_synthesis', 'head_of_trading', 'execution_microstructure_router', 'halt_no_trade', '__end__'];
  const idxOf = (id) => [...G.nodes.keys()].indexOf(id);
  for (const id of order) { const el = nodes[idxOf(id)]; if (el) el.style.opacity = 1; await sleep(D.stagger); }
  edges.forEach((e) => { e.style.opacity = 1; }); boxes.forEach((b) => { b.style.opacity = 1; }); tabs.forEach((t) => { t.style.opacity = 1; });
  sl.style.transition = 'opacity .6s'; sl.style.opacity = 1;
  mark('tour-revealed');
  await sleep(D.hold);
  // 1. syndicate
  await CAM.go('syndicate', D.cam);
  const c1 = co('fan-out', 'cio_regime_router starts 6 analysts + 1 Send() worker per ticker, all at once', { x: P('cio_regime_router').x + 120, y: 70 }, P('cio_regime_router', 'r'), { size: 24 });
  await sleep(D.syn * 0.45);
  const c2 = co('fan-in', 'intelligence_aggregator waits for every branch, reducers merge the results', { x: P('intelligence_aggregator').x - 30, y: 430 }, P('intelligence_aggregator', 'b'), { size: 24 });
  await sleep(D.syn * 0.55); c1.remove(); c2.remove();
  // 2. debate
  await CAM.go('debate', D.cam);
  const c3 = co('adversarial debate', 'bull and bear argue in parallel, the arbiter rules', { x: P('bull_thesis_advocate').x + 10, y: 120 }, P('bull_thesis_advocate', 't'), { size: 24 });
  await sleep(D.deb); c3.remove();
  // 3. desks + gate
  await CAM.go('desks', D.cam);
  const gb = G.groups.get('strategy_desk').box;
  const c4 = co('one subgraph, four desks', 'strategy_desk is compiled once and Send() runs it per desk', { x: gb.cx + 40, y: 620 }, { x: gb.cx, y: gb.y + 4 }, { size: 24 });
  await sleep(D.desk * 0.5);
  const cro = nodeBox('cro_risk_synthesis');
  const c5 = co('the gate', 'a deterministic engine, not the LLM, decides the verdict', { x: cro.cx - 40, y: 835 }, { x: cro.cx, y: cro.cy + cro.h / 2 + 6 }, { size: 24 });
  await sleep(D.desk * 0.5); c4.remove(); c5.remove();
  // 4. execution
  await CAM.go('execution', D.cam);
  await sleep(D.exe);
  await CAM.go('overview', D.cam + 200);
  await sleep(D.end);
  window.__done = true;
}

// ---------------------------------------------------------------------------------------------------- ?mode=fail (the REJECTED branch, drawn as an illustration)
async function runFail() {
  setStatus('grey', 'ILLUSTRATION'); $('gsub').textContent = 'LangGraph · illustration';
  $('tid').textContent = 'illustration · no run';
  buildStrip(); CAM.jump('overview');
  await sleep(600);
  await CAM.go('desks', 1400);
  for (const id of ['strategy_desk:desk_quant_modeler', 'strategy_desk:desk_head_pm', 'factor_exposure_auditor', 'tail_var_monte_carlo']) V.setNode(id, 'done');
  V.setNode('cro_risk_synthesis', 'running');
  const rc = document.querySelector('.card[data-k="revision_count"]');
  rc.querySelector('.cv').textContent = '2 of 2'; rc.querySelector('.cv').classList.remove('dim'); rc.classList.add('hi');
  const ra = document.querySelector('.card[data-k="risk_assessment"]');
  await sleep(1300);
  const cro = nodeBox('cro_risk_synthesis'), halt = nodeBox('halt_no_trade');
  const c1 = callout('revision budget spent', '2 of 2 rounds used and the engine still reports breaches', { x: cro.cx - 360, y: cro.cy - 150 }, { x: cro.cx - 40, y: cro.cy - 24 }, { size: 24 });
  await sleep(2600);
  V.setNode('cro_risk_synthesis', 'done');
  verdictCard('REJECTED', ['breaches still open after the last round', 'fail closed: never approve by default']);
  lightPill('REJECTED');
  ra.querySelector('.cv').textContent = 'REJECTED'; ra.querySelector('.cv').classList.remove('dim'); ra.classList.add('hi');
  const e1 = V.edges['cro_risk_synthesis>halt_no_trade'];
  V.setEdge('cro_risk_synthesis>halt_no_trade', 'active');
  await V.travel('cro_risk_synthesis>halt_no_trade', 1100);
  V.setEdge('cro_risk_synthesis>halt_no_trade', 'done'); void e1;
  V.setNode('halt_no_trade', 'interrupted');
  await sleep(1500);
  c1.remove(); if (verdictG) { verdictG.remove(); verdictG = null; }
  await CAM.go('execution', 1500);
  V.setEdge('halt_no_trade>__end__', 'active');
  await V.travel('halt_no_trade>__end__', 1500);
  V.setEdge('halt_no_trade>__end__', 'done');
  V.setNode('__end__', 'done');
  const en = nodeBox('__end__');
  callout('no trade', 'the run ends here, nothing reaches the head of trading', { x: en.cx - 230, y: en.cy - 130 }, { x: en.cx, y: en.cy - 22 }, { size: 24 });
  await sleep(1700);
  callout('this branch was not taken in the recorded run', 'it is covered by the offline test suite', { x: halt.cx + 430, y: halt.cy + 92 }, null, { size: 22 });
  await sleep(3300);
  window.__done = true;
}

// ---------------------------------------------------------------------------------------------------- ?mode=state (who writes which channel)
async function runState() {
  setStatus('grey', 'SHARED STATE'); $('gsub').textContent = 'LangGraph · shared state';
  $('tid').textContent = 'not started';
  buildStrip(); CAM.jump('overview');
  await sleep(1400);
  const over = $('over');
  over.innerHTML = '';
  const say = (text, sub) => {
    over.innerHTML = '';
    const d = document.createElement('div');
    d.style.cssText = 'position:absolute;left:24px;right:24px;top:640px;height:64px;border-radius:14px;background:linear-gradient(90deg,#172033,#121a2b);border:2px solid #2b364f;border-left:8px solid #f4c95d;display:flex;align-items:center;padding:0 24px;animation:coin .45s ease both';
    d.innerHTML = `<div style="font-size:25px;font-weight:700;color:#eef2fa"><span style="color:#f4c95d;font-weight:800">${text}</span> ${sub || ''}</div>`;
    over.appendChild(d);
    window.__log.push({ t: +now().toFixed(2), kind: 'caption', text: `${text} ${sub || ''}`.replace(/<[^>]+>/g, '') });
  };
  void say;
  const cards = [...document.querySelectorAll('.card')];
  const lightAll = (on) => cards.forEach((c) => c.classList.toggle('hi', on));
  lightAll(true); mark('state', { phase: 'all-channels' });
  await sleep(2400); lightAll(false);
  for (const c of SC.channels) {
    const card = document.querySelector(`.card[data-k="${CSS.escape(c.k)}"]`);
    card.classList.add('hi');
    const ws = WRITERS[c.k] || [];
    ws.forEach((w) => V.setNode(w, 'running'));
    const stage = NODE_STAGE[ws[0]] || 'overview';
    CAM.go(stage, 1500);
    mark('state', { channel: c.k, writers: ws });
    window.__log.push({ t: +now().toFixed(2), kind: 'channel', channel: c.k, writers: ws });
    await sleep(c.tag ? 3000 : 1800);
    ws.forEach((w) => V.setNode(w, 'idle'));
    card.classList.remove('hi');
  }
  await CAM.go('overview', 1600);
  lightAll(true);
  await sleep(2200);
  window.__done = true;
}

// ---------------------------------------------------------------------------------------------------- boot
(async function boot() {
  buildChrome();
  try {
    const as = await loadAssistants();
    A = as[GRAPH];
    if (!A) throw new Error('no such graph: ' + GRAPH);
    G = layoutDesk(await loadGraph(A.id));
    V = createView($('g'), G, { groupSub: SC.groupSub, edgeW: 3.2, tabLeft: ['triad_risk_committee'] });
    window.__G = G; window.__V = V;
    staticLabels();
    buildStrip();
    CAM.jump('overview');
    if (MODE === 'norun') { setStatus('grey', 'LAYOUT ONLY'); const st = Q.get('stage'); if (st) CAM.jump(st); window.__done = true; return; }
    if (MODE === 'tour') { await runTour(); return; }
    if (MODE === 'fail') { await runFail(); return; }
    if (MODE === 'state') { await runState(); return; }
    await runOnce();
  } catch (e) {
    console.error(e);
    setStatus('red', 'ERROR');
    window.__error = String(e && e.message || e);
    window.__done = true;
  }
})();
