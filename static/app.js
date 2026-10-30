const $ = (id) => document.getElementById(id);
let state = null, readback = null, busy = false, roster, log, studio;

async function api(path, options = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body));
  return body;
}

function banner(text, error = false) {
  const b = $("banner");
  b.textContent = text; b.hidden = !text; b.className = error ? "error" : "";
}

function agent(id) { return state.agents.find((a) => a.id === id); }
function buy(id) { return state.purchases.find((p) => p.agent_id === id); }
function denied(id) { return state.gateway.some((g) => g.agent_id === id && g.outcome === "denied"); }
function lastRun(id) { return state.runs.find((r) => r.agent_id === id); }

const STEPS = [
  { title: "Supplies agent reorders beans", note: "It runs on its own brief.", go: () => api("/api/run/supplies", { method: "POST" }),
    done: (run) => brainNote(run) },
  { title: "Goods arrive", note: "The delivery is checked against the stock sheet.", go: () => api(`/api/receive/${buy("supplies").capture_id}`, { method: "POST" }),
    done: (r) => (r.matched ? "Stock matched, so no return." : r.ticket.reason) },
  { title: "Refunds agent returns the stock", note: "A refund needs an open return ticket.", go: () => api("/api/refund-open-ticket", { method: "POST" }),
    done: (run) => brainNote(run) },
  { title: "Check PayPal", note: "Scores move only on PayPal readback.", go: () => api("/api/reconcile", { method: "POST" }),
    done: (r) => `${r.changes.length} verified change(s) applied.` },
  { title: "Fired agent retries directly", note: "It skips discovery and calls the gateway.", go: () => api("/api/agents/supplies/retry", { method: "POST" }),
    done: (r) => `Gateway answered ${r.http}: ${r.body.detail?.denied || "allowed"}` },
  { title: "Ads agent buys a placement", note: "The placement costs more than its limit.", go: () => api("/api/run/ads", { method: "POST" }),
    done: (run) => brainNote(run) },
  { title: "Raise the ads limit in the grid", note: "Edit the ads agent's limit, then come back here.", go: null },
  { title: "Ads agent tries again", note: "It clears at the new limit.", go: () => api("/api/run/ads", { method: "POST" }),
    done: (run) => brainNote(run) },
  { title: "Hand the reorder to the refunds agent", note: "A one-time grant for the right stock only.", go: () => api("/api/handover/supplies", { method: "POST" }),
    done: (run) => brainNote(run) },
  { title: "Read the end state from PayPal", note: "Live from the sandbox.", go: async () => {
      await api("/api/reconcile", { method: "POST" });
      return (readback = await api("/api/readback"));
    },
    done: (r) => `${r.captures.length} capture(s) read back.` },
  { title: "Review and reinstate", note: "Your decision is logged with the evidence.", go: async () => {
      const reason = prompt("Why reinstate the supplies agent?", "The brief was stale. Back on demoted terms.");
      if (!reason) throw new Error("Reinstatement cancelled: a reason is required.");
      return api("/api/agents/supplies/reinstate", { method: "POST", body: JSON.stringify({ reason }) });
    }, done: () => "Reinstated on demoted terms." },
];

function brainNote(run) {
  const said = run.outcome ? `Outcome: ${run.outcome}.` : run.final;
  return run.brain === "model" ? `Model run. ${said}` : `Scripted fallback ran: model unavailable (${run.model_error}). ${said}`;
}

function current() {
  const s = agent("supplies"), a = agent("ads");
  if (!buy("supplies")) return 0;
  if (!state.tickets.length) return 1;
  if (state.tickets.some((t) => t.state === "open")) return 2;
  if (s.tier !== "fired" && !state.decisions.some((d) => d.action === "reinstate")) return 3;
  if (!state.gateway.some((g) => g.agent_id === "supplies" && g.detail.includes("tier fired"))) return 4;
  if (!denied("ads") && !buy("ads")) return 5;
  if (!buy("ads")) return a.limit_usd >= 60 ? 7 : 6;
  if (!buy("refunds")) return 8;
  if (!readback) return 9;
  return state.decisions.some((d) => d.action === "reinstate") ? 11 : 10;
}

function renderSteps() {
  const now = current(), ol = $("steps");
  ol.replaceChildren(...STEPS.map((step, i) => {
    const li = document.createElement("li");
    li.className = `step ${i < now ? "done" : i === now ? "now" : ""}`;
    const b = document.createElement("b"); b.textContent = `${i + 1}. ${step.title}`;
    const small = document.createElement("small"); small.textContent = step.note;
    li.append(b, small);
    if (i === now && step.go) {
      const btn = document.createElement("button");
      btn.textContent = "Run this step"; btn.disabled = busy; btn.onclick = () => runStep(step);
      li.append(btn);
    }
    return li;
  }));
}

async function runStep(step) {
  busy = true; renderSteps(); banner("Working...");
  try { const r = await step.go(); banner(step.done(r)); }
  catch (e) { banner(e.message, true); }
  busy = false; await refresh();
}

function renderTools() {
  $("tools").replaceChildren(...state.agents.map((a) => {
    const card = document.createElement("div");
    card.className = `toolcard ${a.tier === "fired" ? "fired" : ""}`;
    const h = document.createElement("h3"); h.textContent = `${a.name}: tools at discovery`;
    card.append(h);
    a.tools.forEach((t) => {
      const c = document.createElement("span");
      c.className = `tool ${a.paypal_tools.includes(t) ? "" : "other"}`; c.textContent = t; card.append(c);
    });
    if (!a.paypal_tools.length) {
      const n = document.createElement("div"); n.className = "none"; n.textContent = "No PayPal tools."; card.append(n);
    }
    return card;
  }));
}

function renderEvidence() {
  const rows = [...state.events.map((e) => ({ when: e.created, text: `${e.agent_id}: ${e.kind.replaceAll("_", " ")} (${e.delta > 0 ? "+" : ""}${e.delta}), now ${e.tier_after}`, id: e.txn_id, src: `${e.source}. ${e.detail}` })),
    ...state.decisions.map((d) => ({ when: d.created, text: `Owner: ${d.action.replace("_", " ")} on ${d.agent_id}`, id: d.evidence, src: d.reason }))]
    .sort((a, b) => b.when - a.when);
  if (readback) rows.unshift(...readback.captures.map((c) => ({ when: 0, text: `PayPal says ${c.paypal_status}: ${c.agent_id} bought ${c.sku} for $${c.amount_usd.toFixed(2)}`, id: c.refund_id ? `${c.capture_id} refund ${c.refund_id}` : c.capture_id, src: readback.source })));
  if (!rows.length) { const e = document.createElement("div"); e.className = "empty"; e.textContent = "No verified outcomes yet."; $("evidence").replaceChildren(e); return; }
  $("evidence").replaceChildren(...rows.map((r) => {
    const d = document.createElement("div"); d.className = "row";
    const t = document.createElement("div"); t.textContent = r.text;
    const c = document.createElement("code"); c.textContent = r.id || "";
    const s = document.createElement("div"); s.className = "src"; s.textContent = r.src;
    d.append(t, c, s); return d;
  }));
}

function money(p) { return p.value == null ? "" : `$${Number(p.value).toFixed(2)}`; }

function makeGrids() {
  const G = agGrid;
  G.ModuleRegistry.registerModules([G.AllCommunityModule]);
  roster = G.createGrid($("roster"), {
    getRowId: (p) => p.data.id, domLayout: "normal", singleClickEdit: true, stopEditingWhenCellsLoseFocus: true,
    defaultColDef: { sortable: false, resizable: true },
    columnDefs: [
      { field: "name", headerName: "Agent", minWidth: 140 },
      { field: "tier", headerName: "Status", width: 100, valueFormatter: (p) => p.value[0].toUpperCase() + p.value.slice(1), cellClass: (p) => `tier-${p.value}` },
      { field: "score", width: 80 },
      { field: "limit_usd", headerName: "Limit", width: 100, valueFormatter: money, editable: (p) => p.data.tier !== "fired" },
      { field: "review_over_usd", headerName: "Review over", width: 110, valueFormatter: money, editable: (p) => p.data.tier !== "fired" },
      { field: "last_txn", headerName: "Transaction ID", flex: 1, minWidth: 180 },
    ],
    onCellValueChanged: async (e) => {
      try {
        await api(`/api/agents/${e.data.id}`, { method: "PATCH", body: JSON.stringify({ [e.colDef.field]: Number(e.newValue) }) });
        banner(`${e.data.name}: ${e.colDef.headerName} is now $${Number(e.newValue).toFixed(2)}.`);
      } catch (err) { banner(err.message, true); }
      refresh();
    },
  });
  log = G.createGrid($("log"), {
    getRowId: (p) => String(p.data.id), defaultColDef: { resizable: true, sortable: true, filter: true },
    overlayNoRowsTemplate: "No gateway calls yet.",
    columnDefs: [{ field: "id", width: 80 }, { field: "agent_id", headerName: "Agent", width: 110 }, { field: "tool", width: 190 },
      { field: "outcome", width: 110 }, { field: "http", width: 80 }, { field: "detail", flex: 1 }],
  });
}

function studioData() {
  return { sources: [
    { id: "agents", data: state.agents.map((a) => ({ Agent: a.name, Status: a.tier, Score: a.score, Limit: a.limit_usd, "PayPal tools": a.paypal_tools.length })) },
    { id: "purchases", data: state.purchases.map((p) => ({ Agent: p.agent_id, SKU: p.sku, Amount: p.amount_usd, "PayPal status": p.paypal_status })) },
  ] };
}

const STUDIO_STATE = { selectedPageId: "views", pages: [
  { id: "views", name: "Risk and spend", widgets: {
      r1: { type: "grid", dataMapping: { cols: [{ id: "agents.Agent" }, { id: "agents.Status" }, { id: "agents.Score" }, { id: "agents.Limit" }, { id: "agents.PayPal tools" }] } },
      r2: { type: "column-chart-grouped", dataMapping: { categoryKey: [{ id: "agents.Agent" }], valueKey: [{ id: "agents.Score", aggregation: "sum" }] } },
      s1: { type: "column-chart-grouped", dataMapping: { categoryKey: [{ id: "purchases.PayPal status" }], valueKey: [{ id: "purchases.Amount", aggregation: "sum" }] } },
      s2: { type: "grid", dataMapping: { cols: [{ id: "purchases.Agent" }, { id: "purchases.SKU" }, { id: "purchases.PayPal status" }, { id: "purchases.Amount", aggregation: "sum" }] } } },
    widgetLayout: { r1: { xTrack: 0, yTrack: 0, xSpan: 12, ySpan: 12 }, r2: { xTrack: 12, yTrack: 0, xSpan: 12, ySpan: 12 },
      s1: { xTrack: 0, yTrack: 12, xSpan: 12, ySpan: 14 }, s2: { xTrack: 12, yTrack: 12, xSpan: 12, ySpan: 14 } } },
] };

function renderStudio() {
  const has = state.purchases.length > 0;
  $("views-empty").hidden = has; $("studio").hidden = !has;
  if (!has || $("tab-views").hidden) return;
  try {
    if (!studio) studio = agStudio.createStudio($("studio"), { mode: "view", initialState: STUDIO_STATE, data: studioData(), layout: { columns: 24, rowHeight: 24 } });
    else studio.setProperty("data", studioData());
  } catch (e) { banner(`AG Studio could not load: ${e.message}`, true); }
}

async function refresh() {
  try {
    state = await api("/api/state");
    roster.setGridOption("rowData", state.agents);
    log.setGridOption("rowData", state.gateway);
    renderSteps(); renderTools(); renderEvidence(); renderStudio();
  } catch (e) { banner(`Could not reach Probation: ${e.message}`, true); }
}

document.querySelectorAll("nav button").forEach((b) => (b.onclick = () => {
  document.querySelectorAll("nav button").forEach((x) => x.setAttribute("aria-selected", x === b));
  ["team", "views", "activity"].forEach((t) => ($(`tab-${t}`).hidden = t !== b.dataset.tab));
  renderStudio();
}));
$("ask-view").onsubmit = async (e) => {
  e.preventDefault();
  const question = $("ask-text").value.trim();
  if (!question || !studio) return;
  banner("Working...");
  try {
    const v = await api("/api/views/ask", { method: "POST", body: JSON.stringify({ question }) });
    const now = studio.getState(), page = now.pages[0], id = `ai${Date.now()}`;
    const count = Object.keys(page.widgets).filter((k) => k.startsWith("ai")).length;
    studio.setState({ ...now, pages: [{ ...page,
      widgets: { ...page.widgets, [id]: { type: "column-chart-grouped", dataMapping: {
        categoryKey: [{ id: `${v.table}.${v.category}` }], valueKey: [{ id: `${v.table}.${v.value}`, aggregation: "sum" }] } } },
      widgetLayout: { ...page.widgetLayout, [id]: { xTrack: count % 2 ? 12 : 0, yTrack: 26 + 14 * Math.floor(count / 2), xSpan: 12, ySpan: 14 } } }] });
    banner(`Added "${v.title}": ${v.value} by ${v.category}.`);
  } catch (err) { banner(err.message, true); }
};
$("reset").onclick = async () => {
  if (!confirm("Clear this run and start over? PayPal keeps its records.")) return;
  await api("/api/reset", { method: "POST" }); readback = null; banner(""); refresh();
};
makeGrids();
refresh();
