from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import streamlit as st

from src.data import AIRPORTS, time_text
from src.optimizer import solve_recovery
from src.page_helpers import csv_bytes
from src.validation import validate_scenario


def render() -> None:
    st.title("Disruption Recovery")
    s=st.session_state.scenario_shared
    st.session_state.scenario_shared=s
    st.caption("One shared scenario · aircraft and passenger decisions are optimized together · all displayed demo operations are synthetic")

    desired_fleet = st.number_input("Fleet count (editable)", min_value=1, max_value=20,
                                    value=len(s["aircraft"]), step=1,
                                    key=f"fleet_count_{s['profile']['profile_id']}",
                                    help="This adds or removes aircraft records used by the recovery model.")
    fleet_changed = int(desired_fleet) != len(s["aircraft"])
    if fleet_changed:
        aircraft = s["aircraft"]
        if desired_fleet > len(aircraft):
            template = deepcopy(aircraft[-1])
            stem = template["aircraft_id"].rstrip("0123456789")
            used_numbers = [int(a["aircraft_id"][len(stem):]) for a in aircraft
                            if a["aircraft_id"].startswith(stem) and a["aircraft_id"][len(stem):].isdigit()]
            next_number = max(used_numbers, default=len(aircraft)) + 1
            while len(aircraft) < desired_fleet:
                added = deepcopy(template)
                added["aircraft_id"] = f"{stem}{next_number}"
                added["flights_originally_assigned"] = []
                aircraft.append(added)
                next_number += 1
        else:
            del aircraft[int(desired_fleet):]
        retained = {a["aircraft_id"] for a in aircraft}
        counts = {a["aircraft_id"]: 0 for a in aircraft}
        for flight in s["flights"]:
            if flight["original_aircraft"] not in retained:
                replacement = min(counts, key=counts.get)
                flight["original_aircraft"] = replacement
            counts[flight["original_aircraft"]] += 1
        for a in aircraft:
            a["flights_originally_assigned"] = [f["flight_id"] for f in s["flights"] if f["original_aircraft"] == a["aircraft_id"]]
        st.session_state.pop(f"aircraft_input_editor_{s['profile']['profile_id']}", None)
        st.session_state.scenario_shared = s
        st.session_state.recovery_result = None

    count_aircraft=len(s["aircraft"])
    count_flights=len(s["flights"])
    count_pax=sum(int(g["count"]) for g in s["passenger_groups"])
    c1,c2,c3,c4=st.columns(4)
    c1.metric("Airline",s["profile"]["display_name"])
    c2.metric("Fleet count",count_aircraft,help="Derived from the aircraft records shown below")
    c3.metric("Scheduled flights",count_flights)
    c4.metric("Passenger groups",f"{len(s['passenger_groups'])} · {count_pax} passengers")
    st.info(f"Scenario: **{s['disruption']['type']}** on {s.get('affected_origin_airport','?')} → {s.get('affected_destination_airport','?')} · passenger focus {s['origin_airport']} → {s['destination_airport']} · watched connections {', '.join(s['connecting_airports']) or 'none'} · {s['weather_scenario']} · monitored airport {s['monitor_airport']} · {s['data_label']}")
    with st.expander("Flight, fleet, and passenger records"):
        tabs=st.tabs(["Fleet & aircraft","Flight schedule","Passenger groups","Passenger protection","Scenario assumptions"])
        changed=fleet_changed
        with tabs[0]:
            aircraft_rows=[{k:a.get(k) for k in ("aircraft_id","type","seats","location","available_min","status")} for a in s["aircraft"]]
            aircraft_columns={
                "aircraft_id":st.column_config.TextColumn("Aircraft ID"),
                "type":st.column_config.TextColumn("Aircraft type"),
                "seats":st.column_config.NumberColumn("Seat capacity",min_value=1,step=1),
                "location":st.column_config.SelectboxColumn("Current airport",options=list(s["airports"])),
                "available_min":st.column_config.NumberColumn("Available from (min)",min_value=0,max_value=1440,step=5,help="Minutes after midnight, local time; for example, 600 = 10:00 IST."),
                "status":st.column_config.TextColumn("Operational status"),
            }
            edited=st.data_editor(aircraft_rows,width="stretch",hide_index=True,num_rows="fixed",disabled=["aircraft_id","type"],key=f"aircraft_input_editor_{s['profile']['profile_id']}",column_config=aircraft_columns)
            edited_rows=edited.to_dict(orient="records") if hasattr(edited,"to_dict") else edited
            by_id={a["aircraft_id"]:a for a in s["aircraft"]}
            for row in edited_rows:
                target=by_id.get(row["aircraft_id"])
                if target:
                    for key in ("seats","location","available_min","status"):
                        if row.get(key)!=target.get(key): target[key]=row.get(key); changed=True
            st.caption("Editable: fleet size above, seats, current airport, availability time, and status. Each fleet-count change adds or removes aircraft records used by the model.")
        with tabs[1]:
            flight_rows=[{k:f.get(k) for k in ("flight_id","origin","destination","scheduled_departure_min","scheduled_arrival_min","duration_min","original_aircraft","status")} for f in s["flights"]]
            flight_columns={
                "flight_id":st.column_config.TextColumn("Flight number"),
                "origin":st.column_config.SelectboxColumn("Origin",options=list(s["airports"])),
                "destination":st.column_config.SelectboxColumn("Destination",options=list(s["airports"])),
                "scheduled_departure_min":st.column_config.NumberColumn("Scheduled departure (min)",min_value=0,max_value=1440,step=5,help="Minutes after midnight, local time; for example, 480 = 08:00 IST."),
                "scheduled_arrival_min":st.column_config.NumberColumn("Scheduled arrival (min)",min_value=0,max_value=1440,step=5,help="Minutes after midnight, local time."),
                "duration_min":st.column_config.NumberColumn("Block duration (min)",min_value=1,max_value=1440,step=5,help="Scheduled flight block time in minutes."),
                "original_aircraft":st.column_config.SelectboxColumn("Assigned aircraft",options=[a["aircraft_id"] for a in s["aircraft"]]),
                "status":st.column_config.SelectboxColumn("Flight status",options=["Scheduled","Operated","Delayed","Cancelled"]),
            }
            edited=st.data_editor(flight_rows,width="stretch",hide_index=True,num_rows="fixed",disabled=["flight_id"],key=f"flights_input_editor_{s['profile']['profile_id']}",column_config=flight_columns)
            edited_rows=edited.to_dict(orient="records") if hasattr(edited,"to_dict") else edited
            by_id={f["flight_id"]:f for f in s["flights"]}
            for row in edited_rows:
                target=by_id.get(row["flight_id"])
                if target:
                    for key in ("origin","destination","scheduled_departure_min","scheduled_arrival_min","duration_min","original_aircraft","status"):
                        if row.get(key)!=target.get(key): target[key]=row.get(key); changed=True
            st.caption("Times are minutes after midnight; schedule edits, aircraft selection, and status feed the shared sample instance.")
        with tabs[2]:
            group_rows=[{"group_id":g["group_id"],"count":g["count"],"origin":g["origin"],"destination":g["destination"],"itinerary":" > ".join(g["itinerary"]),"scheduled_final_arrival_min":g["scheduled_final_arrival_min"],"priority":g["priority"],"splittable":g["splittable"],"refund_allowed":g.get("refund_allowed",True),"fare_plus_fuel_inr":g.get("fare_plus_fuel_inr")} for g in s["passenger_groups"]]
            group_columns={
                "group_id":st.column_config.TextColumn("Group ID"),
                "count":st.column_config.NumberColumn("Passengers",min_value=1,step=1),
                "origin":st.column_config.SelectboxColumn("Journey origin",options=list(s["airports"])),
                "destination":st.column_config.SelectboxColumn("Final destination",options=list(s["airports"])),
                "itinerary":st.column_config.TextColumn("Original flight sequence",help="Ordered flight IDs, including connection legs."),
                "scheduled_final_arrival_min":st.column_config.NumberColumn("Scheduled final arrival (min)",min_value=0,max_value=1440,step=5,help="Local time represented as minutes after midnight; for example, 780 = 13:00 IST."),
                "priority":st.column_config.SelectboxColumn("Service priority",options=["Standard","Assistance requested"]),
                "splittable":st.column_config.CheckboxColumn("Group may be split"),
                "refund_allowed":st.column_config.CheckboxColumn("Refund available"),
                "fare_plus_fuel_inr":st.column_config.NumberColumn("Fare + fuel (INR/pax)",min_value=0,step=100,help="Illustrative fare input used by the demo compensation cap."),
            }
            edited=st.data_editor(group_rows,width="stretch",hide_index=True,num_rows="fixed",disabled=["group_id","itinerary"],key=f"passengers_input_editor_{s['profile']['profile_id']}",column_config=group_columns)
            edited_rows=edited.to_dict(orient="records") if hasattr(edited,"to_dict") else edited
            by_id={g["group_id"]:g for g in s["passenger_groups"]}
            for row in edited_rows:
                target=by_id.get(row["group_id"])
                if target:
                    for key in ("count","origin","destination","scheduled_final_arrival_min","priority","splittable","refund_allowed","fare_plus_fuel_inr"):
                        if row.get(key)!=target.get(key): target[key]=row.get(key); changed=True
            st.caption("The preloaded synthetic passenger groups need no upload. Group size, route endpoints, priority, split policy, and refund availability can be edited here.")
            splittable_groups=[g for g in s["passenger_groups"] if int(g["count"])>1]
            if splittable_groups:
                source_id=st.selectbox("Split passengers from group",[g["group_id"] for g in splittable_groups],key=f"split_source_{s['profile']['profile_id']}")
                source=next(g for g in splittable_groups if g["group_id"]==source_id)
                split_count=st.number_input("Passengers to move to a new group",min_value=1,max_value=int(source["count"])-1,value=max(1,int(source["count"])//2),key=f"split_count_{s['profile']['profile_id']}_{source_id}")
                if st.button("Create separate passenger group",key=f"split_group_{s['profile']['profile_id']}"):
                    new_group=deepcopy(source)
                    suffix=2
                    existing={g["group_id"] for g in s["passenger_groups"]}
                    while f"{source_id}-{suffix:02d}" in existing: suffix+=1
                    new_group["group_id"]=f"{source_id}-{suffix:02d}"
                    new_group["count"]=int(split_count)
                    new_group["seats_needed_per_leg"]=int(split_count)
                    source["count"]-=int(split_count)
                    s["passenger_groups"].append(new_group)
                    st.session_state.scenario_shared=s
                    st.session_state.recovery_result=None
                    st.rerun()
        with tabs[3]:
            st.subheader("Why these passengers are grouped together")
            st.caption("Each row is a group of passengers with the same journey and recovery-relevant attributes. Protection facts entered below apply to every passenger in that row. Split the group when any of those facts differ.")
            basis_rows=[]
            for g in s["passenger_groups"]:
                connections="; ".join(f"{c['airport']} · MCT {c['min_connection_min']} min" for c in g.get("connections",[])) or "Nonstop"
                basis_rows.append({"Group":g["group_id"],"Passengers":int(g["count"]),"Journey":f"{g['origin']} → {g['destination']}","Original itinerary":" → ".join(g["itinerary"]),"Connection / minimum time":connections,"One ticket":"Yes" if g.get("single_ticket") else "No","Priority":g.get("priority","Standard"),"May split": "Yes" if g.get("splittable") else "No","Refund allowed":"Yes" if g.get("refund_allowed",True) else "No"})
            st.dataframe(basis_rows,width="stretch",hide_index=True)
            st.caption("All group IDs are synthetic. The app does not cluster named passengers; staff define groups by shared itinerary and service facts, then split a group when records differ.")
            st.subheader("Protection and service records by group")
            st.write("Enter evidence separately for each group. These case facts and meal/hotel support records are not shared across groups.")
            protection_rows=[]
            for g in s["passenger_groups"]:
                p=g.setdefault("protection",{})
                protection_rows.append({"Group":g["group_id"],"Passengers":int(g["count"]),"Cause verification":p.get("cause_status","Unresolved"),"Cause":p.get("cause","unknown"),"Notice hours":p.get("notice_hours"),"Notice method":p.get("notice_method","Unknown"),"Check-in":p.get("check_in_status","Unknown"),"Alternative offered":p.get("alternative_offered","Unknown"),"Passenger choice":p.get("passenger_choice","Unknown"),"Contact info":p.get("contact_information_available","Unknown"),"Meals provided":bool(p.get("meals_provided",False)),"Hotel provided":bool(p.get("hotel_provided",False))})
            edited=st.data_editor(protection_rows,width="stretch",hide_index=True,num_rows="fixed",key=f"protection_editor_{s['profile']['profile_id']}",disabled=["Group","Passengers"],column_config={"Cause verification":st.column_config.SelectboxColumn(options=["Unresolved","Estimated","Confirmed"]),"Cause":st.column_config.SelectboxColumn(options=["unknown","weather","atc","operational/maintenance","security","other"]),"Notice hours":st.column_config.NumberColumn(min_value=0,max_value=1000,step=0.25),"Notice method":st.column_config.SelectboxColumn(options=["Unknown","SMS","Email","Phone call","Airport announcement","Other"]),"Check-in":st.column_config.SelectboxColumn(options=["Unknown","Checked in","Not checked in"]),"Alternative offered":st.column_config.SelectboxColumn(options=["Unknown","Yes","No"]),"Passenger choice":st.column_config.SelectboxColumn(options=["Unknown","Accepted alternative","Chose refund","No choice recorded"]),"Contact info":st.column_config.SelectboxColumn(options=["Unknown","Yes","No"]),"Meals provided":st.column_config.CheckboxColumn(),"Hotel provided":st.column_config.CheckboxColumn()})
            edited_rows=edited.to_dict(orient="records") if hasattr(edited,"to_dict") else edited
            by_group={g["group_id"]:g for g in s["passenger_groups"]}
            mapping={"Cause verification":"cause_status","Cause":"cause","Notice hours":"notice_hours","Notice method":"notice_method","Check-in":"check_in_status","Alternative offered":"alternative_offered","Passenger choice":"passenger_choice","Contact info":"contact_information_available","Meals provided":"meals_provided","Hotel provided":"hotel_provided"}
            for row in edited_rows:
                group=by_group[row["Group"]]
                for label,key in mapping.items():
                    value=row.get(label)
                    if key=="notice_hours" and value in (None,""): value=None
                    if group["protection"].get(key)!=value:
                        group["protection"][key]=value
                        changed=True
            st.caption(f"Passenger groups shown: {len(s['passenger_groups'])} · passengers represented: {sum(int(g['count']) for g in s['passenger_groups'])}. Facts and support costs apply to each group's passenger count.")
        with tabs[4]:
            st.write("Weather is a configurable seasonal preset. No live weather or airline data source is connected.")
            st.write("Seat counts, schedules, fleet positions, capacities, costs, and operating rules are illustrative synthetic assumptions.")
            st.write(f"Delay choices (min): {s['profile']['operations']['delay_choices_min']} · minimum turnaround: {s['profile']['operations']['minimum_turnaround_min']} min · time bucket: {s['config']['time_bucket_min']} min")
            st.write(f"Protection-rule sources: {s['config']['dgca_rules']['source_cancel']} · {s['config']['dgca_rules']['source_refund']}")

    if changed:
        st.session_state.scenario_shared=s
        st.session_state.recovery_result=None
    errors=validate_scenario(s)
    if errors:
        st.error("Scenario validation found issues:")
        for error in errors: st.write(f"• {error}")
        return
    if st.button("Run recovery optimization",type="primary",width="content"):
        with st.spinner("Solving aircraft routes and passenger recovery choices together…"):
            st.session_state.recovery_result=solve_recovery(s,time_limit_seconds=20,alternatives=True)
    result=st.session_state.get("recovery_result")
    if not result:
        st.info("No recovery plan has been generated for the current scenario.")
    elif result.get("status") not in {"OPTIMAL","FEASIBLE"}:
        st.error(f"Solver status: {result.get('status')} · {result.get('message','No feasible plan was found.')}")
        st.caption("Check disruption timing, aircraft start locations, airport restrictions, and passenger itinerary data. The application has not approved or executed a plan.")
    else:
        recovery_cost=int(result["total_cost_inr"])
        c1,c2,c3,c4=st.columns(4)
        c1.metric("Solver status",result["status"])
        c2.metric("Recovery cost",f"₹{recovery_cost:,.0f}",help="Includes disruption-related aircraft delay, cancellation, and passenger recovery costs. Routine flight operating costs are excluded.")
        c3.metric("Solver time",f"{result.get('runtime_seconds_total',result['runtime_seconds']):.2f} sec")
        c4.metric("Finite instance", "Optimal" if result.get("optimal") else "Feasible, not proven")
        if result.get("optimal"):
            st.success("The solver proved optimality for the finite candidate instance and the configured assumptions. This is not a claim of a real airline global optimum.")
        else: st.warning("A feasible plan was found, but the solver did not prove it optimal within the configured time limit.")

        st.subheader("Aircraft recovery plan")
        frows=[]
        for f in result["flight_plan"]:
            scheduled_flight=next((item for item in s["flights"] if item["flight_id"]==f["flight_id"]),{})
            scheduled_arrival=time_text(scheduled_flight.get("scheduled_arrival_min"))
            if f["status"]=="Cancelled":
                reason=result.get("flight_reasons",{}).get(f["flight_id"],"No selected operating option; cancellation remains subject to staff review.")
                departure=arrival="—"
            else:
                d=s["disruption"]
                if d.get("type")=="Flight delay" and f["flight_id"]==d.get("affected_flight"):
                    reason=f"Observed disruption requires at least {d.get('minimum_delay_min')} minutes delay."
                elif f.get("delay_min",0): reason=result.get("flight_reasons",{}).get(f["flight_id"],"Selected delay from the finite candidate set; inspect the model assumptions and constraints.")
                else: reason="Feasible at selected time with assigned aircraft and airport constraints."
                departure=f"{f['departure_min']//60:02d}:{f['departure_min']%60:02d}"
                arrival=f"{f['arrival_min']//60:02d}:{f['arrival_min']%60:02d}"
            frows.append({"Flight":f["flight_id"],"Route":f"{f.get('origin','')} → {f.get('destination','')}","Outcome":f["status"],"Aircraft":f.get("aircraft_id") or "—","Departure delay (min)":str(f.get("delay_min")) if f.get("delay_min") is not None else "—","Predicted departure":departure,"Scheduled arrival":scheduled_arrival,"Predicted arrival":arrival,"Seats":str(f.get("seats","—")),"Reason":reason})
        st.dataframe(frows,width="stretch",hide_index=True,alt="Aircraft recovery plan with scheduled arrival and predicted arrival times")
        st.caption("Scheduled arrival is the original synthetic timetable time; predicted arrival includes the selected recovery delay and airport weather assumption. No live actual-arrival feed is connected.")
        st.subheader("Passenger recovery plan")
        passenger_display=[{**row,"Delay vs original (min)":str(row.get("Delay vs original (min)","—"))} for row in result["passenger_plan"]]
        st.dataframe(passenger_display,width="stretch",hide_index=True)
        if result.get("selected_connections"):
            st.markdown("**Connections on selected passenger itineraries**")
            st.dataframe(result["selected_connections"],width="stretch",hide_index=True)
        if result.get("connection_risks"):
            st.markdown("**Pre-recovery missed-connection risks**")
            st.dataframe(result["connection_risks"],width="stretch",hide_index=True)
        else: st.success("No missed connection is predicted in the pre-recovery scenario for the listed passenger groups.")
        st.subheader("Recovery cost summary (incremental INR)")
        cost_basis={
            "Aircraft delay":"Selected departure-delay minutes × the configured rate.",
            "Flight cancellation":"Configured cost for each cancelled flight.",
            "Passenger delay impact":"Passenger count × final-arrival delay hours × configured rate.",
            "Rebooking":"Passengers assigned a different itinerary × configured rate.",
            "Refunds":"Passengers assigned refund / recovery × configured rate.",
            "DGCA cancellation compensation":"Configured rule result; uncertain cases require human review.",
            "Meals":"Per affected passenger when support is recorded or the configured delay condition applies.",
            "Hotels":"Per affected passenger when support is recorded or the configured delay condition applies.",
        }
        costrows=[{"Component":k,"Cost (INR)":v,"Basis":cost_basis.get(k,"Illustrative editable profile assumption.")} for k,v in result["cost_breakdown"].items()]
        st.dataframe(costrows,width="stretch",hide_index=True)
        st.caption("Routine operating costs for flights are excluded. This summary counts only disruption and passenger recovery costs.")
        st.write(f"**Total recovery cost: ₹{recovery_cost:,.0f}**")
        st.caption("Passenger final-arrival delay is charged once per passenger. Legal compensation and facility costs are separate from subjective delay impact.")
        st.subheader("Passenger case review checklist")
        st.caption("Complete a separate checklist for each passenger group. Checks and reviewer notes stay in this local session and are included in the results export. Do not enter passenger names.")
        review_items = [
            ("cause", "Disruption cause and supporting evidence reviewed"),
            ("times", "Scheduled/revised times and flight block duration verified"),
            ("notice", "Passenger notice timing/method and check-in status verified"),
            ("choice", "Alternative offered and passenger choice recorded"),
            ("facilities", "Waiting time, meals, and hotel support checked"),
            ("records", "Ticket/contact details checked where applicable"),
        ]
        checklist_rows = []
        dgca_by_group = {row["Group"]: row for row in result.get("dgca_results", [])}
        review_prefix = f"dgca_review_{s['profile']['profile_id']}_{s['disruption'].get('disruption_id', 'scenario')}"
        for group_index, group in enumerate(s["passenger_groups"]):
            gid = group["group_id"]
            legal = dgca_by_group.get(gid, {})
            with st.expander(f"{gid} · {group['count']} passengers · {legal.get('Compensation status', 'Review required')}", expanded=(group_index == 0)):
                checked = {}
                left, right = st.columns(2)
                for item_index, (field, label) in enumerate(review_items):
                    column = left if item_index < 3 else right
                    with column:
                        checked[field] = st.checkbox(label, key=f"{review_prefix}_{gid}_{field}")
                outcome = st.selectbox("Reviewer outcome", ["Pending human review", "Potential eligibility · verify rule", "No compensation indicated · facts verified", "Missing facts · follow up", "Escalate for specialist review"], key=f"{review_prefix}_{gid}_outcome")
                notes = st.text_area("Reviewer notes (no personal data)", max_chars=500, key=f"{review_prefix}_{gid}_notes")
                done = all(checked.values())
                st.success("All checklist items confirmed; reviewer disposition is still required." if done and outcome == "Pending human review" else ("Checklist complete." if done else f"{sum(checked.values())} of {len(review_items)} checks complete."))
            checklist_rows.append({"Group": gid, "Passengers": int(group["count"]),
                                   **{label: checked[field] for field, label in review_items},
                                   "Reviewer outcome": outcome, "Reviewer notes": notes})
        st.download_button("Export DGCA human review checklist CSV", data=csv_bytes(checklist_rows),
                           file_name="dgca_human_review_checklist.csv", mime="text/csv")
        if result.get("capacity_constraints"):
            st.subheader("Airport constraints in the selected plan")
            st.dataframe(result["capacity_constraints"],width="stretch",hide_index=True)
        else: st.caption("No selected airport movement bucket was saturated in this plan.")
        st.subheader("Decision rationale")
        for reason in result.get("why_this_plan",[]): st.write(f"• {reason}")
        st.write(f"Solver examined {result.get('options_count',0)} flight-aircraft-delay options and {result.get('passenger_path_options',0)} passenger itinerary variants.")
        st.subheader("Plan comparison")
        before_cancel=0; before_delay=0
        after_cancel=sum(x["status"]=="Cancelled" for x in result["flight_plan"])
        after_delay=sum(x["status"]=="Delayed" for x in result["flight_plan"])
        compare=[{"Measure":"Cancelled flights","Before":"0","Recommended":str(after_cancel)},
                 {"Measure":"Delayed flights","Before":"0","Recommended":str(after_delay)},
                 {"Measure":"Passenger groups with connection risk","Before":str(len(result.get("connection_risks",[]))),"Recommended":"See outcome table"},
                 {"Measure":"Modelled cost (INR)","Before":"Not separately optimized","Recommended":f"{result['total_cost_inr']:,.0f}"}]
        st.dataframe(compare,width="stretch",hide_index=True)
        if result.get("alternatives"):
            st.subheader("Alternative recovery plan")
            for index,alt in enumerate(result["alternatives"],start=1):
                with st.expander(f"Alternative {index}: ₹{alt['total_cost_inr']:,.0f} · {alt['solver_status']} · {'optimal for alternative search' if alt['optimal'] else 'feasible'}"):
                    st.dataframe([{ "Flight":x["flight_id"],"Outcome":x["status"],"Aircraft":x.get("aircraft_id") or "—","Delay (min)":str(x.get("delay_min")) if x.get("delay_min") is not None else "—"} for x in alt["flight_plan"]],width="stretch",hide_index=True)
                    alt_passenger_display=[{**row,"Delay vs original (min)":str(row.get("Delay vs original (min)","—"))} for row in alt["passenger_plan"]]
                    st.dataframe(alt_passenger_display,width="stretch",hide_index=True)
        else: st.caption("No distinct feasible alternative was found within the configured second solve.")
        with st.expander("Operational review and sign-off"):
            st.selectbox("Review status",["Pending human review","Approved for manual planning","Return for revision"],key="staff_review_status")
            st.caption("This records a local classroom review label only. It does not transmit, approve, or execute flight changes.")
        result_export={k:v for k,v in result.items() if k!="decision_signature"}
        result_export["human_review_checklist"] = checklist_rows
        st.download_button("Export scenario and recovery results JSON",data=json.dumps({"scenario":s,"result":result_export},indent=2,ensure_ascii=False).encode("utf-8"),file_name="recovery_results.json",mime="application/json")
        st.download_button("Export recommended flight plan CSV",data=csv_bytes(frows),file_name="recommended_flights.csv",mime="text/csv")
        st.download_button("Export passenger recovery CSV",data=csv_bytes(result["passenger_plan"]),file_name="passenger_recovery.csv",mime="text/csv")


render()
