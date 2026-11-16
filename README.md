# land-feed

An MLS-style feed of federal and state land coming up for sale, with a map, a sortable table and push alerts.

- **Live page:** https://dfirouz.github.io/land-feed/
- **Data:** `docs/data/listings.json`, `docs/data/listings.csv`, `docs/data/changes.json`
- **Refresh:** GitHub Action runs twice daily (`.github/workflows/refresh.yml`), or on demand from the Actions tab.

## Sources
| Source | What | How |
|---|---|---|
| Federal Register | BLM Notices of Realty Action (land sales), usually weeks ahead of the sale | Official API |
| realestatesales.gov | GSA surplus federal property | Headless browser |
| EnergyNet / EfficientMarkets | BLM land auctions (e.g. SNPLMA) and state trust land auctions (e.g. Utah SITLA) | Headless browser |

## Alerts
1. Install the **ntfy** app (iOS/Android) and subscribe to a long random topic name.
2. Add that topic name as a repo secret: Settings → Secrets → Actions → `NTFY_TOPIC`.
3. Edit `alerts.json` filters (states, min acres, max price, max $/acre, exclude direct sales).

Locations are approximate (ZIP / city / county centroid) until parcel geometry is added.
