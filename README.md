# Maxima Latvia offers aggregator

A static website for collecting and browsing verified supermarket offers across Latvia.

## Project status

The static interface, daily collector and price-history charts are in place. GitHub Actions records one price observation per product per day while it appears on Maxima's public offers page. Price history is retained after an offer disappears from the current page. The collector does not infer a city or nationwide availability.

## Local preview

Run any static HTTP server from the repository root, for example:

```bash
python3 -m http.server 8000
```

Then open `http://localhost:8000`.

## GitHub Pages

In repository Settings → Pages, set the build and deployment source to **GitHub Actions**. The workflow `.github/workflows/maxima.yml` then publishes the static site after a successful data refresh. You can also start it from Actions → Update Maxima offers and deploy → Run workflow.

## Offer data

Each offer can include:

- `name`: product name
- `brand`: brand, if known
- `image`: product image URL, if available
- `price`: current promotional price
- `regularPrice`: regular price, if verified
- `unit`: package size or price unit
- `discountPercent`: discount percentage, if verified
- `validFrom`, `validUntil`: offer validity dates (`YYYY-MM-DD`)
- `city`, `store`: geographic scope; omit rather than guessing
- `paldis`: whether a PALDIES card is required
- `source`: source URL
- `checkedAt`: when the offer was checked\n- `productId`: stable product key used to connect daily observations

Price charts initially have one observation and become useful after repeated daily runs. They show observed promotional prices from the public offers page, not a complete archive of ordinary shelf prices. Only publish offers supported by an identifiable source. The collector does not assign a city or claim nationwide availability. Review the Actions log if the source markup changes or no products are detected.