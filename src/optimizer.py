"""Finite integrated aircraft/passenger recovery MILP formulated with CP-SAT."""
from __future__ import annotations

from collections import defaultdict
from itertools import product
from time import perf_counter
from typing import Any

from ortools.sat.python import cp_model

from .capacity import effective_capacity, predicted_times, restriction_reason
from .dgca import compensation_per_passenger
from .data import time_text


def _candidate_options(s: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[int, list[int]]]]:
    d = s["disruption"]
    delays = list(map(int, s["profile"]["operations"].get("delay_choices_min", [0,15,30,45,60,75,90])))
    flights = s["flights"]
    options: list[dict[str, Any]] = []
    eligible_delays: dict[str, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
    for f in flights:
        for delay in delays:
            if d.get("type") == "Flight delay" and f["flight_id"] == d.get("affected_flight") and delay < int(d.get("minimum_delay_min", 0)):
                continue
            dep, arr = predicted_times(f, delay, s)
            if restriction_reason(s, f, dep, arr):
                continue
            if str(f.get("status","Scheduled")).lower() == "cancelled":
                continue
            if d.get("type") == "Flight cancelled" and f["flight_id"] == d.get("affected_flight"):
                continue
            for a in s["aircraft"]:
                if str(a.get("status","Available")).lower() not in {"available","operational","ready"}:
                    continue
                eligible_airports=set(a.get("airport_compatibility",s["airports"].keys()))
                if f["origin"] not in eligible_airports or f["destination"] not in eligible_airports:
                    continue
                if d.get("type") == "Aircraft unavailable" and a["aircraft_id"] == d.get("affected_aircraft") and dep < int(d.get("release_min", 0)):
                    continue
                opts = {"flight": f, "aircraft": a, "delay": delay, "departure": dep, "arrival": arr}
                options.append(opts)
                eligible_delays[f["flight_id"]][delay].append(len(options)-1)
    return options, eligible_delays


def _route_ids(group: dict[str, Any], flights: list[dict[str, Any]]) -> list[tuple[str, ...]]:
    by_origin: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in flights: by_origin[f["origin"]].append(f)
    for fs in by_origin.values(): fs.sort(key=lambda f: f["scheduled_departure_min"])
    first_original = min(next(f["scheduled_departure_min"] for f in flights if f["flight_id"] == fid) for fid in group["itinerary"])
    results: set[tuple[str, ...]] = set()
    def walk(airport: str, route: tuple[str, ...], latest: int, depth: int) -> None:
        if airport == group["destination"] and route:
            results.add(route)
            return
        if depth >= 3: return
        for f in by_origin.get(airport, []):
            if f["flight_id"] in route or f["scheduled_departure_min"] < first_original or f["scheduled_departure_min"] < latest:
                continue
            walk(f["destination"], route + (f["flight_id"],), f["scheduled_departure_min"], depth+1)
    walk(group["origin"], tuple(), first_original, 0)
    original = tuple(group["itinerary"])
    if original not in results: results.add(original)
    return sorted(results, key=lambda r: (len(r), r))


def _path_variants(group: dict[str, Any], s: dict[str, Any], eligible: dict[str, dict[int, list[int]]]) -> list[dict[str, Any]]:
    f_by_id = {f["flight_id"]: f for f in s["flights"]}
    weather = s["config"]["weather_scenarios"]
    del weather
    variants: list[dict[str, Any]] = []
    mct_default = int(s["config"].get("minimum_connection_min", 40))
    for route in _route_ids(group, s["flights"]):
        delay_sets = [list(eligible.get(fid, {})) for fid in route]
        if any(not ds for ds in delay_sets): continue
        for ds in product(*delay_sets):
            timings = [predicted_times(f_by_id[fid], delay, s) for fid, delay in zip(route, ds)]
            feasible = True
            for i in range(len(route)-1):
                airport = f_by_id[route[i]]["destination"]
                mct = next((int(x["min_connection_min"]) for x in group.get("connections", []) if x["airport"] == airport), mct_default)
                if timings[i][1] + mct > timings[i+1][0]:
                    feasible = False; break
            if feasible:
                variants.append({"route":route,"delays":tuple(ds),"arrival":timings[-1][1],"departure":timings[0][0],
                                 "is_original":tuple(route)==tuple(group["itinerary"])})
    return variants


def _group_is_disrupted(g: dict[str, Any], scenario: dict[str, Any]) -> bool:
    """Return whether the group's original itinerary intersects the selected disruption."""
    disruption = scenario.get("disruption", {})
    flights = {f["flight_id"]: f for f in scenario["flights"]}
    kind = disruption.get("type")
    if kind in {"Flight delay", "Flight cancelled"}:
        affected = {disruption.get("affected_flight")}
    elif kind == "Aircraft unavailable":
        affected = {fid for fid in g.get("itinerary", [])
                    if flights.get(fid, {}).get("original_aircraft") == disruption.get("affected_aircraft")}
    elif kind in {"Airport capacity reduction", "Airport closure", "Security restriction"}:
        airport = disruption.get("affected_airport")
        affected = {fid for fid in g.get("itinerary", [])
                    if airport in (flights.get(fid, {}).get("origin"), flights.get(fid, {}).get("destination"))}
    else:
        affected = set()
    return bool(set(g.get("itinerary", [])) & affected)


def _path_cost(g: dict[str, Any], path: dict[str, Any], costs: dict[str, Any], scenario: dict[str, Any]) -> dict[str, int]:
    arrival_delay = max(0, int(path["arrival"]) - int(g["scheduled_final_arrival_min"]))
    multiplier = 1.5 if g.get("priority") == "Assistance requested" else 1.0
    delay_cost = int(round(arrival_delay * int(costs["passenger_delay_per_hour_inr"]) * multiplier / 60))
    rebook_cost = 0 if path["is_original"] else int(costs["rebooking_per_passenger_inr"])
    first_delay = max((int(delay) for delay in path["delays"]), default=0)
    first_flight=next(f for f in scenario["flights"] if f["flight_id"]==path["route"][0])
    block_hours=float(first_flight["duration_min"])/60
    thresholds=scenario["config"]["dgca_rules"]["delay_facility_threshold_hours_by_block_time"]
    meal_hours=next((float(x["delay_hours"]) for x in thresholds if block_hours<=float(x["max_block_hours"])),4.0)
    disrupted_group = _group_is_disrupted(g, scenario)
    facts={**scenario["disruption"],**g.get("protection",{})}
    meals_provided = bool(facts.get("meals_provided", False))
    hotel_provided = bool(facts.get("hotel_provided", False))
    meal_cost = int(costs["meal_per_passenger_inr"]) if disrupted_group and (meals_provided or first_delay >= meal_hours*60) else 0
    departure_hour=int(first_flight["scheduled_departure_min"])//60
    hotel_limit=360 if departure_hour>=20 or departure_hour<3 else 1440
    hotel_cost = int(costs["hotel_per_passenger_inr"]) if disrupted_group and (hotel_provided or first_delay > hotel_limit) else 0
    return {"passenger_delay":delay_cost,"rebooking":rebook_cost,"refund":0,"compensation":0,"meals":meal_cost,"hotel":hotel_cost}


def _capacity_buckets(s: dict[str, Any], options: list[dict[str, Any]]) -> dict[tuple[str,int,str], list[int]]:
    bucket_size = int(s["config"].get("time_bucket_min", 15))
    buckets: dict[tuple[str,int,str], list[int]] = defaultdict(list)
    for oi, o in enumerate(options):
        f = o["flight"]
        depb = o["departure"] // bucket_size * bucket_size
        arrb = o["arrival"] // bucket_size * bucket_size
        buckets[(f["origin"], depb, "departure")].append(oi)
        buckets[(f["destination"], arrb, "arrival")].append(oi)
        buckets[(f["origin"], depb, "combined")].append(oi)
        buckets[(f["destination"], arrb, "combined")].append(oi)
    return buckets


def _build_and_solve(s: dict[str, Any], time_limit: int = 20, exclude: set[int] | None = None) -> dict[str, Any]:
    start = perf_counter()
    model = cp_model.CpModel()
    options, eligible = _candidate_options(s)
    flights = s["flights"]
    groups = s["passenger_groups"]
    costs = s["profile"]["costs"]
    if not options:
        return {"status":"INFEASIBLE","message":"No flight/aircraft operating options remain after applying the selected disruption and restrictions.","runtime_seconds":perf_counter()-start,"optimal":False}
    x = [model.NewBoolVar(f"op_{o['flight']['flight_id']}_{o['aircraft']['aircraft_id']}_{o['delay']}") for o in options]
    cancel: dict[str, Any] = {}
    option_by_flight: dict[str, list[int]] = defaultdict(list)
    option_by_aircraft: dict[str, list[int]] = defaultdict(list)
    option_by_flight_delay: dict[tuple[str,int], list[int]] = defaultdict(list)
    for oi,o in enumerate(options):
        fid=o["flight"]["flight_id"]; aid=o["aircraft"]["aircraft_id"]
        option_by_flight[fid].append(oi); option_by_aircraft[aid].append(oi); option_by_flight_delay[(fid,o["delay"])].append(oi)
    for f in flights:
        fid=f["flight_id"]
        cancel[fid] = model.NewBoolVar(f"cancel_{fid}")
        model.Add(sum(x[i] for i in option_by_flight[fid]) + cancel[fid] == 1)

    by_aircraft = {a["aircraft_id"]:a for a in s["aircraft"]}
    # A selected aircraft sequence must start at its configured position and remain connected.
    for aid, indices in option_by_aircraft.items():
        a=by_aircraft[aid]
        for oi in indices:
            o=options[oi]; can_start=(o["flight"]["origin"]==a["location"] and o["departure"]>=int(a["available_min"]))
            predecessors=[]
            for pi in indices:
                if pi == oi: continue
                p=options[pi]
                if p["flight"]["destination"] == o["flight"]["origin"] and p["arrival"] + int(s["profile"]["operations"]["minimum_turnaround_min"]) <= o["departure"]:
                    predecessors.append(pi)
            if not can_start:
                if predecessors: model.Add(x[oi] <= sum(x[pi] for pi in predecessors))
                else: model.Add(x[oi] == 0)
            for pj in indices:
                if pj <= oi: continue
                p=options[pj]
                if p["flight"]["flight_id"] == o["flight"]["flight_id"]: continue
                p_before=(p["arrival"] + int(s["profile"]["operations"]["minimum_turnaround_min"]) <= o["departure"] and p["flight"]["destination"] == o["flight"]["origin"])
                o_before=(o["arrival"] + int(s["profile"]["operations"]["minimum_turnaround_min"]) <= p["departure"] and o["flight"]["destination"] == p["flight"]["origin"])
                if not (p_before or o_before): model.Add(x[oi]+x[pj] <= 1)

    # Airport movement capacity is checked per explicit interval and movement type.
    bucket_vars=_capacity_buckets(s, options)
    d=s["disruption"]
    for (airport,minute,kind), indices in bucket_vars.items():
        cap_key = "arrival" if kind == "arrival" else "departure" if kind == "departure" else "combined"
        if kind == "combined":
            base=s["config"]["airport_capacity_per_bucket"].get(airport,{}).get("combined")
            cap=None if base is None else int(base)
        else: cap=effective_capacity(s,airport,cap_key)
        if cap is not None:
            if d.get("type") == "Airport capacity reduction" and airport == d.get("affected_airport") and minute >= int(d.get("start_min",0)):
                cap=max(0, cap-int(d.get("capacity_reduction",1)))
            model.Add(sum(x[i] for i in set(indices)) <= cap)

    # Passenger itinerary alternatives are linked directly to selected aircraft/timing options.
    group_vars: dict[str, dict[str, Any]] = {}
    flight_timing_demand: dict[tuple[str,int], list[Any]] = defaultdict(list)
    passenger_cost_terms: dict[str, list[tuple[Any,dict[str,int]]]] = defaultdict(list)
    path_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for g in groups:
        gid=g["group_id"]; count=int(g["count"]); splittable=bool(g.get("splittable",True))
        vars_for_group=[]
        variants=_path_variants(g,s,eligible)
        for vi,path in enumerate(variants):
            if splittable:
                y=model.NewIntVar(0,count,f"pax_{gid}_{vi}")
            else:
                z=model.NewBoolVar(f"choose_{gid}_{vi}")
                y=model.NewIntVar(0,count,f"pax_{gid}_{vi}")
                model.Add(y==count*z)
            for fid,delay in zip(path["route"],path["delays"]):
                model.Add(y <= count*sum(x[oi] for oi in option_by_flight_delay.get((fid,delay),[])))
                flight_timing_demand[(fid,delay)].append(y)
            path["var"]=y; path["passengers"]=count
            path["costs"]=_path_cost(g,path,costs,s)
            path["passenger_delay_min"]=max(0,int(path["arrival"])-int(g["scheduled_final_arrival_min"]))
            vars_for_group.append(y); passenger_cost_terms[gid].append((y,path["costs"]))
            path_rows[gid].append(path)
        if g.get("refund_allowed",True):
            refund=model.NewIntVar(0,count,f"refund_{gid}") if splittable else model.NewBoolVar(f"refund_{gid}")
        else:
            refund=model.NewIntVar(0,0,f"refund_{gid}") if splittable else model.NewBoolVar(f"refund_{gid}")
            if not splittable: model.Add(refund==0)
        if not splittable:
            refund_pax=model.NewIntVar(0,count,f"refund_pax_{gid}"); model.Add(refund_pax==count*refund); refund=refund_pax
        vars_for_group.append(refund)
        model.Add(sum(vars_for_group)==count)
        group_vars[gid]={"refund":refund,"paths":path_rows[gid],"variables":vars_for_group}
        impacted=_group_is_disrupted(g,s)
        facts={**d,**g.get("protection",{})}
        refund_path={"passenger_delay":0,"rebooking":0,"refund":int(costs["refund_per_passenger_inr"]),"compensation":0,
                     "meals":int(costs["meal_per_passenger_inr"]) if impacted and facts.get("meals_provided",False) else 0,
                     "hotel":int(costs["hotel_per_passenger_inr"]) if impacted and facts.get("hotel_provided",False) else 0}
        passenger_cost_terms[gid].append((refund,refund_path))

    for (fid,delay), demand_vars in flight_timing_demand.items():
        capacity_expr=sum(int(o["aircraft"]["seats"])*x[oi] for oi,o in enumerate(options) if o["flight"]["flight_id"]==fid and o["delay"]==delay)
        model.Add(sum(demand_vars) <= capacity_expr)

    # Explicit group-level OR: cancelled original travel triggers the configured compensation term only when assessable.
    group_cancel: dict[str, Any] = {}
    compensation_rates: dict[str,int|None] = {}
    for g in groups:
        gid=g["group_id"]; cvar=model.NewBoolVar(f"original_cancel_{gid}")
        cancelled=[cancel[fid] for fid in g["itinerary"]]
        for cf in cancelled: model.Add(cvar>=cf)
        model.Add(cvar<=sum(cancelled))
        group_cancel[gid]=cvar
        rate, _status, _source=compensation_per_passenger(g,s)
        compensation_rates[gid]=rate

    # Linear objective in INR includes only disruption and passenger recovery costs.
    objective=[]
    for oi,o in enumerate(options):
        objective.append(int(costs["delay_per_flight_min_inr"])*int(o["delay"])*x[oi])
    for f in flights: objective.append(int(costs["cancellation_inr"])*cancel[f["flight_id"]])
    for gid,terms in passenger_cost_terms.items():
        for var,parts in terms:
            per_pax=sum(parts.values())
            if per_pax: objective.append(per_pax*var)
    for g in groups:
        rate=compensation_rates[g["group_id"]]
        if rate is not None and rate > 0:
            objective.append(int(rate)*int(g["count"])*group_cancel[g["group_id"]])
    model.Minimize(sum(objective))

    decisions=list(cancel.values())
    decisions.extend(x)
    if exclude:
        chosen=[idx for idx in exclude if idx < len(decisions)]
        if chosen: model.Add(sum(decisions[i] for i in chosen) <= len(flights)-1)

    solver=cp_model.CpSolver(); solver.parameters.max_time_in_seconds=max(1,time_limit); solver.parameters.num_search_workers=8
    status=solver.Solve(model); runtime=perf_counter()-start
    status_name=solver.StatusName(status)
    if status not in (cp_model.OPTIMAL,cp_model.FEASIBLE):
        msg="The selected restrictions leave no feasible integrated plan." if status==cp_model.INFEASIBLE else "The solver did not find a feasible plan within its limit."
        return {"status":status_name,"message":msg,"runtime_seconds":runtime,"optimal":False,"options_count":len(options)}

    selected=[]; selected_decision_indices=[]
    for fid in [f["flight_id"] for f in flights]:
        if solver.Value(cancel[fid]):
            selected_decision_indices.append(list(cancel).index(fid))
            flight=next(f for f in flights if f["flight_id"]==fid)
            selected.append({"flight_id":fid,"status":"Cancelled","aircraft_id":None,"delay_min":None,"origin":flight["origin"],"destination":flight["destination"]})
        else:
            oi=next(i for i in option_by_flight[fid] if solver.Value(x[i]))
            selected_decision_indices.append(len(flights)+oi)
            o=options[oi]
            selected.append({"flight_id":fid,"status":"Operated" if o["delay"]==0 else "Delayed","aircraft_id":o["aircraft"]["aircraft_id"],"delay_min":o["delay"],
                             "departure_min":o["departure"],"arrival_min":o["arrival"],"origin":o["flight"]["origin"],"destination":o["flight"]["destination"],"seats":o["aircraft"]["seats"]})
    selected_by_id={row["flight_id"]:row for row in selected}
    passenger_rows=[]; selected_costs=defaultdict(int); group_status={}
    flight_lookup={f["flight_id"]:f for f in flights}
    for g in groups:
        gid=g["group_id"]; refund=int(solver.Value(group_vars[gid]["refund"]))
        canceled_original=any(selected_by_id[fid]["status"]=="Cancelled" for fid in g["itinerary"])
        assigned=0
        for p in path_rows[gid]:
            n=int(solver.Value(p["var"]))
            if n<=0: continue
            assigned+=n
            classification="Kept original itinerary" if p["is_original"] else ("Rebooked" if n==int(g["count"]) else "Split / rebooked")
            connection_airports=[flight_lookup[left]["destination"] for left in p["route"][:-1]]
            watched=[code for code in connection_airports if code in s.get("connecting_airports",[])]
            focus=(g["origin"]==s.get("origin_airport") and g["destination"]==s.get("destination_airport"))
            passenger_rows.append({"Group":gid,"Passengers":n,"Outcome":classification,"Flights":" → ".join(p["route"]),
                                   "Departure":time_text(p["departure"]),"Predicted final arrival":time_text(p["arrival"]),
                                   "Delay vs original (min)":p["passenger_delay_min"],"Connection risk":"No: MCT satisfied in selected plan",
                                   "Journey focus":"Selected passenger route" if focus else "Other network flow",
                                   "Watched connections":", ".join(watched) if watched else "—",
                                   "Priority":g.get("priority","Standard"),"Fare ticketed as one ticket":g.get("single_ticket",False)})
            for component,amount in p["costs"].items(): selected_costs[component]+=amount*n
            group_status.setdefault(gid,{"arrival":p["arrival"],"refund":False,"cancelled_original":canceled_original})
        if refund:
            focus=(g["origin"]==s.get("origin_airport") and g["destination"]==s.get("destination_airport"))
            passenger_rows.append({"Group":gid,"Passengers":refund,"Outcome":"Refund / recovery review","Flights":"—","Departure":"—","Predicted final arrival":"—","Delay vs original (min)":"—","Connection risk":"No feasible itinerary assigned","Journey focus":"Selected passenger route" if focus else "Other network flow","Watched connections":"—","Priority":g.get("priority","Standard"),"Fare ticketed as one ticket":g.get("single_ticket",False)})
            selected_costs["refund"]+=refund*int(costs["refund_per_passenger_inr"])
            group_status[gid]={"arrival":None,"refund":True,"cancelled_original":canceled_original}
        if assigned+refund!=int(g["count"]):
            return {"status":"MODEL_ERROR","message":f"Passenger conservation check failed for {gid}.","runtime_seconds":runtime,"optimal":False}

    # Recompute cost components from the chosen plan, in the same units shown to the user.
    flight_components={"Aircraft delay":0,"Flight cancellation":0}
    for row in selected:
        if row["status"]=="Cancelled": flight_components["Flight cancellation"]+=int(costs["cancellation_inr"])
        else:
            flight_components["Aircraft delay"]+=int(costs["delay_per_flight_min_inr"])*int(row["delay_min"])
    component_totals=dict(flight_components)
    component_totals.update({"Passenger delay impact":selected_costs["passenger_delay"],"Rebooking":selected_costs["rebooking"],"Refunds":selected_costs["refund"],"DGCA cancellation compensation":0,"Meals":selected_costs["meals"],"Hotels":selected_costs["hotel"]})
    dgca_rows=[]
    for g in groups:
        facts={**d,**g.get("protection",{})}
        state=group_status.get(g["group_id"],{"arrival":None,"refund":True,"cancelled_original":any(selected_by_id[fid]["status"]=="Cancelled" for fid in g["itinerary"])})
        if state["cancelled_original"]:
            rate, status_text, source=compensation_per_passenger(g,s)
            if rate is not None and rate>0:
                component_totals["DGCA cancellation compensation"] += rate*int(g["count"])
            dgca_rows.append({"Group":g["group_id"],"Passengers":g["count"],"Cancelled original leg":True,"Compensation status":status_text,"Potential INR / passenger":rate if rate is not None else "Review","Meals reported provided":facts.get("meals_provided",False),"Hotel reported provided":facts.get("hotel_provided",False),"Source/version":source,"Notice hours":facts.get("notice_hours","Missing"),"Notification method":facts.get("notice_method","Unknown"),"Check-in":facts.get("check_in_status","Unknown"),"Alternative offered":facts.get("alternative_offered","Unknown"),"Passenger choice":facts.get("passenger_choice","Unknown"),"Contact info":facts.get("contact_information_available","Unknown"),"Cause":facts.get("cause","Unknown")})
        else:
            dgca_rows.append({"Group":g["group_id"],"Passengers":g["count"],"Cancelled original leg":False,"Compensation status":"Not triggered by a cancelled original leg","Potential INR / passenger":"—","Meals reported provided":facts.get("meals_provided",False),"Hotel reported provided":facts.get("hotel_provided",False),"Source/version":s["config"]["dgca_rules"]["source_cancel"],"Notice hours":facts.get("notice_hours","Missing"),"Notification method":facts.get("notice_method","Unknown"),"Check-in":facts.get("check_in_status","Unknown"),"Alternative offered":facts.get("alternative_offered","Unknown"),"Passenger choice":facts.get("passenger_choice","Unknown"),"Contact info":facts.get("contact_information_available","Unknown"),"Cause":facts.get("cause","Unknown")})

    cap_rows=[]
    for (airport,minute,kind), indices in bucket_vars.items():
        used=sum(solver.Value(x[i]) for i in set(indices))
        if used==0: continue
        key="arrival" if kind=="arrival" else "departure" if kind=="departure" else "combined"
        base=s["config"]["airport_capacity_per_bucket"].get(airport,{}).get(key)
        cap=(effective_capacity(s,airport,key) if key!="combined" else (None if base is None else int(base)))
        if cap is not None and d.get("type")=="Airport capacity reduction" and airport==d.get("affected_airport") and minute>=int(d.get("start_min",0)): cap=max(0,cap-int(d.get("capacity_reduction",1)))
        if cap is not None and used>=cap:
            cap_rows.append({"Airport":airport,"Time":time_text(minute),"Movement":kind.title(),"Demand":used,"Capacity":cap,"Status":"At capacity" if used==cap else "Over capacity"})

    original_connection_risks=[]
    for g in groups:
        if len(g["itinerary"])<2: continue
        for left,right in zip(g["itinerary"],g["itinerary"][1:]):
            lf=next(f for f in flights if f["flight_id"]==left); rf=next(f for f in flights if f["flight_id"]==right)
            disruption_delay=int(d.get("minimum_delay_min",0)) if d.get("type")=="Flight delay" and d.get("affected_flight")==left else 0
            _,la=predicted_times(lf,disruption_delay,s); rd, _=predicted_times(rf,0,s)
            mct=next((int(c["min_connection_min"]) for c in g.get("connections",[]) if c["airport"]==lf["destination"]),40)
            if la+mct>rd: original_connection_risks.append({"Group":g["group_id"],"Connection":f"{left} → {right}","Airport":lf["destination"],"Watchlist":"Yes" if lf["destination"] in s.get("connecting_airports",[]) else "No","Predicted arrival":time_text(la),"Onward departure":time_text(rd),"Required MCT (min)":mct,"Risk":"Missed connection in pre-recovery schedule"})
    risk_groups={r["Group"] for r in original_connection_risks}
    for legal_row in dgca_rows:
        if legal_row["Group"] in risk_groups and not legal_row["Cancelled original leg"]:
            legal_row["Compensation status"]="Human review required: missed connection may have separate same-ticket protection; no amount calculated"
            legal_row["Potential INR / passenger"]="Review"

    selected_connections=[]
    for g in groups:
        for path in path_rows[g["group_id"]]:
            pax=int(solver.Value(path["var"]))
            if pax<=0: continue
            for pos,(left,right) in enumerate(zip(path["route"],path["route"][1:])):
                inbound=flight_lookup[left]; onward=flight_lookup[right]
                _,arr=predicted_times(inbound,path["delays"][pos],s)
                dep,_=predicted_times(onward,path["delays"][pos+1],s)
                airport=inbound["destination"]
                mct=next((int(c["min_connection_min"]) for c in g.get("connections",[]) if c["airport"]==airport),40)
                selected_connections.append({"Group":g["group_id"],"Passengers":pax,"Connection":f"{left} -> {right}","Airport":airport,
                    "Watched":"Yes" if airport in s.get("connecting_airports",[]) else "No","Arrival":time_text(arr),"Onward departure":time_text(dep),
                    "MCT required (min)":mct,"Connection buffer (min)":dep-arr-mct,"Feasible":"Yes" if dep-arr>=mct else "No"})

    why=[]; flight_reasons={}
    turn=int(s["profile"]["operations"]["minimum_turnaround_min"])
    for row in selected:
        fid=row["flight_id"]
        if row["status"]=="Cancelled":
            flight_reasons[fid]="No selected operating option in the least-cost feasible plan; inspect the airport, aircraft, and cancellation cost rows before staff review."
            why.append(f"{fid} is cancelled in the least-cost feasible plan; the cancellation penalty and affected passenger outcomes are listed separately.")
            continue
        if not row["delay_min"]: continue
        if d.get("type")=="Flight delay" and fid==d.get("affected_flight"):
            flight_reasons[fid]=f"The selected delay meets the configured minimum disruption delay of {d.get('minimum_delay_min')} minutes."
            why.append(f"{fid} meets the configured {d.get('minimum_delay_min')}-minute minimum departure delay.")
            continue
        prevs=[p for p in selected if p["status"]!="Cancelled" and p["aircraft_id"]==row["aircraft_id"] and p["destination"]==row["origin"] and p["arrival_min"]<=row["departure_min"] and p["flight_id"]!=fid]
        pred=max(prevs,key=lambda p:p["arrival_min"]) if prevs else None
        scheduled_dep=int(flight_lookup[fid]["scheduled_departure_min"])
        if pred and scheduled_dep < int(pred["arrival_min"])+turn <= int(row["departure_min"]):
            earliest=int(pred["arrival_min"])+turn
            flight_reasons[fid]=(f"Aircraft turnaround: {pred['flight_id']} arrives at {pred['destination']} at {time_text(pred['arrival_min'])}; "
                f"the {turn}-minute minimum makes {time_text(earliest)} the earliest departure, so the selected delay is {row['delay_min']} minutes.")
        else:
            connection_reason=None
            for g in groups:
                for path in path_rows[g["group_id"]]:
                    if int(solver.Value(path["var"]))<=0 or fid not in path["route"]: continue
                    pos=path["route"].index(fid)
                    if pos==0: continue
                    inbound_id=path["route"][pos-1]
                    inbound=flight_lookup[inbound_id]
                    inbound_delay=path["delays"][pos-1]
                    _,inbound_arr=predicted_times(inbound,inbound_delay,s)
                    mct=next((int(c["min_connection_min"]) for c in g.get("connections",[]) if c["airport"]==inbound["destination"]),40)
                    required=inbound_arr+mct
                    if int(flight_lookup[fid]["scheduled_departure_min"])<required<=int(row["departure_min"]):
                        connection_reason=f"Passenger connection: {g['group_id']} needs {mct} minutes at {inbound['destination']}; {inbound_id} arrives {time_text(inbound_arr)} and this flight departs {time_text(row['departure_min'])}."
                        break
                if connection_reason: break
            flight_reasons[fid]=connection_reason or f"Selected from the configured delay choices in the minimum-cost feasible plan; no single binding constraint is identified for this flight."
        why.append(f"{fid}: {flight_reasons[fid]}")
    for cap in cap_rows:
        if cap["Status"]=="At capacity":
            why.append(f"{cap['Airport']} {cap['Movement'].lower()} bucket at {cap['Time']} is binding at {cap['Demand']}/{cap['Capacity']} movements.")
    if not why: why.append("No flight cancellation or departure delay was required by this finite scenario; passenger choices satisfy selected connection times and seat capacities.")
    optimum=status==cp_model.OPTIMAL
    return {"status":status_name,"message":"Optimal for the defined finite candidate instance." if optimum else "Feasible plan found; optimality not proven before the limit.",
            "runtime_seconds":runtime,"optimal":optimum,"objective_inr":int(round(solver.ObjectiveValue())),"flight_plan":selected,
            "passenger_plan":passenger_rows,"cost_breakdown":component_totals,"total_cost_inr":sum(component_totals.values()),
            "dgca_results":dgca_rows,"capacity_constraints":cap_rows,"connection_risks":original_connection_risks,"selected_connections":selected_connections,
            "why_this_plan":why,"flight_reasons":flight_reasons,"options_count":len(options),"passenger_path_options":sum(len(v) for v in path_rows.values()),
            "decision_signature":selected_decision_indices}


def solve_recovery(scenario: dict[str, Any], time_limit_seconds: int = 20, alternatives: bool = True) -> dict[str, Any]:
    """Solve the integrated finite instance and optionally find one distinct alternative."""
    primary=_build_and_solve(scenario,time_limit_seconds)
    if primary.get("status") not in {"OPTIMAL","FEASIBLE"} or not alternatives:
        primary["alternatives"]=[]
        return primary
    alternative=_build_and_solve(scenario,max(1,time_limit_seconds),set(primary.get("decision_signature",[])))
    alt=[]
    if alternative.get("status") in {"OPTIMAL","FEASIBLE"}:
        alt=[{"total_cost_inr":alternative["total_cost_inr"],"solver_status":alternative["status"],"optimal":alternative["optimal"],
              "flight_plan":alternative["flight_plan"],"passenger_plan":alternative["passenger_plan"],"cost_breakdown":alternative["cost_breakdown"]}]
    primary["alternatives"]=alt
    primary["runtime_seconds_total"]=primary["runtime_seconds"]+alternative.get("runtime_seconds",0)
    if alt:
        primary["alternative_status"]=alternative["status"]
        difference=alt[0]["total_cost_inr"]-primary["total_cost_inr"]
        comparison="the same modelled cost" if difference==0 else f"₹{abs(difference):,.0f} {'more' if difference>0 else 'less'} than the selected plan"
        primary["why_this_plan"].append(f"The distinct alternative costs {comparison} using the same configured objective components.")
    return primary
