const state = {
  workspaceId: null,
  dashboard: null,
  runs: [],
  sourceField: "wips_independent_claims",
  filterTopicIds: new Set(),
  mergeMode: false,
  mergeTopicIds: new Set(),
  mergeSuggestions: [],
  mergeHistory: [],
  search: "",
  busy: false,
};

const elements = {
  workspaceSelect: document.querySelector("#workspaceSelect"),
  workspaceMeta: document.querySelector("#workspaceMeta"),
  refreshButton: document.querySelector("#refreshButton"),
  sourceStatus: document.querySelector("#sourceStatus"),
  sourceTabs: [...document.querySelectorAll(".source-tab")],
  candidatePanel: document.querySelector("#candidatePanel"),
  topicList: document.querySelector("#topicList"),
  filterCount: document.querySelector("#filterCount"),
  clearFilterButton: document.querySelector("#clearFilterButton"),
  mergeModeButton: document.querySelector("#mergeModeButton"),
  incrementalButton: document.querySelector("#incrementalButton"),
  mergeTray: document.querySelector("#mergeTray"),
  mergeSelection: document.querySelector("#mergeSelection"),
  mergeSuggestions: document.querySelector("#mergeSuggestions"),
  mergeHistory: document.querySelector("#mergeHistory"),
  mergeLabel: document.querySelector("#mergeLabel"),
  confirmMergeButton: document.querySelector("#confirmMergeButton"),
  searchInput: document.querySelector("#searchInput"),
  resultCount: document.querySelector("#resultCount"),
  patentRows: document.querySelector("#patentRows"),
  emptyState: document.querySelector("#emptyState"),
  toast: document.querySelector("#toast"),
};

document.addEventListener("DOMContentLoaded", initialize);

async function initialize() {
  bindEvents();
  await loadWorkspaces();
}

function bindEvents() {
  elements.workspaceSelect.addEventListener("change", async (event) => {
    state.workspaceId = Number(event.target.value);
    clearSelections();
    await loadWorkspace();
  });
  elements.refreshButton.addEventListener("click", loadWorkspace);
  elements.sourceTabs.forEach((button) => button.addEventListener("click", () => {
    state.sourceField = button.dataset.source;
    clearSelections();
    render();
  }));
  elements.clearFilterButton.addEventListener("click", () => {
    state.filterTopicIds.clear();
    renderTopics();
    renderPatents();
  });
  elements.mergeModeButton.addEventListener("click", toggleMergeMode);
  elements.incrementalButton.addEventListener("click", runIncremental);
  elements.confirmMergeButton.addEventListener("click", confirmMerge);
  elements.searchInput.addEventListener("input", (event) => {
    state.search = event.target.value.trim().toLowerCase();
    renderPatents();
  });
}

async function loadWorkspaces() {
  try {
    const workspaces = await api("/api/workspaces");
    elements.workspaceSelect.innerHTML = workspaces.map((item) =>
      `<option value="${item.workspace_id}">${escapeHtml(item.workspace_name)} (${item.patent_count})</option>`
    ).join("");
    if (!workspaces.length) {
      elements.workspaceSelect.innerHTML = "<option>尚無 workspace</option>";
      showToast("尚無 workspace，請先建立並完成初次分群。", true);
      return;
    }
    state.workspaceId = Number(elements.workspaceSelect.value);
    await loadWorkspace();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function loadWorkspace() {
  if (!state.workspaceId) return;
  setBusy(true);
  try {
    [state.dashboard, state.runs] = await Promise.all([
      api(`/api/workspaces/${state.workspaceId}`),
      api(`/api/workspaces/${state.workspaceId}/runs`),
    ]);
    render();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

function render() {
  if (!state.dashboard) return;
  const workspace = state.dashboard.workspace;
  elements.workspaceMeta.textContent = `${workspace.workspace_name} · ${state.dashboard.patents.length} 件專利`;
  elements.sourceTabs.forEach((button) => button.classList.toggle("active", button.dataset.source === state.sourceField));
  renderCandidates();
  renderTopics();
  renderPatents();
}

function currentSource() {
  return state.dashboard.sources.find((item) => item.source_field === state.sourceField) || { topics: [] };
}

function currentRun() {
  return state.runs.find((item) => item.source_field === state.sourceField);
}

function renderCandidates() {
  const run = currentRun();
  if (!run) {
    elements.sourceStatus.textContent = "尚未執行分群";
    elements.candidatePanel.classList.remove("hidden");
    elements.candidatePanel.innerHTML = '<button class="primary-button" id="calibrateButton">開始分群</button>';
    document.querySelector("#calibrateButton").addEventListener("click", calibrateCurrentSource);
    return;
  }
  if (run.status !== "needs_review" || !run.candidates.length) {
    elements.candidatePanel.classList.add("hidden");
    const status = run.status === "completed" ? "模型已定案" : `模型狀態：${run.status}`;
    elements.sourceStatus.textContent = status;
    return;
  }
  const names = { conservative: "保守", balanced: "平衡", detailed: "細分" };
  elements.sourceStatus.textContent = `${run.input_doc_count} 篇 · 等待選擇方案`;
  elements.candidatePanel.classList.remove("hidden");
  elements.candidatePanel.innerHTML = `
    <h3>選擇主題粒度</h3>
    ${run.candidates.map((item, index) => `
      <label class="candidate-option">
        <input type="radio" name="candidate" value="${item.candidate_id}" ${index === 0 ? "checked" : ""}>
        <span>
          <strong>${names[item.candidate_type] || item.candidate_type} · ${item.candidate_k} 個主題</strong>
          <p>${escapeHtml(item.llm_explanation || "候選說明尚未產生")}</p>
          <span class="metric-row">
            <span>c_v ${formatMetric(item.coherence)}</span>
            <span>diversity ${formatMetric(item.diversity)}</span>
            <span>balance ${formatMetric(item.balance)}</span>
          </span>
        </span>
        <span>${formatMetric(item.score)}</span>
      </label>
    `).join("")}
    <button class="primary-button" id="finalizeCandidateButton">使用此方案</button>
  `;
  document.querySelector("#finalizeCandidateButton").addEventListener("click", finalizeCandidate);
}

function renderTopics() {
  const topics = currentSource().topics || [];
  elements.filterCount.textContent = state.filterTopicIds.size ? `已選 ${state.filterTopicIds.size} 個主題` : "全部主題";
  elements.topicList.innerHTML = topics.map((topic, index) => {
    const selected = state.filterTopicIds.has(topic.topic_id);
    const mergeSelected = state.mergeTopicIds.has(topic.topic_id);
    const canMerge = topic.topic_kind === "model";
    return `
      <div class="topic-item ${selected ? "selected" : ""} ${mergeSelected ? "merge-selected" : ""}" data-topic-id="${topic.topic_id}">
        ${state.mergeMode && canMerge
          ? `<input class="merge-checkbox" type="checkbox" ${mergeSelected ? "checked" : ""} aria-label="選取合併">`
          : "<span></span>"}
        <button class="topic-chip" title="${escapeHtml(topic.label)}">${escapeHtml(topic.label)}</button>
        <span class="topic-count">${topic.doc_count}</span>
        <span class="topic-tools">
          <button class="rename-topic" title="重新命名" aria-label="重新命名">✎</button>
          <button class="move-up" title="往上" aria-label="往上" ${index === 0 ? "disabled" : ""}>↑</button>
          <button class="move-down" title="往下" aria-label="往下" ${index === topics.length - 1 ? "disabled" : ""}>↓</button>
        </span>
      </div>`;
  }).join("");

  elements.topicList.querySelectorAll(".topic-item").forEach((row) => {
    const topicId = Number(row.dataset.topicId);
    row.querySelector(".topic-chip").addEventListener("click", () => toggleFilter(topicId));
    row.querySelector(".merge-checkbox")?.addEventListener("change", () => toggleMergeTopic(topicId));
    row.querySelector(".rename-topic").addEventListener("click", () => renameTopic(topicId));
    row.querySelector(".move-up").addEventListener("click", () => moveTopic(topicId, -1));
    row.querySelector(".move-down").addEventListener("click", () => moveTopic(topicId, 1));
  });
  renderMergeTray();
}

function renderPatents() {
  if (!state.dashboard) return;
  const topicKey = state.sourceField === "effect_summary" ? "effect_topic_id" : "technical_topic_id";
  const visible = state.dashboard.patents.filter((patent) => {
    const matchesTopic = !state.filterTopicIds.size || state.filterTopicIds.has(patent[topicKey]);
    const haystack = `${patent.patent_number || ""} ${patent.title || ""}`.toLowerCase();
    return matchesTopic && (!state.search || haystack.includes(state.search));
  });
  elements.resultCount.textContent = `${visible.length} 件`;
  elements.patentRows.innerHTML = visible.map((patent) => `
    <tr>
      <td>${escapeHtml(patent.patent_number || "-")}</td>
      <td>${escapeHtml(patent.title || "-")}</td>
      <td class="topic-cell">${escapeHtml(patent.technical_topic || "未分類")}</td>
      <td class="topic-cell">${escapeHtml(patent.effect_topic || "未分類")}</td>
      <td>${escapeHtml(patent.country_code || "-")}</td>
    </tr>
  `).join("");
  elements.emptyState.classList.toggle("hidden", visible.length > 0);
}

function toggleFilter(topicId) {
  if (state.filterTopicIds.has(topicId)) state.filterTopicIds.delete(topicId);
  else state.filterTopicIds.add(topicId);
  renderTopics();
  renderPatents();
}

async function toggleMergeMode() {
  state.mergeMode = !state.mergeMode;
  state.mergeTopicIds.clear();
  state.mergeSuggestions = [];
  state.mergeHistory = [];
  elements.mergeModeButton.classList.toggle("active", state.mergeMode);
  elements.mergeModeButton.textContent = state.mergeMode ? "取消合併" : "合併主題";
  renderTopics();
  if (state.mergeMode) await loadMergeSupport();
}

function toggleMergeTopic(topicId) {
  if (state.mergeTopicIds.has(topicId)) state.mergeTopicIds.delete(topicId);
  else if (state.mergeTopicIds.size < 2) state.mergeTopicIds.add(topicId);
  else showToast("一次只能選擇兩個主題。", true);
  renderTopics();
}

function renderMergeTray() {
  elements.mergeTray.classList.toggle("hidden", !state.mergeMode);
  const topics = currentSource().topics.filter((item) => state.mergeTopicIds.has(item.topic_id));
  elements.mergeSelection.textContent = topics.length
    ? topics.map((item) => item.label).join(" ＋ ")
    : "請選擇兩個主題";
  elements.confirmMergeButton.disabled = state.busy || topics.length !== 2;
  elements.mergeSuggestions.innerHTML = state.mergeSuggestions.map((item) => `
    <button class="suggestion-button" data-topic-ids="${item.topic_ids.join(",")}">
      <span>${item.labels.map(escapeHtml).join(" ＋ ")}</span>
      <small>${Number(item.distance).toFixed(3)}</small>
    </button>
  `).join("");
  elements.mergeSuggestions.querySelectorAll(".suggestion-button").forEach((button) => {
    button.addEventListener("click", () => {
      state.mergeTopicIds = new Set(button.dataset.topicIds.split(",").map(Number));
      renderTopics();
    });
  });
  elements.mergeHistory.innerHTML = state.mergeHistory.length
    ? state.mergeHistory.map((item) => {
        const sources = item.source_topics.map((topic) => escapeHtml(topic.label)).join(" ＋ ");
        const result = escapeHtml(item.result_topic.label);
        const disabled = !item.can_unmerge || state.busy;
        return `
          <div class="history-item">
            <span title="${escapeHtml(item.blocked_reason || "")}">${sources} → ${result}</span>
            <button class="restore-merge text-button" data-run-id="${item.merge_run_id}"
              ${disabled ? "disabled" : ""}>${item.is_reverted ? "已復原" : "復原"}</button>
          </div>
        `;
      }).join("")
    : '<span class="history-empty">尚無合併紀錄</span>';
  elements.mergeHistory.querySelectorAll(".restore-merge:not(:disabled)").forEach((button) => {
    button.addEventListener("click", () => restoreMerge(Number(button.dataset.runId)));
  });
}

async function loadMergeSupport() {
  try {
    [state.mergeSuggestions, state.mergeHistory] = await Promise.all([
      api(`/api/workspaces/${state.workspaceId}/merge-suggestions/${state.sourceField}`),
      api(`/api/workspaces/${state.workspaceId}/merge-history/${state.sourceField}`),
    ]);
    renderMergeTray();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function restoreMerge(mergeRunId) {
  if (state.busy) return;
  const record = state.mergeHistory.find((item) => item.merge_run_id === mergeRunId);
  if (!record?.can_unmerge) return;
  const sourceNames = record.source_topics.map((topic) => topic.label).join(" ＋ ");
  if (!window.confirm(`復原「${record.result_topic.label}」為 ${sourceNames}？`)) return;
  setBusy(true);
  try {
    await api(`/api/workspaces/${state.workspaceId}/unmerge/${state.sourceField}/${mergeRunId}`, {
      method: "POST",
      body: JSON.stringify({ reverted_by: "temporary-ui" }),
    });
    showToast("合併紀錄已復原並保存新版模型。")
    state.mergeMode = false;
    state.mergeTopicIds.clear();
    state.mergeHistory = [];
    await loadWorkspace();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function calibrateCurrentSource() {
  setBusy(true);
  try {
    await api(`/api/workspaces/${state.workspaceId}/calibrate/${state.sourceField}`, { method: "POST" });
    await loadWorkspace();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function runIncremental() {
  setBusy(true);
  try {
    const result = await api(`/api/workspaces/${state.workspaceId}/incremental/${state.sourceField}`, {
      method: "POST",
    });
    showToast(result.new_document_count ? `已更新 ${result.new_document_count} 件專利。` : "目前沒有新增專利。");
    await loadWorkspace();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function finalizeCandidate() {
  const selected = document.querySelector("input[name='candidate']:checked");
  if (!selected) return;
  setBusy(true);
  try {
    await api(`/api/runs/${currentRun().run_id}/finalize`, {
      method: "POST",
      body: JSON.stringify({ candidate_id: Number(selected.value), selected_by: "temporary-ui" }),
    });
    showToast("主題方案已定案。")
    await loadWorkspace();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function confirmMerge() {
  if (state.busy || state.mergeTopicIds.size !== 2) return;
  setBusy(true);
  try {
    await api(`/api/workspaces/${state.workspaceId}/merge/${state.sourceField}`, {
      method: "POST",
      body: JSON.stringify({
        topic_ids: [...state.mergeTopicIds],
        merged_by: "temporary-ui",
        label: elements.mergeLabel.value.trim() || null,
      }),
    });
    showToast("兩個主題已合併並保存模型版本。")
    state.mergeMode = false;
    state.mergeTopicIds.clear();
    elements.mergeLabel.value = "";
    await loadWorkspace();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function renameTopic(topicId) {
  const topic = currentSource().topics.find((item) => item.topic_id === topicId);
  const label = window.prompt("主題名稱", topic?.label || "");
  if (!label || label.trim() === topic?.label) return;
  try {
    await api(`/api/topics/${topicId}/label`, {
      method: "PATCH",
      body: JSON.stringify({ label: label.trim(), updated_by: "temporary-ui" }),
    });
    await loadWorkspace();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function moveTopic(topicId, delta) {
  const topics = [...currentSource().topics];
  const index = topics.findIndex((item) => item.topic_id === topicId);
  const target = index + delta;
  if (index < 0 || target < 0 || target >= topics.length) return;
  [topics[index], topics[target]] = [topics[target], topics[index]];
  try {
    await api(`/api/workspaces/${state.workspaceId}/topics/${state.sourceField}/order`, {
      method: "PATCH",
      body: JSON.stringify({ topic_ids: topics.map((item) => item.topic_id) }),
    });
    await loadWorkspace();
  } catch (error) {
    showToast(error.message, true);
  }
}

function clearSelections() {
  state.filterTopicIds.clear();
  state.mergeTopicIds.clear();
  state.mergeMode = false;
  state.mergeSuggestions = [];
  state.mergeHistory = [];
}

function setBusy(busy) {
  state.busy = busy;
  elements.refreshButton.disabled = busy;
  elements.incrementalButton.disabled = busy;
  elements.mergeModeButton.disabled = busy;
  elements.confirmMergeButton.disabled = busy || state.mergeTopicIds.size !== 2;
  document.body.style.cursor = busy ? "progress" : "";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
  return payload;
}

function showToast(message, error = false) {
  elements.toast.textContent = message;
  elements.toast.classList.remove("hidden", "error");
  elements.toast.classList.toggle("error", error);
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => elements.toast.classList.add("hidden"), 4000);
}

function formatMetric(value) {
  return Number(value || 0).toFixed(3);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
