// desk_layout.js: hand-placed layout for the trading_desk graph (22 nodes do not fit the automatic layouter's single column).
// Structure still comes from the LangGraph API (normalizeGraph on GET /assistants/{id}/graph?xray=true); only coordinates and
// edge routes are decided here. Produces the same G shape lg.js createView() expects, on a big virtual canvas that the page's
// camera (SVG viewBox) pans and zooms over, so text stays vector-crisp at any zoom.
import { normalizeGraph, textW } from './lg.js';

const F = 19, PADX = 18, NH = 40; // node font / horizontal padding / height (virtual px)

export function layoutDesk(raw) {
  const G = normalizeGraph(raw, { id: 'trading_desk' });
  const N = (id) => { const n = G.nodes.get(id); if (!n) throw new Error('layout: missing node ' + id); return n; };
  for (const n of G.nodes.values()) {
    if (n.kind === 'node') { n.lines = [n.label]; n.h = NH; n.w = Math.ceil(textW(n.label, F, 650) + 2 * PADX); }
    else { n.h = 38; n.w = n.kind === 'start' ? 92 : 80; }
  }
  const uni = (ids) => { const w = Math.max(...ids.map((i) => N(i).w)); ids.forEach((i) => { N(i).w = w; }); return w; };
  const R = (n) => n.cx + n.w / 2, L = (n) => n.cx - n.w / 2, T = (n) => n.cy - n.h / 2, B = (n) => n.cy + n.h / 2;
  const at = (id, left, cy) => { const n = N(id); n.cx = left + n.w / 2; n.cy = cy; n.row = cy < 480 ? 0 : 1; return n; };

  const ANALYSTS = ['premarket_microstructure', 'technical_analyst', 'macro_strategist', 'news_sentiment', 'vol_surface_analyst', 'factor_cointegration_analyst'];
  const WORKER = 'ticker_deep_research_worker';
  const STACK = [...ANALYSTS, WORKER];
  const R1 = 262, R2 = 704;

  // ---- row 1: START -> cio -> [6 analysts + Send workers] -> aggregator -> bull / bear -> arbiter
  N('cio_regime_router').h = 60; N('intelligence_aggregator').h = 88;
  uni(STACK); uni(['bull_thesis_advocate', 'bear_thesis_critic']); uni(['factor_exposure_auditor', 'tail_var_monte_carlo']);
  at('__start__', 24, R1);
  at('cio_regime_router', R(N('__start__')) + 56, R1);
  const sx = R(N('cio_regime_router')) + 130;
  ANALYSTS.forEach((id, i) => at(id, sx, 100 + i * 52));
  at(WORKER, sx, 100 + 5 * 52 + 70);
  at('intelligence_aggregator', sx + N(ANALYSTS[0]).w + 130, R1);
  const bx = R(N('intelligence_aggregator')) + 120;
  at('bull_thesis_advocate', bx, R1 - 50);
  at('bear_thesis_critic', bx, R1 + 50);
  at('adversarial_debate_arbiter', R(N('bull_thesis_advocate')) + 120, R1);

  // ---- row 2: strategy_desk subgraph -> auditors -> cro -> head_of_trading -> exec -> END, halt below cro
  const quant = at('strategy_desk:desk_quant_modeler', 150, R2);
  const pm = at('strategy_desk:desk_head_pm', R(quant) + 74, R2);
  const audX = R(pm) + 24 + 116;
  at('factor_exposure_auditor', audX, R2 - 34);
  at('tail_var_monte_carlo', audX, R2 + 34);
  at('cro_risk_synthesis', audX + N('factor_exposure_auditor').w + 100, R2);
  at('head_of_trading', R(N('cro_risk_synthesis')) + 110, R2);
  at('execution_microstructure_router', R(N('head_of_trading')) + 84, R2);
  at('__end__', R(N('execution_microstructure_router')) + 60, R2);
  const cro = N('cro_risk_synthesis');
  const halt = at('halt_no_trade', cro.cx - N('halt_no_trade').w / 2, R2 + 132);
  const W = Math.ceil(R(N('__end__')) + 28), H = 880;

  // ---- groups: the real subgraph + two purely visual boxes (the Intelligence Syndicate, the Triad Risk Committee)
  const addBox = (id, members, o = {}) => {
    let g = G.groups.get(id);
    if (!g) { g = { id, members, startId: null, endId: null, pseudo: true }; G.groups.set(id, g); }
    const ms = members.map(N);
    const pad = o.padX ?? 24, top = o.top ?? 46, bot = o.bottom ?? 22;
    const x1 = Math.min(...ms.map(L)) - pad, x2 = Math.max(...ms.map(R)) + pad;
    const y1 = Math.min(...ms.map(T)) - top, y2 = Math.max(...ms.map(B)) + bot;
    g.box = { x: x1, y: y1, w: x2 - x1, h: y2 - y1, cx: (x1 + x2) / 2, cy: (y1 + y2) / 2, row: ms[0].row };
    return g;
  };
  const desk = addBox('strategy_desk', ['strategy_desk:desk_quant_modeler', 'strategy_desk:desk_head_pm']);
  addBox('intelligence_syndicate', ANALYSTS, { top: 44, bottom: 18 });
  addBox('triad_risk_committee', ['factor_exposure_auditor', 'tail_var_monte_carlo', 'cro_risk_synthesis'], { top: 46, bottom: 22 });

  // ---- edge routing (explicit; ports are spread along a node side so parallel edges do not pile up)
  const GAP = 5;
  const hb = (x1, y1, x2, y2) => { const xm = (x1 + x2) / 2; return Math.abs(y1 - y2) < 0.5 ? `M${x1} ${y1} L${x2 - GAP} ${y2}` : `M${x1} ${y1} C${xm} ${y1} ${xm} ${y2} ${x2 - GAP} ${y2}`; };
  const spread = (list, n, h) => { const k = list.length; return k > 1 ? (list.indexOf(n) - (k - 1) / 2) * Math.min(11, (h - 14) / (k - 1)) : 0; };
  const outs = new Map(), ins = new Map();
  for (const e of G.edges) { (outs.get(e.src) || outs.set(e.src, []).get(e.src)).push(e.dst); (ins.get(e.dst) || ins.set(e.dst, []).get(e.dst)).push(e.src); }
  const round = (pts) => pts; void round;
  const elbowDown = (x1, y1, yc, x2, y2, rad = 18) => {
    const dir = x2 < x1 ? -1 : 1;
    return `M${x1} ${y1} L${x1} ${yc - rad} Q${x1} ${yc} ${x1 + dir * rad} ${yc} L${x2 - dir * rad} ${yc} Q${x2} ${yc} ${x2} ${yc + rad} L${x2} ${y2 - GAP}`;
  };
  for (const e of G.edges) {
    const A = N(e.src), Bn = N(e.dst);
    let d, mid, type = 'h';
    const key = `${e.src}>${e.dst}`;
    if (key === 'adversarial_debate_arbiter>strategy_desk:desk_quant_modeler') {
      const yc = 530;
      d = elbowDown(A.cx, B(A), yc, quant.cx, desk.box.y);
      mid = { x: (A.cx + quant.cx) / 2, y: yc };
    } else if (key === 'cro_risk_synthesis>strategy_desk:desk_quant_modeler') {
      const x1 = cro.cx - 34, yc = 566, x2 = pm.cx;
      d = elbowDown(x1, T(cro), yc, x2, desk.box.y);
      mid = { x: (x1 + x2) / 2, y: yc };
      e.back = true;
    } else if (key === 'cro_risk_synthesis>halt_no_trade') {
      d = `M${cro.cx} ${B(cro)} L${cro.cx} ${T(halt) - GAP}`; mid = { x: cro.cx, y: (B(cro) + T(halt)) / 2 }; type = 'v';
    } else if (key === 'halt_no_trade>__end__') {
      const en = N('__end__'); const x1 = R(halt), y1 = halt.cy;
      d = `M${x1} ${y1} C${x1 + 360} ${y1} ${en.cx} ${y1 - 30} ${en.cx} ${B(en) + GAP}`; mid = { x: (x1 + en.cx) / 2, y: y1 - 6 };
    } else if (key === 'strategy_desk:desk_quant_modeler>strategy_desk:desk_head_pm') {
      d = `M${R(quant)} ${quant.cy} L${L(pm) - GAP} ${pm.cy}`; mid = { x: (R(quant) + L(pm)) / 2, y: quant.cy };
    } else {
      const x1 = R(A), y1 = A.cy + spread(outs.get(e.src), e.dst, A.h);
      const x2 = L(Bn), y2 = Bn.cy + spread(ins.get(e.dst), e.src, Bn.h);
      d = hb(x1, y1, x2, y2); mid = { x: (x1 + x2) / 2, y: (y1 + y2) / 2 };
    }
    e.d = d; e.mid = mid; e.type = type;
  }

  G.size = { W, H, overflow: 0 };
  G.opt = { font: F, termFont: 17, chip: 20, chipGap: 5, arrow: 16, nodeStroke: 2.8, tabH: 30, groupFont: 17, edgeW: 3.2 };
  G.rowsSpec = [1, 1];

  // ---- camera stages (virtual-canvas rectangles the camera can fit)
  const bbox = (ids, pad = 40, ext = {}) => {
    const ms = ids.map(N);
    return { x0: Math.min(...ms.map(L)) - pad - (ext.l || 0), y0: Math.min(...ms.map(T)) - pad - (ext.t || 0), x1: Math.max(...ms.map(R)) + pad + (ext.r || 0), y1: Math.max(...ms.map(B)) + pad + (ext.b || 0) };
  };
  const gb = desk.box, wk = N(WORKER), agg = N('intelligence_aggregator'), arb = N('adversarial_debate_arbiter'), st = N('__start__');
  G.stages = {
    overview: { x0: 0, y0: 0, x1: W, y1: H },
    syndicate: { x0: st.cx - st.w / 2 - 30, x1: R(agg) + 70, y0: 0, y1: B(wk) + 96 },
    debate: { x0: L(agg) - 40, x1: R(arb) + 50, y0: 100, y1: 440 },
    desks: { x0: L(quant) - 90, x1: R(cro) + 160, y0: 496, y1: halt.cy + 56 },
    execution: { x0: L(cro) - 80, x1: W + 10, y0: T(cro) - 120, y1: B(halt) + 50 },
    top: { x0: 0, y0: 0, x1: R(arb) + 40, y1: 600 },
  };
  void gb;
  G.ANALYSTS = ANALYSTS; G.WORKER = WORKER;
  return G;
}
