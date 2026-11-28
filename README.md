# Latvia supermarket offers aggregator

Static site and collectors for promotional offers from Latvian supermarket chains.

Currently supported:

| Store   | Scraper                    | Data file               | Status                        |
|---------|----------------------------|-------------------------|-------------------------------|
| Maxima  | `scripts/scrape_maxima.py` | `data/offers.json`      | Production (daily GH Actions) |
| Mego    | `scripts/scrape_mego.py`   | `data/mego-offers.json` | Production (PDF booklets)     |
| Vesko   | `scripts/scrape_vesko.py`  | `data/vesko-offers.json`| Production (PDF booklet)      |
| Aibe    | `scripts/scrape_aibe.py`   | `data/aibe-offers.json` | Production (SSR cards)        |
| Citro   | `scripts/scrape_citro.py`  | `data/citro-offers.json`| Production (PDF newspaper)    |
| Spar    | `scripts/scrape_spar.py`   | `data/spar-offers.json` | Production (vision prices)    |
| Beta    | `scripts/scrape_beta.py`  | `data/beta-offers.json` | Catalogue link (monthly)      |
| Top!    | `scripts/scrape_top.py`    | `data/top-offers.json`  | Production (daily GH Actions) |
| Lidl    | `scripts/scrape_lidl.py`   | `data/lidl-offers.json` | Initial (PDF booklets)        |
| Rimi    | `scripts/scrape_rimi_booklets.py` | `data/rimi-offers.json` | Production (daily GH Actions) |
| Rimi e-veikals | `scripts/scrape_rimi.py` | `data/rimi-eveikals-offers.json` | Production (daily GH Actions) |
| Elvi    | `scripts/scrape_elvi.py`   | `data/elvi-offers.json` | Production (daily GH Actions) |
| LaTS    | `scripts/scrape_latts.py`  | `data/latts-offers.json`| Production (daily GH Actions) |

---

## Maxima

Collects:

- Weekly offers — https://www.maxima.lv/piedavajumi/
- Well Done Premium — https://www.maxima.lv/premium
- Full PALDIES-card catalogue (AJAX pagination)
- All active brochures from https://www.maxima.lv/bukleti/ (PDF + store scope)

PDF parsing uses PyMuPDF with word coordinates, column splitting, PALDIES price precedence, and a review queue for ambiguous cards. Product identity is conservative (normalized tokens + package size, no fuzzy merge).

When a newly published brochure contains ambiguous product cards, the review load is split between two vision models:

1. **Gemini Flash-Lite first pass** (`scripts/gemini_pdf_review.py`) — new card crops go to the Gemini API (`gemini-3.5-flash-lite` by default; 2.5 Flash-Lite is closed to new API keys). Paced to ~13 RPM, capped per run (`GEMINI_MAX_REQUESTS`, default 40) and per day (`GEMINI_DAILY_BUDGET`, default 200, ledger in `data/gemini-usage.json`). Requires the `GEMINI_API_KEY` repository secret; without it the step is skipped. Locally a gitignored `gemini.token.txt` in the repo root works too.
2. **Local Qwen3-VL fallback** (`scripts/qwen_pdf_review.py`) — cards Gemini could not resolve (quota exhausted, API errors, `decision=unclear`) are reviewed by the public Qwen3-VL-4B GGUF weights on the runner: GitHub Actions downloads them, builds CPU-only `llama-mtmd-cli`, and processes only the affected card crops. The model and projector are cached; no Hugging Face token is used. If every card was resolved by Gemini, the download/build steps are skipped entirely.

Both layers attach advisory notes to `reviewEvidence.visionAssist` only and never promote prices into the verified feed. If either layer fails, the regular scrape and deployment continue.

```bash
python scripts/scrape_maxima.py
```

---

## Mego

Collects active PDF booklets from https://mego.lv/akcijas-un-piedavajumi/ (WordPress page + DFLIP embeds).

- Discovers current weekly / campaign PDFs and date ranges
- Parses with word coordinates; handles cent-style prices (`129` → €1.29) and multi-column layout
- Flags `requiresCard` / `onlyInApp` when detected
- Ambiguous names go to `reviewOffers`
- Product photos are recovered from the PDF's embedded images (see
  [Booklet product photos](#booklet-product-photos)) into `data/mego-img/`

```bash
python scripts/scrape_mego.py
```

Output: `data/mego-offers.json` (refreshed daily by the same workflow as Maxima).

---

## Vesko, Aibe, Citro and Spar

Four more Latvian chains, each read from its own published source:

- **Vesko** (`veskoveikals.lv`) — the monthly booklet linked from the home
  page (`/booklets/<id>/lv/pdf_download`) has a real text layer in three
  columns. The tallest cent integer in a card is the sale price, the next is
  the struck-through regular price; the printed `-NN%` badge validates the
  pair. Names keep the package size; unit prices, discount badges and the
  duplicated shadow text layer are removed.
- **Aibe** (`aibe.lv/akcijas/`) — server-rendered WordPress cards grouped by
  category, with euro+cent sale price, regular price, discount, photo and a
  `Product, Brand, size` name. The akciju avīze (DearPDF flipbook) is recorded
  as a catalogue link and supplies the campaign year.
- **Citro** (`citro.lv/akcijas/`) — the "Citro Avīze" PDF (the sister-chain
  ELDO avīze and the lottery booklet on the same page are skipped). Cards are
  anchored on the large cent sale price; the ALL-CAPS name sits to the right
  (standard cards) or below (fruit & vegetable cards). Regular-price ranges
  keep their lower bound.
- **Spar** (`spar.lv/akcijas/`) — the DearPDF newspaper keeps names, sizes and
  the validity period as text but renders **prices as images**. Product names
  are reconstructed from the text layer into per-card candidates; each page is
  rendered to PNG and the project's two-layer vision stack reads the price
  badges: **Gemini Flash-Lite** first (shared daily budget ledger with Maxima,
  ≤8 requests/run), then the existing local **Qwen3-VL** llama.cpp build for
  pages Gemini could not resolve (`--qwen-review`). Guards keep it conservative:
  names must come from the text layer, prices are sanity-ranged, the printed
  discount badge must agree with the sale/regular pair, "N for X€" bundle
  totals are divided locally, alcohol is dropped and anything inconsistent goes
  to `reviewOffers`. Without any vision layer it degrades to a catalogue link.

All four drop alcoholic products (matching LaTS/Rimi/top!) and send ambiguous
cards to `reviewOffers`. The site shows a **"Akciju bukleti"** strip listing
every chain's current booklet/avīze with its period, so catalogue-only stores
like Spar stay visible.

```bash
python scripts/scrape_vesko.py
python scripts/scrape_aibe.py
python scripts/scrape_citro.py
python scripts/scrape_spar.py
```

---

## Beta

Beta has no public structured product feed; its current monthly booklet is an
image-only catalogue listed on MazaCena.lv. The scraper reads every page with
Gemini Flash-Lite, then uses the workflow's local Qwen3-VL fallback for pages
Gemini could not resolve. Only products with a legible sale price and high
confidence are published; unclear cards stay in `reviewOffers`.

```bash
python scripts/scrape_beta.py
```

Output: `data/beta-offers.json` (refreshed daily by the same workflow as Maxima;
the shared Gemini daily budget applies).

---

## Top! (etop.lv)

Collects the full promotion catalogue of the top! chain from the public JSON API
behind its Angular storefront — no PDF parsing required:

- `GET https://etop.lv/v1/Promotions/GetActivePromotions` — active booklets
  (weekly `top! buklets` and `Lielo veikalu top! buklets`) with direct PDF links
  and validity periods;
- `POST https://etop.lv/v1/Products/GetPromotionProducts` with
  `{"page": N, "pageSize": 200}` — every discounted product with prices, brand,
  EAN, product image and per-product validity dates (paged until `hasMore=false`).

Price handling:

- regular discounts (`discountType 3`): sale price + the lower bound of the
  regular-price range (`regPriceMin`, prices vary by store format);
- `top! cena` (`discountType 9`): the loyalty price becomes the sale price, the
  non-card price becomes the regular price, and the offer is flagged
  `requiresCard` with `cardName: "mans top!"`;
- everyday low prices (`Zemā cena`, private label) and the lowest-price
  guarantee (`Zemākā cena`) are kept without a crossed-out regular price.

Validity dates are converted from UTC instants to Europe/Riga local dates
(EU DST rules, no external tz database); the API end instant is exclusive.
Alcoholic products are not part of the feed (only 0% beer/cider variants are).
Offers are matched to booklets by local validity dates; `top! cena`, `Zemā cena`
and `Zemākā cena` form their own campaigns.

```bash
python scripts/scrape_top.py
```

Output: `data/top-offers.json` (refreshed daily by the same workflow as Maxima).

---

## Lidl (lidl.lv)

Lidl's Latvian storefront has no structured product feed (the leaflet API returns
`products: []`), so offers are read from the weekly booklet **PDF text layer** with
word coordinates — the same approach as Mego.

- Booklet discovery: the landing page
  `https://www.lidl.lv/c/lv-LV/akciju-bukleti/s10024390` links to every active
  leaflet viewer `/l/lv/akciju-buklets/<slug>/ar/0`.
- Metadata + PDF: `GET https://endpoints.leaflets.schwarz/v4/flyer?flyer_identifier=<slug>&region_id=0&region_code=0`
  returns the booklet name, offer validity dates and a direct `pdfUrl`.

Extraction per page:

- Lidl's sale-price font identifies the actual price separately from large
  crossed-out regular prices; titles are assigned to price anchors by block
  distance, with page-specific mappings for headings shared by several variants;
- leading A/B/C variant markers that leak from the booklet layout are removed;
  pages with a single sale-price anchor are still parsed;
- a small decimal to the **right** of the anchor is the crossed-out regular price;
  unit prices (`1 kg = 16,14 €`) live inside the name block and are ignored;
- `Ar Lidl Plus` near the card sets `requiresCard` with `cardName: "Lidl Plus"`.

Quality gate: only cards with a readable title and a finite sale price are promoted
to `offers`; empty/generic names, oversized title blocks, marketing copy and date
banners go to `reviewOffers` and are **not shown**. Regular
prices are kept only when confidently detected, otherwise `null`, so a discount is
never derived from a unit price. Template pages (`Product name`, `XXX`) are skipped.
Evergreen catalogues with placeholder validity windows longer than a year are
skipped during discovery.

```bash
python scripts/scrape_lidl.py
```

Output: `data/lidl-offers.json` (refreshed daily by the same workflow as Maxima).
Product photos are recovered from the PDF's embedded images into `data/lidl-img/`
(see [Booklet product photos](#booklet-product-photos)).

---

## Booklet product photos

Maxima and top! supply product images through their APIs. Mego and Lidl only
publish PDF booklets, but those PDFs keep a real text layer **and** place each
product photo as a separate embedded image, so clean photos (no printed text) can
be recovered without OCR or vision models.

`scripts/booklet_images.py` matches an embedded image's placement rectangle to a
product card:

- the rectangle must stay inside the card's brochure column (page-wide banners and
  background art span several columns and are rejected);
- its bottom edge must sit at the sale-price block and its top inside the card's
  vertical band;
- tiny logos/badges are rejected by minimum dimensions; the largest qualifying
  image wins.

Matched photos are written to `data/mego-img/` / `data/lidl-img/` as
`<offerId>.jpg|png` and referenced by `offer.image`; files no longer referenced by
the feed are pruned on each run. Cards without a confident match simply keep no
image (the frontend falls back to a letter placeholder).

---

## Rimi (physical stores)

Rimi's official [booklets and offers page](https://www.rimi.lv/akcijas-un-bukleti)
links to the active weekly, Mini and store-specific in-store booklets. The
booklets are hosted in Rimi's iPaper viewer and can be downloaded as PDFs
without a login. Product names and prices are read from the PDFs' positioned
text layer:

- large sale prices anchor each product card; a nearby smaller price is used as
  the regular price;
- products with a clear discount but an incomplete name, or a price without a
  verifiable regular price or discount, are sent to `reviewOffers` instead of
  being shown as confirmed discounts;
- if an active booklet fails to refresh, still-valid offers from its previous
  feed are kept and marked stale; expired offers are dropped;
- alcoholic products are excluded, matching the project's Rimi e-veikals feed.

```bash
python scripts/scrape_rimi_booklets.py
```

Output: `data/rimi-offers.json`. It includes the parsed offers and links to the
source booklets.

---

## Rimi e-veikals (rimi.lv)

Rimi's online store server-renders its full promotion catalogue, so offers are
collected from the HTML listing — no PDF parsing and no login required:

- `GET https://www.rimi.lv/e-veikals/lv/akcijas-piedavajumi?currentPage=N&pageSize=100`
  — every product that currently carries a price label (100 cards per page is
  the server maximum; the last page number is read from the pagination).

Two kinds of promotion cards are recognized:

- **Mans Rimi card price** — the `price-label` block ("Piedāvājums spēkā tikai
  MansRimi kartes lietotājiem") holds the loyalty price, the plain `card__price`
  is the regular price; flagged `requiresCard` with `cardName: "Mans Rimi"`,
  campaign "Mans Rimi cena";
- **Regular discount** — `card__price` is the sale price and `old-price-tag`
  ("Regulārā cena: …") the crossed-out regular price; campaign "Akcija".

Sale prices use dot decimals, regular prices comma decimals — both are
normalized; unit prices are ignored. Alcoholic products are excluded by the
GTM category code embedded in each card (top-level `SH-1` Alkoholiskie dzērieni
and `SH-18` Vīns un šampanietis); non-alcoholic beer/cider/wine (`SH-5-1`)
stay in the feed. The listing has no per-product validity dates, so
`validFrom`/`validUntil` are `null` instead of guessing a booklet period.

```bash
python scripts/scrape_rimi.py
```

Output: `data/rimi-eveikals-offers.json` (refreshed daily by the same workflow as Maxima).

---

## Elvi (elvi.lv)

ELVI publishes its weekly newspaper ("Akcijas avīze") as an image-only issuu
flipbook, but the promoted products are also server-rendered on the site:

- `GET https://elvi.lv/akcijas-avize/` — first batch of product cards plus the
  campaign validity header ("Spēkā no … līdz …"), applied to every offer;
- `POST https://elvi.lv/wp-admin/admin-ajax.php` with `action=loadMore`,
  `offset=N` (steps of 16) and empty `cat` — the remaining cards, same markup,
  until the response only hides the load-more button.

Card variants:

- **default** — struck-through regular price (a range such as `3.19€ - 3.39€`
  keeps its lower bound, like top!) and the sale price; a lone `p.single`
  price is kept without a regular price;
- **two-plus** — per-unit price valid when buying 2+; kept with a `multiBuy`
  field that the site renders as a "Pērkot 2+" condition tag;
- **percent** — only a percentage, no absolute price → `reviewOffers`;
- `tooltip card` / `tooltip card_plus3` icons set `requiresCard` with
  `cardName: "ELVI Klienta karte"` / `"3+ karte"`.

The detail span ("Brand; 400 g; 5,98 €/kg") supplies brand and package size;
the package is appended to the name when missing so identity keeps sizes
apart. Unit prices and deposit notes are stripped. The issuu flipbook is
recorded as a catalogue link (no public PDF, no text layer). ELVI does not
publish alcohol in the online newspaper, so no alcohol filter is needed.

```bash
python scripts/scrape_elvi.py
```

Output: `data/elvi-offers.json` (refreshed daily by the same workflow as Maxima).

---

## LaTS (latts.lv / e-latts.lv)

LaTS publishes its monthly newspaper ("Akciju avīze"), the charity low-price
list ("Zīļuka zemās cenas") and the loyalty-card list ("Ar Draugu karti lētāk!")
as image-only flipbooks on https://www.latts.lv/akciju-avize — the linked PDFs
have no text layer, so they are recorded as catalogue links with the campaign
period (the same approach Elvi uses for its issuu flipbook).

The promoted products themselves are server-rendered by the chain's online
store, which is the structured source:

- `GET https://www.e-latts.lv/products.sale.ppl` — the "Akcijas" listing,
  40 product cards per page, paginated with `?p=N` (highest page linked from
  the first page).

Each card (`div.-oProduct -id="…"`) supplies the full name (`a.-oTitle`, size
and brand already included), the product URL and image, the sale price
(`-price` attribute), the struck-through regular price (`div.-eOld`) and the
category path (`th.-oGroupPath`). The unit price (`-ePerUnit`) and the
refundable container deposit (`-iDepozit`) are never used as the product price.
Alcoholic products (top-level category "Alkoholiskie dzērieni") are dropped;
non-alcoholic beer/cider/wine are kept.

The listing publishes general online sale prices: it carries no per-card
loyalty marker and no per-product dates, so offers keep `requiresCard=False`
and `validFrom/validUntil=None`; the newspaper period is attached to the
catalogue links instead. A lone sale price without a reference price is kept
(the site simply shows no discount percentage). Brand comes from the hidden
`-brand` label, falling back to the trailing name segment ("Product, Brand")
while rejecting package sizes and descriptors such as "sveramas".

```bash
python scripts/scrape_latts.py
```

Output: `data/latts-offers.json` (refreshed daily by the same workflow as Maxima).

---

## Product identity and grouping

Shared logic in `scripts/product_identity.py`:

- Latvian diacritics and punctuation normalized
- Word order / capitalization ignored
- Package sizes normalized (1 kg ≡ 1000 g)
- Different sizes and flavours stay separate

Output shapes:

- `offers` — flat source-level variants
- `products` — grouped by identity
- `reviewOffers` — needs manual check

## Expired offers

The site compares each offer's and booklet's `validUntil` (inclusive last day)
with today's date in Europe/Riga and hides anything that has ended, so a feed
that failed to refresh never shows finished promotions. Items without a
`validUntil` (Rimi e-veikals, LaTS, part of Maxima) are always shown. A chain
whose feed contains no current offers drops out of the store filter.

## Local preview

```bash
python3 -m http.server 8000
```

Open http://localhost:8000

## Admin panel

Open `http://localhost:8000/admin.html` to review ambiguous offers and inspect the
Gemini / Qwen review statistics. Gemini's daily usage comes from
`data/gemini-usage.json` (the configured default budget is 200 requests/day;
the counter uses UTC dates). Qwen runs locally and has no API request quota, so
the panel reports suggestions saved in the feeds and the remaining Maxima/Spar
review queue instead.

The site is static, so edits are stored in the browser's local IndexedDB draft
store. Use **Download changes** to export one edited feed as JSON or several as a
ZIP, then replace the matching files in `data/` and commit them. Imported feeds
can also be edited when the panel is opened without a local server. Approving a
card moves it from `reviewOffers` to `offers`; rejecting it archives it under
`rejectedOffers` in the exported feed. After publishing exported data, use
**Reset** to clear the browser's cached drafts and load the updated feed files.

## Tests

```bash
python -m unittest discover -s tests -v
```

## GitHub Pages

Settings → Pages → source **GitHub Actions**.  
Workflow `.github/workflows/maxima.yml` refreshes every store feed (Maxima, top!, Lidl, Rimi, Rimi e-veikals, Elvi, LaTS, Mego, Vesko, Aibe, Citro, Spar and Beta) daily and deploys.  
Trigger manually: Actions → Update Maxima offers and deploy → Run workflow.
