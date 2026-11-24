"""Airport capacity and weather calculations in configurable 15-minute buckets."""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def effect_for(scenario: dict[str, Any], airport: str) -> dict[str, int]:
    return scenario["config"]["weather_effects"][scenario["weather_scenario"]].get(
        airport, {"arrival_capacity_delta": 0, "departure_capacity_delta": 0, "delay_min": 0})


def predicted_times(flight: dict[str, Any], delay_min: int, scenario: dict[str, Any]) -> tuple[int, int]:
    origin_fx = effect_for(scenario, flight["origin"])
    dest_fx = effect_for(scenario, flight["destination"])
    return (int(flight["scheduled_departure_min"]) + int(delay_min),
            int(flight["scheduled_arrival_min"]) + int(delay_min) + int(dest_fx.get("delay_min", 0)))


def effective_capacity(scenario: dict[str, Any], airport: str, movement: str) -> int | None:
    base = scenario["config"]["airport_capacity_per_bucket"].get(airport, {}).get(movement)
    if base is None:
        return None
    delta_key = "arrival_capacity_delta" if movement == "arrival" else "departure_capacity_delta"
    return max(0, int(base) + int(effect_for(scenario, airport).get(delta_key, 0)))


def _active_capacity_disruption(scenario: dict[str, Any], airport: str, minute: int) -> bool:
    d = scenario.get("disruption", {})
    return d.get("type") == "Airport capacity reduction" and d.get("affected_airport") == airport and minute >= int(d.get("start_min", 0))


def movement_counts(scenario: dict[str, Any], mode: str = "scheduled", result: dict[str, Any] | None = None) -> dict[tuple[str, int, str], int]:
    bucket = int(scenario["config"].get("time_bucket_min", 15))
    counts: dict[tuple[str, int, str], int] = defaultdict(int)
    selected={x["flight_id"]:x for x in (result or {}).get("flight_plan",[])}
    for f in scenario["flights"]:
        if mode == "recommended" and f["flight_id"] in selected:
            op=selected[f["flight_id"]]
            if op["status"]=="Cancelled": continue
            dep,arr=op["departure_min"],op["arrival_min"]
        elif mode == "scheduled":
            dep, arr = f["scheduled_departure_min"], f["scheduled_arrival_min"]
        else:
            d = scenario.get("disruption", {})
            delay = int(d.get("minimum_delay_min", 0)) if d.get("type") == "Flight delay" and d.get("affected_flight") == f["flight_id"] else 0
            if d.get("type") == "Aircraft unavailable" and f["original_aircraft"] == d.get("affected_aircraft"):
                delay = max(delay, int(d.get("release_min", 0)) - int(f["scheduled_departure_min"]))
            dep, arr = predicted_times(f, max(0, delay), scenario)
        counts[(f["origin"], (int(dep)//bucket)*bucket, "Departure")] += 1
        counts[(f["destination"], (int(arr)//bucket)*bucket, "Arrival")] += 1
        counts[(f["origin"], (int(dep)//bucket)*bucket, "Combined")] += 1
        counts[(f["destination"], (int(arr)//bucket)*bucket, "Combined")] += 1
    return counts


def capacity_timeline(scenario: dict[str, Any], airport: str, result: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    bucket = int(scenario["config"].get("time_bucket_min", 15))
    counts = movement_counts(scenario, "scheduled")
    predicted = movement_counts(scenario, "predicted")
    recommended = movement_counts(scenario, "recommended", result) if result else {}
    active_minutes = sorted({minute for (code, minute, _kind) in set(counts) | set(predicted) | set(recommended) if code == airport})
    rows: list[dict[str, Any]] = []
    for minute in active_minutes:
        for kind, key in (("Arrival", "arrival"), ("Departure", "departure"), ("Combined", "combined")):
            scheduled = counts.get((airport, minute, kind), 0)
            revised = predicted.get((airport, minute, kind), 0)
            recommended_count = recommended.get((airport,minute,kind), 0) if result else None
            base=scenario["config"]["airport_capacity_per_bucket"].get(airport,{}).get(key)
            cap = effective_capacity(scenario, airport, key) if key != "combined" else (None if base is None else int(base))
            if _active_capacity_disruption(scenario, airport, minute):
                cap = max(0, int(cap or 0) - int(scenario["disruption"].get("capacity_reduction", 1)))
            rows.append({"Airport": airport, "Window start (min)": minute, "Window (local)": f"{minute//60:02d}:{minute%60:02d}–{(minute+bucket)//60:02d}:{(minute+bucket)%60:02d}",
                         "Movement": kind, "Scheduled": scheduled, "Predicted": revised,
                         "Capacity / {bucket} min".format(bucket=bucket): str(cap) if cap is not None else "Not supplied",
                         "Scheduled conflict": cap is not None and scheduled > cap,
                         "Recommended": str(recommended_count) if recommended_count is not None else "—",
                         "Predicted conflict": cap is not None and revised > cap,
                         "Recommended conflict": cap is not None and result is not None and isinstance(recommended_count,int) and recommended_count > cap,
                         "Source": "Synthetic sample data"})
    return rows


def weather_assumption_rows(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    effects = scenario["config"]["weather_effects"][scenario["weather_scenario"]]
    bucket=int(scenario["config"].get("time_bucket_min",15))
    return [{"Airport": code, f"Capacity effect (arrivals / {bucket}m)": fx.get("arrival_capacity_delta", 0),
             f"Capacity effect (departures / {bucket}m)": fx.get("departure_capacity_delta", 0),
             "Assumed arrival delay (min)": fx.get("delay_min", 0), "Preset": scenario["weather_scenario"],
             "Basis": "Editable seasonal scenario assumption; no live forecast"} for code, fx in effects.items()]


def restriction_reason(scenario: dict[str, Any], flight: dict[str, Any], departure: int, arrival: int) -> str | None:
    restrictions = scenario["config"].get("restrictions", {})
    d = scenario.get("disruption", {})
    closures = list(restrictions.get("closures", []))
    if d.get("type") in {"Airport closure", "Security restriction"} and d.get("affected_airport"):
        closures.append({"airport": d["affected_airport"], "start_min": d.get("start_min", 0), "end_min": d.get("end_min", 1440),
                         "security": d.get("type") == "Security restriction"})
    for c in closures:
        restriction_name="Security restriction" if c.get("security") else "Airport closure"
        if c.get("airport") == flight["origin"] and int(c.get("start_min", 0)) <= departure < int(c.get("end_min", 1440)):
            return f"{restriction_name} at origin {flight['origin']} is active in this time window"
        if c.get("airport") == flight["destination"] and int(c.get("start_min", 0)) <= arrival < int(c.get("end_min", 1440)):
            return f"{restriction_name} at destination {flight['destination']} is active in this time window"
    for curfew in restrictions.get("curfews", []):
        start=int(curfew.get("start_min",0)); end=int(curfew.get("end_min",1440))
        active=lambda minute: (start<=minute<end) if start<=end else (minute>=start or minute<end)
        if flight["origin"] == curfew.get("airport") and active(departure):
            return f"Departure falls within {flight['origin']} curfew"
        if flight["destination"] == curfew.get("airport") and active(arrival):
            return f"Arrival falls within {flight['destination']} curfew"
    return None

