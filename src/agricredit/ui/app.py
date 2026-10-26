"""AgriCredit Agent — officer dashboard (Streamlit).

    streamlit run src/agricredit/ui/app.py

Eight panels: case selection, agent activity, evidence, risk assessment, stress test, draft advisory,
officer approval, audit trail. Every number shown comes from the agent's MCP tool outputs.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from agricredit.agent.runner import AgentRunner  # noqa: E402
from agricredit.approval import gate  # noqa: E402
from agricredit.audit.logger import read_jsonl  # noqa: E402
from agricredit.config import AS_OF, get_settings  # noqa: E402
from agricredit.db import data_sources, list_cases, list_officers  # noqa: E402
from agricredit.models.schemas import ApprovalDecision  # noqa: E402

st.set_page_config(page_title="AgriCredit Agent", page_icon="🌾", layout="wide")
st.markdown("""
<style>
  .block-container {padding-top: 3.2rem; max-width: 1400px;}
  .pill {display:inline-block; padding:3px 10px; border-radius:999px; font-weight:600; font-size:0.82rem; margin-right:6px;}
  .ok {background:#e3f2e1; color:#1f5f1a;} .warn {background:#fff1d6; color:#7a4b00;}
  .bad {background:#fde2e1; color:#8a1c17;} .neutral {background:#e8ecf1; color:#2c3e50;}
  .rec {font-size:1.35rem; font-weight:700;}
  .headline {font-size:1.05rem; margin:0.4rem 0 0.2rem 0;}
  .small {font-size:0.85rem; color:#5b6470;}
</style>""", unsafe_allow_html=True)

REC_STYLE = {"RECOMMEND": "ok", "RECOMMEND_WITH_CONDITIONS": "warn", "REDUCE_CREDIT_AMOUNT": "warn",
             "REQUEST_MORE_EVIDENCE": "neutral", "NO_RECOMMENDATION": "bad"}
STATUS_STYLE = {"AWAITING_APPROVAL": "warn", "RELEASED": "ok", "RELEASE_BLOCKED": "bad", "REFUSED": "bad",
                "TERMINATED_BY_OFFICER": "neutral", "ERROR": "bad"}


@st.cache_resource
def get_runner() -> AgentRunner:
    return AgentRunner()


def pill(text: str, style: str) -> str:
    return f'<span class="pill {style}">{text}</span>'


def fcfa(x) -> str:
    return "n/a" if x is None else f"{x:,.0f} FCFA"


s = get_settings()
runner = get_runner()

# ---------------------------------------------------------------- 1. case selection (sidebar)
with st.sidebar:
    st.markdown("## 🌾 AgriCredit Agent")
    real_sources = data_sources()
    if real_sources:
        st.caption("Evidence-first input-credit advisory for extension officers.")
        st.success("**Real open data mode.** Rainfall (NASA POWER, ERA5), market prices (WFP), official commune "
                   "yields (DSA via HarvestStat) and fertiliser prices are real. Households and their loans are "
                   "simulated inside real communes.")
        with st.expander("Data sources and licences"):
            for src in real_sources:
                st.markdown(f"- [{src['name']}]({src['url']}): {src['publisher']} · {src['licence']}")
    else:
        st.caption("Evidence-first input-credit advisory for extension officers. **All household data is synthetic.**")
    if s.is_open_weight_backend:
        st.success(f"Model: {s.model_version}")
    else:
        st.warning(f"Model backend: {s.model_version}. Set AGRICREDIT_LLM_BACKEND=ollama to use Qwen.")
    cases = list_cases()
    labels = {c["case_id"]: f"{c['case_id']} · {c['crop']} · {c['requested_amount_fcfa']:,.0f} FCFA" for c in cases}
    tags = {c["case_id"]: c["scenario_tag"] for c in cases}
    case_id = st.selectbox("1 · Select a case", list(labels), format_func=lambda k: labels[k],
                           index=list(labels).index("BEN-0421") if "BEN-0421" in labels else 0)
    st.caption(f"Scenario: {tags[case_id]}")
    st.markdown("**Deliberate data problem (demo)**")
    outage = st.checkbox("Simulate rainfall-service outage for this case's zone",
                         help="Sets AGRICREDIT_FAULTS=weather_unavailable:<zone> for this run only.")
    if st.button("▶ Run agent", type="primary", width="stretch"):
        zone = None
        if outage:
            from agricredit.services import get_household_case
            try:
                zone = get_household_case(next(c for c in cases if c["case_id"] == case_id)["household_id"]).location_id
            except Exception:
                zone = None
        os.environ["AGRICREDIT_FAULTS"] = f"weather_unavailable:{zone}" if zone else ""
        with st.spinner("Agent is gathering evidence through MCP tools..."):
            try:
                state = runner.start(case_id)
            finally:
                os.environ["AGRICREDIT_FAULTS"] = ""
        st.session_state["run_id"] = state["run_id"]
    st.caption(f"Data clock: {AS_OF:%d %b %Y %H:%M}")

run_id = st.session_state.get("run_id")
st.markdown("### AgriCredit Agent — officer desk")
if not run_id:
    st.info("Select a case and press **Run agent**. The agent gathers evidence through MCP tools, computes "
            "serviceability, drafts an advisory and then **stops** until a named officer decides.")
    st.stop()

state = runner.state(run_id)
adv = state.get("advisory") or {}
a = state.get("assessment") or {}
status = state.get("status", "?")

# ---------------------------------------------------------------- status bar
approved = (state.get("release") or {}).get("released")
human = ("✅ Officer approved and released (sandbox)" if approved else
         "⛔ Release blocked" if status == "RELEASE_BLOCKED" else
         "⏳ Awaiting a named officer" if state.get("_waiting_for_officer") else
         "— No human decision yet" if status != "TERMINATED_BY_OFFICER" else "✖ Officer ended the case")
st.markdown(
    pill(state.get("case_id", ""), "neutral") + pill(status, STATUS_STYLE.get(status, "neutral"))
    + (pill(adv["recommendation"], REC_STYLE.get(adv.get("recommendation"), "neutral")) if adv else "")
    + f'<span class="small">{human} · run {run_id}</span>', unsafe_allow_html=True)
if state.get("refusal_reason"):
    st.error(state["refusal_reason"])
elif adv:
    figs = (f" · risk score {a['risk_score']}/100 · PD {a['probability_of_default']:.3f} · capacity "
            f"{a['base_repayment_capacity_ratio']:.2f}x base / {a['stress_repayment_capacity_ratio']:.2f}x combined shock"
            if a else "")
    sugg = f" → about {fcfa(adv['suggested_amount_fcfa'])}" if adv.get("suggested_amount_fcfa") else ""
    st.markdown(f'<div class="headline"><b>Agent recommends: {adv["recommendation"].replace("_", " ")}{sugg}</b>'
                f'{figs}</div><div class="small">Officer action: {adv["officer_action_required"]}</div>',
                unsafe_allow_html=True)

# ---------------------------------------------------------------- 2. activity + 3. evidence
col1, col2 = st.columns([5, 6])
with col1:
    st.markdown("#### 2 · What the agent did")
    with st.container(height=380):
        for x in state.get("activity", []):
            tool = f" `{x['tool']}`" if x.get("tool") else ""
            st.markdown(f"**{x['node']}** — {x['message']}{tool}")
    if state.get("plan"):
        with st.expander(f"Investigation plan ({state.get('plan_source')})"):
            st.json(state["plan"])
with col2:
    st.markdown("#### 3 · Evidence retrieved")
    ev = adv.get("evidence") or ((state.get("household") or {}).get("evidence", [])
                                 + (state.get("weather") or {}).get("evidence", []) + (state.get("market") or {}).get("evidence", []))
    if ev:
        df = pd.DataFrame([{"record ID": e["source_uri"].removeprefix("sqlite://data/agricredit.db/"), "evidence": e["label"], "value": e["value"],
                            "kind": e["kind"], "observed": (e.get("observation_timestamp") or "")[:10],
                            "stale": "⚠ stale" if e.get("is_stale") else ""} for e in ev])
        st.dataframe(df, hide_index=True, width="stretch", height=380, column_config={
            "record ID": st.column_config.TextColumn(width="medium"),
            "evidence": st.column_config.TextColumn(width="medium"),
            "value": st.column_config.TextColumn(width="small"),
            "kind": st.column_config.TextColumn(width="small")})
        st.caption("observed = recorded data · derived = computed from observed · model_prediction = risk model · "
                   "every row links to a record ID")
    else:
        st.caption("No evidence retrieved.")

# ---------------------------------------------------------------- 4. risk + 5. stress
if a:
    st.markdown("#### 4 · Risk assessment")
    m = st.columns(5)
    m[0].metric("Risk score", f"{a['risk_score']}/100", a["risk_band"], delta_color="off")
    m[1].metric("Probability of default", f"{a['probability_of_default']:.3f}")
    m[2].metric("Repayment capacity (base)", f"{a['base_repayment_capacity_ratio']:.2f}x")
    m[3].metric("Capacity (combined shock)", f"{a['stress_repayment_capacity_ratio']:.2f}x")
    m[4].metric("Debt service (FCFA)", f"{a['debt_service_fcfa']:,.0f}")
    c3, c4 = st.columns(2)
    with c3:
        contrib = pd.DataFrame([{"feature": k, "log-odds contribution": v} for k, v in a["feature_contributions"].items()])
        contrib = contrib.reindex(contrib["log-odds contribution"].abs().sort_values(ascending=False).index).head(8)
        st.markdown("**Why the risk is what it is** (positive = raises default risk)")
        st.altair_chart(alt.Chart(contrib).mark_bar().encode(
            x="log-odds contribution:Q", y=alt.Y("feature:N", sort="-x"),
            color=alt.condition("datum['log-odds contribution'] > 0", alt.value("#c0392b"), alt.value("#2e7d32"))),
            width="stretch")
    with c4:
        st.markdown("#### 5 · Stress test")
        sdf = pd.DataFrame(a["stress_results"]).round({"expected_revenue_fcfa": 0})
        base = alt.Chart(sdf).mark_bar().encode(
            x=alt.X("scenario:N", sort=None, axis=alt.Axis(labelAngle=0)), y=alt.Y("repayment_capacity_ratio:Q", title="repayment capacity (x)"),
            color=alt.condition("datum.repayment_capacity_ratio >= 1", alt.value("#2e7d32"), alt.value("#c0392b")))
        rule = alt.Chart(pd.DataFrame({"y": [1.0]})).mark_rule(strokeDash=[4, 4]).encode(y="y:Q")
        st.altair_chart(base + rule, width="stretch")
        st.caption(f"Shocks: yield −{s.yield_shock_pct:.0%}, price −{s.price_shock_pct:.0%} (configurable). "
                   "Dashed line = debt fully covered.")
        st.dataframe(sdf[["scenario", "yield_kg_per_ha", "price_fcfa_per_kg", "expected_revenue_fcfa",
                          "repayment_capacity_ratio"]], hide_index=True, width="stretch", column_config={
            "yield_kg_per_ha": st.column_config.NumberColumn("yield kg/ha", format="%.0f"),
            "price_fcfa_per_kg": st.column_config.NumberColumn("price FCFA/kg", format="%.1f"),
            "expected_revenue_fcfa": st.column_config.NumberColumn("revenue FCFA", format="localized"),
            "repayment_capacity_ratio": st.column_config.NumberColumn("capacity", format="%.2fx")})

# ---------------------------------------------------------------- 6. draft advisory
if adv:
    st.markdown("#### 6 · Draft advisory (not sent)")
    st.markdown(f'<span class="rec">{adv["recommendation"].replace("_", " ")}</span>'
                + (f' &nbsp;→ suggested {fcfa(adv["suggested_amount_fcfa"])}' if adv.get("suggested_amount_fcfa") else ""),
                unsafe_allow_html=True)
    if adv["viable_only_under_optimistic_assumptions"]:
        st.warning("Viable only under optimistic assumptions: fails the combined stress test at the requested amount.")
    st.markdown(f"**{adv['proposed_product']}** · {fcfa(adv['proposed_amount_fcfa'])} · {adv['tenor_months']} months · "
                f"{adv['plot_context']} · confidence {adv['confidence']:.2f}")
    st.markdown(f"**Why:** {adv['rationale']}")
    st.markdown(f"**Officer action required:** {adv['officer_action_required']}")
    for g in adv["missing_data"]:
        st.error(f"Missing evidence: {g}")
    for w in adv["data_quality_warnings"]:
        st.warning(w)
    with st.expander(f"Explanation (written by {adv['narrative_author']})", expanded=True):
        st.write(adv["narrative"])
    with st.expander("Assumptions"):
        for x in adv["assumptions"]:
            st.markdown(f"- {x}")
    st.caption(f"Recommendation hash {adv['recommendation_hash']} · evidence version {adv['evidence_version']} · "
               f"model {adv['model_version']} · risk model {adv['risk_model_version']}")

# ---------------------------------------------------------------- 7. approval
st.markdown("#### 7 · Officer decision")
if state.get("_waiting_for_officer") and adv:
    officers = list_officers()
    with st.form("approval"):
        off = st.selectbox("Officer", officers, format_func=lambda o: f"{o['officer_id']} · {o['officer_name']}")
        decision = st.radio("Decision", ["APPROVE", "REJECT"], horizontal=True)
        reason = st.text_input("Reason (required to reject)")
        st.caption("Approving binds your name to this exact recommendation hash. If the evidence or recommendation "
                   "changes, the approval is void and a new one is required.")
        if st.form_submit_button("Record decision", type="primary"):
            try:
                appr = gate.record_approval(case_id=adv["case_id"], recommendation_hash_=adv["recommendation_hash"],
                                            officer_id=off["officer_id"], officer_name=off["officer_name"],
                                            decision=ApprovalDecision(decision), reason=reason or None)
                payload = ({"action": "release", "approval": appr.model_dump(mode="json"),
                            "recommendation_hash": adv["recommendation_hash"]} if decision == "APPROVE"
                           else {"action": "terminate", "reason": reason})
                runner.resume(run_id, payload)
                st.rerun()
            except gate.ApprovalError as e:
                st.error(str(e))
elif state.get("release"):
    rel = state["release"]
    (st.success if rel["released"] else st.error)(rel["reason"])
    if rel.get("checks"):
        st.dataframe(pd.DataFrame([{"check": k, "passed": "✅" if v else "❌"} for k, v in rel["checks"].items()]),
                     hide_index=True)
else:
    st.caption("No decision pending.")

# ---------------------------------------------------------------- 8. audit trail
st.markdown("#### 8 · Audit trail")
calls = read_jsonl("tool_calls.jsonl", run_id=run_id)
events = read_jsonl("audit_events.jsonl", case_id=state.get("case_id"))
t1, t2 = st.tabs([f"Tool calls this run ({len(calls)})", f"Audit events for case ({len(events)})"])
with t1:
    if calls:
        st.dataframe(pd.DataFrame([{"time": c["timestamp"][11:19], "node": c["agent_node"], "server": c["mcp_server"],
                                    "tool": c["tool_name"], "attempt": c["attempt"], "ok": "✅" if c["success"] else "❌",
                                    "ms": c["latency_ms"], "sources": len(c["source_ids"]), "error": (c["error"] or "")[:80]}
                                   for c in calls]), hide_index=True, width="stretch")
with t2:
    if events:
        st.dataframe(pd.DataFrame([{"time": e["timestamp"][11:19], "event": e["event_type"], "actor": e["actor"],
                                    "details": str(e["details"])[:160]} for e in events]),
                     hide_index=True, width="stretch")
st.caption("Full logs: logs/tool_calls.jsonl and logs/audit_events.jsonl")
