"use strict";

// ---------------------------------------------------------------------------
// 状态与 DOM 构建
// ---------------------------------------------------------------------------

const sides = ["A", "B"];

function $(id) { return document.getElementById(id); }

function state(side) {
  return {
    n: parseInt($(`n-${side}`).value, 10),
    safe: $(`safe-${side}`).value,
    initial: $(`initial-${side}`).value,
    symbols: $(`symbols-${side}`).value,
  };
}

function parseSymbols(text) {
  return text.split(",").map((s) => s.trim()).filter((s) => s.length > 0);
}

function buildCommands(side) {
  const n = parseInt($(`n-${side}`).value, 10);
  const symbols = parseSymbols($(`symbols-${side}`).value);
  const container = $(`commands-${side}`);
  const old = readMatrices(side);
  container.innerHTML = "";

  if (!(n >= 1)) return;

  for (const symbol of symbols) {
    const block = document.createElement("div");
    block.className = "command-block";
    const h = document.createElement("h3");
    h.textContent = `命令 ${JSON.stringify(symbol)}`;
    block.appendChild(h);

    const table = document.createElement("table");
    table.className = "matrix";

    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    headRow.appendChild(Object.assign(document.createElement("th"), { textContent: "源状态 \\ 条目" }));
    for (let c = 0; c < n; c++) {
      const th = document.createElement("th");
      th.textContent = `#${c}`;
      headRow.appendChild(th);
    }
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    for (let r = 0; r < n; r++) {
      const tr = document.createElement("tr");
      const rowLabel = document.createElement("th");
      rowLabel.textContent = `状态 ${r}`;
      tr.appendChild(rowLabel);

      for (let c = 0; c < n; c++) {
        const td = document.createElement("td");

        const target = document.createElement("select");
        target.dataset.path = `${side}.commands.${symbol}.rows.${r}.${c}.target`;
        for (let j = 0; j < n; j++) {
          const opt = document.createElement("option");
          opt.value = String(j);
          opt.textContent = `→ ${j}`;
          if (j === c) opt.selected = true;
          target.appendChild(opt);
        }

        const prob = document.createElement("input");
        prob.type = "text";
        prob.placeholder = "p/q";
        prob.dataset.path = `${side}.commands.${symbol}.rows.${r}.${c}.prob`;
        // 默认单位矩阵，方便录入
        prob.value = r === c ? "1" : "0";

        const prev = old[symbol] && old[symbol][r] && old[symbol][r][c];
        if (prev) {
          if ([...target.options].some((o) => o.value === String(prev.target))) {
            target.value = String(prev.target);
          }
          prob.value = prev.prob;
        }

        target.addEventListener("input", markDirty);
        prob.addEventListener("input", markDirty);
        td.appendChild(target);
        td.appendChild(document.createTextNode(" "));
        td.appendChild(prob);
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    block.appendChild(table);
    container.appendChild(block);
  }
}

function readMatrices(side) {
  const out = {};
  const container = $(`commands-${side}`);
  const blocks = container.querySelectorAll(".command-block");
  for (const block of blocks) {
    const symbol = JSON.parse(block.querySelector("h3").textContent.replace("命令 ", ""));
    const rows = [];
    const trs = block.querySelectorAll("tbody tr");
    trs.forEach((tr) => {
      const row = [];
      tr.querySelectorAll("td").forEach((td) => {
        row.push({
          target: parseInt(td.querySelector("select").value, 10),
          prob: td.querySelector("input").value.trim(),
        });
      });
      rows.push(row);
    });
    out[symbol] = rows;
  }
  return out;
}

function collectPayload(side) {
  const n = parseInt($(`n-${side}`).value, 10);
  const initial = $(`initial-${side}`).value.split(/[\s,]+/).filter(Boolean);
  const safe = $(`safe-${side}`).value.split(/[\s,]+/).filter(Boolean).map(Number);
  const matrices = readMatrices(side);
  return {
    n,
    initial,
    safe,
    commands: Object.keys(matrices).map((symbol) => ({ symbol, rows: matrices[symbol] })),
  };
}

// ---------------------------------------------------------------------------
// 草稿修改即清除旧结论（“不显示旧结论”）
// ---------------------------------------------------------------------------

let dirty = false;
let draftVersion = 0;
function markDirty() {
  dirty = true;
  draftVersion += 1;
  $("result").hidden = true;
  // 草稿一旦修改，同时清空最小实现审计的压缩结论；
  // 审计区与双规程复核区相互独立，互不覆盖。
  $("audit").hidden = true;
  clearHighlights();
}

for (const side of sides) {
  $(`n-${side}`).addEventListener("input", () => { markDirty(); buildCommands(side); });
  $(`symbols-${side}`).addEventListener("input", () => { markDirty(); buildCommands(side); });
  $(`safe-${side}`).addEventListener("input", markDirty);
  $(`initial-${side}`).addEventListener("input", markDirty);
}

// ---------------------------------------------------------------------------
// 错误定位
// ---------------------------------------------------------------------------

function clearHighlights() {
  document.querySelectorAll("input.invalid, select.invalid").forEach((el) => {
    el.classList.remove("invalid");
  });
}

function locToSelector(loc) {
  // loc 形如 ["A", "commands", 0, "rows", 1, 2, "prob"]
  // 或 ["A","initial",0] / ["A","safe",0] / ["A","n"] / ["commands"]
  if (!Array.isArray(loc) || loc.length === 0) return null;
  const side = loc[0];
  if (side !== "A" && side !== "B") return null;

  if (loc[1] === "initial" && loc.length >= 3) return `${side}.initial.${loc[2]}`;
  if (loc[1] === "safe" && loc.length >= 3) return `${side}.safe.${loc[2]}`;
  if (loc[1] === "n") return `n-${side}`;

  if (loc[1] === "commands") {
    // [side, "commands", index, ...]
    const rest = loc.slice(2);
    if (rest.length === 0) return null;
    const container = $(`commands-${side}`);
    const blocks = container.querySelectorAll(".command-block");
    const block = blocks[rest[0]];
    if (!block) return null;
    if (rest.length === 1) return null; // 高亮整个命令块（用其首个输入代替）
    // ["rows", r, c, field?]
    if (rest[1] === "rows" && rest.length >= 3) {
      const r = rest[2];
      const tr = block.querySelectorAll("tbody tr")[r];
      if (!tr) return null;
      if (rest.length === 3) {
        const first = tr.querySelector("input");
        return first ? first.dataset.path : null;
      }
      const c = rest[3];
      const td = tr.querySelectorAll("td")[c];
      if (!td) return null;
      const field = rest[4] === "target" ? "select" : "input";
      const el = td.querySelector(field);
      return el ? el.dataset.path : null;
    }
    if (rest[1] === "symbol") {
      const el = block.querySelector("input, select");
      return el ? el.dataset.path : null;
    }
  }
  return null;
}

function showErrors(errors) {
  clearHighlights();
  const box = $("errors");
  box.innerHTML = "";
  const h = document.createElement("h2");
  h.textContent = `复核未通过：发现 ${errors.length} 处录入问题（已在表单中定位）`;
  box.appendChild(h);
  const ul = document.createElement("ul");
  for (const err of errors) {
    const li = document.createElement("li");
    li.className = "error-item";
    const loc = document.createElement("span");
    loc.className = "error-loc";
    loc.textContent = err.loc && err.loc.length ? `[${err.loc.join(" › ")}] ` : "";
    li.appendChild(loc);
    li.appendChild(document.createTextNode(err.msg));
    ul.appendChild(li);

    const path = locToSelector(err.loc);
    if (path) {
      const el = document.querySelector(`[data-path="${CSS.escape(path)}"]`);
      if (el) el.classList.add("invalid");
    }
    if (err.loc && (err.loc[0] === "A" || err.loc[0] === "B")) {
      const field = err.loc[1];
      if (field === "initial") $(`initial-${err.loc[0]}`).classList.add("invalid");
      if (field === "safe") $(`safe-${err.loc[0]}`).classList.add("invalid");
      if (field === "n") $(`n-${err.loc[0]}`).classList.add("invalid");
    }
  }
  box.appendChild(ul);
  box.hidden = false;
}

// ---------------------------------------------------------------------------
// 结果渲染
// ---------------------------------------------------------------------------

function fracText(f) { return f.text; }

function renderEquivalent() {
  const box = $("result");
  box.className = "result verdict-equivalent";
  box.innerHTML = "";
  const h = document.createElement("h2");
  h.textContent = "✓ 两份规程对所有有限命令串精确等价";
  box.appendChild(h);
  const p = document.createElement("p");
  p.textContent =
    "经任意精度有理数线性子空间判定（覆盖全部有限命令串，非长度上限内抽样）：" +
    "现网规程 A 与候选规程 B 在每个有限命令串下的最终进入安全态概率完全相同。";
  box.appendChild(p);
  box.hidden = false;
}

function renderTraceTable(trace, nStates) {
  const table = document.createElement("table");
  table.className = "step-table";
  const thead = document.createElement("thead");
  const hr = document.createElement("tr");
  const headers = ["命令串后", ...Array.from({ length: nStates }, (_, i) => `状态 ${i}`), "安全概率"];
  for (const text of headers) {
    const th = document.createElement("th");
    th.textContent = text;
    hr.appendChild(th);
  }
  thead.appendChild(hr);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  trace.steps.forEach((step, idx) => {
    const tr = document.createElement("tr");
    const td0 = document.createElement("td");
    td0.style.textAlign = "center";
    td0.textContent = idx === 0 ? "ε（空串）" : `第 ${idx} 步`;
    tr.appendChild(td0);
    for (const v of step.distribution) {
      const td = document.createElement("td");
      td.textContent = fracText(v);
      tr.appendChild(td);
    }
    const tdSafe = document.createElement("td");
    tdSafe.textContent = fracText(step.safeProb);
    tdSafe.style.fontWeight = "600";
    tr.appendChild(tdSafe);
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  return table;
}

function renderCounterexample(result) {
  const box = $("result");
  box.className = "result verdict-diff";
  box.innerHTML = "";

  const h = document.createElement("h2");
  h.textContent = "✗ 两份规程不等价";
  box.appendChild(h);

  const meta = document.createElement("p");
  const word = result.word.length ? result.word.join("") : "ε（空串）";
  meta.innerHTML =
    `最短反例命令串：<span class="word-badge">${word.replace(/</g, "&lt;")}</span>` +
    `（长度 ${result.wordLength}，同长度内 ASCII 字典序最小）`;
  box.appendChild(meta);

  const traceWrap = document.createElement("div");
  traceWrap.className = "trace";
  for (const side of sides) {
    const div = document.createElement("div");
    const h3 = document.createElement("h3");
    h3.textContent = `规程 ${side}：最终安全概率 ${fracText(result[side].final)}`;
    div.appendChild(h3);
    div.appendChild(renderTraceTable(result[side], result[side].steps[0].distribution.length));
    traceWrap.appendChild(div);
  }
  box.appendChild(traceWrap);

  const diff = document.createElement("p");
  diff.className = "diff-line";
  diff.textContent = `最终概率差 P_A(安全) − P_B(安全) = ${fracText(result.difference)}`;
  box.appendChild(diff);

  box.hidden = false;
}

// ---------------------------------------------------------------------------
// 提交
// ---------------------------------------------------------------------------

$("btn-review").addEventListener("click", async () => {
  clearHighlights();
  $("errors").hidden = true;
  $("result").hidden = true;
  dirty = false;

  let payload;
  try {
    payload = { A: collectPayload("A"), B: collectPayload("B") };
  } catch (e) {
    showErrors([{ loc: [], msg: `表单读取失败：${e.message}` }]);
    return;
  }

  try {
    const resp = await fetch("/api/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (!resp.ok || !data.ok) {
      showErrors(data.errors || [{ loc: [], msg: "服务端返回未知错误" }]);
      return;
    }
    if (data.result.equivalent) renderEquivalent();
    else renderCounterexample(data.result);
  } catch (e) {
    showErrors([{ loc: [], msg: `API 请求失败：${e.message}` }]);
  }
});

// ---------------------------------------------------------------------------
// 最小安全观测实现审计（可对 A 或 B 单侧发起）
// ---------------------------------------------------------------------------

function wordText(prefix, middle, suffix) {
  const p = prefix.length ? prefix : "ε";
  const s = suffix.length ? suffix : "ε";
  return middle ? `${p} · ${middle} · ${s}` : `${p} · ${s}`;
}

function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

function renderFractionMatrix(mat) {
  const table = document.createElement("table");
  table.className = "step-table compact-matrix";
  const tbody = document.createElement("tbody");
  mat.forEach((row) => {
    const tr = document.createElement("tr");
    row.forEach((f) => {
      const td = document.createElement("td");
      td.textContent = fracText(f);
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  return table;
}

function renderVector(v) {
  return "(" + v.map(fracText).join(", ") + ")";
}

function renderAudit(side, a) {
  const box = $("audit");
  box.className = "result verdict-audit";
  box.innerHTML = "";

  const h = document.createElement("h2");
  h.textContent = `最小安全观测实现审计 · 规程 ${side}`;
  box.appendChild(h);

  // --- 维数总览 ---
  const dim = document.createElement("div");
  dim.className = "audit-dims";
  dim.innerHTML =
    `原状态数 <strong>${a.n}</strong> ｜ 初始分布可达行空间秩 <strong>${a.reachableRank}</strong>` +
    ` ｜ 安全观测反向列空间秩 <strong>${a.observableRank}</strong>` +
    ` ｜ Hankel 秩 / 最小维数 <strong class="${a.reduced ? "dim-reduced" : "dim-equal"}">${a.minimalDimension}</strong>`;
  box.appendChild(dim);

  // --- 语义边界声明 ---
  const note = document.createElement("div");
  note.className = a.reduced ? "audit-warning" : "audit-note";
  if (a.reduced) {
    note.innerHTML =
      `最小维数 <strong>${a.minimalDimension}</strong> &lt; 原状态数 <strong>${a.n}</strong>：` +
      "存在 " + (a.n - a.minimalDimension) + " 维对“任意有限 ASCII 命令串结束时进入安全态的概率”不产生影响。" +
      "该压缩模型由 Hankel 配对秩导出，<strong>只保持安全概率语义，不是可直接替换原控制器的随机控制器</strong>" +
      "（命令矩阵条目可为负、行和不必为 1，也不对应真实状态分布）。";
  } else {
    note.innerHTML =
      `最小维数 <strong>${a.minimalDimension}</strong> = 原状态数 <strong>${a.n}</strong>：` +
      "不存在可收缩的冗余维度。以下仍返回完整证据（基串、初始/终止向量、各命令矩阵与逐项回放）。";
  }
  box.appendChild(note);

  // --- 基命令串 ---
  const basis = document.createElement("div");
  basis.className = "audit-basis";
  const fmtList = (ws) => ws.length ? ws.map((w) => `“${w || "ε"}”`).join("，") : "（空）";
  basis.innerHTML =
    `<div><span class="k">可达行空间前缀基（共 ${a.prefixBasis.length} 个）：</span>${esc(fmtList(a.prefixBasis))}</div>` +
    `<div><span class="k">观测列空间后缀基（共 ${a.suffixBasis.length} 个）：</span>${esc(fmtList(a.suffixBasis))}</div>` +
    `<div><span class="k">Hankel 主元前缀（最小实现坐标，共 ${a.pivotPrefixes.length} 个）：</span>${esc(fmtList(a.pivotPrefixes))}</div>` +
    `<div><span class="k">Hankel 主元后缀（共 ${a.pivotSuffixes.length} 个）：</span>${esc(fmtList(a.pivotSuffixes))}</div>`;
  box.appendChild(basis);

  // --- 初始 / 终止向量 ---
  const vecs = document.createElement("div");
  vecs.className = "audit-vectors";
  if (a.minimalDimension === 0) {
    vecs.innerHTML =
      '<div class="hint">最小维数为 0：安全态从初始分布经任意命令串都不可达，' +
      "安全概率函数恒为零，初始/终止向量与命令矩阵均为空。</div>";
  } else {
    vecs.innerHTML =
      `<div><span class="k">初始向量 α：</span><code>${esc(renderVector(a.alpha))}</code></div>` +
      `<div><span class="k">终止向量 β：</span><code>${esc(renderVector(a.beta))}</code></div>`;
  }
  vecs.insertAdjacentHTML(
    "beforeend",
    '<div class="hint">对任意有限命令串 w：f(w) = α · A(w) · β，与原规程 π · M(w) · 1_安全态 精确相等。</div>'
  );
  box.appendChild(vecs);

  // --- 各命令矩阵 ---
  const mh = document.createElement("h3");
  mh.textContent = "各命令的最小实现矩阵 A(c)（Hankel 坐标，精确分数）";
  box.appendChild(mh);
  const mwrap = document.createElement("div");
  mwrap.className = "audit-matrices";
  Object.keys(a.matrices).sort().forEach((symbol) => {
    const div = document.createElement("div");
    const hh = document.createElement("div");
    hh.className = "k";
    hh.textContent = `A(${JSON.stringify(symbol)})`;
    div.appendChild(hh);
    if (a.minimalDimension === 0) {
      const p = document.createElement("p");
      p.className = "hint";
      p.textContent = "最小维数为 0：安全概率函数恒为零，无矩阵条目。";
      div.appendChild(p);
    } else {
      div.appendChild(renderFractionMatrix(a.matrices[symbol]));
    }
    mwrap.appendChild(div);
  });
  box.appendChild(mwrap);

  // --- 逐项回放取证 ---
  const rh = document.createElement("h3");
  rh.textContent = `基对逐项回放（原规程概率 vs 压缩模型概率，共 ${a.replay.length} 项）`;
  box.appendChild(rh);
  const table = document.createElement("table");
  table.className = "step-table replay-table";
  const thead = document.createElement("thead");
  thead.innerHTML =
    "<tr><th>类别</th><th>命令串（前缀 · 命令 · 后缀）</th><th>长度</th>" +
    "<th>原规程安全概率</th><th>压缩模型安全概率</th><th>主元</th><th>一致</th></tr>";
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  const kindName = { empty: "空串", basis: "基对", transition: "转移对" };
  a.replay.forEach((item) => {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${kindName[item.kind] || item.kind}</td>` +
      `<td class="mono">${esc(wordText(item.prefix, item.middle, item.suffix))}</td>` +
      `<td>${item.wordLength}</td>` +
      `<td>${fracText(item.original)}</td>` +
      `<td>${fracText(item.compressed)}</td>` +
      `<td>${item.pivot ? "★" : ""}</td>` +
      `<td>${item.match ? "✓" : "✗"}</td>`;
    if (!item.match) tr.classList.add("replay-mismatch");
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  box.appendChild(table);

  const verdict = document.createElement("p");
  verdict.className = a.verified ? "audit-ok" : "audit-fail";
  verdict.textContent = a.verified
    ? "✓ 全部基对回放精确一致：压缩模型可复算（精确分数，无浮点）。"
    : "✗ 存在回放不一致项，请复核输入。";
  box.appendChild(verdict);

  box.hidden = false;
}

async function runAudit(side) {
  clearHighlights();
  $("errors").hidden = true;
  $("audit").hidden = true;

  let procedure;
  try {
    procedure = collectPayload(side);
  } catch (e) {
    showErrors([{ loc: [side], msg: `表单读取失败：${e.message}` }]);
    return;
  }

  // 记录发起时的草稿版本：返回时若版本已变（审计期间草稿被修改），
  // 丢弃本次压缩结论，不渲染、不覆盖任何现有结果。
  const versionAtRequest = draftVersion;

  let data;
  let resp;
  try {
    resp = await fetch("/api/audit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ side, procedure }),
    });
    data = await resp.json();
  } catch (e) {
    if (versionAtRequest === draftVersion) {
      showErrors([{ loc: [], msg: `审计接口请求失败：${e.message}` }]);
    }
    return;
  }

  if (versionAtRequest !== draftVersion) return;

  if (!resp.ok || !data.ok) {
    // 未通过原有校验：不产出压缩结论（审计区保持隐藏），错误在表单中定位
    showErrors(data.errors || [{ loc: [], msg: "服务端返回未知错误" }]);
    return;
  }
  renderAudit(data.side, data.audit);
}

$("btn-audit-A").addEventListener("click", () => runAudit("A"));
$("btn-audit-B").addEventListener("click", () => runAudit("B"));

// ---------------------------------------------------------------------------
// 示例
// ---------------------------------------------------------------------------

function fillForm(side, { n, initial, safe, symbols, matrices }) {
  $(`n-${side}`).value = String(n);
  $(`initial-${side}`).value = initial;
  $(`safe-${side}`).value = safe;
  $(`symbols-${side}`).value = symbols;
  buildCommands(side);
  const container = $(`commands-${side}`);
  container.querySelectorAll(".command-block").forEach((block) => {
    const symbol = JSON.parse(block.querySelector("h3").textContent.replace("命令 ", ""));
    block.querySelectorAll("tbody tr").forEach((tr, r) => {
      tr.querySelectorAll("td").forEach((td, c) => {
        td.querySelector("select").value = String(c);
        td.querySelector("input").value = matrices[symbol][r][c];
      });
    });
  });
  markDirty();
}

$("btn-example").addEventListener("click", () => {
  // 差异只在长度 2 的 "aa" 暴露
  fillForm("A", {
    n: 3,
    initial: "1 0 0",
    safe: "2",
    symbols: "a",
    matrices: {
      a: [["0", "1", "0"], ["0", "1", "0"], ["0", "0", "1"]],
    },
  });
  fillForm("B", {
    n: 3,
    initial: "1 0 0",
    safe: "2",
    symbols: "a",
    matrices: {
      a: [["0", "1", "0"], ["0", "0", "1"], ["0", "0", "1"]],
    },
  });
});

$("btn-equivalent-example").addEventListener("click", () => {
  const m = [["1/2", "1/2"], ["1/3", "2/3"]];
  for (const side of sides) {
    fillForm(side, {
      n: 2,
      initial: "1 0",
      safe: "1",
      symbols: "x",
      matrices: { x: m },
    });
  }
});

$("btn-reducible-example").addEventListener("click", () => {
  // 3 态但状态 1、2 转移行为完全一致：Hankel 秩 = 2，最小实现 3→2 收缩。
  const m = [["0", "1/2", "1/2"], ["1", "0", "0"], ["1", "0", "0"]];
  fillForm("A", {
    n: 3,
    initial: "1 0 0",
    safe: "2",
    symbols: "a",
    matrices: { a: m },
  });
  fillForm("B", {
    n: 3,
    initial: "1 0 0",
    safe: "1 2",
    symbols: "a",
    matrices: { a: m },
  });
});

// 初始化
for (const side of sides) buildCommands(side);
