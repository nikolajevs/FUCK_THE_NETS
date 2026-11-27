# Maxima Latvia offers aggregator

A static website for collecting and comparing Maxima Latvia promotional offers.

## Current collection sources

The daily collector reads two public Maxima campaign pages:

- Weekly offers: \`https://www.maxima.lv/piedavajumi/\`
- Well Done Premium offers: \`https://www.maxima.lv/premium\`

It resolves each campaign's date range and brochure detail page from \`https://www.maxima.lv/bukleti/\`. The campaign detail page is used to verify whether an offer applies in MAXIMA X, XX and/or XXX stores.

The collector currently adds only these two campaign sources. It does not claim that every Maxima campaign or every store-specific offer has been collected.

## Product identity and grouping

Offers are preserved as separate variants, including their campaign, source URL, dates, PALDIES requirement, prices and store formats. Product cards are grouped using a conservative normalized identity:

- Latvian diacritics and punctuation are normalized.
- Word order and capitalization do not affect a match.
- Package sizes are normalized (for example, 1 kg and 1000 g).
- Different package sizes and flavour names remain separate.
- No fuzzy matching is used to merge products automatically.

The output keeps both formats:

- \`offers\`: flat source-level offer variants.
- \`products\`: grouped products, each containing all matching \`offers\` variants.

This allows the same product to appear in more than one campaign without losing the source-level price, PALDIES or store availability details.

## Price history

Price history is keyed by canonical product identity. Each product keeps:

- \`observations\`: one record per source offer and day.
- \`points\`: one daily point using the lowest verified price observed across collected variants.

The previous name-keyed history is migrated into canonical product history by the scraper. The chart therefore shows the lowest verified observed price per day, while the separate offer variants retain campaign-level detail.

The collector does not infer a city, store-specific availability or store format when the official page does not state it. Ambiguous price cards are flagged in \`reviewOffers\` and excluded from verified history.

## Local preview

Run any static HTTP server from the repository root:

\`\`\`bash
python3 -m http.server 8000
\`\`\`

Then open \`http://localhost:8000\`.

## Tests

Run parser and identity tests:

\`\`\`bash
python -m unittest discover -s tests -v
\`\`\`

## GitHub Pages

In repository Settings → Pages, set the build and deployment source to **GitHub Actions**. The workflow \`.github/workflows/maxima.yml\` publishes the static site after a successful data refresh. You can also start it from Actions → Update Maxima offers and deploy → Run workflow.
