const state = {
  overview: null,
  accounts: [],
  selectedAccount: null,
  selectedKeys: new Set(),
  page: 1,
  pageSize: 50,
  totalPages: 1,
  total: 0,
  activeTab: "accounts",
  filters: {
    q: "",
    status: "",
    quotaErrorType: "",
    group: "",
  },
  sort: {
    sortBy: "",
    sortOrder: "",
  },
};

function createNoopElement() {
  return {
    textContent: "",
    innerHTML: "",
    disabled: false,
    value: "",
    checked: false,
    dataset: {},
    classList: {
      add() {},
      remove() {},
      toggle() {},
      contains() {
        return false;
      },
    },
    addEventListener() {},
    removeEventListener() {},
    querySelector() {
      return createNoopElement();
    },
  };
}

const loginModal = document.getElementById("loginModal");
const loginForm = document.getElementById("loginForm");
const loginToken = document.getElementById("loginToken");
const loginError = document.getElementById("loginError");
const accountListTab = document.getElementById("accountListTab");
const disabledRefreshTab = document.getElementById("disabledRefreshTab");
const accountListPanel = document.getElementById("accountListPanel");
const disabledRefreshPanel = document.getElementById("disabledRefreshPanel");
const accountsTbody = document.getElementById("accountsTbody");
const searchInput = document.getElementById("searchInput");
const statusSelect = document.getElementById("statusSelect");
const quotaErrorSelect = document.getElementById("quotaErrorSelect");
const groupSelect = document.getElementById("groupSelect");
const pageSizeSelect = document.getElementById("pageSizeSelect");
const resultsMeta = document.getElementById("resultsMeta");
const selectedMeta = document.getElementById("selectedMeta");
const selectAllDeactivated = document.getElementById("selectAllDeactivated");
const clearSelectionBtn = document.getElementById("clearSelectionBtn");
const batchEnableBtn = document.getElementById("batchEnableBtn");
const batchRefreshRecoverBtn = document.getElementById("batchRefreshRecoverBtn");
const batchTokenRefreshBtn = document.getElementById("batchTokenRefreshBtn");
const downloadDisabledUnrefreshedBtn = document.getElementById("downloadDisabledUnrefreshedBtn");
const batchDeleteBtn = document.getElementById("batchDeleteBtn");
const deleteAllDeactivatedBtn = document.getElementById("deleteAllDeactivatedBtn");
const pageInfo = document.getElementById("pageInfo");
const prevPageBtn = document.getElementById("prevPageBtn");
const nextPageBtn = document.getElementById("nextPageBtn");
const scanMeta = document.getElementById("scanMeta");
const refreshBtn = document.getElementById("refreshBtn");
const disabledRefreshCard = document.getElementById("disabledRefreshCard");
const disabledRefreshMeta = document.getElementById("disabledRefreshMeta");
const disabledRefreshDetails = document.getElementById("disabledRefreshDetails");
const runDisabledRefreshBtn = document.getElementById("runDisabledRefreshBtn");
const downloadDisabledRefreshResultBtn = document.getElementById("downloadDisabledRefreshResultBtn");
const logoutBtn = document.getElementById("logoutBtn") || createNoopElement();
const detailRefreshQuotaQuickBtn = document.getElementById("detailRefreshQuotaQuickBtn") || createNoopElement();
const detailSettingsBtn = document.getElementById("detailSettingsBtn") || createNoopElement();
const detailEmpty = document.getElementById("detailEmpty") || createNoopElement();
const detailContent = document.getElementById("detailContent") || createNoopElement();
const detailSummaryType = document.getElementById("detailSummaryType") || createNoopElement();
const detailSummaryStatus = document.getElementById("detailSummaryStatus") || createNoopElement();
const detailSummaryName = document.getElementById("detailSummaryName") || createNoopElement();
const detailSummarySize = document.getElementById("detailSummarySize") || createNoopElement();
const detailSummaryModified = document.getElementById("detailSummaryModified") || createNoopElement();
const detailSummaryQuotaPlan = document.getElementById("detailSummaryQuotaPlan") || createNoopElement();
const detailSummaryQuotaStatus = document.getElementById("detailSummaryQuotaStatus") || createNoopElement();
const detailSummaryQuotaMeta = document.getElementById("detailSummaryQuotaMeta") || createNoopElement();
const detailList = document.getElementById("detailList");
const detailModal = document.getElementById("detailModal");
const detailModalTitle = document.getElementById("detailModalTitle");
const detailModalSubtitle = document.getElementById("detailModalSubtitle");
const detailModalCloseBtn = document.getElementById("detailModalCloseBtn");
const detailToggleStatusBtn = document.getElementById("detailToggleStatusBtn");
const detailRefreshQuotaBtn = document.getElementById("detailRefreshQuotaBtn");
const detailActionHint = document.getElementById("detailActionHint");
const quotaMeta = document.getElementById("quotaMeta");
const quotaEmpty = document.getElementById("quotaEmpty");
const quotaContent = document.getElementById("quotaContent");
const detailDangerZone = document.getElementById("detailDangerZone");
const detailDeleteBtn = document.getElementById("detailDeleteBtn");
const detailDangerTitle = detailDangerZone.querySelector("h3");
const detailDangerText = detailDangerZone.querySelector("p");
const metaForm = document.getElementById("metaForm");
const metaGroup = document.getElementById("metaGroup");
const metaOwner = document.getElementById("metaOwner");
const metaTags = document.getElementById("metaTags");
const metaNote = document.getElementById("metaNote");
const metaStarred = document.getElementById("metaStarred");
const saveMetaText = document.getElementById("saveMetaText");
const toast = document.getElementById("toast");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function showToast(message, type = "info") {
  toast.textContent = message;
  toast.className = `toast ${type}`;
  toast.classList.remove("hidden");
  window.clearTimeout(showToast._timer);
  showToast._timer = window.setTimeout(() => toast.classList.add("hidden"), 3200);
}

async function apiFetch(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  if (response.status === 401) {
    loginModal.classList.remove("hidden");
    if (!url.includes("/api/login")) {
      throw new Error("未登录");
    }
  }

  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(data.error || data.failed?.[0]?.reason || "请求失败");
  }
  return data;
}

function compactNumber(value) {
  if (value === null || value === undefined || value === "") return "-";
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value);
  if (num >= 1_000_000_000) return `${(num / 1_000_000_000).toFixed(1)}B`;
  if (num >= 1_000_000) return `${(num / 1_000_000).toFixed(1)}M`;
  if (num >= 10_000) return `${(num / 1_000).toFixed(1)}K`;
  return String(num);
}

function formatDate(value) {
  if (!value) return "-";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function formatBytes(value) {
  const num = Number(value);
  if (!Number.isFinite(num) || num <= 0) return "-";
  const units = ["B", "KB", "MB", "GB"];
  let size = num;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  const fixed = size >= 10 || index === 0 ? 2 : 2;
  return `${size.toFixed(fixed)} ${units[index]}`;
}

function formatProviderType(value) {
  const key = String(value || "").trim().toLowerCase();
  const map = {
    codex: "Codex",
    claude: "Claude",
    gemini: "Gemini",
    openai: "OpenAI",
    kimi: "Kimi",
    qwen: "Qwen",
    vertex: "Vertex",
  };
  return map[key] || (key ? key.charAt(0).toUpperCase() + key.slice(1) : "-");
}

function formatEnabledState(detail) {
  if (!detail) return "-";
  if (detail.status === "deactivated") return "已迁移停用";
  return detail.disabled ? "停用" : "启用";
}

function formatQuotaErrorLabel(value) {
  const key = String(value || "").trim();
  if (!key) return "";
  if (key === "usage_limit_reached") return "额度耗尽";
  if (key === "token_expired") return "Token 过期";
  return key;
}

function summarizeQuotaStatus(quota) {
  if (!quota) {
    return {
      planText: "-",
      statusText: "未刷新",
      metaText: "点击“刷新额度”后查看当前账号的额度状态。",
    };
  }

  const planText = quota.plan_type || "-";
  if (quota.status === "error" && quota.error) {
    const errorLabel = formatQuotaErrorLabel(quota.error.type);
    const metaParts = [quota.error.message || "额度请求失败"];
    if (quota.error.resets_at_iso) {
      metaParts.push(`重置时间：${formatDate(quota.error.resets_at_iso)}`);
    } else if (quota.error.resets_in_seconds) {
      metaParts.push(`重置倒计时：${formatDuration(quota.error.resets_in_seconds)}`);
    }
    return {
      planText,
      statusText: errorLabel || "请求失败",
      metaText: metaParts.join(" · "),
    };
  }

  const primaryWindow = quota.rate_limit?.primary_window || quota.code_review_rate_limit?.primary_window || null;
  const rateLimited = Boolean(
    quota.rate_limit?.limit_reached ||
      quota.code_review_rate_limit?.limit_reached ||
      quota.rate_limit?.allowed === false ||
      quota.code_review_rate_limit?.allowed === false
  );
  const statusText = rateLimited ? "已达上限" : "正常";
  const metaParts = [];
  if (primaryWindow?.remaining_percent !== undefined && primaryWindow?.remaining_percent !== null) {
    metaParts.push(`剩余：${formatPercent(primaryWindow.remaining_percent)}`);
  }
  if (primaryWindow?.reset_at_iso) {
    metaParts.push(`重置：${formatDate(primaryWindow.reset_at_iso)}`);
  }
  if (!metaParts.length && quota.refreshed_at) {
    metaParts.push(`已刷新：${formatDate(quota.refreshed_at)}`);
  }
  return {
    planText,
    statusText,
    metaText: metaParts.join(" · ") || "额度信息已刷新",
  };
}

function isDeletableAccount(account) {
  if (!account) return false;
  return account.status === "deactivated" || account.quota_error_type === "token_expired";
}

function isDisabledAccount(account) {
  if (!account) return false;
  return account.status === "disabled";
}

function isRefreshSelectableAccount(account) {
  if (!account) return false;
  if (account.status === "deactivated") return false;
  if (account.quota_error_type === "token_expired") return false;
  return Boolean(account.quota_refresh_supported);
}

function isBatchSelectableAccount(account) {
  if (!account) return false;
  return isRefreshSelectableAccount(account) || isDisabledAccount(account) || isDeletableAccount(account);
}

function isTokenRefreshSelectableAccount(account) {
  if (!account) return false;
  if (account.status === "deactivated") return false;
  return Boolean(account.has_refresh_token);
}

function selectedTokenRefreshableAccounts() {
  const keys = new Set(state.selectedKeys);
  return state.accounts.filter((item) => keys.has(item.key) && isTokenRefreshSelectableAccount(item));
}

function deleteScopeLabel(account) {
  if (!account) return "账号";
  if (account.status === "deactivated") return "迁移停用账号";
  if (account.quota_error_type === "token_expired") return "Token 过期账号";
  return "账号";
}

function formatPercent(value) {
  if (value === null || value === undefined || value === "") return "-";
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value);
  return `${num.toFixed(num % 1 === 0 ? 0 : 1)}%`;
}

function formatDuration(seconds) {
  const total = Number(seconds);
  if (!Number.isFinite(total) || total <= 0) return "-";
  if (total >= 86400) return `${(total / 86400).toFixed(total % 86400 === 0 ? 0 : 1)} 天`;
  if (total >= 3600) return `${(total / 3600).toFixed(total % 3600 === 0 ? 0 : 1)} 小时`;
  if (total >= 60) return `${(total / 60).toFixed(total % 60 === 0 ? 0 : 1)} 分钟`;
  return `${total} 秒`;
}

function quotaStatusBadge(bucket) {
  if (!bucket) {
    return `<span class="badge subtle">暂无数据</span>`;
  }
  if (!bucket.allowed || bucket.limit_reached) {
    return `<span class="badge status-disabled">已触达限制</span>`;
  }
  return `<span class="badge status-active">可用</span>`;
}

function renderQuotaWindow(windowLabel, window) {
  if (!window) {
    return `
      <div class="quota-window-item">
        <strong>${escapeHtml(windowLabel)}</strong>
        <p>暂无窗口信息</p>
      </div>
    `;
  }

  return `
    <div class="quota-window-item">
      <strong>${escapeHtml(windowLabel)}</strong>
      <p>已使用：${escapeHtml(formatPercent(window.used_percent))}</p>
      <p>剩余：${escapeHtml(formatPercent(window.remaining_percent))}</p>
      <p>窗口周期：${escapeHtml(formatDuration(window.limit_window_seconds))}</p>
      <p>重置倒计时：${escapeHtml(formatDuration(window.reset_after_seconds))}</p>
      <p>重置时间：${escapeHtml(formatDate(window.reset_at_iso))}</p>
    </div>
  `;
}

function renderQuotaBucket(bucket) {
  if (!bucket) return "";
  return `
    <article class="quota-card">
      <div class="quota-card-header">
        <div>
          <div class="quota-card-title">${escapeHtml(bucket.label || bucket.key || "额度")}</div>
          <div class="quota-card-subtitle">${escapeHtml(bucket.key || "-")}</div>
        </div>
        ${quotaStatusBadge(bucket)}
      </div>
      <div class="quota-window-grid">
        ${renderQuotaWindow("主窗口", bucket.primary_window)}
        ${renderQuotaWindow("次窗口", bucket.secondary_window)}
      </div>
    </article>
  `;
}

function renderQuotaError(error) {
  if (!error) return "";
  return `
    <article class="quota-card quota-card-error">
      <div class="quota-card-header">
        <div>
          <div class="quota-card-title">额度状态错误</div>
          <div class="quota-card-subtitle">${escapeHtml(error.type || "request_failed")}</div>
        </div>
        <span class="badge status-disabled">已缓存错误状态</span>
      </div>
      <div class="quota-window-grid">
        <div class="quota-window-item">
          <strong>错误信息</strong>
          <p>${escapeHtml(error.message || "-")}</p>
        </div>
        <div class="quota-window-item">
          <strong>重置信息</strong>
          <p>重置倒计时：${escapeHtml(formatDuration(error.resets_in_seconds))}</p>
          <p>重置时间：${escapeHtml(formatDate(error.resets_at_iso))}</p>
          <p>套餐：${escapeHtml(error.plan_type || "-")}</p>
        </div>
      </div>
    </article>
  `;
}

function renderQuota(quota) {
  if (!quota) {
    quotaMeta.textContent = "点击“刷新额度”后查看当前账号的额度窗口。";
    quotaEmpty.classList.remove("hidden");
    quotaContent.classList.add("hidden");
    quotaContent.innerHTML = "";
    return;
  }

  const cards = [
    renderQuotaError(quota.error),
    renderQuotaBucket(quota.rate_limit),
    renderQuotaBucket(quota.code_review_rate_limit),
    ...(quota.additional_rate_limits || []).map((item) => renderQuotaBucket(item)),
  ].filter(Boolean);

  const chips = [];
  if (quota.plan_type) {
    chips.push(`<span class="quota-summary-chip">套餐：${escapeHtml(quota.plan_type)}</span>`);
  }
  if (quota.email) {
    chips.push(`<span class="quota-summary-chip">额度账号：${escapeHtml(quota.email)}</span>`);
  }
  if (quota.status === "error" && quota.error?.type) {
    chips.push(`<span class="quota-summary-chip">错误类型：${escapeHtml(quota.error.type)}</span>`);
  }

  quotaMeta.textContent = quota.refreshed_at
    ? `上次刷新：${formatDate(quota.refreshed_at)}${quota.status === "error" ? " · 已缓存额度错误状态" : ""}`
    : "额度已加载";
  quotaEmpty.classList.add("hidden");
  quotaContent.classList.remove("hidden");
  quotaContent.innerHTML = `
    <div class="quota-topline">
      ${chips.join("")}
    </div>
    ${cards.join("") || `<div class="empty-state quota-empty">额度接口未返回窗口数据</div>`}
  `;
}

function openDetailModal() {
  if (!state.selectedAccount) return;
  detailModal.classList.remove("hidden");
}

function closeDetailModal() {
  detailModal.classList.add("hidden");
}

function setActiveTab(tab) {
  state.activeTab = tab === "disabled-refresh" ? "disabled-refresh" : "accounts";
  const accountActive = state.activeTab === "accounts";
  accountListTab.classList.toggle("active", accountActive);
  disabledRefreshTab.classList.toggle("active", !accountActive);
  accountListPanel.classList.toggle("hidden", !accountActive);
  disabledRefreshPanel.classList.toggle("hidden", accountActive);
  accountListPanel.classList.toggle("active", accountActive);
  disabledRefreshPanel.classList.toggle("active", !accountActive);
}

function selectableAccountsOnPage() {
  return state.accounts.filter((item) => isBatchSelectableAccount(item));
}

function deletableAccountsOnPage() {
  return state.accounts.filter((item) => isDeletableAccount(item));
}

function disabledAccountsOnPage() {
  return state.accounts.filter((item) => isDisabledAccount(item));
}

function refreshSelectableAccountsOnPage() {
  return state.accounts.filter((item) => isRefreshSelectableAccount(item));
}

function syncSelectionWithCurrentPage() {
  const availableKeys = new Set(selectableAccountsOnPage().map((item) => item.key));
  state.selectedKeys = new Set([...state.selectedKeys].filter((key) => availableKeys.has(key)));
}

function updateSelectionUI() {
  const selectableItems = selectableAccountsOnPage();
  const selectedOnPage = selectableItems.filter((item) => state.selectedKeys.has(item.key));
  const selectedRefreshableCount = state.accounts.filter((item) => state.selectedKeys.has(item.key) && isRefreshSelectableAccount(item)).length;
  const selectedDisabledCount = state.accounts.filter((item) => state.selectedKeys.has(item.key) && isDisabledAccount(item)).length;
  const selectedDeletableCount = state.accounts.filter((item) => state.selectedKeys.has(item.key) && isDeletableAccount(item)).length;
  const selectedTokenRefreshCount = state.accounts.filter((item) => state.selectedKeys.has(item.key) && isTokenRefreshSelectableAccount(item)).length;
  const totalDeactivated = Number(state.overview?.summary?.status_counts?.deactivated || 0);
  selectedMeta.textContent = `已选 ${state.selectedKeys.size} 个账号（可刷新 ${selectedRefreshableCount} / 禁用 ${selectedDisabledCount} / 可删除 ${selectedDeletableCount} / Token刷新 ${selectedTokenRefreshCount}）`;
  clearSelectionBtn.disabled = state.selectedKeys.size === 0;
  batchEnableBtn.disabled = selectedDisabledCount === 0;
  batchRefreshRecoverBtn.disabled = selectedRefreshableCount === 0;
  batchTokenRefreshBtn.disabled = selectedTokenRefreshCount === 0;
  batchDeleteBtn.disabled = selectedDeletableCount === 0;
  deleteAllDeactivatedBtn.disabled = totalDeactivated === 0;
  selectAllDeactivated.disabled = selectableItems.length === 0;
  selectAllDeactivated.checked = selectableItems.length > 0 && selectedOnPage.length === selectableItems.length;
  selectAllDeactivated.indeterminate = selectedOnPage.length > 0 && selectedOnPage.length < selectableItems.length;
}

function renderDisabledRefreshSampleSection(title, items, emptyText, detailBuilder) {
  const safeItems = Array.isArray(items) ? items : [];
  const content = safeItems.length
    ? `<ul class="disabled-refresh-sample-list">${safeItems
        .map((item) => `<li class="disabled-refresh-sample-item">${detailBuilder(item || {})}</li>`)
        .join("")}</ul>`
    : `<p class="disabled-refresh-sample-empty">${escapeHtml(emptyText)}</p>`;
  return `
    <section class="disabled-refresh-sample-card">
      <h3>${escapeHtml(title)}</h3>
      ${content}
    </section>
  `;
}

function renderDisabledRefresh() {
  const worker = state.overview?.disabled_refresh || null;
  if (!worker || !disabledRefreshCard) return;
  if (!worker.enabled) {
    disabledRefreshMeta.textContent = "disabled refresh worker 未启用。";
    disabledRefreshDetails.className = "empty-state";
    disabledRefreshDetails.textContent = "当前环境没有启用 disabled refresh。";
    runDisabledRefreshBtn.disabled = true;
    downloadDisabledRefreshResultBtn.disabled = true;
    return;
  }

  const summary = worker.summary || {};
  const blockedByType = summary.blocked_by_type || {};
  const blockedTypeText = Object.entries(blockedByType)
    .map(([key, count]) => `${key}: ${count}`)
    .join(" · ");
  const runningText = worker.running ? "正在运行中" : "空闲";
  const lastTrigger = summary.trigger || (worker.last_result ? "scheduled" : "-");
  disabledRefreshMeta.textContent = [
    `状态：${runningText}`,
    `上次开始：${formatDate(worker.last_started_at)}`,
    `上次结束：${formatDate(worker.last_finished_at)}`,
    `下次执行：${formatDate(worker.next_run_at)}`,
    worker.last_error ? `错误：${worker.last_error}` : "最近无错误",
  ].join(" · ");

  if (!worker.last_result) {
    disabledRefreshDetails.className = "empty-state";
    disabledRefreshDetails.textContent = "当前还没有 disabled refresh 结果。";
    runDisabledRefreshBtn.disabled = false;
    downloadDisabledRefreshResultBtn.disabled = true;
    return;
  }

  disabledRefreshDetails.className = "disabled-refresh-details";
  disabledRefreshDetails.innerHTML = `
    <div class="disabled-refresh-grid">
      <div><strong>选择模式</strong><p>${escapeHtml(summary.selection_mode || "-")}</p></div>
      <div><strong>触发来源</strong><p>${escapeHtml(lastTrigger)}</p></div>
      <div><strong>手动禁用标记</strong><p>${escapeHtml(summary.manual_disabled_total ?? "-")}</p></div>
      <div><strong>候选 disabled</strong><p>${escapeHtml(summary.eligible_disabled_total ?? "-")}</p></div>
      <div><strong>本轮选中</strong><p>${escapeHtml(summary.selected_count ?? "-")}</p></div>
      <div><strong>本轮检查</strong><p>${escapeHtml(summary.checked_count ?? "-")}</p></div>
      <div><strong>最近恢复</strong><p>${escapeHtml(summary.recovered_count ?? "-")}</p></div>
      <div><strong>触达限制</strong><p>${escapeHtml(summary.limited_count ?? "-")}</p></div>
      <div><strong>最近 blocked</strong><p>${escapeHtml(summary.blocked_count ?? "-")}</p></div>
      <div><strong>最近 skipped</strong><p>${escapeHtml(summary.skipped_count ?? "-")}</p></div>
      <div><strong>最近 failed</strong><p>${escapeHtml(summary.failed_count ?? "-")}</p></div>
      <div><strong>阻塞分布</strong><p>${escapeHtml(blockedTypeText || "-")}</p></div>
    </div>
    <div class="disabled-refresh-sample-grid">
      ${renderDisabledRefreshSampleSection(
        "最近 blocked 样本",
        summary.blocked_samples,
        "最近没有 blocked 样本。",
        (item) => `${escapeHtml(item.email || "-")} · ${escapeHtml(item.type || "unknown")} · ${escapeHtml(item.message || "-")}`
      )}
      ${renderDisabledRefreshSampleSection(
        "最近 skipped 样本",
        summary.skipped_samples,
        "最近没有 skipped 样本。",
        (item) => `${escapeHtml(item.email || "-")} · ${escapeHtml(item.reason || "-")}`
      )}
      ${renderDisabledRefreshSampleSection(
        "最近 failed 样本",
        summary.failed_samples,
        "最近没有 failed 样本。",
        (item) => `${escapeHtml(item.email || "-")} · ${escapeHtml(item.reason || "-")}`
      )}
    </div>
  `;
  runDisabledRefreshBtn.disabled = Boolean(worker.running);
  downloadDisabledRefreshResultBtn.disabled = false;
}

function renderSummary() {
  if (!state.overview) return;
  const summary = state.overview.summary || {};
  const interfaceStatus = summary.management_connected
    ? `接口状态 ${summary.management_auth_status || "ok"}`
    : `接口状态 ${summary.management_error || "未连接"}`;
  const lastScanText = `最近扫描 ${formatDate(summary.last_scan_at)}`;
  const durationText = `扫描耗时 ${summary.scan_duration_ms ?? "-"} ms`;
  scanMeta.textContent = `${lastScanText} · ${durationText} · ${interfaceStatus}`;
  downloadDisabledUnrefreshedBtn.disabled = Number(summary.disabled_unrefreshed_total || 0) === 0;
}

function renderSelectOptions() {
  const statusOptions = state.overview?.status_options || [];
  statusSelect.innerHTML = `<option value="">全部状态</option>${statusOptions
    .map(
      (item) =>
        `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}${Number.isFinite(Number(item.count)) ? ` (${item.count})` : ""}</option>`
    )
    .join("")}`;
  statusSelect.value = state.filters.status;

  const quotaErrorOptions = state.overview?.quota_error_options || [];
  const mergedQuotaOptions = [
    { value: "usage_limit_reached", label: "usage_limit_reached", count: (state.overview?.summary?.quota_error_counts || {}).usage_limit_reached || 0 },
    { value: "token_expired", label: "token_expired", count: (state.overview?.summary?.quota_error_counts || {}).token_expired || 0 },
    ...quotaErrorOptions.filter((item) => !["usage_limit_reached", "token_expired"].includes(item.value)),
  ];
  quotaErrorSelect.innerHTML = `<option value="">全部额度状态</option>${mergedQuotaOptions
    .map((item) => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}${Number.isFinite(Number(item.count)) ? ` (${item.count})` : ""}</option>`)
    .join("")}`;
  quotaErrorSelect.value = state.filters.quotaErrorType;

  const groups = state.overview?.groups || [];
  groupSelect.innerHTML = `<option value="">全部分组</option>${groups
    .map((item) => `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)} (${item.count})</option>`)
    .join("")}`;
  groupSelect.value = state.filters.group;
}

function renderDetailSection(title, rows) {
  const safeRows = Array.isArray(rows) ? rows.filter((item) => Array.isArray(item) && item.length >= 2) : [];
  return `
    <section class="detail-section">
      <h3>${escapeHtml(title)}</h3>
      <div class="detail-section-grid">
        ${safeRows
          .map(
            ([label, value]) => `
              <div class="detail-row">
                <dt>${escapeHtml(label)}</dt>
                <dd>${escapeHtml(value)}</dd>
              </div>
            `
          )
          .join("")}
      </div>
    </section>
  `;
}

function renderAccounts() {
  syncSelectionWithCurrentPage();

  if (!state.accounts.length) {
    accountsTbody.innerHTML = `<tr><td colspan="8" class="empty-cell">没有符合条件的账号</td></tr>`;
  } else {
    accountsTbody.innerHTML = state.accounts
      .map((item) => {
        const selectable = isBatchSelectableAccount(item);
        const accountName = item.name ? `${item.email} · ${item.name}` : item.email;
        const tagsText = (item.tags || []).join(", ");
        const canRefresh = Boolean(item.quota_refresh_supported);
        const canToggle = Boolean(item.status_toggle_supported);
        const canTokenRefresh = Boolean(item.has_refresh_token);
        const toggleLabel = item.disabled ? "启用" : "停用";
        return `
          <tr class="table-row ${state.selectedAccount?.key === item.key ? "selected" : ""}" data-key="${escapeHtml(item.key)}">
            <td class="checkbox-cell">
              <input
                class="row-select"
                type="checkbox"
                data-key="${escapeHtml(item.key)}"
                ${selectable ? "" : "disabled"}
                ${state.selectedKeys.has(item.key) ? "checked" : ""}
              />
            </td>
            <td>
              <div class="cell-with-subtag">
                <span class="status-pill status-${escapeHtml(item.status)}">${escapeHtml(item.status_label)}</span>
                ${
                  item.quota_error_type
                    ? `<span class="quota-error-inline quota-error-inline-${escapeHtml(item.quota_error_type)}">${escapeHtml(
                        formatQuotaErrorLabel(item.quota_error_type)
                      )}</span>`
                    : ""
                }
                ${item.manual_disabled ? `<span class="quota-error-inline">手动禁用标记</span>` : ""}
              </div>
            </td>
            <td>
              <div class="cell-with-subtag">
                <strong>${escapeHtml(accountName)}</strong>
                <span class="table-secondary">${escapeHtml(item.starred ? "星标账号" : item.key || "-")}</span>
              </div>
            </td>
            <td>
              <div class="cell-with-subtag">
                <strong>${escapeHtml(item.group || "未分组")}</strong>
                <span class="table-secondary">${escapeHtml(tagsText || "无标签")}</span>
              </div>
            </td>
            <td>${escapeHtml(item.plan_type || "-")}</td>
            <td>${escapeHtml(formatDate(item.expires_at))}</td>
            <td>${escapeHtml(formatDate(item.last_refresh))}</td>
            <td>
              <div class="row-actions">
                <button type="button" class="text-button row-action-btn row-action-settings" data-key="${escapeHtml(item.key)}">详情</button>
                <button type="button" class="text-button row-action-btn row-action-token-refresh" data-key="${escapeHtml(item.key)}" ${canTokenRefresh ? "" : "disabled"}>刷新Token</button>
                <button type="button" class="text-button row-action-btn row-action-refresh" data-key="${escapeHtml(item.key)}" ${canRefresh ? "" : "disabled"}>刷新额度</button>
                <button type="button" class="text-button row-action-btn row-action-toggle" data-key="${escapeHtml(item.key)}" ${canToggle ? "" : "disabled"}>${escapeHtml(toggleLabel)}</button>
                <button type="button" class="text-button row-action-btn row-action-delete" data-key="${escapeHtml(item.key)}" ${isDeletableAccount(item) ? "" : "disabled"}>删除</button>
              </div>
            </td>
          </tr>
        `;
      })
      .join("");
  }

  resultsMeta.textContent = `共 ${state.total} 条，当前第 ${state.page} / ${state.totalPages} 页`;
  pageInfo.textContent = `第 ${state.page} / ${state.totalPages} 页`;
  prevPageBtn.disabled = state.page <= 1;
  nextPageBtn.disabled = state.page >= state.totalPages;
  updateSelectionUI();

  document.querySelectorAll(".table-row").forEach((row) => {
    row.addEventListener("click", async (event) => {
      if (event.target.closest("button") || event.target.closest("input")) {
        return;
      }
      const key = row.dataset.key;
      await loadAccountDetail(key);
    });
  });

  document.querySelectorAll(".row-select").forEach((input) => {
    input.addEventListener("click", (event) => event.stopPropagation());
    input.addEventListener("change", (event) => {
      const key = event.target.dataset.key;
      if (!key) return;
      if (event.target.checked) {
        state.selectedKeys.add(key);
      } else {
        state.selectedKeys.delete(key);
      }
      updateSelectionUI();
    });
  });

  document.querySelectorAll(".row-action-settings").forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      const key = event.currentTarget.dataset.key;
      await loadAccountDetail(key);
      openDetailModal();
    });
  });

  document.querySelectorAll(".row-action-refresh").forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      const key = event.currentTarget.dataset.key;
      await loadAccountDetail(key);
      await refreshSelectedAccountQuota();
    });
  });

  document.querySelectorAll(".row-action-token-refresh").forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      const key = event.currentTarget.dataset.key;
      await singleTokenRefresh(key);
    });
  });

  document.querySelectorAll(".row-action-toggle").forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      const key = event.currentTarget.dataset.key;
      await loadAccountDetail(key);
      await toggleSelectedAccountStatus();
    });
  });

  document.querySelectorAll(".row-action-delete").forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      const key = event.currentTarget.dataset.key;
      await deleteSingleAccount(key);
    });
  });
}

function renderDetail(detail) {
  if (!detail) {
    closeDetailModal();
    detailEmpty.classList.remove("hidden");
    detailContent.classList.add("hidden");
    detailRefreshQuotaQuickBtn.classList.add("hidden");
    detailRefreshQuotaQuickBtn.disabled = true;
    detailRefreshQuotaQuickBtn.dataset.key = "";
    detailSettingsBtn.classList.add("hidden");
    detailSettingsBtn.disabled = true;
    detailSettingsBtn.dataset.key = "";
    detailSummaryType.textContent = "-";
    detailSummaryStatus.textContent = "-";
    detailSummaryName.textContent = "-";
    detailSummarySize.textContent = "-";
    detailSummaryModified.textContent = "-";
    detailSummaryQuotaPlan.textContent = "-";
    detailSummaryQuotaStatus.textContent = "未刷新";
    detailSummaryQuotaMeta.textContent = "点击“刷新额度”后查看当前账号的额度状态。";
    detailModalTitle.textContent = "认证文件详情 / 编辑";
    detailModalSubtitle.textContent = "在这里查看认证文件详情、切换启用状态，并手动刷新额度。";
    detailToggleStatusBtn.disabled = true;
    detailRefreshQuotaBtn.disabled = true;
    detailToggleStatusBtn.textContent = "切换状态";
    detailActionHint.textContent = "请选择一个账号后进行操作。";
    quotaMeta.textContent = "点击“刷新额度”后查看当前账号的额度窗口。";
    detailList.innerHTML = "";
    renderQuota(null);
    detailDangerZone.classList.add("hidden");
    detailDeleteBtn.dataset.key = "";
    detailDeleteBtn.textContent = "删除这个账号";
    detailDangerTitle.textContent = "危险操作";
    detailDangerText.textContent = "删除前会先做本地备份。";
    return;
  }

  detailEmpty.classList.add("hidden");
  detailContent.classList.remove("hidden");
  detailRefreshQuotaQuickBtn.classList.remove("hidden");
  detailSettingsBtn.classList.remove("hidden");
  detailRefreshQuotaQuickBtn.dataset.key = detail.key;
  detailSettingsBtn.disabled = false;
  detailRefreshQuotaQuickBtn.disabled = false;
  detailSettingsBtn.dataset.key = detail.key;
  detailDangerZone.classList.toggle("hidden", !isDeletableAccount(detail));
  detailDeleteBtn.dataset.key = detail.key;
  if (detail.status === "deactivated") {
    detailDangerTitle.textContent = "删除迁移停用账号";
    detailDangerText.textContent = "该账号会从停用归档目录删除，删除前会先做本地备份。";
    detailDeleteBtn.textContent = "删除这个迁移停用账号";
  } else if (detail.quota_error_type === "token_expired") {
    detailDangerTitle.textContent = "删除 Token 过期账号";
    detailDangerText.textContent = "该账号会从主认证目录删除，删除前会先做本地备份。";
    detailDeleteBtn.textContent = "删除这个 Token 过期账号";
  } else {
    detailDangerTitle.textContent = "危险操作";
    detailDangerText.textContent = "删除前会先做本地备份。";
    detailDeleteBtn.textContent = "删除这个账号";
  }
  detailToggleStatusBtn.dataset.key = detail.key;
  detailRefreshQuotaBtn.dataset.key = detail.key;
  detailSummaryType.textContent = formatProviderType(detail.type);
  detailSummaryStatus.textContent = formatEnabledState(detail);
  detailSummaryName.textContent = detail.file_display_name || detail.source_file || detail.email || "-";
  detailSummarySize.textContent = formatBytes(detail.file_size_bytes);
  detailSummaryModified.textContent = formatDate(detail.modified_at);
  const quotaSummary = summarizeQuotaStatus(detail.quota);
  detailSummaryQuotaPlan.textContent = quotaSummary.planText;
  detailSummaryQuotaStatus.textContent = quotaSummary.statusText;
  detailSummaryQuotaMeta.textContent = quotaSummary.metaText;
  detailModalTitle.textContent = `认证文件详情 / 编辑 - ${detail.file_display_name || detail.source_file || detail.email || detail.key}`;
  detailModalSubtitle.textContent = `${formatProviderType(detail.type)} · ${formatEnabledState(detail)} · ${
    detail.file_display_name || detail.source_file || "-"
  }`;

  const canToggleStatus = Boolean(detail.status_toggle_supported);
  const canRefreshQuota = Boolean(detail.quota_refresh_supported);
  detailToggleStatusBtn.disabled = !canToggleStatus;
  detailRefreshQuotaBtn.disabled = !canRefreshQuota;
  detailRefreshQuotaQuickBtn.disabled = !canRefreshQuota;
  detailToggleStatusBtn.textContent = detail.disabled ? "启用账号" : "停用账号";

  if (detail.status === "deactivated") {
    detailActionHint.textContent = "已迁移停用账号仅支持删除归档文件，不支持启用/停用或额度刷新。";
  } else if (detail.quota_error_type === "token_expired") {
    detailActionHint.textContent = "该账号已缓存 Token 过期状态；你可以删除认证文件，或先尝试刷新额度确认状态。";
  } else if (!canToggleStatus && !canRefreshQuota) {
    detailActionHint.textContent = "当前账号没有可用的管理接口上下文，暂时无法切换状态或刷新额度。";
  } else if (!canRefreshQuota) {
    detailActionHint.textContent = "可切换账号启用状态；当前账号缺少 auth_index 或 chatgpt_account_id，暂时无法刷新额度。";
  } else {
    detailActionHint.textContent = "你可以手动启用/停用账号，并刷新额度后查看最新窗口。";
  }

  const identityRows = [
    ["基础信息", detail.email || "-"],
    ["名称", detail.name || "-"],
    ["文件名", detail.file_display_name || detail.source_file || "-"],
    ["类型", formatProviderType(detail.type)],
    ["套餐", detail.plan_type || "-"],
  ];
  const lifecycleRows = [
    ["当前状态", detail.status_label || "-"],
    ["启用状态", formatEnabledState(detail)],
    ["到期时间", formatDate(detail.expires_at)],
    ["最后刷新", formatDate(detail.last_refresh)],
    ["剩余天数", detail.days_remaining ?? "-"],
  ];
  const trafficRows = [
    ["额度缓存状态", detail.quota_state === "error" ? `错误 / ${detail.quota_error_type || "-"}` : detail.quota_state === "ok" ? "已刷新" : "-"],
    ["最近恢复", formatDate(detail.quota_updated_at)],
    ["请求总数", compactNumber(detail.request_count || 0)],
    ["失败次数", compactNumber(detail.failure_count || 0)],
    ["累计 Token", compactNumber(detail.total_tokens || 0)],
    ["最近请求", formatDate(detail.last_request_at)],
    ["最近模型", (detail.models || []).join(", ") || "-"],
  ];
  const opsRows = [
    ["业务分组", detail.group || "-"],
    ["负责人", detail.owner || "-"],
    ["标签", (detail.tags || []).join(", ") || "-"],
    ["备注", detail.note || "-"],
    ["手动禁用标记", detail.manual_disabled ? "是" : "否"],
    ["状态说明", detail.status_message || "-"],
  ];

  detailList.innerHTML = [
    renderDetailSection("基础信息", identityRows),
    renderDetailSection("生命周期", lifecycleRows),
    renderDetailSection("流量 / 额度", trafficRows),
    renderDetailSection("运营信息", opsRows),
  ].join("");

  metaGroup.value = detail.group || "";
  metaOwner.value = detail.owner || "";
  metaTags.value = (detail.tags || []).join(", ");
  metaNote.value = detail.note || "";
  metaStarred.checked = Boolean(detail.starred);
  saveMetaText.textContent = detail.updated_at ? `上次修改：${formatDate(detail.updated_at)}` : "";
  renderQuota(detail.quota || null);
}

async function loadOverview(force = false) {
  const url = force ? "/api/refresh" : "/api/overview";
  state.overview = await apiFetch(url, {
    method: force ? "POST" : "GET",
  });
  renderSummary();
  renderDisabledRefresh();
  renderSelectOptions();
}

async function loadAccounts({ preserveSelection = false } = {}) {
  const params = new URLSearchParams({
    q: state.filters.q,
    status: state.filters.status,
    quota_error_type: state.filters.quotaErrorType,
    group: state.filters.group,
    page: String(state.page),
    page_size: String(state.pageSize),
  });
  if (state.sort.sortBy) {
    params.set("sort_by", state.sort.sortBy);
    params.set("sort_order", state.sort.sortOrder);
  }
  const data = await apiFetch(`/api/accounts?${params.toString()}`);
  state.accounts = data.items || [];
  state.total = data.total || 0;
  state.totalPages = data.total_pages || 1;
  if (!preserveSelection) {
    state.selectedKeys = new Set();
  }
  renderAccounts();
  updateSortIndicators();
}

async function loadAccountDetail(key) {
  state.selectedAccount = await apiFetch(`/api/accounts/${encodeURIComponent(key)}`);
  renderDetail(state.selectedAccount);
  renderAccounts();
}

async function applyUpdatedAccountDetail(updated, successMessage) {
  state.selectedAccount = updated;
  renderDetail(updated);
  await loadOverview();
  await loadAccounts({ preserveSelection: true });
  if (successMessage) {
    showToast(successMessage, "success");
  }
}

async function toggleSelectedAccountStatus() {
  const detail = state.selectedAccount;
  if (!detail) {
    showToast("请先选择一个账号", "error");
    return;
  }
  if (!detail.status_toggle_supported) {
    showToast("当前账号不支持切换启用状态", "error");
    return;
  }

  const nextDisabled = !detail.disabled;
  const actionLabel = nextDisabled ? "停用" : "启用";
  const confirmed = window.confirm(`确认${actionLabel}账号 ${detail.email} 吗？`);
  if (!confirmed) return;

  detailToggleStatusBtn.disabled = true;
  try {
    const updated = await apiFetch(`/api/accounts/${encodeURIComponent(detail.key)}/status`, {
      method: "POST",
      body: JSON.stringify({ disabled: nextDisabled }),
    });
    await applyUpdatedAccountDetail(updated, `${actionLabel}成功`);
  } catch (error) {
    showToast(error.message || `${actionLabel}失败`, "error");
  } finally {
    detailToggleStatusBtn.disabled = false;
  }
}

async function refreshSelectedAccountQuota() {
  const detail = state.selectedAccount;
  if (!detail) {
    showToast("请先选择一个账号", "error");
    return;
  }
  if (!detail.quota_refresh_supported) {
    showToast("当前账号缺少额度刷新所需信息", "error");
    return;
  }

  detailRefreshQuotaBtn.disabled = true;
  detailRefreshQuotaQuickBtn.disabled = true;
  try {
    const updated = await apiFetch(`/api/accounts/${encodeURIComponent(detail.key)}/quota-refresh`, {
      method: "POST",
    });
    const refreshResult = updated.quota_refresh_result || null;
    await applyUpdatedAccountDetail(updated);
    if (refreshResult?.ok === false) {
      showToast(`额度状态已更新：${refreshResult.message || refreshResult.type || "请求失败"}`, "info");
      return;
    }
    showToast(refreshResult?.message || "额度刷新成功", "success");
  } catch (error) {
    showToast(error.message || "额度刷新失败", "error");
  } finally {
    detailRefreshQuotaBtn.disabled = false;
    detailRefreshQuotaQuickBtn.disabled = false;
  }
}

function selectedDisabledAccounts() {
  const keys = new Set(state.selectedKeys);
  return state.accounts.filter((item) => keys.has(item.key) && isDisabledAccount(item));
}

function selectedRefreshableAccounts() {
  const keys = new Set(state.selectedKeys);
  return state.accounts.filter((item) => keys.has(item.key) && isRefreshSelectableAccount(item));
}

function selectedDeletableAccounts() {
  const keys = new Set(state.selectedKeys);
  return state.accounts.filter((item) => keys.has(item.key) && isDeletableAccount(item));
}

async function reloadAfterBatchAction(affectedKeys = []) {
  const selectedKey = state.selectedAccount?.key || "";
  const shouldReloadDetail = Boolean(selectedKey && affectedKeys.includes(selectedKey));
  await loadOverview();
  await loadAccounts();
  if (!shouldReloadDetail) {
    return;
  }
  const updated = state.accounts.find((item) => item.key === selectedKey);
  if (updated) {
    await loadAccountDetail(selectedKey);
  } else {
    state.selectedAccount = null;
    renderDetail(null);
  }
}

function buildBatchEnableConfirmMessage(accounts) {
  const preview = accounts.slice(0, 6).map((item) => `- ${item.email}`).join("\n");
  const moreText = accounts.length > 6 ? `\n- ...另有 ${accounts.length - 6} 个账号` : "";
  return `确认批量启用 ${accounts.length} 个禁用账号吗？\n\n${preview}${moreText}`;
}

function buildBatchRefreshRecoverConfirmMessage(accounts) {
  const preview = accounts.slice(0, 6).map((item) => `- ${item.email}`).join("\n");
  const moreText = accounts.length > 6 ? `\n- ...另有 ${accounts.length - 6} 个账号` : "";
  return `确认批量检查 ${accounts.length} 个账号的 Limit 状态吗？\n\n${preview}${moreText}\n\n系统会先刷新这些账号的额度状态；如果账号已禁用且所有限流已重置，就会自动恢复启用。`;
}

async function enableSelectedDisabledAccounts() {
  const accounts = selectedDisabledAccounts();
  if (!accounts.length) {
    showToast("请先勾选要启用的禁用账号", "error");
    return;
  }
  const confirmed = window.confirm(buildBatchEnableConfirmMessage(accounts));
  if (!confirmed) return;

  batchEnableBtn.disabled = true;
  try {
    const result = await apiFetch("/api/accounts/batch-status", {
      method: "POST",
      body: JSON.stringify({ keys: accounts.map((item) => item.key), disabled: false }),
    });
    state.selectedKeys = new Set();
    await reloadAfterBatchAction(accounts.map((item) => item.key));
    const updatedCount = Number(result.updated_count || 0);
    const skippedCount = Number(result.skipped_count || 0);
    const failedCount = Number(result.failed_count || 0);
    if (updatedCount > 0) {
      showToast(`批量启用完成：成功 ${updatedCount} 个，跳过 ${skippedCount} 个，失败 ${failedCount} 个`, "success");
      return;
    }
    throw new Error(result.failed?.[0]?.reason || result.skipped?.[0]?.reason || "批量启用失败");
  } catch (error) {
    showToast(error.message || "批量启用失败", "error");
  } finally {
    batchEnableBtn.disabled = false;
  }
}

async function refreshRecoverSelectedDisabledAccounts() {
  const accounts = selectedRefreshableAccounts();
  if (!accounts.length) {
    showToast("请先勾选可批量刷新额度的账号（Token 过期账号不参与）", "error");
    return;
  }
  const confirmed = window.confirm(buildBatchRefreshRecoverConfirmMessage(accounts));
  if (!confirmed) return;

  batchRefreshRecoverBtn.disabled = true;
  try {
    const result = await apiFetch("/api/accounts/batch-refresh-recover", {
      method: "POST",
      body: JSON.stringify({ keys: accounts.map((item) => item.key) }),
    });
    state.selectedKeys = new Set();
    await reloadAfterBatchAction(accounts.map((item) => item.key));
    const recoveredCount = Number(result.recovered_count || 0);
    const limitedCount = Number(result.limited_count || 0);
    const blockedCount = Number(result.blocked_count || 0);
    const failedCount = Number(result.failed_count || 0);
    const skippedCount = Number(result.skipped_count || 0);
    showToast(
      `批量检查完成：恢复 ${recoveredCount} 个，仍 Limit ${limitedCount} 个，其它阻塞 ${blockedCount} 个，跳过 ${skippedCount} 个，失败 ${failedCount} 个`,
      recoveredCount > 0 ? "success" : "info"
    );
  } catch (error) {
    showToast(error.message || "批量检查失败", "error");
  } finally {
    batchRefreshRecoverBtn.disabled = false;
  }
}

function buildBatchTokenRefreshConfirmMessage(accounts) {
  const preview = accounts.slice(0, 6).map((item) => `- ${item.email}`).join("\n");
  const moreText = accounts.length > 6 ? `\n- ...另有 ${accounts.length - 6} 个账号` : "";
  return `确认批量刷新 ${accounts.length} 个账号的 Token 吗？\n\n${preview}${moreText}\n\n系统会通过 OpenAI OAuth 端点刷新 access_token 和 refresh_token，并更新本地文件记录。刷新失败的账号将被自动禁用。`;
}

async function singleTokenRefresh(key) {
  const account = state.accounts.find((item) => item.key === key);
  if (!account) {
    showToast("找不到该账号，请刷新页面重试", "error");
    return;
  }
  const email = account.email || key;
  const confirmed = window.confirm(`确认刷新 ${email} 的 Token 吗？\n\n将通过 OpenAI OAuth 端点刷新 access_token 和 refresh_token。刷新失败将自动禁用该账号。`);
  if (!confirmed) return;

  try {
    showToast(`正在刷新 ${email} 的 Token...`, "info");
    const result = await apiFetch("/api/accounts/batch-token-refresh", {
      method: "POST",
      body: JSON.stringify({ keys: [key] }),
    });
    const refreshedCount = Number(result.refreshed_count || 0);
    const failedCount = Number(result.failed_count || 0);
    if (refreshedCount > 0) {
      showToast(`${email} Token 刷新成功`, "success");
      // Fetch updated detail and open panel
      state.page = 1;
      await reloadAfterBatchAction([key]);
      let updated = state.accounts.find((item) => item.key === key);
      if (!updated) {
        updated = await apiFetch(`/api/accounts/${encodeURIComponent(key)}`);
      }
      if (updated) {
        state.selectedAccount = updated;
        renderDetail(updated);
        openDetailModal();
      }
    } else if (failedCount > 0) {
      showToast(`${email} Token 刷新失败，已自动禁用`, "error");
      await reloadAfterBatchAction([key]);
    } else {
      showToast(`${email} Token 刷新被跳过（无 refresh_token）`, "info");
    }
  } catch (error) {
    showToast(error.message || `刷新 ${email} Token 失败`, "error");
  }
}

async function tokenRefreshSelectedAccounts() {
  const accounts = selectedTokenRefreshableAccounts();
  if (!accounts.length) {
    showToast("请先勾选有 refresh_token 的账号", "error");
    return;
  }
  const confirmed = window.confirm(buildBatchTokenRefreshConfirmMessage(accounts));
  if (!confirmed) return;

  batchTokenRefreshBtn.disabled = true;
  try {
    const result = await apiFetch("/api/accounts/batch-token-refresh", {
      method: "POST",
      body: JSON.stringify({ keys: accounts.map((item) => item.key) }),
    });
    state.selectedKeys = new Set();
    // Token refresh changes expires_at / days_remaining, which affects sort order.
    // Reset to page 1 so refreshed accounts remain visible.
    state.page = 1;
    await reloadAfterBatchAction(accounts.map((item) => item.key));
    // If only one account was refreshed, auto-open its detail to show updated times
    if (accounts.length === 1 && Number(result.refreshed_count || 0) > 0) {
      let updated = state.accounts.find((item) => item.key === accounts[0].key);
      if (!updated) {
        // Account moved to a later page due to sort change — fetch detail directly
        updated = await apiFetch(`/api/accounts/${encodeURIComponent(accounts[0].key)}`);
      }
      if (updated) {
        state.selectedAccount = updated;
        renderDetail(updated);
      }
    }
    const refreshedCount = Number(result.refreshed_count || 0);
    const failedCount = Number(result.failed_count || 0);
    const skippedCount = Number(result.skipped_count || 0);
    showToast(
      `Token 刷新完成：成功 ${refreshedCount} 个，失败 ${failedCount} 个（已自动禁用），跳过 ${skippedCount} 个`,
      refreshedCount > 0 ? "success" : "info"
    );
  } catch (error) {
    showToast(error.message || "批量刷新 Token 失败", "error");
  } finally {
    batchTokenRefreshBtn.disabled = false;
  }
}

function buildDeleteConfirmMessage(accounts) {
  const preview = accounts.slice(0, 6).map((item) => `- ${item.email}`).join("\n");
  const moreText = accounts.length > 6 ? `\n- ...另有 ${accounts.length - 6} 个账号` : "";
  return `确认删除 ${accounts.length} 个可删除账号吗？\n\n${preview}${moreText}\n\n迁移停用账号会从归档目录删除；Token 过期账号会从主认证目录删除。删除前会先做本地备份。`;
}

async function applyDeleteResult(result, actionLabel) {
  const deletedKeys = new Set((result.deleted || []).map((item) => item.key));
  deletedKeys.forEach((key) => state.selectedKeys.delete(key));
  if (state.selectedAccount && deletedKeys.has(state.selectedAccount.key)) {
    state.selectedAccount = null;
    renderDetail(null);
  }

  await loadOverview();
  await loadAccounts();

  const deletedCount = result.deleted_count || 0;
  const failedCount = result.failed_count || 0;
  if (deletedCount === 0 && failedCount === 0 && Number(result.available_count || 0) === 0) {
    showToast("当前没有可删除的迁移停用账号");
    return;
  }
  if (deletedCount > 0 && failedCount > 0) {
    showToast(`${actionLabel}完成：成功删除 ${deletedCount} 个，失败 ${failedCount} 个`, "success");
    return;
  }
  if (deletedCount > 0) {
    showToast(`${actionLabel}成功，已删除 ${deletedCount} 个账号`, "success");
    return;
  }
  throw new Error(result.failed?.[0]?.reason || `${actionLabel}失败`);
}

async function deleteSingleAccount(key) {
  const account = state.accounts.find((item) => item.key === key) || state.selectedAccount;
  if (!account) {
    showToast("未找到账号", "error");
    return;
  }
  if (!isDeletableAccount(account)) {
    showToast("仅允许删除已迁移停用或 Token 过期账号", "error");
    return;
  }
  const confirmed = window.confirm(buildDeleteConfirmMessage([account]));
  if (!confirmed) return;

  try {
    const result = await apiFetch(`/api/accounts/${encodeURIComponent(key)}`, {
      method: "DELETE",
    });
    await applyDeleteResult(result, `删除${deleteScopeLabel(account)}`);
  } catch (error) {
    showToast(error.message || "删除失败", "error");
  }
}

async function deleteSelectedAccounts() {
  const accounts = selectedDeletableAccounts();
  if (!accounts.length) {
    showToast("请先勾选本页要删除的账号", "error");
    return;
  }
  const confirmed = window.confirm(buildDeleteConfirmMessage(accounts));
  if (!confirmed) return;

  batchDeleteBtn.disabled = true;
  try {
    const result = await apiFetch("/api/accounts/batch-delete", {
      method: "POST",
      body: JSON.stringify({ keys: accounts.map((item) => item.key) }),
    });
    await applyDeleteResult(result, "批量删除账号");
  } catch (error) {
    showToast(error.message || "批量删除失败", "error");
  } finally {
    batchDeleteBtn.disabled = state.selectedKeys.size === 0;
  }
}

async function deleteAllDeactivatedAccounts() {
  const total = Number(state.overview?.summary?.status_counts?.deactivated || 0);
  if (total <= 0) {
    showToast("当前没有可清空的迁移停用账号", "error");
    return;
  }
  const confirmed = window.confirm(
    `确认一键清空全部 ${total} 个迁移停用账号吗？\n\n删除前会先做本地备份，然后从停用归档目录移除。`
  );
  if (!confirmed) return;

  deleteAllDeactivatedBtn.disabled = true;
  try {
    const result = await apiFetch("/api/accounts/delete-all-deactivated", {
      method: "POST",
    });
    state.selectedKeys = new Set();
    await applyDeleteResult(result, "一键清空停用账号");
  } catch (error) {
    showToast(error.message || "一键清空失败", "error");
  } finally {
    deleteAllDeactivatedBtn.disabled = false;
  }
}

function parseDownloadFileName(disposition) {
  const raw = String(disposition || "");
  const utf8Match = raw.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      return utf8Match[1];
    }
  }
  const plainMatch = raw.match(/filename=\"?([^\";]+)\"?/i);
  if (plainMatch?.[1]) {
    return plainMatch[1];
  }
  return "disabled_problem_accounts.zip";
}

async function downloadDisabledUnrefreshedAccounts() {
  const total = Number(state.overview?.summary?.disabled_unrefreshed_total || 0);
  if (total <= 0) {
    showToast("当前没有符合条件的异常 JSON（缺少 refresh token / Token 过期 / 已过期）", "info");
    return;
  }
  const confirmed = window.confirm(
    `确认打包下载并删除 ${total} 个异常 JSON 文件吗？\n\n包含范围：\n1. 已禁用且缺少 refresh token\n2. quota 刷新后判定为 Token 过期\n3. 账号状态已过期（expired）\n\n系统会先把这些文件打成 ZIP 下载到浏览器；下载完成后，服务端会立即删除对应源文件。`
  );
  if (!confirmed) return;

  downloadDisabledUnrefreshedBtn.disabled = true;
  try {
    const response = await fetch("/api/accounts/export-disabled-unrefreshed", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (response.status === 401) {
      loginModal.classList.remove("hidden");
      throw new Error("未登录");
    }
    if (!response.ok) {
      const text = await response.text();
      let payload = {};
      try {
        payload = text ? JSON.parse(text) : {};
      } catch {
        payload = {};
      }
      throw new Error(payload.error || "打包下载失败");
    }

    const blob = await response.blob();
    const fileName = parseDownloadFileName(response.headers.get("Content-Disposition"));
    const blobUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = blobUrl;
    anchor.download = fileName;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(blobUrl), 30_000);

    state.selectedKeys = new Set();
    await new Promise((resolve) => window.setTimeout(resolve, 600));
    await loadOverview(true);
    await loadAccounts();
    showToast(`打包下载成功，已归档 ${total} 个异常 JSON`, "success");
  } catch (error) {
    showToast(error.message || "打包下载失败", "error");
  } finally {
    downloadDisabledUnrefreshedBtn.disabled = Number(state.overview?.summary?.disabled_unrefreshed_total || 0) === 0;
  }
}

async function boot() {
  try {
    setActiveTab(state.activeTab);
    await loadOverview();
    await loadAccounts();
    loginModal.classList.add("hidden");
  } catch (error) {
    if (String(error.message || error).includes("未登录")) {
      loginModal.classList.remove("hidden");
      return;
    }
    showToast(error.message || "加载失败", "error");
  }
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  loginError.textContent = "";
  try {
    await apiFetch("/api/login", {
      method: "POST",
      body: JSON.stringify({ token: loginToken.value.trim() }),
    });
    loginToken.value = "";
    loginModal.classList.add("hidden");
    await boot();
    showToast("登录成功", "success");
  } catch (error) {
    loginError.textContent = error.message || "登录失败";
  }
});

runDisabledRefreshBtn.addEventListener("click", async () => {
  runDisabledRefreshBtn.disabled = true;
  try {
    const snapshot = await apiFetch("/api/disabled-refresh/run", {
      method: "POST",
      body: JSON.stringify({}),
    });
    if (state.overview) {
      state.overview.disabled_refresh = snapshot;
    }
    renderDisabledRefresh();
    await loadOverview();
    await loadAccounts({ preserveSelection: true });
    const summary = snapshot.summary || {};
    showToast(
      `disabled refresh 已执行：selected ${summary.selected_count ?? 0} / blocked ${summary.blocked_count ?? 0} / recovered ${summary.recovered_count ?? 0}`,
      "success"
    );
  } catch (error) {
    showToast(error.message || "disabled refresh 执行失败", "error");
  } finally {
    runDisabledRefreshBtn.disabled = Boolean(state.overview?.disabled_refresh?.running);
  }
});

downloadDisabledRefreshResultBtn.addEventListener("click", () => {
  window.open("/api/disabled-refresh/latest-result", "_blank");
});

accountListTab.addEventListener("click", () => {
  setActiveTab("accounts");
});

disabledRefreshTab.addEventListener("click", () => {
  setActiveTab("disabled-refresh");
});

logoutBtn.addEventListener("click", async () => {
  await apiFetch("/api/logout", { method: "POST" });
  state.selectedAccount = null;
  state.selectedKeys = new Set();
  closeDetailModal();
  renderDetail(null);
  renderAccounts();
  loginModal.classList.remove("hidden");
  showToast("已退出登录");
});

function updateSortIndicators() {
  document.querySelectorAll(".sortable-th").forEach((th) => {
    const field = th.dataset.sortBy;
    const indicator = th.querySelector(".sort-indicator");
    if (!indicator) return;
    th.classList.remove("sort-asc", "sort-desc", "sort-active");
    if (state.sort.sortBy === field) {
      const isDesc = state.sort.sortOrder === "desc";
      th.classList.add("sort-active", isDesc ? "sort-desc" : "sort-asc");
      indicator.textContent = isDesc ? " ▼" : " ▲";
    } else {
      indicator.textContent = " ⇅";
    }
  });
}

document.querySelectorAll(".sortable-th").forEach((th) => {
  th.addEventListener("click", () => {
    const field = th.dataset.sortBy;
    if (!field) return;
    if (state.sort.sortBy === field) {
      if (state.sort.sortOrder === "asc") {
        state.sort.sortOrder = "desc";
      } else {
        // Third click: clear sort
        state.sort.sortBy = "";
        state.sort.sortOrder = "";
      }
    } else {
      state.sort.sortBy = field;
      state.sort.sortOrder = "asc";
    }
    state.page = 1;
    loadAccounts({ preserveSelection: true }).catch((error) =>
      showToast(error.message || "排序失败", "error")
    );
  });
});

refreshBtn.addEventListener("click", async () => {
  try {
    await loadOverview(true);
    await loadAccounts();
    showToast("已强制刷新面板缓存", "success");
  } catch (error) {
    showToast(error.message || "刷新失败", "error");
  }
});

searchInput.addEventListener("input", () => {
  state.filters.q = searchInput.value.trim();
  state.page = 1;
  loadAccounts().catch((error) => showToast(error.message || "搜索失败", "error"));
});

statusSelect.addEventListener("change", () => {
  state.filters.status = statusSelect.value;
  state.page = 1;
  loadAccounts().catch((error) => showToast(error.message || "筛选失败", "error"));
});

quotaErrorSelect.addEventListener("change", () => {
  state.filters.quotaErrorType = quotaErrorSelect.value;
  state.page = 1;
  loadAccounts().catch((error) => showToast(error.message || "筛选失败", "error"));
});

groupSelect.addEventListener("change", () => {
  state.filters.group = groupSelect.value;
  state.page = 1;
  loadAccounts().catch((error) => showToast(error.message || "筛选失败", "error"));
});

pageSizeSelect.addEventListener("change", () => {
  state.pageSize = Number(pageSizeSelect.value);
  state.page = 1;
  loadAccounts().catch((error) => showToast(error.message || "更新分页失败", "error"));
});

prevPageBtn.addEventListener("click", () => {
  if (state.page <= 1) return;
  state.page -= 1;
  loadAccounts().catch((error) => showToast(error.message || "翻页失败", "error"));
});

nextPageBtn.addEventListener("click", () => {
  if (state.page >= state.totalPages) return;
  state.page += 1;
  loadAccounts().catch((error) => showToast(error.message || "翻页失败", "error"));
});

selectAllDeactivated.addEventListener("change", (event) => {
  const checked = event.target.checked;
  selectableAccountsOnPage().forEach((item) => {
    if (checked) {
      state.selectedKeys.add(item.key);
    } else {
      state.selectedKeys.delete(item.key);
    }
  });
  renderAccounts();
});

clearSelectionBtn.addEventListener("click", () => {
  state.selectedKeys = new Set();
  renderAccounts();
});

batchDeleteBtn.addEventListener("click", async () => {
  await deleteSelectedAccounts();
});

batchEnableBtn.addEventListener("click", async () => {
  await enableSelectedDisabledAccounts();
});

batchRefreshRecoverBtn.addEventListener("click", async () => {
  await refreshRecoverSelectedDisabledAccounts();
});

batchTokenRefreshBtn.addEventListener("click", async () => {
  await tokenRefreshSelectedAccounts();
});

downloadDisabledUnrefreshedBtn.addEventListener("click", async () => {
  await downloadDisabledUnrefreshedAccounts();
});

deleteAllDeactivatedBtn.addEventListener("click", async () => {
  await deleteAllDeactivatedAccounts();
});

detailToggleStatusBtn.addEventListener("click", async () => {
  await toggleSelectedAccountStatus();
});

detailRefreshQuotaBtn.addEventListener("click", async () => {
  await refreshSelectedAccountQuota();
});

detailRefreshQuotaQuickBtn.addEventListener("click", async () => {
  await refreshSelectedAccountQuota();
});

detailSettingsBtn.addEventListener("click", () => {
  openDetailModal();
});

detailModalCloseBtn.addEventListener("click", () => {
  closeDetailModal();
});

detailModal.addEventListener("click", (event) => {
  if (event.target === detailModal) {
    closeDetailModal();
  }
});

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !detailModal.classList.contains("hidden")) {
    closeDetailModal();
  }
});

detailDeleteBtn.addEventListener("click", async () => {
  const key = detailDeleteBtn.dataset.key;
  if (!key) return;
  await deleteSingleAccount(key);
});

metaForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.selectedAccount) return;

  try {
    const updated = await apiFetch(`/api/accounts/${encodeURIComponent(state.selectedAccount.key)}/meta`, {
      method: "PATCH",
      body: JSON.stringify({
        group: metaGroup.value.trim(),
        owner: metaOwner.value.trim(),
        tags: metaTags.value.trim(),
        note: metaNote.value.trim(),
        starred: metaStarred.checked,
      }),
    });
    state.selectedAccount = updated;
    await loadOverview();
    await loadAccounts();
    renderDetail(updated);
    showToast("元数据已保存", "success");
  } catch (error) {
    showToast(error.message || "保存失败", "error");
  }
});

boot();
