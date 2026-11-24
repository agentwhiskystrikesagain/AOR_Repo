"""Shared page renderers, simulation clock, and flight details."""
from __future__ import annotations

import csv
import io
from typing import Any

import streamlit as st

from .capacity import capacity_timeline, predicted_times
from .data import time_text
from .scene import make_deck, selected_object


@st.fragment(run_every="0.25s")
def _render_scene_fragment(scenario: dict[str, Any], result: dict[str, Any] | None, airport: str, key: str) -> None:
    """Run the shared schedule clock and redraw only the aircraft scene."""
    departures = [int(f["scheduled_departure_min"]) for f in scenario["flights"]]
    arrivals = [int(f["scheduled_arrival_min"]) for f in scenario["flights"]]
    start = max(0, min(departures, default=480) - 20)
    end = min(1439, max(arrivals, default=840) + 10)
    disruption = scenario.get("disruption", {})
    inbound_times = []
    outbound_times = []
    planned = {f["flight_id"]: f for f in (result or {}).get("flight_plan", [])}
    for flight in scenario["flights"]:
        delay = int(disruption.get("minimum_delay_min", 0)) if disruption.get("type") == "Flight delay" and disruption.get("affected_flight") == flight["flight_id"] else 0
        if disruption.get("type") == "Aircraft unavailable" and flight["original_aircraft"] == disruption.get("affected_aircraft"):
            delay = max(delay, int(disruption.get("release_min", 0)) - int(flight["scheduled_departure_min"]))
        dep, arr = predicted_times(flight, max(0, delay), scenario)
        if flight["flight_id"] in planned:
            op = planned[flight["flight_id"]]
            if op["status"] == "Cancelled":
                continue
            dep, arr = int(op["departure_min"]), int(op["arrival_min"])
        if flight["destination"] == airport:
            inbound_times.append(arr)
        if flight["origin"] == airport:
            outbound_times.append(dep)
    first_event = min(inbound_times) - 25 if inbound_times else (min(outbound_times) - 10 if outbound_times else start)
    default_minute = max(start, min(end, first_event))
    if "simulation_min" not in st.session_state or st.session_state.get("simulation_clock_airport") != airport:
        st.session_state.simulation_min = default_minute
        st.session_state.simulation_clock_airport = airport
    st.session_state.simulation_min = max(start, min(end, int(st.session_state.simulation_min)))
    if "simulation_playing" not in st.session_state:
        st.session_state.simulation_playing = True
    if "simulation_clock_revision" not in st.session_state:
        st.session_state.simulation_clock_revision = 0

    with st.container(border=True):
        st.caption("Flight replay clock · IST · 4 simulated minutes per second")
        if st.session_state.simulation_playing:
            if st.button("Pause simulation", key=f"{key}_pause", width="content", icon=":material/pause:"):
                st.session_state.simulation_playing = False
                st.session_state.simulation_clock_revision += 1
            else:
                new_time = min(end, int(st.session_state.simulation_min) + 1)
                st.session_state.simulation_min = new_time
                if new_time >= end:
                    st.session_state.simulation_playing = False
                    st.session_state.simulation_clock_revision += 1
        else:
            slider_key = f"shared_simulation_clock_{st.session_state.simulation_clock_revision}"
            clock_value = st.slider("Set simulation time", min_value=start, max_value=end,
                                    value=int(st.session_state.simulation_min), step=1,
                                    key=slider_key, format="%d min")
            st.session_state.simulation_min = int(clock_value)
            left, right = st.columns(2)
            if left.button("Play flights", key=f"{key}_play", width="stretch", icon=":material/play_arrow:"):
                st.session_state.simulation_playing = True
                st.session_state.simulation_clock_revision += 1
            if right.button("Reset time", key=f"{key}_reset", width="stretch", icon=":material/restart_alt:"):
                st.session_state.simulation_min = start
                st.session_state.simulation_clock_revision += 1

        minute = int(st.session_state.simulation_min)
        st.caption(f"REPLAY TIME · {minute // 60:02d}:{minute % 60:02d} IST")
        try:
            deck, points = make_deck(scenario, airport, result,
                                     selected_flight=st.session_state.get("ui_selected_flight_id"),
                                     simulation_min=minute)
            selection = st.pydeck_chart(deck, height=570, width="stretch", on_select="rerun",
                                       selection_mode="single-object", key=key)
        except ImportError:
            st.warning("The interactive airport scene needs PyDeck. Install the dependencies from requirements.txt; schedule and capacity tables remain available.")
            return
        except TypeError:
            try:
                deck, points = make_deck(scenario, airport, result,
                                         selected_flight=st.session_state.get("ui_selected_flight_id"),
                                         simulation_min=minute)
                st.pydeck_chart(deck, height=570, width="stretch", key=key)
                selection = None
                st.caption("Use the flight selector below to inspect an aircraft.")
            except Exception as exc:
                st.warning(f"The airport scene could not render: {exc}")
                return
        except Exception as exc:
            st.warning(f"The airport scene could not render: {exc}")
            return

        selected = selected_object(selection)
        if selected and selected.get("flight_id"):
            st.session_state.ui_selected_flight_id = selected["flight_id"]
        flight_by_id = {flight["flight_id"]: flight for flight in scenario["flights"]}
        visible_points = {point["flight_id"]: point for point in points}
        options = list(flight_by_id)
        if options:
            default_flight = scenario.get("disruption", {}).get("affected_flight")
            active = st.session_state.get("ui_selected_flight_id", default_flight)
            if active not in options:
                active = options[0]
            if st.session_state.get("ui_selected_flight_id") != active:
                st.session_state.ui_selected_flight_id = active

            def inspector_option(fid: str) -> str:
                flight = flight_by_id[fid]
                dep = int(flight["scheduled_departure_min"])
                if fid in visible_points:
                    location = f"🟢 {visible_points[fid]['phase']}"
                elif airport in (flight["origin"], flight["destination"]):
                    location = "Not visible at replay time"
                else:
                    location = f"Outside {airport} control area"
                return (f"{fid} · {flight['origin']} → {flight['destination']} · "
                        f"{dep // 60:02d}:{dep % 60:02d} IST · {location}")

            st.selectbox(
                "Inspect flight · full airline schedule",
                options,
                index=None,
                format_func=inspector_option,
                key="ui_selected_flight_id",
                help="The 3D scene shows aircraft at this airport; this selector also includes flights elsewhere in the airline network.",
            )
            st.caption("🟢 Aircraft marker currently shown in the 3D scene · no dot means outside this airport scene at replay time.")
            selected_flight_details(scenario, result, airport, st.session_state.ui_selected_flight_id)
        else:
            st.info(f"No aircraft on stand or approach at {airport} at {minute // 60:02d}:{minute % 60:02d}.")


def render_scene(scenario: dict[str, Any], result: dict[str, Any] | None, airport: str, key: str) -> None:
    st.markdown("### 3D flight movement replay")
    effect = scenario["config"]["weather_effects"][scenario["weather_scenario"]].get(airport, {})
    st.caption(f"SIMULATION · SYNTHETIC SCHEDULE · NOT LIVE TRAFFIC · {scenario['weather_scenario']} at {airport}: +{effect.get('delay_min', 0)} min arrival assumption")
    legend = st.columns(4)
    for column, label, color, icon in zip(
        legend,
        ("Scheduled / operated", "Delayed", "Cancelled", "Aircraft in flight"),
        ("green", "orange", "red", "blue"),
        ("check_circle", "schedule", "cancel", "flight"),
    ):
        with column:
            st.badge(label, color=color, icon=f":material/{icon}:")
    _render_scene_fragment(scenario, result, airport, key)


def selected_flight_details(scenario: dict[str, Any], result: dict[str, Any] | None, airport: str, flight_id: str) -> None:
    flight = next((item for item in scenario["flights"] if item["flight_id"] == flight_id), None)
    if not flight:
        return
    plan = next((item for item in (result or {}).get("flight_plan", []) if item["flight_id"] == flight_id), None)
    disruption = scenario["disruption"]
    delay = int(disruption.get("minimum_delay_min", 0)) if disruption.get("type") == "Flight delay" and disruption.get("affected_flight") == flight_id else 0
    if disruption.get("type") == "Aircraft unavailable" and flight["original_aircraft"] == disruption.get("affected_aircraft"):
        delay = max(delay, int(disruption.get("release_min", 0)) - int(flight["scheduled_departure_min"]))
    dep, arr = predicted_times(flight, max(0, delay), scenario)
    status, aircraft = "Scheduled sample", flight["original_aircraft"]
    if plan:
        status, aircraft = plan["status"], plan.get("aircraft_id") or "—"
        if status == "Cancelled":
            dep = arr = None
        else:
            dep, arr = plan.get("departure_min", dep), plan.get("arrival_min", arr)
    st.markdown(f"**{scenario['profile']['display_name']} · {flight_id} · {flight['origin']} → {flight['destination']}**")
    st.write(f"Scheduled departure / arrival: {time_text(flight['scheduled_departure_min'])} / {time_text(flight['scheduled_arrival_min'])} · Predicted / recommended: {time_text(dep)} / {time_text(arr)}")
    st.write(f"Status: **{status}** · Aircraft: **{aircraft}** · Seats: **{plan.get('seats', 'profile configured') if plan else 'profile configured'}**")
    if airport not in (flight["origin"], flight["destination"]):
        st.write(f"Airport capacity at {airport}: **Not applicable · this flight does not use this airport.**")
    else:
        bucket = int(scenario["config"].get("time_bucket_min", 15))
        event_minute = arr if flight["destination"] == airport else dep
        rows = capacity_timeline(scenario, airport, result)
        movement = "Arrival" if flight["destination"] == airport else "Departure"
        capacity_row = next((row for row in rows if event_minute is not None and row["Window start (min)"] == event_minute // bucket * bucket and row["Movement"] == movement), None)
        if result:
            issue = bool(capacity_row and capacity_row["Recommended conflict"])
            st.write("Selected-plan airport capacity issue: **" + (f"Over capacity in {capacity_row['Window (local)']}" if issue else "No selected-plan capacity conflict in this window") + "**")
        else:
            issue = bool(capacity_row and capacity_row["Predicted conflict"])
            st.write("Pre-recovery capacity issue: **" + (f"Conflict in {capacity_row['Window (local)']}" if issue else "No predicted conflict in the displayed sample bucket") + "**")
    risks = [row for row in (result or {}).get("connection_risks", []) if flight_id in row.get("Connection", "")]
    st.write("Connection risk: **" + (risks[0]["Risk"] if risks else "See passenger recovery table; sample connection rules use group minimum connection times") + "**")


def movement_table(scenario: dict[str, Any], airport: str, result: dict[str, Any] | None) -> list[dict[str, Any]]:
    plan = {item["flight_id"]: item for item in (result or {}).get("flight_plan", [])}
    rows = []
    for flight in scenario["flights"]:
        if airport not in (flight["origin"], flight["destination"]):
            continue
        disruption = scenario["disruption"]
        delay = int(disruption.get("minimum_delay_min", 0)) if disruption.get("type") == "Flight delay" and disruption.get("affected_flight") == flight["flight_id"] else 0
        dep, arr = predicted_times(flight, delay, scenario)
        selected = plan.get(flight["flight_id"])
        if selected:
            if selected["status"] == "Cancelled":
                dep = arr = None
            else:
                dep, arr = selected.get("departure_min", dep), selected.get("arrival_min", arr)
            status, aircraft = selected["status"], selected.get("aircraft_id") or "—"
        else:
            status, aircraft = "Pre-recovery estimate", flight["original_aircraft"]
        rows.append({"Airline": scenario["profile"]["display_name"], "Flight": flight["flight_id"], "Movement": "Arrival" if flight["destination"] == airport else "Departure",
                     "Route": f"{flight['origin']} → {flight['destination']}", "Scheduled departure": time_text(flight["scheduled_departure_min"]),
                     "Scheduled arrival": time_text(flight["scheduled_arrival_min"]), "Predicted departure": time_text(dep),
                     "Predicted arrival": time_text(arr), "Aircraft": aircraft, "Status": status, "Data": "Synthetic sample"})
    return sorted(rows, key=lambda row: (row["Predicted arrival"] if row["Movement"] == "Arrival" else row["Predicted departure"], row["Flight"]))


def csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    if not rows:
        return b""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")
