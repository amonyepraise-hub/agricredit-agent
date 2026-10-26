# Real open data mode

The same agent, the same MCP tools and the same approval gate, reading public data for Benin instead of the synthetic store.

```bash
./run_demo.sh --real          # first run downloads the data (internet, about 20 to 30 minutes), then opens the dashboard
./run_demo.sh --real --cli    # same, then runs the first real case in the terminal
./run_demo.sh --real --refresh-data   # download everything again (new month of prices, new rainfall)
```

Or step by step:

```bash
.venv/bin/python -m agricredit.opendata sources   # list the sources and licences
.venv/bin/python -m agricredit.opendata fetch     # download into data/real/ (needs internet)
.venv/bin/python -m agricredit.opendata build     # build data/agricredit_real.db (offline)
```

## Where each number comes from

| Agent input | Source | Extraction | Granularity |
|---|---|---|---|
| Commune list, centroids | HarvestStat Africa v1.2 boundaries (FEWS NET units) | GeoPackage read with Python's `sqlite3`; centroid computed from the polygon | 77 communes |
| Official yields | HarvestStat Africa v1.2, from Benin's Direction de la Statistique Agricole | CSV filtered to Benin; values flagged as outliers by HarvestStat are dropped | Commune, crop, year (1995 to 2023) |
| Season rainfall and anomaly | NASA POWER daily point API, `PRECTOTCORR` | One request per POWER grid cell, 1991 to yesterday; season total vs the 1991-2020 mean of the same calendar window | Commune centroid, daily |
| Rainfall cross-check | ERA5 via Open-Meteo historical API | Season windows only (2006-2020 baseline + this year), for up to 20 communes, spaced out to stay within the free allowance | Commune centroid, daily |
| Market prices | WFP VAM "Benin - Food Prices" on HDX | Resource found through the HDX CKAN API; retail prices per kg; one variety per crop and market | Market, monthly |
| Input package cost | Subsidised fertiliser prices for 2025/26 (NPK 17,000 and urea 15,000 FCFA per 50 kg bag) × the 200 kg NPK + 100 kg urea per hectare maize dose | Transcribed and cited in `src/agricredit/opendata/sources.py` | Per package |
| Default-rate calibration | BCEAO, "Principaux indicateurs des SFD au 31 décembre 2024": Benin portfolio degradation rate 9.4% | Transcribed; the simulated loan outcomes are calibrated to this mean | National |

Season windows (approximate crop calendar): south (latitude < 7.8°) 15 March to 31 July; centre (7.8° to 9.5°) 1 April to 30 September; north (≥ 9.5°) 15 May to 15 October. A deficit of 20% or more against the baseline is flagged as drought, the same rule as the synthetic mode.

## What is still simulated

- **Households, plots and repayment histories.** No open dataset publishes smallholders' loan repayments. Households are simulated inside real communes, and each household's yields are drawn around that commune's official yields for the last three published years.
- **Loan outcomes used to train the risk model** are simulated, with the intercept calibrated so the mean default rate equals the BCEAO figure. The model card (`data/risk_model_real_card.json`) says so.
- **Officers** are fictional.

## How it reaches the agent

Nothing in the agent graph changed. The builder writes the same tables the MCP tools already read, plus four new ones (`locations`, `commune_yields`, `weather_crosscheck`, `data_sources`). The tools now:

- cite the real source URL in every weather and price evidence item;
- add the commune's official yield as an extra evidence item for each crop the household grows;
- add the ERA5 cross-check next to the NASA POWER rainfall, with the gap between them.

Real mode also uses its own policy file (`fs_mcp_root/policy/agent_policy_real.json`), read through the official MCP filesystem server as before. It maps each WFP market to its nearest neighbour for the stale-price fallback and looks back 52 weeks, because prices are monthly.

## Freshness in real mode

| Data | Limit | Why |
|---|---|---|
| Market prices | 90 days | WFP publishes monthly, usually one to two months late |
| Rainfall | 14 days | Satellite and reanalysis rainfall arrives a few days late |
| Repayment records | 30 days | Unchanged |

The agent's clock (`AGRICREDIT_AS_OF`) is the moment of the download, so a run months later still reproduces the same advisory.

## Troubleshooting

- **"open data download did not finish"**: check the internet connection and run `./run_demo.sh --real` again. Finished downloads are cached in `data/real/cache/` and are not repeated.
- **NASA POWER or Open-Meteo stops responding**: the fetcher gives up after three failed communes and says so. Without the cross-check the agent still runs; without the primary rainfall the build stops with a message.
- **WFP prices missing**: HDX may be down for maintenance. Try again later; the build will not start without prices.
