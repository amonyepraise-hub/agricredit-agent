"""AgriCredit — officer desk (Streamlit).

    streamlit run src/agricredit/ui/app.py

A plain-language desk for extension officers and microfinance credit officers: a list of loan requests,
a live review by the agent, a clear verdict with its reasons and evidence, and a named officer's decision.
Every number comes from the agent's MCP tool outputs; the technical record stays one click away.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from agricredit import auth  # noqa: E402
from agricredit.agent.graph import translate_explanation  # noqa: E402
from agricredit.agent.runner import AgentRunner  # noqa: E402
from agricredit.approval import gate  # noqa: E402
from agricredit.audit.logger import read_jsonl  # noqa: E402
from agricredit.config import AS_OF, get_settings  # noqa: E402
from agricredit.db import data_sources, list_officers, read_conn  # noqa: E402
from agricredit.models.schemas import ApprovalDecision  # noqa: E402
from agricredit.ui import helpers as H  # noqa: E402

st.set_page_config(page_title="AgriCredit · Officer desk", page_icon="🌾", layout="wide",
                   initial_sidebar_state="collapsed")

INK, GREEN, GOLD = "#0F2B24", "#1E6B4E", "#E8A838"
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,800&family=Inter:wght@400;500;600;700&display=swap');
html, body, .stMarkdown, .stButton button, input, textarea, label, p, li { font-family: 'Inter', system-ui, sans-serif; }
h1, h2, h3, .display { font-family: 'Bricolage Grotesque', 'Inter', sans-serif !important; letter-spacing: -0.01em; color: #0F2B24; }
.block-container { padding-top: 1.4rem; padding-bottom: 3rem; max-width: 1280px; }
header[data-testid="stHeader"] { background: transparent; }
[data-testid="stMainMenu"], [data-testid="stToolbar"] { display: none; }
[data-testid="stSidebar"], [data-testid="collapsedControl"] { display: none; }
div[data-testid="stVerticalBlockBorderWrapper"] { background: #FFFFFF; border-radius: 16px; border-color: #E6DFCF !important;
  box-shadow: 0 2px 14px rgba(15,43,36,.05); }
.brand { display: flex; align-items: center; gap: 12px; }
.brand .logo { width: 44px; height: 44px; border-radius: 12px; background: #0F2B24; display: grid; place-items: center; font-size: 24px; }
.brand .name { font: 800 24px/1 'Bricolage Grotesque', sans-serif; color: #0F2B24; }
.brand .name span { color: #C98A12; }
.brand .sub { font-size: 13px; color: #5A665F; margin-top: 3px; }
.who { text-align: right; font-size: 14px; color: #3F4F47; padding-top: 6px; }
.who b { color: #0F2B24; }
.hello { font: 800 34px/1.1 'Bricolage Grotesque', sans-serif; color: #0F2B24; margin: 6px 0 4px; }
.lead { font-size: 16.5px; color: #3F4F47; max-width: 820px; }
.databar { background: #0F2B24; color: #E9EFE9; border-radius: 14px; padding: 14px 18px; font-size: 14.5px; margin: 14px 0 6px; }
.databar b { color: #E8A838; }
.kpi .v { font: 800 32px/1 'Bricolage Grotesque', sans-serif; color: #0F2B24; }
.kpi .l { font-size: 13.5px; color: #5A665F; margin-top: 6px; }
.pill { display: inline-block; padding: 3px 11px; border-radius: 999px; font-size: 12.5px; font-weight: 600; }
.pill.good { background: #E3F1E8; color: #1E6B4E; } .pill.warn { background: #FBF0D9; color: #8C5B0A; }
.pill.caution { background: #FBE6D6; color: #9A4A12; } .pill.info { background: #E4EAF6; color: #2C4A7C; }
.pill.bad { background: #F9E1DE; color: #8A1C17; } .pill.neutral { background: #EEEBE3; color: #4A564F; }
.req-place { font: 700 19px/1.2 'Bricolage Grotesque', sans-serif; color: #0F2B24; margin-top: 8px; }
.req-sub { font-size: 13.5px; color: #5A665F; }
.req-amt { font: 800 24px/1.1 'Bricolage Grotesque', sans-serif; color: #0F2B24; margin-top: 10px; }
.req-hl { font-size: 13px; color: #8C5B0A; margin-top: 6px; }
.verdict { border-radius: 18px; padding: 20px 22px; }
.verdict .k { font-size: 12.5px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; opacity: .85; }
.verdict .t { font: 800 30px/1.1 'Bricolage Grotesque', sans-serif; margin: 6px 0 8px; }
.verdict .s { font-size: 15px; line-height: 1.45; }
.verdict.good { background: #1E6B4E; color: #FFFFFF; } .verdict.warn { background: #E8A838; color: #0F2B24; }
.verdict.caution { background: #D9822B; color: #1F0F04; } .verdict.info { background: #2C4A7C; color: #FFFFFF; }
.verdict.bad { background: #8A1C17; color: #FFFFFF; }
.reason { display: flex; gap: 12px; align-items: flex-start; padding: 11px 2px; border-bottom: 1px solid #EEE7D8; font-size: 15.5px; color: #24342C; }
.reason:last-child { border-bottom: 0; }
.reason .ic { font-size: 20px; line-height: 1.2; }
.stepline { font-size: 15px; padding: 3px 0; }
.stepline.done { color: #1E6B4E; } .stepline.now { color: #0F2B24; font-weight: 600; } .stepline.todo { color: #9AA39D; }
.ev-title { font-size: 13px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: #8C5B0A; }
.ev-big { font: 800 28px/1.1 'Bricolage Grotesque', sans-serif; color: #0F2B24; margin: 4px 0; }
.ev-note { font-size: 14px; color: #3F4F47; }
.src { font-size: 13px; color: #5A665F; margin-top: 6px; }
.src a, .links a { color: #1E6B4E; font-weight: 600; text-decoration: none; }
.links div { padding: 8px 0; border-bottom: 1px solid #EEE7D8; font-size: 14.5px; }
.muted { color: #5A665F; font-size: 13.5px; }
.okline { font-size: 14.5px; padding: 3px 0; }
</style>""", unsafe_allow_html=True)


# ---------------------------------------------------------------- shared state
@st.cache_resource
def get_runner() -> AgentRunner:
    return AgentRunner()


@st.cache_resource
def run_lock() -> threading.Lock:
    """One agent run at a time per server: runs set per-run environment (language, model, demo faults),
    and on a shared server this keeps one officer's settings from leaking into another's run."""
    return threading.Lock()


BASE_BACKEND = os.getenv("AGRICREDIT_LLM_BACKEND", "ollama")
# Hosted demo: let the visitor choose Qwen or the instant template (only offered when Qwen is actually available).
ENGINE_CHOICE = (os.getenv("AGRICREDIT_DEMO_ENGINE_CHOICE", "false").strip().lower() in {"1", "true", "yes", "on"}
                 and BASE_BACKEND != "stub")
ENGINES = {"fast": "Fast mode — instant, standard wording", "qwen": "Qwen 2.5 7B — AI writes the explanation (slower)"}


def run_with_env(fn, env: dict):
    """Run one agent segment with per-run environment, under the server-wide lock."""
    with run_lock():
        old = {k: os.environ.get(k) for k in env}
        os.environ.update({k: v for k, v in env.items() if v is not None})
        try:
            return fn()
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def now_local() -> datetime:
    return datetime.now(ZoneInfo("Africa/Porto-Novo"))


ss = st.session_state
ss.setdefault("view", "inbox")
ss.setdefault("runs", {})        # case_id -> run_id (this browser session)
ss.setdefault("run_env", {})     # run_id -> environment used for that run

# ---------------------------------------------------------------- sign-in (hosted deployments)
if auth.auth_required() and "officer" not in ss:
    left, mid, right = st.columns([1, 1.3, 1])
    with mid:
        st.markdown('<div class="brand" style="margin-top:6vh"><div class="logo">🌾</div><div><div class="name">Agri'
                    '<span>Credit</span></div><div class="sub">Officer desk</div></div></div>', unsafe_allow_html=True)
        st.markdown("### Sign in")
        st.markdown('<div class="lead">Evidence-backed advice on seed and fertiliser loans for smallholder '
                    'farmers. Sign in with your officer account.</div>', unsafe_allow_html=True)
        wait = ss.get("login_wait_until", 0) - time.time()
        with st.form("login"):
            username = st.text_input("Username", autocomplete="username")
            password = st.text_input("Password", type="password", autocomplete="current-password")
            submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
        if submitted:
            if wait > 0:
                st.error(f"Too many attempts. Try again in {int(wait) + 1} seconds.")
            else:
                officer = auth.authenticate(username, password)
                if officer:
                    ss["officer"] = officer
                    ss["login_fails"] = 0
                    st.rerun()
                ss["login_fails"] = ss.get("login_fails", 0) + 1
                if ss["login_fails"] >= 5:
                    ss["login_wait_until"] = time.time() + 30 * (ss["login_fails"] - 4)
                time.sleep(1)
                st.error("Wrong username or password.")
    st.stop()

s = get_settings()
runner = get_runner()
signed_in = ss.get("officer")

# ---------------------------------------------------------------- top bar
c_brand, c_who, c_menu = st.columns([6, 4, 1.6], vertical_alignment="center")
with c_brand:
    st.markdown('<div class="brand"><div class="logo">🌾</div><div><div class="name">Agri<span>Credit</span></div>'
                '<div class="sub">Officer desk · seed and fertiliser credit</div></div></div>', unsafe_allow_html=True)
with c_who:
    if signed_in:
        st.markdown(f'<div class="who">Signed in as <b>{H.person(signed_in["officer_name"])}</b> · '
                    f'{signed_in["officer_id"]}</div>', unsafe_allow_html=True)
with c_menu:
    with st.popover("⚙️ Settings", width="stretch"):
        ss["lang"] = st.radio("Language of the written explanation", ["English", "Français"], horizontal=True,
                              index=["English", "Français"].index(ss.get("lang", "Français" if s.advisory_language == "fr"
                                                                          else "English")))
        if ENGINE_CHOICE:
            ss["engine"] = st.radio("Who writes the explanation", list(ENGINES), format_func=ENGINES.get,
                                    index=list(ENGINES).index(ss.get("engine", "fast")),
                                    help="The evidence, the figures, the advice and every safety check are the same "
                                         "in both modes. Only the wording of the explanation changes.")
            note = os.getenv("AGRICREDIT_QWEN_NOTE", "")
            if ss["engine"] == "qwen" and note:
                st.caption(note)
        else:
            st.caption(f"AI model: {'Qwen (open-weight, on this server)' if s.is_open_weight_backend else 'off — standard wording'}")
        ss["outage"] = st.checkbox("Demo: pretend the rainfall service is down", value=ss.get("outage", False),
                                   help="Shows how AgriCredit copes when a data source fails.")
        if signed_in and st.button("Sign out", width="stretch"):
            ss.clear()
            st.rerun()


def open_case(case_id: str):
    ss["view"], ss["case_id"] = "case", case_id
    ss.pop("declining", None)


# ================================================================ VIEW 1: the request list
def inbox():
    rows = H.requests()
    waiting = sum(r["status"] in ("new", "draft") for r in rows)
    decided = sum(r["status"] in ("approved", "declined") for r in rows)
    with read_conn() as c:
        communes = c.execute("SELECT COUNT(*) FROM locations").fetchone()[0]
        markets = c.execute("SELECT COUNT(DISTINCT market_centre) FROM market_prices").fetchone()[0]
    name = H.person(signed_in["officer_name"]) if signed_in else "officer"
    st.markdown(f'<div class="hello">{H.greeting(now_local())}, {name}</div>', unsafe_allow_html=True)
    st.markdown('<div class="lead">These are the seed and fertiliser loan requests from farmers. Open one and '
                'AgriCredit checks the rainfall, market prices, official harvests and the farmer\'s history, tests '
                'the loan against a bad season and drafts advice. <b>You make the decision.</b></div>',
                unsafe_allow_html=True)
    if data_sources():
        st.markdown(f'<div class="databar">🛰️ <b>Real Benin data</b>, updated {AS_OF:%d %B %Y}: satellite rainfall, '
                    'WFP market prices, official crop yields and 2025/26 fertiliser prices. The farmers in this demo '
                    'are simulated; their communes and markets are real.</div>', unsafe_allow_html=True)
    k = st.columns(4)
    for col, (v, label) in zip(k, [(waiting, "requests waiting for you"), (decided, "decisions made"),
                                   (communes, "communes covered"), (markets, "markets tracked")]):
        with col, st.container(border=True):
            st.markdown(f'<div class="kpi"><div class="v">{v}</div><div class="l">{label}</div></div>',
                        unsafe_allow_html=True)

    st.markdown("### Loan requests")
    f1, f2, f3 = st.columns([3, 2, 2])
    q = f1.text_input("Search", placeholder="Search a commune or department", label_visibility="collapsed")
    crops = sorted({r["crop"] for r in rows})
    crop = f2.selectbox("Crop", ["All crops"] + crops, format_func=lambda x: x if x == "All crops" else
                        f"{H.crop_icon(x)} {H.crop_name(x)}", label_visibility="collapsed")
    status = f3.selectbox("Status", ["All", "Waiting", "Decided"], label_visibility="collapsed")
    shown = [r for r in rows
             if (not q or q.lower() in r["place"].lower())
             and (crop == "All crops" or r["crop"] == crop)
             and (status == "All" or (status == "Waiting") == (r["status"] in ("new", "draft")))]
    featured = [r for r in shown if r["highlight"]]
    rest = [r for r in shown if not r["highlight"]]
    if featured:
        st.markdown("**Worth a look this season**")
        grid(featured, primary=True)
    if rest:
        if featured:
            st.markdown("**All other requests**")
        grid(rest)
    if not shown:
        st.info("No request matches your search.")
    learn_more_footer()


def grid(rows: list[dict], primary: bool = False):
    for i in range(0, len(rows), 3):
        cols = st.columns(3)
        for col, r in zip(cols, rows[i:i + 3]):
            label, tone = H.STATUS_LABELS[r["status"]]
            area = f" · {r['area_ha']:g} ha of {H.crop_name(r['crop']).lower()}" if r.get("area_ha") else ""
            with col, st.container(border=True):
                st.markdown(
                    f'<span class="pill {tone}">{label}</span> &nbsp;<span class="muted">{H.crop_icon(r["crop"])} '
                    f'{H.crop_name(r["crop"])}</span>'
                    f'<div class="req-place">{r["place"]}</div>'
                    f'<div class="req-sub">{H.household_label(r["household_id"])}{area}</div>'
                    f'<div class="req-amt">{H.fcfa(r["requested_amount_fcfa"])}</div>'
                    f'<div class="req-sub">fertiliser on credit · repay in {r["tenor_months"]} months</div>'
                    + (f'<div class="req-hl">★ {r["highlight"]}</div>' if r["highlight"] else "")
                    + (f'<div class="req-sub">by {r["decided_by"]}</div>' if r["decided_by"] else ""),
                    unsafe_allow_html=True)
                st.button("Review request →" if r["status"] != "approved" else "Open", key=f"open-{r['case_id']}",
                          type="primary" if primary and r["status"] == "new" else "secondary",
                          width="stretch", on_click=open_case, args=(r["case_id"],))


def learn_more_footer():
    st.markdown("---")
    with st.expander("📚 Where the data comes from, and where to learn more"):
        st.markdown('<div class="links">' + "".join(
            f'<div><b>{t}</b> — <a href="{u}" target="_blank">{n} ↗</a></div>' for t, n, u in H.LEARN_MORE)
            + "</div>", unsafe_allow_html=True)


# ================================================================ VIEW 2: one request
def case_view(case_id: str):
    row = next((r for r in H.requests() if r["case_id"] == case_id), None)
    if row is None:
        ss["view"] = "inbox"
        st.rerun()
    st.write("")
    st.button("← All requests", on_click=lambda: ss.update(view="inbox"))
    st.markdown(f'<div class="hello">{H.crop_icon(row["crop"])} {row["place"]}</div>'
                f'<div class="lead">{H.household_label(row["household_id"])} asks for '
                f'<b>{H.fcfa(row["requested_amount_fcfa"])}</b> of {H.package_plain(row["package_name"])}, to repay '
                f'in {row["tenor_months"]} months.</div>', unsafe_allow_html=True)
    if row["highlight"]:
        st.markdown(f'<div class="req-hl" style="font-size:14.5px">★ {row["highlight"]}</div>', unsafe_allow_html=True)
    st.write("")

    run_id = ss["runs"].get(case_id)
    if not run_id:
        start_panel(row)
        return
    state = runner.state(run_id)
    if state.get("status") == "REFUSED" or state.get("refusal_reason"):
        verdict_box("Cannot review this request", "bad", H.plain(state.get("refusal_reason") or "The request is invalid."),
                    kicker="AgriCredit stopped")
        technical(state, run_id)
        return

    left, right = st.columns([1.7, 1], gap="large")
    with right:
        decision_panel(state, run_id)
    with left:
        t1, t2, t3, t4, t5 = st.tabs(["Summary", "Evidence", "Bad-season test", "The farmer", "Sources"])
        with t1:
            summary_tab(state, run_id)
        with t2:
            evidence_tab(state)
        with t3:
            stress_tab(state)
        with t4:
            farmer_tab(state)
        with t5:
            sources_tab()
    technical(state, run_id)


def start_panel(row: dict):
    with st.container(border=True):
        st.markdown("### Let AgriCredit review this request")
        st.markdown('<div class="lead">It gathers this season\'s rainfall, the local market price, the commune\'s '
                    'official harvests and the farmer\'s own history; tests the loan against a bad season; and drafts '
                    'advice for you. <b>Nothing is sent to the farmer without your approval.</b></div>',
                    unsafe_allow_html=True)
        engine = ss.get("engine", "fast") if ENGINE_CHOICE else None
        if engine == "qwen":
            st.caption("Qwen 2.5 7B will write the explanation. " + os.getenv("AGRICREDIT_QWEN_NOTE", ""))
        if st.button("▶  Review with AgriCredit", type="primary", key="run"):
            review(row, engine)


def review(row: dict, engine: str | None):
    case_id = row["case_id"]
    zone = None
    if ss.get("outage"):
        from agricredit.services import get_household_case
        try:
            zone = get_household_case(row["household_id"]).location_id
        except Exception:
            zone = None
    env = {"AGRICREDIT_FAULTS": f"weather_unavailable:{zone}" if zone else "",
           "AGRICREDIT_ADVISORY_LANGUAGE": "fr" if ss.get("lang") == "Français" else "en",
           "AGRICREDIT_LLM_BACKEND": (BASE_BACKEND if engine == "qwen" else "stub") if engine else None}
    lines = {}
    with st.status("AgriCredit is reviewing this request…", expanded=True) as box:
        for i, (node, label) in enumerate(H.STEPS):
            lines[node] = st.empty()
            lines[node].markdown(f'<div class="stepline {"now" if i == 0 else "todo"}">{"⏳" if i == 0 else "○"} '
                                 f'{label}</div>', unsafe_allow_html=True)

        def on_step(node: str):
            i = H.STEP_INDEX.get(node)
            if i is None:
                return
            lines[node].markdown(f'<div class="stepline done">✅ {H.STEPS[i][1]}</div>', unsafe_allow_html=True)
            if i + 1 < len(H.STEPS):
                nxt = H.STEPS[i + 1]
                lines[nxt[0]].markdown(f'<div class="stepline now">⏳ {nxt[1]}</div>', unsafe_allow_html=True)

        t0 = time.time()
        try:
            state = run_with_env(lambda: runner.start(case_id, on_step=on_step), env)
        except Exception as e:  # never show a stack trace to an officer
            box.update(label="AgriCredit could not finish the review", state="error")
            st.error(f"Something went wrong ({type(e).__name__}). Please try again; the technical log has the details.")
            return
        box.update(label=f"Review complete in {time.time() - t0:.0f} seconds", state="complete", expanded=False)
    ss["runs"][case_id] = state["run_id"]
    ss["run_env"][state["run_id"]] = env
    time.sleep(0.4)
    st.rerun()


def verdict_box(title: str, tone: str, sentence: str, kicker: str = "AgriCredit advises"):
    st.markdown(f'<div class="verdict {tone}"><div class="k">{kicker}</div><div class="t">{title}</div>'
                f'<div class="s">{sentence}</div></div>', unsafe_allow_html=True)


def decision_panel(state: dict, run_id: str):
    adv = state.get("advisory") or {}
    rec = adv.get("recommendation", "REQUEST_MORE_EVIDENCE")
    title, tone, sentence = H.VERDICTS.get(rec, (rec, "info", ""))
    if rec == "REDUCE_CREDIT_AMOUNT" and adv.get("suggested_amount_fcfa"):
        title = f"Lend {H.fcfa(adv['suggested_amount_fcfa'])}"
        sentence = f"Instead of {H.fcfa(adv.get('proposed_amount_fcfa'))}. " + sentence
    verdict_box(title, tone, sentence)
    st.write("")
    with st.container(border=True):
        st.markdown("**What to do next**")
        st.markdown(f'<div class="ev-note">{H.next_step(state)}</div>', unsafe_allow_html=True)

    st.write("")
    with st.container(border=True):
        st.markdown("**Your decision**")
        if state.get("_waiting_for_officer") and adv:
            if signed_in:
                off = signed_in
                st.markdown(f'<div class="muted">Signed in as <b>{H.person(off["officer_name"])}</b>. Your name is '
                            f'recorded with your decision.</div>', unsafe_allow_html=True)
            else:
                off = st.selectbox("Acting as", list_officers(),
                                   format_func=lambda o: f"{H.person(o['officer_name'])} ({o['officer_id']})")
            b1, b2 = st.columns(2)
            label = "✅ Confirm this advice" if rec == "NO_RECOMMENDATION" else "✅ Approve this advice"
            if b1.button(label, type="primary", key="approve", width="stretch"):
                decide(state, run_id, off, ApprovalDecision.APPROVE, None)
            if b2.button("Decline", key="decline", width="stretch"):
                ss["declining"] = True
            if ss.get("declining"):
                reason = st.text_area("Why are you declining?", key="decline_reason",
                                      placeholder="For example: the farmer has another loan elsewhere.")
                if st.button("Confirm decline", key="confirm_decline"):
                    if not (reason or "").strip():
                        st.warning("Please give a short reason.")
                    else:
                        decide(state, run_id, off, ApprovalDecision.REJECT, reason.strip())
            st.caption("Approving binds your name to exactly this advice. If anything changes, a new approval is needed.")
        elif state.get("release"):
            rel = state["release"]
            checks = rel.get("checks") or {}
            appr = next((e for e in read_jsonl("audit_events.jsonl", case_id=state.get("case_id"))
                         if e["event_type"] == "officer_approve"), None)
            who = H.person((appr or {}).get("details", {}).get("officer_name")) if appr else ""
            if rel["released"]:
                st.success(f"Approved{' by ' + who if who else ''}. The advice is ready to send to the farmer by SMS.")
                st.caption("Demo: the message goes to a test outbox; nothing is sent to a real phone.")
            else:
                st.error("The advice was NOT released: a safety check failed.")
            passed = sum(bool(v) for v in checks.values())
            with st.expander(f"🛡️ {passed} of {len(checks)} safety checks passed"):
                for k, v in checks.items():
                    st.markdown(f'<div class="okline">{"✅" if v else "❌"} {H.SAFETY_CHECKS.get(k, k)}</div>',
                                unsafe_allow_html=True)
        elif state.get("status") == "TERMINATED_BY_OFFICER":
            st.info("You declined this request. Your reason is recorded in the audit trail.")
        else:
            st.caption("No decision is pending.")


def decide(state: dict, run_id: str, off: dict, decision: ApprovalDecision, reason: str | None):
    adv = state["advisory"]
    try:
        appr = gate.record_approval(case_id=adv["case_id"], recommendation_hash_=adv["recommendation_hash"],
                                    officer_id=off["officer_id"], officer_name=off["officer_name"],
                                    decision=decision, reason=reason)
        payload = ({"action": "release", "approval": appr.model_dump(mode="json"),
                    "recommendation_hash": adv["recommendation_hash"]} if decision == ApprovalDecision.APPROVE
                   else {"action": "terminate", "reason": reason})
        env = {k: v for k, v in ss["run_env"].get(run_id, {}).items() if k != "AGRICREDIT_FAULTS"}
        with st.spinner("Recording your decision and running the safety checks…"):
            run_with_env(lambda: runner.resume(run_id, payload), env)
        ss.pop("declining", None)
        st.rerun()
    except gate.ApprovalError as e:
        st.error(str(e))


def summary_tab(state: dict, run_id: str):
    st.markdown("#### Why AgriCredit advises this")
    st.markdown("".join(f'<div class="reason"><span class="ic">{ic}</span><span>{txt}</span></div>'
                        for ic, txt, _ in H.reasons(state)), unsafe_allow_html=True)
    adv = state.get("advisory") or {}
    author = adv.get("narrative_author", "")
    if author.startswith(("ollama:", "openai_compatible:")):
        st.write("")
        src_lang = state.get("narrative_language", "en")
        other = "en" if src_lang == "fr" else "fr"
        flip = st.toggle("Show in English" if other == "en" else "Afficher en français", key=f"tr-{run_id}")
        text, by = adv["narrative"], author
        if flip:
            cache = ss.setdefault("translations", {})
            key = (run_id, adv["recommendation_hash"], other)
            if key not in cache:
                env = {k: v for k, v in ss["run_env"].get(run_id, {}).items() if k == "AGRICREDIT_LLM_BACKEND"}
                with st.spinner("Translating…"):
                    cache[key] = run_with_env(lambda: translate_explanation(state, other), env)
            text, by = cache[key]
        with st.container(border=True):
            st.markdown("**In the AI's words**")
            st.write(H.clean_narrative(text))
            st.caption("Written by Qwen 2.5 7B, an open AI model running on this server. It never decides and never "
                       "calculates: the figures above come from fixed formulas, and its text is checked against them.")
    else:
        st.caption("Fast mode: the summary above uses standard wording. The figures and the advice are the same as "
                   "with the AI writer.")


def evidence_tab(state: dict):
    w, m, a, case = state.get("weather") or {}, state.get("market") or {}, state.get("assessment") or {}, state["case"]
    ev = (state.get("advisory") or {}).get("evidence", [])
    crop = H.crop_name(case["crop"])
    c1, c2 = st.columns(2)
    with c1, st.container(border=True):
        st.markdown('<div class="ev-title">🌧️ Rainfall this season</div>', unsafe_allow_html=True)
        if w:
            anom = w["rainfall_anomaly_pct"]
            st.markdown(f'<div class="ev-big">{w["rainfall_mm"]:,.0f} mm</div><div class="ev-note">{abs(anom):.0f}% '
                        f'{"below" if anom < 0 else "above"} the usual {w["seasonal_baseline_mm"]:,.0f} mm for this area '
                        f'({"drought" if w["drought_indicator"] else "no drought"}).</div>', unsafe_allow_html=True)
            df = pd.DataFrame({"what": ["This season", "Usual"], "mm": [w["rainfall_mm"], w["seasonal_baseline_mm"]]})
            st.altair_chart(alt.Chart(df).mark_bar(cornerRadiusEnd=4).encode(
                x=alt.X("mm:Q", title=None), y=alt.Y("what:N", sort=None, title=None),
                color=alt.Color("what:N", scale=alt.Scale(domain=["This season", "Usual"], range=[GREEN, "#C9C2B2"]),
                                legend=None)).properties(height=90),
                width="stretch")
            cross = next((e for e in ev if e["label"].startswith("Independent rainfall")), None)
            if cross:
                st.markdown(f'<div class="muted">Second opinion (NASA): {cross["value"].split(";")[0]}. When sources '
                            'disagree, AgriCredit shows both instead of guessing.</div>', unsafe_allow_html=True)
            if w.get("from_cache_fallback"):
                st.warning("The rainfall service was down, so a saved reading was used.")
            st.markdown('<div class="src">Source: <a href="https://open-meteo.com/en/docs/historical-weather-api" '
                        'target="_blank">ERA5 satellite reanalysis via Open-Meteo ↗</a></div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="ev-note">No rainfall data could be retrieved.</div>', unsafe_allow_html=True)
    with c2, st.container(border=True):
        st.markdown(f'<div class="ev-title">🏷️ {crop} price at {H.market_name(m.get("market_centre"))}</div>',
                    unsafe_allow_html=True)
        if m and m.get("latest_price_fcfa_per_kg"):
            price = m["latest_price_fcfa_per_kg"]
            usual, sa = m.get("seasonal_median_fcfa_per_kg"), m.get("seasonal_anomaly_pct")
            obs = pd.to_datetime(m["observation_timestamp"])
            st.markdown(f'<div class="ev-big">{price:,.0f} FCFA/kg</div><div class="ev-note">Latest price, '
                        f'{obs:%B %Y}' + (f'. Usual for {obs:%B}: {usual:,.0f} FCFA/kg ({abs(sa):.0f}% '
                                          f'{"lower" if sa < 0 else "higher"}).' if usual and sa is not None else ".")
                        + '</div>', unsafe_allow_html=True)
            mm = m.get("monthly_median_fcfa_per_kg") or {}
            if len(mm) >= 6:
                months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                df = pd.DataFrame({"month": months, "usual": [mm.get(str(i + 1)) for i in range(12)], "n": range(12)})
                line = alt.Chart(df).mark_line(point=True, color="#C9A24A").encode(
                    x=alt.X("month:N", sort=months, title=None, axis=alt.Axis(labelAngle=0)), y=alt.Y("usual:Q", title="FCFA/kg"))
                now_pt = alt.Chart(pd.DataFrame({"month": [months[obs.month - 1]], "usual": [price]})).mark_point(
                    size=160, filled=True, color="#8A1C17").encode(x=alt.X("month:N", sort=months), y="usual:Q")
                st.altair_chart((line + now_pt).properties(height=150), width="stretch")
                st.caption("Gold line: usual price each month (recent years). Red dot: price now.")
            st.markdown('<div class="src">Source: <a href="https://data.humdata.org/dataset/wfp-food-prices-for-benin" '
                        'target="_blank">World Food Programme market prices ↗</a></div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="ev-note">No recent market price was found.</div>', unsafe_allow_html=True)
    c3, c4 = st.columns(2)
    with c3, st.container(border=True):
        st.markdown('<div class="ev-title">🌾 Expected harvest</div>', unsafe_allow_html=True)
        off = next((e for e in ev if e["label"].startswith(f"Official {case['crop']} yield")), None)
        if a:
            st.markdown(f'<div class="ev-big">{a["expected_yield_kg_per_ha"]:,.0f} kg/ha</div><div class="ev-note">'
                        f'Expected on {a["area_ha"]:g} ha with the fertiliser, from the farmer\'s own past harvests.'
                        '</div>', unsafe_allow_html=True)
        if off:
            st.markdown(f'<div class="muted">Official average in the commune: {off["value"].split(" (")[0]} '
                        f'({off["label"].split(", ")[-1]}).</div>', unsafe_allow_html=True)
        st.markdown('<div class="src">Source: <a href="https://github.com/HarvestStat/HarvestStat-Africa" '
                    'target="_blank">official statistics (DSA Benin, via HarvestStat) ↗</a> · <a href='
                    '"https://doi.org/10.3389/fsufs.2021.605610" target="_blank">fertiliser trials in Benin ↗</a></div>',
                    unsafe_allow_html=True)
    with c4, st.container(border=True):
        st.markdown('<div class="ev-title">🧺 What the loan buys</div>', unsafe_allow_html=True)
        pkg = case.get("package") or {}
        st.markdown(f'<div class="ev-big">{H.fcfa(pkg.get("cost_fcfa"))}</div><div class="ev-note">'
                    f'{(lambda t: t[:1].upper() + t[1:])(H.package_plain(pkg.get("name")))}.</div>', unsafe_allow_html=True)
        lp = case.get("loan_product") or {}
        if lp:
            st.markdown(f'<div class="muted">{lp["name"]}: {lp["interest_rate_pct"]:g}% a year, {lp["fee_pct"]:g}% '
                        f'fee, {case["tenor_months"]} months.</div>', unsafe_allow_html=True)
        st.markdown('<div class="src">Source: <a href="https://www.gouv.bj/article/3046/production-agricole-campagne-'
                    'agricole-2025-2026-lancee-dassa-zoume/" target="_blank">Government of Benin, 2025/26 prices ↗'
                    '</a></div>', unsafe_allow_html=True)


def stress_tab(state: dict):
    a = state.get("assessment")
    if not a:
        st.info("The repayment calculation could not be completed for this request.")
        return
    st.markdown("#### Can the farmer still repay if the season goes badly?")
    st.markdown('<div class="ev-note">AgriCredit replays the loan in four seasons. Above the dashed line, the harvest '
                'covers what the farmer owes.</div>', unsafe_allow_html=True)
    df = pd.DataFrame([{"season": H.SCENARIOS.get(r["scenario"], r["scenario"]),
                        "times": round(r["repayment_capacity_ratio"], 2),
                        "ok": "Can repay" if r["repayment_capacity_ratio"] >= 1 else "Cannot repay"}
                       for r in a["stress_results"]])
    bars = alt.Chart(df).mark_bar(cornerRadiusEnd=5).encode(
        y=alt.Y("season:N", sort=None, title=None), x=alt.X("times:Q", title="Times what the farmer owes"),
        color=alt.Color("ok:N", scale=alt.Scale(domain=["Can repay", "Cannot repay"], range=[GREEN, "#8A1C17"]),
                        legend=alt.Legend(title=None, orient="bottom")),
        tooltip=["season", "times"])
    text = bars.mark_text(align="left", dx=6, fontWeight="bold").encode(text=alt.Text("times:Q", format=".1f"),
                                                                          color=alt.value(INK))
    rule = alt.Chart(pd.DataFrame({"x": [1.0]})).mark_rule(strokeDash=[6, 4], color=INK).encode(x="x:Q")
    st.altair_chart((bars + text + rule).properties(height=230), width="stretch")
    k = st.columns(3)
    for col, (label, v) in zip(k, [("Expected harvest value", a["expected_revenue_fcfa"]),
                                   ("Other farming costs", a["expected_cost_fcfa"]),
                                   ("Amount to repay", a["debt_service_fcfa"])]):
        with col, st.container(border=True):
            st.markdown(f'<div class="kpi"><div class="v" style="font-size:24px">{H.fcfa(v)}</div>'
                        f'<div class="l">{label}</div></div>', unsafe_allow_html=True)
    with st.expander("The assumptions behind these numbers"):
        for x in (state.get("advisory") or {}).get("assumptions", []):
            st.markdown(f"- {x}")


def farmer_tab(state: dict):
    h = state.get("household")
    if not h:
        st.info("No farmer record was found.")
        return
    f = H.farmer_summary(h, state["case"]["crop"])
    c1, c2 = st.columns(2)
    with c1, st.container(border=True):
        st.markdown('<div class="ev-title">🧑🏾‍🌾 Farm</div>', unsafe_allow_html=True)
        for p in f["plots"]:
            st.markdown(f'{H.crop_icon(p["crop"])} {p["area_ha"]:g} ha of {H.crop_name(p["crop"]).lower()}')
        if f["obligations"]:
            for o in f["obligations"]:
                st.markdown(f'<div class="muted">Other loan: {H.fcfa(o["monthly_due_fcfa"])} a month '
                            f'({H.fcfa(o["outstanding_amount_fcfa"])} left to pay)</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="muted">No other loans to repay.</div>', unsafe_allow_html=True)
    with c2, st.container(border=True):
        st.markdown(f'<div class="ev-title">🤝 Past loans</div><div class="ev-note"><b>{f["record"]}</b></div>',
                    unsafe_allow_html=True)
        for l in f["loans"]:
            mark = "❌ not repaid" if l["defaulted"] else (f"⏰ {l['days_overdue']} days late" if l["days_overdue"]
                                                          else "✅ on time")
            st.markdown(f'<div class="okline">{H.fcfa(l["amount_fcfa"])} · due {l["due_date"]} · {mark}</div>',
                        unsafe_allow_html=True)
    if f["yields"]:
        with st.container(border=True):
            st.markdown(f'<div class="ev-title">📈 {H.crop_name(state["case"]["crop"])} harvests on this farm</div>',
                        unsafe_allow_html=True)
            df = pd.DataFrame([{"season": y["season"], "kg/ha": y["yield_kg_per_ha"]} for y in f["yields"]])
            st.altair_chart(alt.Chart(df).mark_bar(color=GOLD, cornerRadiusEnd=4, size=40).encode(
                x=alt.X("season:N", title=None, axis=alt.Axis(labelAngle=0)), y=alt.Y("kg/ha:Q")).properties(height=180), width="stretch")
    if h.get("synthetic"):
        st.caption("This farmer is simulated for the demo: real repayment records are private. The commune, "
                   "rainfall, prices and official yields are real.")


def sources_tab():
    st.markdown("#### Where every number comes from")
    st.markdown('<div class="links">' + "".join(
        f'<div><b>{t}</b> — <a href="{u}" target="_blank">{n} ↗</a></div>' for t, n, u in H.LEARN_MORE) + "</div>",
        unsafe_allow_html=True)


def technical(state: dict, run_id: str):
    st.write("")
    with st.expander("🔍 Technical record (for auditors and developers)"):
        adv = state.get("advisory") or {}
        st.markdown("**What the agent did, step by step**")
        for x in state.get("activity", []):
            tool = f" · tool `{x['tool']}`" if x.get("tool") else ""
            st.markdown(f"- **{x['node']}**: {x['message']}{tool}")
        calls = read_jsonl("tool_calls.jsonl", run_id=run_id)
        if calls:
            st.markdown(f"**MCP tool calls in this review ({len(calls)})**")
            st.dataframe(pd.DataFrame([{"time": c["timestamp"][11:19], "step": c["agent_node"], "server": c["mcp_server"],
                                        "tool": c["tool_name"], "ok": "✅" if c["success"] else "❌",
                                        "ms": round(c["latency_ms"]), "sources": len(c["source_ids"])} for c in calls]),
                         hide_index=True, width="stretch")
        if adv:
            st.markdown("**Evidence cited in the advice**")
            st.dataframe(pd.DataFrame([{"evidence": e["label"], "value": e["value"], "kind": e["kind"],
                                        "source": e["source_uri"]} for e in adv.get("evidence", [])]),
                         hide_index=True, width="stretch")
            st.caption(f"Advice fingerprint {adv.get('recommendation_hash', '')[:16]}… · evidence version "
                       f"{adv.get('evidence_version', '')} · writer {adv.get('model_version', '')} · risk model "
                       f"{adv.get('risk_model_version', '')}")
        events = read_jsonl("audit_events.jsonl", case_id=state.get("case_id"))
        if events:
            st.markdown(f"**Audit trail for this request ({len(events)} events)**")
            st.dataframe(pd.DataFrame([{"time": e["timestamp"][:19].replace("T", " "), "event": e["event_type"],
                                        "by": e["actor"]} for e in events]), hide_index=True, width="stretch")


# ---------------------------------------------------------------- router
if ss["view"] == "case" and ss.get("case_id"):
    case_view(ss["case_id"])
else:
    inbox()
