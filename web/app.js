const state = {
  analysis: null,
  activeEdges: new Set(["direct", "async", "callback", "function_pointer"]),
  selectedNode: null,
  queryFocus: new Set(),
  transform: { x: 0, y: 0, scale: 1 },
  drag: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const svgNS = "http://www.w3.org/2000/svg";

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function toast(message, isError = false) {
  const element = $("#toast");
  element.textContent = message;
  element.className = `toast show${isError ? " error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { element.className = "toast"; }, 2600);
}

async function request(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "请求失败");
  return payload;
}

async function loadHealth() {
  try {
    const health = await request("/api/health");
    $("#workspace-note").textContent = `工作区：${health.workspace}`;
  } catch (error) {
    $("#workspace-note").textContent = "无法连接本地分析引擎";
  }
}

async function analyze(path = null) {
  const button = $("#analyze");
  button.disabled = true;
  button.textContent = "分析中";
  try {
    const analysis = path === null
      ? await request("/api/demo")
      : await request("/api/analyze", { method: "POST", body: JSON.stringify({ path }) });
    state.analysis = analysis;
    state.selectedNode = null;
    state.queryFocus.clear();
    renderAnalysis();
    toast(`完成：${analysis.summary.function_count} 个函数，${analysis.summary.edge_count} 条关系`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "分析";
  }
}

function renderAnalysis() {
  const analysis = state.analysis;
  const summary = analysis.summary;
  const values = [summary.file_count, summary.function_count, summary.edge_count, summary.macro_count];
  $$("#summary-strip strong").forEach((element, index) => { element.textContent = values[index]; });
  $("#target-label").textContent = analysis.target;
  $("#module-list").innerHTML = analysis.modules.map((module) => `
    <div class="module-item">
      <strong>${escapeHtml(module.name)}</strong>
      <span>${module.files} 文件 · ${module.functions} 函数</span>
    </div>
  `).join("") || '<p class="empty">未发现模块</p>';
  $("#graph-empty").hidden = analysis.functions.length > 0;
  renderGraph.hasFit = false;
  renderGraph();
  renderOverviewAnswer();
}

function filteredEdges() {
  if (!state.analysis) return [];
  return state.analysis.edges.filter((edge) => state.activeEdges.has(edge.type));
}

function graphLayout(functions, edges) {
  const byId = new Map(functions.map((fn) => [fn.id, fn]));
  const incoming = new Map(functions.map((fn) => [fn.id, 0]));
  const adjacent = new Map(functions.map((fn) => [fn.id, []]));
  edges.forEach((edge) => {
    if (byId.has(edge.source) && byId.has(edge.target)) {
      adjacent.get(edge.source).push(edge.target);
      incoming.set(edge.target, incoming.get(edge.target) + 1);
    }
  });

  const roots = state.analysis.entry_points.filter((id) => byId.has(id));
  functions.forEach((fn) => { if (!incoming.get(fn.id) && !roots.includes(fn.id)) roots.push(fn.id); });
  const level = new Map();
  const queue = roots.map((id) => [id, 0]);
  while (queue.length) {
    const [id, depth] = queue.shift();
    if (level.has(id) && level.get(id) >= depth) continue;
    level.set(id, depth);
    adjacent.get(id).forEach((target) => {
      if (depth < functions.length) queue.push([target, depth + 1]);
    });
  }
  functions.forEach((fn) => { if (!level.has(fn.id)) level.set(fn.id, 0); });

  const columns = new Map();
  functions.forEach((fn) => {
    const depth = Math.min(level.get(fn.id), 6);
    if (!columns.has(depth)) columns.set(depth, []);
    columns.get(depth).push(fn);
  });

  const positions = new Map();
  const sortedLevels = [...columns.keys()].sort((a, b) => a - b);
  sortedLevels.forEach((depth) => {
    const column = columns.get(depth);
    column.sort((a, b) => a.name.localeCompare(b.name));
    column.forEach((fn, index) => {
      positions.set(fn.id, { x: 42 + depth * 220, y: 42 + index * 88 });
    });
  });
  return positions;
}

function svgElement(name, attributes = {}) {
  const element = document.createElementNS(svgNS, name);
  Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
  return element;
}

function renderGraph() {
  const root = $("#graph-root");
  root.innerHTML = "";
  if (!state.analysis || !state.analysis.functions.length) return;
  const functions = state.analysis.functions;
  const edges = filteredEdges();
  const byId = new Map(functions.map((fn) => [fn.id, fn]));
  const positions = graphLayout(functions, edges);
  const search = $("#function-search").value.trim().toLowerCase();

  const defs = svgElement("defs");
  ["direct", "async", "callback", "function_pointer"].forEach((type) => {
    const marker = svgElement("marker", { id: `arrow-${type}`, viewBox: "0 0 10 10", refX: "8", refY: "5", markerWidth: "5", markerHeight: "5", orient: "auto-start-reverse" });
    marker.appendChild(svgElement("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: type === "async" ? "#b36b00" : type === "callback" ? "#326ca8" : type === "function_pointer" ? "#7654a8" : "#9aa7a0" }));
    defs.appendChild(marker);
  });
  root.appendChild(defs);

  edges.forEach((edge) => {
    const source = positions.get(edge.source);
    const target = positions.get(edge.target);
    if (!source || !target) return;
    const startX = source.x + 166;
    const startY = source.y + 27;
    const endX = target.x;
    const endY = target.y + 27;
    const curve = Math.max(46, Math.abs(endX - startX) * .42);
    const path = svgElement("path", {
      d: `M ${startX} ${startY} C ${startX + curve} ${startY}, ${endX - curve} ${endY}, ${endX} ${endY}`,
      class: `graph-edge ${edge.type}`,
      "marker-end": `url(#arrow-${edge.type})`,
    });
    const title = svgElement("title");
    title.textContent = `${edge.type} · ${Math.round(edge.confidence * 100)}% · ${edge.file}:${edge.line}`;
    path.appendChild(title);
    root.appendChild(path);
  });

  functions.forEach((fn) => {
    const position = positions.get(fn.id);
    const classes = ["graph-node"];
    if (state.analysis.entry_points.includes(fn.id)) classes.push("entry");
    if (state.selectedNode === fn.id || state.queryFocus.has(fn.id)) classes.push("focus");
    if (search && !`${fn.name} ${fn.qualified_name} ${fn.file}`.toLowerCase().includes(search)) classes.push("dimmed");
    const group = svgElement("g", { class: classes.join(" "), transform: `translate(${position.x}, ${position.y})`, "data-node-id": fn.id });
    group.appendChild(svgElement("rect", { width: "166", height: "54" }));
    if (state.analysis.entry_points.includes(fn.id)) group.appendChild(svgElement("circle", { cx: "153", cy: "13", r: "4", class: "entry-mark" }));
    const name = svgElement("text", { x: "12", y: "22", class: "node-name" });
    name.textContent = fn.name.length > 21 ? `${fn.name.slice(0, 20)}…` : fn.name;
    group.appendChild(name);
    const meta = svgElement("text", { x: "12", y: "40", class: "node-meta" });
    meta.textContent = `${fn.file.split("/").pop()}:${fn.line} · ${fn.kind}`;
    group.appendChild(meta);
    group.addEventListener("click", () => selectNode(fn.id));
    root.appendChild(group);
  });

  applyTransform();
  if (!renderGraph.hasFit) {
    fitGraph();
    renderGraph.hasFit = true;
  }
}

function selectNode(id) {
  state.selectedNode = id;
  const fn = state.analysis.functions.find((item) => item.id === id);
  const related = state.analysis.edges.filter((edge) => edge.source === id || edge.target === id);
  const detail = $("#node-detail");
  detail.hidden = false;
  detail.innerHTML = `
    <strong>${escapeHtml(fn.qualified_name)}</strong>
    <code>${escapeHtml(fn.signature)}</code>
    <code>${escapeHtml(fn.file)}:${fn.line}-${fn.end_line} · ${related.length} 条关联关系</code>
  `;
  renderGraph();
}

function graphBounds() {
  const root = $("#graph-root");
  try { return root.getBBox(); } catch (_) { return { x: 0, y: 0, width: 600, height: 400 }; }
}

function fitGraph() {
  const stage = $("#graph-stage");
  const bounds = graphBounds();
  if (!bounds.width || !bounds.height) return;
  const padding = 48;
  const scale = Math.min((stage.clientWidth - padding * 2) / bounds.width, (stage.clientHeight - padding * 2) / bounds.height, 1.25);
  state.transform = {
    scale: Math.max(.28, scale),
    x: (stage.clientWidth - bounds.width * scale) / 2 - bounds.x * scale,
    y: (stage.clientHeight - bounds.height * scale) / 2 - bounds.y * scale,
  };
  applyTransform();
}

function applyTransform() {
  const { x, y, scale } = state.transform;
  $("#graph-root").setAttribute("transform", `translate(${x} ${y}) scale(${scale})`);
}

function renderOverviewAnswer() {
  const s = state.analysis.summary;
  $("#answer-panel").innerHTML = `
    <p class="answer-copy">已分析 <strong>${s.file_count}</strong> 个文件，识别 <strong>${s.function_count}</strong> 个函数和 <strong>${s.edge_count}</strong> 条关系。\n\n选择上方问题，或直接输入函数名继续追踪。</p>
    <p class="evidence-title">当前关系构成</p>
    <div class="evidence-list">
      <div class="evidence-item"><strong>direct · ${s.direct_calls}</strong><code>编译期可直接解析的函数调用</code></div>
      <div class="evidence-item"><strong>async / callback · ${s.async_calls + s.callback_calls}</strong><code>根据调度函数与回调参数推断</code></div>
      <div class="evidence-item"><strong>function pointer · ${s.function_pointer_calls}</strong><code>由指针赋值和调用位置联合推断</code></div>
    </div>
  `;
}

function formatAnswer(text) {
  return escapeHtml(text).replace(/`([^`]+)`/g, "<code>$1</code>");
}

async function ask(question) {
  if (!state.analysis) {
    toast("请先分析代码", true);
    return;
  }
  const panel = $("#answer-panel");
  panel.innerHTML = '<p class="answer-copy">正在沿调用图检索证据...</p>';
  try {
    const reply = await request("/api/query", {
      method: "POST",
      body: JSON.stringify({ analysis_id: state.analysis.analysis_id, question }),
    });
    state.queryFocus = new Set(reply.focus || []);
    panel.innerHTML = `
      <p class="answer-copy">${formatAnswer(reply.answer)}</p>
      ${reply.citations.length ? `
        <p class="evidence-title">源码证据</p>
        <div class="evidence-list">${reply.citations.map((item) => `
          <div class="evidence-item">
            <strong>${escapeHtml(item.file)}:${item.line}</strong>
            <code>${escapeHtml(item.evidence)}</code>
          </div>
        `).join("")}</div>` : ""}
    `;
    renderGraph();
  } catch (error) {
    panel.innerHTML = `<p class="answer-copy">${escapeHtml(error.message)}</p>`;
    toast(error.message, true);
  }
}

$("#analyze").addEventListener("click", () => analyze($("#repo-path").value.trim()));
$("#repo-path").addEventListener("keydown", (event) => { if (event.key === "Enter") analyze(event.target.value.trim()); });
$("#load-demo").addEventListener("click", () => analyze(null));
$("#fit-graph").addEventListener("click", fitGraph);
$("#function-search").addEventListener("input", renderGraph);
$$('[data-edge]').forEach((button) => button.addEventListener("click", () => {
  const type = button.dataset.edge;
  if (state.activeEdges.has(type)) state.activeEdges.delete(type); else state.activeEdges.add(type);
  button.classList.toggle("active", state.activeEdges.has(type));
  renderGraph();
  fitGraph();
}));
$$('[data-question]').forEach((button) => button.addEventListener("click", () => {
  $("#question").value = button.dataset.question;
  ask(button.dataset.question);
}));
$("#query-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const question = $("#question").value.trim();
  if (question) ask(question);
});

const graph = $("#call-graph");
graph.addEventListener("wheel", (event) => {
  event.preventDefault();
  const rect = graph.getBoundingClientRect();
  const px = event.clientX - rect.left;
  const py = event.clientY - rect.top;
  const oldScale = state.transform.scale;
  const nextScale = Math.min(2.4, Math.max(.22, oldScale * (event.deltaY < 0 ? 1.1 : .9)));
  state.transform.x = px - (px - state.transform.x) * nextScale / oldScale;
  state.transform.y = py - (py - state.transform.y) * nextScale / oldScale;
  state.transform.scale = nextScale;
  applyTransform();
}, { passive: false });
graph.addEventListener("pointerdown", (event) => {
  state.drag = { x: event.clientX, y: event.clientY, tx: state.transform.x, ty: state.transform.y };
  graph.setPointerCapture(event.pointerId);
});
graph.addEventListener("pointermove", (event) => {
  if (!state.drag) return;
  state.transform.x = state.drag.tx + event.clientX - state.drag.x;
  state.transform.y = state.drag.ty + event.clientY - state.drag.y;
  applyTransform();
});
graph.addEventListener("pointerup", () => { state.drag = null; });
window.addEventListener("resize", () => { if (state.analysis) fitGraph(); });

loadHealth();
analyze(null);
