/* 考古地层关系推断台 —— 原生 JS 前端 */
"use strict";

const REL_META = {
  earlier: { label: "早于", symbol: "→", color: "#4e7a52" },
  cuts:    { label: "切割", symbol: "✂", color: "#c1502e" },
  fills:   { label: "填充", symbol: "▽", color: "#3f6d9e" },
};

const state = {
  areas: [], nodes: [], edges: [], groups: [], height: 0,
  affected: [],
  nodeById: new Map(),
  // 邻接（时间方向 early -> late）：nodeId -> Map(lateId -> [edge...])
  adj: new Map(),
  // subject/object 原始方向的直接关系： "s|o" -> [edge]
  raw: new Map(),
  selectedNode: null,
  highlightPath: null, // [nodeId...]
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

// ---------------------------------------------------------------------
// 数据加载
// ---------------------------------------------------------------------
async function api(path, opts) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  let body = null;
  try { body = await r.json(); } catch (e) { /* noop */ }
  return { ok: r.ok, status: r.status, body };
}

function indexState(s) {
  state.areas = s.areas;
  state.nodes = s.nodes;
  state.edges = s.edges;
  state.groups = s.groups;
  state.height = s.height;
  state.affected = s.affected || [];
  state.nodeById = new Map(s.nodes.map((n) => [n.id, n]));
  state.adj = new Map(s.nodes.map((n) => [n.id, new Map()]));
  state.raw = new Map();
  for (const e of s.edges) {
    if (!state.adj.has(e.early)) state.adj.set(e.early, new Map());
    const m = state.adj.get(e.early);
    if (!m.has(e.late)) m.set(e.late, []);
    m.get(e.late).push(e);
    const key = `${e.subject}|${e.object}`;
    if (!state.raw.has(key)) state.raw.set(key, []);
    state.raw.get(key).push(e);
  }
}

async function refresh(showAffected = false) {
  const { body } = await api("/api/state");
  indexState(body);
  renderAll(showAffected);
}

// ---------------------------------------------------------------------
// 渲染总入口
// ---------------------------------------------------------------------
function renderAll(showAffected = false) {
  renderSelects();
  renderGraph();
  renderMatrix();
  renderInference();
  renderHistory();
  renderSummary();
  if (showAffected) flashAffected();
}

function renderSummary() {
  $("#graph-summary").textContent =
    `${state.nodes.length} 个对象 · ${state.edges.length} 条关系 · ` +
    `时间跨度 ${state.height} 层`;
}

// ---------------------------------------------------------------------
// 下拉框
// ---------------------------------------------------------------------
function unitLabel(n) {
  return `[${n.area_code}] ${n.code}${n.name ? " " + n.name : ""}`;
}

function sortedNodes() {
  return [...state.nodes].sort((a, b) =>
    a.area_code.localeCompare(b.area_code) ||
    a.earliest - b.earliest ||
    a.code.localeCompare(b.code));
}

function fillSelect(sel, predicate) {
  const cur = sel.value;
  sel.innerHTML = "";
  for (const n of sortedNodes()) {
    if (predicate && !predicate(n)) continue;
    const o = document.createElement("option");
    o.value = n.id;
    o.textContent = unitLabel(n);
    sel.appendChild(o);
  }
  if (cur) sel.value = cur;
}

function renderSelects() {
  fillSelect($("#rel-subject"));
  fillSelect($("#rel-object"));
  fillSelect($("#basis-from"));
  fillSelect($("#basis-to"));
  updateRelSemantic();
}

function updateRelSemantic() {
  const t = $("#rel-type").value;
  const s = state.nodeById.get($("#rel-subject").value);
  const o = state.nodeById.get($("#rel-object").value);
  let txt = "";
  if (s && o) {
    if (t === "earlier") txt = `时间方向：${s.code}（早） → ${o.code}（晚）`;
    if (t === "cuts") txt = `时间方向：${o.code}（早，被打破） → ${s.code}（晚，切割者）`;
    if (t === "fills") txt = `时间方向：${o.code}（早，遗迹） → ${s.code}（晚，填土）`;
  }
  $("#rel-semantic").textContent = txt;
}

// ---------------------------------------------------------------------
// SVG 关系图（分层布局，最早在底）
// ---------------------------------------------------------------------
const view = { x: 0, y: 0, k: 1 };
const NODE_W = 92, NODE_H = 46, LEVEL_GAP = 104, NODE_GAP = 26;

function levelOf(n) { return n.earliest; }

function layout() {
  // 按 earliest 分层；同层节点按面积与编号排序
  const layers = new Map();
  for (const n of state.nodes) {
    const lv = levelOf(n);
    if (!layers.has(lv)) layers.set(lv, []);
    layers.get(lv).push(n);
  }
  const pos = new Map();
  const maxRows = Math.max(...[...layers.values()].map((l) => l.length), 1);
  const canvasH = (state.height + 1) * LEVEL_GAP + 40;
  const canvasW = Math.max(maxRows * (NODE_W + NODE_GAP) + 60, 700);

  for (const [lv, arr] of layers) {
    arr.sort((a, b) =>
      a.area_code.localeCompare(b.area_code) ||
      a.kind.localeCompare(b.kind) ||
      a.code.localeCompare(b.code));
    const rowW = arr.length * NODE_W + (arr.length - 1) * NODE_GAP;
    let x = (canvasW - rowW) / 2;
    // y：最早(0) 在底
    const y = canvasH - 30 - (lv + 1) * LEVEL_GAP + 26;
    for (const n of arr) {
      pos.set(n.id, { x, y, w: NODE_W, h: NODE_H });
      x += NODE_W + NODE_GAP;
    }
  }
  return { pos, canvasW, canvasH };
}

let currentLayout = null;

function renderGraph() {
  currentLayout = layout();
  const { pos, canvasW, canvasH } = currentLayout;
  const svg = $("#graph");
  svg.setAttribute("viewBox", `0 0 ${canvasW} ${canvasH}`);
  svg.dataset.w = canvasW;
  svg.dataset.h = canvasH;

  // 层位背景带
  const vp = $("#viewport");
  vp.querySelectorAll(".layer-band, .layer-band-label").forEach((n) => n.remove());
  for (let lv = 0; lv <= state.height; lv++) {
    const y = canvasH - 30 - (lv + 1) * LEVEL_GAP + 2;
    const band = el("rect", {
      class: "layer-band", x: 0, y, width: canvasW,
      height: LEVEL_GAP - 8,
    });
    vp.insertBefore(band, vp.firstChild);
    const lb = el("text", {
      class: "layer-band-label", x: 8, y: y + 14,
    }, `层位 ${lv}${lv === 0 ? "（最早）" : ""}${lv === state.height ? "（最晚）" : ""}`);
    vp.insertBefore(lb, vp.firstChild);
  }

  renderEdges(pos);
  renderNodes(pos);
  applyView();
  applyGraphHighlight();
}

function renderNodes(pos) {
  const layer = $("#node-layer");
  layer.innerHTML = "";
  for (const n of state.nodes) {
    const p = pos.get(n.id);
    const g = el("g", {
      class: `node ${n.kind}`,
      transform: `translate(${p.x},${p.y})`,
      "data-id": n.id,
    });
    g.appendChild(el("rect", { width: p.w, height: p.h, rx: 7 }));
    g.appendChild(el("text", { class: "code", x: p.w / 2, y: 19 }, n.code));
    const lvTxt = n.fixed
      ? `层位 ${n.earliest}`
      : `层位 ${n.earliest}~${n.latest}`;
    g.appendChild(el("text", { class: "lv", x: p.w / 2, y: 35 }, lvTxt));
    g.addEventListener("click", (ev) => {
      ev.stopPropagation();
      selectNode(n.id);
    });
    layer.appendChild(g);
  }
}

function renderEdges(pos) {
  const layer = $("#edge-layer");
  layer.innerHTML = "";
  for (const e of state.edges) {
    const a = pos.get(e.early);
    const b = pos.get(e.late);
    if (!a || !b) continue;
    const x1 = a.x + a.w / 2;
    const y1 = a.y; // 早端顶部
    const x2 = b.x + b.w / 2;
    const y2 = b.y + b.h; // 晚端底部
    // 同层不会有边；轻微弯曲减少重叠
    const dx = x2 - x1;
    const c = 18;
    const d = `M${x1},${y1} C${x1},${y1 - c} ${x2},${y2 + c} ${x2},${y2}`;
    const path = el("path", {
      class: `edge-path ${e.type}`,
      d, "data-id": e.id,
      "marker-end": `url(#arrow-${e.type})`,
    });
    path.addEventListener("click", (ev) => {
      ev.stopPropagation();
      confirmDeleteEdge(e);
    });
    layer.appendChild(path);

    // 关系标签：取三次贝塞尔曲线 t=0.5 处
    // M = P0/8 + 3P1/8 + 3P2/8 + P3/8
    const midX = (x1 + 3 * x1 + 3 * x2 + x2) / 8;
    const midY = (y1 + 3 * (y1 - c) + 3 * (y2 + c) + y2) / 8;
    const t = el("text", {
      class: "edge-label", x: midX, y: midY + 3,
    }, REL_META[e.type].label);
    layer.appendChild(t);
  }
}

function applyView() {
  $("#viewport")
    .setAttribute("transform", `translate(${view.x},${view.y}) scale(${view.k})`);
}

function applyGraphHighlight() {
  const sel = state.selectedNode;
  const path = state.highlightPath;
  const pathSet = new Set(path || []);
  const pathEdgeKeys = new Set();
  if (path) {
    for (let i = 0; i < path.length - 1; i++) {
      for (const e of (state.adj.get(path[i]).get(path[i + 1]) || []))
        pathEdgeKeys.add(e.id);
    }
  }
  $("#node-layer").querySelectorAll(".node").forEach((g) => {
    const id = g.dataset.id;
    g.classList.toggle("selected", id === sel);
    g.classList.toggle("affected", state.affected.includes(id));
    if (!sel && !path) { g.classList.remove("dim"); return; }
    let related = true;
    if (sel) {
      related = id === sel ||
        isReachable(sel, id) || isReachable(id, sel);
    }
    if (path) related = pathSet.has(id) || !sel;
    g.classList.toggle("dim", !related);
  });
  $("#edge-layer").querySelectorAll(".edge-path").forEach((p) => {
    const id = p.dataset.id;
    p.classList.toggle("highlight", pathEdgeKeys.has(id));
    if (!sel && !path) { p.classList.remove("dim"); return; }
    let related = !sel;
    if (sel) {
      const e = state.edges.find((x) => x.id === id);
      related = e.subject === sel || e.object === sel ||
        e.early === sel || e.late === sel;
    }
    if (path) related = pathEdgeKeys.has(id) ? true : (!sel && related);
    p.classList.toggle("dim", !related);
  });
}

// 可达性（BFS，供前端高亮/推断链查询）
function reachableFrom(src) {
  const seen = new Set([src]);
  const q = [src];
  while (q.length) {
    const u = q.shift();
    for (const v of (state.adj.get(u) || new Map()).keys()) {
      if (!seen.has(v)) { seen.add(v); q.push(v); }
    }
  }
  return seen;
}
function isReachable(a, b) { return reachableFrom(a).has(b); }

function shortestPath(src, dst) {
  if (src === dst) return [src];
  const prev = new Map([[src, null]]);
  const q = [src];
  while (q.length) {
    const u = q.shift();
    for (const v of (state.adj.get(u) || new Map()).keys()) {
      if (prev.has(v)) continue;
      prev.set(v, u);
      if (v === dst) {
        const out = [v];
        while (prev.get(out[out.length - 1]) != null)
          out.push(prev.get(out[out.length - 1]));
        return out.reverse();
      }
      q.push(v);
    }
  }
  return null;
}

// ---------------------------------------------------------------------
// 检查器
// ---------------------------------------------------------------------
function selectNode(id) {
  state.selectedNode = state.selectedNode === id ? null : id;
  state.highlightPath = null;
  renderInspector();
  applyGraphHighlight();
}

function renderInspector() {
  const box = $("#inspector");
  const id = state.selectedNode;
  if (!id) {
    box.innerHTML = `<h3>对象检查器</h3><p class="hint">点击图中节点查看其全部关系与推断结果。</p>`;
    return;
  }
  const n = state.nodeById.get(id);
  const outgoing = [], incoming = [];
  for (const e of state.edges) {
    if (e.subject === id) outgoing.push(e);
    if (e.object === id) incoming.push(e);
  }
  box.innerHTML = "";
  box.appendChild(el("h3", {}, `${n.code} ${n.name || ""}`));
  const kv = el("dl", { class: "kv" });
  kv.innerHTML =
    `<dt>发掘区</dt><dd>${n.area_code}</dd>` +
    `<dt>类型</dt><dd><span class="pill ${n.kind}">${n.kind === "layer" ? "地层" : "遗迹"}</span></dd>` +
    `<dt>层位</dt><dd>${n.earliest} ～ ${n.latest} ` +
      `<span class="pill ${n.fixed ? "fixed" : "loose"}">${n.fixed ? "已锁定" : `可浮动 ${n.slack} 层`}</span></dd>`;
  box.appendChild(kv);

  const relList = (title, arr, dir) => {
    box.appendChild(el("div", { class: "hint", style: "margin:6px 0 3px" }, title));
    if (!arr.length) box.appendChild(el("div", { class: "hint" }, "（无）"));
    for (const e of arr) {
      const other = state.nodeById.get(dir === "out" ? e.object : e.subject);
      const row = el("div", { class: "rel-row" });
      row.appendChild(el("span", { class: `tag ${e.type}` }, REL_META[e.type].label));
      let txt;
      if (dir === "out") {
        // 当前对象是 subject
        txt = e.type === "earlier"
          ? `早于 ${other.code}`
          : `${REL_META[e.type]} ${other.code}`;
      } else {
        // 当前对象是 object
        txt = e.type === "earlier"
          ? `晚于 ${other.code}`
          : `被${other.code} ${REL_META[e.type]}`;
      }
      row.appendChild(el("span", {}, txt));
      const x = el("span", { class: "x", title: "删除关系" }, "×");
      x.addEventListener("click", () => confirmDeleteEdge(e));
      row.appendChild(x);
      box.appendChild(row);
    }
  };
  relList("作为主体（A）：", outgoing, "out");
  relList("作为客体（B）：", incoming, "in");

  const del = el("button", { class: "btn danger", style: "width:100%;margin-top:8px" },
    `删除对象 ${n.code}（连同其 ${outgoing.length + incoming.length} 条关系）`);
  del.addEventListener("click", () => deleteUnit(n));
  box.appendChild(del);
}

function flashAffected() {
  const card = $("#affected-card");
  const list = $("#affected-list");
  if (!state.affected.length) { card.hidden = true; return; }
  card.hidden = false;
  list.innerHTML = "";
  for (const id of state.affected) {
    const n = state.nodeById.get(id);
    if (n) list.appendChild(el("span", { class: "atag" }, n.code));
  }
  applyGraphHighlight();
  clearTimeout(flashAffected._t);
  flashAffected._t = setTimeout(() => {
    state.affected = [];
    applyGraphHighlight();
  }, 6000);
}

// ---------------------------------------------------------------------
// 矩阵
// ---------------------------------------------------------------------
function renderMatrix() {
  const table = $("#matrix");
  const nodes = sortedNodes();
  const showIndirect = $("#chk-indirect").checked;
  const showGroups = $("#chk-groupbounds").checked;

  // 可达闭包（按需计算，一次构建完）
  const closure = new Map();
  for (const n of nodes) closure.set(n.id, reachableFrom(n.id));

  // 可互换组成员索引
  const groupOf = new Map();
  state.groups.forEach((g, gi) => g.members.forEach((m) => groupOf.set(m, gi)));

  let html = `<thead><tr><th class="corner">早 ↓ ／ 晚 →</th>`;
  for (const b of nodes) {
    html += `<th title="[${b.area_code}] ${b.code}">${b.code}<br>
             <span class="chain-note">${b.area_code}·${b.earliest}</span></th>`;
  }
  html += `</tr></thead><tbody>`;
  for (const a of nodes) {
    html += `<tr><th title="${escapeHtml(a.name || "")}">[${a.area_code}] ${a.code}</th>`;
    for (const b of nodes) {
      const attrs = [];
      if (a.id === b.id) {
        html += `<td class="diag" title="自环被禁止">·</td>`;
        continue;
      }
      if (a.area_id !== b.area_id) {
        html += `<td class="cross" title="跨发掘区不可建立关系"></td>`;
        continue;
      }
      attrs.push("cell");
      const direct = state.adj.get(a.id).get(b.id);
      if (direct) {
        const e = direct[0];
        attrs.push(`data-from="${a.id}"`, `data-to="${b.id}"`);
        const symbols = direct.map((x) => REL_META[x.type].symbol).join("");
        const names = direct.map((x) => REL_META[x.type].label).join("、");
        html += `<td class="${attrs.join(" ")}" data-from="${a.id}" data-to="${b.id}"
                 title="${names}"><span class="cell-dot ${e.type}">${symbols}</span>
                 <span class="cell-sub">${direct.length} 类</span></td>`;
      } else {
        const indirect = closure.get(a.id).has(b.id);
        if (indirect && showIndirect) attrs.push("indirect");
        if (showGroups && groupOf.has(a.id) && groupOf.get(a.id) === groupOf.get(b.id))
          attrs.push("groupsame");
        html += `<td class="${attrs.join(" ")}" data-from="${a.id}"
                 data-to="${b.id}">${indirect && showIndirect ? "·" : ""}</td>`;
      }
    }
    html += `</tr>`;
  }
  html += `</tbody>`;
  table.innerHTML = html;

  table.querySelectorAll("td.cell").forEach((td) => {
    td.addEventListener("click", (ev) => openCellPop(td, ev));
  });
}

function openCellPop(td, ev) {
  const from = td.dataset.from, to = td.dataset.to;
  const a = state.nodeById.get(from), b = state.nodeById.get(to);
  const pop = $("#matrix-cell-info");
  const direct = state.adj.get(from).get(to) || [];
  const indirect = shortestPath(from, to);

  pop.innerHTML = "";
  pop.appendChild(el("h4", {}, `[${a.area_code}] ${a.code} → ${b.code}`));

  if (direct.length) {
    for (const e of direct) {
      const row = el("div", { class: "rel-row" });
      row.appendChild(el("span", { class: `tag ${e.type}` }, REL_META[e.type].label));
      const s = state.nodeById.get(e.subject);
      const o = state.nodeById.get(e.object);
      row.appendChild(el("span", {}, `${s.code} ${REL_META[e.type].label} ${o.code}`));
      const x = el("span", { class: "x" }, "删除");
      x.addEventListener("click", () => { pop.hidden = true; confirmDeleteEdge(e); });
      row.appendChild(x);
      pop.appendChild(row);
    }
  } else {
    pop.appendChild(el("p", { class: "hint" },
      `尚无直接关系。矩阵行→列为时间方向（${a.code} 早 → ${b.code} 晚），添加：`));
    const actions = el("div", { class: "pop-actions" });
    const hints = {
      earlier: `${a.code} 早于 ${b.code}`,
      cuts: `${b.code} 切割 ${a.code}（${b.code} 打破 ${a.code}）`,
      fills: `${b.code} 填充 ${a.code}（${b.code} 是 ${a.code} 的填土）`,
    };
    for (const t of ["earlier", "cuts", "fills"]) {
      const btn = el("button", {
        class: "btn mini",
        title: hints[t],
      }, `＋ ${REL_META[t].label}`);
      btn.addEventListener("click", () => {
        // 矩阵按时间方向（early→late=行→列）；cuts/fills 的主体是晚端（列 b）
        const payload = t === "earlier"
          ? { type: t, subject: from, object: to }
          : { type: t, subject: to, object: from };
        pop.hidden = true;
        submitRelation(payload);
      });
      actions.appendChild(btn);
    }
    pop.appendChild(actions);
  }

  if (indirect && indirect.length > 1) {
    pop.appendChild(el("div", { class: "hint", style: "margin-top:8px" },
      "间接推断链（点击在图中高亮）："));
    const chain = el("div", {});
    const codes = indirect.map((id) => state.nodeById.get(id).code).join(" → ");
    const link = el("span", {
      style: "cursor:pointer;color:#8a6410;font-weight:600",
    }, codes);
    link.addEventListener("click", () => {
      state.highlightPath = indirect;
      switchTab("graph");
      applyGraphHighlight();
      pop.hidden = true;
    });
    chain.appendChild(link);
    pop.appendChild(chain);
  }

  pop.hidden = false;
  const r = td.getBoundingClientRect();
  let left = r.left + r.width / 2 - 115;
  left = Math.max(10, Math.min(left, window.innerWidth - 260));
  pop.style.left = left + "px";
  pop.style.top = (r.bottom + 6) + "px";
}

document.addEventListener("click", (ev) => {
  const pop = $("#matrix-cell-info");
  if (!pop.hidden && !pop.contains(ev.target) && !ev.target.closest("td.cell"))
    pop.hidden = true;
});

// ---------------------------------------------------------------------
// 层位推断
// ---------------------------------------------------------------------
function renderInference() {
  const tb = $("#layer-table tbody");
  tb.innerHTML = "";
  for (const n of sortedNodes()) {
    const tr = el("tr");
    const pctE = state.height ? (n.earliest / state.height) * 100 : 0;
    const pctL = state.height ? (n.latest / state.height) * 100 : 100;
    tr.innerHTML =
      `<td>${n.area_code}</td><td><b>${n.code}</b></td>` +
      `<td>${n.kind === "layer" ? "地层" : "遗迹"}</td>` +
      `<td>${n.name || ""}</td>` +
      `<td>${n.earliest}</td><td>${n.latest}</td>` +
      `<td>${n.slack}</td>`;
    const td = el("td");
    const bar = el("div", { class: "winbar" });
    bar.appendChild(el("div", {
      class: "seg",
      style: `left:${pctE}%;right:${100 - pctL}%`,
    }));
    td.appendChild(bar);
    tr.appendChild(td);
    tb.appendChild(tr);
  }

  const gl = $("#groups-list");
  gl.innerHTML = "";
  if (!state.groups.length) {
    gl.appendChild(el("p", { class: "hint" }, "当前没有可互换对象组（所有不可比对象的前驱后继集合均不同，或已被完全排序）。"));
  }
  for (const g of state.groups) {
    const chip = el("div", { class: "group-chip" });
    chip.innerHTML =
      `<b>${g.codes.join(" · ")}</b><br>` +
      `<span class="chain-note">同处层位 ${g.earliest}` +
      (g.earliest === g.latest ? "" : `～${g.latest}`) +
      `，前驱与后继集合完全一致，可在年代序列中互换，共 ${g.members.length} 个。</span>`;
    gl.appendChild(chip);
  }
}

// ---------------------------------------------------------------------
// 版本与日志
// ---------------------------------------------------------------------
async function renderHistory() {
  const [{ body: vs }, { body: ev }] = await Promise.all([
    api("/api/versions"), api("/api/events"),
  ]);
  const vt = $("#version-table tbody");
  vt.innerHTML = "";
  for (const v of vs.versions) {
    const tr = el("tr");
    tr.innerHTML =
      `<td>R${v.id}</td><td>${fmtTime(v.created)}</td>` +
      `<td>${actionLabel(v.action)}</td><td>${escapeHtml(v.label)}</td>`;
    const td = el("td");
    const btn = el("button", { class: "btn mini" }, "回滚");
    btn.addEventListener("click", () => restoreVersion(v));
    td.appendChild(btn);
    tr.appendChild(td);
    vt.appendChild(tr);
  }
  const et = $("#event-table tbody");
  et.innerHTML = "";
  for (const e of ev.events) {
    const tr = el("tr");
    tr.innerHTML =
      `<td>${fmtTime(e.created)}</td><td>${actionLabel(e.action)}</td>` +
      `<td class="status-${e.status}">${e.status === "ok" ? "通过" : "拒绝"}</td>` +
      `<td>${escapeHtml(e.detail)}</td>`;
    et.appendChild(tr);
  }
}

function actionLabel(a) {
  return {
    seed: "初始化", create_area: "建区", create_unit: "新建对象",
    delete_unit: "删除对象", create_relation: "录入关系",
    delete_relation: "删除关系", restore_version: "版本回滚",
  }[a] || a;
}

// ---------------------------------------------------------------------
// 操作
// ---------------------------------------------------------------------
async function submitRelation(payload) {
  const r = await api("/api/relations", { method: "POST", body: JSON.stringify(payload) });
  if (r.body && r.body.code === "cycle") {
    showCycle(r.body);
  } else if (!r.ok) {
    toast(r.body?.error || "提交失败", "err");
  } else {
    indexState(r.body);
    renderAll(true);
    renderInspector();
    toast(`关系已提交，重算 ${r.body.affected.length} 个受影响对象`, "ok");
  }
}

async function confirmDeleteEdge(e) {
  const s = state.nodeById.get(e.subject);
  const o = state.nodeById.get(e.object);
  if (!confirm(`删除关系：${s.code} ${REL_META[e.type].label} ${o.code}？\n（将自动保留版本，可在“版本/日志”中回滚）`))
    return;
  const r = await api(`/api/relations/${e.id}`, { method: "DELETE" });
  if (r.ok) {
    indexState(r.body);
    state.selectedNode = null;
    renderAll(true);
    renderInspector();
    toast("关系已删除，仅重算受影响层位", "ok");
  }
}

async function deleteUnit(n) {
  if (!confirm(`删除对象 ${n.code} 及其全部关系？版本仍可回滚。`)) return;
  const r = await api(`/api/units/${n.id}`, { method: "DELETE" });
  if (r.ok) {
    indexState(r.body);
    state.selectedNode = null;
    renderAll(true);
    renderInspector();
    toast(`对象 ${n.code} 已删除`, "ok");
  }
}

async function restoreVersion(v) {
  if (!confirm(`回滚到 R${v.id}（${v.label}）？\n当前状态会先被另存为新版本，不会丢失。`)) return;
  const r = await api(`/api/versions/${v.id}/restore`, { method: "POST" });
  if (r.ok) {
    indexState(r.body);
    state.selectedNode = null;
    renderAll(true);
    renderInspector();
    toast(`已回滚到 R${v.id}`, "ok");
  }
}

// 矛盾链
function showCycle(data) {
  const box = $("#cycle-chain");
  box.innerHTML = "";
  data.chain.forEach((step, i) => {
    box.appendChild(el("span", { class: "cycle-node" }, step.from));
    box.appendChild(el("span", { class: "cycle-edge" }, step.detail));
    box.appendChild(el("span", { class: "cycle-arrow" }, "→"));
    if (i === data.chain.length - 1)
      box.appendChild(el("span", { class: "cycle-node" }, step.to));
  });
  box.appendChild(el("div", {
    style: "width:100%;margin-top:8px;font-size:12px;color:#8a4a33",
  }, `链长 ${data.length}：首尾相接形成时间循环。请删除或修改其中一条关系。`));
  $("#cycle-modal").hidden = false;
}

// ---------------------------------------------------------------------
// 图交互：缩放 / 平移
// ---------------------------------------------------------------------
(function bindGraphControls() {
  const svg = $("#graph");
  let dragging = false, sx = 0, sy = 0, ox = 0, oy = 0;
  svg.addEventListener("wheel", (e) => {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.12 : 0.89;
    const nk = Math.min(2.5, Math.max(0.3, view.k * factor));
    const rect = svg.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    view.x = mx - (mx - view.x) * (nk / view.k);
    view.y = my - (my - view.y) * (nk / view.k);
    view.k = nk;
    applyView();
  }, { passive: false });
  svg.addEventListener("mousedown", (e) => {
    if (e.target.closest(".node,.edge-path")) return;
    dragging = true; sx = e.clientX; sy = e.clientY;
    ox = view.x; oy = view.y;
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    view.x = ox + e.clientX - sx;
    view.y = oy + e.clientY - sy;
    applyView();
  });
  window.addEventListener("mouseup", () => (dragging = false));
  svg.addEventListener("click", (e) => {
    if (e.target === svg || e.target.id === "viewport" ||
        e.target.classList.contains("layer-band")) {
      state.selectedNode = null;
      state.highlightPath = null;
      renderInspector();
      applyGraphHighlight();
    }
  });
  $("#btn-reset-view").addEventListener("click", () => {
    view.x = 0; view.y = 0; view.k = 1;
    applyView();
  });
})();

// ---------------------------------------------------------------------
// 表单 / Tab / 工具
// ---------------------------------------------------------------------
function switchTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".tabpanel").forEach((p) =>
    p.classList.toggle("active", p.id === `tab-${name}`));
  // 关系图在隐藏容器中初始化时尺寸为 0，切回时重设视图
  if (name === "graph") {
    requestAnimationFrame(() => { applyView(); });
  }
}

$$(".tab").forEach((t) =>
  t.addEventListener("click", () => switchTab(t.dataset.tab)));

$("#rel-type").addEventListener("change", updateRelSemantic);
$("#rel-subject").addEventListener("change", updateRelSemantic);
$("#rel-object").addEventListener("change", updateRelSemantic);

$("#form-rel").addEventListener("submit", async (e) => {
  e.preventDefault();
  const payload = {
    type: $("#rel-type").value,
    subject: $("#rel-subject").value,
    object: $("#rel-object").value,
    note: $("#rel-note").value,
  };
  if (payload.subject === payload.object) {
    toast("主体与客体不能相同（自环被禁止）", "err");
    return;
  }
  await submitRelation(payload);
  $("#rel-note").value = "";
});

$("#btn-basis").addEventListener("click", () => {
  const from = $("#basis-from").value, to = $("#basis-to").value;
  const box = $("#basis-result");
  box.innerHTML = "";
  if (from === to) { box.textContent = "请选择两个不同对象"; return; }
  const path = shortestPath(from, to);
  if (!path) {
    box.innerHTML = `<span class="chain-note">${state.nodeById.get(from).code} 与 ${state.nodeById.get(to).code} 之间不存在时间方向上的可达关系（可能不可比或方向相反）。</span>`;
    return;
  }
  if (path.length === 2 && state.adj.get(from).get(to)) {
    box.appendChild(el("div", { class: "chain-note" }, "二者为直接关系："));
  } else {
    box.appendChild(el("div", { class: "chain-note" },
      `最短推断链（${path.length - 1} 跳）：`));
  }
  path.forEach((id, i) => {
    const n = state.nodeById.get(id);
    box.appendChild(el("span", { class: "chain-step" }, n.code));
    if (i < path.length - 1) {
      const edges = state.adj.get(id).get(path[i + 1]);
      const t = edges[0].type;
      box.appendChild(el("span", { class: "chain-arrow" },
        `—${REL_META[t].label}→`));
    }
  });
  const hl = el("button", { class: "btn mini", style: "margin-left:10px" }, "在图中高亮");
  hl.addEventListener("click", () => {
    state.highlightPath = path;
    switchTab("graph");
    applyGraphHighlight();
  });
  box.appendChild(hl);
});

$("#chk-indirect").addEventListener("change", renderMatrix);
$("#chk-groupbounds").addEventListener("change", renderMatrix);
$("#btn-refresh").addEventListener("click", () => refresh());
$("#btn-cycle-ok").addEventListener("click", () => $("#cycle-modal").hidden = true);

$("#btn-add-unit").addEventListener("click", () => {
  const areaChoices = state.areas.map((a) => `${a.id}: ${a.code} ${a.name}`).join("\n");
  const areaId = prompt("选择发掘区（输入 id）：\n" + areaChoices, state.areas[0]?.id);
  if (!areaId) return;
  const code = prompt("对象编号（如 L5、H4）：");
  if (!code) return;
  const kind = prompt("类型：layer=地层，feature=遗迹", "layer");
  if (!["layer", "feature"].includes(kind)) return toast("类型无效", "err");
  const name = prompt("名称（可留空）：", "") || "";
  api("/api/units", {
    method: "POST",
    body: JSON.stringify({ area_id: Number(areaId), code, kind, name }),
  }).then((r) => {
    if (!r.ok) return toast(r.body?.error || "新建失败", "err");
    indexState(r.body);
    renderAll(true);
    toast(`对象 ${code} 已创建`, "ok");
  });
});

$("#btn-add-area").addEventListener("click", async () => {
  const code = prompt("发掘区编号（如 T3）：");
  if (!code) return;
  const name = prompt("发掘区名称（可留空）：", "") || "";
  const r = await api("/api/areas", {
    method: "POST", body: JSON.stringify({ code, name }),
  });
  if (r.ok) { toast(`发掘区 ${code} 已创建`, "ok"); refresh(); }
  else toast(r.body?.error || "创建失败", "err");
});

// 工具函数
function el(tag, attrs, text) {
  const e = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text != null) e.textContent = text;
  return e;
}
function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const p = (x) => String(x).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
let toastTimer = null;
function toast(msg, kind) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = `toast ${kind || ""}`;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 3200);
}

// 启动
refresh();
