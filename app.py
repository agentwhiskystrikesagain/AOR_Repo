from __future__ import annotations

import streamlit as st

from src.ui import configure_shared_scenario

st.set_page_config(
    page_title="Airline Operations Control",
    page_icon=":material/flight_takeoff:",
    layout="wide",
    initial_sidebar_state="expanded",
)
pages = [
    st.Page("views/recovery_planner.py", title="Disruption Recovery", icon=":material/flight_takeoff:", default=True),
    st.Page("views/airport_monitor.py", title="Airport Operations", icon=":material/connecting_airports:"),
]
current_page = st.navigation(pages, position="top", expanded=False)
scenario = configure_shared_scenario(show_airport_control=current_page.title == "Airport Operations")
brand = scenario["profile"]["brand"]
st.markdown(f"""
<style>
  :root {{ --brand-primary: {brand['primary']}; --brand-accent: {brand['accent']}; }}
  .block-container {{ padding-top: 1.35rem; padding-bottom: 2rem; }}
  h1, h2, h3 {{ letter-spacing: -.025em; }}
  div[data-testid="stMetric"] {{ border: 1px solid rgba(118,136,154,.22); border-radius: 12px; padding: .65rem .85rem; background: rgba(255,255,255,.02); }}
  .source-note {{ color: #9aa7b5; font-size: .84rem; }}
  [data-testid="stSidebar"] {{ border-right: 1px solid rgba(118,136,154,.25); }}
</style>
""", unsafe_allow_html=True)
current_page.run()
