// Prior Authorization Copilot UI.
// - AG-UI: POST /investigate and read the Server-Sent Event stream.
// - A2UI: render only components in the fixed CATALOG below; anything else is refused.
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let current = null;

async function loadCases() {
  const cases = await (await fetch("/api/cases")).json();
  $("#case-list").innerHTML = cases.map((c) => `
    <li data-id="${esc(c.case_id)}" class="${c.case_id === current ? "active" : ""}">
      <div class="id">${esc(c.case_id)} ${c.recommendation ? `<span class="pill r-${esc(c.recommendation)}">${esc(c.recommendation)}</span>` : ""}</div>
      <div class="sub">${esc(c.summary)}</div>
      <div class="sub">service ${esc(c.service_date)} · ${esc(c.status)}${c.disposition ? " · " + esc(c.disposition.disposition) : ""}</div>
    </li>`).join("");
  document.querySelectorAll("#case-list li").forEach((li) => (li.onclick = () => select(li.dataset.id)));
}

async function select(id) {
  current = id;
  $("#run").disabled = false;
  $("#events").innerHTML = "";
  $("#agents").innerHTML = "";
  await loadCases();
  const d = await (await fetch(`/api/cases/${id}`)).json();
  if (d.a2ui) renderSurface(d.a2ui); else $("#surface-root").innerHTML = `<p class="muted">Not investigated yet.</p>`;
  if (d.trace) d.trace.forEach(showEvent);
  loadAudit();
}

// ------------------------------------------------------------------ AG-UI stream
async function investigate() {
  $("#run").disabled = true;
  $("#events").innerHTML = "";
  $("#surface-root").innerHTML = `<p class="muted">Investigating…</p>`;
  const resp = await fetch(`/api/cases/${current}/investigate`, { method: "POST" });
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
      if (chunk.startsWith("data: ")) handle(JSON.parse(chunk.slice(6)));
    }
  }
  $("#run").disabled = false;
  loadCases(); loadAudit();
}

let agentState = {};
function handle(ev) {
  showEvent(ev);
  if (ev.type === "STATE_SNAPSHOT" && ev.snapshot.agents) { agentState = { ...ev.snapshot.agents }; drawAgents(); }
  if (ev.type === "STATE_DELTA") { ev.delta.forEach((op) => { agentState[op.path.split("/").pop()] = op.value; }); drawAgents(); }
  if (ev.type === "STEP_STARTED") { agentState[ev.stepName] = "running"; drawAgents(); }
  if (ev.type === "STEP_FINISHED" && agentState[ev.stepName] === "running") { agentState[ev.stepName] = "done"; drawAgents(); }
  if (ev.type === "RUN_FINISHED") {
    Object.keys(agentState).forEach((k) => { if (agentState[k] === "running" || agentState[k] === "pending") agentState[k] = "done"; });
    drawAgents();
  }
  if (ev.type === "CUSTOM" && ev.name === "a2ui") renderSurface(ev.value);
}
function drawAgents() {
  $("#agents").innerHTML = Object.entries(agentState).map(([k, v]) =>
    `<span class="pill ${v === "failed" ? "s-NOT_SUPPORTED" : v === "done" ? "s-SUPPORTED" : ""}">${esc(k)}: ${esc(v)}</span>`).join("");
}
const toolNames = {};
function showEvent(ev) {
  const li = document.createElement("li");
  const t = `<span class="t">${new Date(ev.timestamp).toLocaleTimeString()}</span> `;
  switch (ev.type) {
    case "TOOL_CALL_START": toolNames[ev.toolCallId] = ev.toolCallName; return;
    case "TOOL_CALL_ARGS": li.className = "tool"; li.innerHTML = `${t}→ <b>${esc(toolNames[ev.toolCallId] || "")}</b> ${esc(ev.delta)}`; break;
    case "TOOL_CALL_RESULT": li.className = ev.ok ? "tool" : "fail";
      li.innerHTML = `${t}${ev.ok ? "✓" : "✗"} ${esc(toolNames[ev.toolCallId] || "")} ${ev.latencyMs ? ev.latencyMs + " ms" : ""} <span class="muted">${esc(ev.content).slice(0, 160)}</span>`; break;
    case "STEP_STARTED": case "STEP_FINISHED": li.className = "step"; li.innerHTML = `${t}${ev.type === "STEP_STARTED" ? "▶" : "■"} ${esc(ev.stepName)}`; break;
    case "CUSTOM": if (ev.name === "a2ui") return; li.className = "custom"; li.innerHTML = `${t}<b>${esc(ev.name)}</b> ${esc(JSON.stringify(ev.value)).slice(0, 400)}`; break;
    case "RUN_ERROR": li.className = "error"; li.innerHTML = `${t}<b>RUN_ERROR</b> ${esc(ev.code)}: ${esc(ev.message)}`; break;
    case "RUN_STARTED": case "RUN_FINISHED": li.innerHTML = `${t}<b>${ev.type}</b> ${esc(ev.runId || "")}`; break;
    default: return;
  }
  $("#events").appendChild(li);
  li.scrollIntoView({ block: "nearest" });
}

// ------------------------------------------------------------------ A2UI renderer
const CATALOG = {
  RecommendationBanner: (p) => `<div class="banner"><div class="rec"><span class="pill r-${esc(p.recommendation)}">${esc(p.recommendation)}</span>
      ${p.red_flag ? '<span class="pill s-NOT_SUPPORTED">RED FLAG · EXPEDITE</span>' : ""}</div>
      <div class="muted">Policy ${esc(p.policy_id)} · ${p.requires_human_review ? "human review required" : "no open issues"}</div>
      <p>${p.summary_source === "llm" ? '<span class="pill">AI summary · citations verified</span> ' : ""}${esc(p.summary)}</p><div class="muted" style="font-size:12px">${esc(p.disclaimer)}</div></div>`,
  AlertList: (p) => p.severity === "info"
    ? `<p class="muted" style="font-size:12px;margin:8px 0">${esc(p.title)}: ${p.items.map(esc).join(" ")}</p>`
    : `<div class="alert ${esc(p.severity)}"><b>${esc(p.title)}</b><ul>${p.items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul></div>`,
  CriteriaChecklist: (p) => `<h3>${esc(p.title)}</h3><table><tr><th>Criterion</th><th>Status</th><th>Evidence</th></tr>${p.items.map((i) => `
      <tr><td><b>${esc(i.criterion_id)}</b> ${esc(i.description)}<div class="muted">${esc(i.rationale)}</div></td>
      <td><span class="pill s-${esc(i.status)}">${esc(i.status)}</span></td>
      <td>${i.evidence_ids.map((e) => `<div class="ev" data-ev="${esc(e)}">${esc(e)}</div>`).join("")}</td></tr>`).join("")}</table>`,
  MissingDocumentationForm: (p) => `<h3>${esc(p.title)}</h3><form id="missing-form">${p.fields.map((f) => `
      <label>${esc(f.label)}</label>${f.input === "boolean"
        ? `<select name="${esc(f.field_id)}"><option value="">—</option><option value="true">Yes</option><option value="false">No</option></select>`
        : `<input type="text" inputmode="numeric" autocomplete="off" name="${esc(f.field_id)}" data-min="${f.min ?? ""}" data-max="${f.max ?? ""}" placeholder="whole number, ${f.min ?? 0}–${f.max ?? ""}">`}`).join("")}
      <label>Source of this information (required)</label><input type="text" name="_note" placeholder="e.g. Outside PT records received 2026-10-06">
      <p><button type="submit">Save and re-run</button> <span id="form-msg" class="err"></span></p></form>`,
  EvidenceList: (p) => `<h3>Evidence</h3>${p.items.map((e) => `<div class="evidence-item ${e.quarantined ? "q" : ""}" id="ev-${esc(e.evidence_id)}">
      <b>${esc(e.evidence_id)}</b> <span class="pill">${esc(e.source_type)}</span> ${e.quarantined ? '<span class="pill s-NOT_SUPPORTED">QUARANTINED</span>' : ""}
      <span class="muted">${esc(e.date)}</span><div>${esc(e.quote)}</div></div>`).join("")}`,
  ReviewerActions: (p) => `<h3>Reviewer action</h3><form id="actions-form">
      <label>Disposition</label><select name="disposition">${p.dispositions.map((d) => `<option>${esc(d)}</option>`).join("")}</select>
      <label>Rationale</label><textarea name="message" rows="2"></textarea>
      <p><button type="submit">Record disposition</button>
      <button type="button" class="secondary" id="clarify">Request clarification</button> <span id="act-msg" class="err"></span></p></form>`,
};

function renderSurface(messages) {
  const upd = messages.find((m) => m.surfaceUpdate)?.surfaceUpdate;
  if (!upd) return;
  const byId = Object.fromEntries(upd.components.map((c) => [c.id, c.component]));
  $("#surface-root").innerHTML = upd.layout.children.map((id) => {
    const [type, props] = Object.entries(byId[id])[0];
    const r = CATALOG[type];
    return r ? r(props) : `<div class="alert critical">Refused to render unknown component type ${esc(type)}</div>`;
  }).join("");
  wireSurface();
}

function wireSurface() {
  document.querySelectorAll(".ev").forEach((el) => (el.onclick = () => {
    document.querySelectorAll(".evidence-item").forEach((x) => x.classList.remove("hl"));
    const t = document.getElementById("ev-" + el.dataset.ev); if (t) { t.classList.add("hl"); t.scrollIntoView({ block: "center" }); }
  }));
  const mf = $("#missing-form");
  if (mf) mf.onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(mf); const note = fd.get("_note");
    // Text inputs, not type=number: a number box changes value when the mouse wheel scrolls over it.
    const items = [];
    for (const [k, raw] of fd.entries()) {
      const v = String(raw).trim();
      if (k === "_note" || v === "") continue;
      if (v === "true" || v === "false") { items.push([k, v === "true"]); continue; }
      const el = mf.querySelector(`[name="${k}"]`);
      const lo = Number(el.dataset.min), hi = Number(el.dataset.max);
      if (!/^\d+(\.\d+)?$/.test(v) || (el.dataset.min !== "" && Number(v) < lo) || (el.dataset.max !== "" && Number(v) > hi)) {
        $("#form-msg").textContent = `"${v}" is not a valid number of weeks (${lo}–${hi}).`; return;
      }
      items.push([k, Number(v)]);
    }
    if (!items.length) { $("#form-msg").textContent = "Enter a value first."; return; }
    if (!String(note || "").trim()) { $("#form-msg").textContent = "Say where this information came from."; return; }
    for (const [k, value] of items) {
      const r = await fetch(`/api/cases/${current}/review`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "supply_data", fact: k, value, note }) });
      if (!r.ok) { $("#form-msg").textContent = (await r.json()).detail; return; }
    }
    investigate();
  };
  const af = $("#actions-form");
  if (af) {
    af.onsubmit = async (e) => {
      e.preventDefault(); const fd = new FormData(af);
      const r = await fetch(`/api/cases/${current}/review`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "record_disposition", disposition: fd.get("disposition"), message: fd.get("message") }) });
      $("#act-msg").textContent = r.ok ? "Recorded." : (await r.json()).detail; loadCases(); loadAudit();
    };
    $("#clarify").onclick = async () => {
      const msg = af.querySelector("textarea").value || "Please provide supporting documentation.";
      const r = await fetch(`/api/cases/${current}/review`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "request_clarification", message: msg }) });
      $("#act-msg").textContent = r.ok ? "Clarification requested (simulated)." : "Failed"; loadCases(); loadAudit();
    };
  }
}

async function loadAudit() {
  if (!current) return;
  const a = await (await fetch(`/api/cases/${current}/audit`)).json();
  $("#audit").innerHTML = a.map((x) => `<li><span class="muted">${esc(x.timestamp)}</span> ${esc(x.actor)} · <b>${esc(x.action)}</b>
    ${esc(x.recommendation || x.disposition || (x.fact ? `${x.fact} = ${x.value}` : ""))}</li>`).join("");
}

// ------------------------------------------------------------------ new case
$("#new-case-btn").onclick = async () => {
  const f = $("#new-case");
  f.hidden = !f.hidden;
  if (!f.hidden && !$("#nc-patient").options.length) {
    const d = await (await fetch("/api/patients")).json();
    $("#nc-patient").innerHTML = d.patients.map((p) => `<option value="${esc(p.patient_id)}">${esc(p.patient_id)} · ${esc(p.sex)} · ${esc(p.age)}</option>`).join("");
    $("#nc-service").innerHTML = Object.entries(d.services).map(([k, v]) => `<option value="${esc(k)}">${esc(k)} ${esc(v)}</option>`).join("");
  }
};
$("#new-case").onsubmit = async (e) => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(e.target).entries());
  const r = await fetch("/api/cases", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const d = await r.json();
  if (!r.ok) { $("#nc-msg").textContent = d.detail; return; }
  $("#nc-msg").textContent = ""; e.target.hidden = true; e.target.reset();
  await select(d.case_id);
};

$("#reset").onclick = async () => {
  if (!confirm("Clear all investigations, reviewer inputs and new cases?")) return;
  await fetch("/api/admin/reset", { method: "POST" });
  current = null; $("#outage").value = "none"; $("#events").innerHTML = ""; $("#agents").innerHTML = "";
  $("#surface-root").innerHTML = `<p class="muted">Select a case and run an investigation.</p>`; $("#audit").innerHTML = "";
  $("#run").disabled = true; loadCases();
};

$("#run").onclick = investigate;
$("#outage").onchange = (e) => fetch("/api/admin/outage", { method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ mode: e.target.value }) });
fetch("/api/admin/outage").then((r) => r.json()).then((d) => ($("#outage").value = d.mode));
loadCases();
