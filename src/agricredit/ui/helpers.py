"""Plain-language layer for the officer dashboard.

Everything here turns the agent's real output into words an extension officer or a microfinance
credit officer reads at a glance. Nothing is invented: every sentence is built from a figure the
agent computed or a record it retrieved.
"""
from __future__ import annotations

import re
from datetime import datetime

from .. import db

CROPS = {"maize": ("Maize", "🌽"), "rice": ("Rice", "🌾"), "soybean": ("Soybean", "🫘"), "sorghum": ("Sorghum", "🌾"),
         "cassava": ("Cassava", "🥔"), "cotton": ("Cotton", "☁️")}

# The agent's steps, in order, as an officer would describe them.
STEPS = [
    ("load_case", "Reading the lending rules and the request"),
    ("validate_data", "Checking the request is complete"),
    ("plan_investigation", "Planning what to check"),
    ("get_household_evidence", "Looking at the farmer's harvests and past loans"),
    ("get_weather_evidence", "Checking this season's rainfall"),
    ("get_market_evidence", "Checking crop prices at the local market"),
    ("quantitative_assessment", "Calculating whether the loan can be repaid"),
    ("contradiction_check", "Making sure the sources agree"),
    ("evidence_sufficiency", "Making sure there is enough evidence"),
    ("draft_advisory", "Writing the advice for you"),
]
STEP_INDEX = {n: i for i, (n, _) in enumerate(STEPS)}

# Verdict cards: title, tone (CSS class), one sentence.
VERDICTS = {
    "RECOMMEND": ("Lend", "good", "The farmer can repay comfortably, even in a bad season."),
    "RECOMMEND_WITH_CONDITIONS": ("Lend, with conditions", "warn",
                                  "The loan can be repaid, but there is something to discuss with the farmer first."),
    "REDUCE_CREDIT_AMOUNT": ("Lend a smaller amount", "caution",
                             "The full amount is too risky; a smaller package can be repaid."),
    "REQUEST_MORE_EVIDENCE": ("More information needed", "info",
                              "Some information is missing or out of date. Collect it before deciding."),
    "NO_RECOMMENDATION": ("Do not lend this season", "bad",
                          "The expected harvest would not cover the repayment."),
}

STATUS_LABELS = {"new": ("New", "neutral"), "draft": ("Advice ready", "info"),
                 "approved": ("Approved", "good"), "declined": ("Declined", "bad")}

SCENARIOS = {"base_case": "Normal season", "yield_shock": "Harvest 20% lower", "price_shock": "Price 10% lower",
             "combined_shock": "Both at once"}

SAFETY_CHECKS = {
    "approval_exists": "Your approval was recorded",
    "approval_matches_stored_record": "Your approval has not been altered",
    "decision_is_approve": "The decision is an approval",
    "case_matches": "It is for this loan request",
    "named_officer_registered": "You are a registered officer",
    "approval_not_expired": "The approval is recent (less than 72 hours)",
    "approval_not_replayed": "The approval has not been used before",
    "hash_matches_approval": "You approved exactly this advice",
    "advisory_exists": "The advice is on file",
    "recommendation_unchanged": "The advice has not changed since you read it",
    "evidence_unchanged": "The evidence has not changed since you read it",
    "model_version_unchanged": "The same AI model wrote it",
}

LEARN_MORE = [
    ("Rainfall this season", "Open-Meteo historical weather (ERA5, Copernicus / ECMWF)",
     "https://open-meteo.com/en/docs/historical-weather-api"),
    ("Independent rainfall check", "NASA POWER", "https://power.larc.nasa.gov/"),
    ("Market prices", "WFP food prices for Benin (Humanitarian Data Exchange)",
     "https://data.humdata.org/dataset/wfp-food-prices-for-benin"),
    ("Official crop yields", "HarvestStat Africa (DSA Benin figures)", "https://github.com/HarvestStat/HarvestStat-Africa"),
    ("Fertiliser prices 2025/26", "Government of Benin, agricultural campaign launch",
     "https://www.gouv.bj/article/3046/production-agricole-campagne-agricole-2025-2026-lancee-dassa-zoume/"),
    ("Fertiliser yield gains in Benin", "On-farm maize trials (Frontiers, 2021)", "https://doi.org/10.3389/fsufs.2021.605610"),
    ("Microfinance repayment rates", "BCEAO, microfinance indicators (Dec 2024)",
     "https://bceao.int/sites/default/files/inline-files/Principaux%20indicateurs%20des%20SFD%20au%2031%20d%C3%A9cembre%202024.pdf"),
    ("Farm sizes by department", "EHCVM 2021/22 household survey (World Bank)", "https://microdata.worldbank.org/catalog/6272"),
    ("How AgriCredit works", "Project documentation", "https://github.com/amonyepraise-hub/agricredit-agent"),
]


def fcfa(x) -> str:
    return "—" if x is None else f"{x:,.0f} FCFA"


def crop_name(crop: str) -> str:
    return CROPS.get(crop, (crop.title(), "🌱"))[0]


def crop_icon(crop: str) -> str:
    return CROPS.get(crop, ("", "🌱"))[1]


def market_name(centre: str | None) -> str:
    return (centre or "").removesuffix("-Market").replace("-", " ") + " market" if centre else "the local market"


def person(name: str | None) -> str:
    return re.sub(r"\s*\(fictional officer\)", "", name or "").strip()


def household_label(household_id: str) -> str:
    return "Household " + household_id.removeprefix("HH-")


def times(x: float | None) -> str:
    return "—" if x is None else f"{x:.1f}×"


def highlight(tag: str | None) -> str | None:
    """'REAL rainfall: driest commune this season (...) | Tanguiéta ...' -> 'Driest commune this season (...)'."""
    if not tag:
        return None
    head = tag.split(" | ")[0]
    head = re.sub(r"^REAL [a-z]+:\s*", "", head).strip()
    if not head or "routine" in head:
        return None
    return head[0].upper() + head[1:]


def greeting(now: datetime) -> str:
    return "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 18 else "Good evening"


# ---------------------------------------------------------------- data for the request list
def requests() -> list[dict]:
    """Every loan request with where it comes from and its latest status."""
    with db.read_conn() as c:
        rows = [dict(r) for r in c.execute("""
            SELECT c.case_id, c.household_id, c.crop, c.requested_amount_fcfa, c.tenor_months, c.scenario_tag,
                   c.market_centre, p.name AS package_name, l.name AS commune, l.admin_1 AS department,
                   (SELECT ROUND(SUM(area_ha), 2) FROM plots WHERE plots.household_id = c.household_id
                                                       AND plots.crop = c.crop) AS area_ha
            FROM credit_cases c
            LEFT JOIN input_packages p ON p.package_id = c.package_id
            LEFT JOIN households h ON h.household_id = c.household_id
            LEFT JOIN locations l ON l.location_id = h.location_id
            ORDER BY c.case_id""")]
        approvals = {r["case_id"]: dict(r) for r in c.execute(
            "SELECT case_id, decision, officer_name, timestamp FROM approvals ORDER BY timestamp")}
        drafts = {r["case_id"] for r in c.execute("SELECT DISTINCT case_id FROM advisories")}
    for r in rows:
        a = approvals.get(r["case_id"])
        r["status"] = ("approved" if a and a["decision"] == "APPROVE" else "declined" if a else
                       "draft" if r["case_id"] in drafts else "new")
        r["decided_by"] = person(a["officer_name"]) if a else None
        r["highlight"] = highlight(r["scenario_tag"])
        r["place"] = ", ".join(x for x in (r.get("commune"), r.get("department")) if x) or "Unknown location"
    return rows


def package_plain(name: str | None) -> str:
    """'Maize fertiliser 1 ha: 4 x 50 kg NPK + 2 x 50 kg urea at ...' -> 'fertiliser for 1 ha of maize (4 bags of NPK
    and 2 bags of urea, 50 kg each)'."""
    if not name:
        return "inputs"
    bags = re.findall(r"(\d+) x 50 kg (\w+)", name)
    area = re.search(r"(\d+(?:\.\d+)?) ha", name)
    crop = name.split()[0].lower()
    if not bags or not area:
        return name[0].lower() + name[1:]
    contents = " and ".join(f"{n} {'bag' if n == '1' else 'bags'} of {p}" for n, p in bags)
    return f"fertiliser for {area[1]} ha of {crop} ({contents}, 50 kg each)"


# ---------------------------------------------------------------- plain-language warnings and gaps
_RULES = [
    (r"latest (\w+) price is ([+-]?\d+(?:\.\d+)?)% vs the usual (\w+) price \((.+?) median\)",
     lambda m: f"{m[1].title()} sells {abs(float(m[2])):.0f}% {'below' if float(m[2]) < 0 else 'above'} its usual "
               f"{m[3]} price at the local market."),
    (r"latest (\w+) price is ([+-]?\d+(?:\.\d+)?)% vs the (\d+)-week median",
     lambda m: f"{m[1].title()} sells {abs(float(m[2])):.0f}% {'below' if float(m[2]) < 0 else 'above'} its price "
               f"over the last {m[3]} weeks."),
    (r"drought conditions this season \(([+-]?\d+(?:\.\d+)?)% rainfall",
     lambda m: f"Drought this season: rainfall is {abs(float(m[1])):.0f}% below normal."),
    (r"latest repayment record is (\d+) days old",
     lambda m: f"The farmer's latest repayment record is {m[1]} days old. Confirm their current status."),
    (r"(\w+) price taken from alternate centre (\S+) because (\S+) data is stale",
     lambda m: f"{m[1].title()} price taken from {market_name(m[2])}, because {market_name(m[3])} has no recent price."),
    (r"rainfall evidence is a cached observation (\d+) days old",
     lambda m: f"The rainfall service was unavailable, so a saved reading from {m[1]} days ago was used."),
    (r"Household notes contain instruction-like text",
     lambda m: "The farmer's file contains unusual text. It was ignored for safety."),
    (r"LLM unavailable", lambda m: "The AI writer was unavailable, so a standard explanation was used."),
    (r"no rainfall evidence for", lambda m: "No rainfall data could be retrieved for this area."),
    (r"only a cached rainfall observation (\d+) days old",
     lambda m: f"Only a saved rainfall reading from {m[1]} days ago is available."),
    (r"rainfall observation is (\d+) days old", lambda m: f"The latest rainfall reading is {m[1]} days old."),
    (r"no (\w+) price series for (\S+)", lambda m: f"No {m[1]} prices were found for {market_name(m[2])}."),
    (r"conflicting same-day (\w+) prices at (\S+)",
     lambda m: f"Two {m[1]} price records for {market_name(m[2])} disagree on the same day. Check which is right."),
    (r"no current (\w+) price: latest observation at (\S+) is (\d+) days",
     lambda m: f"No recent {m[1]} price: the latest at {market_name(m[2])} is {m[3]} days old."),
    (r"no repayment history on file",
     lambda m: "First-time borrower with no repayment history. Ask for a cooperative reference or a savings-group record."),
    (r"only (\d+) season\(s\) of yield records", lambda m: f"Only {m[1]} season(s) of harvest records are on file."),
    (r"quantitative assessment could not be completed", lambda m: "The repayment calculation could not be completed."),
]


def plain(text: str) -> str:
    for pattern, fn in _RULES:
        m = re.search(pattern, text, flags=re.I)
        if m:
            return fn(m)
    return text.replace("_", " ")


def clean_narrative(text: str) -> str:
    """Hide evidence IDs and internal names from model-written text (the stored draft keeps them)."""
    text = re.sub(r"\s*\[[^\]\s]*[:/][^\]]*\]", "", text)
    for k in sorted(VERDICTS, key=len, reverse=True):  # longest first: RECOMMEND is inside RECOMMEND_WITH_CONDITIONS
        text = text.replace(k, VERDICTS[k][0].lower())
    return re.sub(r"\s{2,}", " ", text).strip()


# ---------------------------------------------------------------- the reasons behind a verdict
def reasons(state: dict) -> list[tuple[str, str, str]]:
    """(icon, sentence, tone) for the summary, most important first."""
    a = state.get("assessment") or {}
    adv = state.get("advisory") or {}
    case = state.get("case") or {}
    market = state.get("market") or {}
    weather = state.get("weather") or {}
    crop = crop_name(case.get("crop", ""))
    out: list[tuple[str, str, str]] = []
    base, stress = a.get("base_repayment_capacity_ratio"), a.get("stress_repayment_capacity_ratio")
    if base is not None:
        out.append(("💰", f"In a normal season, the harvest should bring in about {base:.1f} times what the farmer "
                          f"will owe.", "good" if base >= 1.2 else "warn" if base >= 1 else "bad"))
    if stress is not None:
        if stress >= 1:
            out.append(("🛡️", f"Even if the harvest falls 20% and prices drop 10%, it still covers the loan "
                              f"{stress:.1f} times.", "good"))
        else:
            out.append(("⚠️", f"If the harvest falls 20% and prices drop 10%, it would cover only {stress:.1f} times "
                              f"the loan: not enough.", "bad"))
    pd_ = a.get("probability_of_default")
    if pd_ is not None:
        out.append(("📊", f"Estimated chance of not repaying: {pd_ * 100:.0f} in 100. For comparison, about 9 in 100 "
                          f"microfinance loans in Benin were in difficulty at the end of 2024.",
                    "good" if pd_ < 0.094 else "warn"))
    be, price = a.get("break_even_price_fcfa_per_kg"), market.get("latest_price_fcfa_per_kg")
    if be and price:
        where = market_name(market.get("market_centre"))
        if price >= be:
            out.append(("🏷️", f"The loan still works as long as {crop.lower()} sells above {be:,.0f} FCFA/kg "
                              f"(today at {where}: {price:,.0f} FCFA/kg).", "good"))
        else:
            out.append(("🏷️", f"The loan would only work if {crop.lower()} sold above {be:,.0f} FCFA/kg; today at "
                              f"{where} it sells at {price:,.0f} FCFA/kg.", "bad"))
    t = a.get("repayment_timing")
    if t:
        out.append(("📅", f"If repayment were due in {t['best_month']} instead of at harvest, the farmer could sell "
                          f"when {crop.lower()} prices usually recover (about {t['expected_price_deferred']:,.0f} FCFA/kg "
                          f"after storage losses).", "info"))
    if weather:
        anom = weather.get("rainfall_anomaly_pct")
        if anom is not None:
            word = "below" if anom < 0 else "above"
            out.append(("🌧️", f"Rainfall this season: {weather['rainfall_mm']:,.0f} mm, {abs(anom):.0f}% {word} "
                              f"normal for this area.", "warn" if weather.get("drought_indicator") else "info"))
    for w in adv.get("data_quality_warnings", []):
        out.append(("❗", plain(w), "warn"))
    for g in adv.get("missing_data", []):
        out.append(("📄", plain(g), "bad"))
    return out


def next_step(state: dict) -> str:
    adv = state.get("advisory") or {}
    rec = adv.get("recommendation")
    warn = [plain(w) for w in adv.get("data_quality_warnings", [])]
    gaps = [plain(g) for g in adv.get("missing_data", [])]
    if rec == "RECOMMEND":
        return "Approve to send the advice. Nothing else needs checking."
    if rec == "RECOMMEND_WITH_CONDITIONS":
        return "Before approving, discuss with the farmer: " + " ".join(warn) if warn else \
            "Before approving, agree the repayment conditions with the farmer."
    if rec == "REDUCE_CREDIT_AMOUNT":
        return f"Offer about {fcfa(adv.get('suggested_amount_fcfa'))} instead of the full package, then approve."
    if rec == "REQUEST_MORE_EVIDENCE":
        return "Collect the missing information, then review again: " + " ".join(gaps)
    if rec == "NO_RECOMMENDATION":
        return "Explain to the farmer why the loan is not advised this season, and suggest reapplying next season."
    return ""


def farmer_summary(household: dict, crop: str) -> dict:
    loans = household.get("repayment_history", [])
    late = [l for l in loans if (l.get("days_overdue") or 0) > 0 and not l.get("defaulted")]
    defaulted = [l for l in loans if l.get("defaulted")]
    yields = sorted((y for y in household.get("yield_history", []) if y["crop"] == crop), key=lambda y: y["season"])
    if not loans:
        record = "No previous loans on file"
    elif defaulted:
        record = f"{len(loans)} past loans, {len(defaulted)} not repaid"
    elif late:
        record = f"{len(loans)} past loans, all repaid ({len(late)} late)"
    else:
        record = f"{len(loans)} past loans, all repaid on time"
    return {"plots": household.get("plots", []), "loans": loans, "record": record, "yields": yields,
            "obligations": household.get("current_obligations", [])}
