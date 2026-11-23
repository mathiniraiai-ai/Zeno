"""The admin Usage page: a React page of who edited what, when, and what Gemini cost.

Every editor and every run, with the editor's e-mail: coding agent, model, tokens in / cached / out, Gemini transcription tokens, video
length and an estimated cost. The page holds no data. It asks for a company Google sign-in (any account of the allowed domain, no admin
list) and then reads the read-only `/api/usage`, which returns numbers only (no plan files, no keys).

No build step: React 18.3.1 comes pinned from cdnjs and the components are written with `React.createElement` (`h`). The calculations sit
before the `/*__DOM__*/` marker so tests can run them in Node without a browser. The cost is an estimate from list prices (USD per million
tokens, PRICES below); models not listed, such as Gemini or Antigravity models, are shown without a cost instead of with a guess.
"""
NOTION_STYLE = """
*{box-sizing:border-box}
body{margin:0;background:#191919;color:#ebebeb;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:none;width:100%;margin:0;padding:36px 48px 80px}
h1{margin:0 0 4px;font-size:30px;font-weight:700;letter-spacing:-.4px}
h2{margin:34px 0 6px;font-size:16px;font-weight:600}
a{color:#7fb2ff}
p.sub,.sub{margin:0 0 10px;color:#9b9b9b;font-size:14px}
.note{color:#9b9b9b;font-size:13px;margin:0 0 8px}
.muted,td.muted{color:#9b9b9b}
td.muted{white-space:nowrap}
.top{display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin:0 0 26px;color:#9b9b9b;font-size:13px}
.top .sp{flex:1}
.brand{font-weight:600;color:#ebebeb}
button,input{font:inherit;color:inherit}
button{cursor:pointer}
.tabs{display:flex;flex-wrap:nowrap;gap:2px;margin:0 0 14px;border-bottom:1px solid #2f2f2f;overflow-x:auto;overflow-y:hidden;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tab{flex:none;white-space:nowrap;background:none;border:0;border-bottom:2px solid transparent;margin-bottom:-1px;padding:7px 12px;color:#9b9b9b;font-size:14px}
.tab:hover{color:#ebebeb}
.tab.on{color:#ebebeb;border-bottom-color:#ebebeb}
.bar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:0 0 6px}
.chip{background:none;border:0;border-radius:4px;padding:4px 10px;color:#9b9b9b;font-size:13px;text-decoration:none}
.chip:hover{background:#2a2a2a;color:#ebebeb}
.chip.on{background:#2f2f2f;color:#ebebeb}
.bar input{background:transparent;border:1px solid #2f2f2f;border-radius:4px;padding:4px 10px;font-size:13px;outline:none}
.bar input:focus{border-color:#5a5a5a}
.bar input[type=number]{width:70px}
.bar label{color:#9b9b9b;font-size:13px;display:flex;align-items:center;gap:6px;margin:0}
.grow{flex:1;min-width:200px}
.card{margin:0 0 6px}
.scroll{overflow-x:auto;scrollbar-width:thin;scrollbar-color:#3a3a3a transparent;padding-bottom:6px}
.scroll::-webkit-scrollbar{height:8px}.scroll::-webkit-scrollbar-thumb{background:#3a3a3a;border-radius:4px}.scroll::-webkit-scrollbar-track{background:transparent}
table{width:max-content;min-width:100%;border-collapse:collapse;font-size:14px}
th,td{white-space:nowrap}
th:first-child,td:first-child{position:sticky;left:0;background:#191919;z-index:1}
tbody tr:hover td:first-child{background:#202020}
th{text-align:left;font-weight:400;color:#9b9b9b;font-size:12.5px;padding:6px 10px;border-bottom:1px solid #2f2f2f}
td{padding:7px 10px;border-bottom:1px solid #2f2f2f;vertical-align:top}
tbody tr:hover td{background:#202020}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.mail{white-space:nowrap;color:#9b9b9b}
tr.total td{font-weight:600}
.tag{display:inline-block;background:#2a2a2a;border-radius:3px;padding:0 6px;margin:1px 4px 1px 0;font-size:12.5px;white-space:nowrap}
.pill{display:inline-block;border-radius:3px;padding:0 6px;font-size:12.5px;white-space:nowrap}
.pill.ok{background:rgba(70,150,100,.28);color:#8fd6ac}
.pill.amber{background:rgba(205,150,40,.26);color:#e9c46a}
.pill.red{background:rgba(210,80,70,.28);color:#f19a93}
.pill.gray{background:#2a2a2a;color:#9b9b9b}
.stats{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 12px}
.stat{border:1px solid #2f2f2f;border-radius:4px;padding:8px 14px;min-width:150px}
.stat b{display:block;font-size:20px;font-weight:600}
.stat span{color:#9b9b9b;font-size:12.5px}
.foot{color:#7a7a7a;font-size:12.5px;margin:30px 0 0}
.signin{padding:40px 0}
.signin h1{font-size:22px}
.dayhead{margin:26px 0 4px;font-size:15px}
.gantt{margin:6px 0 4px}
.grow2{display:grid;grid-template-columns:170px 1fr;align-items:center;gap:10px;margin:0}
.glabel{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600;padding:5px 0}
.gtrack{position:relative;height:30px;border-bottom:1px solid #2f2f2f;background:repeating-linear-gradient(90deg,transparent 0,transparent calc(var(--hw) - 1px),#262626 calc(var(--hw) - 1px),#262626 var(--hw))}
.gb{position:absolute;top:6px;height:18px;min-width:4px;border-radius:3px;opacity:.95}
.gb.long{top:12px;height:5px;opacity:.5}
.gb.good{background:#3d9a6b}.gb.rework{background:#d9a441}.gb.bad{background:#d4574b}.gb.open{background:#6b7480}
.gaxis{display:grid;grid-template-columns:170px 1fr;gap:10px;color:#9b9b9b;font-size:12px}
.gticks{position:relative;height:18px}.gticks span{position:absolute;transform:translateX(-50%)}
.legend{display:flex;flex-wrap:wrap;gap:16px;margin:10px 0 0;color:#9b9b9b;font-size:12.5px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.rw{color:#e9c46a;font-size:12px;margin-left:6px}
.dt{background:transparent;border:1px solid #2f2f2f;border-radius:4px;padding:3px 8px;color:inherit;font:inherit;font-size:13px;color-scheme:dark}
select.dt{padding:4px 8px}
.pill.ok2{background:rgba(70,150,100,.28);color:#8fd6ac}

main{max-width:none;margin:0;padding:24px 28px 60px}
.controls{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:center;margin:0 0 16px;padding:10px 14px;background:#202020;border:1px solid #2f2f2f;border-radius:8px}
.controls .bar{margin:0}
.dash{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:14px;align-items:stretch}
.c{background:#202020;border:1px solid #2f2f2f;border-radius:8px;padding:16px 18px;min-width:0}
.c h3{margin:0;font-size:13.5px;font-weight:600;color:#cfcfcf}
.s2{grid-column:span 2}.s3{grid-column:span 3}.s4{grid-column:span 4}.s6{grid-column:span 6}.s8{grid-column:span 8}.s12{grid-column:span 12}
.kpi{display:flex;flex-direction:column;justify-content:center;padding:18px}
.kpi b{font-size:32px;font-weight:700;line-height:1.1;letter-spacing:-.5px}
.kpi span{color:#9b9b9b;font-size:13px;margin-top:6px}
.sechead{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px;margin:0 0 12px}
.note{color:#9b9b9b;font-size:12.5px;margin:-6px 0 10px}
.seg{display:flex;gap:2px}
.chart{width:100%;max-height:300px;display:block}
.pad{padding:14px 0;margin:0}
.blist{display:flex;flex-direction:column;gap:9px}
.brow{display:grid;grid-template-columns:minmax(80px,32%) 1fr auto;align-items:center;gap:10px;font-size:13.5px}
.bname{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.btrack{height:10px;background:#2a2a2a;border-radius:5px;overflow:hidden}
.bfill{height:100%;border-radius:5px}
.bval{color:#9b9b9b;font-variant-numeric:tabular-nums}
table.grid td.cell{min-width:46px;text-align:center}
td.cell.sel{outline:1px solid #7fb2ff;outline-offset:-1px}
button:disabled{opacity:.5;cursor:default}
.dt[type=number]{width:84px}
.chartbox{position:relative}
.tip{position:absolute;z-index:6;background:#111;border:1px solid #3a3a3a;border-radius:8px;padding:9px 12px;font-size:12.5px;pointer-events:none;min-width:190px;max-width:360px;box-shadow:0 8px 24px rgba(0,0,0,.45)}
.tiptitle{font-weight:600;margin:0 0 6px;color:#ebebeb}
.tiprow{margin:0 0 4px}
.tipline{display:flex;align-items:center;gap:7px}
.tipline i{width:9px;height:9px;border-radius:2px;flex:none}
.tipline span{flex:1;color:#cfcfcf}
.tipline b{font-variant-numeric:tabular-nums}
.tipsub{color:#9b9b9b;font-size:12px;margin:1px 0 0 16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tiptotal{border-top:1px solid #2f2f2f;margin-top:6px;padding-top:6px}
.legend + .note{margin-top:8px}
.minis{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 12px}
.mini{border:1px solid #2f2f2f;border-radius:6px;padding:6px 12px;min-width:120px;background:#1c1c1c}
.mini b{display:block;font-size:17px;font-weight:600}
.mini span{color:#9b9b9b;font-size:12px}
.legacy h1{font-size:15px;font-weight:600;margin:0 0 6px;letter-spacing:0}
.legacy h2{font-size:14px;margin:18px 0 6px}
.legacy .stats{margin:6px 0 12px}
.c th:first-child,.c td:first-child{background:#202020}
.c tbody tr:hover td:first-child{background:#262626}
@media(max-width:1000px){.s2,.s3,.s4{grid-column:span 6}.s6,.s8{grid-column:span 12}}
@media(max-width:560px){.s2,.s3,.s4{grid-column:span 12}}
@media(max-width:600px){main{padding:24px 14px 60px}h1{font-size:24px}}
"""

USAGE_PAGE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zeno · Usage</title><style>__STYLE__</style></head><body>
<div id="root"><main><h1>Usage</h1><p class="sub">Loading…</p></main></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/react/18.3.1/umd/react.production.min.js" crossorigin="anonymous"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/react-dom/18.3.1/umd/react-dom.production.min.js" crossorigin="anonymous"></script>
<script>
const {useState, useEffect, useRef, useMemo} = React;
const h = React.createElement;
const TOKEN_KEY = "zeno_usage_session";
const PRICE_KEY = "zeno_gemini_prices_v2";
const COLORS = ["#7fb2ff", "#3d9a6b", "#d9a441", "#d4574b", "#b48ead", "#5fc3c8", "#e08e45", "#9aa5b1"];
const MODELS_IN_USE = ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-2.5-flash"];  // keep in step with GEMINI_MODELS on the server
// USD per million tokens, audio-input rate (Zeno sends audio) and output rate, from Google's Gemini API price list (23 Nov 2026). Editable on the Gemini page.
const DEFAULT_PRICES = {"gemini-3.1-flash-lite": {i: 0.5, o: 1.5}, "gemini-3.5-flash-lite": {i: 0.3, o: 2.5}, "gemini-2.5-flash": {i: 1, o: 2.5}};
const TABS = [["overview", "Overview"], ["videos", "Videos"], ["renders", "Renders"], ["gemini", "Gemini"], ["tokens", "Tokens and cost"], ["editors", "Editors"], ["health", "Health"]];

const fmtNum = x => n(x).toLocaleString();
const fmtBig = x => { x = n(x); return x >= 1e9 ? (x / 1e9).toFixed(2) + "B" : x >= 1e6 ? (x / 1e6).toFixed(1) + "M" : x >= 1e4 ? Math.round(x / 1e3) + "k" : fmtNum(x); };
const pad2 = x => String(x).padStart(2, "0");
const keyOf = d => d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
const parseKey = k => new Date(k + "T12:00:00");
const addDays = (k, d) => { const x = parseKey(k); x.setDate(x.getDate() + d); return keyOf(x); };
const daysBetween = (a, b) => { const out = []; let k = a, g = 0; while (k <= b && g++ < 400) { out.push(k); k = addDays(k, 1); } return out; };
const dayShort = k => parseKey(k).toLocaleDateString("en-GB", {weekday: "short", day: "numeric", month: "short"});
const dayTiny = k => parseKey(k).toLocaleDateString("en-GB", {day: "numeric", month: "short"});
const hour12 = hr => (hr % 12 || 12) + (hr < 12 ? " AM" : " PM");
const clock = t => { const d = new Date(n(t) * 1000); return (d.getHours() % 12 || 12) + ":" + pad2(d.getMinutes()) + " " + (d.getHours() < 12 ? "AM" : "PM"); };
const clock12 = d => { const h12 = d.getHours() % 12 || 12; return h12 + ":" + pad2(d.getMinutes()) + " " + (d.getHours() < 12 ? "AM" : "PM"); };
const fmtDate = t => { if (!t) return ""; const d = new Date(t * 1000); return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) + " " + clock12(d); };
const inr = (usd, rate) => "₹" + Math.round(usd * rate).toLocaleString();
const fmtWhen = t => t ? dayShort(dayKey(t)) + ", " + clock(t) : "";
const rupees = (usd, rate) => { const v = n(usd) * n(rate); return "₹" + (v >= 100 ? Math.round(v).toLocaleString() : v.toFixed(2)); };
const canon = s => String(s || "unknown").trim().toLowerCase();
const nice = s => String(s || "").split(" ").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
const videoName = v => String(v || "").replace(/^(reel_|SQ_)/i, "").replace(/[.]mp4$/i, "").replace(/ *[(][0-9]+[)] *$/, "").trim();
const short = (s, k) => { s = String(s || ""); k = k || 40; return s.length > k ? s.slice(0, k - 1) + "…" : s; };
const niceMax = v => { const p = Math.pow(10, Math.floor(Math.log10(Math.max(v, 1e-9)))); const m = v / p; return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p; };
const loadPrices = () => { let saved = {}; try { saved = JSON.parse(localStorage.getItem(PRICE_KEY) || "{}") || {}; } catch (e) { saved = {}; } const out = {}; MODELS_IN_USE.forEach(m => { out[m] = Object.assign({}, DEFAULT_PRICES[m], saved[m] || {}); }); return out; };

function wavePath(pts, top, base) {  // a smooth curve through the points, so the ups and downs read as a wave
  if (pts.length < 2) return "";
  const clamp = y => Math.max(top, Math.min(base, y));
  let d = "M" + pts[0][0].toFixed(1) + "," + pts[0][1].toFixed(1);
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
    d += " C" + (p1[0] + (p2[0] - p0[0]) / 6).toFixed(1) + "," + clamp(p1[1] + (p2[1] - p0[1]) / 6).toFixed(1) + " " +
      (p2[0] - (p3[0] - p1[0]) / 6).toFixed(1) + "," + clamp(p2[1] - (p3[1] - p1[1]) / 6).toFixed(1) + " " + p2[0].toFixed(1) + "," + p2[1].toFixed(1);
  }
  return d;
}

// ---------- small pieces ----------
const Pill = ({kind, children}) => h("span", {className: "pill " + (kind || "gray")}, children);
const Th = ({children, right}) => h("th", {className: right ? "n" : ""}, children);
function Stat({value, label, span}) { return h("div", {className: "c kpi s" + (span || 2)}, h("b", null, value), h("span", null, label)); }
function BarList({items, color, empty}) {  // horizontal bars, biggest first
  const max = Math.max(1, ...items.map(i => i.value));
  return items.length ? h("div", {className: "blist"}, items.map((i, k) => h("div", {key: i.name, className: "brow"},
    h("div", {className: "bname"}, i.name), h("div", {className: "btrack"}, h("div", {className: "bfill", style: {width: Math.max(2, 100 * i.value / max) + "%", background: i.color || color || COLORS[k % COLORS.length]}})), h("div", {className: "bval"}, i.label)))) : h(Empty, {text: empty});
}
function Section({title, sub, right, span, children}) {
  return h("section", {className: "c s" + (span || 12)},
    h("div", {className: "sechead"}, h("h3", null, title), right ? h("div", {className: "bar"}, right) : null),
    sub ? h("p", {className: "note"}, sub) : null,
    children);
}
function Seg({value, onChange, options}) {
  return h("div", {className: "seg"}, options.map(([id, label]) => h("button", {key: id, className: "chip" + (value === id ? " on" : ""), onClick: () => onChange(id)}, label)));
}
const Empty = ({text}) => h("p", {className: "muted pad"}, text || "Nothing recorded in this range.");

// ---------- the chart: a wave (smooth curves) or bars ----------
function Chart({series, labels, kind, fmtY, height, onPick, tips, tipRows}) {
  const [hov, setHov] = useState(null);
  const box = useRef(null);
  const W = 960, H = height || 250, L = 52, R = 14, T = 16, B = 30;
  const count = labels.length;
  const sums = labels.map((_, i) => series.reduce((t, s) => t + n(s.vals[i]), 0));
  const rawMax = kind === "bars" ? Math.max(0, ...sums) : Math.max(0, ...series.flatMap(s => s.vals.map(n)));
  if (!count || rawMax <= 0) return h(Empty, {text: "No data in this range."});
  const top = niceMax(rawMax);
  const slotW = (W - L - R) / count;
  const x = i => kind === "bars" ? L + slotW * (i + 0.5) : L + (count < 2 ? (W - L - R) / 2 : i * (W - L - R) / (count - 1));
  const y = v => T + (1 - n(v) / top) * (H - T - B);
  const every = Math.max(1, Math.ceil(count / 10));
  const fy = fmtY || fmtNum;
  const move = (e, i) => { const r = box.current ? box.current.getBoundingClientRect() : {left: 0, top: 0}; setHov({i, px: e.clientX - r.left, py: e.clientY - r.top}); };
  const parts = [];
  [0, 0.25, 0.5, 0.75, 1].forEach(f => parts.push(h("g", {key: "g" + f},
    h("line", {x1: L, x2: W - R, y1: y(top * f), y2: y(top * f), stroke: "#2f2f2f"}),
    h("text", {x: L - 8, y: y(top * f) + 4, fill: "#9b9b9b", fontSize: 11, textAnchor: "end"}, fy(top * f)))));
  if (kind === "bars") {
    const bw = Math.min(slotW * 0.68, 46);
    labels.forEach((lab, i) => {
      let acc = 0;
      series.forEach(s => {
        const v = n(s.vals[i]); if (v <= 0) return;
        const y0 = y(acc + v), y1 = y(acc); acc += v;
        parts.push(h("rect", {key: "b" + i + s.name, x: x(i) - bw / 2, y: y0, width: bw, height: Math.max(1, y1 - y0), fill: s.color, rx: 3, style: onPick ? {cursor: "pointer"} : null, onClick: onPick ? () => onPick(i) : undefined}));
      });
      if (count <= 31 && sums[i] > 0) parts.push(h("text", {key: "v" + i, x: x(i), y: y(sums[i]) - 6, fill: "#cfcfcf", fontSize: 11, textAnchor: "middle"}, fy(sums[i])));
    });
  } else {
    series.forEach((s, si) => {
      const pts = s.vals.map((v, i) => [x(i), y(v)]);
      const path = wavePath(pts, T, H - B);
      if (!path) return;
      if (series.length === 1 || si === 0 && s.fill) {
        parts.push(h("defs", {key: "d" + si}, h("linearGradient", {id: "wf" + si, x1: 0, y1: 0, x2: 0, y2: 1}, h("stop", {offset: "0%", stopColor: s.color, stopOpacity: 0.5}), h("stop", {offset: "100%", stopColor: s.color, stopOpacity: 0.03}))));
        parts.push(h("path", {key: "a" + si, d: path + " L" + x(count - 1).toFixed(1) + "," + (H - B) + " L" + x(0).toFixed(1) + "," + (H - B) + " Z", fill: "url(#wf" + si + ")"}));
      }
      parts.push(h("path", {key: "l" + si, d: path, fill: "none", stroke: s.color, strokeWidth: series.length > 1 ? 2 : 2.6, strokeLinecap: "round"}));
      if (count <= 45) s.vals.forEach((v, i) => parts.push(h("circle", {key: "c" + si + "-" + i, cx: x(i), cy: y(v), r: 3.2, fill: s.color, stroke: "#191919", strokeWidth: 1.5}, h("title", null, labels[i] + " · " + s.name + " · " + fy(v)))));
    });
  }
  labels.forEach((lab, i) => { if (i % every === 0) parts.push(h("text", {key: "x" + i, x: x(i), y: H - 9, fill: "#9b9b9b", fontSize: 11, textAnchor: "middle"}, lab)); });
  labels.forEach((lab, i) => {
    parts.push(h("rect", {key: "hit" + i, x: x(i) - slotW / 2, y: T, width: slotW, height: H - T - B, fill: hov && hov.i === i ? "rgba(255,255,255,0.05)" : "transparent", style: {cursor: onPick ? "pointer" : "default"},
      onMouseEnter: e => move(e, i), onMouseMove: e => move(e, i), onMouseLeave: () => setHov(null), onClick: onPick ? () => onPick(i) : undefined}));
  });
  const rows = hov ? (tipRows ? tipRows(hov.i) : series.map(sr => ({label: sr.name, value: fy(n(sr.vals[hov.i])), color: sr.color, raw: n(sr.vals[hov.i])})).filter(r => r.raw > 0).sort((a, b) => b.raw - a.raw)) : [];
  const total = hov && series.length > 1 && (!tipRows || tipRows.isVideos) ? fy(sums[hov.i]) : null;
  const flip = hov && box.current && hov.px > box.current.getBoundingClientRect().width * 0.6;
  return h("div", {className: "chartbox", ref: box},
    h("svg", {viewBox: "0 0 " + W + " " + H, className: "chart"}, parts),
    hov ? h("div", {className: "tip", style: {left: flip ? undefined : hov.px + 14, right: flip ? (box.current.getBoundingClientRect().width - hov.px + 14) : undefined, top: Math.max(0, hov.py - 12)}},
      h("div", {className: "tiptitle"}, tips ? tips[hov.i] : labels[hov.i]),
      rows.length ? rows.map((r, k) => h("div", {key: k, className: "tiprow"},
        h("div", {className: "tipline"}, h("i", {style: {background: r.color || "#9b9b9b"}}), h("span", null, r.label), h("b", null, r.value)),
        (r.sub || []).map((t, j) => h("div", {key: j, className: "tipsub"}, t)))) : h("div", {className: "tipsub"}, "Nothing in this slot."),
      total ? h("div", {className: "tipline tiptotal"}, h("span", null, "Total"), h("b", null, total)) : null) : null);
}
const Legend = ({series}) => series.length > 1 ? h("div", {className: "legend"}, series.map(s => h("span", {key: s.name}, h("i", {style: {background: s.color}}), s.name))) : null;

// ---------- the controls: dates, editor, rate ----------
function presets() { const t = keyOf(new Date()); return [["Today", t, t], ["Yesterday", addDays(t, -1), addDays(t, -1)], ["7 days", addDays(t, -6), t], ["30 days", addDays(t, -29), t], ["All time", "", ""]]; }
function Controls({period, setPeriod, editors, editor, setEditor, rate, setRate, onRefresh, busy, includeDev, setIncludeDev, devCount}) {
  const ps = presets();
  const devLabel = includeDev
    ? ("Showing: All (+dev" + (devCount ? " · " + devCount : "") + ")")
    : ("Showing: Editor runs" + (devCount ? " (" + devCount + " dev hidden)" : ""));
  return h("div", {className: "controls"},
    h("div", {className: "bar"},
      ps.map(([label, a, b]) => h("button", {key: label, className: "chip" + (period.from === a && period.to === b ? " on" : ""), onClick: () => setPeriod({from: a, to: b})}, label)),
      h("label", null, "From", h("input", {type: "date", className: "dt", value: period.from, onChange: e => setPeriod({from: e.target.value, to: period.to && e.target.value > period.to ? e.target.value : period.to})})),
      h("label", null, "To", h("input", {type: "date", className: "dt", value: period.to, onChange: e => setPeriod({from: period.from && e.target.value < period.from ? e.target.value : period.from, to: e.target.value})}))),
    h("div", {className: "bar"},
      h("label", null, "Editor", h("select", {className: "dt", value: editor, onChange: e => setEditor(e.target.value)}, h("option", {value: ""}, "All editors"), editors.map(e => h("option", {key: e.key, value: e.key}, e.name)))),
      h("label", null, "₹ per $", h("input", {type: "number", step: "0.1", value: rate, onChange: e => setRate(n(e.target.value))})),
      setIncludeDev ? h("button", {className: "chip" + (includeDev ? " on" : ""), title: "Include or hide runs mixed with Zeno code development", onClick: () => setIncludeDev(!includeDev)}, devLabel) : null,
      h("button", {className: "chip", disabled: busy, onClick: onRefresh}, busy ? "Refreshing…" : "Refresh")));
}


// ---------- the original numbers: tokens, cache, cost, problems, health (unchanged calculations) ----------
const PRICES = [  // [model match, USD per million tokens: input, cached read, cache write, output] from OpenAI and Anthropic list prices
  ['gpt-6.1-sol', 2, 0.1, 2.5, 10], ['gpt-5-codex', 1.25, 0.125, 1.25, 10], ['gpt-5', 1.25, 0.125, 1.25, 10], ['o3', 2, 0.5, 2, 8],
  ['opus-5-5', 4, 0.2, 5, 20], ['opus-5', 5, 0.5, 6.25, 25], ['opus-4', 15, 1.5, 18.75, 75],
  ['sonnet-5', 2, 0.2, 2.5, 10], ['sonnet-4', 3, 0.3, 3.75, 15],
  ['haiku-5', 0.1, 0.01, 0.125, 0.5], ['haiku-4', 1, 0.1, 1.25, 5], ['fable', 10, 0.25, 12.5, 50]];
function normModel(s) {
  return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}
function priceFor(model) {
  const m = normModel(model);
  if (!m) return null;
  for (const p of PRICES) {
    const k = normModel(p[0]);
    if (k.length <= 2 ? (m === k || m.startsWith(k + '-') || m.endsWith('-' + k) || m.includes('-' + k + '-')) : m.includes(k)) return p;
  }
  return null;
}
function n(v) { return Number(v || 0); }
function cacheWriteBreakdown(r) {
  const jr = (r && r.job_report) || {};
  const acc = jr.accounting || {};
  const ag = jr.agent || {};
  const cw = Math.max(0, n(acc.cache_write_tokens ?? acc.agent_cache_write_tokens ?? ag.cache_write_tokens ?? (r && (r.agent_cache_write_tokens ?? r.cache_write_tokens))));
  const e1h = Math.max(0, n(acc.ephemeral_1h_input_tokens ?? ag.ephemeral_1h_input_tokens ?? (r && r.ephemeral_1h_input_tokens)));
  const e5m = Math.max(0, n(acc.ephemeral_5m_input_tokens ?? ag.ephemeral_5m_input_tokens));
  const total = Math.max(cw, e5m + e1h);
  const w1h = Math.min(total, e1h);
  return {total, w5m: total - w1h, w1h};
}
function cacheWriteTokens(r) { return cacheWriteBreakdown(r).total; }
function agentCombinedTotal(r) {
  const acc = ((r && r.job_report) || {}).accounting || {};
  if (acc.agent_total_including_cache != null && n(acc.agent_total_including_cache) > 0) return n(acc.agent_total_including_cache);
  const cw = cacheWriteTokens(r);
  const aIn = n(r.agent_in_tokens), aCached = n(r.agent_cached_tokens), aOut = n(r.agent_out_tokens), aTot = n(r.agent_total_tokens);
  const writeSeparate = cw > 0 && (aIn < cw || (aTot > 0 && aTot === aIn + cw + aOut));
  return Math.max(aIn + (writeSeparate ? cw : 0) + aCached + aOut, aTot);
}
function costUsd(r) {
  const p = priceFor(r && r.agent_model);
  if (!p) return null;
  const aIn = n(r.agent_in_tokens), aCached = n(r.agent_cached_tokens), aOut = n(r.agent_out_tokens), aTot = n(r.agent_total_tokens);
  const {total: cw, w5m, w1h} = cacheWriteBreakdown(r);
  if (cw > 0) {
    const acc = ((r && r.job_report) || {}).accounting || {};
    const totIncl = n(acc.agent_total_including_cache);
    const writeSeparate = aIn < cw || (totIncl > 0 && totIncl === aIn + cw + aCached + aOut) || (aTot > 0 && aTot === aIn + cw + aOut);
    const freshIn = writeSeparate ? aIn : Math.max(0, aIn - cw);
    return (freshIn * p[1] + aCached * p[2] + w5m * p[3] + w1h * (p[1] * 2) + aOut * p[4]) / 1e6;
  }
  return (aIn * p[1] + aCached * p[2] + aOut * p[4]) / 1e6;
}
function who(r) { return (r.user_email || r.editor_name || 'unknown').toLowerCase(); }
function periodCutoff(p, now, from, to) {  // returns {from, to} in seconds; to = 0 means "until now"
  if (p === 'all') return {from: 0, to: 0};
  if (p === 'custom') { const a = from ? new Date(from).getTime() / 1000 : 0, b = to ? new Date(to).getTime() / 1000 : 0; return {from: a || 0, to: b || 0}; }
  if (p === 'today') { const d = new Date(now * 1000); d.setHours(0, 0, 0, 0); return {from: d.getTime() / 1000, to: 0}; }
  return {from: now - Number(p) * 86400, to: 0};
}
function inPeriod(r, rg) { const t = n(r.created_at); return t >= n(rg.from) && (!rg.to || t <= rg.to); }
const HEAVY_CACHE = 5000000;  // a run that re-read more than 5M cached tokens had a long chat with many steps
function wasteGroups(runs) {  // one row per editor and project; flagged when a run re-read a lot or the project was run several times
  const g = new Map();
  for (const r of runs) {
    if (!r.project) continue;
    const k = who(r) + '|' + r.project;
    const e = g.get(k) || {editor: r.editor_name || who(r), project: r.project, runs: 0, fresh: 0, cached: 0, maxCached: 0};
    e.runs += 1; e.fresh += n(r.agent_in_tokens) + n(r.agent_out_tokens); e.cached += n(r.agent_cached_tokens); e.maxCached = Math.max(e.maxCached, n(r.agent_cached_tokens));
    g.set(k, e);
  }
  const rows = [...g.values()].filter(e => e.runs > 1 || e.maxCached >= HEAVY_CACHE);
  rows.forEach(e => { e.flags = [e.maxCached >= HEAVY_CACHE ? 'Long chat' : '', e.runs > 1 ? 'Run ' + e.runs + ' times' : ''].filter(Boolean); });
  return rows.sort((a, b) => b.cached - a.cached);
}
function transcribeSummary(events) {
  const ev = events || [];
  const bad = ev.filter(e => e.status !== 'ok');
  const failed = ev.filter(e => e.status === 'failed').length;
  const by = {};
  ev.forEach(e => { const u = by[e.user_name] || (by[e.user_name] = {name: e.user_name, n: 0, bad: 0, last: 0}); u.n += 1; if (e.status !== 'ok') u.bad += 1; u.last = Math.max(u.last, n(e.created_at)); });
  return {calls: ev.length, bad: bad.length, failed, incomplete: bad.length - failed, rate: ev.length ? Math.round(100 * (ev.length - bad.length) / ev.length) : 100,
    users: Object.values(by).sort((a, b) => b.bad - a.bad || b.n - a.n)};
}
function dayKey(t) { const d = new Date(n(t) * 1000); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
function dayLabel(k, short) { const d = new Date(k + 'T12:00:00'); return d.toLocaleDateString('en-GB', short ? {weekday: 'short', day: 'numeric'} : {weekday: 'long', day: 'numeric', month: 'short'}); }
function byDay(runs) {  // newest day first; each day holds its runs
  const days = new Map();
  for (const r of runs) { const k = dayKey(r.created_at); if (!days.has(k)) days.set(k, []); days.get(k).push(r); }
  return [...days.entries()].sort((a, b) => a[0] < b[0] ? 1 : -1).map(([key, rs]) => ({key, runs: rs}));
}
function quality(runs) {  // how complete the recorded numbers are for these videos
  const q = {total: runs.length, noAgent: 0, noDetail: 0, mixed: 0, exact: 0, noGemini: 0};
  for (const r of runs) {
    const jr = r.job_report || {}, ag = jr.agent || {}, acc = jr.accounting || {};
    if (!n(r.agent_total_tokens) && !n(r.agent_cached_tokens)) q.noAgent += 1;
    if (ag.turns == null) q.noDetail += 1;
    if (ag.mixed_with_development) q.mixed += 1;
    if (acc.agent_token_precision === 'exact') q.exact += 1;
    if (!n(r.transcribe_total_tokens)) q.noGemini += 1;
  }
  return q;
}
function notionName(v) { return String(v || '').replace(/^(reel_|SQ_)/i, '').replace(/\\.mp4$/i, '').replace(/\\s*\\(\\d+\\)\\s*$/, '').trim(); }
function clusterRuns(rs) {  // renders of one video share the job start, so each later render already holds the earlier ones: keep the last of each window
  const sorted = rs.slice().sort((a, b) => n(a.created_at) - n(b.created_at));
  const out = [];
  for (const r of sorted) {
    const w = n(((r.job_report || {}).agent || {}).window_minutes);
    const start = w ? n(r.created_at) - w * 60 : null;
    const last = out[out.length - 1];
    if (last && start != null && last.start != null && Math.abs(start - last.start) <= 300) out[out.length - 1] = {start, row: r};
    else out.push({start, row: r});
  }
  return out.map(c => c.row);
}
function jobOutcome(j) {
  if (j.kind === 'blocked' || j.kind === 'render_failed' || j.kind === 'failed') return 'bad';
  if (j.kind === 'render') return j.idx > 0 ? 'rework' : 'good';
  if (j.kind === 'feedback') return 'rework';
  return 'open';
}
function buildEdits(jobs, runs) {  // one video edit = one editor + one script; every extra job on it is a rework
  const by = new Map(), loose = [];
  for (const j of jobs || []) {
    if (!j.project) { loose.push(Object.assign({idx: 0}, j)); continue; }
    const k = String(j.editor_name || '').toLowerCase() + '|' + j.project;
    if (!by.has(k)) by.set(k, {key: k, editor: j.editor_name, project: j.project, jobs: []});
    by.get(k).jobs.push(Object.assign({}, j));
  }
  const edits = [];
  for (const e of by.values()) {
    e.jobs.sort((a, b) => n(a.created_at) - n(b.created_at));
    e.jobs.forEach((j, i) => { j.idx = i; });
    const named = e.jobs.filter(j => j.video_name);
    e.name = notionName(named.length ? named[named.length - 1].video_name : '') || e.project;
    e.start = n(e.jobs[0].created_at);
    const ok = e.jobs.filter(j => j.kind === 'render');
    e.finished = ok.length ? n(ok[0].updated_at) : 0;
    e.last = Math.max(...e.jobs.map(j => n(j.updated_at)));
    e.nJobs = e.jobs.length; e.rendersOk = ok.length;
    e.failed = e.jobs.filter(j => j.kind === 'render_failed' || j.kind === 'failed').length;
    e.feedback = e.jobs.filter(j => j.kind === 'feedback').length;
    e.blocked = e.jobs.filter(j => j.kind === 'blocked').length;
    e.result = !ok.length ? 'open' : e.nJobs === 1 ? 'one' : 'rework';
    const mine = (runs || []).filter(r => String(r.editor_name || '').toLowerCase() === String(e.editor || '').toLowerCase() && r.project === e.project);
    const kept = clusterRuns(mine);
    e.q = kept.length ? kept.map(dataQuality).sort((a, b) => QRANK[a[0]] - QRANK[b[0]])[0] : null; e.tok = aggregate(kept).total; e.raw = mine.length; e.kept = kept.length;
    e.calls = kept.some(r => ((r.job_report || {}).agent || {}).turns != null) ? kept.reduce((a, r) => a + n(((r.job_report || {}).agent || {}).turns), 0) : null;
    e.warn = [];
    if (e.nJobs > 1) e.warn.push(e.nJobs + ' jobs');
    if (e.feedback) e.warn.push(e.feedback + (e.feedback === 1 ? ' feedback' : ' feedbacks'));
    if (e.failed) e.warn.push(e.failed + (e.failed === 1 ? ' failed render' : ' failed renders'));
    if (!ok.length) e.warn.push('not finished');
    edits.push(e);
  }
  edits.sort((a, b) => b.last - a.last);
  return {edits, loose, jobs: edits.flatMap(e => e.jobs).concat(loose)};
}
function verParts(v) { return String(v || '').replace(/^v/i, '').split('.').map(x => parseInt(x, 10) || 0); }
function verCmp(a, b) { const x = verParts(a), y = verParts(b); for (let i = 0; i < 3; i++) { if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) - (y[i] || 0); } return 0; }
function versionRows(keys) {  // one row per editor: newest version their keys report, and when any key was last used
  const by = new Map();
  for (const k of keys || []) {
    const name = String(k.name || '').trim();
    if (!name || k.revoked_at) continue;
    const id = name.toLowerCase();
    const cur = by.get(id) || {name, version: '', last: 0};
    cur.last = Math.max(cur.last, n(k.last_used_at));
    if (k.zeno_version && (!cur.version || verCmp(k.zeno_version, cur.version) > 0)) cur.version = k.zeno_version;
    by.set(id, cur);
  }
  const rows = [...by.values()].filter(r => r.last > 86400);
  let newest = '';
  rows.forEach(r => { if (r.version && (!newest || verCmp(r.version, newest) > 0)) newest = r.version; });
  rows.forEach(r => { r.state = !r.version ? 'unknown' : verCmp(r.version, newest) < 0 ? 'behind' : 'current'; });
  const rank = {behind: 0, unknown: 1, current: 2};
  rows.sort((a, b) => rank[a.state] - rank[b.state] || b.last - a.last);
  return {rows, newest};
}
function matches(r, q) {
  if (!q) return true;
  const hay = [r.editor_name, r.user_email, r.project, r.agent_name, r.agent_model, r.language].join(' ').toLowerCase();
  return q.toLowerCase().split(/\\s+/).every(w => hay.includes(w));
}
function aggregate(runs) {
  const users = new Map();
  const total = {runs: 0, seconds: 0, ain: 0, cached: 0, cwrite: 0, aout: 0, atotal: 0, acombined: 0, gin: 0, gout: 0, gtotal: 0, usd: 0, unpriced: 0, editors: 0};
  for (const r of runs) {
    const k = who(r);
    if (!users.has(k)) users.set(k, {key: k, name: r.editor_name || k, email: (r.user_email || '').toLowerCase(), agents: new Set(), models: new Set(),
      runs: 0, seconds: 0, ain: 0, cached: 0, cwrite: 0, aout: 0, atotal: 0, acombined: 0, gin: 0, gout: 0, gtotal: 0, usd: 0, unpriced: 0, last: 0});
    const u = users.get(k);
    const c = costUsd(r);
    const cw = cacheWriteTokens(r);
    const comb = agentCombinedTotal(r);
    for (const t of [u, total]) {
      t.runs += 1; t.seconds += n(r.video_seconds); t.ain += n(r.agent_in_tokens); t.cached += n(r.agent_cached_tokens);
      t.cwrite += cw; t.aout += n(r.agent_out_tokens); t.atotal += n(r.agent_total_tokens); t.acombined += comb;
      t.gin += n(r.transcribe_in_tokens); t.gout += n(r.transcribe_out_tokens); t.gtotal += n(r.transcribe_total_tokens);
      if (c === null) t.unpriced += 1; else t.usd += c;
    }
    u.agents.add(r.agent_name || 'unknown'); u.models.add(r.agent_model || 'unknown');
    u.last = Math.max(u.last, n(r.created_at));
  }
  total.editors = users.size;
  return {users: [...users.values()].sort((a, b) => b.acombined - a.acombined || b.atotal - a.atotal), total};
}
function csvOf(runs) {
  const head = ['when', 'editor', 'email', 'project', 'language', 'agent', 'model', 'clips', 'video_seconds', 'agent_in', 'agent_cached_read',
    'agent_cache_write', 'agent_out', 'agent_total', 'agent_combined_total', 'gemini_in', 'gemini_out', 'gemini_total', 'est_usd'];
  const q = v => '"' + String(v ?? '').replace(/"/g, '""') + '"';
  return [head.join(',')].concat(runs.map(r => [fmtDate(r.created_at), r.editor_name, r.user_email, r.project, r.language, r.agent_name, r.agent_model,
    r.clips_count, r.video_seconds, r.agent_in_tokens, r.agent_cached_tokens, cacheWriteTokens(r), r.agent_out_tokens, r.agent_total_tokens,
    agentCombinedTotal(r), r.transcribe_in_tokens, r.transcribe_out_tokens, r.transcribe_total_tokens, (costUsd(r) ?? '')].map(q).join(','))).join('\\n');
}

function Tags({items}) { return h('span', null, [...items].map(x => h('span', {className: 'tag', key: x}, x))); }
function TranscriptionHealth({data, cutoff}) {
  const ev = (data.transcriptions || []).filter(e => inPeriod(e, cutoff));
  const bad = ev.filter(e => e.status !== 'ok');
  const sm = transcribeSummary(ev);
  const avg = a => a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0;
  const models = {};
  ev.filter(e => e.model).forEach(e => { const m = models[e.model] || (models[e.model] = {n: 0, s: []}); m.n += 1; m.s.push(n(e.seconds)); });
  const fell = ev.filter(e => e.status === 'ok' && e.failed_models).length;
  const stat = (v, label) => h('div', {className: 'stat', key: label}, h('b', null, v), h('span', null, label));
  return h('div', {className: 'card'},
    h('h1', null, 'Transcription health'),
    h('p', {className: 'sub'}, 'Every Gemini transcription call: who, which model answered, how long it took, and why a call failed. Numbers and short reasons only, never audio or text.'),
    h('div', {className: 'stats'},
      stat(fmtNum(sm.calls), 'transcription calls'), stat(sm.rate + '%', 'answered fully'), stat(fmtNum(sm.failed), 'failed'),
      stat(fmtNum(sm.incomplete), 'incomplete'), stat(fmtNum(fell), 'saved by a fallback model'),
      stat(avg(ev.map(e => n(e.seconds))).toFixed(1) + ' s', 'average time')),
    Object.keys(models).length ? h('p', {className: 'sub', style: {marginTop: 14}}, 'Models that answered: ' + Object.keys(models).map(m => m + ' ' + models[m].n + ' calls, ' + avg(models[m].s).toFixed(1) + ' s').join(' · ')) : null,
    h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'Editor'), h(Th, {right: true}, 'Calls'), h(Th, {right: true}, 'Problems'), h(Th, null, 'Last call'))),
      h('tbody', null, sm.users.length ? sm.users.map(u => h('tr', {key: u.name},
        h('td', null, h('b', null, u.name)), h('td', {className: 'n'}, fmtNum(u.n)),
        h('td', {className: 'n'}, u.bad ? h('b', null, fmtNum(u.bad)) : '0'), h('td', {className: 'muted'}, fmtDate(u.last))))
        : h('tr', null, h('td', {colSpan: 4}, 'No transcription calls recorded in this period yet'))))),
    bad.length ? h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'When'), h(Th, null, 'Editor'), h(Th, null, 'Result'), h(Th, null, 'Why'), h(Th, null, 'Models that failed'), h(Th, {right: true}, 'Audio s'), h(Th, {right: true}, 'Took s'))),
      h('tbody', null, bad.slice(0, 50).map(e => h('tr', {key: e.id},
        h('td', {className: 'muted'}, fmtDate(e.created_at)), h('td', null, e.user_name), h('td', null, h('b', null, e.status)), h('td', null, e.reason),
        h('td', {className: 'muted'}, e.failed_models), h('td', {className: 'n'}, Math.round(n(e.audio_seconds))), h('td', {className: 'n'}, n(e.seconds).toFixed(1))))))) : null);
}

const sumNew = t => t.ain + t.aout;
const tokHead = () => [['i', 'Input'], ['cw', 'of which cache write'], ['cr', 'Re-read (cache read)'], ['o', 'Output'], ['gi', 'Gemini in'], ['go', 'Gemini out'], ['gt', 'Gemini total']]
  .map(([k, label]) => h(Th, {right: true, key: k}, label));
const tokCells = u => ['ain', 'cwrite', 'cached', 'aout', 'gin', 'gout', 'gtotal'].map(k => h('td', {className: 'n' + (k === 'cwrite' ? ' muted' : ''), key: k}, fmtNum(u[k])));
const money = (t, rate) => (!t.usd && t.unpriced) ? 'No price' : inr(t.usd, rate) + (t.unpriced ? ' *' : '');
function dataQuality(r) {  // how far the numbers of one render can be trusted
  const jr = r.job_report || {}, ag = jr.agent || {}, acc = jr.accounting || {};
  if (!n(r.agent_total_tokens) && !n(r.agent_cached_tokens)) return ['red', 'No agent tokens', 'Zeno reported no agent tokens for this render'];
  if (!r.job_report || !Object.keys(jr).length) return ['gray', 'Old Zeno', 'No job report: this Zeno version does not send calls, window or precision'];
  if (ag.mixed_with_development) return ['gray', 'Mixed', 'The chat also held work on Zeno code, so tokens are more than one edit'];
  if (acc.agent_token_precision === 'exact') return ['ok', 'Exact', 'Tokens read exactly from the agent log'];
  return ['amber', 'Estimated', 'Tokens are an approximation'];
}
const QRANK = {red: 0, gray: 1, amber: 2, ok: 3};
const qPill = q => h('span', {className: 'pill ' + (q[0] === 'ok' ? 'ok2' : q[0]), title: q[2]}, q[1]);

function Daily({runs, rate}) {
  const {users, total: t} = aggregate(runs);
  const days = byDay(runs).slice(0, 31);
  const cols = days.slice(0, 7).map(d => d.key).reverse();           // up to 7 days, oldest first
  const count = new Map();
  for (const r of runs) { const e = who(r), d = dayKey(r.created_at); if (!count.has(e)) count.set(e, new Map()); count.get(e).set(d, (count.get(e).get(d) || 0) + 1); }
  const rows = users.slice().sort((a, b) => b.runs - a.runs || sumNew(b) - sumNew(a));
  const sum = (arr, k) => arr.reduce((x, r) => x + n(r[k]), 0);
  return h('div', null,
    h('p', {className: 'sub', style: {marginTop: 14}}, t.runs ? fmtNum(t.runs) + ' renders by ' + fmtNum(t.editors) + ' editors · ' + fmtNum(sumNew(t)) + ' new tokens · ' + money(t, rate) + ' estimated' : 'No renders in this period.'),
    h('h2', null, 'Renders per editor, per day'),
    h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'Editor'), cols.map(c => h(Th, {right: true, key: c}, dayLabel(c, true))), h(Th, {right: true}, 'Total'))),
      h('tbody', null, rows.map(u => h('tr', {key: u.key}, h('td', null, h('b', null, u.name)),
          cols.map(c => { const v = (count.get(u.key) || new Map()).get(c) || 0; return h('td', {className: 'n' + (v ? '' : ' muted'), key: c}, v || '–'); }),
          h('td', {className: 'n'}, h('b', null, fmtNum(u.runs))))),
        rows.length ? h('tr', {className: 'total'}, h('td', null, 'All editors'), cols.map(c => h('td', {className: 'n', key: c}, fmtNum((days.find(d => d.key === c) || {runs: []}).runs.length))), h('td', {className: 'n'}, fmtNum(t.runs))) : null))),
    cols.length < days.length ? h('p', {className: 'note'}, 'The table shows the latest 7 days; every day is listed below.') : null,
    h('h2', null, 'Usage per editor, day by day (per render)'),
    days.map(d => {
      const a = aggregate(d.runs);
      return h('div', {key: d.key},
        h('div', {className: 'dayhead'}, h('b', null, dayLabel(d.key)), h('span', {className: 'muted'}, '  ' + d.runs.length + (d.runs.length === 1 ? ' render' : ' renders') + ' · ' + fmtNum(sumNew(a.total)) + ' new tokens · ' + money(a.total, rate))),
        h('div', {className: 'scroll'}, h('table', null,
          h('thead', null, h('tr', null, h(Th, null, 'Editor'), h(Th, {right: true}, 'Renders'), tokHead(), h(Th, {right: true}, 'Est. cost'), h(Th, null, 'Last render'))),
          h('tbody', null, a.users.slice().sort((x, y) => y.runs - x.runs || sumNew(y) - sumNew(x)).map(u => h('tr', {key: u.key},
            h('td', null, h('b', null, u.name)), h('td', {className: 'n'}, fmtNum(u.runs)), tokCells(u),
            h('td', {className: 'n'}, (u.usd || !u.unpriced) ? inr(u.usd, rate) + (u.unpriced ? ' *' : '') : '–'), h('td', {className: 'muted'}, fmtDate(u.last)))),
            h('tr', {className: 'total'}, h('td', null, 'Day total'), h('td', {className: 'n'}, fmtNum(a.total.runs)), tokCells(a.total), h('td', {className: 'n'}, money(a.total, rate)), h('td', null))))));
    }),
    t.unpriced ? h('p', {className: 'foot'}, '* ' + t.unpriced + ' video(s) use a model with no price here and are left out of the cost.') : null);
}

function Videos({runs, rate, showAll, setShowAll, jobs, data}) {
  const shown = showAll ? runs : runs.slice(0, 100);
  const names = new Map();
  for (const j of jobs || []) { if (j.project && j.video_name) names.set(String(j.editor_name || '').toLowerCase() + '|' + j.project, notionName(j.video_name)); }
  const jobCount = new Map();
  for (const j of jobs || []) { if (j.project) { const k = String(j.editor_name || '').toLowerCase() + '|' + j.project; jobCount.set(k, (jobCount.get(k) || 0) + 1); } }
  const tev = data.transcriptions || [];
  const keyVer = new Map();
  for (const k of data.keys || []) { if (k.zeno_version && !k.revoked_at) { const id = String(k.name || '').toLowerCase(); if (!keyVer.has(id) || verCmp(k.zeno_version, keyVer.get(id)) > 0) keyVer.set(id, k.zeno_version); } }
  const verOf = r => {  // version seen on that editor's Gemini call closest to this render; else the newest version their key reports now
    const me = String(r.editor_name || '').toLowerCase();
    let best = null, gap = 6 * 3600;
    for (const e of tev) { if (String(e.user_name || '').toLowerCase() === me && e.zeno_version) { const d = Math.abs(n(e.created_at) - n(r.created_at)); if (d < gap) { gap = d; best = e.zeno_version; } } }
    return best ? {v: best, exact: true} : keyVer.has(me) ? {v: keyVer.get(me), exact: false} : null;
  };
  const gcalls = r => {  // Gemini requests this editor made while this render's chat ran
    const w = n(((r.job_report || {}).agent || {}).window_minutes);
    if (!w) return null;
    const lo = n(r.created_at) - w * 60 - 60, hi = n(r.created_at) + 60, me = String(r.editor_name || '').toLowerCase();
    const ev = tev.filter(e => String(e.user_name || '').toLowerCase() === me && n(e.created_at) >= lo && n(e.created_at) <= hi);
    return {all: ev.length, bad: ev.filter(e => e.status !== 'ok').length, hit: ev.filter(e => n(e.cached) > 0).length};
  };
  return h('div', null,
    h('p', {className: 'sub', style: {marginTop: 14}}, 'One row per finished render, newest first. A video rendered again appears again. Zeno version * = not seen on a request near that render, so it is the newest version their key reports now. The Video edits tab counts each video once.'),
    h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'When'), h(Th, null, 'Editor'), h(Th, null, 'Notion name'), h(Th, null, 'Model'), h(Th, null, 'Zeno version'), h(Th, null, 'Language'), h(Th, {right: true}, 'Clips'), h(Th, {right: true}, 'Seconds'),
        tokHead(), h(Th, {right: true}, 'Agent calls'), h(Th, {right: true}, 'Cache re-read calls'), h(Th, {right: true}, 'Re-read per call'), h(Th, {right: true}, 'Gemini calls'), h(Th, {right: true}, 'Window (min)'), h(Th, {right: true}, 'Est. cost'), h(Th, null, 'Data'), h(Th, null, 'Notes'))),
      h('tbody', null, shown.length ? shown.map(r => {
        const c = costUsd(r), ag = (r.job_report || {}).agent || {}, fails = ((r.job_report || {}).failures || []).length;
        const u = {ain: n(r.agent_in_tokens), cwrite: cacheWriteTokens(r), cached: n(r.agent_cached_tokens), aout: n(r.agent_out_tokens), gin: n(r.transcribe_in_tokens), gout: n(r.transcribe_out_tokens), gtotal: n(r.transcribe_total_tokens)};
        return h('tr', {key: r.id},
          h('td', {className: 'muted'}, fmtDate(r.created_at)), h('td', null, h('b', null, r.editor_name)), h('td', {title: r.project, style: {maxWidth: 360, overflow: 'hidden', textOverflow: 'ellipsis'}}, names.get(String(r.editor_name || '').toLowerCase() + '|' + r.project) || short(r.project)), h('td', {className: 'muted'}, r.agent_model && r.agent_model !== 'unknown' ? r.agent_model : '–'), (() => { const v = verOf(r); return h('td', {className: 'muted', title: v && !v.exact ? 'No Gemini call near this render; this is the newest version their key reports now' : 'Version seen on the request closest to this render'}, v ? 'v' + String(v.v).replace(/^v/i, '') + (v.exact ? '' : ' *') : '–'); })(), h('td', null, r.language),
          h('td', {className: 'n'}, fmtNum(r.clips_count)), h('td', {className: 'n'}, Math.round(n(r.video_seconds))), tokCells(u),
          h('td', {className: 'n muted'}, ag.turns != null ? fmtNum(ag.turns) : '–'), h('td', {className: 'n muted', title: 'Every agent call after the first re-reads the cached chat'}, ag.turns != null ? (n(r.agent_cached_tokens) ? fmtNum(ag.turns) : 0) : '–'),
          h('td', {className: 'n muted'}, ag.turns && n(r.agent_cached_tokens) ? fmtNum(Math.round(n(r.agent_cached_tokens) / ag.turns)) : '–'),
          (() => { const g = gcalls(r); return h('td', {className: 'n muted'}, g ? fmtNum(g.all) + (g.bad ? ' · ' + g.bad + ' failed' : '') + (g.hit ? ' · ' + g.hit + ' reused' : '') : '–'); })(), h('td', {className: 'n muted'}, ag.window_minutes != null ? ag.window_minutes : '–'),
          h('td', {className: 'n'}, c === null ? '–' : inr(c, rate)),
          h('td', null, qPill(dataQuality(r))),
          h('td', null, (() => { const c = jobCount.get(String(r.editor_name || '').toLowerCase() + '|' + r.project); return h('span', {className: 'pill ok2', style: {marginRight: 4}, title: 'This render finished and the video went to the output folder'}, c == null ? 'Done' : c === 1 ? 'Done in 1 job' : 'Done after ' + c + ' jobs'); })(),
            n(r.agent_cached_tokens) >= HEAVY_CACHE ? h('span', {className: 'pill amber', style: {marginRight: 4}}, 'Long chat') : null,
            !n(r.agent_total_tokens) && !n(r.agent_cached_tokens) ? h('span', {className: 'pill red', style: {marginRight: 4}}, 'No agent tokens') : null,
            ag.mixed_with_development ? h('span', {className: 'pill gray', style: {marginRight: 4}, title: 'This window also holds work on Zeno code, so it is not the cost of one edit'}, 'Mixed with dev work') : null,
            fails ? h('span', {className: 'pill red'}, fails + (fails === 1 ? ' failure' : ' failures')) : null));
      }) : h('tr', null, h('td', {colSpan: 25}, 'No videos in this period'))))),
    runs.length > 100 ? h('p', {className: 'note', style: {marginTop: 10}}, h('a', {href: '#', onClick: e => { e.preventDefault(); setShowAll(!showAll); }}, showAll ? 'Show latest 100' : 'Show all ' + runs.length)) : null);
}

function Editors({runs, rate, vers}) {
  const {users, total: t} = aggregate(runs);
  const verOf = new Map(vers.rows.map(r => [r.name.toLowerCase(), r]));
  const dayCount = u => new Set(runs.filter(r => who(r) === u.key).map(r => dayKey(r.created_at))).size;
  return h('div', null,
    h('p', {className: 'sub', style: {marginTop: 14}}, 'Totals for the chosen period, one row per editor.'),
    h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'Editor'), h(Th, null, 'Version'), h(Th, {right: true}, 'Renders'), h(Th, {right: true}, 'Active days'), h(Th, {right: true}, 'Renders per day'),
        tokHead(), h(Th, {right: true}, 'New tokens per render'), h(Th, {right: true}, 'Est. cost'), h(Th, null, 'Last render'))),
      h('tbody', null, users.slice().sort((a, b) => b.runs - a.runs).map(u => {
        const v = verOf.get(String(u.name).toLowerCase()), d = dayCount(u) || 1;
        return h('tr', {key: u.key}, h('td', null, h('b', null, u.name)),
          h('td', null, v && v.version ? h('span', {className: 'pill ' + (v.state === 'behind' ? 'amber' : 'gray')}, 'v' + String(v.version).replace(/^v/i, '')) : ''),
          h('td', {className: 'n'}, fmtNum(u.runs)), h('td', {className: 'n'}, fmtNum(d)), h('td', {className: 'n'}, (u.runs / d).toFixed(1)), tokCells(u),
          h('td', {className: 'n'}, fmtNum(Math.round(sumNew(u) / Math.max(1, u.runs)))),
          h('td', {className: 'n'}, (u.usd || !u.unpriced) ? inr(u.usd, rate) + (u.unpriced ? ' *' : '') : '–'), h('td', {className: 'muted'}, fmtDate(u.last)));
      }),
        users.length ? h('tr', {className: 'total'}, h('td', null, 'All editors'), h('td', null), h('td', {className: 'n'}, fmtNum(t.runs)), h('td', null), h('td', null), tokCells(t),
          h('td', {className: 'n'}, fmtNum(Math.round(sumNew(t) / Math.max(1, t.runs)))), h('td', {className: 'n'}, money(t, rate)), h('td', null))
          : h('tr', null, h('td', {colSpan: 17}, 'No videos in this period'))))));
}

function Problems({data, runs, cutoff}) {
  const waste = wasteGroups(runs);
  return h('div', null,
    h('h2', null, 'Waste watch'),
    h('p', {className: 'note'}, 'Projects where a video re-read over ' + fmtNum(HEAVY_CACHE) + ' cached tokens (a long chat), or that were edited more than once.'),
    h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'Editor'), h(Th, null, 'Project'), h(Th, {right: true}, 'Videos'), h(Th, {right: true}, 'New tokens'), h(Th, {right: true}, 'Re-read'), h(Th, null, 'Why flagged'))),
      h('tbody', null, waste.length ? waste.slice(0, 20).map(e => h('tr', {key: e.editor + e.project},
        h('td', null, h('b', null, e.editor)), h('td', {title: e.project}, short(e.project)), h('td', {className: 'n'}, fmtNum(e.runs)), h('td', {className: 'n'}, fmtNum(e.fresh)),
        h('td', {className: 'n'}, fmtNum(e.cached)), h('td', null, e.flags.map(f => h('span', {className: 'pill amber', key: f, style: {marginRight: 4}}, f)))))
        : h('tr', null, h('td', {colSpan: 6}, 'Nothing flagged in this period'))))),
    h(TranscriptionHealth, {data, cutoff}));
}

function Health({health, vers}) {
  const ok = (v, yes, no) => h('span', {className: 'pill ' + (v ? 'ok' : 'red')}, v ? yes : no);
  return h('div', null,
    h('h2', null, 'Server'),
    h('div', {className: 'scroll'}, h('table', null, h('tbody', null, health ? [
      ['Server', ok(health.ok, 'Up', 'Problem')], ['Gemini key', ok(health.gemini_configured, 'Configured', 'Missing')],
      ['Key store', ok(health.key_store_ok, (health.key_store || '?') + ' working', (health.key_store || '?') + ' not working')],
      ['Google sign-in', ok(health.google_oauth_enabled, 'On', 'Off')]].map(r => h('tr', {key: r[0]}, h('td', {style: {width: 200}}, r[0]), h('td', null, r[1])))
      : h('tr', null, h('td', null, 'Checking…'))))),
    h(Versions, {vers}));
}

function Versions({vers}) {
  const pill = r => r.state === 'behind' ? h('span', {className: 'pill amber'}, 'Update needed') : r.state === 'current' ? h('span', {className: 'pill ok'}, 'Up to date') : h('span', {className: 'pill gray'}, 'Unknown');
  return h('div', {className: 'card'}, h('h1', null, 'Zeno versions'),
    h('p', {className: 'sub'}, 'The newest version each editor\\'s keys have reported, compared with the newest version seen across the team' + (vers.newest ? ' (v' + String(vers.newest).replace(/^v/i, '') + ')' : '') + '.'),
    h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'Editor'), h(Th, null, 'Version'), h(Th, null, 'Status'), h(Th, null, 'Last used'))),
      h('tbody', null, vers.rows.length ? vers.rows.map(r => h('tr', {key: r.name},
        h('td', null, h('b', null, r.name)), h('td', null, r.version ? 'v' + String(r.version).replace(/^v/i, '') : '–'), h('td', null, pill(r)), h('td', {className: 'muted'}, fmtDate(r.last))))
        : h('tr', null, h('td', {colSpan: 4}, 'No version data yet')))))); 
}

function Notes({runs, data}) {
  const q = quality(runs);
  const pct = x => q.total ? Math.round(100 * x / q.total) + '%' : '–';
  const row = (label, v, why) => h('tr', {key: label}, h('td', null, label), h('td', {className: 'n'}, fmtNum(v)), h('td', {className: 'n muted'}, pct(v)), h('td', {className: 'muted', style: {whiteSpace: 'normal'}}, why));
  const para = (t) => h('p', {className: 'note', style: {maxWidth: 900, fontSize: 14, color: '#cfcfcf'}}, t);
  return h('div', null,
    h('h2', null, 'How each video is recorded'),
    para('One row is one video job. When the editor finishes, their Zeno reads the coding agent\\'s own log (Claude Code, Codex or Antigravity) for the time from the start of the job to its end, and sends the totals to the server.'),
    para('Input = fresh input plus cache write. Cache write is shown again in its own column. Re-read = cache read: the chat sent again on every step. Output = what the agent wrote. Every response is counted once, summed over all steps in the window, so the numbers are exact for what the agent log holds.'),
    para('Gemini in / out / total are the transcription tokens of that video\\'s clips, added up per video. Gemini calls are also logged one by one (including failed ones and retries) in the Problems tab, but those calls are not tied to a single video.'),
    h('h2', null, 'How complete is the data for this period?'),
    h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'Check'), h(Th, {right: true}, 'Videos'), h(Th, {right: true}, 'Share'), h(Th, null, 'What it means'))),
      h('tbody', null,
        row('Videos in this period', q.total, 'Every row on the Videos tab.'),
        row('Marked "exact" by the agent log', q.exact, 'Newer records say how precise the token count is. Older rows do not carry this.'),
        row('Without step and window details', q.noDetail, 'Older records: no cache write, steps or window minutes, so the cache write column can show 0.'),
        row('Mixed with work on Zeno code', q.mixed, 'The same chat also edited Zeno itself, so these tokens are more than the cost of one edit.'),
        row('No agent tokens recorded', q.noAgent, 'The agent log could not be read for that job (token count shows 0).'),
        row('No Gemini tokens recorded', q.noGemini, 'No transcription tokens were reported for the video.')))),
    h('p', {className: 'note', style: {marginTop: 10}}, 'A video is a job that was reported to the server. An editor who edits the same project again creates a second row.'));
}

function dayOptions(jobs) { return [...new Set(jobs.map(j => dayKey(j.created_at)))].sort().reverse(); }

function Timeline({jobs, day, setDay, days}) {
  const list = jobs.filter(j => dayKey(j.created_at) === day);
  const hr = 3600;
  const isLong = j => n(j.updated_at) - n(j.created_at) > 3 * hr;   // a record that stayed open for hours: its real working time is unknown
  const core = list.filter(j => !isLong(j)), basis = core.length ? core : list;
  const starts = basis.map(j => n(j.created_at)), ends = basis.map(j => Math.max(n(j.updated_at), n(j.created_at)));
  const x0 = Math.floor(Math.min(...starts) / hr) * hr, x1 = Math.max(x0 + 4 * hr, Math.ceil(Math.max(...ends) / hr) * hr);
  const pos = t => Math.max(0, Math.min(100, 100 * (t - x0) / (x1 - x0)));
  const hours = []; for (let t = x0; t <= x1; t += hr) hours.push(t);
  const step = hours.length > 14 ? 2 : 1, hw = (100 / (hours.length - 1)).toFixed(4) + '%';
  const by = new Map();
  list.forEach(j => { const k = j.editor_name || '?'; if (!by.has(k)) by.set(k, []); by.get(k).push(j); });
  const rows = [...by.entries()].sort((a, b) => Math.min(...a[1].map(j => n(j.created_at))) - Math.min(...b[1].map(j => n(j.created_at))));
  const hhmm = t => { const d = new Date(t * 1000); return clock12(d); };
  const kindText = {render: 'finished', feedback: 'feedback job', render_failed: 'render failed', failed: 'render failed', blocked: 'blocked at the check', inspect: 'started, not finished', inspected: 'started, not finished', checking: 'started, not finished'};
  return h('div', null,
    h('div', {className: 'bar', style: {margin: '0 0 8px'}}, h('span', {className: 'note', style: {margin: 0}}, 'Day'),
      h('select', {className: 'dt', value: day, onChange: e => setDay(e.target.value)}, days.map(d => h('option', {key: d, value: d}, dayLabel(d))))),
    list.length ? h('div', {className: 'gantt'},
      rows.map(([name, js]) => h('div', {className: 'grow2', key: name}, h('div', {className: 'glabel'}, name),
        h('div', {className: 'gtrack', style: {'--hw': hw}}, js.map(j => h('div', {key: j.id, className: 'gb ' + jobOutcome(j) + (isLong(j) ? ' long' : ''),
          style: {left: pos(n(j.created_at)) + '%', width: Math.max(0.3, pos(Math.max(n(j.updated_at), n(j.created_at))) - pos(n(j.created_at))) + '%'},
          title: (notionName(j.video_name) || j.project || 'No video yet') + '\\n' + (kindText[j.kind] || j.kind) + (j.idx > 0 ? ' (job ' + (j.idx + 1) + ', rework)' : '') + '\\n' + hhmm(n(j.created_at)) + ' to ' + hhmm(n(j.updated_at)) + (isLong(j) ? '\\nThis record stayed open for ' + Math.round((n(j.updated_at) - n(j.created_at)) / hr) + ' h, so the real working time is unknown' : '')}))))),
      h('div', {className: 'gaxis'}, h('div', null), h('div', {className: 'gticks'}, hours.map((t, i) => i % step === 0 ? h('span', {key: t, style: {left: pos(t) + '%'}}, hhmm(t).replace(':00 ', ' ')) : null))),
      h('div', {className: 'legend'},
        h('span', null, h('i', {style: {background: '#3d9a6b'}}), 'Finished in the first job'), h('span', null, h('i', {style: {background: '#d9a441'}}), 'Rework: a second or later job on the same video'),
        h('span', null, h('i', {style: {background: '#d4574b'}}), 'Failed render or blocked at the check'), h('span', null, h('i', {style: {background: '#6b7480'}}), 'Started, never finished'),
        h('span', null, h('i', {style: {background: '#6b7480', opacity: .5, height: 4}}), 'Thin faded line: the job record stayed open for hours, real working time unknown'), h('span', null, 'Each bar runs from job start to job end. Hover for the Notion name.')))
      : h('p', {className: 'note'}, 'No jobs on this day.'));
}

function EditsView({data, jobs, jobsMsg, rg, q, rate, day, setDay}) {
  if (!jobs) return h('p', {className: 'note', style: {marginTop: 14}}, jobsMsg || 'Loading the job log…');
  const built = buildEdits(jobs, data.runs);
  const inRg = j => inPeriod({created_at: j.created_at}, rg);
  const qq = String(q || '').toLowerCase().split(/\\s+/).filter(Boolean);
  const hay = e => (String(e.editor) + ' ' + e.name + ' ' + e.project).toLowerCase();
  const edits = built.edits.filter(e => inPeriod({created_at: e.start}, rg) && qq.every(w => hay(e).includes(w)));
  const jobsIn = built.jobs.filter(inRg).filter(j => !qq.length || qq.every(w => (String(j.editor_name) + ' ' + notionName(j.video_name) + ' ' + String(j.project)).toLowerCase().includes(w)));
  const days = dayOptions(jobsIn);
  const curDay = days.includes(day) ? day : (days[0] || '');
  const names = [...new Set(edits.map(e => e.editor))];
  const cols = dayOptions(edits.map(e => ({created_at: e.start}))).slice(0, 7).reverse();
  const cell = (ed, d) => edits.filter(e => e.editor === ed && dayKey(e.start) === d);
  const score = names.map(ed => {
    const es = edits.filter(e => e.editor === ed);
    const t = aggregate([].concat(...es.map(e => clusterRuns((data.runs || []).filter(r => String(r.editor_name || '').toLowerCase() === String(ed).toLowerCase() && r.project === e.project))))).total;
    return {ed, n: es.length, one: es.filter(e => e.result === 'one').length, re: es.filter(e => e.result === 'rework').length, open: es.filter(e => e.result === 'open').length,
      failed: es.reduce((x, e) => x + e.failed, 0), blocked: jobsIn.filter(j => !j.project && j.editor_name === ed && j.kind === 'blocked').length, tok: t};
  }).sort((a, b) => b.n - a.n);
  const tot = score.reduce((x, r) => ({n: x.n + r.n, one: x.one + r.one, re: x.re + r.re, open: x.open + r.open, failed: x.failed + r.failed, blocked: x.blocked + r.blocked}), {n: 0, one: 0, re: 0, open: 0, failed: 0, blocked: 0});
  const rate1 = (a, b) => b ? Math.round(100 * a / b) + '%' : '–';
  const resultPill = e => e.result === 'one' ? h('span', {className: 'pill ok2'}, 'Finished in 1 job') : e.result === 'rework' ? h('span', {className: 'pill amber'}, 'Finished after ' + e.nJobs + ' jobs') : (n(data.now) - e.last < 1800 ? h('span', {className: 'pill gray'}, 'In progress') : h('span', {className: 'pill red'}, 'Not finished'));
  const span = e => { const end = e.finished || e.last; const m = Math.max(0, Math.round((end - e.start) / 60)); return m >= 60 ? Math.floor(m / 60) + ' h ' + (m % 60) + ' min' : m + ' min'; };
  return h('div', null,
    h('p', {className: 'sub', style: {marginTop: 14}}, edits.length ? fmtNum(edits.length) + ' video edits by ' + names.length + ' editors: ' + tot.one + ' finished in one job, ' + tot.re + ' needed more than one job, ' + tot.open + ' not finished' : 'No video edits in this range.'),
    h('h2', null, 'Timeline'),
    h('p', {className: 'note'}, 'A job starts when the editor gives the script, footage and Notion name, and ends when the video is in the output folder. A second job on the same video counts as rework.'),
    curDay ? h(Timeline, {jobs: jobsIn, day: curDay, setDay, days}) : h('p', {className: 'note'}, 'No jobs in this range.'),
    h('h2', null, 'Video edits per editor, per day'),
    h('p', {className: 'note'}, 'Number of different videos started that day. The amber number is how many of them needed more than one job.'),
    h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'Editor'), cols.map(c => h(Th, {right: true, key: c}, dayLabel(c, true))), h(Th, {right: true}, 'Total'))),
      h('tbody', null, score.map(r => h('tr', {key: r.ed}, h('td', null, h('b', null, r.ed)),
        cols.map(c => { const es = cell(r.ed, c), rw = es.filter(e => e.result !== 'one').length; return h('td', {className: 'n' + (es.length ? '' : ' muted'), key: c}, es.length || '–', rw ? h('span', {className: 'rw'}, rw) : null); }),
        h('td', {className: 'n'}, h('b', null, r.n)))),
        names.length ? h('tr', {className: 'total'}, h('td', null, 'All editors'), cols.map(c => h('td', {className: 'n', key: c}, edits.filter(e => dayKey(e.start) === c).length || '–')), h('td', {className: 'n'}, tot.n)) : null))),
    h('h2', null, 'Scorecard'),
    h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'Editor'), h(Th, {right: true}, 'Video edits'), h(Th, {right: true}, 'In 1 job'), h(Th, {right: true}, 'Needed rework'), h(Th, {right: true}, 'Not finished'),
        h(Th, {right: true}, 'One-job rate'), h(Th, {right: true}, 'Failed renders'), h(Th, {right: true}, 'Blocked checks'), h(Th, {right: true}, 'New tokens per edit'), h(Th, {right: true}, 'Re-read per edit'))),
      h('tbody', null, score.map(r => h('tr', {key: r.ed}, h('td', null, h('b', null, r.ed)), h('td', {className: 'n'}, r.n), h('td', {className: 'n'}, r.one),
        h('td', {className: 'n'}, r.re ? h('span', {className: 'pill amber'}, r.re) : 0), h('td', {className: 'n'}, r.open ? h('span', {className: 'pill red'}, r.open) : 0),
        h('td', {className: 'n'}, rate1(r.one, r.n)), h('td', {className: 'n'}, r.failed || 0), h('td', {className: 'n'}, r.blocked || 0),
        h('td', {className: 'n'}, fmtNum(Math.round(sumNew(r.tok) / Math.max(1, r.n)))), h('td', {className: 'n'}, fmtNum(Math.round(r.tok.cached / Math.max(1, r.n)))))),
        names.length ? h('tr', {className: 'total'}, h('td', null, 'All editors'), h('td', {className: 'n'}, tot.n), h('td', {className: 'n'}, tot.one), h('td', {className: 'n'}, tot.re), h('td', {className: 'n'}, tot.open),
          h('td', {className: 'n'}, rate1(tot.one, tot.n)), h('td', {className: 'n'}, tot.failed), h('td', {className: 'n'}, tot.blocked), h('td', null), h('td', null)) : null))),
    h('h2', null, 'Every video edit'),
    h('p', {className: 'note'}, 'One row per video, newest activity first (a video still being edited shows as In progress and has no tokens until it renders). Tokens count each video once: renders that share the same job start are not added together. Agent calls = model requests the editor chat made (each one re-sends the chat, so each one is a cache re-read); – means that Zeno version did not report it.'),
    h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'Started'), h(Th, null, 'Finished'), h(Th, null, 'Time taken'), h(Th, null, 'Editor'), h(Th, null, 'Notion name'), h(Th, {right: true}, 'Jobs'), h(Th, null, 'Result'),
        h(Th, {right: true}, 'Agent calls'), h(Th, {right: true}, 'Input'), h(Th, {right: true}, 'Output'), h(Th, {right: true}, 'Re-read'), h(Th, {right: true}, 'Re-read per call'), h(Th, {right: true}, 'Gemini in'), h(Th, {right: true}, 'Gemini out'), h(Th, {right: true}, 'Est. cost'), h(Th, null, 'Data'), h(Th, null, 'Warnings'))),
      h('tbody', null, edits.length ? edits.map(e => h('tr', {key: e.key},
        h('td', {className: 'muted'}, fmtDate(e.start)), h('td', {className: 'muted'}, e.finished ? fmtDate(e.finished) : '–'), h('td', {className: 'muted'}, span(e)),
        h('td', null, h('b', null, e.editor)), h('td', {title: e.name + '  (' + e.project + ')', style: {maxWidth: 440, overflow: 'hidden', textOverflow: 'ellipsis'}}, e.name), h('td', {className: 'n'}, e.nJobs), h('td', null, resultPill(e)),
        h('td', {className: 'n'}, e.calls ? fmtNum(e.calls) : '–'), h('td', {className: 'n'}, e.kept ? fmtNum(e.tok.ain) : '–'), h('td', {className: 'n'}, e.kept ? fmtNum(e.tok.aout) : '–'), h('td', {className: 'n'}, e.kept ? fmtNum(e.tok.cached) : '–'), h('td', {className: 'n'}, e.calls && e.tok.cached ? fmtNum(Math.round(e.tok.cached / e.calls)) : '–'),
        h('td', {className: 'n'}, e.kept ? fmtNum(e.tok.gin) : '–'), h('td', {className: 'n'}, e.kept ? fmtNum(e.tok.gout) : '–'), h('td', {className: 'n'}, e.kept ? money(e.tok, rate) : '–'),
        h('td', null, e.q ? qPill(e.q) : '–'),
        h('td', null, e.warn.map(w => h('span', {className: 'pill amber', key: w, style: {marginRight: 4}}, w)))))
        : h('tr', null, h('td', {colSpan: 17}, 'No video edits in this range'))))));
}


// ---------- the numbers behind every page ----------
function useModel(data, jobs, period, editor, rate, prices) {
  return useMemo(() => {
    const today = keyOf(new Date());
    const gem = (data.gemini && data.gemini.buckets) || [];
    const jobList = jobs || [];
    const dates = [];
    jobList.forEach(j => dates.push(dayKey(j.updated_at || j.created_at)));
    gem.forEach(b => dates.push(dayKey(b.t)));
    dates.sort();
    const from = period.from || dates[0] || today, to = period.to || today;
    const single = from === to;
    let slotKeys, slotLabels;
    if (single) { slotKeys = Array.from({length: 24}, (_, i) => i); slotLabels = slotKeys.map(hour12); }
    else { slotKeys = daysBetween(from, to).slice(-120); slotLabels = slotKeys.map(dayTiny); }
    const slotOf = t => { const d = new Date(n(t) * 1000), k = keyOf(d); if (k < from || k > to) return -1; return single ? d.getHours() : slotKeys.indexOf(k); };
    const ok = name => !editor || canon(name) === editor;
    // editors known anywhere
    const names = {};
    const see = (name) => { const c = canon(name); if (!names[c]) names[c] = nice(String(name || "unknown").trim()); return c; };
    (data.keys || []).forEach(k => see(k.name));
    jobList.forEach(j => see(j.editor_name));
    gem.forEach(b => see(b.u));
    // videos (a render job = one delivered video; the same video re-rendered counts once per slot)
    const vids = {};   // editor -> slot -> Set(video)
    const renderJobs = jobList.map(j => Object.assign({}, j, {c: see(j.editor_name), when: n(j.updated_at || j.created_at), video: videoName(j.video_name)}));
    const renderCount = {};
    renderJobs.forEach(j => { if (j.kind === "render") { const k = j.c + "|" + j.video; renderCount[k] = (renderCount[k] || 0) + 1; } });
    const inJobs = renderJobs.filter(j => ok(j.c) && slotOf(j.when) >= 0);
    const delivered = inJobs.filter(j => j.kind === "render");
    delivered.forEach(j => { const s = slotOf(j.when); ((vids[j.c] = vids[j.c] || {})[s] = (vids[j.c][s] || new Set())).add(j.video); });
    const videoList = (c, s) => (vids[c] && vids[c][s]) ? [...vids[c][s]] : [];
    const slotTips = slotKeys.map(k => single ? hour12(k) + " on " + dayShort(from) : dayShort(k));
    const videoSlots = c => slotKeys.map((_, s) => (vids[c] && vids[c][s]) ? vids[c][s].size : 0);
    const perEditor = {};
    const ed = c => perEditor[c] || (perEditor[c] = {c, name: names[c] || nice(c), videos: new Set(), days: new Set(), failed: 0, blocked: 0, checked: 0, calls: 0, bad: 0, tin: 0, tout: 0, usd: 0, unpriced: 0, models: {}, peak: 0, last: 0});
    delivered.forEach(j => { const e = ed(j.c); e.videos.add(j.video); e.days.add(dayKey(j.when)); });
    inJobs.forEach(j => { const e = ed(j.c); if (j.kind === "failed") e.failed += 1; else if (j.kind === "blocked") e.blocked += 1; else if (j.kind !== "render") e.checked += 1; });
    // gemini
    const gb = gem.filter(b => ok(canon(b.u)) && slotOf(b.t) >= 0);
    const gslots = slotKeys.map(() => ({calls: 0, bad: 0, tin: 0, tout: 0, usd: 0, byModel: {}}));
    const gmodels = {}, gdays = {};
    const price = m => { const p = prices[m]; return p && (p.i !== "" || p.o !== "") ? p : null; };
    let gcalls = 0, gbad = 0, gtin = 0, gtout = 0, gusd = 0, gunpriced = 0;
    gb.forEach(b => {
      const c = see(b.u), e = ed(c), m = b.m || "unknown", p = price(m);
      const cost = p ? (n(b.in) * n(p.i) + n(b.out) * n(p.o)) / 1e6 : null;
      e.calls += n(b.n); e.bad += n(b.bad); e.tin += n(b.in); e.tout += n(b.out); e.models[m] = (e.models[m] || 0) + n(b.n);
      const gm = gmodels[m] || (gmodels[m] = {m, calls: 0, bad: 0, tin: 0, tout: 0, usd: 0, unpriced: 0}); gm.calls += n(b.n); gm.bad += n(b.bad); gm.tin += n(b.in); gm.tout += n(b.out);
      const s = gslots[slotOf(b.t)]; s.calls += n(b.n); s.bad += n(b.bad); s.tin += n(b.in); s.tout += n(b.out); s.byModel[m] = (s.byModel[m] || 0) + n(b.n);
      const dk = dayKey(b.t), gd = gdays[dk] || (gdays[dk] = {day: dk, calls: 0, bad: 0, tin: 0, tout: 0, usd: 0, unpriced: 0, byModel: {}}); gd.calls += n(b.n); gd.bad += n(b.bad); gd.tin += n(b.in); gd.tout += n(b.out); gd.byModel[m] = (gd.byModel[m] || 0) + n(b.n);
      if (cost == null) { e.unpriced += n(b.n); gm.unpriced += n(b.n); gd.unpriced += n(b.n); gunpriced += n(b.n); } else { e.usd += cost; gm.usd += cost; gd.usd += cost; s.usd += cost; gusd += cost; }
      gcalls += n(b.n); gbad += n(b.bad); gtin += n(b.in); gtout += n(b.out);
    });
    ((data.gemini && data.gemini.users) || []).forEach(u => { const c = canon(u.name); if (!editor || c === editor) { const e = ed(see(u.name)); e.peak = Math.max(e.peak, n(u.peak_rpm)); e.last = Math.max(e.last, n(u.last)); } });
    // versions and agents
    const latest = data.latest_version || "";
    const keysBy = {};
    (data.keys || []).forEach(k => {
      const c = canon(k.name), cur = keysBy[c];
      if (!cur) keysBy[c] = {c, name: names[c], email: k.email, version: k.zeno_version || "", seen: n(k.version_seen_at), used: n(k.last_used_at), platform: k.client_platform || "", requests: n(k.requests), revoked: !!k.revoked_at};
      else {
        if (verCmp(k.zeno_version, cur.version) > 0 || (!cur.version && k.zeno_version)) { cur.version = k.zeno_version; cur.seen = n(k.version_seen_at); cur.platform = k.client_platform || cur.platform; }
        cur.used = Math.max(cur.used, n(k.last_used_at)); cur.requests += n(k.requests);
      }
    });
    const agents = {};
    (data.runs || []).forEach(r => { if (slotOf(r.created_at) < 0) return; const c = canon(r.user_email ? String(r.user_email).split("@")[0] : r.editor_name); const nm = canon(r.editor_name); const key = names[nm] ? nm : c; const a = agents[key] || (agents[key] = {}); const label = (r.agent_name || "agent") + (r.agent_model ? " / " + r.agent_model : ""); a[label] = (a[label] || 0) + 1; });
    return {from, to, single, slotKeys, slotLabels, slotOf, names, ok, delivered, inJobs, renderJobs, renderCount, videoSlots, videoList, slotTips, perEditor, ed, gslots, gmodels, gdays, gcalls, gbad, gtin, gtout, gusd, gunpriced, latest, keysBy, agents, price, today};
  }, [data, jobs, period.from, period.to, editor, rate, prices]);
}
const videoTipRows = (m, cs) => { const f = i => cs.map((c, k) => ({label: m.names[c] || c, color: COLORS[k % COLORS.length], count: m.videoList(c, i).length})).filter(r => r.count)
  .map(r => ({label: r.label, color: r.color, value: r.count + (r.count === 1 ? " video" : " videos")})); f.isVideos = true; return f; };
const editorList = m => Object.keys(m.names).sort((a, b) => (m.names[a] < m.names[b] ? -1 : 1)).map(k => ({key: k, name: m.names[k]}));

// ---------- pages ----------

// ---------- the coding agent (Claude Code, Codex, Antigravity): its own tokens, never mixed with Gemini ----------
const AGENT_COLORS = ["#7fb2ff", "#5fc3c8", "#d9a441"];
function AgentCard({m, runs, rate, onPick}) {
  const [metric, setMetric] = useState("tokens");
  const tot = aggregate(runs).total;
  const calls = runs.reduce((t, r) => t + n(((r.job_report || {}).agent || {}).turns), 0);
  const withCalls = runs.some(r => ((r.job_report || {}).agent || {}).turns != null);
  const slots = m.slotKeys.map(() => ({fresh: 0, cw: 0, out: 0, reread: 0, calls: 0, usd: 0}));
  runs.forEach(r => {
    const s = m.slotOf(n(r.created_at)); if (s < 0) return;
    const cw = cacheWriteTokens(r), x = slots[s], c = costUsd(r);
    x.cw += cw; x.fresh += Math.max(0, n(r.agent_in_tokens) - cw); x.out += n(r.agent_out_tokens); x.reread += n(r.agent_cached_tokens);
    x.calls += n(((r.job_report || {}).agent || {}).turns); x.usd += c === null ? 0 : c * rate;
  });
  const series = metric === "tokens" ? [{name: "Agent input (new)", color: AGENT_COLORS[0], vals: slots.map(x => x.fresh)}, {name: "Cache write", color: AGENT_COLORS[1], vals: slots.map(x => x.cw)}, {name: "Agent output", color: AGENT_COLORS[2], vals: slots.map(x => x.out)}]
    : metric === "reread" ? [{name: "Re-read (cache read)", color: AGENT_COLORS[1], vals: slots.map(x => x.reread)}]
    : metric === "calls" ? [{name: "Agent calls", color: AGENT_COLORS[0], vals: slots.map(x => x.calls)}]
    : [{name: "Agent cost (₹)", color: AGENT_COLORS[2], vals: slots.map(x => x.usd)}];
  const fmtY = v => metric === "cost" ? "₹" + (v >= 100 ? Math.round(v).toLocaleString() : v.toFixed(v >= 10 ? 0 : 1)) : fmtBig(v);
  const mini = (v, label) => h("div", {className: "mini", key: label}, h("b", null, v), h("span", null, label));
  return h(Section, {title: "Coding agent tokens " + (m.single ? "by hour" : "per day") + " (Claude Code, Codex, Antigravity)", span: 8,
    right: h(Seg, {value: metric, onChange: setMetric, options: [["tokens", "Input / output"], ["reread", "Re-read (cache)"], ["calls", "Calls"], ["cost", "Cost ₹"]]})},
    h("div", {className: "minis"}, mini(fmtBig(tot.ain), "Agent input"), mini(fmtBig(tot.cwrite), "of which cache write"), mini(fmtBig(tot.cached), "Re-read (cache read)"), mini(fmtBig(tot.aout), "Agent output"),
      mini(withCalls ? fmtNum(calls) : "–", "Agent calls"), mini(tot.usd || !tot.unpriced ? inr(tot.usd, rate) + (tot.unpriced ? " *" : "") : "–", "Est. agent cost")),
    h(Chart, {series, labels: m.slotLabels, kind: "bars", fmtY, height: 220, onPick, tips: m.slotTips, tipRows: i => { const x = slots[i]; return [
      {label: "Agent input (new)", color: AGENT_COLORS[0], value: fmtNum(x.fresh)}, {label: "Cache write", color: AGENT_COLORS[1], value: fmtNum(x.cw)}, {label: "Agent output", color: AGENT_COLORS[2], value: fmtNum(x.out)},
      {label: "Re-read (cache read)", color: "#9aa5b1", value: fmtNum(x.reread)}, {label: "Agent calls", color: "#9aa5b1", value: withCalls ? fmtNum(x.calls) : "–"}, {label: "Estimated cost", color: "#9aa5b1", value: "₹" + (x.usd >= 100 ? Math.round(x.usd).toLocaleString() : x.usd.toFixed(2))}]; }}), h(Legend, {series}));
}

function OverviewPage({m, rate, setTab, jobs, setPeriod, runs}) {
  const [split, setSplit] = useState("total");
  const drill = i => { if (!m.single && setPeriod) setPeriod({from: m.slotKeys[i], to: m.slotKeys[i]}); };
  const rows = Object.values(m.perEditor).filter(e => m.ok(e.c)).sort((a, b) => b.videos.size - a.videos.size || b.calls - a.calls);
  const active = rows.filter(e => e.videos.size > 0);
  const totalVideos = new Set(m.delivered.map(j => j.c + "|" + j.video)).size;
  const failed = m.inJobs.filter(j => j.kind === "failed" || j.kind === "blocked").length;
  const perDay = m.slotKeys.map((_, i) => 0);
  const seen = m.slotKeys.map(() => new Set());
  m.delivered.forEach(j => { const s = m.slotOf(j.when); seen[s].add(j.c + "|" + j.video); });
  seen.forEach((set, i) => { perDay[i] = set.size; });
  const spanDays = Math.max(1, m.single ? 1 : m.slotKeys.length);
  const status = (c) => { const k = m.keysBy[c]; if (!k || !k.version) return h(Pill, {kind: "gray"}, "Unknown"); return verCmp(k.version, m.latest) >= 0 || !m.latest ? h(Pill, {kind: "ok"}, "Up to date") : h(Pill, {kind: "amber"}, "Behind " + k.version); };
  const byEditor = active.map((e, i) => ({name: e.name, value: e.videos.size, label: fmtNum(e.videos.size), color: COLORS[i % COLORS.length]}));
  const gmods = [...MODELS_IN_USE, ...Object.keys(m.gmodels).filter(x => !MODELS_IN_USE.includes(x))].filter(x => m.gmodels[x]);
  const gseries = gmods.map((mm, i) => ({name: mm, color: COLORS[i % COLORS.length], vals: m.gslots.map(x => x.byModel[mm] || 0)}));
  const bymodel = Object.values(m.gmodels).sort((a, b) => b.usd - a.usd || b.calls - a.calls).map((x, i) => ({name: x.m, value: x.usd || x.calls, label: x.unpriced && !x.usd ? fmtNum(x.calls) + " calls" : rupees(x.usd, rate), color: COLORS[(i + 2) % COLORS.length]}));
  return h("div", {className: "dash"},
    h(Stat, {value: fmtNum(totalVideos), label: "Videos delivered"}), h(Stat, {value: fmtNum(active.length), label: "Editors who delivered"}),
    h(Stat, {value: active.length ? (totalVideos / active.length / spanDays).toFixed(1) : "0", label: "Videos per editor per day"}),
    h(Stat, {value: fmtNum(failed), label: "Failed or blocked jobs"}), h(Stat, {value: fmtNum(m.gcalls), label: "Gemini calls"}),
    h(Stat, {value: m.gunpriced && !m.gusd ? "Set prices" : rupees(m.gusd, rate), label: "Gemini spend"}),
    h(Section, {title: "Videos " + (m.single ? "by hour" : "per day"), span: 8, right: h(Seg, {value: split, onChange: setSplit, options: [["total", "Total"], ["editor", "By editor"]]})},
      jobs === null ? h(Empty, {text: "Loading…"}) : h("div", null,
        h(Chart, {series: split === "editor" ? active.slice(0, 8).map((e, i) => ({name: e.name, color: COLORS[i % COLORS.length], vals: m.videoSlots(e.c)})) : [{name: "Videos", color: COLORS[0], vals: perDay}], labels: m.slotLabels, kind: "bars", height: 240, onPick: drill, tips: m.slotTips, tipRows: videoTipRows(m, active.slice(0, 8).map(e => e.c))}),
        split === "editor" ? h(Legend, {series: active.slice(0, 8).map((e, i) => ({name: e.name, color: COLORS[i % COLORS.length]}))}) : null,
        !m.single ? h("p", {className: "note"}, "Click a bar to see that day hour by hour.") : null)),
    h(Section, {title: "Videos by editor", span: 4}, jobs === null ? h(Empty, {text: "Loading…"}) : h(BarList, {items: byEditor, empty: "No videos in this range."})),
    h(AgentCard, {m, runs: runs || [], rate, onPick: drill}),
    h(Section, {title: "Gemini (transcription) spend by model", span: 4}, h(BarList, {items: bymodel, empty: "No Gemini calls in this range."})),
    h(Section, {title: "Editors"},
      rows.length ? h("div", {className: "scroll"}, h("table", null,
        h("thead", null, h("tr", null, h(Th, null, "Editor"), h(Th, {right: true}, "Videos"), h(Th, {right: true}, "Days active"), h(Th, {right: true}, "Avg / day"), h(Th, {right: true}, "Failed / blocked"), h(Th, {right: true}, "Gemini calls"), h(Th, {right: true}, "Gemini spend"), h(Th, null, "Version"))),
        h("tbody", null, rows.map(e => h("tr", {key: e.c},
          h("td", null, h("b", null, e.name)), h("td", {className: "n"}, fmtNum(e.videos.size)), h("td", {className: "n"}, fmtNum(e.days.size)),
          h("td", {className: "n"}, e.days.size ? (e.videos.size / e.days.size).toFixed(1) : "0"), h("td", {className: "n"}, (e.failed + e.blocked) ? h("b", null, fmtNum(e.failed + e.blocked)) : "0"),
          h("td", {className: "n"}, fmtNum(e.calls)), h("td", {className: "n"}, e.unpriced && !e.usd ? "—" : rupees(e.usd, rate) + (e.unpriced ? " *" : "")), h("td", null, status(e.c))))))) : h(Empty)));
}

function VideosPage({m, jobs, setPeriod}) {
  const [pick, setPick] = useState(null);
  if (jobs === null) return h(Empty, {text: "Loading the job log…"});
  const eds = Object.values(m.perEditor).filter(e => m.ok(e.c) && e.videos.size > 0).sort((a, b) => b.videos.size - a.videos.size);
  const series = eds.slice(0, 8).map((e, i) => ({name: e.name, color: COLORS[i % COLORS.length], vals: m.videoSlots(e.c)}));
  const max = Math.max(1, ...eds.flatMap(e => m.videoSlots(e.c)));
  const picked = pick ? m.delivered.filter(j => j.c === pick.c && m.slotOf(j.when) === pick.s).sort((a, b) => a.when - b.when) : [];
  return h("div", {className: "dash"},
    h(Section, {title: "Videos by editor"},
      h(Chart, {series, labels: m.slotLabels, kind: "bars", tips: m.slotTips, tipRows: videoTipRows(m, eds.slice(0, 8).map(e => e.c)), onPick: i => { if (!m.single && setPeriod) setPeriod({from: m.slotKeys[i], to: m.slotKeys[i]}); }}), h(Legend, {series})),
    h(Section, {title: "Day by day (click a number for the videos)"},
      eds.length ? h("div", {className: "scroll"}, h("table", {className: "grid"},
        h("thead", null, h("tr", null, h(Th, null, "Editor"), m.slotLabels.map((l, i) => h(Th, {right: true, key: i}, l)), h(Th, {right: true}, "Total"))),
        h("tbody", null, eds.map(e => { const vals = m.videoSlots(e.c); return h("tr", {key: e.c},
          h("td", null, h("b", null, e.name)),
          vals.map((v, s) => h("td", {key: s, className: "n cell" + (pick && pick.c === e.c && pick.s === s ? " sel" : ""), style: v ? {background: "rgba(127,178,255," + (0.12 + 0.5 * v / max).toFixed(2) + ")", cursor: "pointer"} : null, onClick: v ? () => setPick({c: e.c, s}) : undefined}, v || "")),
          h("td", {className: "n"}, h("b", null, fmtNum(e.videos.size)))); })))) : h(Empty)),
    pick ? h(Section, {title: (m.names[pick.c] || pick.c) + " · " + m.slotLabels[pick.s], right: h("button", {className: "chip", onClick: () => setPick(null)}, "Close")},
      h("div", {className: "scroll"}, h("table", null, h("thead", null, h("tr", null, h(Th, null, "When"), h(Th, null, "Video"), h(Th, null, "Zeno"))),
        h("tbody", null, picked.map(j => h("tr", {key: j.id}, h("td", {className: "muted"}, fmtWhen(j.when)), h("td", null, short(j.video, 90)), h("td", {className: "muted"}, j.zeno_version || ""))))))) : null);
}

function GeminiPage({m, rate, prices, setPrices, setPeriod}) {
  const [metric, setMetric] = useState("calls");
  const kind = "bars";
  const setPrice = (mod, k, v) => { const next = Object.assign({}, prices, {[mod]: Object.assign({}, prices[mod], {[k]: v})}); setPrices(next); try { localStorage.setItem(PRICE_KEY, JSON.stringify(next)); } catch (e) {} };
  const gm = Object.values(m.gmodels).sort((a, b) => b.calls - a.calls);
  const older = gm.filter(x => !MODELS_IN_USE.includes(x.m)).map(x => x.m);
  const val = s => metric === "calls" ? s.calls : metric === "tokens" ? s.tin + s.tout : s.usd * rate;
  const fmtY = v => metric === "spend" ? "₹" + (v >= 100 ? Math.round(v).toLocaleString() : v.toFixed(v >= 10 ? 0 : 1)) : fmtBig(v);
  const modelSeries = [...MODELS_IN_USE, ...older].filter(mm => m.gmodels[mm]);
  let series;
  if (metric === "calls") series = modelSeries.map((mm, i) => ({name: mm, color: COLORS[i % COLORS.length], vals: m.gslots.map(s => s.byModel[mm] || 0)}));
  else series = [{name: metric === "calls" ? "Calls" : metric === "tokens" ? "Tokens" : "Spend (₹)", color: COLORS[0], vals: m.gslots.map(val)}];
  const days = Object.values(m.gdays).sort((a, b) => (a.day < b.day ? 1 : -1));
  const eds = Object.values(m.perEditor).filter(e => m.ok(e.c) && e.calls > 0).sort((a, b) => b.calls - a.calls);
  const spend = (usd, unpriced) => unpriced && !usd ? "No price" : rupees(usd, rate) + (unpriced ? " *" : "");
  const peakAll = Math.max(0, ...Object.values(m.perEditor).filter(e => m.ok(e.c)).map(e => e.peak));
  return h("div", {className: "dash"},
    h(React.Fragment, null, h(Stat, {value: fmtNum(m.gcalls), label: "calls"}), h(Stat, {value: m.gcalls ? Math.round(100 * (m.gcalls - m.gbad) / m.gcalls) + "%" : "100%", label: "answered fully"}),
      h(Stat, {value: fmtBig(m.gtin), label: "tokens in"}), h(Stat, {value: fmtBig(m.gtout), label: "tokens out"}), h(Stat, {value: m.gunpriced && !m.gusd ? "Set prices" : rupees(m.gusd, rate), label: "estimated spend"}), h(Stat, {value: peakAll + " / min", label: "busiest minute"})),
    h(Section, {title: "Usage " + (m.single ? "by hour" : "per day") + (metric === "calls" ? " by model" : ""),
      right: [h(Seg, {key: "m", value: metric, onChange: setMetric, options: [["calls", "Calls"], ["tokens", "Tokens"], ["spend", "Spend ₹"]]})]},
      h(Chart, {series, labels: m.slotLabels, kind: "bars", fmtY, tips: m.slotTips, tipRows: i => { const x = m.gslots[i]; const rows = modelSeries.filter(mm => x.byModel[mm]).map((mm, k) => ({label: mm, color: COLORS[k % COLORS.length], value: fmtNum(x.byModel[mm]) + " calls"}));
      return rows.concat([{label: "Failed calls", color: "#d4574b", value: fmtNum(x.bad)}, {label: "Tokens in / out", color: "#9aa5b1", value: fmtBig(x.tin) + " / " + fmtBig(x.tout)}, {label: "Spend", color: "#9aa5b1", value: x.usd ? rupees(x.usd, rate) : "—"}]); },
      onPick: i => { if (!m.single && setPeriod) setPeriod({from: m.slotKeys[i], to: m.slotKeys[i]}); }}), h(Legend, {series})),
    h(Section, {title: "Models and prices (USD per million tokens, audio input)"},
      h("div", {className: "scroll"}, h("table", null,
        h("thead", null, h("tr", null, h(Th, null, "Model"), h(Th, {right: true}, "Input $ / 1M"), h(Th, {right: true}, "Output $ / 1M"), h(Th, {right: true}, "Input ₹ / 1M"), h(Th, {right: true}, "Output ₹ / 1M"), h(Th, {right: true}, "Calls"), h(Th, {right: true}, "Share"), h(Th, {right: true}, "Tokens in"), h(Th, {right: true}, "Tokens out"), h(Th, {right: true}, "Spend"))),
        h("tbody", null, MODELS_IN_USE.map(mm => { const x = m.gmodels[mm] || {calls: 0, tin: 0, tout: 0, usd: 0, unpriced: 0}; const p = prices[mm] || {};
          return h("tr", {key: mm}, h("td", null, h("b", null, mm)),
            ["i", "o"].map(k => h("td", {className: "n", key: k}, h("input", {type: "number", step: "0.01", className: "dt", style: {width: 84}, value: p[k] == null ? "" : p[k], onChange: e => setPrice(mm, k, e.target.value)}))),
            h("td", {className: "n"}, p.i === "" || p.i == null ? "—" : "₹" + (n(p.i) * rate).toFixed(2)), h("td", {className: "n"}, p.o === "" || p.o == null ? "—" : "₹" + (n(p.o) * rate).toFixed(2)),
            h("td", {className: "n"}, fmtNum(x.calls)), h("td", {className: "n"}, m.gcalls ? Math.round(100 * x.calls / m.gcalls) + "%" : "0%"), h("td", {className: "n"}, fmtBig(x.tin)), h("td", {className: "n"}, fmtBig(x.tout)), h("td", {className: "n"}, spend(x.usd, x.unpriced))); })))),
      older.length ? h("p", {className: "note"}, "Older records also hold calls from models Zeno no longer uses (" + older.join(", ") + "). They are in the totals but have no price, so they are left out of the spend (*).") : null),
    h(Section, {title: "By editor"},
      eds.length ? h("div", {className: "scroll"}, h("table", null,
        h("thead", null, h("tr", null, h(Th, null, "Editor"), h(Th, {right: true}, "Calls"), h(Th, null, "Models used"), h(Th, {right: true}, "Busiest minute"), h(Th, {right: true}, "Tokens in"), h(Th, {right: true}, "Tokens out"), h(Th, {right: true}, "Failed"), h(Th, {right: true}, "Spend"), h(Th, null, "Last call"))),
        h("tbody", null, eds.map(e => h("tr", {key: e.c}, h("td", null, h("b", null, e.name)), h("td", {className: "n"}, fmtNum(e.calls)),
          h("td", null, Object.keys(e.models).map(mm => h("span", {className: "tag", key: mm}, mm + " " + e.models[mm]))),
          h("td", {className: "n"}, e.peak + " / min"), h("td", {className: "n"}, fmtBig(e.tin)), h("td", {className: "n"}, fmtBig(e.tout)), h("td", {className: "n"}, e.bad ? h("b", null, fmtNum(e.bad)) : "0"), h("td", {className: "n"}, spend(e.usd, e.unpriced)), h("td", {className: "muted"}, fmtWhen(e.last))))))) : h(Empty)),
    h(Section, {title: "By day"},
      days.length ? h("div", {className: "scroll"}, h("table", null,
        h("thead", null, h("tr", null, h(Th, null, "Day"), modelSeries.map(mm => h(Th, {right: true, key: mm}, mm)), h(Th, {right: true}, "Calls"), h(Th, {right: true}, "Failed"), h(Th, {right: true}, "Tokens"), h(Th, {right: true}, "Spend"))),
        h("tbody", null, days.map(d => h("tr", {key: d.day}, h("td", null, h("b", null, dayShort(d.day))), modelSeries.map(mm => h("td", {className: "n", key: mm}, d.byModel[mm] ? fmtNum(d.byModel[mm]) : "")),
          h("td", {className: "n"}, fmtNum(d.calls)), h("td", {className: "n"}, d.bad ? fmtNum(d.bad) : "0"), h("td", {className: "n"}, fmtBig(d.tin + d.tout)), h("td", {className: "n"}, spend(d.usd, d.unpriced))))))) : h(Empty)));
}

function EditorsPage({m}) {
  const rows = Object.keys(m.names).filter(c => m.ok(c)).map(c => {
    const k = m.keysBy[c] || {}, e = m.perEditor[c];
    const behind = k.version && m.latest && verCmp(k.version, m.latest) < 0;
    return {c, name: m.names[c], email: k.email || "", version: k.version || "", seen: k.seen || 0, used: k.used || 0, platform: k.platform || "", behind, videos: e ? e.videos.size : 0, gemini: e ? e.models : {}, agents: m.agents[c] || {}};
  }).sort((a, b) => (b.behind - a.behind) || (b.used - a.used));
  const upTo = rows.filter(r => r.version && !r.behind).length, behind = rows.filter(r => r.behind).length, unknown = rows.filter(r => !r.version).length;
  return h("div", {className: "dash"},
    h(React.Fragment, null, h(Stat, {span: 3, value: m.latest || "—", label: "Latest published version"}), h(Stat, {span: 3, value: fmtNum(upTo), label: "Editors up to date"}), h(Stat, {span: 3, value: fmtNum(behind), label: "Editors behind"}), h(Stat, {span: 3, value: fmtNum(unknown), label: "Version unknown"})),
    h(Section, {title: "Editors, updates and models"},
      rows.length ? h("div", {className: "scroll"}, h("table", null,
        h("thead", null, h("tr", null, h(Th, null, "Editor"), h(Th, null, "Email"), h(Th, null, "Zeno version"), h(Th, null, "Update"), h(Th, null, "Last used"), h(Th, null, "Computer"), h(Th, {right: true}, "Videos"), h(Th, null, "Coding agent / model"), h(Th, null, "Gemini models"))),
        h("tbody", null, rows.map(r => h("tr", {key: r.c},
          h("td", null, h("b", null, r.name)), h("td", {className: "mail"}, r.email), h("td", null, r.version || "—"),
          h("td", null, !r.version ? h(Pill, {kind: "gray"}, "Unknown") : r.behind ? h(Pill, {kind: "amber"}, "Not updated (latest " + m.latest + ")") : h(Pill, {kind: "ok"}, "Received " + m.latest)),
          h("td", {className: "muted"}, r.used ? fmtWhen(r.used) : ""), h("td", {className: "muted"}, r.platform), h("td", {className: "n"}, fmtNum(r.videos)),
          h("td", null, Object.keys(r.agents).map(a => h("span", {className: "tag", key: a}, a + " ×" + r.agents[a]))),
          h("td", null, Object.keys(r.gemini).map(g => h("span", {className: "tag", key: g}, g + " " + r.gemini[g])))))))) : h(Empty)));
}

// ---------- sign in and the page ----------
function SignIn({message, onToken}) {
  const box = useRef(null);
  const [note, setNote] = useState(message || "Use your company Google account.");
  useEffect(() => {
    let dead = false;
    (async () => {
      try {
        const cfg = await (await fetch("/api/auth/config")).json();
        if (!cfg.google_client_id) { setNote("Google sign-in is not set up on this server."); return; }
        if (!(window.google && google.accounts && google.accounts.id)) {
          await new Promise((res, rej) => { const s = document.createElement("script"); s.src = "https://accounts.google.com/gsi/client"; s.onload = res; s.onerror = rej; document.head.appendChild(s); });
        }
        if (dead) return;
        google.accounts.id.initialize({client_id: cfg.google_client_id, callback: resp => onToken(resp.credential)});
        google.accounts.id.renderButton(box.current, {theme: "filled_blue", size: "large", text: "signin_with"});
      } catch (e) { setNote("Could not start Google sign-in: " + e.message); }
    })();
    return () => { dead = true; };
  }, []);
  return h("div", {className: "signin"}, h("h1", null, "Sign in to see the usage log"), h("p", {className: "sub"}, note), h("div", {ref: box, style: {display: "inline-block"}}));
}

const Wrap = ({children}) => h("div", {className: "dash"}, h("section", {className: "c s12 legacy"}, children));
function Dashboard({data, jobs, jobsMsg, tab, setTab, period, setPeriod, editor, setEditor, rate, setRate, onRefresh, busy, health}) {
  const [prices, setPrices] = useState(loadPrices());
  const [showAll, setShowAll] = useState(false);
  const [day, setDay] = useState("");
  const [q, setQ] = useState("");
  const [includeDev, setIncludeDev] = useState(false);
  const m = useModel(data, jobs, period, editor, rate, prices);
  const cur = TABS.some(t => t[0] === tab) ? tab : "overview";
  const cutoff = {from: period.from ? new Date(period.from + "T00:00:00").getTime() / 1000 : 0, to: period.to ? new Date(period.to + "T23:59:59").getTime() / 1000 : 0};
  const runs = (data.runs || []).filter(r => inPeriod(r, cutoff) && (!editor || canon(r.editor_name) === editor || canon(String(r.user_email || "").split("@")[0]) === editor)).sort((a, b) => n(b.created_at) - n(a.created_at));
  const isDevRun = r => !!(((r.job_report || {}).agent || {}).mixed_with_development);
  const devCount = runs.filter(isDevRun).length;
  const cleanRuns = useMemo(() => {
    const base = includeDev ? runs : runs.filter(r => !isDevRun(r));
    const byKey = new Map();
    base.forEach(r => {
      const k = who(r) + "|" + (r.project || r.id);
      if (!byKey.has(k)) byKey.set(k, []);
      byKey.get(k).push(r);
    });
    return [...byKey.values()].flatMap(clusterRuns).sort((a, b) => n(b.created_at) - n(a.created_at));
  }, [runs, includeDev]);
  const rrRuns = runs.filter(r => matches(r, q));
  const vers = versionRows(data.keys || []);
  const download = () => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([csvOf(runs)], {type: "text/csv"})); a.download = "zeno-usage.csv"; a.click(); };
  return h("div", null,
    h("div", {className: "tabs"}, TABS.map(([id, label]) => h("button", {key: id, className: "tab" + (cur === id ? " on" : ""), onClick: () => setTab(id)}, label))),
    h(Controls, {period, setPeriod, editors: editorList(m), editor, setEditor, rate, setRate, onRefresh, busy, includeDev, setIncludeDev, devCount}),
    jobsMsg ? h("p", {className: "note"}, jobsMsg) : null,
    cur === "overview" ? h(OverviewPage, {m, rate, setTab, jobs, setPeriod, runs: cleanRuns})
      : cur === "videos" ? h("div", null, h(VideosPage, {m, jobs, setPeriod}), jobs === null ? null : h("div", {style: {height: 14}}), jobs === null ? null : h(Wrap, null, h(EditsView, {data, jobs, jobsMsg, rg: cutoff, q: "", rate, day, setDay})))
      : cur === "renders" ? h("div", null,
          h("div", {className: "bar", style: {marginTop: 4}},
            h("input", {className: "grow", placeholder: "Search an editor, project or language", value: q, onChange: e => setQ(e.target.value)}),
            h("button", {className: "chip", onClick: download}, "Download CSV")),
          h(Videos, {runs: rrRuns, rate, showAll, setShowAll, jobs, data}))
      : cur === "gemini" ? h(GeminiPage, {m, rate, prices, setPrices, setPeriod})
      : cur === "tokens" ? h(Wrap, null, h("div", {className: "bar"}, h("button", {className: "chip", onClick: download}, "Download CSV")), h(Editors, {runs: cleanRuns, rate, vers}), h(Daily, {runs: cleanRuns, rate}))
      : cur === "editors" ? h(EditorsPage, {m})
      : h("div", null, h(Wrap, null, h(Health, {health, vers})), h("div", {style: {height: 14}}), h(Wrap, null, h(Problems, {data, runs, cutoff})), h("div", {style: {height: 14}}), h(Wrap, null, h(Notes, {runs, data}))),
    h("p", {className: "foot"}, "Local time. A video counts once per day however often it was re-rendered. * = some calls use a model with no price. Costs are estimates from list prices."));
}

function App() {
  const read = k => { try { return localStorage.getItem(k) || ""; } catch (e) { return ""; } };
  const [token, setToken] = useState(read(TOKEN_KEY));
  const [data, setData] = useState(null);
  const [jobs, setJobs] = useState(null);
  const [jobsMsg, setJobsMsg] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState(null);
  const [reload, setReload] = useState(0);
  const [tab, setTabState] = useState(() => { const hsh = (location.hash || "").replace("#", ""); return TABS.some(t => t[0] === hsh) ? hsh : "overview"; });  // opens on Overview unless the link names a tab
  const setTab = t => { setTabState(t); try { location.hash = t; } catch (e) {} };
  const [period, setPeriod] = useState(() => { const p = presets()[2]; return {from: p[1], to: p[2]}; });
  const [editor, setEditor] = useState("");
  const [rate, setRate] = useState(() => { try { return Number(localStorage.getItem("zeno_rate")) || 94.8; } catch (e) { return 94.8; } });
  useEffect(() => { try { localStorage.setItem("zeno_rate", String(rate)); } catch (e) {} }, [rate]);
  useEffect(() => { fetch("/health").then(r => r.json()).then(setHealth).catch(() => setHealth({ok: false})); }, []);
  useEffect(() => {
    if (!token) { setData(null); return; }
    const hdr = {headers: {Authorization: "Bearer " + token}};
    setBusy(true);
    const jobsReq = fetch("/api/jobs", hdr);   // asked at the same time as the numbers
    fetch("/api/usage" + (reload ? "?fresh=1" : ""), hdr).then(async r => {
      if (r.status === 401 || r.status === 403) {
        try { localStorage.removeItem(TOKEN_KEY); } catch (e) {}
        setMsg(r.status === 403 ? "That account is not allowed. Use your company Google account." : "Please sign in again.");
        setToken(""); setBusy(false); return;
      }
      if (!r.ok) throw new Error("Could not load (" + r.status + ").");
      setData(await r.json()); setMsg(""); setBusy(false);
      jobsReq.then(async j => { if (j.ok) { setJobs((await j.json()).jobs || []); setJobsMsg(""); } else setJobsMsg("The job log is not available on this server yet (status " + j.status + ")."); })
        .catch(e => setJobsMsg("Could not load the job log: " + e.message));
    }).catch(e => { setMsg(e.message); setBusy(false); });
  }, [token, reload]);
  const onToken = async t => {  // trade the one-hour Google sign-in for a 30-day session
    let keep = t;
    try { const r = await fetch("/api/usage/session", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({credential: t})}); if (r.ok) keep = (await r.json()).token || t; } catch (e) {}
    try { localStorage.setItem(TOKEN_KEY, keep); } catch (e) {}
    setToken(keep);
  };
  return h("main", null,
    h("div", {className: "top"}, h("div", {className: "brand"}, "Zeno · Usage"), h("span", {className: "sp"}),
      data ? h("span", {className: "muted"}, "Updated " + fmtWhen(data.now)) : null, h("a", {className: "chip", href: "/"}, "Zeno home")),
    data ? h(Dashboard, {data, jobs, jobsMsg, health, tab, setTab, period, setPeriod, editor, setEditor, rate, setRate, busy, onRefresh: () => setReload(reload + 1)})
         : token ? h("div", {className: "card"}, h("h2", null, "Loading…"), h("p", {className: "sub"}, msg)) : h(SignIn, {message: msg, onToken}));
}
if (typeof document !== "undefined" && document.getElementById("root")) ReactDOM.createRoot(document.getElementById("root")).render(h(App));
</script></body></html>
""".replace("__STYLE__", NOTION_STYLE)
