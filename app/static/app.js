/* MedLabel Review — local document review workspace. No remote assets or services. */
"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) =>
  Array.from(root.querySelectorAll(selector));
const main = $("#main");
const modal = $("#modal");
const state = {
  route: "",
  generation: 0,
  comparison: null,
  reviewStatus: "all",
  priority: "all",
  drafts: new Map(),
  drugs: [],
  comparisons: [],
  audit: [],
  modalVersion: null,
};
const glyphs = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  archive: '<path d="M4 8h16v12H4zM3 4h18v4H3zM9 12h6"/>',
  compare: '<path d="M3 7h16m-4-4 4 4-4 4M21 17H5m4-4-4 4 4 4"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  file: '<path d="M6 3h8l4 4v14H6zM14 3v5h4M9 12h6m-6 4h6"/>',
  upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6"/>',
  download: '<path d="M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6zM8 12l3 3 5-6"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  back: '<path d="M20 12H4m6-6-6 6 6 6"/>',
  warning: '<path d="M12 3 2 21h20zM12 9v5m0 3v1"/>',
  edit: '<path d="m14 5 5 5M4 20l1-6L16 3l5 5-11 11z"/>',
  filter: '<path d="M3 5h18l-7 8v7l-4-2v-5z"/>',
  spark:
    '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z"/>',
};
function icon(name) {
  return `<svg viewBox="0 0 24 24" aria-hidden="true">${glyphs[name] || glyphs.file}</svg>`;
}
function esc(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (character) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        character
      ],
  );
}
function id(value) {
  return Number.isInteger(Number(value)) && Number(value) > 0
    ? String(Number(value))
    : "0";
}
function fmtDate(value, includeTime = false) {
  if (!value) return "未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    ...(includeTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(date);
}
function statusTag(status) {
  const options = {
    pending: ["待复核", "amber"],
    confirmed: ["已确认", "teal"],
    ignored: ["已忽略", "gray"],
    completed: ["已完成", "teal"],
    complete: ["已完成", "teal"],
    reviewed: ["已完成", "teal"],
  };
  const [label, color] = options[status] || ["待复核", "amber"];
  return `<span class="tag ${color}"><span class="badge-pending">${label}</span></span>`;
}
function kindTag(kind) {
  return `<span class="tag ${kind === "added" ? "green" : kind === "removed" ? "red" : "gray"}">${{ added: "新增", removed: "删除", modified: "修改" }[kind] || esc(kind)}</span>`;
}
function pageHeading(eyebrow, title, subtitle, actions = "", extra = "") {
  return `<div class="page-heading ${extra}"><div><span class="eyebrow">${esc(eyebrow)}</span><h1>${esc(title)}</h1><p class="subtitle">${esc(subtitle)}</p></div>${actions ? `<div class="actions">${actions}</div>` : ""}</div>`;
}
function empty(title, description, action = "", compact = false) {
  return `<div class="empty-state ${compact ? "compact" : ""}">${icon("file")}<h3>${esc(title)}</h3><p>${esc(description)}</p>${action}</div>`;
}
function button(label, action, data = "", primary = false, glyph = "") {
  return `<button type="button" class="button${primary ? " primary" : ""}" data-action="${action}" ${data}>${glyph ? icon(glyph) : ""}${esc(label)}</button>`;
}
function warnings(items) {
  if (!items?.length) return "";
  return `<div class="notice">${icon("warning")}<p>${items.map((item) => esc(item)).join("<br>")}</p></div>`;
}
async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.method && options.method !== "GET")
    headers.set("X-Requested-With", "MedLabelReview");
  if (options.body && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
    options.body = JSON.stringify(options.body);
  }
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers,
      credentials: "same-origin",
    });
  } catch (_) {
    throw new Error("无法连接本地服务，请确认应用已启动后重试。");
  }
  let result;
  try {
    result = await response.json();
  } catch (_) {
    throw new Error("服务返回了无法读取的内容，请稍后重试。");
  }
  if (!response.ok) {
    let detail =
      result.detail || result.message || `请求失败（${response.status}）`;
    if (Array.isArray(detail))
      detail = detail
        .map(
          (item) =>
            `${(item.loc || []).filter((p) => p !== "body").join(" / ")}：${item.msg}`,
        )
        .join("；");
    const error = new Error(
      typeof detail === "string" ? detail : JSON.stringify(detail),
    );
    error.status = response.status;
    throw error;
  }
  return result;
}
function toast(message, error = false) {
  const element = document.createElement("div");
  element.className = `toast${error ? " error" : ""}`;
  element.innerHTML = `${icon(error ? "warning" : "check")}<span>${esc(message)}</span>`;
  $("#toasts").append(element);
  setTimeout(() => element.remove(), error ? 7500 : 4200);
}
function renderError(error) {
  main.innerHTML = `<div class="error-state"><h2>暂时无法加载</h2><p>${esc(error.message)}</p>${button("重新加载", "refresh", "", "", "arrow")}</div>`;
}
function modalError(message) {
  let container = $(".dialog-error", modal);
  if (!container) {
    container = document.createElement("div");
    container.className = "dialog-error";
    container.setAttribute("role", "alert");
    $("#modal-content").prepend(container);
  }
  container.textContent = message;
  container.scrollIntoView({ block: "nearest" });
}
function showModal(title, content, wide = false) {
  $("#modal-title").textContent = title;
  $("#modal-content").innerHTML = content;
  modal.classList.toggle("wide", wide);
  if (!modal.open) modal.showModal();
  // Set initial focus synchronously; a delayed callback can steal focus after
  // the user has already moved to another field and misdirect their input.
  const input = $('input:not([type="hidden"]), textarea, select', modal);
  (input || $("#modal-close")).focus();
}
function closeModal() {
  modal.close();
  state.modalVersion = null;
}
$("#modal-close").addEventListener("click", closeModal);
modal.addEventListener("click", (event) => {
  if (event.target === modal) {
    const bounds = modal.getBoundingClientRect();
    if (
      event.clientX < bounds.left ||
      event.clientX > bounds.right ||
      event.clientY < bounds.top ||
      event.clientY > bounds.bottom
    )
      closeModal();
  }
});
$$("[data-icon]").forEach((element) => {
  element.innerHTML = icon(element.dataset.icon);
});

async function navigate() {
  const route = location.hash.slice(1) || "dashboard";
  const generation = ++state.generation;
  const previous = state.route;
  state.route = route;
  if (previous !== route) {
    state.reviewStatus = "all";
    state.priority = "all";
  }
  const parts = route.split("/");
  const section = ["dashboard", "drugs", "comparisons", "audit"].includes(
    parts[0],
  )
    ? parts[0]
    : "dashboard";
  $$("[data-nav]").forEach((element) => {
    const selected = element.dataset.nav === section;
    element.classList.toggle("active", selected);
    if (selected) element.setAttribute("aria-current", "page");
    else element.removeAttribute("aria-current");
  });
  $("#breadcrumb-current").textContent = {
    dashboard: "工作台",
    drugs: parts[1] ? "药品档案" : "药品与版本",
    comparisons: parts[1] ? "变更审核" : "比对与审核",
    audit: "操作记录",
  }[section];
  main.innerHTML =
    '<div class="loading-state"><span class="spinner"></span>正在加载…</div>';
  try {
    let html;
    if (section === "drugs" && parts[1])
      html = await drugPage(Number(parts[1]));
    else if (section === "drugs") html = await drugsPage();
    else if (section === "comparisons" && parts[1])
      html = await comparisonPage(Number(parts[1]));
    else if (section === "comparisons") html = await comparisonsPage();
    else if (section === "audit") html = await auditPage();
    else html = await dashboardPage();
    if (generation !== state.generation) return;
    main.innerHTML = html;
    document.title = `${$("#breadcrumb-current").textContent} · MedLabel Review`;
    if (previous !== route) window.scrollTo(0, 0);
  } catch (error) {
    if (generation === state.generation) renderError(error);
  }
}
window.addEventListener("hashchange", navigate);

function auditLabel(action) {
  const mapping = {
    "drug.create": "建立药品档案",
    "version.import": "导入说明书版本",
    "version.create": "导入说明书版本",
    "version.correct": "校正说明书章节",
    "version.sections.update": "校正说明书章节",
    "comparison.create": "创建版本比对",
    "change.review": "更新审核结论",
    "demo.seed": "载入模拟示例",
    create_drug: "建立药品档案",
    import_version: "导入说明书版本",
    correct_sections: "校正说明书章节",
    create_comparison: "创建版本比对",
    review_change: "更新审核结论",
  };
  return mapping[action] || action;
}
function detailText(detail) {
  let value = detail;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch (_) {
      return value;
    }
  }
  if (!value || typeof value !== "object") return String(value ?? "");
  if (Array.isArray(value.before) && Array.isArray(value.after)) {
    return `章节校正：${value.before.length} 个 → ${value.after.length} 个章节`;
  }
  if (value.before?.status && value.after?.status) {
    const labels = {
      pending: "待复核",
      confirmed: "已确认",
      ignored: "已忽略",
    };
    return `${labels[value.before.status] || value.before.status} → ${labels[value.after.status] || value.after.status}${value.after.note ? `；复核意见：${value.after.note}` : ""}`;
  }
  const labels = {
    name: "药品",
    label: "版本",
    filename: "文件",
    drug_id: "药品档案",
    old_version_id: "旧版编号",
    new_version_id: "新版编号",
    changes: "变更数",
    comparison_id: "比对编号",
    note: "复核意见",
    status: "状态",
  };
  return Object.entries(value)
    .filter(([key]) => key !== "sha256")
    .map(
      ([key, item]) =>
        `${labels[key] || key}：${typeof item === "object" ? JSON.stringify(item) : item}`,
    )
    .join(" · ");
}
function activityList(items, limit = 5) {
  if (!items?.length)
    return empty(
      "尚无操作记录",
      "导入说明书后，操作记录会显示在这里。",
      "",
      true,
    );
  return `<ul class="activity">${items
    .slice(0, limit)
    .map(
      (item) =>
        `<li><span class="activity-dot"></span><div class="activity-content"><strong>${esc(auditLabel(item.action))}</strong><p>${esc(detailText(item.detail))}</p></div><time>${esc(fmtDate(item.created_at))}</time></li>`,
    )
    .join("")}</ul>`;
}
async function dashboardPage() {
  const [data, comparisons] = await Promise.all([
    api("/api/dashboard"),
    api("/api/comparisons"),
  ]);
  const count = Number(data.pending_changes || 0);
  $("#nav-pending").textContent = count;
  $("#nav-pending").hidden = !count;
  const stats = [
    ["药品档案", data.drugs, "已建立的药品资料", "archive"],
    ["说明书版本", data.versions, "保留来源与原始文件", "file"],
    ["比对任务", data.comparisons, "可追溯的版本变更", "compare"],
    ["待复核变更", count, "等待人工确认与处理", "shield"],
  ];
  return (
    pageHeading(
      "YOUR REVIEW WORKSPACE",
      "每一次变更，都清晰可见",
      "管理说明书版本，让变更识别、复核与归档有序进行。",
      button("新建药品", "new-drug", "", true, "plus"),
    ) +
    `<div class="stats">${stats.map(([label, value, hint, glyph], index) => `<div class="stat-card ${index === 3 ? "accent" : ""}"><div class="stat-label">${label}${icon(glyph)}</div><div class="stat-number">${esc(value ?? 0)}</div><div class="stat-hint">${hint}</div></div>`).join("")}</div>` +
    `<div class="dashboard-grid"><div><section class="panel"><header class="panel-header"><div><h2>最近的比对任务</h2><p>从差异发现到审核完成</p></div><a class="text-button" href="#comparisons">查看全部 →</a></header>${
      comparisons.length
        ? comparisons
            .slice(0, 5)
            .map(
              (item) =>
                `<a class="list-row row-link" href="#comparisons/${id(item.id)}"><span class="list-icon">${icon("compare")}</span><div class="list-details"><strong>${esc(item.drug_name || "说明书版本比对")}</strong><p>${esc(item.old_label)} → ${esc(item.new_label)} · ${esc(item.total_changes ?? 0)} 处变更</p></div>${statusTag(Number(item.pending_changes) ? "pending" : "completed")}<span class="list-arrow">›</span></a>`,
            )
            .join("")
        : empty(
            "开启第一份说明书审核",
            "建立药品档案，导入两个版本，即可生成章节差异。",
            button("体验模拟示例", "demo", "", "", "spark"),
          )
    }</section><section class="panel"><header class="panel-header"><h2>最近操作</h2><a href="#audit" class="text-button">完整记录 →</a></header><div class="panel-body">${activityList(data.recent_activity)}</div></section></div><aside class="dashboard-aside"><div class="welcome-panel"><span class="eyebrow">A CLEARER REVIEW PROCESS</span><h2>关注重要变化，<br>保留判断依据。</h2><p>对齐说明书章节，定位数字、单位和重点条款变化，逐条记录人工复核意见。</p><a class="button" href="#drugs">进入药品资料库 ${icon("arrow")}</a></div><section class="panel"><header class="panel-header"><h2>四步完成审核</h2></header><div class="panel-body"><ol class="workflow"><li><span class="step-number">01</span><div><strong>建立档案与导入版本</strong><p>支持纯文字 PDF、DOCX、TXT 和粘贴文本</p></div></li><li><span class="step-number">02</span><div><strong>检查章节，生成比对</strong><p>必要时先校正识别结果，再固定比对依据</p></div></li><li><span class="step-number">03</span><div><strong>逐条人工复核</strong><p>确认或忽略变更，保存审核意见</p></div></li><li><span class="step-number">04</span><div><strong>导出报告，留存记录</strong><p>HTML、CSV、JSON 三种格式</p></div></li></ol></div></section></aside></div>`
  );
}
async function drugsPage() {
  state.drugs = await api("/api/drugs");
  return (
    pageHeading(
      "DOCUMENT LIBRARY",
      "药品与版本",
      "以药品为单位归集说明书，保留每一版的来源与原文。",
      button("新建药品", "new-drug", "", true, "plus"),
    ) +
    `<section class="panel"><div class="toolbar"><div class="search-wrap">${icon("search")}<input type="search" id="drug-search" aria-label="搜索药品名称、厂家或批准文号" placeholder="搜索药品、厂家或批准文号"></div><span class="count-label" id="drug-count">${state.drugs.length} 个药品档案</span></div><div id="drug-list">${drugTable(state.drugs)}</div></section>`
  );
}
function drugTable(items) {
  if (!items.length)
    return empty(
      "没有找到药品档案",
      "新建档案后，可以导入说明书并开始版本比对。",
      button("新建药品", "new-drug", "", true, "plus"),
    );
  return `<div class="table-scroll"><table><thead><tr><th>药品名称 / 规格</th><th>生产厂家</th><th>批准文号</th><th>版本</th><th>建立日期</th><th></th></tr></thead><tbody>${items.map((item) => `<tr><td><a class="cell-title" href="#drugs/${id(item.id)}">${esc(item.name)}</a><span class="cell-sub">${esc(item.specification || "未填写规格")}</span></td><td>${esc(item.manufacturer || "—")}</td><td class="mono">${esc(item.approval_number || "—")}</td><td><span class="tag teal">${esc(item.version_count ?? 0)} 版</span></td><td class="nowrap muted">${esc(fmtDate(item.created_at))}</td><td class="text-right"><a class="button small" href="#drugs/${id(item.id)}">查看档案 →</a></td></tr>`).join("")}</tbody></table></div>`;
}
async function drugPage(drugId) {
  const [drug, versions] = await Promise.all([
    api(`/api/drugs/${id(drugId)}`),
    api(`/api/drugs/${id(drugId)}/versions`),
  ]);
  const fields = [
    ["生产厂家", drug.manufacturer],
    ["药品规格", drug.specification],
    ["批准文号", drug.approval_number],
    ["建立日期", fmtDate(drug.created_at)],
  ];
  return (
    `<a class="back-link" href="#drugs">${icon("back")} 返回药品资料库</a>` +
    pageHeading(
      "DRUG DOSSIER",
      drug.name,
      "保存来源，核对章节，再开始版本审核。",
      button(
        "导入版本",
        "import-version",
        `data-id="${id(drugId)}"`,
        true,
        "upload",
      ) +
        button(
          "创建比对",
          "new-comparison",
          `data-id="${id(drugId)}" ${versions.length < 2 ? 'disabled title="请先导入至少两个版本"' : ""}`,
          "",
          "compare",
        ),
    ) +
    `<section class="panel"><div class="panel-body"><div class="metadata-grid">${fields.map(([label, value]) => `<div><span class="metadata-label">${label}</span><span class="metadata-value">${esc(value || "未填写")}</span></div>`).join("")}</div>${drug.notes ? `<div class="metadata-note">${esc(drug.notes)}</div>` : ""}</div></section><div class="page-heading"><div><h2>说明书版本</h2><p class="subtitle">共 ${versions.length} 个版本 · 导入后请检查章节识别结果</p></div></div>${versions.length ? `<div class="version-card-grid">${versions.map((version) => `<article class="version-card"><div class="version-card-head"><div><h3>${esc(version.label)}</h3><span class="cell-sub">版本日期 ${esc(fmtDate(version.version_date))}</span></div><span class="list-icon">${icon("file")}</span></div><p>文件：${esc(version.filename || "粘贴文本")}</p><p>来源：${esc(version.source || "未填写")}</p><p>识别章节：${esc(version.section_count ?? 0)} 个</p><p class="hash" title="原始内容 SHA-256">SHA-256 ${esc(String(version.sha256 || "").slice(0, 20))}…</p><div class="actions">${button("查看与校正", "view-version", `data-id="${id(version.id)}"`, "", "edit")}<a class="button small" href="/api/versions/${id(version.id)}/file">原始文件 ${icon("download")}</a></div></article>`).join("")}</div>` : `<section class="panel">${empty("还没有说明书版本", "先导入旧版和新版说明书，随后可查看章节并创建比对。", button("导入第一个版本", "import-version", `data-id="${id(drugId)}"`, true, "upload"))}</section>`}`
  );
}
async function comparisonsPage() {
  state.comparisons = await api("/api/comparisons");
  return (
    pageHeading(
      "REVIEW QUEUE",
      "比对与审核",
      "逐项核查版本差异，保留复核结论与变更依据。",
      `<a class="button primary" href="#drugs">${icon("plus")} 从药品档案创建比对</a>`,
    ) +
    `<section class="panel"><div class="toolbar"><div class="search-wrap">${icon("search")}<input type="search" id="comparison-search" aria-label="搜索比对任务" placeholder="搜索药品名称或版本"></div><select class="filter-select" id="comparison-status" aria-label="任务状态"><option value="all">全部任务</option><option value="pending">有待复核变更</option><option value="completed">已完成复核</option></select><span class="count-label" id="comparison-count">${state.comparisons.length} 个比对任务</span></div><div id="comparison-list">${comparisonTable(state.comparisons)}</div></section>`
  );
}
function comparisonTable(items) {
  if (!items.length)
    return empty(
      "暂无匹配的比对任务",
      "在药品档案中选择两个版本，创建一份新的比对任务。",
      '<a href="#drugs" class="button primary">前往药品档案 →</a>',
    );
  return `<div class="table-scroll"><table><thead><tr><th>药品 / 版本</th><th>变更数量</th><th>待复核</th><th>任务状态</th><th>创建时间</th><th></th></tr></thead><tbody>${items.map((item) => `<tr><td><a href="#comparisons/${id(item.id)}" class="cell-title">${esc(item.drug_name)}</a><span class="cell-sub">${esc(item.old_label)} → ${esc(item.new_label)}</span></td><td>${esc(item.total_changes ?? 0)} 项</td><td>${esc(item.pending_changes ?? 0)} 项</td><td>${statusTag(Number(item.pending_changes) ? "pending" : "completed")}</td><td class="nowrap muted">${esc(fmtDate(item.created_at))}</td><td><a class="button small" href="#comparisons/${id(item.id)}">打开审核 →</a></td></tr>`).join("")}</tbody></table></div>`;
}
async function comparisonPage(comparisonId) {
  const result = await api(`/api/comparisons/${id(comparisonId)}`);
  if (state.route !== `comparisons/${id(comparisonId)}`) return "";
  state.comparison = result;
  return comparisonMarkup();
}
function comparisonMarkup() {
  const data = state.comparison;
  const changes = data.changes || [];
  const pending = changes.filter(
    (change) => change.status === "pending",
  ).length;
  const oldVersion = data.old_version || {};
  const newVersion = data.new_version || {};
  const progress = changes.length
    ? Math.round(((changes.length - pending) / changes.length) * 100)
    : 100;
  return (
    `<a class="back-link" href="#comparisons">${icon("back")} 返回审核任务</a>` +
    pageHeading(
      "VERSION COMPARISON",
      data.drug_name || "说明书变更审核",
      `${changes.length} 处章节变更 · ${data.unchanged_sections || 0} 个章节内容一致 · 逐条核对后记录结论`,
      `<div class="report-links"><a class="button" href="/api/comparisons/${id(data.id)}/export?format=html">${icon("download")} HTML 报告</a><a class="button" href="/api/comparisons/${id(data.id)}/export?format=csv">CSV</a><a class="button" href="/api/comparisons/${id(data.id)}/export?format=json">JSON</a></div>`,
      "comparison-heading",
    ) +
    warnings(data.warnings) +
    `<div class="comparison-summary"><div class="version-pair"><div class="version-pill"><span class="eyebrow">旧版说明书</span><strong>${esc(oldVersion.label || data.old_label)}</strong><small>${esc(fmtDate(oldVersion.version_date))}</small></div>${icon("arrow")}<div class="version-pill"><span class="eyebrow">新版说明书</span><strong>${esc(newVersion.label || data.new_label)}</strong><small>${esc(fmtDate(newVersion.version_date))}</small></div></div><div class="review-progress"><div class="review-progress-label"><span>审核完成度</span><strong>${changes.length - pending} / ${changes.length}</strong></div><div class="progress-track" role="progressbar" aria-label="审核完成度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}"><div class="progress-fill" style="width:${progress}%"></div></div></div></div><div class="filter-bar"><div class="segmented" role="group" aria-label="按审核状态筛选">${[
      ["all", "全部变更"],
      ["pending", "待复核"],
      ["confirmed", "已确认"],
      ["ignored", "已忽略"],
    ]
      .map(
        ([value, label]) =>
          `<button type="button" data-action="filter-review" data-value="${value}" aria-pressed="${state.reviewStatus === value}" class="${state.reviewStatus === value ? "active" : ""}">${label}${value === "pending" ? ` ${pending}` : ""}</button>`,
      )
      .join(
        "",
      )}</div><select id="priority-filter" class="filter-select" aria-label="按复核优先级筛选"><option value="all" ${state.priority === "all" ? "selected" : ""}>全部优先级</option><option value="high" ${state.priority === "high" ? "selected" : ""}>重点复核</option><option value="normal" ${state.priority === "normal" ? "selected" : ""}>常规复核</option></select><span class="small-hint">红色为删除 · 绿色为新增</span></div><div id="changes-list">${changesMarkup()}</div><p class="comparison-footer">重点标记用于安排复核顺序，不代表医学风险等级。HTML 报告可通过浏览器打印为 PDF。</p>`
  );
}
function markedText(text) {
  // Match untrusted plain text first; escaping before matching could split an HTML entity.
  const value = String(text ?? "");
  const pattern =
    /\d+(?:\.\d+)?(?:\s*(?:mg|μg|ug|kg|g|ml|mL|L|%|毫克|微克|克|毫升|片|次|日|天))?/g;
  let result = "";
  let cursor = 0;
  for (const match of value.matchAll(pattern)) {
    result += esc(value.slice(cursor, match.index));
    result += `<span class="numeric">${esc(match[0])}</span>`;
    cursor = match.index + match[0].length;
  }
  return result + esc(value.slice(cursor));
}
function diffText(change, side) {
  const text = side === "old" ? change.old_text : change.new_text;
  if (!text) return '<span class="absent-text">该版本无此章节内容</span>';
  const segments = change.segments;
  if (!Array.isArray(segments) || !segments.length) return esc(text);
  const relevant = segments.filter(
    (segment) =>
      segment.op === "equal" ||
      segment.op === (side === "old" ? "delete" : "insert"),
  );
  // Only apply inline segments if they reconstruct this exact source; never hide source text.
  if (relevant.map((segment) => segment.text || "").join("") !== text)
    return esc(text);
  return relevant
    .map((segment) =>
      segment.op === "equal"
        ? esc(segment.text)
        : `<${side === "old" ? "del" : "ins"}>${markedText(segment.text)}</${side === "old" ? "del" : "ins"}>`,
    )
    .join("");
}
function changesMarkup() {
  const changes = state.comparison.changes || [];
  const filtered = changes.filter(
    (change) =>
      (state.reviewStatus === "all" || change.status === state.reviewStatus) &&
      (state.priority === "all" || change.priority === state.priority),
  );
  if (!filtered.length)
    return `<section class="panel">${empty(changes.length ? "当前筛选下没有变更" : "两个版本未发现章节内容差异", changes.length ? "切换状态或优先级，查看其他变更。" : "已保留本次比对记录，可导出报告。", "", true)}</section>`;
  return filtered
    .map((change) => {
      const index = changes.findIndex((item) => item.id === change.id) + 1;
      const note = state.drafts.has(change.id)
        ? state.drafts.get(change.id)
        : change.note || "";
      return `<article class="change-card" id="change-${id(change.id)}" data-priority="${change.priority === "high" ? "high" : "normal"}"><header class="change-card-head"><div class="change-title"><span class="change-index">${String(index).padStart(2, "0")}</span><h3>${esc(change.section_title)}</h3>${kindTag(change.kind)}${change.priority === "high" ? '<span class="tag amber">重点复核</span>' : ""}</div>${statusTag(change.status)}</header>${change.flags?.length ? `<div class="change-flags">${change.flags.map((flag) => `<span>${esc(flag)}</span>`).join("")}</div>` : ""}<div class="diff-grid"><div class="diff-column"><div class="diff-label"><span>旧版内容</span><span>${esc(change.old_location || "—")}</span></div><div class="diff-text">${diffText(change, "old")}</div></div><div class="diff-column"><div class="diff-label"><span>新版内容</span><span>${esc(change.new_location || "—")}</span></div><div class="diff-text">${diffText(change, "new")}</div></div></div><div class="review-area">${change.reviewed_at ? `<div class="review-history"><strong>${esc(change.reviewer || "本地审核员")}</strong> · ${esc(fmtDate(change.reviewed_at, true))} · 已保存上次审核记录</div>` : ""}<div class="review-controls"><div class="review-note"><label for="note-${id(change.id)}">复核意见 <span class="muted">（忽略变更时必填）</span></label><textarea id="note-${id(change.id)}" data-note-id="${id(change.id)}" maxlength="3000" rows="1" placeholder="记录变更依据或处理意见…">${esc(note)}</textarea></div><div class="actions">${change.status !== "pending" ? button("重置待复核", "review", `data-id="${id(change.id)}" data-status="pending"`) : ""}${button("忽略", "review", `data-id="${id(change.id)}" data-status="ignored"`)}${button(change.status === "confirmed" ? "保存确认意见" : "确认变更", "review", `data-id="${id(change.id)}" data-status="confirmed"`, true, "check")}</div></div></div></article>`;
    })
    .join("");
}
async function auditPage() {
  state.audit = await api("/api/audit");
  return (
    pageHeading(
      "ACTIVITY TRAIL",
      "操作记录",
      "记录导入、章节校正、版本比对与每次审核结论的变化。",
      button("刷新记录", "refresh", "", "", "clock"),
    ) +
    `<section class="panel"><div class="toolbar"><div class="search-wrap">${icon("search")}<input id="audit-search" type="search" aria-label="搜索操作记录" placeholder="搜索操作、对象或记录内容"></div><span class="count-label" id="audit-count">${state.audit.length} 条记录</span></div><div id="audit-list">${auditTable(state.audit)}</div></section>`
  );
}
function auditTable(items) {
  if (!items.length)
    return empty(
      "暂无匹配的操作记录",
      "建立药品、导入版本和审核变更后，将在此保留操作过程。",
      "",
      true,
    );
  return `<div class="table-scroll"><table><thead><tr><th>时间</th><th>操作</th><th>操作对象</th><th>记录内容</th><th>记录人</th></tr></thead><tbody>${items.map((item) => `<tr><td class="nowrap muted">${esc(fmtDate(item.created_at, true))}</td><td class="nowrap">${esc(auditLabel(item.action))}</td><td><span class="tag gray">${esc({ drug: "药品", version: "版本", comparison: "比对", change: "变更" }[item.entity_type] || item.entity_type)} #${esc(item.entity_id)}</span></td><td class="audit-detail">${esc(detailText(item.detail))}</td><td class="nowrap muted">${esc(item.actor)}</td></tr>`).join("")}</tbody></table></div>`;
}

function newDrugModal() {
  showModal(
    "建立药品档案",
    `<form id="drug-form"><div class="help-box">同一药品的不同说明书版本应放在同一档案中，便于准确比对。</div><div class="form-grid"><div class="field span-2"><label for="drug-name">药品名称 <span class="required">*</span></label><input id="drug-name" name="name" maxlength="200" required placeholder="例如：某药品片剂"></div><div class="field"><label for="drug-manufacturer">生产厂家</label><input id="drug-manufacturer" name="manufacturer" maxlength="200" placeholder="填写说明书标示厂家"></div><div class="field"><label for="drug-specification">药品规格</label><input id="drug-specification" name="specification" maxlength="200" placeholder="按说明书填写"></div><div class="field span-2"><label for="drug-approval">批准文号</label><input id="drug-approval" name="approval_number" maxlength="100" placeholder="可选"></div><div class="field span-2"><label for="drug-notes">备注</label><textarea id="drug-notes" name="notes" maxlength="3000" placeholder="例如：资料用途、来源说明"></textarea></div></div><div class="form-actions">${button("取消", "close-modal")}<button class="button primary" type="submit">${icon("plus")} 建立档案</button></div></form>`,
  );
}
function importModal(drugId) {
  showModal(
    "导入说明书版本",
    `<form id="import-form" data-drug-id="${id(drugId)}"><div class="form-grid"><div class="field"><label for="version-label">版本名称 <span class="required">*</span></label><input id="version-label" name="label" required maxlength="120" placeholder="例如：2026 年修订版"></div><div class="field"><label for="version-date">版本日期 <span class="required">*</span></label><input id="version-date" name="version_date" type="date" required></div><div class="field span-2"><label for="version-source">资料来源 <span class="required">*</span></label><input id="version-source" name="source" required maxlength="1000" placeholder="文件出处、公告链接或资料提供方"><small>来源信息会随比对报告保留，请填写可追溯的出处。</small></div></div><div class="segmented import-tabs" role="group" aria-label="导入方式"><button type="button" class="active" data-action="import-tab" data-value="file" aria-pressed="true">${icon("upload")} 上传文件</button><button type="button" data-action="import-tab" data-value="text" aria-pressed="false">${icon("edit")} 粘贴文本</button></div><input type="hidden" id="import-mode" value="file"><div id="import-file-pane" class="tab-pane"><div class="upload-area">${icon("upload")}<label for="version-file">选择说明书文件</label><input id="version-file" name="file" type="file" required accept=".pdf,.docx,.txt"><small>纯文字 PDF（不含图片）/ DOCX / TXT · 最大 10 MB</small></div><p class="field-hint" style="margin-top:12px">含图片的 PDF（包括扫描件或带图片页眉的文档）请先转换为经核对的 DOCX 或 TXT。导入后可手动校正章节识别结果。</p></div><div id="import-text-pane" class="tab-pane" hidden><div class="field"><label for="version-text">说明书原文 <span class="required">*</span></label><textarea id="version-text" name="text" disabled rows="10" placeholder="请保留章节标题，如：&#10;【药品名称】&#10;…&#10;【用法用量】&#10;…"></textarea><small>请使用有权使用的资料；模拟内容应明确标注。</small></div></div><div class="form-actions">${button("取消", "close-modal")}<button class="button primary" type="submit">${icon("upload")} 导入并识别章节</button></div></form>`,
  );
}
async function newComparisonModal(drugId) {
  const versions = await api(`/api/drugs/${id(drugId)}/versions`);
  if (versions.length < 2) {
    toast("请先为该药品导入至少两个版本。", true);
    return;
  }
  const sorted = [...versions].sort(
    (a, b) =>
      String(a.version_date).localeCompare(String(b.version_date)) ||
      a.id - b.id,
  );
  const options = (selected) =>
    sorted
      .map(
        (version) =>
          `<option value="${id(version.id)}" ${version.id === selected ? "selected" : ""}>${esc(version.label)} · ${esc(version.version_date)}</option>`,
      )
      .join("");
  showModal(
    "创建版本比对",
    `<form id="comparison-form"><div class="help-box">请选择同一药品的旧版与新版。创建比对后，所用版本的章节内容将固定，后续修订请另行导入新版本。</div><div class="field"><label for="old-version">旧版说明书</label><select id="old-version" name="old_version_id" required>${options(sorted[sorted.length - 2].id)}</select></div><div class="field"><label for="new-version">新版说明书</label><select id="new-version" name="new_version_id" required>${options(sorted[sorted.length - 1].id)}</select></div><p class="field-hint">建议先查看章节识别结果，确认文档提取完整。新版日期不能早于旧版。</p><div class="form-actions">${button("取消", "close-modal")}<button type="submit" class="button primary">${icon("compare")} 生成章节比对</button></div></form>`,
  );
}
function sectionEditor(section, index) {
  return `<div class="section-editor"><div class="section-editor-head"><span class="section-number">${String(index + 1).padStart(2, "0")}</span><input name="section_title" aria-label="第 ${index + 1} 个章节标题" required maxlength="200" value="${esc(section.title)}"><button class="icon-button" type="button" data-action="remove-section" aria-label="移除第 ${index + 1} 个章节">×</button></div><textarea name="section_text" rows="4" aria-label="第 ${index + 1} 个章节正文">${esc(section.text)}</textarea></div>`;
}
async function viewVersionModal(versionId) {
  const version = await api(`/api/versions/${id(versionId)}`);
  state.modalVersion = version;
  showModal(
    version.label,
    `<div class="version-meta"><span>版本日期 ${esc(version.version_date)}</span><span>${esc(version.filename || "粘贴文本")}</span><span>${version.sections?.length || 0} 个章节</span></div>${warnings(version.warnings)}<div class="segmented import-tabs" role="group" aria-label="版本查看方式"><button type="button" class="active" data-action="version-tab" data-value="sections" aria-pressed="true">章节与校正</button><button type="button" data-action="version-tab" data-value="raw" aria-pressed="false">原始提取文本</button><button type="button" data-action="version-tab" data-value="source" aria-pressed="false">来源与文件</button></div><div class="tab-pane" id="version-sections-pane"><div class="help-box">检查标题和正文是否完整。尚未参与比对的版本可以校正章节；已参与比对的版本会锁定，以保留原有审核依据。校正不会改写原始文件。</div><form id="sections-form" data-version-id="${id(versionId)}"><div id="section-editors">${(version.sections || []).map(sectionEditor).join("")}</div><div class="editor-actions">${button("添加章节", "add-section", "", "", "plus")}</div><div class="form-actions sticky-form-actions">${button("关闭", "close-modal")}<button type="submit" class="button primary">${icon("check")} 保存章节校正</button></div></form></div><div class="tab-pane" id="version-raw-pane" hidden><pre class="raw-text">${esc(version.text)}</pre></div><div class="tab-pane" id="version-source-pane" hidden><div class="field"><span class="metadata-label">资料来源</span><div class="metadata-value">${esc(version.source || "未填写")}</div></div><div class="field"><span class="metadata-label">原始文件 SHA-256</span><div class="hash">${esc(version.sha256)}</div></div><div class="field"><span class="metadata-label">导入时间</span><div class="metadata-value">${esc(fmtDate(version.created_at, true))}</div></div><a class="button" href="/api/versions/${id(version.id)}/file">${icon("download")} 下载原始文件</a></div>`,
    true,
  );
  state.modalVersion = version;
}

document.addEventListener("input", (event) => {
  const element = event.target;
  if (element.dataset.noteId) {
    state.drafts.set(Number(element.dataset.noteId), element.value);
    return;
  }
  if (element.id === "drug-search") {
    const query = element.value.toLocaleLowerCase().trim();
    const filtered = state.drugs.filter((item) =>
      [item.name, item.manufacturer, item.approval_number].some((value) =>
        String(value || "")
          .toLocaleLowerCase()
          .includes(query),
      ),
    );
    $("#drug-list").innerHTML = drugTable(filtered);
    $("#drug-count").textContent = `${filtered.length} 个药品档案`;
  }
  if (element.id === "comparison-search") filterComparisonTable();
  if (element.id === "audit-search") {
    const query = element.value.toLocaleLowerCase().trim();
    const filtered = state.audit.filter((item) =>
      [
        auditLabel(item.action),
        item.entity_type,
        item.entity_id,
        item.actor,
        detailText(item.detail),
      ].some((value) =>
        String(value || "")
          .toLocaleLowerCase()
          .includes(query),
      ),
    );
    $("#audit-list").innerHTML = auditTable(filtered);
    $("#audit-count").textContent = `${filtered.length} 条记录`;
  }
});
function filterComparisonTable() {
  const query = $("#comparison-search").value.toLocaleLowerCase().trim();
  const status = $("#comparison-status").value;
  const filtered = state.comparisons.filter(
    (item) =>
      [item.drug_name, item.old_label, item.new_label].some((value) =>
        String(value || "")
          .toLocaleLowerCase()
          .includes(query),
      ) &&
      (status === "all" ||
        (status === "pending"
          ? Number(item.pending_changes) > 0
          : !Number(item.pending_changes))),
  );
  $("#comparison-list").innerHTML = comparisonTable(filtered);
  $("#comparison-count").textContent = `${filtered.length} 个比对任务`;
}
document.addEventListener("change", (event) => {
  if (event.target.id === "comparison-status") filterComparisonTable();
  if (event.target.id === "priority-filter") {
    state.priority = event.target.value;
    $("#changes-list").innerHTML = changesMarkup();
  }
});

document.addEventListener("click", async (event) => {
  const trigger = event.target.closest("[data-action]");
  if (!trigger || trigger.disabled) return;
  const action = trigger.dataset.action;
  try {
    if (action === "new-drug") return newDrugModal();
    if (action === "close-modal") return closeModal();
    if (action === "refresh") return navigate();
    if (action === "import-version") return importModal(trigger.dataset.id);
    if (action === "filter-review") {
      state.reviewStatus = trigger.dataset.value;
      $$('[data-action="filter-review"]').forEach((button) => {
        const active = button.dataset.value === state.reviewStatus;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", String(active));
      });
      $("#changes-list").innerHTML = changesMarkup();
      return;
    }
    if (action === "import-tab") {
      const mode = trigger.dataset.value;
      $("#import-mode").value = mode;
      $$('[data-action="import-tab"]').forEach((button) => {
        const active = button.dataset.value === mode;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", String(active));
      });
      $("#import-file-pane").hidden = mode !== "file";
      $("#import-text-pane").hidden = mode !== "text";
      $("#version-file").disabled = mode !== "file";
      $("#version-file").required = mode === "file";
      $("#version-text").disabled = mode !== "text";
      $("#version-text").required = mode === "text";
      return;
    }
    if (action === "version-tab") {
      const tab = trigger.dataset.value;
      $$('[data-action="version-tab"]').forEach((button) => {
        const active = button.dataset.value === tab;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", String(active));
      });
      ["sections", "raw", "source"].forEach((name) => {
        $(`#version-${name}-pane`).hidden = name !== tab;
      });
      return;
    }
    if (action === "add-section") {
      $("#section-editors").insertAdjacentHTML(
        "beforeend",
        sectionEditor(
          { title: "新章节", text: "" },
          $$(".section-editor").length,
        ),
      );
      $$(".section-editor").at(-1).querySelector("input").focus();
      return;
    }
    if (action === "remove-section") {
      if ($$(".section-editor").length <= 1) {
        modalError("至少保留一个章节。");
        return;
      }
      const editor = trigger.closest(".section-editor");
      if (
        $("textarea", editor).value.trim() &&
        !window.confirm(
          "移除此章节会从结构化版本中删除其正文，原始文件仍保留。确认移除？",
        )
      )
        return;
      editor.remove();
      return;
    }
    trigger.disabled = true;
    if (action === "demo") {
      const data = await api("/api/demo", { method: "POST" });
      toast("模拟示例已就绪，所有示例内容均不用于临床。");
      const next = `comparisons/${id(data.comparison_id)}`;
      if (state.route === next) await navigate();
      else location.hash = next;
    } else if (action === "view-version")
      await viewVersionModal(trigger.dataset.id);
    else if (action === "new-comparison")
      await newComparisonModal(trigger.dataset.id);
    else if (action === "review") await saveReview(trigger);
  } catch (error) {
    if (modal.open) modalError(error.message);
    else toast(error.message, true);
  } finally {
    if (trigger.isConnected) trigger.disabled = false;
  }
});

async function saveReview(trigger) {
  const change = state.comparison.changes.find(
    (item) => item.id === Number(trigger.dataset.id),
  );
  if (!change) throw new Error("变更记录已更新，请刷新页面。");
  const note = $(`#note-${id(change.id)}`).value.trim();
  const status = trigger.dataset.status;
  if (status === "ignored" && !note) {
    $(`#note-${id(change.id)}`).focus();
    throw new Error("请填写忽略原因后再保存。");
  }
  const card = $(`#change-${id(change.id)}`);
  const controls = $$("button, textarea", card);
  controls.forEach((element) => {
    element.disabled = true;
  });
  try {
    await api(`/api/changes/${id(change.id)}`, {
      method: "PATCH",
      body: { status, note, revision: change.revision },
    });
    state.drafts.delete(change.id);
    state.comparison = await api(`/api/comparisons/${id(state.comparison.id)}`);
    const scrollY = window.scrollY;
    main.innerHTML = comparisonMarkup();
    window.scrollTo(0, scrollY);
    toast("审核意见已保存，操作记录已更新。");
    const focusTarget =
      $(`#note-${id(change.id)}`) || $('[data-action="filter-review"].active');
    if (focusTarget) focusTarget.focus({ preventScroll: true });
  } catch (error) {
    if (error.status === 409) {
      state.comparison = await api(
        `/api/comparisons/${id(state.comparison.id)}`,
      );
      main.innerHTML = comparisonMarkup();
      throw new Error(
        "此变更已被其他页面更新，已载入最新状态并保留你的未提交意见。请核对后重新保存。",
      );
    }
    throw error;
  } finally {
    controls.forEach((element) => {
      element.disabled = false;
    });
  }
}

document.addEventListener("submit", async (event) => {
  const form = event.target;
  if (
    !["drug-form", "import-form", "comparison-form", "sections-form"].includes(
      form.id,
    )
  )
    return;
  event.preventDefault();
  if (form.dataset.busy === "true") return;
  const submit = $('button[type="submit"]', form);
  const previousText = submit.innerHTML;
  const formData = new FormData(form);
  form.dataset.busy = "true";
  submit.disabled = true;
  submit.innerHTML = '<span class="spinner"></span> 正在处理…';
  $(".dialog-error", modal)?.remove();
  try {
    if (form.id === "drug-form") {
      const payload = Object.fromEntries(formData);
      Object.keys(payload).forEach((key) => {
        payload[key] = payload[key].trim();
      });
      if (!payload.name) throw new Error("请输入药品名称。");
      const drug = await api("/api/drugs", { method: "POST", body: payload });
      closeModal();
      toast("药品档案已建立，现在可以导入说明书。");
      location.hash = `drugs/${id(drug.id)}`;
    } else if (form.id === "import-form") {
      const drugId = form.dataset.drugId;
      const mode = $("#import-mode").value;
      let version;
      if (mode === "file") {
        const file = formData.get("file");
        if (!file || !file.size) throw new Error("请选择非空的说明书文件。");
        if (file.size > 10 * 1024 * 1024)
          throw new Error("文件超过 10 MB，请缩减后重试。");
        if (!/\.(pdf|docx|txt)$/i.test(file.name))
          throw new Error("请选择 PDF、DOCX 或 TXT 文件。");
        version = await api(`/api/drugs/${id(drugId)}/versions`, {
          method: "POST",
          body: formData,
        });
      } else {
        const payload = {
          label: formData.get("label").trim(),
          version_date: formData.get("version_date"),
          source: formData.get("source").trim(),
          text: formData.get("text"),
        };
        if (!payload.text.trim()) throw new Error("请粘贴说明书原文。");
        version = await api(`/api/drugs/${id(drugId)}/versions/text`, {
          method: "POST",
          body: payload,
        });
      }
      closeModal();
      toast("版本已导入，请检查章节识别结果。");
      await navigate();
      await viewVersionModal(version.id);
    } else if (form.id === "comparison-form") {
      const oldId = Number(formData.get("old_version_id"));
      const newId = Number(formData.get("new_version_id"));
      if (oldId === newId) throw new Error("请选择两个不同的说明书版本。");
      const result = await api("/api/comparisons", {
        method: "POST",
        body: { old_version_id: oldId, new_version_id: newId },
      });
      closeModal();
      toast("比对已生成，开始逐条复核。");
      const route = `comparisons/${id(result.id)}`;
      if (state.route === route) await navigate();
      else location.hash = route;
    } else if (form.id === "sections-form") {
      const sections = $$(".section-editor", form).map((editor) => ({
        title: $("input", editor).value.trim(),
        text: $("textarea", editor).value,
      }));
      if (sections.some((section) => !section.title))
        throw new Error("每个章节都需要填写标题。");
      if (!sections.length || !sections.some((section) => section.text.trim()))
        throw new Error("请至少保留一个有正文的章节。");
      const versionId = form.dataset.versionId;
      await api(`/api/versions/${id(versionId)}/sections`, {
        method: "PUT",
        body: { sections },
      });
      toast("章节校正已保存，原始文件保持不变。");
      await navigate();
      await viewVersionModal(versionId);
    }
  } catch (error) {
    modalError(error.message);
  } finally {
    form.dataset.busy = "false";
    if (submit.isConnected) {
      submit.disabled = false;
      submit.innerHTML = previousText;
    }
  }
});

navigate();
