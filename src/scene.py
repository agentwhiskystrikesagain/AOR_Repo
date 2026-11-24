"""Local, synthetic 3D airport scene and animated aircraft positions."""
from __future__ import annotations

import math
from typing import Any

from .capacity import predicted_times

AIRPORT_COORDS = {
    "DEL": (77.1031, 28.5562), "BLR": (77.7066, 13.1986),
    "BOM": (72.8679, 19.0896), "HYD": (78.4294, 17.2403),
    "MAA": (80.1693, 12.9941),
}


def _point_on_path(path: list[list[float]], progress: float, latitude: float) -> tuple[list[float], float]:
    """Interpolate by local 3D segment length and return a north-referenced heading."""
    if len(path) < 2:
        return path[0], 0.0
    coslat = max(0.25, math.cos(math.radians(latitude)))
    lengths = []
    for a, b in zip(path, path[1:]):
        dx = (b[0] - a[0]) * 111320 * coslat
        dy = (b[1] - a[1]) * 111320
        dz = (b[2] - a[2]) * 0.35
        lengths.append(math.sqrt(dx * dx + dy * dy + dz * dz))
    target = max(0.0, min(1.0, progress)) * sum(lengths)
    walked = 0.0
    for i, length in enumerate(lengths):
        if walked + length >= target or i == len(lengths) - 1:
            a, b = path[i], path[i + 1]
            part = 0 if length == 0 else (target - walked) / length
            point = [a[j] + (b[j] - a[j]) * part for j in range(3)]
            dx = (b[0] - a[0]) * coslat
            dy = b[1] - a[1]
            heading = math.degrees(math.atan2(dx, dy))
            return point, heading
        walked += length
    return path[-1], 0.0


def _flight_times(flight: dict[str, Any], scenario: dict[str, Any], planned: dict[str, Any]) -> tuple[int, int]:
    if planned and planned.get("status") != "Cancelled":
        return int(planned["departure_min"]), int(planned["arrival_min"])
    disruption = scenario.get("disruption", {})
    delay = 0
    if disruption.get("type") == "Flight delay" and disruption.get("affected_flight") == flight["flight_id"]:
        delay = int(disruption.get("minimum_delay_min", 0))
    if disruption.get("type") == "Aircraft unavailable" and flight["original_aircraft"] == disruption.get("affected_aircraft"):
        delay = max(delay, int(disruption.get("release_min", 0)) - int(flight["scheduled_departure_min"]))
    return predicted_times(flight, max(0, delay), scenario)


def make_deck(
    scenario: dict[str, Any], airport: str, result: dict[str, Any] | None = None,
    selected_flight: str | None = None, simulation_min: int = 480,
):
    """Build a selectable local airport model; all motion is a schedule simulation."""
    import pydeck as pdk

    lon, lat = AIRPORT_COORDS.get(airport, (78.0, 20.0))

    def rect(dx1: float, dy1: float, dx2: float, dy2: float, z: float = 0) -> list[list[float]]:
        return [[lon + dx1, lat + dy1, z], [lon + dx2, lat + dy1, z],
                [lon + dx2, lat + dy2, z], [lon + dx1, lat + dy2, z]]

    def aircraft_polygon(point: list[float], heading: float) -> list[list[float]]:
        # Readable scaled plan-view silhouette, aligned to the flight's heading.
        shape = [(0, 185), (20, 44), (210, 22), (232, -15), (34, -8),
                 (28, -76), (72, -112), (64, -132), (0, -104),
                 (-64, -132), (-72, -112), (-28, -76), (-34, -8),
                 (-232, -15), (-210, 22), (-20, 44)]
        theta = math.radians(heading)
        coslat = max(0.25, math.cos(math.radians(point[1])))
        polygon = []
        for east, north in shape:
            x = east * math.cos(theta) + north * math.sin(theta)
            y = -east * math.sin(theta) + north * math.cos(theta)
            polygon.append([point[0] + x / (111320 * coslat),
                            point[1] + y / 111320, point[2] + 12])
        return polygon

    # Runway, taxiway, apron, terminal, stands and tower are local 3D scene geometry.
    airport_shapes = [
        {"name": "Runway 09 / 27", "polygon": rect(-0.030, -0.0015, 0.030, 0.0015), "fill": [48, 57, 68, 255]},
        {"name": "Taxiway", "polygon": rect(-0.023, -0.007, 0.022, -0.0055), "fill": [83, 96, 108, 255]},
        {"name": "Apron", "polygon": rect(-0.014, -0.014, 0.016, -0.008), "fill": [63, 77, 88, 255]},
    ]
    buildings = [
        {"name": "Passenger terminal", "polygon": rect(-0.010, -0.020, 0.013, -0.014), "height": 26, "fill": [65, 91, 113, 255]},
        {"name": "Concourse A", "polygon": rect(-0.013, -0.016, -0.009, -0.010), "height": 18, "fill": [77, 105, 124, 255]},
        {"name": "Concourse B", "polygon": rect(0.012, -0.016, 0.016, -0.010), "height": 18, "fill": [77, 105, 124, 255]},
    ]
    # Short white runway markings and cyan gate labels make the geometry recognizable.
    for i in range(11):
        x = -0.024 + i * 0.0048
        airport_shapes.append({"name": "Runway marking", "polygon": rect(x, -0.00015, x + 0.0017, 0.00015, 1), "fill": [218, 224, 228, 230]})
    gates = []
    gate_labels = []
    for i in range(8):
        gx = lon - 0.008 + i * 0.0027
        gy = lat - 0.012
        gates.append({"gate": f"G{i + 1:02d}", "polygon": rect(gx-lon-0.00045, gy-lat-0.00035, gx-lon+0.00045, gy-lat+0.00035, 2), "fill": [56, 164, 177, 255]})
        gate_labels.append({"position": [gx, gy, 14], "text": f"G{i + 1:02d}", "color": [236, 242, 246, 255]})

    plan = {x["flight_id"]: x for x in (result or {}).get("flight_plan", [])}
    flight_points: list[dict[str, Any]] = []
    paths = []
    labels = [
        {"position": [lon, lat + 0.004, 30], "text": "RUNWAY 09 / 27", "color": [244, 247, 250, 255]},
        {"position": [lon, lat - 0.0215, 28], "text": "TERMINAL", "color": [244, 247, 250, 255]},
        *gate_labels,
    ]
    now = int(simulation_min)
    for i, flight in enumerate(scenario["flights"]):
        if airport not in (flight["origin"], flight["destination"]):
            continue
        flight_id = flight["flight_id"]
        planned = plan.get(flight_id, {})
        status = planned.get("status", "Scheduled")
        cancelled = status == "Cancelled"
        dep, arr = _flight_times(flight, scenario, planned)
        is_arrival = flight["destination"] == airport
        xoff = -0.018 + 0.004 * (i % 8)
        gate = [lon - 0.007 + (i % 7) * 0.0021, lat - 0.011, 18]
        taxi = [lon + xoff * 0.55, lat - 0.005, 12]
        runway = [lon + xoff * 0.55, lat + 0.0005, 18]
        climb = [lon + xoff, lat + 0.015, 700]
        outbound_edge = [lon + xoff * 1.35, lat + 0.047, 1500]
        inbound_edge = [lon + xoff * 1.35, lat + 0.047, 1500]
        approach = [lon + xoff * 0.8, lat + 0.022, 850]
        final = [lon + xoff * 0.55, lat + 0.007, 260]
        touchdown = [lon + xoff * 0.55, lat + 0.0005, 18]
        arriving_path = [inbound_edge, approach, final, touchdown, taxi, gate]
        departing_path = [gate, taxi, runway, climb, outbound_edge]
        phase = ""
        point: list[float] | None = None
        heading = 0.0
        if cancelled:
            if flight["origin"] == airport:
                point = gate
                phase = "Cancelled · aircraft at stand"
        elif flight["origin"] == airport:
            if now < dep:
                point, phase = gate, "At gate · awaiting departure"
            elif now < arr:
                point, heading = _point_on_path(departing_path, (now - dep) / max(1, arr - dep), lat)
                phase = "Taxiing / departing" if now < dep + 12 else "In flight · outbound"
        else:
            if now < dep:
                point = None
            elif now < arr:
                point, heading = _point_on_path(arriving_path, (now - dep) / max(1, arr - dep), lat)
                phase = "In flight · inbound" if now < arr - 20 else ("Approach" if now < arr - 5 else "Landing")
            else:
                point, phase = gate, "Landed · at stand"

        status_color = ([203, 78, 76, 255] if cancelled else
                        [222, 151, 53, 255] if status == "Delayed" or planned.get("delay_min", 0) else
                        [44, 174, 126, 255])
        # Route lines remain visible as context; the aircraft itself follows these points over time.
        route = arriving_path if is_arrival else departing_path
        paths.append({"path": route, "color": status_color, "flight_id": flight_id})
        if point is None:
            continue
        if phase in {"At gate · awaiting departure", "Cancelled · aircraft at stand", "Landed · at stand"}:
            heading = 0.0
        airline_name = scenario["profile"]["display_name"]
        plane = {"flight_id": flight_id, "airline": airline_name,
                 "origin": flight["origin"], "destination": flight["destination"],
                 "position": point, "aircraft_polygon": aircraft_polygon(point, heading),
                 "color": status_color, "label": f"{flight_id} · {phase}", "status": status,
                 "phase": phase, "scheduled_departure_min": flight["scheduled_departure_min"],
                 "scheduled_arrival_min": flight["scheduled_arrival_min"], "predicted_departure_min": dep,
                 "predicted_arrival_min": arr, "aircraft_id": planned.get("aircraft_id", flight["original_aircraft"]),
                 "capacity_issue": "Review airport time-window table", "connection_risk": "See passenger plan"}
        flight_points.append(plane)
        outline = [255, 222, 112, 255] if flight_id == selected_flight else [250, 251, 253, 255]
        plane["outline"] = outline
        labels.append({"position": [point[0], point[1], point[2] + 85],
                       "text": f"{flight_id} · {phase}", "color": [248, 250, 252, 255]})

    tower = [{"position": [lon + 0.018, lat - 0.008, 0], "elevation": 52,
              "radius": 72, "color": [113, 133, 149, 255]}]

    layers = [
        pdk.Layer("PolygonLayer", data=airport_shapes, get_polygon="polygon", get_fill_color="fill",
                  get_line_color=[160, 173, 184, 210], line_width_min_pixels=1, extruded=False, pickable=False),
        pdk.Layer("PolygonLayer", data=buildings, get_polygon="polygon", get_fill_color="fill",
                  get_elevation="height", get_line_color=[160, 179, 192, 255], line_width_min_pixels=1,
                  extruded=True, wireframe=True, pickable=False),
        pdk.Layer("PolygonLayer", data=gates, get_polygon="polygon", get_fill_color="fill",
                  get_line_color=[105, 218, 226, 255], line_width_min_pixels=1, extruded=False, pickable=False),
        pdk.Layer("ColumnLayer", data=tower, get_position="position", get_elevation="elevation",
                  get_fill_color="color", get_radius="radius", disk_resolution=16, extruded=True, pickable=False),
        pdk.Layer("PathLayer", data=paths, get_path="path", get_color="color", get_width=5,
                  width_min_pixels=2, pickable=False),
        pdk.Layer("PolygonLayer", data=flight_points, get_polygon="aircraft_polygon", get_fill_color="color",
                  get_line_color="outline", line_width_min_pixels=2, extruded=False,
                  filled=True, pickable=True, auto_highlight=True, id="aircraft"),
        pdk.Layer("TextLayer", data=labels, get_position="position", get_text="text", get_size=15,
                  get_color="color", get_alignment_baseline="bottom", get_pixel_offset=[0, -6], pickable=False),
    ]
    view = pdk.ViewState(longitude=lon, latitude=lat - 0.007, zoom=13.2, pitch=55, bearing=-3,
                         min_zoom=11, max_zoom=16)
    deck = pdk.Deck(layers=layers, initial_view_state=view, map_provider=None,
                    tooltip={"html": "<b>{airline}</b><br/>{flight_id}<br/>{origin} → {destination}<br/>{phase}<br/>Aircraft: {aircraft_id}",
                             "style": {"color": "white"}})
    return deck, flight_points


def selected_object(selection: Any) -> dict[str, Any] | None:
    """Read Streamlit's PyDeck selection result across supported wrappers."""
    if selection is None:
        return None
    value = getattr(selection, "selection", selection)
    if isinstance(value, dict):
        objects = value.get("objects", {})
        if isinstance(objects, dict):
            selected = objects.get("aircraft", [])
            if selected:
                return selected[0]
        elif isinstance(objects, list) and objects:
            return objects[0]
        if "flight_id" in value:
            return value
    return None
