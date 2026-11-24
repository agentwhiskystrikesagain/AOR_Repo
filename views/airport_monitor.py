from __future__ import annotations

import streamlit as st

from src.capacity import capacity_timeline, weather_assumption_rows
from src.data import AIRPORTS
from src.page_helpers import movement_table, render_scene, csv_bytes


def render() -> None:
    s=st.session_state.scenario_shared
    result=st.session_state.get("recovery_result")
    airport=s["monitor_airport"]
    st.title("Airport Operations")
    st.caption(f"Airport control area · {s['airports'][airport]} ({airport}) · synthetic schedule")
    st.info(f"{s['weather_scenario']} planning scenario · {s['config']['time_bucket_min']}-minute movement intervals · no live feed")
    disruption=s.get("disruption",{})
    if disruption.get("type")=="Security restriction":
        start=int(disruption.get("start_min",0)); end=int(disruption.get("end_min",1440))
        affected=disruption.get("affected_airport",airport)
        start_text=f"{start//60:02d}:{start%60:02d}"; end_text=f"{end//60:02d}:{end%60:02d}"
        st.warning(f"Security-risk scenario · {s['airports'].get(affected,affected)} ({affected}) has a synthetic movement restriction from {start_text} to {end_text}. No live security alert is connected.")
    weather=next((r for r in weather_assumption_rows(s) if r["Airport"]==airport),{})
    c1,c2,c3,c4=st.columns(4)
    c1.metric("Control area",f"{s['airports'][airport]} ({airport})")
    c2.metric("Weather scenario",s["weather_scenario"])
    c3.metric("Arrival delay assumption",f"{weather.get('Assumed arrival delay (min)',0)} min")
    c4.metric("Recovery plan","Available" if result and result.get("flight_plan") else "Not generated")

    render_scene(s,result,airport,"airport_scene")

    st.subheader("Flight movement schedule")
    movements=movement_table(s,airport,result)
    st.dataframe(movements,width="stretch",hide_index=True)
    if movements:
        st.download_button("Download airport movements CSV",data=csv_bytes(movements),file_name=f"{airport.lower()}_movements.csv",mime="text/csv")

    st.subheader(f"Runway movement capacity by {s['config']['time_bucket_min']}-minute interval")
    timeline=capacity_timeline(s,airport,result)
    if timeline:
        st.dataframe(timeline,width="stretch",hide_index=True)
        conflicts=[r for r in timeline if r["Predicted conflict"]]
        scheduled_conflicts=[r for r in timeline if r["Scheduled conflict"]]
        recommended_conflicts=[r for r in timeline if r["Recommended conflict"]]
        if conflicts:
            st.error(f"Capacity alert · {len(conflicts)} predicted movement conflict(s) in this control area.")
        elif scheduled_conflicts:
            st.warning(f"Schedule alert · {len(scheduled_conflicts)} baseline movement conflict(s); weather and disruption assumptions shift the predicted demand.")
        elif result and not recommended_conflicts:
            st.success("No capacity conflict remains in the recommended movement plan.")
        elif recommended_conflicts:
            st.error("The recommended plan exceeds configured airport capacity.")
        else:
            st.info(f"No movement-capacity conflict is reported for the displayed {s['config']['time_bucket_min']}-minute intervals.")
    else:
        st.info("No scheduled movements for this airport in the sample window.")

    st.subheader("Seasonal weather assumptions by airport")
    st.dataframe(weather_assumption_rows(s),width="stretch",hide_index=True)
    with st.expander("Airport operating restrictions"):
        restrictions=s["config"].get("restrictions",{})
        if disruption.get("type")=="Security restriction":
            st.write(f"Active synthetic security restriction: {disruption.get('affected_airport')} · {disruption.get('start_min')}–{disruption.get('end_min')} minutes after midnight")
        st.write(f"Closures: {restrictions.get('closures') or 'None configured'}")
        st.write(f"Curfews: {restrictions.get('curfews') or 'None configured'}")
        st.write(f"Gate / stand capacity: {restrictions.get('gate_capacity_per_15m') if restrictions.get('gate_capacity_per_15m') is not None else 'Not configured'}")
        st.write(f"Capacity data: {restrictions.get('source','Synthetic training assumptions')}")


render()
