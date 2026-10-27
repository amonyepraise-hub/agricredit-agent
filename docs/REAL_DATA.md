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
| Season rainfall and anomaly (primary) | ERA5 reanalysis via the Open-Meteo historical API | Season windows only, 2016-2025 baseline + recent seasons, one request per year per 0.5° cell, spaced out to stay within the free allowance; season total vs the mean of the same calendar window | 0.5° cell, daily |
| Rainfall cross-check | NASA POWER daily point API, `PRECTOTCORR` | One request per POWER grid cell, 1991 to yesterday; compared with its own 1991-2020 mean | Commune centroid, daily |
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
- add the NASA POWER cross-check next to the ERA5 rainfall, with the gap between them.

**Why ERA5 is primary.** The first real download (October 2026) showed NASA POWER's 2026 season 56% wetter than its own 1991-2020 average in the median commune, with every one of the 77 communes above average, while ERA5 showed a near-normal season (−18% to +22%). A reanalysis like ERA5 is produced by one fixed model for every year, so this season and its baseline are comparable. POWER is kept, visibly, as the cross-check.

Real mode also uses its own policy file (`fs_mcp_root/policy/agent_policy_real.json`), read through the official MCP filesystem server as before. It maps each WFP market to its nearest neighbour for the stale-price fallback and looks back 52 weeks, because prices are monthly.

## What real data changed in the agent

Running on real data exposed four things the synthetic data never could, and each one changed the agent:

1. **Rainfall sources disagree.** NASA POWER's 2026 season was 56% wetter than its own history in the median commune; ERA5 was near normal. ERA5 became primary, and NASA is shown beside it with the gap.
2. **Prices are seasonal.** Maize is cheapest just after harvest, so comparing with a 12-month median calls every harvest a shock. The agent now compares the latest price with the same month in earlier years (`seasonal_anomaly_pct`), and warns when it is 10% or more below the usual level.
3. **Repayment at harvest means selling at the bottom.** When a multi-year monthly history exists, the agent also computes capacity if repayment moves to the month (up to four months later) when prices have usually been highest, net of 5% storage loss and the extra interest. If that makes the loan serviceable, the policy can recommend it with a condition: repayment scheduled for that month, with safe storage (for example warrantage). It is marked as relying on the usual seasonal recovery.
4. **Small models draw wrong conclusions from right numbers.** A real qwen2.5:1.5b draft said "suggesting a drought" at -19.1% (the drought line is -20%) and that a disagreeing cross-check "confirms" it. The agent now computes key findings itself, gives them to the model, checks the text against them (in English and French), asks for one correction, and otherwise uses its template.

It also fixed two of our own assumptions: packages are never larger than the plot, and real packages credit a +30% yield gain from on-farm trials in Benin instead of a hypothetical figure. Rice revenue uses paddy or local rice prices, never imported rice.

## French advisories

Choose **Français** under "Explanation language" in the dashboard, or run `python -m agricredit.cli run BEN-2001 --lang fr`. The model writes the explanation in French; the template and the safety checks also work in French. Figures and the recommendation do not depend on the language.

## Real farm sizes (EHCVM 2021/22)

1. Create a free account at https://microdata.worldbank.org/catalog/6272 and download the Stata files.
2. Copy `s16a_me_ben2021.dta` and `ehcvm_welfare_ben2021.dta` into `data/real/ehcvm/` (never committed: the licence forbids redistribution).
3. Run `.venv/bin/python -m agricredit.opendata ehcvm`. It writes only department-level, survey-weighted aggregates (crop shares and area quantiles; cells under 20 households suppressed) to `data/real/processed/ehcvm_farm_profile.csv`.
4. Rebuild: `.venv/bin/python -m agricredit.opendata build`, then `./run_demo.sh --real`.

## Evaluation on real data

`./run_demo.sh --real --evals` runs every real case and writes `EVALS_REAL.md`: source coverage, decision-rule violations, seasonal-flag errors, explanations contradicting the key findings, model explanations kept, and reproducibility.

## Freshness in real mode

| Data | Limit | Why |
|---|---|---|
| Market prices | 90 days | WFP publishes monthly, usually one to two months late |
| Rainfall | 14 days | Satellite and reanalysis rainfall arrives a few days late |
| Repayment records | 30 days | Unchanged |

The agent's clock (`AGRICREDIT_AS_OF`) is the moment of the download, so a run months later still reproduces the same advisory.

## Troubleshooting

- **"open data download did not finish"**: check the internet connection and run `./run_demo.sh --real` again. Finished downloads are cached in `data/real/cache/` and are not repeated.
- **Open-Meteo says "rate limited"**: the fetcher waits 65 s and tries again, for up to about an hour; leave it running. Running `python -m agricredit.opendata fetch` again later reuses everything already downloaded.
- **NASA POWER or Open-Meteo stops responding**: the fetcher gives up after three failures and says so. Without the cross-check the agent still runs; without the primary rainfall the build stops with a message.
- **WFP prices missing**: HDX may be down for maintenance. Try again later; the build will not start without prices.
