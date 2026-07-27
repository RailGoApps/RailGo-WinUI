/* ============================================================
   RailGPT Web — Frontend Application
   ============================================================ */

"use strict";

// ============================================================
// Page zoom  (Ctrl + Wheel  /  Ctrl ± / Ctrl 0)
// Works inside pywebview (EdgeChromium) where the OS Ctrl+wheel
// shortcut may be swallowed by the WebView.
// ============================================================
(function () {
  var _zoom = 1.0;
  function _applyZoom(z) {
    _zoom = Math.min(2.5, Math.max(0.4, Math.round(z * 10) / 10));
    document.documentElement.style.zoom = _zoom;
  }
  document.addEventListener('wheel', function (e) {
    if (!e.ctrlKey) return;
    e.preventDefault();
    _applyZoom(_zoom + (e.deltaY < 0 ? 0.1 : -0.1));
  }, { passive: false });
  document.addEventListener('keydown', function (e) {
    if (!e.ctrlKey) return;
    if (e.key === '0') { e.preventDefault(); _applyZoom(1.0); }
    if (e.key === '=' || e.key === '+') { e.preventDefault(); _applyZoom(_zoom + 0.1); }
    if (e.key === '-' || e.key === '_') { e.preventDefault(); _applyZoom(_zoom - 0.1); }
  });
})();

// ============================================================
// State
// ============================================================
const state = {
  currentCid:     null,
  busy:           false,
  mode:           "fast-go",
  theme:          "light",
  hue:            270,    // used by colorful theme
  observerPanel:  "thinking",
  currentAiBubble: null,
  currentAiText:  "",
  thinkingText:   "",
  settings:       null,
  apiConfigured:  false,
  thinkingApiConfigured: false,
  activeSettingsTab: "account",
  aboutLoaded:    false,
};

// ============================================================
// Utilities
// ============================================================
function $(sel, ctx = document) { return ctx.querySelector(sel); }
function $$(sel, ctx = document) { return [...ctx.querySelectorAll(sel)]; }

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderMarkdown(text) {
  if (typeof marked === "undefined") return escapeHtml(text);
  return marked.parse(text, { breaks: true, gfm: true });
}

function scrollToBottom(el) { el.scrollTop = el.scrollHeight; }

function timeAgo(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr.replace(" ", "T"));
  const diff = Math.floor((Date.now() - d.getTime()) / 1000);
  if (isNaN(diff)) return dateStr.slice(0, 10);
  if (diff < 60)    return "刚刚";
  if (diff < 3600)  return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

// ============================================================
// API helpers
// ============================================================
async function apiGet(url)          { return (await fetch(url)).json(); }
async function apiPost(url, body)   {
  return (await fetch(url, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body) })).json();
}
async function apiDelete(url)       { return (await fetch(url, { method:"DELETE" })).json(); }
async function apiPut(url, body)    {
  return (await fetch(url, { method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body) })).json();
}

// ============================================================
// Settings
// ============================================================
async function loadSettingsState() {
  try {
    const payload = await apiGet("/api/settings");
    applySettingsPayload(payload);
    return payload;
  } catch (e) {
    setSettingsFeedback("设置读取失败，请稍后重试。", true);
    return null;
  }
}

function applySettingsPayload(payload) {
  state.settings = payload || {};
  state.apiConfigured = !!(payload && payload.has_primary_api_key);
  state.thinkingApiConfigured = !!(payload && payload.has_thinking_api_key);

  $("#account-status-title").textContent = payload?.account?.title || "未登录";
  $("#account-status-desc").textContent = payload?.account?.description || "账号功能预留中。";

  const providerSelect = $("#settings-provider-select");
  if (providerSelect) {
    const providers = Array.isArray(payload?.providers) ? payload.providers : [];
    const currentProviderId = payload?.provider || "deepseek";
    providerSelect.value = currentProviderId;

    const dropdown = $("#settings-provider-dropdown");
    const currentProvider = providers.find(provider => provider.id === currentProviderId) || providers[0] || null;
    $("#settings-provider-label").textContent = currentProvider?.label || "DeepSeek";
    $("#settings-provider-desc").textContent = currentProvider?.description || "DeepSeek API（OpenAI 兼容请求格式）";

    if (dropdown) {
      dropdown.innerHTML = providers.map(provider => `
        <button
          type="button"
          class="theme-option settings-provider-option ${provider.id === currentProviderId ? "active" : ""}"
          data-provider-id="${escapeHtml(provider.id)}"
        >
          <span class="settings-provider-option-copy">
            <span class="settings-provider-option-title">${escapeHtml(provider.label)}</span>
            <span class="settings-provider-option-desc">${escapeHtml(provider.description || "")}</span>
          </span>
        </button>
      `).join("");

      $$(".settings-provider-option", dropdown).forEach(btn => {
        btn.addEventListener("click", () => selectSettingsProvider(btn.dataset.providerId));
      });
    }
  }

  $("#settings-primary-key-status").textContent = payload?.has_primary_api_key
    ? `已配置：${payload.masked_primary_api_key || payload.masked_api_key}`
    : "未配置，未配置时聊天发送会被锁定。";

  $("#settings-thinking-key-status").textContent = payload?.has_thinking_api_key
    ? `已配置：${payload.masked_thinking_api_key}`
    : "未配置，将自动复用主对话 Key。";

  $("#settings-config-path").textContent = payload?.config_path || "未找到配置路径";
  updateInputAvailability();
}

function updateInputAvailability() {
  const locked = !state.apiConfigured;
  const input = $("#chat-input");
  const send = $("#send-btn");
  const quickFill = $("#quick-fill-btn");
  const newChat = $("#btn-new-chat");
  const banner = $("#api-lock-banner");

  if (send) send.disabled = state.busy || locked;
  if (quickFill) quickFill.disabled = state.busy || locked;
  if (input) {
    input.disabled = state.busy || locked;
    input.placeholder = locked
      ? "请先在设置中配置主对话 API Key"
      : "输入铁路问题… Enter 换行，Ctrl+Enter 发送";
  }
  if (newChat) newChat.disabled = state.busy;
  if (banner) banner.classList.toggle("hidden", !locked);
}

function setSettingsFeedback(message, isError = false) {
  const el = $("#settings-api-feedback");
  if (!el) return;
  const text = String(message || "").trim();
  el.classList.toggle("hidden", !text);
  el.classList.toggle("error", !!text && isError);
  el.textContent = text;
}

function switchSettingsTab(tab) {
  state.activeSettingsTab = tab;
  $$(".settings-tab").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.settingsTab === tab);
  });
  $$(".settings-panel").forEach(panel => {
    panel.classList.toggle("hidden", panel.id !== `settings-panel-${tab}`);
  });
  if (tab === "about") loadAboutContent();
}

async function openSettingsDialog(tab = "account") {
  $("#settings-dialog").classList.remove("hidden");
  switchSettingsTab(tab);
  await loadSettingsState();
  if (tab === "about") await loadAboutContent();
}

function closeSettingsDialog() {
  $("#settings-dialog").classList.add("hidden");
  closeSettingsProviderDropdown();
}

function toggleSettingsProviderDropdown() {
  const selector = $("#settings-provider-selector");
  const trigger = $("#settings-provider-btn");
  if (!selector || !trigger) return;
  const nextOpen = !selector.classList.contains("open");
  selector.classList.toggle("open", nextOpen);
  trigger.setAttribute("aria-expanded", nextOpen ? "true" : "false");
}

function closeSettingsProviderDropdown() {
  const selector = $("#settings-provider-selector");
  const trigger = $("#settings-provider-btn");
  if (!selector || !trigger) return;
  selector.classList.remove("open");
  trigger.setAttribute("aria-expanded", "false");
}

function selectSettingsProvider(providerId) {
  const providers = Array.isArray(state.settings?.providers) ? state.settings.providers : [];
  const selected = providers.find(provider => provider.id === providerId);
  if (!selected) return;

  $("#settings-provider-select").value = selected.id;
  $("#settings-provider-label").textContent = selected.label;
  $("#settings-provider-desc").textContent = selected.description || "";
  $$(".settings-provider-option").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.providerId === selected.id);
  });
  closeSettingsProviderDropdown();
}

async function loadAboutContent() {
  const content = $("#about-content");
  if (!content || state.aboutLoaded) return;
  content.innerHTML = '<p style="color:var(--text-muted)">加载中…</p>';
  try {
    const res = await fetch("/api/readme");
    const data = await res.json();
    if (data.error) {
      content.innerHTML = `<p style="color:var(--danger)">${escapeHtml(data.error)}</p>`;
      return;
    }
    content.innerHTML = renderMarkdown(data.content);
    content.querySelectorAll("pre code").forEach(el => {
      if (typeof hljs !== "undefined") hljs.highlightElement(el);
    });
    state.aboutLoaded = true;
  } catch (_) {
    content.innerHTML = `<p style="color:var(--danger)">加载失败</p>`;
  }
}

async function saveApiKey(slot) {
  const provider = $("#settings-provider-select").value || "deepseek";
  const input = slot === "thinking" ? $("#settings-thinking-key-input") : $("#settings-primary-key-input");
  const value = input.value.trim();
  if (!value) {
    setSettingsFeedback(slot === "thinking" ? "请输入 Thinker API Key。" : "请输入主对话 API Key。", true);
    input.focus();
    return;
  }

  setSettingsFeedback("保存中…");
  const body = { provider };
  if (slot === "thinking") body.thinking_api_key = value;
  else body.primary_api_key = value;

  try {
    const payload = await apiPut("/api/settings/api", body);
    if (payload.error) {
      setSettingsFeedback(payload.error, true);
      return;
    }
    input.value = "";
    applySettingsPayload(payload);
    setSettingsFeedback(slot === "thinking" ? "Thinker API Key 已更新。" : "主对话 API Key 已更新。");
  } catch (_) {
    setSettingsFeedback("保存失败，请稍后重试。", true);
  }
}

async function deleteApiKey(slot) {
  const label = slot === "thinking" ? "Thinker API Key" : "主对话 API Key";
  if (!confirm(`确定删除${label}吗？`)) return;

  setSettingsFeedback("删除中…");
  try {
    const res = await fetch(`/api/settings/api?slot=${encodeURIComponent(slot)}`, {
      method: "DELETE",
    });
    const payload = await res.json();
    if (!res.ok || payload.error) {
      setSettingsFeedback(payload.error || "删除失败，请稍后重试。", true);
      return;
    }
    if (slot === "thinking") $("#settings-thinking-key-input").value = "";
    else $("#settings-primary-key-input").value = "";
    applySettingsPayload(payload);
    setSettingsFeedback(`${label} 已删除。`);
  } catch (_) {
    setSettingsFeedback("删除失败，请稍后重试。", true);
  }
}

// ============================================================
// Conversation list (sidebar)
// ============================================================
async function loadConvList() {
  const list = await apiGet("/api/conversations");
  renderConvList(list);
  if (window.chrome && window.chrome.webview) {
    window.chrome.webview.postMessage({ type: "conversations_changed" });
  }
}

function renderConvList(items) {
  const el = $("#conv-list");
  if (!items || items.length === 0) {
    el.innerHTML = `<p style="font-size:12px;color:var(--text-muted);padding:12px 10px;">暂无对话</p>`;
    return;
  }
  el.innerHTML = items.map(item => `
    <div class="conv-item ${item.id === state.currentCid ? "active" : ""}" data-cid="${item.id}">
      <div class="conv-icon">💬</div>
      <div class="conv-info">
        <div class="conv-title">${escapeHtml(item.title || "New Chat")}</div>
        <div class="conv-time">${timeAgo(item.updated)}</div>
      </div>
      <div class="conv-actions">
        <button class="conv-action-btn rename-btn" data-cid="${item.id}" title="重命名">
          <svg viewBox="0 0 20 20" fill="currentColor"><path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zm-2.207 2.207L3 14.172V17h2.828l8.38-8.379-2.83-2.828z"/></svg>
        </button>
        <button class="conv-action-btn danger delete-btn" data-cid="${item.id}" title="删除">
          <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd"/></svg>
        </button>
        <button class="conv-action-btn export-btn" data-cid="${item.id}" title="导出 Markdown">
          <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M3 17a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm3.293-7.707a1 1 0 011.414 0L9 10.586V3a1 1 0 112 0v7.586l1.293-1.293a1 1 0 111.414 1.414l-3 3a1 1 0 01-1.414 0l-3-3a1 1 0 010-1.414z" clip-rule="evenodd"/></svg>
        </button>
      </div>
    </div>
  `).join("");

  $$(".conv-item").forEach(item => {
    item.addEventListener("click", e => {
      if (e.target.closest(".conv-actions")) return;
      const cid = parseInt(item.dataset.cid);
      if (cid !== state.currentCid) selectConversation(cid);
    });
  });
  $$(".rename-btn").forEach(btn => btn.addEventListener("click", e => {
    e.stopPropagation(); openRenameDialog(parseInt(btn.dataset.cid));
  }));
  $$(".delete-btn").forEach(btn => btn.addEventListener("click", e => {
    e.stopPropagation(); deleteConversation(parseInt(btn.dataset.cid));
  }));
  $$(".export-btn").forEach(btn => btn.addEventListener("click", e => {
    e.stopPropagation(); exportConversation(parseInt(btn.dataset.cid));
  }));
}

// ============================================================
// Chat area
// ============================================================
function showHomePanelIfEmpty() {
  const empty = $("#messages").children.length === 0;
  $("#messages").classList.toggle("hidden", empty);
  $("#home-panel").classList.toggle("hidden", !empty);
}

function hideHomePanel() {
  $("#home-panel").classList.add("hidden");
  $("#messages").classList.remove("hidden");
}

function _addCopyBtn(wrap, getText) {
  const actions = document.createElement("div");
  actions.className = "bubble-actions";
  const btn = document.createElement("button");
  btn.className = "btn-copy-bubble";
  btn.title = "复制文本";
  btn.innerHTML = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="2" width="9" height="11" rx="1.5"/><path d="M2 5v8a1.5 1.5 0 001.5 1.5H11"/></svg>`;
  btn.addEventListener("click", async () => {
    const text = getText();
    try { await navigator.clipboard.writeText(text); } catch (_) {
      const ta = Object.assign(document.createElement("textarea"),
        { value: text, style: "position:fixed;opacity:0" });
      document.body.appendChild(ta); ta.select(); document.execCommand("copy");
      document.body.removeChild(ta);
    }
    const orig = btn.innerHTML;
    btn.innerHTML = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="2,9 6,13 14,4"/></svg>`;
    btn.classList.add("copied");
    setTimeout(() => { btn.innerHTML = orig; btn.classList.remove("copied"); }, 1500);
  });
  actions.appendChild(btn);
  wrap.appendChild(actions);
}

function addUserBubble(text) {
  hideHomePanel();
  const row = document.createElement("div");
  row.className = "bubble-row user";
  row.innerHTML = `<div class="bubble-avatar">你</div><div class="bubble-wrap"><div class="bubble">${escapeHtml(text)}</div></div>`;
  _addCopyBtn(row.querySelector(".bubble-wrap"), () => text);
  $("#messages").appendChild(row);
  scrollToBottom($("#messages"));
}

function addAiBubble(text = "", attachments = []) {
  hideHomePanel();
  const row = document.createElement("div");
  row.className = "bubble-row ai";
  const streaming = text === "";
  row.innerHTML = `
    <div class="bubble-avatar">🚆</div>
    <div class="bubble-wrap">
      <div class="bubble">
        <div class="bubble-text${streaming ? " typing-cursor" : ""}">${text ? renderMarkdown(text) : ""}</div>
        <div class="bubble-attachments"></div>
      </div>
    </div>
  `;
  const bubbleEl = row.querySelector(".bubble");
  _addCopyBtn(row.querySelector(".bubble-wrap"), () => bubbleEl.querySelector(".bubble-text").innerText.trim());
  $("#messages").appendChild(row);
  for (const attachment of (attachments || [])) renderAttachment(bubbleEl, attachment);
  scrollToBottom($("#messages"));
  return bubbleEl;
}

let _renderFrame = null;

function appendToken(bubbleEl, token) {
  state.currentAiText += token;
  if (_renderFrame) return;
  _renderFrame = requestAnimationFrame(() => {
    _renderFrame = null;
    const textEl = bubbleEl.querySelector(".bubble-text") || bubbleEl;
    textEl.innerHTML = renderMarkdown(state.currentAiText);
    scrollToBottom($("#messages"));
  });
}

function finalizeAiBubble(bubbleEl, fullText) {
  if (_renderFrame) { cancelAnimationFrame(_renderFrame); _renderFrame = null; }
  const textEl = bubbleEl.querySelector(".bubble-text") || bubbleEl;
  textEl.classList.remove("typing-cursor");
  textEl.innerHTML = renderMarkdown(fullText || state.currentAiText);
  $$("pre code", textEl).forEach(b => { if (window.hljs) hljs.highlightElement(b); });
  scrollToBottom($("#messages"));
}

const ASSET_ID_RE = /^[0-9a-f]{64}$/;

function renderAttachment(bubbleEl, attachment) {
  if (!attachment || !ASSET_ID_RE.test(String(attachment.asset_id || ""))) return;
  const host = bubbleEl.querySelector(".bubble-attachments");
  if (!host) return;
  const id = String(attachment.asset_id);
  if (host.querySelector(`[data-asset-id="${id}"]`)) return;

  const card = document.createElement("section");
  card.className = `attachment-card attachment-${attachment.type || "unknown"}`;
  card.dataset.assetId = id;
  const caption = document.createElement("div");
  caption.className = "attachment-caption";
  caption.textContent = attachment.caption || "RailGPT 附件";

  if (attachment.type === "coach_image") {
    const image = document.createElement("img");
    image.className = "coach-asset-image";
    image.loading = "lazy";
    image.alt = attachment.caption || "列车车厢图";
    image.src = `/api/assets/coach/${id}`;
    card.appendChild(image);
    card.appendChild(caption);
    host.appendChild(card);
    return;
  }
  if (attachment.type !== "route_map") return;

  const mapEl = document.createElement("div");
  mapEl.className = "route-map-canvas";
  const fallback = document.createElement("div");
  fallback.className = "route-map-fallback hidden";
  const attribution = document.createElement("div");
  attribution.className = "attachment-attribution";
  attribution.textContent = "线路数据 © RailGo · 地图 © OpenStreetMap contributors";
  card.append(mapEl, fallback, caption, attribution);
  host.appendChild(card);
  hydrateRouteMap(id, mapEl, fallback);
}

async function hydrateRouteMap(assetId, mapEl, fallbackEl) {
  const showFallback = (svg, message = "") => {
    mapEl.classList.add("hidden");
    fallbackEl.classList.remove("hidden");
    fallbackEl.innerHTML = svg || `<p>${escapeHtml(message || "地图暂不可用")}</p>`;
  };
  try {
    const response = await fetch(`/api/assets/routes/${assetId}`);
    if (!response.ok) throw new Error("线路资产不可用");
    const data = await response.json();
    if (!window.L) return showFallback(data.fallback_svg, "离线线路图");
    const map = L.map(mapEl, { zoomControl: true, attributionControl: true });
    let tileErrors = 0;
    const tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: "&copy; OpenStreetMap contributors",
      crossOrigin: true,
    });
    tiles.on("tileerror", () => {
      tileErrors += 1;
      if (tileErrors >= 3) {
        try { map.remove(); } catch (_) {}
        showFallback(data.fallback_svg, "网络底图不可用，已切换离线路线图");
      }
    });
    tiles.addTo(map);
    const layer = L.geoJSON(data.geojson, {
      style: { color: "#176b4d", weight: 5, opacity: 0.9 },
      pointToLayer: (feature, latlng) => L.circleMarker(latlng, {
        radius: 5, color: "#ffffff", weight: 2, fillColor: "#e4512b", fillOpacity: 1,
      }),
      onEachFeature: (feature, item) => {
        const name = feature && feature.properties && feature.properties.name;
        if (name) item.bindTooltip(String(name));
      },
    }).addTo(map);
    const bounds = layer.getBounds();
    if (bounds.isValid()) map.fitBounds(bounds, { padding: [22, 22] });
    setTimeout(() => map.invalidateSize(), 50);
  } catch (error) {
    showFallback("", error.message || "线路图加载失败");
  }
}

function addSystemBubble(text) {
  hideHomePanel();
  const row = document.createElement("div");
  row.className = "bubble-row ai";
  row.innerHTML = `<div class="bubble-avatar">⚠️</div><div class="bubble-wrap"><div class="bubble system">${escapeHtml(text)}</div></div>`;
  _addCopyBtn(row.querySelector(".bubble-wrap"), () => text);
  $("#messages").appendChild(row);
  scrollToBottom($("#messages"));
}

function clearMessages() {
  $("#messages").innerHTML = "";
  showHomePanelIfEmpty();
}

// ============================================================
// Observer panel
// ============================================================
function appendThinking(text) {
  state.thinkingText += text;
  const el = $("#thinking-text");
  el.textContent = state.thinkingText;
  scrollToBottom(el);
}
function clearThinking() { state.thinkingText = ""; $("#thinking-text").textContent = ""; }

function appendPswLine(line) {
  const el = $("#psw-log");
  el.textContent += line + "\n";
  scrollToBottom(el);
}
function clearPsw() { $("#psw-log").textContent = ""; }

// ============================================================
// Conversation actions
// ============================================================
async function newConversation() {
  if (state.busy) return;
  const data = await apiPost("/api/conversations", {});
  state.currentCid = data.id;
  state.currentAiText = "";
  clearMessages(); clearThinking(); clearPsw();
  await loadConvList();
}

async function selectConversation(cid) {
  if (state.busy) return false;

  // Fade out messages area before loading new conversation
  const messagesEl = $("#messages");
  messagesEl.classList.add("fade-out");
  await new Promise(r => setTimeout(r, 200));

  const data = await apiPost(`/api/conversations/${cid}/load`, {});
  if (data.error) {
    messagesEl.classList.remove("fade-out");
    return false;
  }
  state.currentCid = cid;
  state.currentAiText = "";
  clearMessages(); clearThinking(); clearPsw();
  for (const msg of (data.messages || [])) {
    if (msg.role === "user") addUserBubble(msg.content);
    else addAiBubble(msg.content, msg.attachments || []);
  }
  await loadConvList();

  // Fade in messages area
  messagesEl.classList.remove("fade-out");
  messagesEl.classList.add("fade-in");
  messagesEl.addEventListener("animationend", () => messagesEl.classList.remove("fade-in"), { once: true });
  return true;
}

async function deleteConversation(cid) {
  if (!confirm("确定要删除这条对话吗？")) return;
  await apiDelete(`/api/conversations/${cid}`);
  if (state.currentCid === cid) {
    state.currentCid = null;
    clearMessages(); clearThinking(); clearPsw();
  }
  await loadConvList();
}

async function exportConversation(cid) {
  try {
    const desktopApi = window.pywebview && window.pywebview.api;
    if (desktopApi && typeof desktopApi.exportConversation === "function") {
      const result = await desktopApi.exportConversation(cid);
      if (!result || result.cancelled) return;
      if (!result.ok) {
        alert(result.error || "导出失败，请稍后重试。");
        return;
      }
      alert(`已导出到：\n${result.path}`);
      return;
    }
  } catch (err) {
    alert((err && err.message) || "桌面导出失败，请稍后重试。");
    return;
  }

  window.location.href = `/api/conversations/${cid}/export`;
}

// ============================================================
// Sidebar search
// ============================================================
let _searchScope = "title";
let _searchTimer = null;

function toggleSearchPanel() {
  const panel   = $("#search-panel");
  const btn     = $("#sidebar-search-btn");
  const sidebar = $("#sidebar");
  const isOpen  = !panel.classList.contains("hidden");
  if (isOpen) {
    panel.classList.add("hidden");
    btn.classList.remove("active");
    sidebar.classList.remove("search-open");
    clearSearch();
  } else {
    // Expand sidebar if it's collapsed
    if ($("#app").classList.contains("left-collapsed")) {
      $("#app").classList.remove("left-collapsed");
    }
    panel.classList.remove("hidden");
    btn.classList.add("active");
    sidebar.classList.add("search-open");
    setTimeout(() => $("#search-input").focus(), 40);
  }
}

function clearSearch() {
  $("#search-input").value = "";
  $("#search-results").innerHTML = "";
}

function _highlightMatch(text, q) {
  const safeText = escapeHtml(text);
  // Escape the HTML-escaped query for use in regex
  const safeQ = escapeHtml(q).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  if (!safeQ) return safeText;
  return safeText.replace(new RegExp(`(${safeQ})`, "gi"),
    '<mark class="search-hl">$1</mark>');
}

async function runSearch() {
  const q = $("#search-input").value.trim();
  const el = $("#search-results");
  if (!q) { el.innerHTML = ""; return; }

  try {
    const res  = await fetch(`/api/search?q=${encodeURIComponent(q)}&scope=${_searchScope}`);
    const data = await res.json();
    _renderSearchResults(data, q);
  } catch (_) {
    $("#search-results").innerHTML = `<p class="search-empty">搜索失败</p>`;
  }
}

function _renderSearchResults(results, q) {
  const el = $("#search-results");
  if (!results.length) {
    el.innerHTML = `<p class="search-empty">无匹配结果</p>`;
    return;
  }

  el.innerHTML = results.map(r => {
    const titleHtml = _highlightMatch(r.title || "New Chat", q);
    if (_searchScope === "title" || !r.matches.length) {
      return `<div class="search-item" data-cid="${r.cid}" data-msgidx="-1">
        <div class="search-item-title">${titleHtml}</div>
      </div>`;
    }
    // Content scope: title row + up to 3 snippet rows
    const snippetsHtml = r.matches.slice(0, 3).map(m =>
      `<div class="search-snippet" data-cid="${r.cid}" data-msgidx="${m.msg_index}">
        <span class="search-snippet-role">${m.role === "user" ? "你" : "AI"}</span>
        <span class="search-snippet-text">${_highlightMatch(m.snippet, q)}</span>
      </div>`
    ).join("");
    return `<div class="search-group">
      <div class="search-item" data-cid="${r.cid}" data-msgidx="-1">
        <div class="search-item-title">${titleHtml}</div>
      </div>
      ${snippetsHtml}
    </div>`;
  }).join("");

  $$(".search-item, .search-snippet", el).forEach(item => {
    item.addEventListener("click", () => {
      const cid    = parseInt(item.dataset.cid);
      const msgIdx = parseInt(item.dataset.msgidx);
      _searchJump(cid, msgIdx);
    });
  });
}

async function _searchJump(cid, msgIdx) {
  // Close search panel
  $("#search-panel").classList.add("hidden");
  $("#sidebar-search-btn").classList.remove("active");
  $("#sidebar").classList.remove("search-open");

  await selectConversation(cid);

  if (msgIdx >= 0) {
    // Messages are rendered in order — bubble-row index matches msg_index
    const rows = $$("#messages .bubble-row");
    const target = rows[msgIdx];
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.classList.add("search-highlight-flash");
      setTimeout(() => target.classList.remove("search-highlight-flash"), 1600);
    }
  }
}

// About dialog
async function openAboutDialog() {
  const dialog = $("#about-dialog");
  const content = $("#about-content");
  dialog.classList.remove("hidden");
  content.innerHTML = '<p style="color:var(--text-muted)">加载中…</p>';
  try {
    const res = await fetch("/api/readme");
    const data = await res.json();
    if (data.error) {
      content.innerHTML = `<p style="color:var(--danger)">${escapeHtml(data.error)}</p>`;
    } else {
      content.innerHTML = renderMarkdown(data.content);
      content.querySelectorAll("pre code").forEach(el => {
        if (typeof hljs !== "undefined") hljs.highlightElement(el);
      });
    }
  } catch (e) {
    content.innerHTML = `<p style="color:var(--danger)">加载失败</p>`;
  }
}
function closeAboutDialog() { $("#about-dialog").classList.add("hidden"); }

// Rename dialog
let _renameCid = null;
function openRenameDialog(cid) {
  _renameCid = cid;
  $("#rename-input").value = "";
  $("#rename-dialog").classList.remove("hidden");
  $("#rename-input").focus();
}
function closeRenameDialog() { $("#rename-dialog").classList.add("hidden"); _renameCid = null; }
async function confirmRename() {
  const title = $("#rename-input").value.trim();
  if (!title || _renameCid === null) return closeRenameDialog();
  await apiPut(`/api/conversations/${_renameCid}/rename`, { title });
  closeRenameDialog();
  await loadConvList();
}

// ============================================================
// Chat send + SSE streaming
// ============================================================
async function sendMessage() {
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text || state.busy) return;
  if (!state.apiConfigured) {
    await openSettingsDialog("api");
    setSettingsFeedback("请先配置主对话 API Key，然后我们继续。", true);
    return;
  }

  input.value = "";
  input.style.height = "auto";

  if (state.currentCid === null) {
    const data = await apiPost("/api/conversations", {});
    state.currentCid = data.id;
    await loadConvList();
  }

  addUserBubble(text);
  const aiBubble = addAiBubble("");
  state.currentAiBubble = aiBubble;
  state.currentAiText = "";

  if (state.observerPanel === "thinking") clearThinking();
  setBusy(true);
  await streamChat(text, aiBubble);
}

async function streamChat(text, aiBubble) {
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });

    if (!response.ok) {
      const err = await response.json();
      if (err.code === "api_not_configured") {
        aiBubble.remove();
        state.currentAiBubble = null;
        state.currentAiText = "";
        await loadSettingsState();
        await openSettingsDialog("api");
        setSettingsFeedback(err.error || "请先配置主对话 API Key。", true);
        addSystemBubble(err.error || "请先配置主对话 API Key。");
        setBusy(false);
        return;
      }
      if (err.error === "Agent is busy") {
        addSystemBubble("智能体正忙，请等待当前任务完成。");
        aiBubble.remove();
        state.currentAiBubble = null;
        state.currentAiText = "";
        setBusy(false);
        return;
      }
      throw new Error(err.error || "请求失败");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop();
      for (const chunk of chunks) {
        if (!chunk.startsWith("data:")) continue;
        const jsonStr = chunk.slice(5).trim();
        if (!jsonStr) continue;
        try { handleSseEvent(JSON.parse(jsonStr), aiBubble); }
        catch (e) { console.warn("SSE parse error:", e); }
      }
    }
  } catch (err) {
    (aiBubble.querySelector(".bubble-text") || aiBubble).classList.remove("typing-cursor");
    state.currentAiBubble = null;
    addSystemBubble("⚠️ " + (err.message || "连接错误"));
    setBusy(false);
  }
}

function handleSseEvent(event, aiBubble) {
  switch (event.type) {
    case "token":
      appendToken(aiBubble, event.text);
      break;
    case "thinking":
      appendThinking(event.text);
      break;
    case "psw":
      appendPswLine(event.text);
      break;
    case "pending":
      appendToken(aiBubble, event.text);
      break;
    case "attachment":
      renderAttachment(aiBubble, event.attachment);
      break;
    case "done":
      finalizeAiBubble(aiBubble, state.currentAiText);
      state.currentAiBubble = null;
      state.currentCid = event.cid || state.currentCid;
      setBusy(false);
      loadConvList();
      break;
    case "error":
      (aiBubble.querySelector(".bubble-text") || aiBubble).classList.remove("typing-cursor");
      state.currentAiBubble = null;
      addSystemBubble("⚠️ " + event.text);
      setBusy(false);
      break;
    case "heartbeat":
      break;
  }
}

// ============================================================
// Mode selection
// ============================================================
async function setMode(mode) {
  state.mode = mode;
  await apiPost("/api/mode", { mode });
  const captions = {
    "fast-go":   "Railway Knowledge + Fast Coordination · Final: Chat",
    "fast-plus": "Railway Knowledge + Fast Coordination · Final: Reasoner",
    "deep":      "Railway Knowledge + Deep Reasoning",
  };
  $("#mode-caption").textContent = captions[mode] || captions["fast-go"];
  $$(".seg-btn", $("#mode-seg")).forEach(btn => {
    btn.classList.toggle("active", btn.dataset.mode === mode);
  });
}

// ============================================================
// Theme selection
// ============================================================
const THEME_LABELS = {
  light:         "白天",
  dark:          "夜间",
  "high-contrast": "高对比",
  colorful:      "多彩",
};

// highlight.js CSS per theme
const HLJS_THEMES = {
  light:           "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css",
  dark:            "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css",
  "high-contrast": "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark-dimmed.min.css",
  colorful:        "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css",
};

function setTheme(theme) {
  state.theme = theme;
  document.documentElement.setAttribute("data-theme", theme);

  // Swap hljs stylesheet
  const hljsLink = $("#hljs-theme");
  if (hljsLink && HLJS_THEMES[theme]) hljsLink.href = HLJS_THEMES[theme];

  // Update label
  $("#theme-label").textContent = THEME_LABELS[theme] || theme;

  // Mark active option
  $$(".theme-option").forEach(opt => {
    opt.classList.toggle("active", opt.dataset.theme === theme);
  });

  // Show hue picker only for colorful theme
  const hueRow = $("#hue-picker-row");
  if (hueRow) hueRow.classList.toggle("hidden", theme !== "colorful");

  // Re-apply hue when switching to colorful
  if (theme === "colorful") setHue(state.hue);

  // Persist choice
  try { localStorage.setItem("railgpt-theme", theme); } catch (_) {}
}

// ============================================================
// Hue picker (colorful theme)
// ============================================================
function setHue(hue) {
  state.hue = hue;
  document.documentElement.style.setProperty("--hue", hue);

  // Update preview dot color
  const dot = $("#hue-preview");
  if (dot) dot.style.background = `hsl(${hue}, 72%, 50%)`;

  // Keep slider thumb in sync (needed when restoring from localStorage)
  const slider = $("#hue-slider");
  if (slider && parseInt(slider.value) !== hue) slider.value = hue;

  try { localStorage.setItem("railgpt-hue", hue); } catch (_) {}
}

function toggleThemeDropdown(e) {
  e.stopPropagation();
  $("#theme-selector").classList.toggle("open");
}

// ============================================================
// Observer panel toggle
// ============================================================
function switchObserverPanel(panel) {
  state.observerPanel = panel;
  $$(".seg-btn", $("#observer-seg")).forEach(btn => {
    btn.classList.toggle("active", btn.dataset.panel === panel);
  });
  $("#thinking-panel").classList.toggle("hidden", panel !== "thinking");
  $("#psw-panel").classList.toggle("hidden", panel !== "psw");
}

// ============================================================
// Sidebar collapse / expand
// ============================================================
function toggleLeftSidebar() {
  const app = $("#app");
  const collapsed = app.classList.toggle("left-collapsed");
  try { localStorage.setItem("railgpt-left-collapsed", collapsed ? "1" : "0"); } catch (_) {}
}

function toggleRightObserver() {
  const app = $("#app");
  const collapsed = app.classList.toggle("right-collapsed");
  try { localStorage.setItem("railgpt-right-collapsed", collapsed ? "1" : "0"); } catch (_) {}
}

// ============================================================
// Busy state
// ============================================================
function setBusy(busy) {
  state.busy = busy;
  updateInputAvailability();
  if (window.chrome && window.chrome.webview) {
    window.chrome.webview.postMessage({ type: "busy.changed", busy: Boolean(busy) });
  }
}

// ============================================================
// Auto-resize textarea
// ============================================================
function syncInputHeight() {
  const el = $("#chat-input");
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 180) + "px";
}

// ============================================================
// Quick Fill Popup
// ============================================================

// Templates: each type has a .route(dep, arr, date) and .train(t, date) builder
const QF_TYPES = {
  "listing": {
    route: (dep, arr, date) => `${dep}到${arr}${date}有哪些直达列车？`,
    train: (t, date)        => `查询${t}次列车${date}的班次信息`,
  },
  "benchmark": {
    route: (dep, arr, date) => `${dep}到${arr}${date}最快的车次是哪趟？`,
    train: (t, date)        => `${t}次${date}是该线路最快的车次吗？`,
  },
  "ticket": {
    route: (dep, arr, date) => `${dep}到${arr}${date}还有哪些车有余票？`,
    train: (t, date)        => `${t}次${date}还有余票吗？`,
  },
  "transfer": {
    route: (dep, arr, date) => `从${dep}到${arr}${date}如何中转？`,
    train: (t, date)        => `乘坐${t}次${date}如何安排中转换乘？`,
  },
  "train-overview": {
    route: (dep, arr, date) => `${dep}到${arr}${date}有哪些车次，各有什么特点？`,
    train: (t, date)        => `介绍一下${t}次列车${date}的基本信息`,
  },
  "train-path": {
    route: (dep, arr, date) => `${dep}到${arr}${date}的列车分别经停哪些站？`,
    train: (t, date)        => `${t}次列车${date}的经停站和时刻表是什么？`,
  },
  "train-assign": {
    route: (dep, arr, date) => `${dep}到${arr}${date}各趟车用的是什么动车组？`,
    train: (t, date)        => `${t}次列车用的是哪款动车组？`,
  },
  "train-stop": {
    route: (dep, arr, date) => `${dep}到${arr}${date}有哪些列车，各停靠哪些站？`,
    train: (t, date)        => `${t}次列车${date}的停靠情况是怎样的？`,
  },
};

function _qfDateStr(d) {
  if (!d) return "";
  const today    = new Date().toISOString().slice(0, 10);
  const tomorrow = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
  if (d === today)    return "今天";
  if (d === tomorrow) return "明天";
  return d;
}

let _qfActiveType = "listing";
let _qfActiveTab  = "route";

// ── Geolocation / current-city detection ─────────────────────────────────────
let _geoCity   = null;   // detected city name (bare, e.g. "北京")
let _geoStatus = "idle"; // "idle" | "loading" | "ready" | "error"

function _updateGeoBar() {
  const bar  = $("#qf-geo-bar");
  const text = $("#qf-geo-text");
  if (!bar || !text) return;
  bar.classList.remove("geo-ready", "geo-loading", "geo-error");
  if (_geoStatus === "idle") {
    text.innerHTML = `<a id="qf-geo-trigger" href="#">获取当前城市</a>`;
    $("#qf-geo-trigger").addEventListener("click", e => { e.preventDefault(); _detectCity(); });
  } else if (_geoStatus === "loading") {
    bar.classList.add("geo-loading");
    text.textContent = "定位中…";
  } else if (_geoStatus === "ready") {
    bar.classList.add("geo-ready");
    text.innerHTML = `当前城市：<span class="qf-geo-city">${_geoCity}</span>`;
    ["btn-loc-dep", "btn-loc-arr"].forEach(id => {
      const btn = $(`#${id}`);
      if (btn) { btn.removeAttribute("disabled"); btn.title = `填入"${_geoCity}"`; }
    });
  } else {
    bar.classList.add("geo-error");
    text.innerHTML = `定位失败 — <a id="qf-geo-trigger" href="#">重试</a>`;
    $("#qf-geo-trigger").addEventListener("click", e => { e.preventDefault(); _detectCity(); });
  }
}

async function _detectCity() {
  if (!navigator.geolocation) {
    _geoStatus = "error"; _updateGeoBar(); return;
  }
  _geoStatus = "loading"; _updateGeoBar();
  navigator.geolocation.getCurrentPosition(
    async pos => {
      try {
        const { latitude: lat, longitude: lon } = pos.coords;
        const url = `https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lon}&format=json&accept-language=zh-CN&zoom=8`;
        const res  = await fetch(url);
        const data = await res.json();
        const parts    = (data.display_name || "").split(", ");
        const cityPart = parts.find(p => /市$/.test(p)) || (data.address||{}).state || "";
        _geoCity = cityPart.replace(/市$/, "");
        _geoStatus = _geoCity ? "ready" : "error";
      } catch (_) {
        _geoStatus = "error";
      }
      _updateGeoBar();
    },
    _err => { _geoStatus = "error"; _updateGeoBar(); },
    { timeout: 8000, maximumAge: 300000 }
  );
}

function _qfOpen() {
  const today = new Date().toISOString().slice(0, 10);
  const rd = $("#qf-route-date"), td = $("#qf-train-date");
  if (rd && !rd.value) rd.value = today;
  if (td && !td.value) td.value = today;
  $("#quick-fill-popup").classList.remove("hidden");
  $("#quick-fill-btn").classList.add("active");
  // Focus first text input of the active tab
  const activeContent = _qfActiveTab === "route" ? $("#qf-tab-route") : $("#qf-tab-train");
  const first = activeContent && activeContent.querySelector("input[type=text]");
  if (first) setTimeout(() => first.focus(), 50);
}

function _qfClose() {
  $("#quick-fill-popup").classList.add("hidden");
  $("#quick-fill-btn").classList.remove("active");
}

function _qfSelectFn(qtype) {
  _qfActiveType = qtype;
  $$(".qf-fn-btn").forEach(b => b.classList.toggle("active", b.dataset.qtype === qtype));
}

function _qfSelectTab(tab) {
  _qfActiveTab = tab;
  $$(".qf-tab").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  $("#qf-tab-route").classList.toggle("hidden", tab !== "route");
  $("#qf-tab-train").classList.toggle("hidden", tab !== "train");
  const activeContent = tab === "route" ? $("#qf-tab-route") : $("#qf-tab-train");
  const first = activeContent && activeContent.querySelector("input[type=text]");
  if (first) first.focus();
}

function _qfFill() {
  const def = QF_TYPES[_qfActiveType];
  if (!def) return;

  let text = "";
  if (_qfActiveTab === "route") {
    const dep  = $("#qf-dep").value.trim();
    const arr  = $("#qf-arr").value.trim();
    const date = _qfDateStr($("#qf-route-date").value);
    let hasError = false;
    [["#qf-dep", dep], ["#qf-arr", arr]].forEach(([sel, val]) => {
      if (!val) {
        const el = $(sel);
        el.classList.add("qf-field-error");
        setTimeout(() => el.classList.remove("qf-field-error"), 800);
        hasError = true;
      }
    });
    if (hasError) return;
    text = def.route(dep, arr, date);
  } else {
    const train = $("#qf-train-no").value.trim();
    const date  = _qfDateStr($("#qf-train-date").value);
    if (!train) {
      const el = $("#qf-train-no");
      el.classList.add("qf-field-error");
      setTimeout(() => el.classList.remove("qf-field-error"), 800);
      return;
    }
    text = def.train(train, date);
  }

  const chatInput = $("#chat-input");
  chatInput.value = text;
  syncInputHeight();
  _qfClose();
  chatInput.focus();
}

// ============================================================
// City Picker
// ============================================================

const _cp = {
  data:         null,   // cached API response
  step:         "province",  // "province" | "city" | "station"
  province:     null,
  city:         null,
  confirmValue: null,
  targetId:     null,   // "qf-dep" | "qf-arr"
};

async function _cpLoad() {
  if (_cp.data) return;
  const res = await fetch("/api/city_picker");
  _cp.data = await res.json();
}

async function _cpOpen(targetId) {
  _cp.targetId = targetId;

  // Position relative to the trigger button
  const triggerBtn = $(`.btn-city-pick[data-target="${targetId}"]`);
  const rect  = triggerBtn.getBoundingClientRect();
  const W = 320, maxH = 420;
  let left = rect.left;
  let top  = rect.bottom + 6;
  if (left + W > window.innerWidth  - 8) left = window.innerWidth  - W - 8;
  if (left < 8) left = 8;
  if (top  + maxH > window.innerHeight - 8) top = rect.top - maxH - 6;
  if (top  < 8) top = 8;

  const picker = $("#city-picker");
  picker.style.left = left + "px";
  picker.style.top  = top  + "px";
  picker.classList.remove("hidden");
  triggerBtn.classList.add("active");

  if (!_cp.data) {
    $("#cp-content").innerHTML = '<div class="cp-loading">加载中…</div>';
    try { await _cpLoad(); } catch (e) {
      $("#cp-content").innerHTML = '<div class="cp-loading">加载失败，请重试</div>';
      return;
    }
  }
  _cpShowProvince();
}

function _cpClose() {
  $("#city-picker").classList.add("hidden");
  $$(".btn-city-pick").forEach(b => b.classList.remove("active"));
}

function _cpShowProvince() {
  _cp.step = "province";
  _cp.province = null;
  _cp.city     = null;
  _cp.confirmValue = null;

  $("#cp-back-btn").classList.add("cp-hidden-btn");
  $("#cp-breadcrumb").textContent = "选择城市";
  $("#cp-footer").classList.add("hidden");

  const content = $("#cp-content");
  content.innerHTML = "";

  _cp.data.regions.forEach(region => {
    const wrap = document.createElement("div");
    wrap.className = "cp-region";

    const lbl = document.createElement("div");
    lbl.className = "cp-region-label";
    lbl.textContent = region.name;
    wrap.appendChild(lbl);

    const grp = document.createElement("div");
    grp.className = "cp-btn-group";
    region.provinces.forEach(prov => {
      const b = document.createElement("button");
      b.className = "cp-province-btn";
      b.textContent = prov;
      b.addEventListener("click", () => _cpShowCity(prov));
      grp.appendChild(b);
    });
    wrap.appendChild(grp);
    content.appendChild(wrap);
  });
}

function _cpShowCity(province) {
  _cp.step     = "city";
  _cp.province = province;
  _cp.city     = null;
  _cp.confirmValue = null;

  $("#cp-back-btn").classList.remove("cp-hidden-btn");
  $("#cp-breadcrumb").textContent = province;
  $("#cp-footer").classList.add("hidden");

  const content = $("#cp-content");
  content.innerHTML = "";

  const grp = document.createElement("div");
  grp.className = "cp-btn-group";
  (_cp.data.cities[province] || []).forEach(city => {
    const b = document.createElement("button");
    b.className = "cp-city-btn";
    b.textContent = city;
    b.addEventListener("click", () => _cpShowStation(city));
    grp.appendChild(b);
  });
  content.appendChild(grp);
}

function _cpShowStation(city) {
  _cp.step         = "station";
  _cp.city         = city;
  _cp.confirmValue = city;   // default: use city name

  $("#cp-back-btn").classList.remove("cp-hidden-btn");
  $("#cp-breadcrumb").textContent = `${_cp.province} › ${city}`;

  const content = $("#cp-content");
  content.innerHTML = "";

  const stations = _cp.data.stations[city] || [];
  const grp = document.createElement("div");
  grp.className = "cp-btn-group";
  stations.forEach(station => {
    const b = document.createElement("button");
    b.className = "cp-station-btn";
    b.textContent = station;
    // Clicking a specific station auto-confirms immediately
    b.addEventListener("click", () => _cpFill(station));
    grp.appendChild(b);
  });
  content.appendChild(grp);

  // Footer confirms with city name (no specific station selected)
  $("#cp-footer").classList.remove("hidden");
  $("#cp-confirm-btn").textContent = `确认 "${city}"`;
}

function _cpFill(value) {
  const input = $(`#${_cp.targetId}`);
  if (input) input.value = value;
  _cpClose();
}

function _cpBack() {
  if      (_cp.step === "station") _cpShowCity(_cp.province);
  else if (_cp.step === "city")    _cpShowProvince();
}

// ============================================================
// Dynamic suggestions
// ============================================================
async function loadSuggestions() {
  const container = $("#suggestions");
  if (!container) return;
  try {
    const res = await fetch("/api/suggestions");
    const items = await res.json();
    container.innerHTML = "";
    items.forEach(item => {
      const btn = document.createElement("button");
      btn.className = "suggestion-chip";
      btn.dataset.text = item.text;
      btn.textContent = item.label;
      btn.addEventListener("click", () => {
        $("#chat-input").value = btn.dataset.text;
        syncInputHeight();
        sendMessage();
      });
      container.appendChild(btn);
    });
  } catch (e) {
    console.warn("Failed to load suggestions:", e);
  }
}

// ============================================================
// Init
// ============================================================
let _hostConversationRequest = null;
let _hostConversationPumpRunning = false;

async function _pumpHostConversationRequests() {
  if (_hostConversationPumpRunning) return;
  _hostConversationPumpRunning = true;
  try {
    while (_hostConversationRequest) {
      const request = _hostConversationRequest;
      _hostConversationRequest = null;

      if (state.busy) {
        window.chrome.webview.postMessage({
          type: "conversation.error",
          requestId: request.requestId,
          conversationId: request.conversationId,
          message: "当前回答仍在生成，请稍后再切换会话。",
        });
        continue;
      }

      try {
        const loaded = await selectConversation(request.conversationId);
        window.chrome.webview.postMessage({
          type: loaded ? "conversation.loaded" : "conversation.error",
          requestId: request.requestId,
          conversationId: request.conversationId,
          message: loaded ? "" : "会话不存在或加载失败。",
        });
      } catch (error) {
        window.chrome.webview.postMessage({
          type: "conversation.error",
          requestId: request.requestId,
          conversationId: request.conversationId,
          message: String(error && error.message ? error.message : error),
        });
      }
    }
  } finally {
    _hostConversationPumpRunning = false;
    if (_hostConversationRequest) _pumpHostConversationRequests();
  }
}

if (window.chrome && window.chrome.webview) {
  window.chrome.webview.addEventListener("message", event => {
    const message = event.data || {};
    if (message.type !== "conversation.load") return;
    const conversationId = Number.parseInt(message.conversationId, 10);
    if (!Number.isInteger(conversationId) || conversationId <= 0) return;
    _hostConversationRequest = {
      requestId: String(message.requestId || ""),
      conversationId,
    };
    _pumpHostConversationRequests();
  });
}

document.addEventListener("DOMContentLoaded", async () => {

  const embeddedParams = new URLSearchParams(window.location.search);
  const embeddedConversation = parseInt(embeddedParams.get("conversation") || "", 10);

  // Configure marked
  if (typeof marked !== "undefined") {
    marked.setOptions({ breaks: true, gfm: true });
  }

  // Restore persisted preferences
  try {
    const savedHue = localStorage.getItem("railgpt-hue");
    if (savedHue !== null) state.hue = parseInt(savedHue);

    const savedTheme = localStorage.getItem("railgpt-theme");
    if (savedTheme) setTheme(savedTheme);
    else setHue(state.hue);   // apply default hue even on light theme (no-op visually)

    if (localStorage.getItem("railgpt-left-collapsed") === "1") {
      $("#app").classList.add("left-collapsed");
    }
    if (localStorage.getItem("railgpt-right-collapsed") === "1") {
      $("#app").classList.add("right-collapsed");
    }
  } catch (_) {}

  // Hue slider
  $("#hue-slider").addEventListener("input", e => {
    setHue(parseInt(e.target.value));
  });

  // Sidebar collapse buttons
  $("#sidebar-collapse-btn").addEventListener("click", toggleLeftSidebar);
  $("#observer-collapse-btn").addEventListener("click", toggleRightObserver);

  // New chat
  $("#btn-new-chat").addEventListener("click", newConversation);

  // Send
  $("#send-btn").addEventListener("click", sendMessage);
  $("#chat-input").addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && (e.key === "Enter" || e.keyCode === 13)) {
      e.preventDefault();
      sendMessage();
    }
  });
  $("#chat-input").addEventListener("input", syncInputHeight);

  // Mode selector
  $$(".seg-btn", $("#mode-seg")).forEach(btn => {
    btn.addEventListener("click", () => setMode(btn.dataset.mode));
  });

  // Theme dropdown
  $("#theme-btn").addEventListener("click", toggleThemeDropdown);
  $$(".theme-option").forEach(opt => {
    opt.addEventListener("click", e => {
      e.stopPropagation();
      const theme = opt.dataset.theme;

      // Compute ripple origin from theme-trigger button center
      const btn = $("#theme-btn");
      const rect = btn.getBoundingClientRect();
      const x = Math.round(rect.left + rect.width / 2);
      const y = Math.round(rect.top + rect.height / 2);
      // Compute max radius so the circle always covers the full viewport
      const r = Math.ceil(Math.hypot(
        Math.max(x, window.innerWidth - x),
        Math.max(y, window.innerHeight - y)
      ));
      document.documentElement.style.setProperty("--ripple-x", `${x}px`);
      document.documentElement.style.setProperty("--ripple-y", `${y}px`);
      document.documentElement.style.setProperty("--ripple-r", `${r}px`);

      if (document.startViewTransition) {
        $("#theme-selector").classList.remove("open");
        // Suppress element-level transitions during the ripple to avoid
        // colour changes bleeding through while clip-path is expanding.
        document.documentElement.classList.add("theme-transitioning");
        const vt = document.startViewTransition(() => setTheme(theme));
        vt.finished
          .catch(() => {})
          .finally(() => document.documentElement.classList.remove("theme-transitioning"));
      } else {
        setTheme(theme);
        $("#theme-selector").classList.remove("open");
      }
    });
  });
  // Close dropdown when clicking outside
  document.addEventListener("click", () => {
    $("#theme-selector").classList.remove("open");
    closeSettingsProviderDropdown();
  });

  // Observer panel toggle
  $$(".seg-btn", $("#observer-seg")).forEach(btn => {
    btn.addEventListener("click", () => switchObserverPanel(btn.dataset.panel));
  });

  // Suggestion chips (loaded dynamically)
  await loadSuggestions();

  // Quick fill popup
  $("#quick-fill-btn").addEventListener("click", e => {
    e.stopPropagation();
    if ($("#quick-fill-popup").classList.contains("hidden")) _qfOpen(); else _qfClose();
  });
  $$(".qf-fn-btn").forEach(btn => {
    btn.addEventListener("click", () => _qfSelectFn(btn.dataset.qtype));
  });
  $$(".qf-tab").forEach(btn => {
    btn.addEventListener("click", () => _qfSelectTab(btn.dataset.tab));
  });
  // Enter in any field triggers fill
  $$(".qf-field-input").forEach(input => {
    input.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); _qfFill(); } });
  });
  $("#qf-fill-btn").addEventListener("click", _qfFill);
  // Clicks inside qf-popup stop propagation (keep popup open) but close city picker
  $("#quick-fill-popup").addEventListener("click", e => { e.stopPropagation(); _cpClose(); });
  // Clicks inside city picker stop propagation (keep picker open)
  $("#city-picker").addEventListener("click", e => e.stopPropagation());
  // Clicks anywhere outside close both
  document.addEventListener("click", () => { _qfClose(); _cpClose(); });

  // Geo bar trigger (initial render)
  _updateGeoBar();

  // Location fill buttons
  $$(".btn-loc-fill").forEach(btn => {
    btn.addEventListener("click", e => {
      e.stopPropagation();
      if (_geoCity) $("#" + btn.dataset.target).value = _geoCity;
    });
  });

  // City picker
  $$(".btn-city-pick").forEach(btn => {
    btn.addEventListener("click", e => {
      e.stopPropagation();
      const alreadyOpen = !$("#city-picker").classList.contains("hidden")
                          && _cp.targetId === btn.dataset.target;
      if (alreadyOpen) _cpClose();
      else _cpOpen(btn.dataset.target);
    });
  });
  $("#cp-back-btn").addEventListener("click",    _cpBack);
  $("#cp-close-btn").addEventListener("click",   _cpClose);
  $("#cp-confirm-btn").addEventListener("click", () => _cpFill(_cp.confirmValue));

  // Sidebar search
  $("#sidebar-search-btn").addEventListener("click", e => { e.stopPropagation(); toggleSearchPanel(); });
  $("#search-input").addEventListener("input", () => {
    clearTimeout(_searchTimer);
    _searchTimer = setTimeout(runSearch, 280);
  });
  $("#search-input").addEventListener("keydown", e => {
    if (e.key === "Escape") { toggleSearchPanel(); }
  });
  $$(".scope-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      _searchScope = btn.dataset.scope;
      $$(".scope-btn").forEach(b => b.classList.toggle("active", b === btn));
      if ($("#search-input").value.trim()) runSearch();
    });
  });
  $("#search-clear-btn").addEventListener("click", () => { clearSearch(); $("#search-input").focus(); });
  // Prevent clicks inside search panel from bubbling to document close-handlers
  $("#search-panel").addEventListener("click", e => e.stopPropagation());

  // Settings dialog
  $("#btn-settings").addEventListener("click", () => openSettingsDialog("account"));
  $("#settings-close").addEventListener("click", closeSettingsDialog);
  $("#settings-dialog").addEventListener("click", e => {
    if (e.target === $("#settings-dialog")) closeSettingsDialog();
  });
  $$(".settings-tab").forEach(btn => {
    btn.addEventListener("click", () => switchSettingsTab(btn.dataset.settingsTab));
  });
  $("#settings-save-primary").addEventListener("click", () => saveApiKey("primary"));
  $("#settings-delete-primary").addEventListener("click", () => deleteApiKey("primary"));
  $("#settings-save-thinking").addEventListener("click", () => saveApiKey("thinking"));
  $("#settings-delete-thinking").addEventListener("click", () => deleteApiKey("thinking"));
  $("#api-lock-open-settings").addEventListener("click", () => openSettingsDialog("api"));
  $("#settings-provider-btn").addEventListener("click", e => {
    e.stopPropagation();
    toggleSettingsProviderDropdown();
  });
  $("#settings-provider-dropdown").addEventListener("click", e => e.stopPropagation());
  $("#settings-primary-key-input").addEventListener("keydown", e => {
    if (e.key === "Enter") saveApiKey("primary");
  });
  $("#settings-thinking-key-input").addEventListener("keydown", e => {
    if (e.key === "Enter") saveApiKey("thinking");
  });

  // Rename dialog
  $("#rename-cancel").addEventListener("click", closeRenameDialog);
  $("#rename-confirm").addEventListener("click", confirmRename);
  $("#rename-input").addEventListener("keydown", e => {
    if (e.key === "Enter") confirmRename();
    if (e.key === "Escape") closeRenameDialog();
  });
  $("#rename-dialog").addEventListener("click", e => {
    if (e.target === $("#rename-dialog")) closeRenameDialog();
  });

  // App-name click animation
  initAppNameAnimation();

  // Initial load
  await loadSettingsState();
  await loadConvList();
  if (Number.isInteger(embeddedConversation) && embeddedConversation > 0) {
    await selectConversation(embeddedConversation);
  }
  showHomePanelIfEmpty();
  updateInputAvailability();
});

/* ── App-name click animations ─────────────────────────────────────────── */
function initAppNameAnimation() {
  const span = document.querySelector(".app-name");
  if (!span) return;

  // Store original inner HTML once
  const originalHTML = span.innerHTML;

  const ANIMATIONS = [
    { cls: "anm-spin",      dur: 600 },
    { cls: "anm-explode",   dur: 900 },
    { cls: "anm-bounce",    dur: 700 },
    { cls: "anm-rainbow",   dur: 800 },
    { cls: "anm-jelly",     dur: 650 },
    { cls: "anm-glitch",    dur: 700 },
    { cls: "anm-flipy",     dur: 600 },
    { cls: "anm-pop",       dur: 500 },
    { cls: "anm-scramble",  dur: 650 },
    { cls: "anm-pendulum",  dur: 800 },
  ];

  let lastIdx = -1;
  let busy = false;

  span.addEventListener("click", () => {
    if (busy) return;
    busy = true;

    // Pick a different animation from last time
    let idx;
    do { idx = Math.floor(Math.random() * ANIMATIONS.length); } while (idx === lastIdx);
    lastIdx = idx;
    const { cls, dur } = ANIMATIONS[idx];

    // Wrap every visible character (skip child element nodes) in a char span
    wrapChars(span, cls);

    // For explode: give each char a random trajectory via CSS custom props
    if (cls === "anm-explode") {
      span.querySelectorAll(".app-name-char").forEach(ch => {
        const angle = Math.random() * 2 * Math.PI;
        const dist  = 30 + Math.random() * 60;
        ch.style.setProperty("--tx", `${Math.cos(angle) * dist}px`);
        ch.style.setProperty("--ty", `${Math.sin(angle) * dist}px`);
        ch.style.setProperty("--tr", `${(Math.random() - 0.5) * 540}deg`);
      });
    }

    // Stagger delay per character
    const stagger = cls === "anm-glitch" ? 0 : 45;
    span.querySelectorAll(".app-name-char").forEach((ch, i) => {
      ch.style.animationDelay = `${i * stagger}ms`;
    });

    // Restore original HTML after all animations finish
    const totalDur = dur + (span.querySelectorAll(".app-name-char").length - 1) * stagger;
    setTimeout(() => {
      // Must remove animation class from span itself before restoring innerHTML,
      // because innerHTML = ... only replaces inner content, not the element's own classes.
      ANIMATIONS.forEach(({ cls: c }) => span.classList.remove(c));
      span.innerHTML = originalHTML;
      busy = false;
    }, totalDur + 80);
  });
}

function wrapChars(container, animClass) {
  // Walk child nodes: text nodes → wrap chars; element nodes → keep as-is
  const nodes = Array.from(container.childNodes);
  container.innerHTML = "";
  container.classList.add(animClass);

  // Animation class is on the container; caller is responsible for removing it after restore.
  nodes.forEach(node => {
    if (node.nodeType === Node.TEXT_NODE) {
      [...node.textContent].forEach(ch => {
        if (ch === " ") {
          container.appendChild(document.createTextNode(" "));
        } else {
          const s = document.createElement("span");
          s.className = "app-name-char";
          s.textContent = ch;
          container.appendChild(s);
        }
      });
    } else {
      // e.g. .app-version span — animate as a block unit
      const el = node.cloneNode(true);
      const wrapper = document.createElement("span");
      wrapper.className = "app-name-char";
      wrapper.appendChild(el);
      container.appendChild(wrapper);
    }
  });
}

// The WinUI host owns native RailGo pages. This helper lets agent/tool
// results open a native page when the frontend is embedded in WebView2.
window.openRailGo = function (uri) {
  if (window.chrome && window.chrome.webview) {
    window.chrome.webview.postMessage({ type: "open_railgo", uri: String(uri) });
    return true;
  }
  window.open(String(uri), "_blank");
  return false;
};
