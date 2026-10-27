"""The open data sources AgriCredit Agent uses in real-open-data mode.

Each entry says who publishes the data, what licence it carries, how it is fetched,
and which part of the agent it feeds. `python -m agricredit.opendata sources` prints
this table; data/real/SOURCES.md is generated from it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    key: str
    name: str
    publisher: str
    licence: str
    url: str
    feeds: str
    access: str


HARVESTSTAT_BASE = "https://raw.githubusercontent.com/HarvestStat/HarvestStat-Africa/main/public/"
HARVESTSTAT_VERSION = "v1.2"
POWER_DAILY_POINT = "https://power.larc.nasa.gov/api/temporal/daily/point"
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
HDX_PACKAGE_SHOW = "https://data.humdata.org/api/3/action/package_show"
HDX_WFP_BENIN = "wfp-food-prices-for-benin"

SOURCES: list[Source] = [
    Source("harveststat", "HarvestStat Africa " + HARVESTSTAT_VERSION + " (harmonised subnational crop statistics)",
           "UC Santa Barbara Climate Hazards Center / FEWS NET; Benin figures from the Direction de la Statistique "
           "Agricole (DSA), Ministere de l'Agriculture", "MIT",
           "https://github.com/HarvestStat/HarvestStat-Africa",
           "Official commune-level yields 1995-2023 for maize, rice, soybean, cotton and cashew (all 77 communes); "
           "commune boundaries and centroids", "Open download, no account"),
    Source("nasa_power", "NASA POWER daily point API (PRECTOTCORR, bias-corrected precipitation)",
           "NASA Langley Research Center", "Public domain (U.S. Government work); cite NASA POWER",
           "https://power.larc.nasa.gov/", "Daily rainfall 1991-today at each commune centroid -> independent "
           "cross-check shown beside the primary rainfall (its current season disagreed with its own history, so it is not primary)", "Open API, no key"),
    Source("open_meteo", "Open-Meteo Historical Weather API (ERA5 reanalysis)",
           "Open-Meteo / Copernicus Climate Change Service (ECMWF)", "CC BY 4.0 (non-commercial free tier)",
           "https://open-meteo.com/en/docs/historical-weather-api",
           "Primary daily rainfall per commune -> season rainfall, 2016-2025 baseline, anomaly and drought flag", "Open API, no key"),
    Source("wfp_hdx", "Benin - Food Prices (WFP VAM) on the Humanitarian Data Exchange",
           "World Food Programme", "CC BY-IGO", "https://data.humdata.org/dataset/" + HDX_WFP_BENIN,
           "Monthly retail market prices (FCFA/kg) for maize, rice and other staples by market -> latest price, "
           "median, volatility, price shock", "Open download, no account"),
    Source("bceao", "Principaux indicateurs des SFD au 31 decembre 2024",
           "BCEAO (Central Bank of West African States)", "Public statistical publication",
           "https://bceao.int/sites/default/files/inline-files/Principaux%20indicateurs%20des%20SFD%20au%2031%20d%C3%A9cembre%202024.pdf",
           "Benin microfinance portfolio degradation rate 9.4% (Dec 2024) -> calibrates the simulated default rate "
           "used to train the risk model", "Open PDF (figure transcribed, see data/real/SOURCES.md)"),
    Source("gouv_bj_fertiliser", "Lancement de la campagne agricole 2025-2026 (subsidised fertiliser prices)",
           "Gouvernement du Benin", "Public government communication",
           "https://www.gouv.bj/article/3046/production-agricole-campagne-agricole-2025-2026-lancee-dassa-zoume/",
           "NPK 17,000 FCFA, urea 15,000 FCFA per 50 kg bag -> real input-package costs", "Open web page"),
    Source("inrab_dose", "Recommended maize fertiliser dose (200 kg/ha NPK + 100 kg/ha urea)",
           "Amogou et al. 2021, INRAB / University of Abomey-Calavi (via FAO AGRIS)", "Scientific publication",
           "https://agris.fao.org/search/fr/records/675ab76d0e654e779babbd53",
           "Package composition per hectare", "Open abstract"),
    Source("ehcvm", "Enquête Harmonisée sur les Conditions de Vie des Ménages (EHCVM) 2021/22, Benin",
           "INSAE Benin, WAEMU Commission, World Bank", "Free account; raw files may not be redistributed",
           "https://microdata.worldbank.org/catalog/6272",
           "Department-level crop mix and farm-size distributions (weighted; cells under 20 households suppressed); "
           "only these aggregates are kept in the repository", "Free registration"),
    Source("benin_trials", "On-farm maize trials in Benin (south, centre, north): yield gain from NPK + urea",
           "Frontiers in Sustainable Food Systems, 2021", "CC BY 4.0", "https://doi.org/10.3389/fsufs.2021.605610",
           "Yield gain credited to fertiliser packages (+30% for the full dose, a conservative figure)", "Open access"),
]

# Figures transcribed from the publications above (each one is cited where it is used).
BCEAO_BENIN_PORTFOLIO_DEGRADATION_RATE_2024 = 0.094
FERTILISER_PRICE_FCFA_PER_50KG = {"NPK": 17000.0, "urea": 15000.0, "SSP": 14000.0}
MAIZE_DOSE_KG_PER_HA = {"NPK": 200.0, "urea": 100.0}
# Yield gain credited to a full-dose package over farmers' usual practice. On-farm trials in Benin's south, centre and
# north found +13% to +38% with only HALF the recommended NPK + urea dose plus a soil inoculant (Frontiers in Sustainable
# Food Systems, 2021, doi:10.3389/fsufs.2021.605610), and +30.6% over the control in a centre-Benin trial (Amogou et al.
# 2021). +30% for the full dose is therefore a conservative planning figure, shown to the officer as an assumption.
FULL_DOSE_YIELD_GAIN = 0.30


def by_key(key: str) -> Source:
    return next(s for s in SOURCES if s.key == key)


def sources_markdown() -> str:
    lines = ["# Open data sources (real-open-data mode)", "",
             "Generated by `python -m agricredit.opendata sources`. Every evidence item the agent shows in this mode "
             "links back to one of these.", "",
             "| Source | Publisher | Licence | Feeds | Access |", "|---|---|---|---|---|"]
    for s in SOURCES:
        lines.append(f"| [{s.name}]({s.url}) | {s.publisher} | {s.licence} | {s.feeds} | {s.access} |")
    lines += ["", "## What is still simulated, and why", "",
              "- **Households, plots and repayment histories.** No open dataset publishes named smallholders' loan "
              "repayments, and none should. Households are simulated *inside real communes*: each household's yields "
              "are drawn around that commune's official yields, and the default rate of the simulated loan outcomes is "
              "calibrated to BCEAO's 9.4% portfolio degradation rate for Benin's microfinance sector.",
              "- **Plot sizes and crop mixes** come from the World Bank / INSAE EHCVM 2021/22 survey (8,032 Benin "
              "households) when `python -m agricredit.opendata ehcvm` has been run: each simulated household's crops and "
              "plot sizes are drawn from its department's weighted distributions. Without it, an assumed distribution is used.",
              "- **Officers** are fictional.", ""]
    return "\n".join(lines)
