# Maxima Latvia offers aggregator

A static website for collecting and browsing verified supermarket offers across Latvia.

## Project status

Initial scaffold. Offer data is currently maintained in `data/offers.json`; automated collection is not yet implemented.

## Local preview

Run any static HTTP server from the repository root, for example:

```bash
python3 -m http.server 8000
```

Then open `http://localhost:8000`.

## GitHub Pages

In repository Settings → Pages, select GitHub Actions or deploy from the default branch root. The project is designed to work as a static site without a backend.

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
- `checkedAt`: when the offer was checked

Only publish offers supported by an identifiable source. Do not label an offer as available throughout Latvia unless its geographic coverage is confirmed.