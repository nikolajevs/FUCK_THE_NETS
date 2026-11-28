const state = {
  products: [],
  offers: [],
  stores: [],
  campaignLabels: new Map(),
  campaignOptions: [],
  updates: [],
  catalogues: [],
  selectedStores: new Set(),
  page: 1,
  pageSize: 24,
  pageCount: 1,
};

const productCategories = {
  produce: { label: "Augļi un dārzeņi", order: 1 },
  dairy: { label: "Piens, siers un olas", order: 2 },
  meat: { label: "Gaļa un zivis", order: 3 },
  bakery: { label: "Maize un konditoreja", order: 4 },
  drinks: { label: "Dzērieni", order: 5 },
  sweets: { label: "Saldumi un uzkodas", order: 6 },
  frozen: { label: "Saldētie produkti", order: 7 },
  grocery: { label: "Pārtikas preces", order: 8 },
  care: { label: "Skaistumkopšana un higiēna", order: 9 },
  home: { label: "Mājai un uzkopšanai", order: 10 },
  kidsPets: { label: "Bērniem un mājdzīvniekiem", order: 11 },
  clothing: { label: "Apģērbs un tekstils", order: 12 },
  electronics: { label: "Elektronika un tehnika", order: 13 },
  other: { label: "Citas preces", order: 14 },
};

function normalizeProductText(value) {
  return String(value || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("lv")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function textHasAny(text, expressions) {
  return expressions.some(expression => expression.test(text));
}

function inferProductCategory(product) {
  const name = normalizeProductText([product.name, product.brand, ...(product.aliases || [])].filter(Boolean).join(" "));
  const rules = [
    ["produce", [/\baug\w*/u, /\bdarz\w*/u, /\bkartupel\w*/u, /\btomat\w*/u, /\bgurk\w*/u, /\bpaprik\w*/u, /\bsalat\w*/u, /\bkapost\w*/u, /\bburkan\w*/u, /\bsipol\w*/u, /\babol\w*/u, /\bbanan\w*/u, /\bapelsin\w*/u, /\bcitr\w*/u, /\bavokad\w*/u, /\barbuz\w*/u, /\bbumbier\w*/u, /\bmelon\w*/u, /\bpersik\w*/u, /\bplum\w*/u, /\bvinog\w*/u, /\bmandarin\w*/u, /\blimon(?:s|a|i|u)?\b/u, /\bgreipfrut\w*/u]],
    ["frozen", [/\bsalde\w*/u, /\bfrozen\b/u, /\bgelat\w*/u]],
    ["dairy", [/\bpien\w*/u, /\bsier\w*/u, /\bjogurt\w*/u, /\bbiezpien\w*/u, /\bkrej\w*/u, /\bkefir\w*/u, /\bsviest\w*/u, /\bol\w*/u, /\bmascarpone\b/u, /\bskab\w*\s+krejum\w*/u, /\bkarum\w*/u]],
    ["meat", [/\bgal(?:a|as|u|ai)\w*/u, /\bcukgal\w*/u, /\bliellop\w*/u, /\bvist\w*/u, /\btitar\w*/u, /\bdes(?:a|as|u|in)\w*/u, /\bskink\w*/u, /\bbekon\w*/u, /\bziv\w*/u, /\blas\w*/u, /\bforel\w*/u, /\bgarnel\w*/u, /\bmenc\w*/u, /\btunc\w*/u, /\bkrab\w*/u, /\bikri\w*/u, /\bsard\w*/u]],
    ["bakery", [/\bmaiz\w*/u, /\bbulcin\w*/u, /\bkruasan\w*/u, /\bkuka\w*/u, /\btort\w*/u, /\bkeks\w*/u, /\bcepum\w*/u, /\bvafel\w*/u, /\bpirag\w*/u, /\bdesert\w*/u]],
    ["drinks", [/\bdzerien\w*/u, /\bdzeram\w*/u, /\bsul\w*/u, /\buden\w*/u, /\blimonad\w*/u, /\bkafij\w*/u, /\btej\w*/u, /\bkakao\w*/u, /\benergetisk\w*/u, /\bkvass\w*/u]],
    ["sweets", [/\bsokolad\w*/u, /\bkonfekt\w*/u, /\bcips\w*/u, /\bpopkorn\w*/u, /\bsaldum\w*/u, /\bkoslaj\w*/u, /\bbatonin\w*/u, /\briekst\w*/u, /\bsekl\w*/u, /\bhalv\w*/u, /\bmarcipan\w*/u]],
    ["grocery", [/\bmakaron\w*/u, /\bpast\w*/u, /\bris\w*/u, /\bmilti\w*/u, /\bella(?:s|u)?\b/u, /\bkonserv\w*/u, /\bmerc\w*/u, /\bputraim\w*/u, /\bgrik\w*/u, /\bgar[vw]\w*/u, /\bmajoniz\w*/u, /\bkecup\w*/u, /\bsauc\w*/u, /\bgar[vw]viel\w*/u, /\bmed\w*/u, /\bievar\w*/u, /\bsenes\w*/u, /\bpup\w*/u, /\bzirn\w*/u, /\bsoj\w*/u]],
    ["kidsPets", [/\bkak\w*/u, /\bsun\w*\s+barib\w*/u, /\bdzivniek\w*/u, /\bmaijdzivniek\w*/u, /\bbernu\s+(?:barib\w*|maisijum\w*|partik\w*)/u, /\bautin\w*/u, /\bplen\w*/u, /\blego\b/u, /\brotal\w*/u, /\bkonstru\w*\s+komplekt\w*/u, /\bgalda\s+spel\w*/u]],
    ["clothing", [/\bzek\w*/u, /\bsievies\w*/u, /\bviries\w*/u, /\bkrekl\w*/u, /\bdzemper\w*/u, /\bjak\w*/u, /\bkleit\w*/u, /\bapavi\w*/u, /\bkurp\w*/u, /\bvel\w*/u, /\btekst\w*/u, /\bpidzam\w*/u]],
    ["care", [/\bdus\w*/u, /\bzob\w*/u, /\bsampun\w*/u, /\bmat\w*\s+(?:balzam\w*|kondicion\w*)/u, /\bkrem\w*/u, /\bdezodor\w*/u, /\bkosmet\w*/u, /\bsej\w*\s+(?:krem\w*|mask\w*)/u, /\bskuvek\w*/u, /\bskuves\w*/u, /\bdezinfek\w*/u, /\bintim\w*/u, /\bieliktn\w*/u, /\bpaket\w*\s+higien\w*/u]],
    ["home", [/\bmazg\w*\s+lidzek\w*/u, /\btiris\w*\s+lidzek\w*/u, /\blidzek\w*/u, /\bpapir\w*/u, /\bsvec\w*/u, /\btrauk\w*/u, /\bmaisi\w*/u, /\bvirtuv\w*/u, /\bgult\w*\s+vel\w*/u, /\bdekor\w*/u, /\binterjer\w*/u, /\btualet\w*\s+papir\w*/u, /\bspodr\w*/u, /\bskap\w*/u, /\bglaz\w*/u, /\bkruz\w*/u, /\bkatl\w*/u]],
    ["electronics", [/\bled\b/u, /\baustin\w*/u, /\belektr\w*/u, /\btelefon\w*/u, /\bladet\w*/u, /\bbater\w*/u, /\bkabel\w*/u, /\bteleviz\w*/u, /\bspel\w*\s+(?:konsol\w*|austin\w*)/u, /\bklaviatur\w*/u, /\bpeli\w*\s+(?:dator\w*|bezvadu\w*)/u, /\bprinter\w*/u, /\bmazgajam\w*\s+masin\w*/u]],
  ];
  for (const [key, expressions] of rules) {
    if (textHasAny(name, expressions)) return key;
  }

  const sourceCategories = product.offers.map(offer => normalizeProductText(offer.category)).filter(Boolean);
  if (sourceCategories.some(category => /\bpiena produkt\w*/u.test(category))) return "dairy";
  if (sourceCategories.some(category => /\bgalas produkt\w*/u.test(category))) return "meat";
  if (sourceCategories.some(category => /\bzivju produkt\w*/u.test(category))) return "meat";
  if (sourceCategories.some(category => /\bbakalej\w*/u.test(category))) return "grocery";
  return "other";
}

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
  if (folded.includes("rimi-eveikals") || folded.includes("rimi e-veikals")) {
    return { key: "rimi-eveikals", name: "Rimi e-veikals" };
  }
  if (folded.includes("rimi")) return { key: "rimi", name: "Rimi" };
  if (folded.includes("elvi")) return { key: "elvi", name: "Elvi" };
  if (folded.includes("latts")) return { key: "latts", name: "LaTS" };
  if (folded.includes("vesko")) return { key: "vesko", name: "Vesko" };
  if (folded.includes("aibe")) return { key: "aibe", name: "Aibe" };
  if (folded.includes("citro")) return { key: "citro", name: "Citro" };
  if (folded === "beta" || folded.startsWith("beta ")) return { key: "beta", name: "Beta" };
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

function duplicateOfferName(offer) {
  let name = String(offer.name || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("lv");
  // Maxima booklet OCR sometimes appends the card's ordinal to its title.
  if (String(offer.sourceProductId || "").startsWith("pdf:")) name = name.replace(/\s+0\s*$/, "");
  return name.replace(/[^a-z0-9]+/g, " ").trim().replace(/\s+/g, " ");
}

function fullOfferDuplicateKey(offer) {
  const name = duplicateOfferName(offer);
  if (!name) return null;
  return JSON.stringify([
    normalizedStore(offer.storeName || offer.store).key,
    campaignKey(offer),
    name,
    duplicateOfferName({ name: offer.brand }),
    offer.packageSize || "",
    offer.price ?? null,
    offer.regularPrice ?? null,
    offer.paldiesPrice ?? null,
    offer.priceWithoutPaldies ?? null,
    offerDiscount(offer),
    Boolean(offer.requiresPaldies || offer.requiresCard),
    Boolean(offer.paldis),
    offer.validFrom || "",
    offer.validUntil || "",
    [...(offer.storeFormats || [])].sort().join("|"),
  ]);
}

function removeFullOfferDuplicates(offers) {
  const unique = new Map();
  for (const offer of offers) {
    const key = fullOfferDuplicateKey(offer);
    if (!key) {
      unique.set(Symbol(), offer);
      continue;
    }
    const current = unique.get(key);
    if (!current) {
      unique.set(key, offer);
      continue;
    }

    const currentHasImage = Boolean(current.image);
    const offerHasImage = Boolean(offer.image);
    if (offerHasImage && !currentHasImage) {
      const cleanerName = String(current.name || "").replace(/\s+0\s*$/, "");
      unique.set(key, { ...offer, name: cleanerName || offer.name });
    } else if (!currentHasImage && !offerHasImage && String(offer.name || "").length > String(current.name || "").length) {
      unique.set(key, offer);
    } else if (currentHasImage && offerHasImage && String(offer.name || "").length > String(current.name || "").length) {
      unique.set(key, offer);
    }
  }
  return [...unique.values()];
}

function groupOffers(offers) {
  const groups = new Map();
  for (const offer of removeFullOfferDuplicates(offers)) {
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
    product.categoryKey = inferProductCategory(product);
    product.category = productCategories[product.categoryKey].label;
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

// Offer dates are Latvian calendar days, so "today" must be the Riga date
// regardless of the visitor's own time zone.
function rigaToday() {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en", {
    timeZone: "Europe/Riga",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date()).map(part => [part.type, part.value]));
  return parts.year + "-" + parts.month + "-" + parts.day;
}

// validUntil is the last day an offer is valid (inclusive). Items without a
// date are kept: several feeds publish no per-offer validity.
function isExpired(item, today) {
  const until = /^(\d{4}-\d{2}-\d{2})/.exec(String(item.validUntil || ""));
  return Boolean(until) && until[1] < today;
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
    + escapeHtml(store.key) + '" aria-pressed="' + String(store.key
      ? state.selectedStores.has(store.key)
      : state.selectedStores.size === 0) + '">'
    + escapeHtml(store.name) + '<span class="store-filter-count">' + store.count + '</span></button>').join("");
}

function renderCampaignOptions() {
  const target = document.querySelector("#campaign");
  target.innerHTML = '<option value="">Visi bukleti</option>' + state.campaignOptions.map(option =>
    '<option value="' + escapeHtml(option.value) + '">' + escapeHtml(option.label) + '</option>'
  ).join("");
}

function renderCategoryOptions() {
  const target = document.querySelector("#category");
  const counts = new Map();
  for (const product of state.products) {
    counts.set(product.categoryKey, (counts.get(product.categoryKey) || 0) + 1);
  }
  const categories = Object.entries(productCategories)
    .filter(([key]) => counts.has(key))
    .sort(([, first], [, second]) => first.order - second.order);
  target.innerHTML = '<option value="">Visas grupas</option>' + categories.map(([key, category]) =>
    '<option value="' + escapeHtml(key) + '">' + escapeHtml(category.label) + ' (' + counts.get(key) + ')</option>'
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
  if (state.selectedStores.size && !state.selectedStores.has(offer.storeKey)) return false;
  if (filters.campaign && campaignToken(offer) !== filters.campaign) return false;
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
  if (offer.stale) tags.push('<span class="condition-tag stale">Buklets nav atjaunināts</span>');
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

function offerListMarkup(offers) {
  const groups = new Map();
  offers.forEach((offer, index) => {
    const key = Number.isFinite(offer.price)
      ? "price:" + Math.round(offer.price * 100)
      : "unknown:" + index;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(offer);
  });

  return [...groups.values()].map(group => {
    const visibleOffer = offerVariantMarkup(group[0]);
    if (group.length === 1) return visibleOffer;

    const hiddenOffers = group.slice(1);
    const count = hiddenOffers.length;
    const label = count === 1 ? "piedāvājums" : "piedāvājumi";
    return '<div class="offer-price-group">' + visibleOffer
      + '<details class="offer-more"><summary>Vēl ' + count + ' ' + label + ' par ' + money(group[0].price) + '</summary>'
      + '<div class="offer-more-list">' + hiddenOffers.map(offerVariantMarkup).join("") + '</div></details>'
      + '</div>';
  }).join("");
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
    + '<h3>' + escapeHtml(product.name) + '</h3><span class="product-category">' + escapeHtml(product.category) + '</span>'
    + packageSize + '</div></div>'
    + '<div class="best-price"><div class="best-price-main"><strong>' + (bestPrice === null ? 'Cena nav norādīta' : money(bestPrice)) + '</strong>'
    + '<span>labākā cena · ' + stores.map(([, name]) => escapeHtml(name)).join(" + ") + '</span></div>' + discountBadge + '</div>'
    + '<div class="offer-list-heading"><span>' + product.offers.length + ' ' + (product.offers.length === 1 ? 'piedāvājums' : 'piedāvājumi') + '</span>'
    + '<span>' + stores.length + ' ' + (stores.length === 1 ? 'veikalu tīkls' : 'veikalu tīkli') + '</span></div>'
    + '<div class="offer-list">' + offerListMarkup(product.offers) + '</div>'
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
    category: document.querySelector("#category").value,
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
    if (filters.category && product.categoryKey !== filters.category) continue;
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
  clear.disabled = !filters.query && !state.selectedStores.size && !filters.campaign && !filters.category
    && filters.minPrice === null && filters.maxPrice === null
    && filters.minDiscount === null && !filters.condition && filters.sort === "price";
}

function renderStats() {
  document.querySelector("#statStores").textContent = state.stores.length;
  document.querySelector("#statProducts").textContent = state.products.length.toLocaleString("lv-LV");
  document.querySelector("#statOffers").textContent = state.offers.length.toLocaleString("lv-LV");
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
      + (cat.stale ? '<span class="catalogue-stale">Buklets nav atjaunināts</span>' : "")
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
    ["data/rimi-eveikals-offers.json", "Rimi e-veikals"],
    ["data/elvi-offers.json", "Elvi"],
    ["data/latts-offers.json", "LaTS"],
    ["data/vesko-offers.json", "Vesko"],
    ["data/aibe-offers.json", "Aibe"],
    ["data/citro-offers.json", "Citro"],
    ["data/spar-offers.json", "Spar"],
    ["data/beta-offers.json", "Beta"],
  ];
  const results = await Promise.allSettled(sources.map(([path, store]) => fetchFeed(path, store)));
  const feeds = [];
  const failures = [];
  results.forEach((result, index) => {
    if (result.status === "fulfilled") feeds.push(result.value);
    else failures.push(sources[index][1]);
  });

  const today = rigaToday();
  const allOffers = [];
  const storeMap = new Map();
  for (const feed of feeds) {
    const store = normalizedStore(feed.data.store || feed.fallbackStore);
    storeMap.set(store.key, store);
    addCampaignMetadata(feed, store.key);
    for (const cat of feed.data.catalogues || []) {
      // Lidl, Mego and top! publish the booklet link as `pdf` instead of `source`.
      const source = cat && (cat.source || cat.pdf);
      if (!source || isExpired(cat, today)) continue;
      // Mego booklet names are URL slugs; reuse the campaign label for them.
      const name = store.key === "mego" ? campaignSlugLabel(cat.id || cat.name) : cat.name;
      state.catalogues.push({ ...cat, source, name, storeKey: store.key, storeName: store.name });
    }
    if (feed.data.updatedAt) state.updates.push({ name: store.name, value: feed.data.updatedAt });
    for (const rawOffer of feed.data.offers || []) {
      if (!Number.isFinite(rawOffer.price) || isExpired(rawOffer, today)) continue;
      const rawStore = String(rawOffer.store || "").trim();
      const rawStoreType = rawStore ? normalizedStore(rawStore) : store;
      const location = rawStore && rawStoreType.key !== store.key
        ? rawStore
        : rawOffer.location || rawOffer.city || null;
      allOffers.push({ ...rawOffer, location, storeKey: store.key, storeName: store.name });
    }
  }

  state.offers = allOffers;
  // A chain whose whole feed has expired would only add an empty filter button.
  const storesWithOffers = new Set(allOffers.map(offer => offer.storeKey));
  state.stores = [...storeMap.values()]
    .filter(store => storesWithOffers.has(store.key))
    .sort((a, b) => a.name.localeCompare(b.name, "lv"));
  state.products = groupOffers(allOffers);
  renderCategoryOptions();
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
  renderStats();
  renderCatalogues();
  render();
}

function clearFilters() {
  document.querySelector("#search").value = "";
  document.querySelector("#campaign").value = "";
  document.querySelector("#category").value = "";
  document.querySelector("#minPrice").value = "";
  document.querySelector("#maxPrice").value = "";
  document.querySelector("#discount").value = "";
  document.querySelector("#condition").value = "";
  document.querySelector("#sort").value = "price";
  state.selectedStores.clear();
  renderStoreFilters();
  render();
}

document.querySelector("#storeFilters").addEventListener("click", event => {
  const button = event.target.closest("[data-store]");
  if (!button) return;
  if (!button.dataset.store) {
    state.selectedStores.clear();
  } else if (state.selectedStores.has(button.dataset.store)) {
    state.selectedStores.delete(button.dataset.store);
  } else {
    state.selectedStores.add(button.dataset.store);
  }
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

["search", "campaign", "category", "minPrice", "maxPrice", "discount", "condition", "sort"]
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
