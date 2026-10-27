"""Deterministic recommendation policy.

The recommendation TYPE is decided here from numbers and evidence gaps, never by
the LLM. The LLM writes the explanation around it. agricredit-mcp's
draft_credit_advisory tool re-runs this decision and refuses any recommendation
more permissive than what the evidence supports.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import get_settings
from .models.schemas import RecommendationType as R

# Higher = more permissive. An agent may always choose something LESS permissive.
PERMISSIVENESS = {
    R.NO_RECOMMENDATION: 0, R.REQUEST_MORE_EVIDENCE: 0, R.REDUCE_CREDIT_AMOUNT: 1,
    R.RECOMMEND_WITH_CONDITIONS: 2, R.RECOMMEND: 3,
}


def load_policy_file() -> dict:
    """Server-side read of the same policy file the agent reads via the filesystem MCP server."""
    s = get_settings()
    return json.loads((Path(s.fs_mcp_root) / "policy" / s.policy_file).read_text())


def decide(assessment: dict | None, blocking_gaps: list[str], warnings: list[str], requested_amount: float,
           policy: dict, weather_from_cache: bool = False) -> dict:
    out = _decide(assessment, blocking_gaps, warnings, requested_amount, policy, weather_from_cache)
    if warnings and out["recommendation"] not in (R.REQUEST_MORE_EVIDENCE, R.RECOMMEND_WITH_CONDITIONS):
        out["officer_action"] += " Before approval, also resolve: " + "; ".join(warnings) + "."
    return out


def _decide(assessment: dict | None, blocking_gaps: list[str], warnings: list[str], requested_amount: float,
            policy: dict, weather_from_cache: bool = False) -> dict:
    d = policy["decision"]
    conf = 0.9 - 0.08 * len(warnings) - (policy["weather"]["confidence_penalty_for_cache"] if weather_from_cache else 0.0)

    if blocking_gaps:
        return {
            "recommendation": R.REQUEST_MORE_EVIDENCE, "suggested_amount_fcfa": None,
            "viable_only_under_optimistic_assumptions": False,
            "rationale": "Evidence is insufficient for a credit recommendation: " + "; ".join(blocking_gaps) + ".",
            "officer_action": "Obtain the missing evidence listed, then re-run the assessment. Do not offer credit on this draft.",
            "confidence": round(max(0.1, min(conf, 0.3)), 2),
        }

    a = assessment
    pd_, base, comb = a["probability_of_default"], a["base_repayment_capacity_ratio"], a["stress_repayment_capacity_ratio"]
    max_ok = a["max_serviceable_amount_fcfa"]
    reducible = max_ok >= d["min_reduced_offer_fcfa"] and max_ok < requested_amount

    if pd_ >= d["pd_no_recommendation_min"]:
        return _out(R.NO_RECOMMENDATION, None, False,
                    f"Estimated probability of default {pd_:.2f} is at or above the {d['pd_no_recommendation_min']:.2f} limit.",
                    "Do not offer this credit. Discuss non-credit support (extension visit, savings group) with the household.", conf)
    if base < 1.0 or comb < d["min_combined_capacity_for_recommend"]:
        optimistic = base >= 1.0
        t = a.get("repayment_timing")
        deferred_ok = bool(d.get("allow_deferred_repayment") and t
                           and t["base_capacity_deferred"] >= d["min_base_capacity_for_recommend"]
                           and t["combined_capacity_deferred"] >= d["min_combined_capacity_for_recommend"])
        later = (f" If repayment were due in {t['best_month']} ({t['months_later']} months later), when prices have usually "
                 f"been {t['typical_price_ratio']:.2f} times today's, capacity would be {t['base_capacity_deferred']:.2f}x base and "
                 f"{t['combined_capacity_deferred']:.2f}x under the combined shock.") if deferred_ok else ""
        if reducible:
            return _out(R.REDUCE_CREDIT_AMOUNT, max_ok, optimistic,
                        f"At {requested_amount:,.0f} FCFA, repayment capacity is {base:.2f}x base and {comb:.2f}x under the combined "
                        f"shock. A scaled package of about {max_ok:,.0f} FCFA keeps combined-shock capacity at or above 1.0x." + later,
                        f"Discuss a reduced package of about {max_ok:,.0f} FCFA with the household before any approval."
                        + (f" Alternative: keep the full package with repayment scheduled for {t['best_month']}." if deferred_ok else ""),
                        conf)
        if deferred_ok:
            return _out(R.RECOMMEND_WITH_CONDITIONS, None, True,
                        f"Repaid at today's price, capacity is {base:.2f}x base and {comb:.2f}x under the combined shock." + later,
                        f"Approve only with repayment scheduled for {t['best_month']} and safe storage of the harvest (for example "
                        "through warrantage / inventory credit). This relies on the usual seasonal price recovery.", conf - 0.1)
        if optimistic and base >= d["min_base_capacity_for_recommend"] and pd_ < d["pd_no_recommendation_min"]:
            return _out(R.RECOMMEND_WITH_CONDITIONS, None, True,
                        f"Repayment capacity is {base:.2f}x in the base case but only {comb:.2f}x under the combined shock: the "
                        "credit is viable only under optimistic assumptions and no smaller package restores stress viability.",
                        "Approve only with explicit acknowledgement of stress-case shortfall and a contingency (crop insurance, "
                        "repayment rescheduling clause, or group guarantee).", conf - 0.1)
        return _out(R.NO_RECOMMENDATION, None, optimistic,
                    f"Repayment capacity is {base:.2f}x base and {comb:.2f}x under the combined shock, and no package of at least "
                    f"{d['min_reduced_offer_fcfa']:,.0f} FCFA is serviceable under stress.",
                    "Do not offer this credit at any tested size.", conf)
    clean = (pd_ < d["pd_recommend_max"] and base >= d["min_base_capacity_for_recommend"] and not warnings)
    if clean:
        return _out(R.RECOMMEND, None, False,
                    f"Probability of default {pd_:.2f}; repayment capacity {base:.2f}x base and {comb:.2f}x under the combined "
                    "shock; no data-quality warnings.",
                    "Review the evidence and approve or reject. Nothing is released without your approval.", conf)
    why = []
    if pd_ >= d["pd_recommend_max"]:
        why.append(f"probability of default {pd_:.2f} is above {d['pd_recommend_max']:.2f}")
    if base < d["min_base_capacity_for_recommend"]:
        why.append(f"base repayment capacity {base:.2f}x is below {d['min_base_capacity_for_recommend']:.1f}x")
    why += warnings
    return _out(R.RECOMMEND_WITH_CONDITIONS, None, False,
                f"Serviceable under stress ({comb:.2f}x combined shock), but: " + "; ".join(why) + ".",
                "Resolve the listed warnings before approval: " + "; ".join(warnings) if warnings
                else "Approve only with conditions addressing the elevated risk.", conf)


def _out(rec, amount, optimistic, rationale, action, conf) -> dict:
    return {"recommendation": rec, "suggested_amount_fcfa": amount, "viable_only_under_optimistic_assumptions": optimistic,
            "rationale": rationale, "officer_action": action, "confidence": round(max(0.1, min(conf, 0.95)), 2)}
