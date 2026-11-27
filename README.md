# Maxima Latvia offers aggregator

A static website for collecting and comparing Maxima Latvia promotional offers.

## Current collection sources

The daily collector reads:

- Weekly offers: \`https://www.maxima.lv/piedavajumi/\`
- Well Done Premium: \`https://www.maxima.lv/premium\`
- The complete PALDIES-card catalogue, selected through the official \`Ar PALDIES karti\` filter on \`https://www.maxima.lv/piedavajumi/\`
- Every currently active brochure linked from \`https://www.maxima.lv/bukleti/\`, including special campaigns, seasonal catalogues, book festivals and store-opening leaflets.

### Active brochure collection

The scraper discovers brochure cards dynamically from the official catalogue index. For every active brochure it resolves its own detail page, date range, PDF link and published store scope.

PDF files are downloaded and parsed with PyMuPDF. Product names, package details and prices may be printed in separate text blocks, so the parser combines nearby blocks by page position before matching each product to its sale and previous price. Explicit PALDIES-card conditions are retained as separate price fields instead of treating every PDF price as an ordinary shelf price. A page may be assigned to a specific campaign inside a combined weekly PDF (for example, Well Done Premium or Grāmatu festivāls). Store-specific catalogues retain their published store description and location rather than being presented as nationwide offers.

The scraper records the PDF page, brochure URL, dates, source product position, store scope and price-quality checks. Product cards that cannot be parsed unambiguously are stored in \`reviewOffers\` rather than silently treated as verified.

Each active brochure has a \`complete\` flag. It is true only when the brochure PDF is present and at least one product page was successfully parsed. Catalogue summaries are printed during each run so missed or empty brochures can be identified.

The PALDIES collector uses Maxima's own \`/ajax/salesloadmore\` endpoint. It reads the filter ID from the page instead of assuming it is fixed, then downloads all result pages in batches of up to 75 using the endpoint's \`offset\` parameter.

For every run, it checks the server-reported \`totalItems\`, the number of cards on each page, and the uniqueness of each source product ID. If a page is missing, repeats products, or the final number of unique products differs from the server total, the scraper fails rather than publishing a partial PALDIES catalogue.

The collector does not infer a city, store-specific availability or store format when the official page does not state it.

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

Run parser, identity, PALDIES pagination and PDF extraction tests:

\`\`\`bash
python -m unittest discover -s tests -v
\`\`\`

## GitHub Pages

In repository Settings → Pages, set the build and deployment source to **GitHub Actions**. The workflow \`.github/workflows/maxima.yml\` publishes the static site after a successful data refresh. You can also start it from Actions → Update Maxima offers and deploy → Run workflow.
