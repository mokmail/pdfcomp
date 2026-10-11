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
  results: [], cur: null, docs: [], selDoc: -1, busy: false, ingesting: false,
  lastQuery: "",
  sources: [], connectors: [], srcSel: null, scan: [],
  libraries: [],
  libs: (() => { try { return JSON.parse(localStorage.getItem("pdfkb-libs") || "[]"); } catch (e) { return []; } })(),
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
   "#lib-add", "#lib-update", "#sp-save", "#scope-all", "#scope-active"]
    .forEach((s) => { const el = $(s); if (el) el.disabled = b; });
}

async function refresh() {
  try {
    const s = await api.get("/api/stats");
    state.lastStats = s;
    renderSetup();
    const hint = $("#search-hint");
    if (hint) {
      hint.hidden = s.documents !== 0;
      hint.textContent = s.documents === 0
        ? "Nothing indexed in this library yet — open Sources → Local files to ingest your first document."
        : "";
    }
  } catch (e) { log("stats unavailable: " + e); }
}

/* The setup in force, kept in the header: what embeds, where it saves, what reads it. */
function renderSetup() {
  const s = state.lastStats || {};
  const st = (state.lastSettings || {}).settings || {};
  const env = (state.lastSettings || {}).env || {};
  const ext = st.extractor || s.extractor || "pymupdf";
  const ver = ext === "docling" && env.docling && env.docling.installed
    ? ` ${env.docling.version || ""}`.trimEnd() : "";
  const dir = String(s.data_dir || "").replace(/^\/(?:Users|home)\/[^/]+/, "~");
  const items = [
    ["embedding", s.provider || "—", ""],
    ["knowledge base", s.library || "default", "is-kb"],
    ["extractor", `${ext}${ver ? " " + ver : ""}`, ""],
    ["vector store", s.vector_backend || "—", "is-store"],
    ["collection", s.vector_collection || "—", "is-store"],
    ["saving in", dir || "—", ""],
    ["indexed", `${s.documents ?? 0} docs · ${s.chunks ?? 0} chunks · ${s.vectors ?? 0} vectors`, ""],
  ];
  if (s.collection_match === false) {
    items.push(["mismatch", `the store holds "${s.collection_stamp || "?"}" — re-ingest`, "is-bad"]);
  }
  const el = $("#setup");
  if (el) {
    el.innerHTML = items.map(([k, v, cls]) =>
      `<span class="setup-item"><span class="setup-k">${esc(k)}</span>`
      + `<span class="setup-v ${cls}">${esc(String(v))}</span></span>`).join("");
  }
  const sum = $("#setup-sum");
  if (sum) {
    sum.textContent = `setup · ${s.library || "default"} · ${ext}${ver ? " " + ver : ""}`
      + ` · ${s.vector_backend || "—"}·${s.vector_collection || "—"}`
      + ` · ${s.vectors ?? 0} vectors`
      + (s.collection_match === false ? "  ⚠ collection mismatch" : "");
  }
}

/* the setup strip folds away; it remembers what you last chose */
const setupBar = $("#setupbar");
if (setupBar) {
  if (localStorage.getItem("pdfkb-setup") === "open") setupBar.open = true;
  setupBar.ontoggle = () =>
    localStorage.setItem("pdfkb-setup", setupBar.open ? "open" : "folded");
}

/* ------------------------------------------------------------ ingest */
/* One line per file, plainly: indexed + embedded, already in the knowledge base,
   nothing to extract, or failed. */
function ingestLine(x) {
  if (x.status === "error") return `  ✕ failed — ${x.filename}  ${x.message || ""}`;
  if (x.status === "skipped") return `  • already in the knowledge base — skipped   ${x.filename}`;
  if (x.status === "empty") return `  ! no text to index   ${x.filename}  ${x.message || ""}`;
  return `  ✓ indexed + embedded   ${x.filename}  —  ${x.npages || 0} pages, `
    + `${x.nchunks || 0} chunks${x.extractor ? ", " + x.extractor : ""}`
    + (x.extractor_note ? `\n      note: ${x.extractor_note}` : "");
}

function ingestSummary(results) {
  const n = (s) => (results || []).filter((x) => x.status === s).length;
  const bits = [];
  if (n("added")) bits.push(`${n("added")} indexed + embedded`);
  if (n("skipped")) bits.push(`${n("skipped")} already in the knowledge base`);
  if (n("empty")) bits.push(`${n("empty")} with no text`);
  if (n("error")) bits.push(`${n("error")} failed`);
  return bits.join("  ·  ");
}

async function ingest(files) {
  files = [...files].filter((f) => f.name.toLowerCase().endsWith(".pdf"));
  if (!files.length) { log("No PDF files in that selection."); return; }
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f));
  fd.append("force", $("#force").checked ? "true" : "false");
  $("#log").textContent = "";
  log(`Ingesting ${files.length} file(s) …`);
  setBusy(true); bar(15);
  state.ingesting = true; watchJob();     // the header reports it from here on, on any page
  try {
    const r = await api.post("/api/ingest", fd);
    bar(100);
    (r.results || []).forEach((x) => log(ingestLine(x)));
    log("\n" + (ingestSummary(r.results) || "nothing to do"));
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
  state.lastQuery = q;                       // what the current results are the answer to
  const mode = $('input[name=mode]:checked').value;
  const k = Math.min(50, Math.max(1, parseInt($("#k").value, 10) || 10));
  const scope = (state.libs && state.libs.length ? state.libs : []).join(",");
  const field = $("#searchfield");
  setBusy(true);
  field.classList.add("is-searching");
  try {
    const r = await api.get(`/api/search?q=${encodeURIComponent(q)}&mode=${mode}&k=${k}`
      + `&libraries=${encodeURIComponent(scope)}`);
    state.results = r.results || [];
    state.cur = null;
    const tb = $("#rtable tbody");
    tb.innerHTML = "";
    if (!state.results.length) {
      tb.innerHTML = `<tr><td colspan="6" class="hint">No results — add PDFs first, try another query, or widen the search scope.</td></tr>`;
      state.cur = null;
      return;
    }
    state.results.forEach((h, i) => {
      const tr = document.createElement("tr");
      const kb = h.knowledge_base || {};
      tr.innerHTML = `<td class="mono">${i + 1}</td><td class="mono">${score(h)}</td>`
        + `<td class="mono">${esc(h.library || "")}`
        + (kb.file ? `<span class="kbtag">${esc(kb.file)}</span>` : "")
        + `</td>`
        + `<td class="src">${esc(h.filename)}</td><td class="mono">${h.page}</td>`
        + `<td>${snip(h.text)}</td>`;
      tr.onclick = () => { select(i); openResult(i); };
      tb.appendChild(tr);
    });
  } catch (e) { log("search failed: " + e); }
  finally { setBusy(false); field.classList.remove("is-searching"); tabCounts(); }
}
$("#go").onclick = doSearch;
$("#q").addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  doSearch();                 // reads the value synchronously
  $("#q").value = "";         // Enter clears the field, ready for the next query
});

/* Changing the search type, the result count or the knowledge bases re-runs the
   query by itself — and re-shows the query it is re-running, since Enter clears
   the field. A short debounce keeps ticking several knowledge bases from firing a
   search per click. */
let rerunTimer = null;
function rerunSearch(delay = 0) {
  clearTimeout(rerunTimer);
  rerunTimer = setTimeout(() => {
    const typed = $("#q").value.trim();
    const q = typed || state.lastQuery || "";
    if (!q) return;
    if (!typed) $("#q").value = q;
    doSearch();
  }, delay);
}
$$("input[name=mode]").forEach((r) => (r.onchange = () => rerunSearch()));
$("#k").onchange = () => rerunSearch();

/* A result is presented in the modal only — the row highlight is just feedback. */
function select(i) {
  const h = state.results[i];
  if (!h) return;
  state.cur = h;
  $$("#rtable tbody tr").forEach((tr, j) => tr.classList.toggle("is-sel", j === i));
}

const copy = async (s) => { try { await navigator.clipboard.writeText(s); } catch (e) {} };

/* --------------------------------------- result modal + document viewer */
function openResult(i) {
  const h = state.results[i];
  if (!h) return;
  state.cur = h;
  const dlg = $("#rmodal");
  const kb0 = h.knowledge_base || {};
  $("#rmodal-crumb").textContent = `${h.library || "—"}  ·  page ${h.page}`;
  $("#rmodal-title").textContent = h.filename || h.chunk_id;
  $("#rmodal-text").textContent = h.text;
  $("#rmodal-context").textContent = "loading page context…";
  $("#rmodal-show").textContent = `Show in document (p.${h.page})`;
  $("#rmodal-open").href = h.url;
  $("#rmodal-note").textContent = `${h.library || "—"} · ${kb0.file || "—"}`
    + `${kb0.store ? ` (${kb0.store})` : ""} · ${h.citation || ""}`;
  $("#rmodal-show").textContent = `Reload at p.${h.page}`;
  const frame = $("#rmodal-frame");
  const hint = $("#rmodal-hint");
  frame.hidden = false;                      // the original is always shown
  hint.hidden = false;
  hint.textContent = "loading the original document…";
  frame.removeAttribute("src");
  frame.onload = () => { hint.hidden = true; };
  frame.onerror = () => {
    hint.hidden = false;
    hint.textContent = "The original could not be previewed here — use “Open in new tab”.";
  };

  if (typeof dlg.showModal === "function") { if (!dlg.open) dlg.showModal(); }
  else dlg.setAttribute("open", "");

  frame.setAttribute("src", h.url);          // …/api/pdf/<library>/<doc>#page=N

  api.get(`/api/context?doc_id=${encodeURIComponent(h.doc_id)}&page=${h.page}`
    + `&library=${encodeURIComponent(h.library || "")}`)
    .then((ctx) => {
      const t = (ctx.chunks || []).map((c) => `[p.${c.page}] ${(c.text || "").replace(/\s+/g, " ")}`).join("\n");
      $("#rmodal-context").textContent = t || "(no neighbouring text on this page)";
    })
    .catch(() => { $("#rmodal-context").textContent = "(context unavailable)"; });
}

$("#rmodal-show").onclick = () => {
  const h = state.cur;
  if (!h) return;
  const frame = $("#rmodal-frame");
  const hint = $("#rmodal-hint");
  frame.removeAttribute("src");
  frame.hidden = false;
  frame.onload = () => { hint.hidden = true; };
  hint.hidden = false;
  hint.textContent = "reloading the original…";
  frame.setAttribute("src", h.url);          // …/api/pdf/<library>/<doc>#page=N
};
$("#rmodal-close").onclick = () => $("#rmodal").close();
$("#rmodal").addEventListener("click", (e) => { if (e.target === $("#rmodal")) $("#rmodal").close(); });
$("#rmodal-cite").onclick = () => state.cur && copy(state.cur.citation);
$("#rmodal-text-copy").onclick = () => state.cur && copy(state.cur.text);

/* ----------------------------------------------------------- library */
const fmtDate = (t) => t ? new Date(t * 1000).toISOString().slice(0, 16).replace("T", " ") : "";

async function loadDocs() {
  try {
    const keep = (state.docs[state.selDoc] || {}).doc_id;   // a re-render must not move the selection
    const r = await api.get("/api/documents");
    state.docs = r.documents || [];
    const idx = keep ? state.docs.findIndex((x) => x.doc_id === keep) : -1;
    state.selDoc = idx >= 0 ? idx : (state.docs.length ? 0 : -1);
    const cur = $("#lib-current");
    if (cur) {
      const act = state.libraries.find((l) => l.active);
      cur.textContent = act ? `${act.name}  (${act.backend})` : "—";
    }
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
    tabCounts();
    refresh();
  } catch (e) { log("documents unavailable: " + e); }
}
const curDoc = () => state.docs[state.selDoc];

/* Live counts on the menubar: hits · saved sources · documents. */
function tabCounts() {
  const set = (panel, n) => {
    const t = $$(".tab").find((x) => x.dataset.panel === panel);
    if (t && t.dataset.n !== undefined) t.dataset.n = n ? String(n) : "";
  };
  set("search", state.results.length);
  set("sources", state.sources.length);
  set("library", state.docs.length);
}

$("#refresh").onclick = () => { refresh(); loadDocs(); };
$("#open-any").onclick = () => { const d = curDoc(); if (d) window.open(`/api/pdf/${d.doc_id}`, "_blank"); };
$("#export").onclick = async () => {
  const d = curDoc(); if (!d) return;
  const r = await api.post("/api/export", new URLSearchParams({ doc_id: d.doc_id }));
  alert("Written:\n" + r.path);
};
function fmtBytes(n) {
  const v0 = Number(n) || 0;
  if (v0 <= 0) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = v0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${u[i]}`;
}
$("#del").onclick = async () => {
  const d = curDoc(); if (!d) return;
  const n = d.chunks ?? d.nchunks ?? "its";
  if (!confirm(`Remove '${d.filename}' from the knowledge base?\n\n`
      + `${n} chunks and their embeddings are deleted as well, together with the `
      + `stored copy of the file.`)) return;
  setBusy(true);
  try {
    const r = await api.post("/api/delete", new URLSearchParams({ doc_id: d.doc_id }));
    const rm = r.removed || {};
    setText("#lib-result", `deleted '${d.filename}' — ${rm.chunks_removed ?? 0} chunks · `
      + `${rm.vectors_removed ?? 0} embeddings`
      + (rm.export_removed ? " · text export removed" : "")
      + (rm.source_removed ? " · stored copy removed" : "")
      + (rm.vector_error ? `\nvector purge error: ${rm.vector_error}` : ""));
    await refresh(); await loadDocs();
  } catch (e) { setText("#lib-result", `delete failed — ${e}`); }
  finally { setBusy(false); }
};
$("#reset").onclick = async () => {
  if (!confirm("Delete ALL documents, chunks and vectors?\n\nThe knowledge-base file is backed up first.")) return;
  const r = await api.post("/api/reset", new URLSearchParams({ confirm: "RESET" }));
  $("#rtable tbody").innerHTML = "";
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
      <span class="inputrow">
        <input id="sf-${esc(f.key)}" data-key="${esc(f.key)}"
               type="${f.secret ? "password" : "text"}" spellcheck="false" autocomplete="off"
               placeholder="${esc(f.placeholder || "")}">
        ${f.browse ? `<button class="btn browse" type="button"
             data-target="sf-${esc(f.key)}" data-kind="${esc(f.browse)}">Browse…</button>` : ""}
      </span></label>`).join("")
    + (c.available ? "" : `<div class="notice">Needs a package: <code>${esc(c.install || "")}</code></div>`);
  wireBrowseButtons(el);
}

/* Pick a folder with the machine's own dialog — the server runs on this Mac,
   so it can hand back a real absolute path (a web page cannot read one). */
function pickFolder(targetId, kind, btn) {
  const inp = document.getElementById(targetId);
  if (!inp) return;
  const section = btn && btn.closest("section");
  const out = section && section.id === "panel-settings" ? "#st-note" : "#src-result";
  const label = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "waiting…"; }
  api.post("/api/sources/browse", new URLSearchParams({ kind: kind || "dir", start: inp.value.trim() }))
    .then((r) => {
      if (r.ok) {
        inp.value = r.path;
        setText(out, (r.is_dir === false ? "selected file: " : "selected folder: ") + r.path);
      } else {
        setText(out, r.cancelled ? "folder picker cancelled" : `folder picker: ${r.error}`);
      }
    })
    .catch((e) => setText(out, "folder picker failed: " + e))
    .finally(() => { if (btn) { btn.disabled = false; btn.textContent = label; } });
}

function wireBrowseButtons(scope) {
  const nodes = scope ? scope.querySelectorAll("button.browse") : document.querySelectorAll("button.browse");
  nodes.forEach((b) => {
    if (b.dataset.wired) return;
    b.dataset.wired = "1";
    b.onclick = () => pickFolder(b.dataset.target, b.dataset.kind || "dir", b);
  });
}
wireBrowseButtons();

const srcForm = () => {
  const fd = new URLSearchParams();
  fd.set("name", $("#src-name").value.trim());
  fd.set("type", $("#src-type").value);
  $$("#src-fields input").forEach((i) => fd.set(i.dataset.key, i.value.trim()));
  return fd;
};

function renderSources() {
  tabCounts();
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
  try {
    const r = await api.post("/api/sources/scan", new URLSearchParams({ name: state.srcSel }));
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
  } catch (e) { setText("#src-result", `scan failed — ${e}`); }
  finally { setBusy(false); }
};
$("#src-ingest").onclick = async () => {
  const picked = $$("#stable tbody input[type=checkbox]").filter((c) => c.checked)
    .map((c) => state.scan[+c.dataset.i].remote);
  if (!picked.length) { setText("#src-result", "tick at least one file"); return; }
  setBusy(true); setText("#src-result", `ingesting ${picked.length} file(s) …`);
  try {
    const fd = new URLSearchParams({
      name: state.srcSel, files: JSON.stringify(picked),
      force: $("#src-force").checked ? "true" : "false",
    });
    const r = await api.post("/api/sources/ingest", fd);
    if (!r.ok) { setText("#src-result", `ingest failed — ${r.error}`); return; }
    const lines = (r.results || []).map(ingestLine);
    setText("#src-log", lines.join("\n"));
    setText("#src-result", `done — ${ingestSummary(r.results) || "nothing to do"}`);
    refresh(); loadDocs();
  } catch (e) { setText("#src-result", `ingest failed — ${e}`); }
  finally { setBusy(false); }
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

  state.lastSettings = r;          // the extractor note reads this — set it before filling
  renderModels(env.ollama.models || []);
  fillStorage(r);
  fillExtraction(r);
  state.libraries = r.libraries || [];
  renderLibraries(r.active_library);
  renderSetup();                   // the extractor + version live in here
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

  // --- search scope: which knowledge bases a query looks in
  const names = state.libraries.map((l) => l.name);
  state.libs = (state.libs || []).filter((n) => names.includes(n));
  if (!state.libs.length) state.libs = [active || names[0]];
  $("#scope-list").innerHTML = state.libraries.map((l) => `
    <label><input type="checkbox" value="${esc(l.name)}">
      <span>${esc(l.name)}${l.active ? "  ✓" : ""}</span>
      <span class="micro">${l.documents != null ? l.documents : "—"}</span></label>`).join("");
  $$("#scope-list input[type=checkbox]").forEach((c) => (c.onchange = onScopeChange));
  updateScope();

  const tb = $("#libtable tbody");
  tb.innerHTML = state.libraries.map((l) => `
    <tr data-name="${esc(l.name)}">
      <td class="src">${esc(l.name)}${l.active ? "  ✓" : ""}</td>
      <td class="mono">${esc(l.collection || l.backend || "")}</td>
      <td class="mono">${l.documents != null ? l.documents : "—"}</td>
      <td class="mono">${esc(l.container || l.data_dir || "")}</td>
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
  try {
    const r = await api.post("/api/libraries/use", new URLSearchParams({ name }));
    const st = r.stats || {};
    setText("#lib-result", r.ok
      ? `active library: ${r.active}\n${st.documents ?? 0} documents · ${st.chunks ?? 0} chunks · `
        + `${st.vectors ?? 0} vectors · ${st.vector_backend || ""}`
      : `switch failed — ${r.error}`);
    await refresh(); await loadSettings(); await loadDocs(); await loadLibraries();
  } catch (e) { setText("#lib-result", `switch failed — ${e}`); }
  finally { setBusy(false); }
}

async function deleteLibrary(name) {
  const lib = (state.libraries || []).find((l) => l.name === name) || {};
  const n = lib.documents == null ? "its" : `${lib.documents}`;
  if (!confirm(`Delete the knowledge base '${name}'?\n\n`
      + `This removes ${n} documents with all their text, chunks and embeddings, and deletes `
      + `its database and files on disk:\n${lib.data_dir || "(unknown location)"}\n\n`
      + `This cannot be undone.`)) return;
  setBusy(true);
  try {
    const r = await api.post("/api/libraries/delete", new URLSearchParams({ name, purge: "1" }));
    const p = r.purge || {};
    setText("#lib-result", r.ok
      ? `deleted '${name}'\n`
        + (p.purged
            ? `removed ${p.documents ?? "?"} documents · freed ${fmtBytes(p.freed_bytes)}\nfrom ${p.purged}`
            : `nothing removed from disk — ${p.skipped || "unknown reason"}`)
        + (p.error ? `\npurge error: ${p.error}` : "")
      : `delete failed — ${r.error}`);
    await loadLibraries(); await loadDocs(); await refresh();
  } catch (e) { setText("#lib-result", `delete failed — ${e}`); }
  finally { setBusy(false); }
}

$("#lib-select").onchange = (e) => useLibrary(e.target.value);

/* ------------------------------------------------------- search scope */
function updateScope() {
  const total = state.libraries.length || 1;
  $("#scope-count").textContent = state.libs.length >= total
    ? `all ${total}` : `${state.libs.length}/${total}`;
  $$("#scope-list input[type=checkbox]").forEach((c) => { c.checked = state.libs.includes(c.value); });
}

function onScopeChange() {
  state.libs = $$("#scope-list input:checked").map((x) => x.value);
  if (!state.libs.length) {
    const act = (state.libraries.find((l) => l.active) || {}).name || state.libraries[0].name;
    state.libs = [act];
  }
  localStorage.setItem("pdfkb-libs", JSON.stringify(state.libs));
  updateScope();
  rerunSearch(250);            // a different set of knowledge bases is a different search
}

$("#scope-all").onclick = () => {
  state.libs = state.libraries.map((l) => l.name);
  localStorage.setItem("pdfkb-libs", JSON.stringify(state.libs));
  updateScope();
  rerunSearch(250);
};
$("#scope-active").onclick = () => {
  const act = (state.libraries.find((l) => l.active) || {}).name;
  if (act) {
    state.libs = [act];
    localStorage.setItem("pdfkb-libs", JSON.stringify(state.libs));
    updateScope();
    rerunSearch(250);
  }
};
$("#lib-add").onclick = async () => {
  const name = $("#lib-name").value.trim();
  if (!name) { setText("#lib-result", "give the knowledge base a name first"); return; }
  // its own container: no paths are sent, so it cannot inherit the current one's
  const r = await api.post("/api/libraries/save", new URLSearchParams({ name }));
  setText("#lib-result", r.ok
    ? `created '${name}' as its own container\n  container   ${r.container || ""}`
      + `\n  collection  ${r.collection || ""}`
      + `\n\nIt starts empty and never touches another knowledge base's files.`
      + ` Switch to it with 'use'.`
    : `failed — ${r.error}`);
  $("#lib-name").value = "";
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
  const ing = (s && s.ingest) || {};

  // an ingest outranks everything: it is what the user is waiting for
  if (ing.running) {
    el.hidden = false;
    el.dataset.kind = "ingest";
    el.innerHTML = `<span class="dot"></span>indexing ${esc(ing.filename || "…")}`
      + (ing.total > 1 ? ` · ${ing.index}/${ing.total}` : "")
      + ` · ${ing.pct || 0}%`;
    bar.classList.add("on");
    return;
  }
  if (!s || (!s.running && s.ok === null)) {
    el.hidden = true;
    bar.classList.remove("on");
    return;
  }
  el.dataset.kind = "job";
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

/* the same progress, on the page that started it */
function liveIngest(ing) {
  const el = $("#ingest-live");
  if (!el) return;
  if (!ing || !ing.running) {
    if (!ing || ing.done === null || ing.done === undefined) { el.hidden = true; return; }
    el.hidden = false;
    el.textContent = (ingestSummary(ing.results) || "nothing to do")
      + (ing.done && ing.started ? `  ·  ${Math.round(ing.done - ing.started)}s` : "");
    return;
  }
  el.hidden = false;
  el.textContent = `${ing.detail || "working"}\n${ing.filename || ""}`
    + (ing.total > 1 ? `  ·  file ${ing.index}/${ing.total}` : "")
    + `  ·  ${ing.pct || 0}%`;
  bar(ing.pct || 0);
}

/* one polling loop for any running install/pull/ingest, wherever the user is */
async function watchJob() {
  clearTimeout(jobTimer);
  let s;
  try { s = await api.get("/api/settings/job"); } catch (e) { return; }
  const wasIngesting = state.ingesting === true;
  state.ingesting = !!(s.ingest && s.ingest.running);
  state.job = s;
  jobBadge(s);
  liveIngest(s.ingest);
  const el = $("#job-log");
  if (el && (s.kind || (s.lines || []).length)) {
    el.hidden = false;
    el.textContent = (s.kind ? `${s.kind} ${s.target}\n` : "") + (s.lines || []).join("\n")
      + (s.running ? "" : (s.ok ? "\n\ndone." : "\n\nfailed."));
    if ($("#panel-settings")?.classList.contains("is-active")) el.scrollTop = el.scrollHeight;
  }
  if (s.running || state.ingesting) jobTimer = setTimeout(watchJob, 1000);
  else if (s.ok) loadSettings();
  // it finished while the user was somewhere else — bring the lists up to date
  if (wasIngesting && !state.ingesting) { refresh(); loadDocs(); }
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
    // never `disabled`: an extractor without its package is still selectable — the
    // server falls back and says so, and refusing the click just looks broken
    xs.innerHTML = (env.extractors || []).map((e) =>
      `<option value="${e.id}" data-ok="${e.available ? 1 : 0}">${esc(e.label)}`
      + `${e.available ? "" : " — needs its package"}</option>`).join("");
    xs.dataset.filled = "1";
  }
  xs.value = s.extractor || "pymupdf";
  $("#x-ocr").checked = String(s.docling_ocr || "false").toLowerCase() === "true";
  $("#x-docling-url").value = s.docling_url || "";
  const d = env.docling || {};
  const svc = d.service_url
    ? `<div class="kvrow"><span class="kvk">docling service</span>`
      + `<span class="kvv ${d.service_ready ? "ok" : "bad"}">${esc(d.service_url)}`
      + ` — ${d.service_ready ? "answering" : "not answering"}</span></div>`
    : "";
  $("#x-extras").innerHTML =
    kv("docling", d.installed ? `installed${d.version ? " " + d.version : ""}` : "not installed")
    + svc
    + ((d.installed || d.service_url) ? ""
       : kv("install", d.install_cmd || "python3 -m pip install docling"));
  xNote();
}

/* The note describes the extractor selected right now — so it is its own function:
   folding it into the form filler made the dropdown snap back on every change. */
function xNote() {
  const env = (state.lastSettings || {}).env || {};
  const d = env.docling || {};
  const chosen = (env.extractors || []).find((e) => e.id === $("#x-extractor").value);
  const note = $("#x-note");
  note.hidden = !(chosen && chosen.id === "docling");
  note.innerHTML = (chosen && chosen.id === "docling")
    ? ((d.service_url && d.service_ready)
        ? `Docling is the default extractor, served here by the <b>docling service</b> at `
          + `<code>${esc(d.service_url)}</code>: layout- and table-aware, and it can OCR scans. `
          + `The OCR switch below applies to it.`
        : d.installed
        ? `Docling is the default extractor — installed${d.version ? ` ${esc(d.version)}` : ""}: `
          + `layout- and table-aware, and it can OCR scans. The OCR switch below applies to it.`
        : (d.service_url
            ? `Docling is the default extractor and a service is configured at `
              + `<code>${esc(d.service_url)}</code>, but it is <b>not answering</b>: ingestion falls `
              + `back to PyMuPDF and says so in the ingest log. Check that the container is up `
              + `(<code>docker compose --profile docling up -d</code>).`
            : `Docling is the default extractor but is <b>not installed on this machine</b>: ingestion `
              + `falls back to PyMuPDF and says so in the ingest log. Install it with `
              + `<code>${esc(d.install_cmd || "python3 -m pip install docling")}</code>, or point `
              + `this app at a docling service container, then re-ingest.`))
    : "";
}

$("#x-extractor").onchange = xNote;
$("#x-save").onclick = async () => {
  const r = await api.post("/api/settings/save", new URLSearchParams({
    extractor: $("#x-extractor").value,
    docling_ocr: $("#x-ocr").checked ? "true" : "false",
    docling_url: $("#x-docling-url").value.trim(),
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
setTimeout(() => { try { $("#q").focus(); } catch (e) {} }, 350);   // ready to type
