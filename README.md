# Latvia supermarket offers aggregator

Static site and collectors for promotional offers from Latvian supermarket chains.

Currently supported:

| Store   | Scraper                    | Data file               | Status                        |
|---------|----------------------------|-------------------------|-------------------------------|
| Maxima  | `scripts/scrape_maxima.py` | `data/offers.json`      | Production (daily GH Actions) |
| Mego    | `scripts/scrape_mego.py`   | `data/mego-offers.json` | Initial (PDF booklets)        |
| Top!    | `scripts/scrape_top.py`    | `data/top-offers.json`  | Production (daily GH Actions) |

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

```bash
python scripts/scrape_mego.py
```

Output: `data/mego-offers.json`

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

## Price history (Maxima)

Keyed by canonical product identity. Daily point = lowest verified price that day. Review offers are excluded until prices are clean.

## Local preview

```bash
python3 -m http.server 8000
```

Open http://localhost:8000

## Tests

```bash
python -m unittest discover -s tests -v
```

## GitHub Pages

Settings → Pages → source **GitHub Actions**.  
Workflow `.github/workflows/maxima.yml` refreshes Maxima and top! data and deploys.  
Trigger manually: Actions → Update Maxima offers and deploy → Run workflow.
