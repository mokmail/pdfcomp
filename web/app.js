/* PDF Knowledge Base front-end — kmail.at. Vanilla JS, no framework. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const snip = (s) => esc((s || "").replace(/\s+/g, " ").slice(0, 180));
const api = {
  get: (u) => fetch(u).then((r) => r.json()),
  post: (u, fd) => fetch(u, { method: "POST", body: fd }).then((r) => r.json()),
};
const setText = (sel, txt) => { const el = $(sel); el.hidden = false; el.textContent = txt; };

const state = {
  results: [], cur: null, docs: [], selDoc: -1, busy: false,
  sources: [], connectors: [], srcSel: null, scan: [],
};

/* ------------------------------------------------------------ theme */
let theme = localStorage.getItem("pdfkb-theme") || "light";
const applyTheme = () => {
  document.documentElement.classList.toggle("dark", theme === "dark");
  $("#theme").textContent = theme === "dark" ? "◐  LIGHT" : "◐  DARK";
};
applyTheme();
$("#theme").onclick = () => {
  theme = theme === "dark" ? "light" : "dark";
  localStorage.setItem("pdfkb-theme", theme);
  applyTheme();
};

/* ------------------------------------------------------------- tabs */
$$(".tab").forEach((t) => (t.onclick = () => {
  $$(".tab").forEach((x) => x.classList.toggle("is-active", x === t));
  $$(".panel").forEach((p) => p.classList.toggle("is-active", p.id === "panel-" + t.dataset.panel));
  if (t.dataset.panel === "settings") loadSettings();
  if (t.dataset.panel === "library") loadDocs();
  if (t.dataset.panel === "sources") loadSources();
}));

/* ------------------------------------------------------------ chrome */
function log(msg) {
  const el = $("#log");
  el.textContent += msg + "\n";
  el.scrollTop = el.scrollHeight;
}
const bar = (pct) => { $("#bar").style.width = Math.max(0, Math.min(100, pct)) + "%"; };

function setBusy(b) {
  state.busy = b;
  ["#go", "#pick-files", "#pick-folder", "#refresh", "#export", "#del", "#reset", "#open-any",
   "#s-save", "#s-test", "#p-install", "#s-refresh", "#i-install", "#x-save",
   "#src-test", "#src-save", "#src-scan", "#src-del", "#st-save", "#st-defaults", "#vt-test",
   "#lib-add", "#lib-update", "#sp-save"]
    .forEach((s) => { const el = $(s); if (el) el.disabled = b; });
}

async function refresh() {
  try {
    const s = await api.get("/api/stats");
    $("#provider").textContent = s.provider;
    $("#status").textContent = `library ${s.library || "default"}  ·  documents ${s.documents}`
      + `  ·  chunks ${s.chunks}  ·  vectors ${s.vectors}  ·  ${s.vector_backend || ""}`;
  } catch (e) { $("#status").textContent = "API unavailable"; }
}

/* ------------------------------------------------------------ ingest */
async function ingest(files) {
  files = [...files].filter((f) => f.name.toLowerCase().endsWith(".pdf"));
  if (!files.length) { log("No PDF files in that selection."); return; }
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f));
  fd.append("force", $("#force").checked ? "true" : "false");
  $("#log").textContent = "";
  log(`Ingesting ${files.length} file(s) …`);
  setBusy(true); bar(15);
  try {
    const r = await api.post("/api/ingest", fd);
    bar(100);
    (r.results || []).forEach((x) => log(x.status === "error"
      ? `  error: ${x.filename} — ${x.message}`
      : `  ${x.status}: ${x.filename} (${x.npages || 0} pages, ${x.nchunks || 0} chunks)`));
    await refresh(); loadDocs();
  } catch (e) { log("  ingest failed: " + e); bar(0); }
  finally { setBusy(false); }
}
$("#pick-files").onchange = (e) => ingest(e.target.files);
$("#pick-folder").onchange = (e) => ingest(e.target.files);

const drop = $("#drop");
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => {
  e.preventDefault(); drop.classList.add("is-over");
}));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => {
  e.preventDefault(); drop.classList.remove("is-over");
}));
drop.addEventListener("drop", (e) => ingest(e.dataTransfer.files));

/* ------------------------------------------------------------ search */
const score = (h) => h.sim != null ? h.sim.toFixed(3)
  : h.kw != null ? "kw " + h.kw.toFixed(2) : (h.score ?? 0).toFixed(3);

async function doSearch() {
  const q = $("#q").value.trim();
  if (!q) return;
  const mode = $('input[name=mode]:checked').value;
  const k = Math.min(50, Math.max(1, parseInt($("#k").value, 10) || 10));
  setBusy(true);
  try {
    const r = await api.get(`/api/search?q=${encodeURIComponent(q)}&mode=${mode}&k=${k}`);
    state.results = r.results || [];
    state.cur = null;
    const tb = $("#rtable tbody");
    tb.innerHTML = "";
    if (!state.results.length) {
      tb.innerHTML = `<tr><td colspan="5" class="hint">No results — add PDFs first, or try another query.</td></tr>`;
      $("#detail").textContent = ""; $("#citation").textContent = "";
      return;
    }
    state.results.forEach((h, i) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td class="mono">${i + 1}</td><td class="mono">${score(h)}</td>`
        + `<td class="src">${esc(h.filename)}</td><td class="mono">${h.page}</td>`
        + `<td>${snip(h.text)}</td>`;
      tr.onclick = () => select(i);
      tb.appendChild(tr);
    });
    select(0);
  } catch (e) { log("search failed: " + e); }
  finally { setBusy(false); }
}
$("#go").onclick = doSearch;
$("#q").addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });

async function select(i) {
  const h = state.results[i];
  if (!h) return;
  state.cur = h;
  $$("#rtable tbody tr").forEach((tr, j) => tr.classList.toggle("is-sel", j === i));
  $("#citation").textContent = "citation:  " + h.citation;
  $("#open").href = h.url;
  let txt = `source  ${h.filename}   (page ${h.page})\nchunk   ${h.chunk_id}\n${"-".repeat(76)}\n${h.text}`;
  try {
    const ctx = await api.get(`/api/context?doc_id=${encodeURIComponent(h.doc_id)}&page=${h.page}`);
    txt += `\n\n—— page ${h.page} context ——`;
    ctx.chunks.forEach((c) => { txt += `\n[p.${c.page}] ${(c.text || "").replace(/\s+/g, " ")}`; });
  } catch (e) { /* best-effort */ }
  $("#detail").textContent = txt;
}

const copy = async (s) => { try { await navigator.clipboard.writeText(s); } catch (e) {} };
$("#copy-cite").onclick = () => state.cur && copy(state.cur.citation);
$("#copy-text").onclick = () => state.cur && copy(state.cur.text);

/* ----------------------------------------------------------- library */
const fmtDate = (t) => t ? new Date(t * 1000).toISOString().slice(0, 16).replace("T", " ") : "";

async function loadDocs() {
  try {
    const r = await api.get("/api/documents");
    state.docs = r.documents || [];
    state.selDoc = state.docs.length ? 0 : -1;
    const tb = $("#dtable tbody");
    tb.innerHTML = "";
    if (!state.docs.length) tb.innerHTML = `<tr><td colspan="5" class="hint">Nothing indexed yet.</td></tr>`;
    state.docs.forEach((d, i) => {
      const tr = document.createElement("tr");
      if (i === state.selDoc) tr.classList.add("is-sel");
      tr.innerHTML = `<td class="src">${esc(d.filename)}</td><td class="mono">${d.npages}</td>`
        + `<td class="mono">${d.nchunks}</td>`
        + `<td class="mono">${esc(d.embed_provider + ":" + d.embed_model)}</td>`
        + `<td class="mono">${fmtDate(d.added_at)}</td>`;
      tr.onclick = () => {
        state.selDoc = i;
        $$("#dtable tbody tr").forEach((x) => x.classList.toggle("is-sel", x === tr));
      };
      tb.appendChild(tr);
    });
    refresh();
  } catch (e) { $("#status").textContent = "API unavailable"; }
}
const curDoc = () => state.docs[state.selDoc];

$("#refresh").onclick = () => { refresh(); loadDocs(); };
$("#open-any").onclick = () => { const d = curDoc(); if (d) window.open(`/api/pdf/${d.doc_id}`, "_blank"); };
$("#export").onclick = async () => {
  const d = curDoc(); if (!d) return;
  const r = await api.post("/api/export", new URLSearchParams({ doc_id: d.doc_id }));
  alert("Written:\n" + r.path);
};
$("#del").onclick = async () => {
  const d = curDoc(); if (!d) return;
  if (!confirm(`Remove '${d.filename}' from the knowledge base?`)) return;
  await api.post("/api/delete", new URLSearchParams({ doc_id: d.doc_id }));
  await refresh(); loadDocs();
};
$("#reset").onclick = async () => {
  if (!confirm("Delete ALL documents, chunks and vectors?\n\nThe knowledge-base file is backed up first.")) return;
  const r = await api.post("/api/reset", new URLSearchParams({ confirm: "RESET" }));
  $("#rtable tbody").innerHTML = ""; $("#detail").textContent = "";
  $("#citation").textContent = "";
  log(r && r.backup ? `Knowledge base reset (backup kept at ${r.backup}).` : "Knowledge base reset.");
  await refresh(); loadDocs();
};

/* ----------------------------------------------------------- sources */
function fillConnectorSelect() {
  const sel = $("#src-type");
  sel.innerHTML = state.connectors.map((c) =>
    `<option value="${c.id}" ${c.available ? "" : "disabled"}>${esc(c.label)}${c.available ? "" : " — needs install"}</option>`).join("");
  sel.onchange = () => renderSrcFields(sel.value);
}

function renderSrcFields(type) {
  const c = state.connectors.find((x) => x.id === type);
  const el = $("#src-fields");
  if (!c) { el.innerHTML = ""; return; }
  el.innerHTML = (c.fields || []).map((f) => `
    <label class="field"><span>${esc(f.label || f.key)}</span>
      <input id="sf-${esc(f.key)}" data-key="${esc(f.key)}"
             type="${f.secret ? "password" : "text"}" spellcheck="false" autocomplete="off"
             placeholder="${esc(f.placeholder || "")}"></label>`).join("")
    + (c.available ? "" : `<div class="notice">Needs a package: <code>${esc(c.install || "")}</code></div>`);
}

const srcForm = () => {
  const fd = new URLSearchParams();
  fd.set("name", $("#src-name").value.trim());
  fd.set("type", $("#src-type").value);
  $$("#src-fields input").forEach((i) => fd.set(i.dataset.key, i.value.trim()));
  return fd;
};

function renderSources() {
  const el = $("#src-list");
  if (!state.sources.length) { el.innerHTML = `<p class="hint">No connections yet.</p>`; return; }
  el.innerHTML = state.sources.map((s) => {
    const bits = [s.type, s.host, s.share, s.path, s.url].filter(Boolean);
    return `<button type="button" class="model ${state.srcSel === s.name ? "is-embed" : ""}" data-name="${esc(s.name)}">
      <span class="mname">${esc(s.name)}${state.srcSel === s.name ? "  ✓" : ""}</span>
      <span class="msize">${esc(bits.join(" · "))}</span></button>`;
  }).join("");
  $$("#src-list .model").forEach((b) => (b.onclick = () => {
    state.srcSel = b.dataset.name;
    renderSources();
    $("#src-name").value = state.srcSel;
  }));
}

async function loadSources() {
  try {
    const r = await api.get("/api/sources");
    state.sources = r.sources || [];
    state.connectors = r.connectors || [];
    $("#src-file").textContent = r.file || "";
    fillConnectorSelect();
    renderSrcFields($("#src-type").value);
    renderSources();
  } catch (e) { $("#src-list").innerHTML = `<p class="hint">sources API unavailable</p>`; }
}

$("#src-save").onclick = async () => {
  const r = await api.post("/api/sources/save", srcForm());
  setText("#src-result", r.ok ? `saved connection '${r.source.name}' (${r.source.type})` : `failed — ${r.error}`);
  if (r.ok) { state.srcSel = r.source.name; await loadSources(); }
};
$("#src-test").onclick = async () => {
  setText("#src-result", "testing …");
  const r = await api.post("/api/sources/test", srcForm());
  setText("#src-result", r.ok ? `ok — ${r.message}` : `failed — ${r.error}`);
};
$("#src-del").onclick = async () => {
  if (!state.srcSel) { setText("#src-result", "select a saved source first"); return; }
  if (!confirm(`Delete the connection '${state.srcSel}'?`)) return;
  await api.post("/api/sources/delete", new URLSearchParams({ name: state.srcSel }));
  state.srcSel = null;
  $("#stable tbody").innerHTML = "";
  setText("#src-result", "connection deleted");
  await loadSources();
};
$("#src-scan").onclick = async () => {
  if (!state.srcSel) { setText("#src-result", "select a saved source first"); return; }
  setBusy(true); setText("#src-result", `scanning ${state.srcSel} …`);
  const r = await api.post("/api/sources/scan", new URLSearchParams({ name: state.srcSel }));
  setBusy(false);
  if (!r.ok) { setText("#src-result", `scan failed — ${r.error}`); return; }
  state.scan = r.files || [];
  const tb = $("#stable tbody");
  tb.innerHTML = "";
  if (!state.scan.length) tb.innerHTML = `<tr><td colspan="4" class="hint">No PDFs found.</td></tr>`;
  state.scan.forEach((f, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="pick"><input type="checkbox" data-i="${i}"></td>`
      + `<td class="src">${esc(f.name)}</td>`
      + `<td class="mono">${f.size ? (f.size / 1048576).toFixed(1) + " MB" : "—"}</td>`
      + `<td class="mono">${esc(f.remote)}</td>`;
    tb.appendChild(tr);
  });
  $("#src-count").textContent = `${state.scan.length} file(s)`;
  $("#src-ingest").disabled = !state.scan.length;
  setText("#src-result", `found ${state.scan.length} PDF(s) at ${state.srcSel}`);
};
$("#src-ingest").onclick = async () => {
  const picked = $$("#stable tbody input[type=checkbox]").filter((c) => c.checked)
    .map((c) => state.scan[+c.dataset.i].remote);
  if (!picked.length) { setText("#src-result", "tick at least one file"); return; }
  setBusy(true); setText("#src-result", `ingesting ${picked.length} file(s) …`);
  const fd = new URLSearchParams({
    name: state.srcSel, files: JSON.stringify(picked),
    force: $("#src-force").checked ? "true" : "false",
  });
  const r = await api.post("/api/sources/ingest", fd);
  setBusy(false);
  if (!r.ok) { setText("#src-result", `ingest failed — ${r.error}`); return; }
  const lines = (r.results || []).map((x) => x.status === "error"
    ? `error: ${x.filename} — ${x.message}`
    : `${x.status}: ${x.filename} (${x.npages || 0} pages, ${x.nchunks || 0} chunks)`);
  setText("#src-log", lines.join("\n"));
  setText("#src-result", `done — ${lines.length} file(s)`);
  refresh(); loadDocs();
};

/* ---------------------------------------------------------- settings */
const kv = (k, v) => `<div class="kvrow"><span class="kvk">${esc(k)}</span><span class="kvv">${esc(String(v))}</span></div>`;

async function loadSettings() {
  let r;
  try { r = await api.get("/api/settings"); }
  catch (e) { $("#s-current").innerHTML = `<p class="hint">settings API unavailable</p>`; return; }
  const s = r.settings, env = r.env;
  state.env = env;

  const sel = $("#s-provider");
  if (!sel.dataset.filled) {
    sel.innerHTML = (env.providers || []).map((p) => `<option value="${p.id}">${esc(p.label)}</option>`).join("");
    sel.dataset.filled = "1";
  }
  sel.value = s.provider;
  $("#s-url").value = s.ollama_url || "";
  $("#s-model").value = s.model || "";

  let cur = kv("live provider", r.live_provider || r.live_error || "—")
    + kv("stored dimension", r.stored_dim ?? "—")
    + kv("ollama", env.ollama.reachable ? "reachable" : (env.ollama.error || "unreachable"))
    + kv("ollama exe", env.ollama.exe || "not found")
    + kv("sentence-transformers", env.sentence_transformers.installed ? "installed" : "not installed");
  if (!env.sentence_transformers.installed) cur += kv("to install", env.sentence_transformers.install_cmd);
  $("#s-current").innerHTML = cur;

  renderModels(env.ollama.models || []);
  fillStorage(r);
  fillExtraction(r);
  state.libraries = r.libraries || [];
  renderLibraries(r.active_library);
  state.lastSettings = r;
}

function renderModels(models) {
  $("#s-models").innerHTML = models.length
    ? models.map((m) => `<button class="model ${m.embed ? "is-embed" : ""}" type="button" data-name="${esc(m.name)}">
        <span class="mname">${esc(m.name)}</span>
        <span class="msize">${m.size ? (m.size / 1e6).toFixed(0) + " MB" : "cloud"}${m.embed ? " · embed" : ""}</span>
      </button>`).join("")
    : `<p class="hint">No models listed — is the Ollama server running?</p>`;
  $("#model-list").innerHTML = models.map((m) => `<option value="${esc(m.name)}"></option>`).join("");
  $$("#s-models .model").forEach((b) => (b.onclick = () => { $("#s-model").value = b.dataset.name; }));
}

function fillStorage(r) {
  const s = r.settings, st = r.storage, env = r.env;
  $("#st-data").value = s.data_dir || "";
  $("#st-sqlite").value = s.sqlite_path || "";
  const bs = $("#st-backend");
  if (!bs.dataset.filled) {
    const byCat = {};
    (env.vector_backends || []).forEach((b) => { (byCat[b.category] = byCat[b.category] || []).push(b); });
    bs.innerHTML = Object.entries(byCat).map(([cat, list]) =>
      `<optgroup label="${esc(cat)}">` + list.map((b) =>
        `<option value="${b.id}" ${b.available ? "" : "disabled"}>${esc(b.label)}`
        + `${b.available ? "" : " — needs install"}</option>`).join("") + `</optgroup>`).join("");
    bs.dataset.filled = "1";
    bs.onchange = noteBackend;
  }
  bs.value = s.vector_backend || "chroma";
  $("#st-vpath").value = s.vector_path || "";
  $("#st-vurl").value = s.vector_url || "";
  $("#st-vkey").value = s.vector_api_key || "";
  $("#st-vindex").value = s.vector_index || "";
  $("#st-vcoll").value = s.vector_collection || "";
  $("#st-text").value = s.text_dir || "";
  $("#st-sources").value = s.sources_dir || "";
  noteBackend();
  const p = st.paths;
  const serverSide = ["qdrant", "opensearch", "elasticsearch"].includes(st.vector_backend);
  $("#st-current").innerHTML =
    kv("library", st.active_library || "default")
    + kv("data dir", p.data_dir)
    + kv("knowledge base", p.sqlite_path + (st.sqlite.exists ? `  (${(st.sqlite.bytes / 1024).toFixed(0)} KB)` : "  (new)"))
    + kv("vector backend", st.vector_backend)
    + kv("vector store", st.vector_backend === "sqlite" ? "inside the knowledge base file"
      : serverSide ? (st.vector_url || "(no url set)") : p.vector_path)
    + kv("collection", st.vector_collection)
    + kv("text exports", p.text_dir)
    + kv("source copies", p.sources_dir);
}

function noteBackend() {
  const b = ((state.env || {}).vector_backends || []).find((x) => x.id === $("#st-backend").value);
  const note = $("#st-note");
  if (!b) { note.hidden = true; return; }
  note.hidden = false;
  note.innerHTML = esc(b.note || "")
    + (b.available ? "" : ` — needs <code>${esc(b.install || "its client library")}</code>`)
    + (b.status === "server" ? " · needs a running server (not exercised in this build)" : "")
    + (b.status === "verified" && b.available ? " · verified on this machine" : "");
}

/* ------------------------------------------------------ libraries (KBs) */
async function loadLibraries() {
  try {
    const r = await api.get("/api/libraries");
    state.libraries = r.libraries || [];
    renderLibraries(r.active);
  } catch (e) { /* non-fatal */ }
}

function renderLibraries(active) {
  $("#lib-select").innerHTML = state.libraries.map((l) =>
    `<option value="${esc(l.name)}" ${l.active ? "selected" : ""}>${esc(l.name)}`
    + `${l.documents != null ? ` · ${l.documents}` : ""}</option>`).join("");
  const tb = $("#libtable tbody");
  tb.innerHTML = state.libraries.map((l) => `
    <tr data-name="${esc(l.name)}">
      <td class="src">${esc(l.name)}${l.active ? "  ✓" : ""}</td>
      <td class="mono">${esc(l.backend)}</td>
      <td class="mono">${l.documents != null ? l.documents : "—"}</td>
      <td class="mono">${esc(l.data_dir)}</td>
      <td class="pick">${l.active
        ? `<button class="btn ghost" type="button" disabled>active</button>`
        : `<button class="btn ghost lib-use" type="button">use</button>
           <button class="btn ghost lib-del" type="button">del</button>`}</td>
    </tr>`).join("") || `<tr><td colspan="5" class="hint">No libraries.</td></tr>`;
  $$("#libtable .lib-use").forEach((b) => (b.onclick = () => useLibrary(b.closest("tr").dataset.name)));
  $$("#libtable .lib-del").forEach((b) => (b.onclick = () => deleteLibrary(b.closest("tr").dataset.name)));
}

async function useLibrary(name) {
  if (!confirm(`Switch to the library '${name}'?\n\nThe current knowledge base is backed up first.`)) return;
  setBusy(true);
  const r = await api.post("/api/libraries/use", new URLSearchParams({ name }));
  setBusy(false);
  const st = r.stats || {};
  setText("#lib-result", r.ok
    ? `active library: ${r.active}\n${st.documents ?? 0} documents · ${st.chunks ?? 0} chunks · `
      + `${st.vectors ?? 0} vectors · ${st.vector_backend || ""}`
    : `switch failed — ${r.error}`);
  await refresh(); await loadSettings(); await loadDocs(); await loadLibraries();
}

async function deleteLibrary(name) {
  if (!confirm(`Delete the library '${name}'?\n\nIts files on disk are left alone.`)) return;
  const r = await api.post("/api/libraries/delete", new URLSearchParams({ name }));
  setText("#lib-result", r.ok ? `deleted '${name}'` : `delete failed — ${r.error}`);
  await loadLibraries();
}

$("#lib-select").onchange = (e) => useLibrary(e.target.value);
$("#lib-add").onclick = async () => {
  const name = $("#lib-name").value.trim();
  if (!name) { setText("#lib-result", "give the library a name first"); return; }
  const fd = storageForm();
  fd.set("name", name);
  const r = await api.post("/api/libraries/save", fd);
  setText("#lib-result", r.ok
    ? `saved library '${name}' from the storage settings above — click 'use' to switch to it`
    : `failed — ${r.error}`);
  await loadLibraries();
};
$("#lib-update").onclick = async () => {
  const active = (state.libraries.find((l) => l.active) || {}).name;
  if (!active) return;
  const fd = storageForm();
  fd.set("name", active);
  const r = await api.post("/api/libraries/save", fd);
  setText("#lib-result", r.ok ? `updated the active library '${active}'` : `failed — ${r.error}`);
  await refresh(); await loadSettings(); await loadLibraries();
};

$("#vt-test").onclick = async () => {
  setText("#vt-result", "connecting to the vector store …");
  const r = await api.post("/api/settings/vector", storageForm());
  setText("#vt-result", r.ok
    ? `${r.backend} · ${r.info}\ncount = ${r.count}`
      + `${r.url ? `\nurl = ${r.url}` : ""}${r.index ? `\nindex = ${r.index}` : ""}`
      + `${r.fallback ? `\nwarning: ${r.fallback}` : ""}`
    : `failed — ${r.error}`);
};

$("#s-test").onclick = async () => {
  setText("#s-result", "testing …");
  const r = await api.post("/api/settings/test", new URLSearchParams({
    provider: $("#s-provider").value, model: $("#s-model").value.trim(),
    ollama_url: $("#s-url").value.trim(),
  }));
  setText("#s-result", r.ok
    ? `ok — provider=${r.provider}  model=${r.model}  dim=${r.dim}  (${r.ms} ms)`
    : `failed — ${r.error}`);
};

$("#s-save").onclick = async () => {
  const r = await api.post("/api/settings/save", new URLSearchParams({
    provider: $("#s-provider").value, model: $("#s-model").value.trim(),
    ollama_url: $("#s-url").value.trim(),
  }));
  setText("#s-result", r.ok
    ? `saved — provider=${r.settings.provider}  model=${r.settings.model || "(provider default)"}`
      + (r.probe ? `  dim=${r.probe.dim}` : "")
    : `save failed — ${r.error}`);
  const w = $("#set-warning");
  w.hidden = !r.warning; w.textContent = r.warning || "";
  await refresh(); await loadSettings();
};
$("#s-refresh").onclick = () => loadSettings();

$("#p-model").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#p-install").click(); });
$("#p-install").onclick = async () => {
  const model = $("#p-model").value.trim();
  if (!model) return;
  const r = await api.post("/api/settings/pull", new URLSearchParams({ model }));
  if (!r.ok) { setText("#job-log", "error: " + r.error); return; }
  watchJob();
};

$("#i-install").onclick = async () => {
  const extra = $("#i-extra").value.trim() || "docling";
  if (!confirm(`Install '${extra}' into this Python environment with pip?\n\nThis downloads packages and can take a while.`)) return;
  const r = await api.post("/api/settings/install", new URLSearchParams({ extra }));
  if (!r.ok) { setText("#job-log", "error: " + r.error); return; }
  watchJob();
};

let jobTimer = null;
let jobDoneTimer = null;

function jobBadge(s) {
  const el = $("#job-badge");
  const bar = $("#jobbar");
  if (!s || (!s.running && s.ok === null)) {
    el.hidden = true;
    bar.classList.remove("on");
    return;
  }
  const pct = (s.lines || []).map((l) => (String(l).match(/(\d{1,3})\s*%/) || [])[1]).filter(Boolean).pop();
  el.hidden = false;
  if (s.running) {
    el.innerHTML = `<span class="dot"></span>${esc(s.kind || "job")} ${esc(s.target || "")}`
      + (pct ? ` · ${pct}%` : "");
    bar.classList.add("on");
  } else {
    el.innerHTML = `${s.ok ? "● done" : "● failed"} — ${esc(s.kind || "")} ${esc(s.target || "")}`;
    bar.classList.remove("on");
    if (s.ok) {
      clearTimeout(jobDoneTimer);
      jobDoneTimer = setTimeout(() => { el.hidden = true; }, 12000);
    }
  }
}

/* one polling loop for any running install/pull, wherever the user is */
async function watchJob() {
  clearTimeout(jobTimer);
  let s;
  try { s = await api.get("/api/settings/job"); } catch (e) { return; }
  state.job = s;
  jobBadge(s);
  const el = $("#job-log");
  if (el && (s.kind || (s.lines || []).length)) {
    el.hidden = false;
    el.textContent = (s.kind ? `${s.kind} ${s.target}\n` : "") + (s.lines || []).join("\n")
      + (s.running ? "" : (s.ok ? "\n\ndone." : "\n\nfailed."));
    if ($("#panel-settings")?.classList.contains("is-active")) el.scrollTop = el.scrollHeight;
  }
  if (s.running) jobTimer = setTimeout(watchJob, 1000);
  else if (s.ok) loadSettings();
}

$("#job-badge").onclick = () => {
  const t = $$(".tab").find((x) => x.dataset.panel === "settings");
  if (t) t.click();
  setTimeout(() => { $("#job-log")?.scrollIntoView({ behavior: "smooth", block: "center" }); }, 250);
};

/* -------------------------------------------------------- extraction */
function fillExtraction(r) {
  const s = r.settings, env = r.env || {};
  const xs = $("#x-extractor");
  if (!xs.dataset.filled) {
    xs.innerHTML = (env.extractors || []).map((e) =>
      `<option value="${e.id}" ${e.available ? "" : "disabled"}>${esc(e.label)}${e.available ? "" : " — not installed"}</option>`).join("");
    xs.dataset.filled = "1";
  }
  xs.value = s.extractor || "pymupdf";
  $("#x-ocr").checked = String(s.docling_ocr || "false").toLowerCase() === "true";
  const d = env.docling || {};
  $("#x-extras").innerHTML =
    kv("docling", d.installed ? `installed${d.version ? " " + d.version : ""}` : "not installed")
    + (d.installed ? "" : kv("install", d.install_cmd || "python3 -m pip install docling"));
  const chosen = (env.extractors || []).find((e) => e.id === xs.value);
  const note = $("#x-note");
  note.hidden = !(chosen && !chosen.available);
  note.innerHTML = chosen && !chosen.available
    ? `The selected extractor needs a package: <code>${esc(chosen.install)}</code>` : "";
}

$("#x-extractor").onchange = () => { if (state.lastSettings) fillExtraction(state.lastSettings); };
$("#x-save").onclick = async () => {
  const r = await api.post("/api/settings/save", new URLSearchParams({
    extractor: $("#x-extractor").value,
    docling_ocr: $("#x-ocr").checked ? "true" : "false",
  }));
  setText("#x-result", r.ok
    ? `saved — extractor=${r.settings.extractor}  ocr=${r.settings.docling_ocr}`
    : `failed — ${r.error}`);
};

/* -------------------------------------------------------- storage form */
const storageForm = () => new URLSearchParams({
  data_dir: $("#st-data").value.trim(),
  sqlite_path: $("#st-sqlite").value.trim(),
  vector_backend: $("#st-backend").value,
  vector_path: $("#st-vpath").value.trim(),
  vector_url: $("#st-vurl").value.trim(),
  vector_api_key: $("#st-vkey").value.trim(),
  vector_index: $("#st-vindex").value.trim(),
  vector_collection: $("#st-vcoll").value.trim(),
  text_dir: $("#st-text").value.trim(),
  sources_dir: $("#st-sources").value.trim(),
});

$("#st-save").onclick = async () => {
  setText("#st-result", "applying …");
  const r = await api.post("/api/settings/storage", storageForm());
  if (r.ok) {
    const s = r.stats || {};
    setText("#st-result", `applied — ${s.documents ?? 0} documents, ${s.chunks ?? 0} chunks, `
      + `${s.vectors ?? 0} vectors\nvector store: ${s.vector_store || ""}`
      + (s.vector_error ? `\nwarning: ${s.vector_error}` : ""));
  } else {
    setText("#st-result", `failed — ${r.error}`);
  }
  await refresh(); await loadSettings(); await loadDocs();
};
$("#st-defaults").onclick = () => {
  ["#st-data", "#st-sqlite", "#st-vpath", "#st-vurl", "#st-vkey", "#st-vindex", "#st-vcoll",
   "#st-text", "#st-sources"]
    .forEach((s) => { $(s).value = ""; });
  $("#st-backend").value = "chroma";
  $("#st-save").click();
};

/* --------------------------------------------------------------- init */
refresh();
loadDocs();
loadSettings();
loadSources();
loadLibraries();
watchJob();          // picks up an install/pull already running
