# Maxima Latvia offers aggregator

A static website for collecting and comparing Maxima Latvia promotional offers.

## Current collection sources

The daily collector reads three offer sources:

- Weekly offers: \`https://www.maxima.lv/piedavajumi/\`
- Well Done Premium: \`https://www.maxima.lv/premium\`
- The complete PALDIES-card catalogue, selected through the official \`Ar PALDIES karti\` filter on \`https://www.maxima.lv/piedavajumi/\`

The PALDIES collector uses Maxima's own \`/ajax/salesloadmore\` endpoint. It reads the filter ID from the page instead of assuming it is fixed, then downloads all result pages in batches of up to 75 using the endpoint's \`offset\` parameter.

For every run, it checks the server-reported \`totalItems\`, the number of cards on each page, and the uniqueness of each source product ID. If a page is missing, repeats products, or the final number of unique products differs from the server total, the scraper fails rather than publishing a partial PALDIES catalogue.

The weekly and Well Done Premium campaign dates and store scope are resolved from each campaign's own brochure detail page. PALDIES offer dates are read from each individual product card's date interval. The collector does not infer a city, store-specific availability or store format when the official page does not state it.

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
- \`reviewOffers\`: collected cards whose prices or product details need manual review.

This allows the same product to appear in more than one campaign without losing the source-level price, PALDIES requirement or availability details.

## Price history

Price history is keyed by canonical product identity. Each product keeps:

- \`observations\`: one record per source offer and day.
- \`points\`: one daily point using the lowest verified price observed across collected variants.

The previous name-keyed history is migrated into canonical product history by the scraper. The chart therefore shows the lowest verified observed price per day, while the separate offer variants retain campaign-level detail. Review offers are kept in the feed but excluded from verified price history until their prices are unambiguous.

## Local preview

Run any static HTTP server from the repository root:

\`\`\`bash
python3 -m http.server 8000
\`\`\`

Then open \`http://localhost:8000\`.

## Tests

Run parser, identity and PALDIES pagination tests:

\`\`\`bash
python -m unittest discover -s tests -v
\`\`\`

## GitHub Pages

In repository Settings → Pages, set the build and deployment source to **GitHub Actions**. The workflow \`.github/workflows/maxima.yml\` publishes the static site after a successful data refresh. You can also start it from Actions → Update Maxima offers and deploy → Run workflow.
