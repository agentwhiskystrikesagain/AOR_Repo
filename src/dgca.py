"""Configurable, source-labelled DGCA demonstration rules; uncertain cases stay for review."""
from __future__ import annotations

from typing import Any

def compensation_per_passenger(group: dict[str, Any], scenario: dict[str, Any]) -> tuple[int | None, str, str]:
    d = {**scenario.get("disruption", {}), **group.get("protection", {})}
    cause = str(d.get("cause", "unknown")).strip().lower()
    notice = d.get("notice_hours")
    rules = scenario["config"]["dgca_rules"]
    source = rules["source_cancel"]
    missing=[]
    if d.get("cause_status") != "Confirmed" or cause in {"", "unknown", "unresolved"}: missing.append("confirmed cause")
    if notice is None: missing.append("notice timing")
    if d.get("notice_method","Unknown")=="Unknown": missing.append("notification method")
    if d.get("check_in_status","Unknown")=="Unknown": missing.append("check-in status")
    if d.get("alternative_offered","Unknown")=="Unknown": missing.append("alternative offered")
    if d.get("passenger_choice","Unknown")=="Unknown": missing.append("passenger choice")
    if d.get("contact_information_available","Unknown")=="Unknown": missing.append("contact-information availability")
    if missing:
        return None, "Human review required: missing or uncertain "+", ".join(missing), source
    exceptions={str(x).lower() for x in rules.get("compensation_exception_causes",["weather","air traffic control","atc","security","force majeure"])}
    if any(term in cause for term in exceptions):
        return 0, "No compensation assessed: recorded cause is configured as an exceptional circumstance; verify cause", source
    if float(notice) >= 24:
        return 0, "No cancellation compensation under the configured under-24-hour trigger", source
    affected_id=d.get("affected_flight")
    original = next((f for f in scenario["flights"] if f["flight_id"]==affected_id and f["flight_id"] in group.get("itinerary", [])), None)
    if original is None:
        original = next((f for f in scenario["flights"] if f["flight_id"] in group.get("itinerary", [])), None)
    if original is None:
        return None, "Human review required: original flight block time is missing", source
    if d.get("contact_information_available")=="No":
        return None, "Human review required: contact-information status may affect compensation handling", source
    if d.get("check_in_status")!="Checked in":
        return None, "Human review required: check-in status needs case-specific review", source
    if d.get("alternative_offered")=="Yes":
        return None, "Human review required: the offered alternative's timing and passenger response must be checked", source
    block_hours = float(original.get("duration_min", 0)) / 60
    tier = next((int(x["amount_inr"]) for x in rules["cancellation_compensation_inr_by_block_time"] if block_hours <= float(x["max_hours"])), None)
    fare = group.get("fare_plus_fuel_inr")
    if tier is None or fare is None:
        return None, "Human review required: compensation tier input is missing", source
    return min(tier, int(fare)), "Potential eligibility under configured cancellation rule; staff must verify all conditions", source


def protection_summary(group: dict[str, Any], scenario: dict[str, Any], final_arrival_min: int | None,
                       cancelled_original: bool, refund: bool, flight_plan: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if cancelled_original:
        amount, status, source = compensation_per_passenger(group, scenario)
        return {"Group":group["group_id"],"Cancellation compensation status":status,
                "Compensation INR / passenger":amount if amount is not None else "Review", "Compensation source":source,
                "Refund outcome":refund,"Facilities status":"Check case-specific offer and passenger choice"}
    if final_arrival_min is None:
        return {"Group":group["group_id"],"Cancellation compensation status":"Not assessed",
                "Compensation INR / passenger":"—","Compensation source":scenario["config"]["dgca_rules"]["source_cancel"],
                "Refund outcome":refund,"Facilities status":"Human review required: no feasible flight itinerary"}
    delay = max(0, final_arrival_min - int(group["scheduled_final_arrival_min"]))
    delay_hours = delay / 60
    source = scenario["config"]["dgca_rules"]["source_cancel"]
    facility = "No configured threshold reached"
    if delay_hours >= 2:
        facility = "Check required meals/refreshments and waiting-time facts"
    return {"Group":group["group_id"],"Cancellation compensation status":"Not a cancellation assessment",
            "Compensation INR / passenger":"—","Compensation source":source,"Refund outcome":refund,
            "Facilities status":facility}

