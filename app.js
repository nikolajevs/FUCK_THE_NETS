const state = {
  products: [],
  offers: [],
  stores: [],
  campaignLabels: new Map(),
  campaignOptions: [],
  updates: [],
  catalogues: [],
  selectedStore: "",
  page: 1,
  pageSize: 24,
  pageCount: 1,
};

const money = value => new Intl.NumberFormat("lv-LV", {
  style: "currency",
  currency: "EUR",
}).format(value);

const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
}[char]));

function normalizedStore(value) {
  const name = String(value || "Veikals").trim();
  const folded = name.toLocaleLowerCase("lv");
  if (folded.includes("maxima")) return { key: "maxima", name: "Maxima" };
  if (folded.includes("mego")) return { key: "mego", name: "Mego" };
  if (folded.includes("top")) return { key: "top", name: "Top!" };
  if (folded.includes("lidl")) return { key: "lidl", name: "Lidl" };
  if (folded.includes("rimi")) return { key: "rimi", name: "Rimi" };
  if (folded.includes("elvi")) return { key: "elvi", name: "Elvi" };
  if (folded.includes("latts")) return { key: "latts", name: "LaTS" };
  if (folded.includes("vesko")) return { key: "vesko", name: "Vesko" };
  if (folded.includes("aibe")) return { key: "aibe", name: "Aibe" };
  if (folded.includes("citro")) return { key: "citro", name: "Citro" };
  if (folded.includes("spar")) return { key: "spar", name: "Spar" };
  return { key: folded.replace(/[^a-z0-9]+/g, "-") || "other", name };
}

function campaignKey(offer) {
  return String(offer.campaignId || offer.sourceId || offer.campaign || "");
}

function campaignToken(offer) {
  return normalizedStore(offer.storeName || offer.store).key + "::" + campaignKey(offer);
}

function campaignSlugLabel(value) {
  const known = {
    "kas-labs-sonedel-buklets-nr-49": "Kas labs šonedēļ · buklets Nr. 49",
    "kas-labs-sonedel-buklets-nr-48": "Kas labs šonedēļ · buklets Nr. 48",
    "tikai-3-dienas": "Tikai 3 dienas",
    "brivdienas-jasvin": "Brīvdienas jāsvin",
  };
  return known[value] || value
    .replace(/-nr-(\d+)/g, " · Nr. $1")
    .replace(/sonedel/g, "šonedēļ")
    .replace(/brivdienas/g, "brīvdienas")
    .replace(/jasvin/g, "jāsvin")
    .replace(/-/g, " ")
    .replace(/^\w/, character => character.toLocaleUpperCase("lv"));
}

function campaignLabel(offer) {
  const token = campaignToken(offer);
  return state.campaignLabels.get(token)
    || (offer.campaign && offer.campaign !== campaignKey(offer)
      ? String(offer.campaign)
      : campaignSlugLabel(campaignKey(offer)));
}

function offerDiscount(offer) {
  if (Number.isFinite(offer.discountPercent)) return offer.discountPercent;
  if (Number.isFinite(offer.regularPrice) && Number.isFinite(offer.price) && offer.regularPrice > offer.price) {
    return Math.round((offer.regularPrice - offer.price) / offer.regularPrice * 100);
  }
  return null;
}

function groupOffers(offers) {
  const groups = new Map();
  for (const offer of offers) {
    const identity = String(offer.identityKey || "").trim();
    const key = identity && identity !== "|" && !identity.startsWith("unmatched:")
      ? "identity:" + identity
      : "product:" + (offer.canonicalProductId || offer.productId || offer.offerId);
    let product = groups.get(key);
    if (!product) {
      product = {
        identityKey: identity,
        canonicalProductId: null,
        name: offer.name || "Prece bez nosaukuma",
        brand: offer.brand || null,
        image: offer.image || null,
        packageSize: offer.packageSize || "",
        aliases: [],
        offers: [],
      };
      groups.set(key, product);
    }
    if (offer.name && !product.aliases.includes(offer.name)) product.aliases.push(offer.name);
    if (offer.name && offer.name.length > product.name.length) product.name = offer.name;
    if (!product.brand && offer.brand) product.brand = offer.brand;
    if (!product.image && offer.image) product.image = offer.image;
    if (!product.packageSize && offer.packageSize) product.packageSize = offer.packageSize;
    if (!product.canonicalProductId || offer.storeKey === "maxima") {
      product.canonicalProductId = offer.canonicalProductId || offer.productId || product.canonicalProductId;
    }
    product.offers.push(offer);
  }
  return [...groups.values()].map(product => {
    product.offers.sort((a, b) => (a.price ?? Infinity) - (b.price ?? Infinity));
    return product;
  }).sort((a, b) => a.name.localeCompare(b.name, "lv"));
}

function formatShortDate(value) {
  if (!value) return "";
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value));
  const date = match
    ? new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 12)
    : new Date(value);
  return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat("lv-LV", {
    day: "numeric",
    month: "short",
  }).format(date);
}

function formatDateRange(offer) {
  if (offer.validFrom && offer.validUntil) {
    return formatShortDate(offer.validFrom) + "–" + formatShortDate(offer.validUntil);
  }
  return formatShortDate(offer.validUntil) || "Termiņš nav norādīts";
}

function formatUpdate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("lv-LV", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function safeSource(value) {
  const source = String(value || "");
  return /^https?:\/\//i.test(source) ? source : "#";
}

function renderStoreFilters() {
  const target = document.querySelector("#storeFilters");
  const counts = new Map(state.stores.map(store => [store.key, state.offers.filter(offer => offer.storeKey === store.key).length]));
  const buttons = [{ key: "", name: "Visi tīkli", count: state.offers.length }, ...state.stores.map(store => ({
    ...store,
    count: counts.get(store.key) || 0,
  }))];
  target.innerHTML = buttons.map(store => '<button class="store-filter" type="button" data-store="'
    + escapeHtml(store.key) + '" aria-pressed="' + String(state.selectedStore === store.key) + '">'
    + escapeHtml(store.name) + '<span class="store-filter-count">' + store.count + '</span></button>').join("");
  document.querySelector("#networkBadges").innerHTML = state.stores.map(store =>
    '<span class="network-badge">' + escapeHtml(store.name) + '</span>'
  ).join("");
}

function renderCampaignOptions() {
  const target = document.querySelector("#campaign");
  target.innerHTML = '<option value="">Visi bukleti</option>' + state.campaignOptions.map(option =>
    '<option value="' + escapeHtml(option.value) + '">' + escapeHtml(option.label) + '</option>'
  ).join("");
}

function renderCityOptions() {
  const cities = [...new Set(state.offers.map(offer => offer.city).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b, "lv"));
  document.querySelector("#city").innerHTML = '<option value="">Visas pilsētas</option>' + cities.map(city =>
    '<option value="' + escapeHtml(city) + '">' + escapeHtml(city) + '</option>'
  ).join("");
}

function conditionMatches(offer, filter) {
  const needsCard = Boolean(offer.requiresPaldies || offer.requiresCard);
  const appOnly = Boolean(offer.onlyInApp);
  if (filter === "none") return !needsCard && !appOnly;
  if (filter === "card") return needsCard;
  if (filter === "app") return appOnly;
  return true;
}

function offerSearchText(offer) {
  return [
    offer.storeName,
    offer.location,
    campaignLabel(offer),
    offer.campaign,
    offer.city,
    ...(offer.storeFormats || []),
  ].filter(Boolean).join(" ").toLocaleLowerCase("lv");
}

function matchesOffer(offer, filters) {
  if (state.selectedStore && offer.storeKey !== state.selectedStore) return false;
  if (filters.campaign && campaignToken(offer) !== filters.campaign) return false;
  if (filters.city && offer.city !== filters.city) return false;
  if (filters.minPrice !== null && (!Number.isFinite(offer.price) || offer.price < filters.minPrice)) return false;
  if (filters.maxPrice !== null && (!Number.isFinite(offer.price) || offer.price > filters.maxPrice)) return false;
  if (filters.minDiscount !== null) {
    const discount = offerDiscount(offer);
    if (discount === null || discount < filters.minDiscount) return false;
  }
  return conditionMatches(offer, filters.condition);
}

function offerVariantMarkup(offer) {
  const regular = Number.isFinite(offer.regularPrice) && offer.regularPrice > offer.price
    ? '<del>' + money(offer.regularPrice) + '</del>'
    : "";
  const discount = offerDiscount(offer);
  const discountBadge = discount !== null && discount > 0
    ? '<span class="variant-discount">−' + escapeHtml(discount) + '%</span>'
    : "";
  const store = normalizedStore(offer.storeName || offer.store);
  const tags = [];
  if (offer.requiresPaldies) tags.push('<span class="condition-tag strong">PALDIES karte</span>');
  else if (offer.requiresCard) tags.push('<span class="condition-tag strong">' + escapeHtml(offer.cardName ? offer.cardName + " karte" : "Klienta karte") + '</span>');
  if (offer.onlyInApp) tags.push('<span class="condition-tag strong">Tikai lietotnē</span>');
  if (offer.multiBuy) tags.push('<span class="condition-tag strong">Pērkot ' + escapeHtml(offer.multiBuy) + '</span>');
  if (!tags.length) tags.push('<span class="condition-tag">Bez kartes nosacījuma</span>');
  const formats = Array.isArray(offer.storeFormats) && offer.storeFormats.length
    ? '<span>Veikali ' + escapeHtml(offer.storeFormats.join(", ")) + '</span>'
    : "";
  const location = offer.location || offer.city;
  const locationLabel = location
    ? '<span>Vieta: ' + escapeHtml(location) + '</span>'
    : formats;
  const productSource = safeSource(offer.source || offer.campaignSource);

  return '<article class="offer-variant">'
    + '<div class="variant-top"><span class="retailer-tag ' + escapeHtml(store.key) + '">' + escapeHtml(store.name) + '</span>'
    + '<span class="variant-date">' + escapeHtml(formatDateRange(offer)) + '</span></div>'
    + '<div class="variant-campaign">' + escapeHtml(campaignLabel(offer)) + '</div>'
    + '<div class="variant-price-row"><div class="variant-price"><strong>' + money(offer.price) + '</strong>' + regular + discountBadge + '</div></div>'
    + '<div class="variant-tags">' + tags.join("") + '</div>'
    + '<div class="variant-bottom">' + locationLabel + '<a href="' + escapeHtml(productSource) + '" target="_blank" rel="noopener">Avots ↗</a></div>'
    + '</article>';
}

function renderProduct(product) {
  const prices = product.offers.map(offer => offer.price).filter(Number.isFinite);
  const bestPrice = prices.length ? Math.min(...prices) : null;
  const bestDiscount = product.offers.map(offerDiscount).filter(Number.isFinite);
  const topDiscount = bestDiscount.length ? Math.max(...bestDiscount) : null;
  const stores = [...new Map(product.offers.map(offer => [offer.storeKey, offer.storeName])).entries()];
  const image = product.image
    ? '<img src="' + escapeHtml(product.image) + '" alt="" loading="lazy">'
    : '<span aria-hidden="true">' + escapeHtml((product.name || "%").slice(0, 1).toLocaleUpperCase("lv")) + '</span>';
  const storeTags = stores.map(([key, name]) => '<span class="retailer-tag ' + escapeHtml(key) + '">' + escapeHtml(name) + '</span>').join("");
  const packageSize = product.packageSize ? '<p class="product-meta">' + escapeHtml(product.packageSize) + '</p>' : "";
  const discountBadge = topDiscount !== null && topDiscount > 0
    ? '<span class="saving-badge">Līdz −' + escapeHtml(topDiscount) + '%</span>'
    : '<span class="saving-badge store-count">' + stores.length + ' ' + (stores.length === 1 ? 'tīkls' : 'tīkli') + '</span>';

  return '<article class="card">'
    + '<div class="product-top"><div class="product-thumb">' + image + '</div>'
    + '<div class="product-heading"><div class="product-stores">' + storeTags + '</div>'
    + '<h3>' + escapeHtml(product.name) + '</h3>' + packageSize + '</div></div>'
    + '<div class="best-price"><div class="best-price-main"><strong>' + (bestPrice === null ? 'Cena nav norādīta' : money(bestPrice)) + '</strong>'
    + '<span>labākā cena · ' + stores.map(([, name]) => escapeHtml(name)).join(" + ") + '</span></div>' + discountBadge + '</div>'
    + '<div class="offer-list-heading"><span>' + product.offers.length + ' ' + (product.offers.length === 1 ? 'piedāvājums' : 'piedāvājumi') + '</span>'
    + '<span>' + stores.length + ' ' + (stores.length === 1 ? 'veikalu tīkls' : 'veikalu tīkli') + '</span></div>'
    + '<div class="offer-list">' + product.offers.map(offerVariantMarkup).join("") + '</div>'
    + '</article>';
}

function renderPagination(itemCount) {
  const pagination = document.querySelector("#pagination");
  const controls = document.querySelector("#pageControls");
  pagination.hidden = state.pageCount <= 1;
  if (state.pageCount <= 1) {
    controls.innerHTML = "";
    document.querySelector("#pageSummary").textContent = "";
    return;
  }

  const start = (state.page - 1) * state.pageSize + 1;
  const end = Math.min(state.page * state.pageSize, itemCount);
  document.querySelector("#pageSummary").textContent = "Rāda "
    + start.toLocaleString("lv-LV") + "–" + end.toLocaleString("lv-LV")
    + " no " + itemCount.toLocaleString("lv-LV") + " produktiem";

  const visiblePages = new Set([1, state.pageCount]);
  for (let page = Math.max(2, state.page - 1); page <= Math.min(state.pageCount - 1, state.page + 1); page++) {
    visiblePages.add(page);
  }
  if (state.page <= 4) {
    for (let page = 2; page <= Math.min(state.pageCount - 1, 5); page++) visiblePages.add(page);
  }
  if (state.page >= state.pageCount - 3) {
    for (let page = Math.max(2, state.pageCount - 4); page < state.pageCount; page++) visiblePages.add(page);
  }

  const pages = [...visiblePages].sort((a, b) => a - b);
  const pageButtons = [];
  let previous = 0;
  for (const page of pages) {
    if (previous && page - previous > 1) pageButtons.push('<span class="page-ellipsis" aria-hidden="true">…</span>');
    pageButtons.push('<button class="page-button' + (page === state.page ? ' current' : '')
      + '" type="button" data-page="' + page + '" aria-label="Lapa ' + page + '"'
      + (page === state.page ? ' aria-current="page"' : '') + '>' + page + '</button>');
    previous = page;
  }

  controls.innerHTML = '<button class="page-button page-step" type="button" data-page="prev" aria-label="Iepriekšējā lapa"'
    + (state.page === 1 ? ' disabled' : '') + '>‹</button>'
    + pageButtons.join("")
    + '<button class="page-button page-step" type="button" data-page="next" aria-label="Nākamā lapa"'
    + (state.page === state.pageCount ? ' disabled' : '') + '>›</button>';
}

function activeFilters() {
  const parsePrice = selector => {
    const value = document.querySelector(selector).value.trim();
    if (!value) return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
  };
  const discount = document.querySelector("#discount").value;
  return {
    query: document.querySelector("#search").value.trim().toLocaleLowerCase("lv"),
    campaign: document.querySelector("#campaign").value,
    city: document.querySelector("#city").value,
    minPrice: parsePrice("#minPrice"),
    maxPrice: parsePrice("#maxPrice"),
    minDiscount: discount ? Number(discount) : null,
    condition: document.querySelector("#condition").value,
    sort: document.querySelector("#sort").value,
  };
}

function render({ resetPage = true } = {}) {
  if (resetPage) state.page = 1;
  const filters = activeFilters();
  const products = [];
  for (const product of state.products) {
    const productText = [product.name, product.brand, ...(product.aliases || [])]
      .filter(Boolean).join(" ").toLocaleLowerCase("lv");
    const productMatchesQuery = !filters.query || productText.includes(filters.query);
    const variants = product.offers.filter(offer => {
      if (!matchesOffer(offer, filters)) return false;
      return productMatchesQuery || offerSearchText(offer).includes(filters.query);
    });
    if (!variants.length) continue;
    products.push({ ...product, offers: variants });
  }

  products.sort((a, b) => {
    if (filters.sort === "name") return a.name.localeCompare(b.name, "lv");
    if (filters.sort === "discount") {
      const discountA = Math.max(-1, ...a.offers.map(offerDiscount).filter(Number.isFinite));
      const discountB = Math.max(-1, ...b.offers.map(offerDiscount).filter(Number.isFinite));
      return discountB - discountA || (Math.min(...a.offers.map(offer => offer.price)) - Math.min(...b.offers.map(offer => offer.price)));
    }
    return Math.min(...a.offers.map(offer => offer.price)) - Math.min(...b.offers.map(offer => offer.price));
  });

  const offerCount = products.reduce((sum, product) => sum + product.offers.length, 0);
  state.pageCount = Math.max(1, Math.ceil(products.length / state.pageSize));
  state.page = Math.min(state.page, state.pageCount);
  const firstItem = (state.page - 1) * state.pageSize;
  const pageProducts = products.slice(firstItem, firstItem + state.pageSize);
  document.querySelector("#offers").innerHTML = pageProducts.map(renderProduct).join("");
  renderPagination(products.length);
  document.querySelector("#count").textContent = products.length + " produkti · " + offerCount + " piedāvājumi";

  const empty = document.querySelector("#empty");
  empty.hidden = products.length !== 0;
  const noData = state.offers.length === 0;
  document.querySelector("#emptyTitle").textContent = noData ? "Piedāvājumi nav pieejami" : "Neko neatradām";
  document.querySelector("#emptyText").textContent = noData
    ? "Datu avoti pašlaik nav sasniedzami. Mēģini vēlāk."
    : "Pamēģini mainīt meklējamo frāzi vai noņemt kādu filtru.";
  document.querySelector("#emptyClear").hidden = noData;

  const clear = document.querySelector("#clearFilters");
  clear.disabled = !filters.query && !state.selectedStore && !filters.campaign
    && !filters.city && filters.minPrice === null && filters.maxPrice === null
    && filters.minDiscount === null && !filters.condition && filters.sort === "price";
}

function renderStats() {
  document.querySelector("#statStores").textContent = state.stores.length;
  document.querySelector("#statProducts").textContent = state.products.length.toLocaleString("lv-LV");
  document.querySelector("#statOffers").textContent = state.offers.length.toLocaleString("lv-LV");
  document.querySelector("#lastUpdated").textContent = state.updates.length
    ? state.updates.map(item => item.name + " · " + formatUpdate(item.value)).join("  /  ")
    : "Atjauninājuma laiks nav norādīts";
  document.querySelector("#updated").textContent = state.updates.length
    ? "Datu atjauninājumi: " + state.updates.map(item => item.name + " " + formatUpdate(item.value)).join(" · ")
    : "";
}

function renderCatalogues() {
  const section = document.querySelector("#cataloguesSection");
  const target = document.querySelector("#catalogues");
  if (!section || !target) return;
  const seen = new Set();
  const items = state.catalogues.filter(cat => {
    if (seen.has(cat.source)) return false;
    seen.add(cat.source);
    return true;
  }).sort((a, b) => a.storeName.localeCompare(b.storeName, "lv"));
  section.hidden = items.length === 0;
  target.innerHTML = items.map(cat => {
    const period = cat.validFrom && cat.validUntil
      ? formatShortDate(cat.validFrom) + "–" + formatShortDate(cat.validUntil)
      : (cat.validUntil ? formatShortDate(cat.validUntil) : "");
    return '<a class="catalogue-card" href="' + escapeHtml(safeSource(cat.source)) + '" target="_blank" rel="noopener">'
      + '<span class="retailer-tag ' + escapeHtml(cat.storeKey) + '">' + escapeHtml(cat.storeName) + '</span>'
      + '<span class="catalogue-name">' + escapeHtml(cat.name || "Akciju buklets") + '</span>'
      + (period ? '<span class="catalogue-period">' + escapeHtml(period) + '</span>' : "")
      + '<span class="catalogue-open">Atvērt bukletu ↗</span>'
      + '</a>';
  }).join("");
}

async function fetchFeed(path, fallbackStore) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(path + " returned " + response.status);
  const data = await response.json();
  return { data, fallbackStore };
}

function addCampaignMetadata(feed, storeKey) {
  const data = feed.data;
  for (const item of [...(data.campaigns || []), ...(data.catalogues || [])]) {
    if (!item.id || !item.name) continue;
    const token = storeKey + "::" + item.id;
    const name = storeKey === "mego" ? campaignSlugLabel(item.id) : item.name;
    state.campaignLabels.set(token, name);
  }
}

async function init() {
  const sources = [
    ["data/offers.json", "Maxima"],
    ["data/mego-offers.json", "Mego"],
    ["data/top-offers.json", "Top!"],
    ["data/lidl-offers.json", "Lidl"],
    ["data/rimi-offers.json", "Rimi"],
    ["data/elvi-offers.json", "Elvi"],
    ["data/latts-offers.json", "LaTS"],
    ["data/vesko-offers.json", "Vesko"],
    ["data/aibe-offers.json", "Aibe"],
    ["data/citro-offers.json", "Citro"],
    ["data/spar-offers.json", "Spar"],
  ];
  const results = await Promise.allSettled(sources.map(([path, store]) => fetchFeed(path, store)));
  const feeds = [];
  const failures = [];
  results.forEach((result, index) => {
    if (result.status === "fulfilled") feeds.push(result.value);
    else failures.push(sources[index][1]);
  });

  const allOffers = [];
  const storeMap = new Map();
  for (const feed of feeds) {
    const store = normalizedStore(feed.data.store || feed.fallbackStore);
    storeMap.set(store.key, store);
    addCampaignMetadata(feed, store.key);
    for (const cat of feed.data.catalogues || []) {
      if (cat && cat.source) state.catalogues.push({ ...cat, storeKey: store.key, storeName: store.name });
    }
    if (feed.data.updatedAt) state.updates.push({ name: store.name, value: feed.data.updatedAt });
    for (const rawOffer of feed.data.offers || []) {
      if (!Number.isFinite(rawOffer.price)) continue;
      const rawStore = String(rawOffer.store || "").trim();
      const rawStoreType = rawStore ? normalizedStore(rawStore) : store;
      const location = rawStore && rawStoreType.key !== store.key
        ? rawStore
        : rawOffer.location || rawOffer.city || null;
      allOffers.push({ ...rawOffer, location, storeKey: store.key, storeName: store.name });
    }
  }

  state.offers = allOffers;
  state.stores = [...storeMap.values()].sort((a, b) => a.name.localeCompare(b.name, "lv"));
  state.products = groupOffers(allOffers);
  state.campaignOptions = [...new Map(allOffers.map(offer => [campaignToken(offer), {
    value: campaignToken(offer),
    label: normalizedStore(offer.storeName).name + " · " + campaignLabel(offer),
  }])).values()].sort((a, b) => a.label.localeCompare(b.label, "lv"));

  if (failures.length) {
    const warning = document.querySelector("#dataWarning");
    warning.hidden = false;
    warning.textContent = "Neizdevās ielādēt " + failures.join(" un ") + " datus. Pārējo veikalu piedāvājumi ir pieejami.";
  }

  renderStoreFilters();
  renderCampaignOptions();
  renderCityOptions();
  renderStats();
  renderCatalogues();
  render();
}

function clearFilters() {
  document.querySelector("#search").value = "";
  document.querySelector("#campaign").value = "";
  document.querySelector("#city").value = "";
  document.querySelector("#minPrice").value = "";
  document.querySelector("#maxPrice").value = "";
  document.querySelector("#discount").value = "";
  document.querySelector("#condition").value = "";
  document.querySelector("#sort").value = "price";
  state.selectedStore = "";
  renderStoreFilters();
  render();
}

document.querySelector("#storeFilters").addEventListener("click", event => {
  const button = event.target.closest("[data-store]");
  if (!button) return;
  state.selectedStore = button.dataset.store;
  renderStoreFilters();
  render();
});

document.querySelector("#pagination").addEventListener("click", event => {
  const button = event.target.closest("button[data-page]");
  if (!button || button.disabled) return;
  const requestedPage = button.dataset.page === "prev"
    ? state.page - 1
    : button.dataset.page === "next"
      ? state.page + 1
      : Number(button.dataset.page);
  state.page = Math.max(1, Math.min(requestedPage, state.pageCount));
  render({ resetPage: false });
  const heading = document.querySelector("#results-title");
  heading.focus({ preventScroll: true });
  heading.scrollIntoView({
    block: "start",
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
  });
});

document.querySelector("#offers").addEventListener("error", event => {
  if (event.target instanceof HTMLImageElement) event.target.hidden = true;
}, true);

["search", "campaign", "city", "minPrice", "maxPrice", "discount", "condition", "sort"]
  .forEach(id => document.querySelector("#" + id).addEventListener(id === "search" || id.endsWith("Price") ? "input" : "change", render));

document.querySelector("#clearFilters").addEventListener("click", clearFilters);
document.querySelector("#emptyClear").addEventListener("click", clearFilters);
document.addEventListener("keydown", event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase("en") === "k") {
    event.preventDefault();
    document.querySelector("#search").focus();
  }
});

init().catch(error => {
  console.error(error);
  document.querySelector("#dataWarning").hidden = false;
  document.querySelector("#dataWarning").textContent = "Neizdevās ielādēt piedāvājumus. Mēģini vēlāk.";
  render();
});
