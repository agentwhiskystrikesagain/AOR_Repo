"""Shared controls and Streamlit session-state wiring for both work areas."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import streamlit as st

from .data import AIRPORTS, build_scenario, load_config

SCHEDULE_DATA_VERSION = "recovery-costs-only-v4"


def _clock_time_label(minutes_after_midnight: int) -> str:
    minute=int(minutes_after_midnight)
    if minute==1440: return "24:00 (midnight)"
    return f"{minute//60:02d}:{minute%60:02d}"


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def configure_shared_scenario(show_airport_control: bool = False) -> dict[str, Any]:
    cfg=load_config()
    if "ui_profile_id" not in st.session_state: st.session_state.ui_profile_id=cfg["profiles"][0]["profile_id"]
    selected_profile=st.sidebar.selectbox("Airline",[p["profile_id"] for p in cfg["profiles"]],
        format_func=lambda pid: next(p["display_name"] for p in cfg["profiles"] if p["profile_id"]==pid),key="ui_profile_id",
        help="All five profiles use synthetic aircraft, schedules, and editable cost assumptions.")
    if ("scenario_shared" not in st.session_state
            or st.session_state.get("_loaded_profile_id")!=selected_profile
            or st.session_state.get("_schedule_data_version")!=SCHEDULE_DATA_VERSION):
        st.session_state.scenario_shared=build_scenario(selected_profile)
        st.session_state._loaded_profile_id=selected_profile
        st.session_state._schedule_data_version=SCHEDULE_DATA_VERSION
        st.session_state.recovery_result=None
        for key in ("ui_monitor_airport","ui_origin_airport","ui_destination_airport","ui_weather","ui_disruption_type","ui_flight","ui_aircraft","ui_turnaround_min","ui_delay_choices","ui_capacity_bucket_min","ui_disrupted_origin","ui_disrupted_destination","ui_selected_flight_id","simulation_min","simulation_clock_airport","simulation_clock_revision"):
            st.session_state.pop(key,None)
    scenario=st.session_state.scenario_shared
    default_case={key:scenario["disruption"].get(key) for key in ("cause_status","cause","notice_hours","notice_method","check_in_status","alternative_offered","passenger_choice","contact_information_available","meals_provided","hotel_provided")}
    for group in scenario["passenger_groups"]:
        group.setdefault("protection",dict(default_case))
    selected_airline=next(p for p in cfg["profiles"] if p["profile_id"]==selected_profile)
    st.sidebar.markdown(f"<div style='border-left:4px solid {selected_airline['brand']['primary']};padding:.55rem .7rem;margin:.15rem 0 .7rem'><small>FICTIONAL TRAINING PROFILE</small><br><b>{selected_airline['display_name']}</b></div>",unsafe_allow_html=True)

    operations=scenario["profile"]["operations"]
    turnaround=st.sidebar.slider("Minimum turnaround (minutes)",0,180,int(operations.get("minimum_turnaround_min",40)),5,key="ui_turnaround_min",
        help="Minimum ground time between an aircraft arrival and its next departure. Enforced as a hard operating constraint.")
    with st.sidebar.expander("Fleet and cost parameters",expanded=False):
        delay_choices=st.multiselect("Permitted departure delay increments (minutes)",list(range(0,181,15)),default=operations.get("delay_choices_min",[0,15,30,45,60,75,90]),key="ui_delay_choices")
        st.caption("The recovery plan can select only one of these delay increments per flight.")
        cost_fields=[("delay_per_flight_min_inr","Aircraft delay cost per minute (INR)"),("cancellation_inr","Cancellation cost per flight (INR)"),("passenger_delay_per_hour_inr","Passenger final-arrival delay cost per hour (INR)"),("rebooking_per_passenger_inr","Rebooking cost per passenger (INR)"),("refund_per_passenger_inr","Refund cost per passenger (INR)"),("meal_per_passenger_inr","Meal support assumption per passenger (INR)"),("hotel_per_passenger_inr","Hotel support assumption per passenger (INR)")]
        edited_costs={}
        for field,label in cost_fields:
            edited_costs[field]=st.number_input(label,min_value=0,max_value=1000000,step=100,value=int(scenario["profile"]["costs"].get(field,0)),key=f"ui_cost_{selected_profile}_{field}")
        st.caption("Monetary rates are synthetic editable assumptions in INR.")
    scenario["profile"]["operations"]["minimum_turnaround_min"]=int(turnaround)
    if delay_choices: scenario["profile"]["operations"]["delay_choices_min"]=sorted(set(map(int,delay_choices)))
    else: st.sidebar.warning("Select at least one allowed delay value. The optimizer will report infeasibility if no operating option remains.")
    scenario["profile"]["costs"].update({k:int(v) for k,v in edited_costs.items()})

    codes=list(scenario["airports"])
    airport_names=scenario["airports"]
    monitor=st.session_state.get("ui_monitor_airport",scenario.get("monitor_airport","BLR"))
    if monitor not in codes:
        monitor="BLR" if "BLR" in codes else codes[0]
    with st.sidebar.expander("Network and airport filters",expanded=True):
        if show_airport_control:
            monitor=st.selectbox("Airport control area",codes,index=codes.index(monitor),format_func=lambda c:f"{airport_names[c]} ({c})",key="ui_monitor_airport")
        origin=st.selectbox("Passenger origin",codes,index=codes.index(scenario.get("origin_airport","DEL")),format_func=lambda c:f"{airport_names[c]} ({c})",key="ui_origin_airport")
        destination=st.selectbox("Passenger final destination",codes,index=codes.index(scenario.get("destination_airport","BOM")),format_func=lambda c:f"{airport_names[c]} ({c})",key="ui_destination_airport")
        connects=st.multiselect("Connection airports to monitor",codes,default=scenario.get("connecting_airports",["BLR"]),format_func=lambda c:f"{airport_names[c]} ({c})",key="ui_connecting_airports")
    with st.sidebar.expander("Weather scenario and impacts",expanded=False):
        weather=st.selectbox("Seasonal scenario",cfg["weather_scenarios"],index=cfg["weather_scenarios"].index(scenario.get("weather_scenario","Monsoon")),key="ui_weather",
            help="Seasonal planning preset; not a live forecast.")
        effect=scenario["config"]["weather_effects"][weather].setdefault(monitor,{"arrival_capacity_delta":0,"departure_capacity_delta":0,"delay_min":0})
        arr_delta=st.slider(f"{weather} arrival capacity change at {monitor}",-10,10,int(effect.get("arrival_capacity_delta",0)),key=f"ui_weather_arr_{weather}_{monitor}")
        dep_delta=st.slider(f"{weather} departure capacity change at {monitor}",-10,10,int(effect.get("departure_capacity_delta",0)),key=f"ui_weather_dep_{weather}_{monitor}")
        weather_delay=st.slider(f"{weather} assumed arrival delay at {monitor} (minutes)",0,180,int(effect.get("delay_min",0)),5,key=f"ui_weather_delay_{weather}_{monitor}")
        st.caption("Airport-specific seasonal assumptions · no live forecast")
    disruption_types=["Flight delay","Aircraft unavailable","Flight cancelled","Airport capacity reduction","Airport closure","Security restriction"]
    disruption_labels={"Security restriction":"Security risk"}
    current_disruption=scenario["disruption"].get("type","Flight delay")
    if current_disruption not in disruption_types: current_disruption="Flight delay"
    disruption=st.sidebar.selectbox("Disruption event",disruption_types,index=disruption_types.index(current_disruption),format_func=lambda kind:disruption_labels.get(kind,kind),key="ui_disruption_type")
    flight_ids=[f["flight_id"] for f in scenario["flights"]]
    aircraft_ids=[a["aircraft_id"] for a in scenario["aircraft"]]
    flights_by_id={f["flight_id"]:f for f in scenario["flights"]}
    def flight_option(fid: str) -> str:
        item=flights_by_id[fid]
        dep=int(item["scheduled_departure_min"])
        return f"{fid}  ·  {item['origin']} → {item['destination']}  ·  {dep//60:02d}:{dep%60:02d} IST"
    start_min=int(scenario["disruption"].get("start_min",480))
    if disruption in ("Flight delay","Flight cancelled"):
        current_flight=scenario["disruption"].get("affected_flight")
        default_flight=current_flight if current_flight in flight_ids else flight_ids[0]
        flight=st.sidebar.selectbox("Flight affected by this event",flight_ids,index=flight_ids.index(default_flight),format_func=flight_option,key="ui_flight",
            help="Choose any scheduled flight in the selected airline's synthetic schedule. This is the flight the recovery model must handle.")
    else:
        flight=scenario["disruption"].get("affected_flight",flight_ids[0])
    if disruption=="Flight delay":
        min_delay=st.sidebar.slider("Minimum departure delay (minutes)",15,180,int(scenario["disruption"].get("minimum_delay_min",45)),15,key="ui_min_delay",
            help="Lower bound for the affected flight's departure delay. 45 means it cannot depart earlier than 45 minutes after schedule; the model may choose a longer permitted delay to recover aircraft or connections.")
        st.sidebar.caption(f"Earliest departure: {min_delay} min after the scheduled time. The selected plan may be later.")
    else: min_delay=int(scenario["disruption"].get("minimum_delay_min",45))
    if disruption=="Aircraft unavailable":
        aircraft=st.sidebar.selectbox("Unavailable aircraft",aircraft_ids,index=aircraft_ids.index(scenario["disruption"].get("affected_aircraft") or aircraft_ids[0]) if (scenario["disruption"].get("affected_aircraft") or aircraft_ids[0]) in aircraft_ids else 0,key="ui_aircraft")
        release=st.sidebar.slider("Aircraft release time (minutes after midnight)",360,1080,int(scenario["disruption"].get("release_min",600)),15,key="ui_release_min")
    else:
        aircraft=scenario["disruption"].get("affected_aircraft") or aircraft_ids[0]
        release=int(scenario["disruption"].get("release_min",600))
    airport_disruption_types={"Airport capacity reduction","Airport closure","Security restriction"}
    if disruption in airport_disruption_types:
        affected_airport=st.sidebar.selectbox("Affected airport",codes,index=codes.index(scenario["disruption"].get("affected_airport","BLR")),format_func=lambda c:f"{airport_names[c]} ({c})",key="ui_affected_airport")
        start_options=list(range(0,1440,15))
        current_start=int(scenario["disruption"].get("start_min",480))
        start_value=min(start_options,key=lambda value:abs(value-current_start))
        start_min=st.sidebar.select_slider("Restriction start time (IST)",options=start_options,value=start_value,format_func=_clock_time_label,key="ui_start_clock",
            help="Clock time in India Standard Time. The selected time is stored internally as minutes after midnight.")
    else: affected_airport=scenario["disruption"].get("affected_airport","BLR")
    if disruption=="Airport capacity reduction":
        cap_reduction=st.sidebar.slider("Movement capacity reduction per bucket",1,3,int(scenario["disruption"].get("capacity_reduction",1)),1,key="ui_capacity_reduction")
        end_min=1440
    else:
        cap_reduction=int(scenario["disruption"].get("capacity_reduction",1)); end_min=int(scenario["disruption"].get("end_min",720))
    if disruption in {"Airport closure","Security restriction"}:
        end_options=list(range(int(start_min)+15,1441,15))
        current_end=int(scenario["disruption"].get("end_min",720))
        end_value=min(end_options,key=lambda value:abs(value-current_end))
        end_min=st.sidebar.select_slider("Restriction end time (IST)",options=end_options,value=end_value,format_func=_clock_time_label,key=f"ui_end_clock_{start_min}",
            help="The restriction ends at this clock time. The end time itself is outside the restricted window.")
        st.sidebar.caption("The restriction window uses local IST clock times; internal calculations still use minutes after midnight.")
    if disruption=="Security restriction":
        st.sidebar.caption("Synthetic security-risk scenario: the selected airport has no arrivals or departures during this window. This is not a live security alert.")
    st.sidebar.caption("Passenger protection facts are recorded separately for each passenger group on Disruption Recovery.")
    with st.sidebar.expander("Airport capacity and restrictions",expanded=False):
        bucket=st.slider("Capacity time bucket (minutes)",5,60,int(scenario["config"].get("time_bucket_min",15)),5,key="ui_capacity_bucket_min")
        capacity=scenario["config"]["airport_capacity_per_bucket"][monitor]
        arrivals=st.number_input(f"Arrival capacity per {bucket}-minute bucket at {monitor}",min_value=0,max_value=60,value=int(capacity.get("arrival",0)),key=f"ui_arr_cap_{monitor}")
        departures=st.number_input(f"Departure capacity per {bucket}-minute bucket at {monitor}",min_value=0,max_value=60,value=int(capacity.get("departure",0)),key=f"ui_dep_cap_{monitor}")
        combined_value=capacity.get("combined")
        combined=st.number_input(f"Combined movement capacity per {bucket}-minute bucket at {monitor}",min_value=0,max_value=120,value=int(combined_value if combined_value is not None else 0),key=f"ui_comb_cap_{monitor}")
        st.caption("Capacity limits use editable time buckets. Stand capacity is not supplied in the demo.")
        current_curfews=[c for c in scenario["config"]["restrictions"].get("curfews",[]) if c.get("airport")==monitor]
        curfew_enabled=st.checkbox(f"Apply a synthetic curfew at {monitor}",value=bool(current_curfews),key=f"ui_curfew_enabled_{monitor}")
        if curfew_enabled:
            curfew_start=st.slider(f"{monitor} curfew start (minutes after midnight)",0,1430,int(current_curfews[0].get("start_min",1320) if current_curfews else 1320),15,key=f"ui_curfew_start_{monitor}")
            curfew_end=st.slider(f"{monitor} curfew end (minutes after midnight)",15,1440,int(current_curfews[0].get("end_min",360) if current_curfews else 360),15,key=f"ui_curfew_end_{monitor}")
            curfew_record={"airport":monitor,"start_min":curfew_start,"end_min":curfew_end}
        else: curfew_record=None
    scenario["config"]["time_bucket_min"]=int(bucket)
    scenario["config"]["airport_capacity_per_bucket"][monitor].update({"arrival":int(arrivals),"departure":int(departures),"combined":int(combined)})
    effect.update({"arrival_capacity_delta":int(arr_delta),"departure_capacity_delta":int(dep_delta),"delay_min":int(weather_delay)})
    curfews=[c for c in scenario["config"]["restrictions"].get("curfews",[]) if c.get("airport")!=monitor]
    if curfew_record: curfews.append(curfew_record)
    scenario["config"]["restrictions"]["curfews"]=curfews
    scenario["weather_scenario"]=weather
    scenario["monitor_airport"]=monitor
    scenario["origin_airport"]=origin
    scenario["destination_airport"]=destination
    selected_flight_record=flights_by_id.get(flight,scenario["flights"][0])
    scenario["affected_origin_airport"]=selected_flight_record["origin"]
    scenario["affected_destination_airport"]=selected_flight_record["destination"]
    scenario["connecting_airports"]=connects
    scenario["disruption"].update({"type":disruption,"affected_flight":flight if disruption in ("Flight delay","Flight cancelled") else None,"affected_aircraft":aircraft if disruption=="Aircraft unavailable" else None,
        "affected_airport":affected_airport,"minimum_delay_min":min_delay,"release_min":release,"start_min":start_min,"end_min":end_min,
        "capacity_reduction":cap_reduction})
    if disruption=="Security restriction":
        scenario["disruption"].update({"cause":"security","cause_status":"Estimated","confidence":"Estimated"})
    event_flight = flight if disruption in ("Flight delay", "Flight cancelled") else None
    if event_flight and st.session_state.get("_last_event_flight") != event_flight:
        st.session_state.ui_selected_flight_id = event_flight
    st.session_state._last_event_flight = event_flight
    current=_digest(scenario)
    if st.session_state.get("_scenario_digest") not in (None,current):
        st.session_state.recovery_result=None
    st.session_state._scenario_digest=current
    st.session_state.scenario_shared=scenario
    st.sidebar.caption("Prototype decisions require airline staff review. The app does not approve or execute flight changes.")
    return scenario
