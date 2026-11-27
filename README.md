# Maxima Latvia offers aggregator

A static website for collecting and browsing verified supermarket offers across Latvia.

## Project status

The static interface and a first automated collector are in place. GitHub Actions runs the collector daily and can be started manually. The collector reads the public Maxima Latvia offers page and only publishes product cards for which it can identify a name and price. The source page may change, so the workflow must be checked after its first runs.

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
- `checkedAt`: when the offer was checked

Only publish offers supported by an identifiable source. The collector does not assign a city or claim nationwide availability: Maxima notes that some offers can differ by store format/location. Review the Actions log if the source markup changes or no products are detected.