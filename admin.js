const FEED_SOURCES = [
  { file: "offers.json", path: "data/offers.json", store: "Maxima" },
  { file: "mego-offers.json", path: "data/mego-offers.json", store: "Mego" },
  { file: "top-offers.json", path: "data/top-offers.json", store: "Top!" },
  { file: "lidl-offers.json", path: "data/lidl-offers.json", store: "Lidl" },
  { file: "rimi-offers.json", path: "data/rimi-offers.json", store: "Rimi" },
  { file: "rimi-eveikals-offers.json", path: "data/rimi-eveikals-offers.json", store: "Rimi e-veikals" },
  { file: "elvi-offers.json", path: "data/elvi-offers.json", store: "Elvi" },
  { file: "latts-offers.json", path: "data/latts-offers.json", store: "LaTS" },
  { file: "vesko-offers.json", path: "data/vesko-offers.json", store: "Vesko" },
  { file: "aibe-offers.json", path: "data/aibe-offers.json", store: "Aibe" },
  { file: "citro-offers.json", path: "data/citro-offers.json", store: "Citro" },
  { file: "spar-offers.json", path: "data/spar-offers.json", store: "Spar" },
  { file: "beta-offers.json", path: "data/beta-offers.json", store: "Beta" },
];

const state = {
  feeds: new Map(),
  ledger: null,
  selectedKey: "",
  selectedFeed: "all",
  search: "",
  reason: "all",
  sortByStore: false,
  database: null,
  saveTimer: null,
  toastTimer: null,
};

const REASON_LABELS = {
  missing_price: "Нет цены",
  weak_name: "Слабое название",
  price_mismatch: "Цена не совпала",
  merged_name: "Объединённое название",
  low_confidence: "Низкая уверенность",
  percent_only: "Только процент",
  no_discount: "Скидка не указана",
};

const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, character => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[character]));

function openDatabase() {
  if (!("indexedDB" in window)) return Promise.resolve(null);
  return new Promise(resolve => {
    const request = indexedDB.open("atlaides-admin", 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains("feed-drafts")) {
        request.result.createObjectStore("feed-drafts", { keyPath: "file" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => resolve(null);
    request.onblocked = () => resolve(null);
  });
}

function databaseRequest(mode, operation) {
  if (!state.database) return Promise.resolve(null);
  return new Promise((resolve, reject) => {
    const transaction = state.database.transaction("feed-drafts", mode);
    const request = operation(transaction.objectStore("feed-drafts"));
    request.onsuccess = () => resolve(request.result ?? null);
    request.onerror = () => reject(request.error);
    transaction.onabort = () => reject(transaction.error || new Error("Не удалось сохранить локальные данные"));
  });
}

async function getSavedDrafts() {
  try { return await databaseRequest("readonly", store => store.getAll()) || []; }
  catch { return []; }
}

async function persistFeed(feed) {
  if (!state.database) return false;
  try {
    await databaseRequest("readwrite", store => store.put({ file: feed.file, payload: feed.payload, savedAt: new Date().toISOString() }));
    feed.saved = true;
    return true;
  } catch (error) {
    console.error("Could not save local draft", error);
    return false;
  }
}

async function removeSavedDraft(file) {
  if (!state.database) return;
  try { await databaseRequest("readwrite", store => store.delete(file)); }
  catch (error) { console.error("Could not remove local draft", error); }
}

function feedOffers(feed, key) {
  if (!Array.isArray(feed.payload[key])) feed.payload[key] = [];
  return feed.payload[key];
}

function feedStore(feed) {
  const stored = String(feed.payload.store || "").trim();
  if (stored) return stored;
  const known = FEED_SOURCES.find(item => item.file === feed.file);
  if (known) return known.store;
  return feed.file.replace(/(?:-offers)?\.json$/i, "").replace(/[-_]+/g, " ");
}

function feedRows() {
  return [...state.feeds.values()].sort((a, b) => feedStore(a).localeCompare(feedStore(b), "lv"));
}

function queueItems() {
  const rows = [];
  for (const feed of feedRows()) {
    feedOffers(feed, "reviewOffers").forEach((offer, index) => {
      const offerId = String(offer.offerId || "");
      const key = feed.file + "::" + (offerId || "row-" + index);
      rows.push({
        key,
        feed,
        offer,
        index,
        store: feedStore(feed),
        name: String(offer.name || "Без названия"),
        reason: String(offer.reviewReason || ""),
      });
    });
  }
  return rows;
}

function visibleQueue() {
  const query = state.search.trim().toLocaleLowerCase("ru");
  let items = queueItems().filter(item => {
    if (state.selectedFeed !== "all" && item.feed.file !== state.selectedFeed) return false;
    if (state.reason !== "all" && item.reason !== state.reason) return false;
    if (!query) return true;
    return [item.name, item.store, item.reason, item.offer.offerId, item.offer.brand, item.offer.campaign]
      .filter(Boolean).join(" ").toLocaleLowerCase("ru").includes(query);
  });
  items.sort((a, b) => state.sortByStore
    ? a.store.localeCompare(b.store, "lv") || a.name.localeCompare(b.name, "lv")
    : a.name.localeCompare(b.name, "lv") || a.store.localeCompare(b.store, "lv"));
  return items;
}

function getSelectedItem() {
  if (!state.selectedKey) return null;
  return queueItems().find(item => item.key === state.selectedKey) || null;
}

function activeFeedName() {
  if (state.selectedFeed !== "all") {
    const feed = state.feeds.get(state.selectedFeed);
    if (feed) return feedStore(feed);
  }
  return "Обзор";
}

function setView(view) {
  const chosen = view === "moderation" ? "moderation" : "overview";
  document.querySelectorAll("[data-panel]").forEach(panel => panel.classList.toggle("is-visible", panel.dataset.panel === chosen));
  document.querySelectorAll(".nav-link[data-view]").forEach(button => button.classList.toggle("is-active", button.dataset.view === chosen));
  document.querySelector("#crumbCurrent").textContent = chosen === "moderation"
    ? (state.selectedFeed === "all" ? "Модерация" : activeFeedName())
    : "Обзор";
  if (chosen === "moderation") {
    document.querySelector("#queueSearch").focus({ preventScroll: true });
  }
}

function formatCount(value) {
  return Number(value || 0).toLocaleString("ru-RU");
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", year: "numeric" }).format(date);
}

function formatMoney(value) {
  return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value))
    ? new Intl.NumberFormat("lv-LV", { style: "currency", currency: "EUR" }).format(Number(value))
    : "Цена не указана";
}

function dailyLedgerStats() {
  const currentUtcDate = new Date().toISOString().slice(0, 10);
  const budget = 200;
  const hasLedger = state.ledger && Number.isFinite(Number(state.ledger.requests));
  const currentRequests = hasLedger && state.ledger.date === currentUtcDate ? Math.max(0, Number(state.ledger.requests)) : 0;
  return { budget, currentRequests, remaining: Math.max(0, budget - currentRequests), hasLedger, currentUtcDate };
}

function visionRecords() {
  const gemini = [];
  const qwen = [];
  let qwenPending = 0;
  for (const feed of state.feeds.values()) {
    const verifiedOffers = feedOffers(feed, "offers");
    const reviewOffers = feedOffers(feed, "reviewOffers");
    const offers = [...verifiedOffers.map(offer => ({ offer, verified: true })), ...reviewOffers.map(offer => ({ offer, verified: false }))];
    for (const { offer, verified } of offers) {
      const suggestion = offer.reviewEvidence?.visionAssist || null;
      const model = String(suggestion?.model || offer.visionModel || "").toLowerCase();
      const record = { file: feed.file, id: offer.offerId || offer.name, suggestion, offer, verified };
      if (model.includes("gemini")) gemini.push(record);
      if (model.includes("qwen")) qwen.push(record);
    }
    if (feed.file === "offers.json") {
      qwenPending += feedOffers(feed, "reviewOffers").filter(offer => !offer.reviewEvidence?.visionAssist).length;
    }
    if (feed.file === "spar-offers.json") {
      qwenPending += feedOffers(feed, "reviewOffers").filter(offer => !offer.reviewEvidence?.visionAssist && !offer.visionModel).length;
    }
  }
  return { gemini, qwen, qwenPending };
}

function updateAiStats() {
  const ledger = dailyLedgerStats();
  const records = visionRecords();
  document.querySelector("#geminiBudget").textContent = formatCount(ledger.budget);
  document.querySelector("#geminiUsed").textContent = ledger.hasLedger ? formatCount(ledger.currentRequests) : "—";
  document.querySelector("#geminiRemaining").textContent = ledger.hasLedger ? formatCount(ledger.remaining) : "—";
  document.querySelector("#geminiSuggestions").textContent = formatCount(records.gemini.length);
  document.querySelector("#geminiLedgerDate").textContent = state.ledger?.date
    ? `Счётчик обновлён ${formatDate(state.ledger.date)} · дата в UTC`
    : "Файл счётчика недоступен";
  const percent = ledger.budget ? Math.min(100, ledger.currentRequests / ledger.budget * 100) : 0;
  document.querySelector("#geminiProgress").style.width = percent + "%";
  const progress = document.querySelector(".quota-track");
  progress.setAttribute("aria-valuenow", String(ledger.currentRequests));
  progress.setAttribute("aria-valuemax", String(ledger.budget));

  const resolved = records.qwen.filter(record => record.suggestion?.decision === "resolved" || (!record.suggestion?.decision && record.verified)).length;
  const unclear = records.qwen.filter(record => record.suggestion?.decision === "unclear" || (!record.suggestion?.decision && !record.verified)).length;
  document.querySelector("#qwenSuggestions").textContent = formatCount(records.qwen.length);
  document.querySelector("#qwenResolved").textContent = formatCount(resolved);
  document.querySelector("#qwenUnclear").textContent = formatCount(unclear);
  document.querySelector("#qwenPending").textContent = formatCount(records.qwenPending);
  const total = resolved + unclear;
  document.querySelector("#qwenResolvedBar").style.width = total ? (resolved / total * 100) + "%" : "0%";
  document.querySelector("#qwenUnclearBar").style.width = total ? (unclear / total * 100) + "%" : "0%";
  document.querySelector("#aiUpdated").textContent = state.ledger?.date
    ? `Счётчик запросов: ${formatDate(state.ledger.date)} · результаты из загруженных фидов`
    : "Результаты из загруженных файлов проекта";
}

function updateFeedSelectors() {
  const current = state.selectedFeed;
  document.querySelector("#feedFilter").innerHTML = '<option value="all">Все магазины</option>'
    + feedRows().map(feed => `<option value="${escapeHtml(feed.file)}">${escapeHtml(feedStore(feed))}</option>`).join("");
  document.querySelector("#feedFilter").value = state.feeds.has(current) ? current : "all";
  document.querySelector("#feedNav").innerHTML = feedRows().map(feed => {
    const reviewCount = feedOffers(feed, "reviewOffers").length;
    const selected = state.selectedFeed === feed.file;
    return `<button class="feed-link${selected ? " is-active" : ""}" data-feed="${escapeHtml(feed.file)}" data-has-reviews="${reviewCount > 0}" type="button"><span class="feed-dot"></span><span>${escapeHtml(feedStore(feed))}</span><span class="feed-count">${formatCount(reviewCount)}</span></button>`;
  }).join("");
}

function updateSourceTable() {
  const body = document.querySelector("#sourceRows");
  if (!state.feeds.size) {
    body.innerHTML = '<tr><td colspan="5" class="table-loading">Не удалось загрузить файлы. Запустите локальный сервер или импортируйте JSON.</td></tr>';
    return;
  }
  body.innerHTML = feedRows().map(feed => {
    const offerCount = feedOffers(feed, "offers").length;
    const reviewCount = feedOffers(feed, "reviewOffers").length;
    const updated = feed.payload.updatedAt ? formatDate(feed.payload.updatedAt) : "—";
    const status = feed.dirty ? "Есть черновик" : "Загружен";
    const dot = feed.error ? "is-error" : "";
    return `<tr data-select-feed="${escapeHtml(feed.file)}" tabindex="0" role="button" aria-label="Открыть очередь ${escapeHtml(feedStore(feed))}"><td class="table-store"><i class="table-dot ${dot}"></i>${escapeHtml(feedStore(feed))}</td><td>${formatCount(offerCount)}</td><td class="${reviewCount ? "table-pending" : ""}">${formatCount(reviewCount)}</td><td>${escapeHtml(updated)}</td><td><span class="table-status ${feed.error ? "is-error" : ""}">${escapeHtml(feed.error || status)}</span></td></tr>`;
  }).join("");
}

function updateSummary() {
  const all = feedRows();
  const pending = queueItems().length;
  const offers = all.reduce((sum, feed) => sum + feedOffers(feed, "offers").length, 0);
  const dirtyCount = all.filter(feed => feed.dirty).length;
  document.querySelector("#navPending").textContent = pending > 99 ? "99+" : String(pending);
  document.querySelector("#pendingMetric").textContent = formatCount(pending);
  document.querySelector("#offersMetric").textContent = formatCount(offers);
  document.querySelector("#feedsMetric").textContent = `из ${all.length} загруженных источников`;
  document.querySelector("#draftsMetric").textContent = formatCount(dirtyCount);
  document.querySelector("#queueHeadingCount").textContent = formatCount(pending);
  document.querySelector("#exportButton").disabled = dirtyCount === 0;
  document.querySelector("#clearDraftsButton").hidden = dirtyCount === 0;
  const loaded = all.filter(feed => !feed.error).length;
  const sync = document.querySelector("#syncState");
  sync.classList.toggle("is-ready", loaded > 0 && loaded === all.length);
  sync.classList.toggle("is-offline", loaded < all.length);
  sync.innerHTML = `<span class="sync-dot"></span>${loaded === all.length && loaded ? "Данные загружены" : `${loaded} из ${FEED_SOURCES.length} источников`}`;
  document.querySelector("#footerStatus").textContent = state.database
    ? `Локальное хранилище подключено${dirtyCount ? ` · черновиков: ${dirtyCount}` : ""}`
    : "Черновик останется в памяти до закрытия вкладки";
}

function updateQueue() {
  const items = visibleQueue();
  const list = document.querySelector("#queueList");
  document.querySelector("#queueResultCount").textContent = `${formatCount(items.length)} ${items.length === 1 ? "запись" : "записей"}`;
  if (!items.length) {
    list.innerHTML = queueItems().length
      ? '<div class="queue-empty"><strong>Ничего не найдено</strong>Измените поиск или фильтры.</div>'
      : '<div class="queue-empty"><strong>Очередь пуста</strong>Все записи проверены. Новые карточки появятся после обновления данных.</div>';
    if (!getSelectedItem()) state.selectedKey = "";
    return;
  }
  if (!items.some(item => item.key === state.selectedKey)) state.selectedKey = items[0].key;
  list.innerHTML = items.map(item => {
    const selected = item.key === state.selectedKey;
    const reason = REASON_LABELS[item.reason] || item.reason || "Нужна проверка";
    return `<button class="queue-item${selected ? " is-selected" : ""}" type="button" data-item="${escapeHtml(item.key)}" aria-pressed="${selected}">
      <span class="queue-item-top"><span class="queue-store"><i class="feed-dot"></i>${escapeHtml(item.store)}</span><span class="reason-chip">${escapeHtml(reason)}</span></span>
      <span class="queue-item-name">${escapeHtml(item.name)}</span>
      <span class="queue-item-meta"><span>${escapeHtml(item.offer.offerId || "Без ID")}</span><strong>${escapeHtml(formatMoney(item.offer.price))}</strong></span>
    </button>`;
  }).join("");
}

function safeHttpUrl(value) {
  const text = String(value || "").trim();
  try {
    const url = new URL(text, window.location.href);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch { return ""; }
}

function numberInputValue(value) {
  return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value)) ? String(value) : "";
}

function renderEditor() {
  const panel = document.querySelector("#editorPanel");
  const item = getSelectedItem();
  if (!item) {
    panel.innerHTML = '<div class="editor-empty"><div class="editor-empty-icon">✎</div><h2>Выберите предложение</h2><p>Карточка и сведения об источнике появятся здесь.</p></div>';
    return;
  }
  const offer = item.offer;
  const imageUrl = safeHttpUrl(offer.image);
  const evidence = offer.reviewEvidence || {};
  const suggestion = evidence.visionAssist || {};
  const sourceUrl = safeHttpUrl(offer.source || offer.campaignSource);
  const visionNote = [suggestion.productName, suggestion.price !== undefined ? `Цена: ${formatMoney(suggestion.price)}` : "", suggestion.regularPrice !== undefined ? `Обычная цена: ${formatMoney(suggestion.regularPrice)}` : "", suggestion.evidence, suggestion.reason]
    .filter(Boolean).map(escapeHtml).join(" · ");
  const evidenceText = evidence.rawCellText || evidence.text || evidence.extractedText || "В фиде нет текста карточки.";
  const model = suggestion.model || offer.visionModel || "";
  const decision = suggestion.decision ? `Решение: ${suggestion.decision}` : "";
  const confidence = Number.isFinite(Number(suggestion.confidence)) ? `Уверенность: ${Math.round(Number(suggestion.confidence) * 100)}%` : "";
  panel.innerHTML = `<div class="editor-content">
    <div class="editor-header">
      <div class="editor-head-main"><div class="product-image">${imageUrl ? `<img src="${escapeHtml(imageUrl)}" alt="" data-product-image>` : escapeHtml((offer.name || "?").slice(0, 1).toLocaleUpperCase("ru"))}</div><div><div class="editor-store">${escapeHtml(item.store)} · ${escapeHtml(REASON_LABELS[item.reason] || item.reason || "Проверка")}</div><h2>${escapeHtml(offer.name || "Без названия")}</h2></div></div>
      <span class="editor-id" title="${escapeHtml(offer.offerId || "Без ID")}">${escapeHtml(offer.offerId || "Без ID")}</span>
    </div>
    <form class="editor-form" id="offerForm" autocomplete="off">
      <div class="form-row"><label class="form-field wide">Название товара<input name="name" data-field="name" value="${escapeHtml(offer.name || "")}" maxlength="240" placeholder="Название товара" required></label></div>
      <div class="form-row"><label class="form-field">Бренд<input name="brand" data-field="brand" value="${escapeHtml(offer.brand || "")}" maxlength="100" placeholder="Не указан"></label><label class="form-field">Фасовка / размер<input name="packageSize" data-field="packageSize" value="${escapeHtml(offer.packageSize || "")}" maxlength="80" placeholder="Например, 500 г"></label></div>
      <div class="form-row"><label class="form-field">Цена по акции, €<input name="price" data-field="price" type="number" min="0" step="0.01" value="${numberInputValue(offer.price)}" placeholder="Обязательное поле"></label><label class="form-field">Обычная цена, €<input name="regularPrice" data-field="regularPrice" type="number" min="0" step="0.01" value="${numberInputValue(offer.regularPrice)}" placeholder="Не указана"></label></div>
      <div class="form-row"><label class="form-field">Скидка, %<input name="discountPercent" data-field="discountPercent" type="number" min="0" max="100" step="1" value="${numberInputValue(offer.discountPercent)}" placeholder="Рассчитать по ценам"></label><label class="form-field">Акция / буклет<input name="campaign" data-field="campaign" value="${escapeHtml(offer.campaign || "")}" maxlength="160" placeholder="Название акции"></label></div>
      <div class="form-row"><label class="form-field">Действует с<input name="validFrom" data-field="validFrom" type="date" value="${escapeHtml(offer.validFrom || "")}"></label><label class="form-field">Действует до<input name="validUntil" data-field="validUntil" type="date" value="${escapeHtml(offer.validUntil || "")}"></label></div>
      <div class="form-row"><label class="form-field wide">Ссылка на источник<input name="source" data-field="source" type="url" value="${escapeHtml(offer.source || offer.campaignSource || "")}" placeholder="https://…"></label></div>
      <div class="form-checks">
        <label class="check-label"><input type="checkbox" data-field="requiresCard"${offer.requiresCard ? " checked" : ""}>Клиентская карта</label>
        <label class="check-label"><input type="checkbox" data-field="requiresPaldies"${offer.requiresPaldies ? " checked" : ""}>PALDIES</label>
        <label class="check-label"><input type="checkbox" data-field="onlyInApp"${offer.onlyInApp ? " checked" : ""}>Только в приложении</label>
      </div>
      <div class="evidence-box"><div class="evidence-title"><span>ДАННЫЕ ИЗ ИСТОЧНИКА</span>${sourceUrl ? `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener">Открыть источник ↗</a>` : ""}</div><pre class="evidence-text">${escapeHtml(evidenceText)}</pre>${model ? `<div class="evidence-model">${escapeHtml(model)}${decision ? ` · ${escapeHtml(decision)}` : ""}${confidence ? ` · ${escapeHtml(confidence)}` : ""}</div>` : ""}${visionNote ? `<div class="vision-note">Подсказка модели: ${visionNote}</div>` : ""}</div>
      <div class="editor-actions"><div class="editor-action-left"><button class="action-button" type="button" data-action="save">Сохранить черновик</button><button class="action-button action-reject" type="button" data-action="reject">Отклонить</button></div><div class="editor-action-right"><span class="editor-autosave" id="autosaveLabel">${item.feed.dirty ? "Черновик изменён" : "Без изменений"}</span><button class="action-button action-approve" type="button" data-action="approve">✓ Одобрить</button></div></div>
    </form>
  </div>`;
  const image = panel.querySelector("[data-product-image]");
  if (image) image.addEventListener("error", () => { image.remove(); });
}

function markDirty(feed) {
  feed.dirty = true;
  feed.error = "";
  updateSummary();
  updateFeedSelectors();
  updateSourceTable();
  const autosave = document.querySelector("#autosaveLabel");
  if (autosave) autosave.textContent = "Сохранение черновика…";
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(async () => {
    const saved = await persistFeed(feed);
    const label = document.querySelector("#autosaveLabel");
    if (label && getSelectedItem()?.feed === feed) label.textContent = saved ? "Черновик сохранён" : "Не удалось сохранить локально";
    if (!saved) showToast("Изменение останется доступно, пока открыта эта вкладка.");
    updateSummary();
  }, 850);
}

function updateOfferFromInput(input) {
  const item = getSelectedItem();
  if (!item) return;
  const field = input.dataset.field;
  let value;
  if (input.type === "checkbox") value = input.checked;
  else if (input.type === "number") value = input.value.trim() === "" ? null : Number(input.value);
  else value = input.value.trim();
  if (field === "source") {
    item.offer.source = value;
    item.offer.campaignSource = item.offer.campaignSource || value;
  } else {
    item.offer[field] = value;
  }
  if (input.type === "checkbox" || input.type === "number") markDirty(item.feed);
  else {
    item.feed.dirty = true;
    clearTimeout(state.saveTimer);
    state.saveTimer = setTimeout(() => markDirty(item.feed), 850);
  }
  updateQueue();
  updateAiStats();
  const approve = document.querySelector('[data-action="approve"]');
  if (approve) approve.disabled = !String(item.offer.name || "").trim() || !(Number.isFinite(item.offer.price) && item.offer.price > 0);
}

function updateFeedMetadata(feed) {
  const payload = feed.payload;
  const offers = feedOffers(feed, "offers");
  const reviews = feedOffers(feed, "reviewOffers");
  const summary = payload.summary && typeof payload.summary === "object" ? payload.summary : {};
  if (Object.hasOwn(summary, "offerVariants")) summary.offerVariants = offers.length;
  if (Object.hasOwn(summary, "verifiedOffers")) summary.verifiedOffers = offers.length;
  if (Object.hasOwn(summary, "reviewOffers")) summary.reviewOffers = reviews.length;
  if (Object.hasOwn(summary, "uniqueProducts")) summary.uniqueProducts = countProducts(feed);
  payload.summary = summary;
  for (const campaign of [...(payload.campaigns || []), ...(payload.catalogues || [])]) {
    if (!campaign || typeof campaign !== "object") continue;
    const matchingOffers = offers.filter(offer => (campaign.id && offer.campaignId === campaign.id) || (campaign.name && offer.campaign === campaign.name));
    const matchingReviews = reviews.filter(offer => (campaign.id && offer.campaignId === campaign.id) || (campaign.name && offer.campaign === campaign.name));
    if (Object.hasOwn(campaign, "verifiedOffers")) campaign.verifiedOffers = matchingOffers.length;
    if (Object.hasOwn(campaign, "reviewOffers")) campaign.reviewOffers = matchingReviews.length;
  }
}

// Feeds no longer publish a products array (the site groups offers itself),
// so only the unique-product count in the summary is kept up to date.
function countProducts(feed) {
  const keys = new Set();
  for (const offer of feedOffers(feed, "offers")) {
    const identity = String(offer.identityKey || "").trim();
    keys.add(identity && identity !== "|" && !identity.startsWith("unmatched:")
      ? "identity:" + identity
      : "product:" + (offer.canonicalProductId || offer.productId || offer.offerId));
  }
  return keys.size;
}

function dropProducts(feed) {
  delete feed.payload.products;
}

function setSelectionAfterAction() {
  state.selectedKey = "";
  updateAll();
  const first = visibleQueue()[0];
  state.selectedKey = first?.key || "";
  updateQueue();
  renderEditor();
}

function approveSelected() {
  const item = getSelectedItem();
  if (!item) return;
  const offer = item.offer;
  if (!String(offer.name || "").trim()) {
    showToast("Добавьте название товара перед одобрением.");
    document.querySelector('[data-field="name"]')?.focus();
    return;
  }
  if (!Number.isFinite(offer.price) || offer.price <= 0) {
    showToast("Укажите цену больше нуля перед одобрением.");
    document.querySelector('[data-field="price"]')?.focus();
    return;
  }
  if (Number.isFinite(offer.regularPrice) && offer.regularPrice <= offer.price) {
    showToast("Обычная цена должна быть выше акционной или оставьте поле пустым.");
    document.querySelector('[data-field="regularPrice"]')?.focus();
    return;
  }
  const feed = item.feed;
  delete offer.reviewReason;
  feedOffers(feed, "offers").push(offer);
  feed.payload.reviewOffers.splice(item.index, 1);
  dropProducts(feed);
  updateFeedMetadata(feed);
  markDirty(feed);
  setSelectionAfterAction();
  showToast("Предложение одобрено и добавлено в проверенные.");
}

function rejectSelected() {
  const item = getSelectedItem();
  if (!item) return;
  const feed = item.feed;
  if (!Array.isArray(feed.payload.rejectedOffers)) feed.payload.rejectedOffers = [];
  const rejected = { ...item.offer, rejectedAt: new Date().toISOString() };
  feed.payload.rejectedOffers.push(rejected);
  feed.payload.reviewOffers.splice(item.index, 1);
  updateFeedMetadata(feed);
  markDirty(feed);
  setSelectionAfterAction();
  showToast("Предложение отклонено и сохранено в rejectedOffers.");
}

function saveCurrentDraft() {
  const item = getSelectedItem();
  if (!item) return;
  clearTimeout(state.saveTimer);
  persistFeed(item.feed).then(saved => {
    const label = document.querySelector("#autosaveLabel");
    if (label) label.textContent = saved ? "Черновик сохранён" : "Не удалось сохранить локально";
    updateSummary();
    showToast(saved ? "Черновик сохранён в этом браузере." : "Сохранение не удалось. Скачайте изменённый JSON.");
  });
}

function updateAll() {
  updateFeedSelectors();
  updateSummary();
  updateAiStats();
  updateSourceTable();
  updateQueue();
  renderEditor();
}

function showToast(message) {
  const toast = document.querySelector("#toast");
  toast.textContent = message;
  toast.classList.add("is-visible");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => toast.classList.remove("is-visible"), 3300);
}

const CRC32_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let value = 0; value < 256; value++) {
    let crc = value;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
    table[value] = crc >>> 0;
  }
  return table;
})();

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) crc = (crc >>> 8) ^ CRC32_TABLE[(crc ^ byte) & 0xff];
  return (crc ^ 0xffffffff) >>> 0;
}

function zipStored(files) {
  const encoder = new TextEncoder();
  const localParts = [];
  const centralParts = [];
  let localSize = 0;
  const date = new Date();
  const dosTime = (date.getHours() << 11) | (date.getMinutes() << 5) | Math.floor(date.getSeconds() / 2);
  const dosDate = ((date.getFullYear() - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate();

  for (const file of files) {
    const name = encoder.encode(file.name);
    const data = encoder.encode(file.content);
    const checksum = crc32(data);
    const local = new Uint8Array(30 + name.length);
    const localView = new DataView(local.buffer);
    localView.setUint32(0, 0x04034b50, true);
    localView.setUint16(4, 20, true);
    localView.setUint16(6, 0x0800, true);
    localView.setUint16(8, 0, true);
    localView.setUint16(10, dosTime, true);
    localView.setUint16(12, dosDate, true);
    localView.setUint32(14, checksum, true);
    localView.setUint32(18, data.length, true);
    localView.setUint32(22, data.length, true);
    localView.setUint16(26, name.length, true);
    localView.setUint16(28, 0, true);
    local.set(name, 30);
    localParts.push(local, data);

    const central = new Uint8Array(46 + name.length);
    const centralView = new DataView(central.buffer);
    centralView.setUint32(0, 0x02014b50, true);
    centralView.setUint16(4, 20, true);
    centralView.setUint16(6, 20, true);
    centralView.setUint16(8, 0x0800, true);
    centralView.setUint16(10, 0, true);
    centralView.setUint16(12, dosTime, true);
    centralView.setUint16(14, dosDate, true);
    centralView.setUint32(16, checksum, true);
    centralView.setUint32(20, data.length, true);
    centralView.setUint32(24, data.length, true);
    centralView.setUint16(28, name.length, true);
    centralView.setUint16(30, 0, true);
    centralView.setUint16(32, 0, true);
    centralView.setUint16(34, 0, true);
    centralView.setUint16(36, 0, true);
    centralView.setUint32(38, 0, true);
    centralView.setUint32(42, localSize, true);
    central.set(name, 46);
    centralParts.push(central);
    localSize += local.length + data.length;
  }

  const centralSize = centralParts.reduce((sum, part) => sum + part.length, 0);
  const end = new Uint8Array(22);
  const endView = new DataView(end.buffer);
  endView.setUint32(0, 0x06054b50, true);
  endView.setUint16(4, 0, true);
  endView.setUint16(6, 0, true);
  endView.setUint16(8, files.length, true);
  endView.setUint16(10, files.length, true);
  endView.setUint32(12, centralSize, true);
  endView.setUint32(16, localSize, true);
  endView.setUint16(20, 0, true);
  return new Blob([...localParts, ...centralParts, end], { type: "application/zip" });
}

function downloadFile(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

function exportChanges() {
  const changed = feedRows().filter(feed => feed.dirty);
  if (!changed.length) return;
  const files = changed.map(feed => ({ name: feed.file.replace(/[\\/]/g, "_"), content: JSON.stringify(feed.payload, null, 2) + "\n" }));
  if (files.length === 1) {
    downloadFile(new Blob([files[0].content], { type: "application/json;charset=utf-8" }), files[0].name);
    showToast(`Скачан ${files[0].name}. Поместите файл в папку data/.`);
    return;
  }
  const archive = zipStored(files);
  downloadFile(archive, `atlaides-admin-export-${new Date().toISOString().slice(0, 10)}.zip`);
  showToast(`Скачан архив с ${files.length} изменёнными фидами. JSON-файлы находятся внутри data/.`);
}

async function clearDrafts() {
  const changed = feedRows().filter(feed => feed.dirty);
  if (!changed.length) return;
  const confirmed = window.confirm(`Удалить ${changed.length} локальных черновика(ов) из этого браузера? Нескачанные изменения будут потеряны.`);
  if (!confirmed) return;
  for (const feed of changed) await removeSavedDraft(feed.file);
  window.location.reload();
}

function normalizeImportedPayload(payload, file) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("Ожидается JSON-объект фида.");
  if (!Array.isArray(payload.offers) || !Array.isArray(payload.reviewOffers)) {
    throw new Error("В файле должны быть массивы offers и reviewOffers.");
  }
  return {
    file,
    payload,
    dirty: true,
    saved: false,
    error: "",
  };
}

async function importFiles(files) {
  let imported = 0;
  for (const file of files) {
    if (!file.name.toLowerCase().endsWith(".json")) continue;
    try {
      const payload = JSON.parse(await file.text());
      const feed = normalizeImportedPayload(payload, file.name);
      state.feeds.set(file.name, feed);
      await persistFeed(feed);
      imported++;
    } catch (error) {
      showToast(`${file.name}: ${error.message || "не удалось прочитать JSON"}`);
    }
  }
  if (imported) {
    state.selectedFeed = "all";
    state.selectedKey = "";
    updateAll();
    showToast(`Импортировано файлов: ${imported}. Данные доступны для модерации.`);
  }
}

async function loadFeeds() {
  const saved = await getSavedDrafts();
  const savedByName = new Map(saved.map(item => [item.file, item]));
  const loadedNames = new Set();
  const results = await Promise.allSettled(FEED_SOURCES.map(async source => {
    const cached = savedByName.get(source.file);
    if (cached?.payload) {
      state.feeds.set(source.file, { file: source.file, payload: cached.payload, dirty: true, saved: true, error: "" });
      loadedNames.add(source.file);
      return;
    }
    try {
      const response = await fetch(source.path, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      const feed = normalizeImportedPayload(payload, source.file);
      feed.dirty = false;
      state.feeds.set(source.file, feed);
      loadedNames.add(source.file);
    } catch (error) {
      state.feeds.set(source.file, { file: source.file, payload: { store: source.store, offers: [], reviewOffers: [] }, dirty: false, saved: false, error: "Нет доступа" });
      console.warn(`Could not load ${source.path}`, error);
    }
  }));
  void results;
  for (const record of saved) {
    if (!record?.file || loadedNames.has(record.file) || !record.payload) continue;
    state.feeds.set(record.file, { file: record.file, payload: record.payload, dirty: true, saved: true, error: "" });
  }
  try {
    const response = await fetch("data/gemini-usage.json", { cache: "no-store" });
    if (response.ok) state.ledger = await response.json();
  } catch (error) {
    console.warn("Could not load Gemini request ledger", error);
  }
  document.querySelector("#todayLabel").textContent = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", year: "numeric" }).format(new Date());
  updateAll();
  const loaded = [...state.feeds.values()].filter(feed => !feed.error).length;
  if (!loaded) showToast("Запустите сайт через локальный сервер или импортируйте JSON-фиды.");
}

document.querySelectorAll(".nav-link[data-view]").forEach(button => button.addEventListener("click", () => setView(button.dataset.view)));
document.querySelectorAll("[data-open-view]").forEach(button => button.addEventListener("click", () => setView(button.dataset.openView)));
document.querySelector("#feedNav").addEventListener("click", event => {
  const button = event.target.closest("[data-feed]");
  if (!button) return;
  state.selectedFeed = button.dataset.feed;
  state.selectedKey = "";
  document.querySelector("#feedFilter").value = state.selectedFeed;
  setView("moderation");
  updateFeedSelectors();
  updateQueue();
  renderEditor();
});
document.querySelector("#feedFilter").addEventListener("change", event => {
  state.selectedFeed = event.target.value;
  state.selectedKey = "";
  document.querySelector("#crumbCurrent").textContent = state.selectedFeed === "all" ? "Модерация" : activeFeedName();
  updateFeedSelectors();
  updateQueue();
  renderEditor();
});
document.querySelector("#reasonFilter").addEventListener("change", event => {
  state.reason = event.target.value;
  state.selectedKey = "";
  updateQueue();
  renderEditor();
});
document.querySelector("#queueSearch").addEventListener("input", event => {
  state.search = event.target.value;
  state.selectedKey = "";
  updateQueue();
  renderEditor();
});
document.querySelector("#sortQueueButton").addEventListener("click", event => {
  state.sortByStore = !state.sortByStore;
  event.currentTarget.title = state.sortByStore ? "Сортировать по названию" : "Сортировать по магазину";
  updateQueue();
  renderEditor();
});
document.querySelector("#queueList").addEventListener("click", event => {
  const button = event.target.closest("[data-item]");
  if (!button) return;
  state.selectedKey = button.dataset.item;
  updateQueue();
  renderEditor();
});
document.querySelector("#editorPanel").addEventListener("input", event => {
  if (event.target.matches("[data-field]")) updateOfferFromInput(event.target);
});
document.querySelector("#editorPanel").addEventListener("change", event => {
  if (event.target.matches('input[type="checkbox"][data-field]')) updateOfferFromInput(event.target);
});
document.querySelector("#editorPanel").addEventListener("submit", event => event.preventDefault());
document.querySelector("#editorPanel").addEventListener("click", event => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  if (button.dataset.action === "approve") approveSelected();
  if (button.dataset.action === "reject") rejectSelected();
  if (button.dataset.action === "save") saveCurrentDraft();
});
document.querySelector("#exportButton").addEventListener("click", exportChanges);
document.querySelector("#clearDraftsButton").addEventListener("click", clearDrafts);
document.querySelector("#importButton").addEventListener("click", () => document.querySelector("#fileInput").click());
document.querySelector("#fileInput").addEventListener("change", event => {
  importFiles([...event.target.files]);
  event.target.value = "";
});
document.querySelector("#refreshButton").addEventListener("click", () => window.location.reload());
document.querySelector("#sourceRows").addEventListener("click", event => {
  const row = event.target.closest("[data-select-feed]");
  if (!row) return;
  state.selectedFeed = row.dataset.selectFeed;
  state.selectedKey = "";
  updateFeedSelectors();
  setView("moderation");
  updateQueue();
  renderEditor();
});
document.querySelector("#sourceRows").addEventListener("keydown", event => {
  if ((event.key === "Enter" || event.key === " ") && event.target.matches("[data-select-feed]")) {
    event.preventDefault();
    event.target.click();
  }
});
document.addEventListener("keydown", event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    setView("moderation");
    document.querySelector("#queueSearch").focus();
  }
});

async function init() {
  state.database = await openDatabase();
  await loadFeeds();
}

init().catch(error => {
  console.error(error);
  showToast("Не удалось загрузить панель. Импортируйте файлы JSON вручную.");
});
