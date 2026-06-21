"""
streamlit_app.py
─────────────────
Leads Radar configuration UI.
Deploy on Streamlit Community Cloud (free) — connects to your GitHub repo.

SETUP:
  1. Deploy this repo on share.streamlit.io
  2. Add GITHUB_TOKEN secret in Streamlit Cloud settings
     (needs repo scope: read/write contents + actions:write)
  3. Set GITHUB_REPO secret to "sya-sys/leads-radar"

LOCAL USE:
  streamlit run streamlit_app.py
  Requires GITHUB_TOKEN and GITHUB_REPO in your .env file.

WHAT IT DOES:
  - Reads config.json from GitHub
  - Lets you edit sources, output columns, schedule
  - Saves → commits config.json to main branch
  - Trigger button → runs GitHub Actions workflow dispatch
"""

import json
import os

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Leads Radar",
    page_icon="📡",
    layout="wide",
)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "sya-sys/leads-radar")
CONFIG_PATH = "config.json"


# ── GitHub helpers ────────────────────────────────────────────────────────────

@st.cache_resource
def _get_repo():
    from github import Github
    if not GITHUB_TOKEN:
        return None
    return Github(GITHUB_TOKEN).get_repo(GITHUB_REPO)


def _load_config_from_github() -> tuple[dict, str]:
    """Returns (config_dict, file_sha)."""
    repo = _get_repo()
    if repo is None:
        st.error("GITHUB_TOKEN not set. Add it to Streamlit secrets or .env.")
        st.stop()
    contents = repo.get_contents(CONFIG_PATH)
    config = json.loads(contents.decoded_content.decode("utf-8"))
    return config, contents.sha


def _save_config_to_github(config: dict, sha: str, message: str = "chore: update config via UI"):
    repo = _get_repo()
    repo.update_file(
        path=CONFIG_PATH,
        message=message,
        content=json.dumps(config, indent=2, ensure_ascii=False),
        sha=sha,
    )


def _trigger_workflow():
    repo = _get_repo()
    workflow = repo.get_workflow("leads_radar.yml")
    workflow.create_dispatch(ref="main")


def _get_last_run_stats() -> dict:
    """Fetch debug_run.json from repo if it exists."""
    try:
        repo = _get_repo()
        contents = repo.get_contents("output/debug_run.json")
        return json.loads(contents.decoded_content.decode("utf-8"))
    except Exception:
        return {}


# ── App ───────────────────────────────────────────────────────────────────────

st.title("📡 Leads Radar")
st.caption(f"Repo: `{GITHUB_REPO}` · Config: `{CONFIG_PATH}`")

# Load config once per session (or on refresh)
if "config" not in st.session_state or st.button("↻ Refresh from GitHub", key="refresh"):
    with st.spinner("Loading config from GitHub..."):
        config, sha = _load_config_from_github()
        st.session_state.config = config
        st.session_state.sha = sha

config: dict = st.session_state.config
sha: str = st.session_state.sha

tab_sources, tab_output, tab_schedule, tab_run = st.tabs(
    ["🔍 Sources", "📋 Output columns", "🕐 Schedule", "▶ Run"]
)

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — SOURCES
# ─────────────────────────────────────────────────────────────────────────────
with tab_sources:
    st.subheader("Sources")
    st.caption("Toggle sources on/off and edit search queries. Click **Save** when done.")

    sources = config.get("sources", [])

    for i, source in enumerate(sources):
        sid = source["id"]
        tool = source.get("tool", "")
        with st.expander(f"{'✅' if source.get('enabled') else '⬜'} **{sid}** ({tool})", expanded=source.get("enabled", False)):
            col1, col2 = st.columns([1, 3])

            with col1:
                enabled = st.toggle("Enabled", value=source.get("enabled", False), key=f"enabled_{i}")
                sources[i]["enabled"] = enabled

                if tool == "apify":
                    max_results = st.number_input("Max results", min_value=1, max_value=200,
                                                   value=source.get("max_results", 50), key=f"max_{i}")
                    sources[i]["max_results"] = max_results
                else:
                    rpp = st.number_input("Results/query", min_value=1, max_value=10,
                                          value=source.get("results_per_query", 5), key=f"rpp_{i}")
                    sources[i]["results_per_query"] = rpp

                if "site" in source:
                    site = st.text_input("Site", value=source.get("site", ""), key=f"site_{i}")
                    sources[i]["site"] = site

            with col2:
                queries_raw = "\n".join(source.get("queries", []))
                new_queries = st.text_area(
                    "Queries (one per line)",
                    value=queries_raw,
                    height=120,
                    key=f"queries_{i}",
                    help="Each line is a separate search query."
                )
                sources[i]["queries"] = [q.strip() for q in new_queries.splitlines() if q.strip()]

    config["sources"] = sources

# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — OUTPUT COLUMNS
# ─────────────────────────────────────────────────────────────────────────────
with tab_output:
    st.subheader("Output columns")
    st.caption("Check columns to include in leads.csv and Google Sheets. CRM columns are always included.")

    ALL_POSSIBLE_COLUMNS = [
        "company_name", "company_sector", "company_size",
        "job_title", "seniority", "contract_type", "is_new_role",
        "tech_mentioned", "transformation_context",
        "signal_type", "project_type", "service_offer", "icp_score", "urgency",
        "location", "posting_date", "source", "url",
        "outreach_status", "contact_name", "notes", "opportunity_type",
    ]
    CRM_COLUMNS = {"outreach_status", "contact_name", "notes", "opportunity_type"}
    current_columns = set(config["output"].get("columns", ALL_POSSIBLE_COLUMNS))

    col_a, col_b, col_c = st.columns(3)
    selected = []

    for j, col_name in enumerate(ALL_POSSIBLE_COLUMNS):
        is_crm = col_name in CRM_COLUMNS
        target_col = [col_a, col_b, col_c][j % 3]
        with target_col:
            checked = st.checkbox(
                f"{col_name} {'🔒' if is_crm else ''}",
                value=col_name in current_columns,
                disabled=is_crm,  # CRM columns always on
                key=f"col_{col_name}",
            )
        if checked or is_crm:
            selected.append(col_name)

    # Preserve order
    config["output"]["columns"] = [c for c in ALL_POSSIBLE_COLUMNS if c in selected]
    config["output"]["crm_columns"] = list(CRM_COLUMNS)

    st.divider()
    col_sig, col_model = st.columns(2)
    with col_sig:
        signal_types_raw = "\n".join(config["extraction"].get("signal_types", []))
        new_signal_types = st.text_area(
            "Signal types (one per line)",
            value=signal_types_raw,
            height=100,
            help="Gemini will classify each lead into one of these categories."
        )
        config["extraction"]["signal_types"] = [s.strip() for s in new_signal_types.splitlines() if s.strip()]

    with col_model:
        config["extraction"]["model"] = st.selectbox(
            "Extraction model",
            options=["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"],
            index=["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"].index(
                config["extraction"].get("model", "gemini-2.0-flash")
            ),
        )
        config["extraction"]["description_max_chars"] = st.number_input(
            "Description max chars",
            min_value=200, max_value=4000,
            value=config["extraction"].get("description_max_chars", 1200),
        )

# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 — SCHEDULE
# ─────────────────────────────────────────────────────────────────────────────
with tab_schedule:
    st.subheader("Schedule")
    st.caption("Controls the GitHub Actions cron trigger. Saving here updates `.github/workflows/leads_radar.yml` via config — see README for manual re-enable steps.")

    schedule = config.get("schedule", {})
    sched_enabled = st.toggle("Enable daily run", value=schedule.get("enabled", False))
    config["schedule"]["enabled"] = sched_enabled

    if sched_enabled:
        cron = st.text_input(
            "Cron expression (UTC)",
            value=schedule.get("cron", "0 6 * * *"),
            help="e.g. '0 6 * * *' = every day at 06:00 UTC",
        )
        config["schedule"]["cron"] = cron
        st.info(f"Runs at: `{cron}` UTC · [crontab.guru](https://crontab.guru/#{cron.replace(' ', '_')})")
    else:
        st.info("Schedule paused. Runs only via manual trigger (Run tab).")

    st.divider()
    st.subheader("Failure alerts")
    threshold = st.number_input(
        "Min leads threshold",
        min_value=0, max_value=50,
        value=config.get("alerts", {}).get("min_leads_threshold", 3),
        help="If a run produces fewer new leads than this, debug_run.json gets status=ALERT.",
    )
    config.setdefault("alerts", {})["min_leads_threshold"] = threshold

# ─────────────────────────────────────────────────────────────────────────────
# TAB 4 — RUN
# ─────────────────────────────────────────────────────────────────────────────
with tab_run:
    st.subheader("Run")
    col_r1, col_r2 = st.columns(2)

    with col_r1:
        st.markdown("**Manual trigger**")
        st.caption("Triggers a GitHub Actions workflow_dispatch run (same as clicking 'Run workflow' on GitHub).")
        if st.button("▶ Run now", type="primary"):
            try:
                _trigger_workflow()
                st.success("Workflow dispatched. Check GitHub Actions for progress.")
            except Exception as exc:
                st.error(f"Failed to trigger: {exc}")

    with col_r2:
        st.markdown("**Last run stats**")
        if st.button("Load last run stats"):
            stats = _get_last_run_stats()
            if stats:
                status = stats.get("status", "?")
                color = "🟢" if status == "OK" else "🔴"
                st.metric("Status", f"{color} {status}")
                st.metric("New leads", stats.get("new_leads_count", 0))
                st.metric("Raw fetched", stats.get("raw_count", 0))
                if stats.get("sources"):
                    st.json(stats["sources"])
                if stats.get("errors"):
                    st.error("Errors: " + " | ".join(stats["errors"]))
            else:
                st.info("No debug_run.json found in repo yet.")

# ─────────────────────────────────────────────────────────────────────────────
# SAVE BUTTON (always visible at bottom)
# ─────────────────────────────────────────────────────────────────────────────
st.divider()
col_save, col_preview = st.columns([1, 3])
with col_save:
    if st.button("💾 Save config to GitHub", type="primary", use_container_width=True):
        try:
            _save_config_to_github(config, sha)
            st.session_state.pop("config", None)  # force reload on next refresh
            st.success("✅ config.json committed to main branch.")
        except Exception as exc:
            st.error(f"Save failed: {exc}")

with col_preview:
    with st.expander("Preview config.json"):
        st.json(config)
