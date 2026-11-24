"""Synthetic data and the shared scenario schema used by both pages."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "profiles.json"
DEMO_DATA_PATH = ROOT / "config" / "demo_schedule.json"
AIRPORTS = {"DEL": "Delhi", "BLR": "Bengaluru", "BOM": "Mumbai", "HYD": "Hyderabad", "MAA": "Chennai"}


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))


def get_profile(profile_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    profile=deepcopy(next(p for p in cfg["profiles"] if p["profile_id"] == profile_id))
    profile["routes"]=deepcopy(cfg.get("profile_routes",{}).get(profile_id,[]))
    return profile


def build_scenario(profile_id: str = "demo1") -> dict[str, Any]:
    """Return an airline-specific synthetic schedule and matching itineraries."""
    cfg = load_config()
    profile = get_profile(profile_id, cfg)
    aircraft = deepcopy(profile["fleet"])
    demo = json.loads(DEMO_DATA_PATH.read_text(encoding="utf-8-sig"))
    schedule_settings = profile.get("schedule", {})
    flight_prefix = str(schedule_settings.get("flight_number_prefix", profile_id.upper()))
    schedule_offset = int(schedule_settings.get("departure_offset_min", 0))
    flight_ids = {
        row["flight_id"]: f"{flight_prefix}{row['flight_id'][1:]}"
        for row in demo["flights"]
    }
    flights=[]
    for row in demo["flights"]:
        slot=int(row.pop("aircraft_slot"))-1
        flights.append({**row,"flight_id":flight_ids[row["flight_id"]],
            "scheduled_departure_min":int(row["scheduled_departure_min"])+schedule_offset,
            "scheduled_arrival_min":int(row["scheduled_arrival_min"])+schedule_offset,
            "profile_id":profile_id,"original_aircraft":aircraft[slot]["aircraft_id"],
            "status":"Scheduled","date":"2026-11-15","timezone":"Asia/Kolkata",
            "last_updated":"2026-11-14T09:00:00+05:30","synthetic":True})
    groups=deepcopy(demo["passenger_groups"])
    for group in groups:
        group["itinerary"]=[flight_ids[fid] for fid in group["itinerary"]]
        group["scheduled_final_arrival_min"] = int(group["scheduled_final_arrival_min"]) + schedule_offset
        for connection in group.get("connections", []):
            if connection.get("onward_flight") in flight_ids:
                connection["onward_flight"] = flight_ids[connection["onward_flight"]]
        group.update({"seats_needed_per_leg":int(group["count"]),"cause":"unknown","refund_allowed":group.get("refund_allowed",True)})
    airport_names=deepcopy(cfg.get("airport_names",AIRPORTS))
    for a in aircraft:
        a["status"]="Available"
        a["cabin_configuration"]={"standard_seats":int(a["seats"])}
        a["airport_compatibility"]=list(airport_names)
        a["flights_originally_assigned"]=[f["flight_id"] for f in flights if f["original_aircraft"]==a["aircraft_id"]]
    return {"schema_version":"1.0","profile":profile,"airports":airport_names,"flights":flights,"aircraft":aircraft,
            "passenger_groups":groups,"config":cfg,"weather_scenario":"Monsoon","monitor_airport":"BLR",
            "origin_airport":"DEL","destination_airport":"BOM","affected_origin_airport":"DEL","affected_destination_airport":"BLR","connecting_airports":["BLR"],
            "disruption":{"disruption_id":"DIS-01","name":"BLR monsoon arrival-bank delay","type":"Flight delay",
                          "affected_flight":flight_ids["F101"],"affected_aircraft":None,"affected_airport":"BLR",
                          "start_min":480+schedule_offset,"minimum_delay_min":45,"release_min":600+schedule_offset,"cause":"weather",
                          "cause_status":"Estimated","last_updated":"2026-11-14T09:00:00+05:30","confidence":"Estimated",
                          "notice_hours":None,"notice_method":"Unknown","check_in_status":"Unknown","passenger_choice":"Unknown","alternative_offered":"Unknown",
                          "contact_information_available":"Unknown","meals_provided":False,"hotel_provided":False},
            "generated_at":datetime.now().astimezone().isoformat(),"data_label":"Synthetic sample data"}


def time_text(minutes: int | None) -> str:
    if minutes is None:
        return "—"
    return f"{(int(minutes) // 60) % 24:02d}:{int(minutes) % 60:02d}"
