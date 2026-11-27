# Maxima Latvia offers aggregator

A static website for collecting and browsing verified supermarket offers across Latvia.

## Project status

The static interface, daily collector and price-history charts are in place. GitHub Actions records one verified price observation per product per day while it appears on Maxima's public offers page. Price history is retained after an offer disappears from the current page.

The collector reads the weekly campaign dates from the section heading, looks up the linked brochure page for an explicit X / XX / XXX store-scope statement, and checks for the PALDIES marker inside each product card. It keeps ambiguous price cards out of the verified price history and records them separately in \`reviewOffers\`.

The scraper does not infer a city, store availability or store format when the official page does not state it.

## Local preview

Run any static HTTP server from the repository root, for example:

\`\`\`bash
python3 -m http.server 8000
\`\`\`

Then open \`http://localhost:8000\`.

## Tests

Run the parser and validation tests before publishing:

\`\`\`bash
python -m unittest discover -s tests -v
\`\`\`

## GitHub Pages

In repository Settings → Pages, set the build and deployment source to **GitHub Actions**. The workflow \`.github/workflows/maxima.yml\` publishes the static site after a successful data refresh. You can also start it from Actions → Update Maxima offers and deploy → Run workflow.

## Offer data

Each offer can include:

- \`name\`, \`brand\`, \`image\`, \`unit\`
- \`price\`: currently shown promotional price
- \`regularPrice\`: second displayed price, when its relationship is unambiguous
- \`paldiesPrice\`, \`priceWithoutPaldies\`, \`requiresPaldies\`
- \`discountPercent\`: source-displayed value, checked against the two prices when both exist
- \`validFrom\`, \`validUntil\`: campaign validity dates (\`YYYY-MM-DD\`)
- \`storeFormats\`: only formats explicitly confirmed by the linked campaign page
- \`city\`, \`store\`: geographic scope; omitted rather than guessed
- \`source\`, \`storeScopeSource\`, \`checkedAt\`
- \`quality\`: independent price, date and store-scope verification states

The \`offers\` list remains backward-compatible with the current frontend. Ambiguous price cards are flagged with \`quality.priceStatus = "review"\` and copied to \`reviewOffers\`; they are not added to price history. Price charts show observed promotional prices, not a complete archive of ordinary shelf prices.

Only publish offers supported by an identifiable source. Review the Actions log if the source markup changes or no products are detected.
