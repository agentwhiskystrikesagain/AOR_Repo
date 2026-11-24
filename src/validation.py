"""Small, explicit validators for scenario objects and optional JSON uploads."""
from __future__ import annotations

from typing import Any
from .data import AIRPORTS


def validate_scenario(s: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("profile", "airports", "flights", "aircraft", "passenger_groups", "disruption", "weather_scenario"):
        if key not in s or s[key] is None:
            errors.append(f"Missing required scenario field: {key}.")
    if errors:
        return errors
    if len(s["profile"].get("fleet", [])) == 0:
        errors.append("Selected airline profile has no aircraft records.")
    valid_codes = set(s["airports"])
    for entity, records, id_field in (("flight", s["flights"], "flight_id"), ("aircraft", s["aircraft"], "aircraft_id"), ("passenger group", s["passenger_groups"], "group_id")):
        ids = [r.get(id_field) for r in records]
        if any(not x for x in ids): errors.append(f"A {entity} is missing {id_field}.")
        if len(ids) != len(set(ids)): errors.append(f"Duplicate {entity} IDs were found.")
    flight_ids = {f["flight_id"] for f in s["flights"]}
    for f in s["flights"]:
        if f.get("origin") not in valid_codes or f.get("destination") not in valid_codes:
            errors.append(f"{f.get('flight_id','Flight')} uses an airport code outside this scenario.")
        if int(f.get("scheduled_arrival_min", -1)) <= int(f.get("scheduled_departure_min", -1)):
            errors.append(f"{f.get('flight_id','Flight')} has an impossible same-day time sequence.")
        if int(f.get("duration_min", 0)) <= 0:
            errors.append(f"{f.get('flight_id','Flight')} has a non-positive duration.")
        if f.get("original_aircraft") not in {a["aircraft_id"] for a in s["aircraft"]}:
            errors.append(f"{f.get('flight_id','Flight')} refers to an unknown original aircraft.")
        if (f.get("origin"),f.get("destination")) not in {tuple(x) for x in s["profile"].get("routes",[])}:
            errors.append(f"{f.get('flight_id','Flight')} uses a route not configured for the selected profile.")
    for a in s["aircraft"]:
        if int(a.get("seats", 0)) <= 0 or int(a.get("available_min", -1)) < 0:
            errors.append(f"{a.get('aircraft_id','Aircraft')} has invalid capacity or availability time.")
        if a.get("location") not in valid_codes:
            errors.append(f"{a.get('aircraft_id','Aircraft')} has an unknown current location.")
        if any(code not in valid_codes for code in a.get("airport_compatibility",[])):
            errors.append(f"{a.get('aircraft_id','Aircraft')} has an invalid airport compatibility code.")
    for g in s["passenger_groups"]:
        if int(g.get("count", 0)) <= 0: errors.append(f"{g.get('group_id','Passenger group')} must have a positive passenger count.")
        if not g.get("itinerary") or any(fid not in flight_ids for fid in g.get("itinerary", [])):
            errors.append(f"{g.get('group_id','Passenger group')} has a missing or unknown original flight leg.")
        elif any(s["flights"][i]["destination"] != s["flights"][j]["origin"] for i,j in zip([next(k for k,f in enumerate(s['flights']) if f['flight_id']==fid) for fid in g['itinerary']], [next(k for k,f in enumerate(s['flights']) if f['flight_id']==fid) for fid in g['itinerary']][1:])):
            errors.append(f"{g.get('group_id','Passenger group')} itinerary airports do not connect in order.")
        if g.get("origin") not in valid_codes or g.get("destination") not in valid_codes:
            errors.append(f"{g.get('group_id','Passenger group')} has an invalid journey airport.")
    config = s.get("config", {})
    for code, caps in config.get("airport_capacity_per_bucket", {}).items():
        if code not in valid_codes or any(int(v) < 0 for v in caps.values() if v is not None):
            errors.append(f"Airport capacity for {code} is invalid (airport code or negative value).")
    if s.get("weather_scenario") not in config.get("weather_scenarios", []):
        errors.append("Weather scenario must be Summer, Monsoon, Autumn, or Winter.")
    return list(dict.fromkeys(errors))


def validate_import(payload: Any, base: dict[str, Any]) -> list[str]:
    """Accept a complete exported JSON scenario only; never silently merge partial data."""
    if not isinstance(payload, dict): return ["JSON root must be an object."]
    errors = validate_scenario(payload)
    if payload.get("schema_version") != base.get("schema_version"):
        errors.append("Unsupported schema_version; expected 1.0.")
    return errors
