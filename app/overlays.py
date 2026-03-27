from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any

# ============================================================================
# COORDINATE SYSTEMS
# ============================================================================
# All overlay payloads exposed by this module are returned in game hex
# coordinates (232x205, 1-indexed).
# ============================================================================

GAME_COLS = 232
GAME_ROWS = 205
COMBAT_REPORT_FILE_NAME = "combatreport.txt"
COMBAT_SEPARATOR_PATTERN = re.compile(r"^-{20,}\s*$")
COMBAT_COORDS_PATTERN = re.compile(r"\((\d+),(\d+)\)|at\s+(\d+),(\d+)", re.IGNORECASE)
INVASION_TITLE_PATTERN = re.compile(
    r"^(?P<kind>Amphibious Assault(?: at)?|Pre-Invasion action off)\s+(?P<name>.+?)\s*\((?P<x>\d+),(?P<y>\d+)\)",
    re.IGNORECASE,
)

_CARRIER_AIRCRAFT_RANGE_SPECS: dict[str, list[tuple[re.Pattern[str], float]]] = {
    "japan": [
        (re.compile(r"A6M\d*[A-Z]*S?\s+ZERO", re.IGNORECASE), 6.0),
        (re.compile(r"B5N\d*[A-Z]*S?\s+KATE", re.IGNORECASE), 6.0),
        (re.compile(r"D3A\d*[A-Z]*S?\s+VAL", re.IGNORECASE), 4.0),
        (re.compile(r"B6N\d*[A-Z]*S?\s+JILL", re.IGNORECASE), 6.0),
        (re.compile(r"D4Y\d*[A-Z]*S?\s+JUDY", re.IGNORECASE), 6.0),
        (re.compile(r"B7A\d*[A-Z]*S?\s+GRACE", re.IGNORECASE), 6.0),
    ],
    "allies": [
        (re.compile(r"F2A-\d+\s+BUFFALO", re.IGNORECASE), 5.0),
        (re.compile(r"F4F-\d+[A-Z]?\s+WILDCAT", re.IGNORECASE), 6.0),
        (re.compile(r"F6F-\d+[A-Z]?\s+HELLCAT", re.IGNORECASE), 7.0),
        (re.compile(r"F4U-\d+[A-Z]?\s+CORSAIR", re.IGNORECASE), 7.0),
        (re.compile(r"SBD-\d+\s+DAUNTLESS", re.IGNORECASE), 5.0),
        (re.compile(r"SB2C-\d+[A-Z]?\s+HELLDIVER", re.IGNORECASE), 5.0),
        (re.compile(r"TBD-\d+\s+DEVASTATOR", re.IGNORECASE), 4.0),
        (re.compile(r"TBF-\d+[A-Z]?\s+AVENGER", re.IGNORECASE), 6.0),
        (re.compile(r"TBM-\d+[A-Z]?\s+AVENGER", re.IGNORECASE), 6.0),
        (re.compile(r"SEA\s+HURRICANE", re.IGNORECASE), 6.0),
        (re.compile(r"MARTLET", re.IGNORECASE), 6.0),
        (re.compile(r"FULMAR", re.IGNORECASE), 5.0),
        (re.compile(r"ALBACORE", re.IGNORECASE), 5.0),
        (re.compile(r"BARRACUDA", re.IGNORECASE), 5.0),
        (re.compile(r"SWORDFISH", re.IGNORECASE), 4.0),
        (re.compile(r"SEAFIRE", re.IGNORECASE), 7.0),
    ],
}

HQ_OVERLAY_KINDS: dict[str, set[str]] = {
    "sea": {"naval", "theater"},
    "air": {"air", "theater"},
    "land": {"corp", "army", "theater"},
}

AREA_COMMAND_DATASET: dict[str, str] = {
    "air": "airgroups",
    "land": "ground_units",
}

LOGISTICS_TF_MISSIONS: dict[str, dict[str, str]] = {
    "TANKER": {
        "solid_color": "rgba(255,165,0,1)",
        "dash_color": "rgba(255,165,0,1)",
        "marker_fill": "rgba(255,205,130,1)",
        "marker_outline": "rgba(210,115,0,1)",
    },
    "CARGO": {
        "solid_color": "rgba(255,225,60,1)",
        "dash_color": "rgba(255,225,60,1)",
        "marker_fill": "rgba(255,244,170,1)",
        "marker_outline": "rgba(190,160,0,1)",
    },
}

AIR_MISSION_OVERLAYS: dict[str, dict[str, str]] = {
    "search": {
        "overlay_id": "air-search",
        "overlay_name": "Air Search",
        "allocation_field": "percent_search",
        "arc_start_field": "search_arc_start",
        "arc_end_field": "search_arc_end",
        "mission_label": "Search",
        "fill_color": "rgba(88,220,108,0.18)",
        "stroke_color": "rgba(88,220,108,0.95)",
    },
    "asw": {
        "overlay_id": "air-asw",
        "overlay_name": "Air ASW",
        "allocation_field": "percent_asw",
        "arc_start_field": "asw_arc_start",
        "arc_end_field": "asw_arc_end",
        "mission_label": "ASW",
        "fill_color": "rgba(255,126,185,0.17)",
        "stroke_color": "rgba(255,126,185,0.96)",
    },
}

AIR_ATTACK_BOMBER_TYPE_CODES: set[str] = {
    "LB",
    "MB",
    "HB",
    "DB",
    "TB",
    "FB",
    "TBF",
    "LONGB",
    "MEDB",
    "HEAVB",
    "LIGHTB",
    "SHORTB",
    "1EB",
    "2EB",
    "4EB",
}

LOGGER = logging.getLogger(__name__)
MAX_JSON_READ_BYTES = 64 * 1024 * 1024
MAX_JSON_LINE_RECORDS = 500_000

_REGION_FEATURES: list[dict[str, Any]] = [
    {
        "name": "North Pacific",
        "abbr": "NOPAC",
        "color": "rgba(100,149,237,0.25)",
        "border": "rgba(100,149,237,0.7)",
        # Use y=0 on the north edge so this region reaches the map top border.
        "polygon": [[88, 0], [GAME_COLS, 0], [GAME_COLS, 55], [88, 55]],
    },
    {
        "name": "Central Pacific",
        "abbr": "CENPAC",
        "color": "rgba(0,200,255,0.20)",
        "border": "rgba(0,200,255,0.7)",
        "polygon": [[96, 55], [GAME_COLS, 55], [GAME_COLS, 120], [96, 120]],
    },
    {
        "name": "South Pacific",
        "abbr": "SOPAC",
        "color": "rgba(0,200,100,0.20)",
        "border": "rgba(0,200,100,0.7)",
        "polygon": [[106, 120], [GAME_COLS, 120], [GAME_COLS, GAME_ROWS], [106, GAME_ROWS]],
    },
    {
        "name": "Southwest Pacific",
        "abbr": "SWPAC",
        "color": "rgba(255,165,0,0.20)",
        "border": "rgba(255,165,0,0.7)",
        "polygon": [[40, 120], [106, 120], [106, GAME_ROWS], [40, GAME_ROWS]],
    },
    {
        "name": "Netherlands East Indies",
        "abbr": "NEI",
        "color": "rgba(200,100,220,0.20)",
        "border": "rgba(200,100,220,0.7)",
        "polygon": [[40, 87], [96, 87], [96, 120], [40, 120]],
    },
    {
        "name": "Philippines / Malaya",
        "abbr": "PHIL",
        "color": "rgba(220,50,50,0.20)",
        "border": "rgba(220,50,50,0.7)",
        "polygon": [[40, 55], [96, 55], [96, 87], [40, 87]],
    },
    {
        "name": "China-Burma-India",
        "abbr": "CBI",
        "color": "rgba(240,220,0,0.20)",
        "border": "rgba(240,220,0,0.7)",
        # Use y=0 on the north edge so this region reaches the map top border.
        "polygon": [[1, 0], [88, 0], [88, 55], [1, 55]],
    },
    {
        "name": "Indian Ocean",
        "abbr": "IO",
        "color": "rgba(0,180,180,0.20)",
        "border": "rgba(0,180,180,0.7)",
        "polygon": [[1, 55], [40, 55], [40, GAME_ROWS], [1, GAME_ROWS]],
    },
]


def _taskforces_path(game_path: str, side: str) -> Path:
    side_folder = "ALLIED" if side == "allies" else "JAPAN"
    return Path(game_path) / "SAVE" / side_folder / "taskforces.json"


def _threats_path(game_path: str, side: str) -> Path:
    side_folder = "ALLIED" if side == "allies" else "JAPAN"
    return Path(game_path) / "SAVE" / side_folder / "threats.json"


def _bases_path(game_path: str, side: str) -> Path:
    side_folder = "ALLIED" if side == "allies" else "JAPAN"
    return Path(game_path) / "SAVE" / side_folder / "bases.json"


def _ground_units_path(game_path: str, side: str) -> Path:
    side_folder = "ALLIED" if side == "allies" else "JAPAN"
    return Path(game_path) / "SAVE" / side_folder / "ground_units.json"


def _ships_path(game_path: str, side: str) -> Path:
    side_folder = "ALLIED" if side == "allies" else "JAPAN"
    return Path(game_path) / "SAVE" / side_folder / "ships.json"


def _airgroups_path(game_path: str, side: str) -> Path:
    side_folder = "ALLIED" if side == "allies" else "JAPAN"
    return Path(game_path) / "SAVE" / side_folder / "airgroups.json"


def _minefields_path(game_path: str, side: str) -> Path:
    side_folder = "ALLIED" if side == "allies" else "JAPAN"
    return Path(game_path) / "SAVE" / side_folder / "minefields.json"


def _combat_report_path(game_path: str) -> Path:
    return Path(game_path) / "SAVE" / COMBAT_REPORT_FILE_NAME


def _load_json_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    try:
        file_size = path.stat().st_size
    except OSError:
        file_size = 0

    # Avoid full-file reads for very large exports where a 32-bit process can
    # exhaust address space during decode/allocation.
    if file_size > MAX_JSON_READ_BYTES:
        records = _load_json_lines_records(path)
        if records:
            return records
        LOGGER.warning(
            "Skipping oversized JSON file for overlays: %s (%s bytes)",
            path,
            file_size,
        )
        return []

    try:
        raw_text = path.read_text(encoding="utf-8")
    except (MemoryError, OSError, UnicodeError):
        records = _load_json_lines_records(path)
        if records:
            return records
        LOGGER.warning("Failed to read JSON file for overlays: %s", path, exc_info=True)
        return []

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        payload = None

    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        for key in ("taskforces", "entries", "records", "items", "data"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, dict)]
        return []

    records: list[dict[str, Any]] = []
    for raw in raw_text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)

    return records


def _load_json_lines_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                if len(records) >= MAX_JSON_LINE_RECORDS:
                    LOGGER.warning(
                        "Reached JSON line parsing cap (%s records) for %s",
                        MAX_JSON_LINE_RECORDS,
                        path,
                    )
                    break

                line = raw.strip()
                if not line:
                    continue

                # NDJSON export format is supported as a low-memory fallback.
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if isinstance(record, dict):
                    records.append(record)
    except (MemoryError, OSError):
        LOGGER.warning("Failed fallback line-read for %s", path, exc_info=True)
        return []

    return records


def _format_invasion_type(kind: str) -> str:
    normalized_kind = kind.strip().lower()
    if normalized_kind.startswith("amphibious assault at"):
        return "Amphibious Assault"
    return "Pre-Invasion Action"


def _load_invasion_records(game_path: str) -> list[dict[str, Any]]:
    report_path = _combat_report_path(game_path)
    if not report_path.exists():
        return []

    try:
        raw_text = report_path.read_text(encoding="utf-8")
    except OSError:
        return []

    records: list[dict[str, Any]] = []
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = INVASION_TITLE_PATTERN.match(line)
        if match is None:
            continue
        records.append(
            {
                "name": match.group("name").strip(),
                "center": [int(match.group("x")), int(match.group("y"))],
                "invasion_type": _format_invasion_type(match.group("kind")),
                "title": line,
            }
        )

    return records


def _parse_combat_report_blocks(game_path: str) -> list[dict[str, Any]]:
    report_path = _combat_report_path(game_path)
    if not report_path.exists():
        return []

    try:
        raw_text = report_path.read_text(encoding="utf-8")
    except OSError:
        return []

    blocks: list[dict[str, Any]] = []
    chunk: list[str] = []

    def flush_chunk() -> None:
        nonlocal chunk
        content = "\n".join(chunk).strip()
        chunk = []
        if not content:
            return
        title = next((line.strip() for line in content.splitlines() if line.strip()), "")
        if not title or title.upper().startswith("AFTER ACTION REPORTS FOR "):
            return
        match = COMBAT_COORDS_PATTERN.search(title)
        coords: tuple[int, int] | None = None
        if match:
            values = [value for value in match.groups() if value]
            if len(values) >= 2:
                coords = (int(values[0]), int(values[1]))
        blocks.append({"title": title, "content": content, "coords": coords})

    for raw_line in raw_text.splitlines():
        if COMBAT_SEPARATOR_PATTERN.match(raw_line.strip()):
            flush_chunk()
            continue
        chunk.append(raw_line)
    flush_chunk()
    return blocks


def _extract_attacking_aircraft_names(block_content: str) -> list[str]:
    names: list[str] = []
    in_attacking_section = False
    pattern = re.compile(
        r"^\s*\d+\s*x\s+(?P<aircraft>.+?)\s+(?:bombing|launching|strafing|sweeping|attacking)\b",
        re.IGNORECASE,
    )

    for raw_line in block_content.splitlines():
        stripped = raw_line.strip()
        if stripped.upper() == "AIRCRAFT ATTACKING:":
            in_attacking_section = True
            continue
        if not in_attacking_section:
            continue
        if not stripped:
            if names:
                break
            continue
        match = pattern.match(stripped)
        if match:
            names.append(match.group("aircraft").strip())
            continue
        if stripped.endswith(":"):
            break
    return names


def _carrier_aircraft_range(name: str, enemy_side: str) -> float | None:
    normalized_name = str(name or "").strip()
    for pattern, radius_hexes in _CARRIER_AIRCRAFT_RANGE_SPECS[enemy_side]:
        if pattern.search(normalized_name):
            return radius_hexes
    return None


def _load_combat_report_carrier_supplements(game_path: str, side: str) -> list[dict[str, Any]]:
    enemy_side = "japan" if side == "allies" else "allies"
    supplements: list[dict[str, Any]] = []

    for block in _parse_combat_report_blocks(game_path):
        title = str(block.get("title") or "")
        coords = block.get("coords")
        if coords is None or "AIR ATTACK" not in title.upper():
            continue

        aircraft_names = _extract_attacking_aircraft_names(str(block.get("content") or ""))
        if not aircraft_names:
            continue
        aircraft_ranges = [_carrier_aircraft_range(name, enemy_side) for name in aircraft_names]
        if any(radius is None for radius in aircraft_ranges):
            continue

        supplements.append(
            {
                "center": [coords[0], coords[1]],
                "radius_hexes": float(min(radius for radius in aircraft_ranges if radius is not None)),
                "score": 0,
                "fill_color": "rgba(120,205,255,0.30)",
                "stroke_color": "rgba(120,205,255,0.96)",
                "radius_source": "combat-report-supplement",
            }
        )

    return supplements


def _load_taskforce_records(game_path: str, side: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for record in _load_json_records(_taskforces_path(game_path, side)):

        coords = (
            record.get("start_of_day_x"),
            record.get("start_of_day_y"),
            record.get("end_of_day_x"),
            record.get("end_of_day_y"),
            record.get("target_x"),
            record.get("target_y"),
        )
        if any(value is None for value in coords):
            continue
        records.append(record)

    return records


def _load_base_records(game_path: str, side: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for record in _load_json_records(_bases_path(game_path, side)):
        x = record.get("x")
        y = record.get("y")
        if x is None or y is None:
            continue
        try:
            int(x)
            int(y)
        except (TypeError, ValueError):
            continue
        records.append(record)

    return records


def _load_ground_unit_records(game_path: str, side: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for record in _load_json_records(_ground_units_path(game_path, side)):
        x = record.get("end_of_day_x")
        y = record.get("end_of_day_y")
        if x is None or y is None:
            continue
        try:
            int(x)
            int(y)
        except (TypeError, ValueError):
            continue
        records.append(record)

    return records


def _load_airgroup_records(game_path: str, side: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for record in _load_json_records(_airgroups_path(game_path, side)):
        x = record.get("x")
        y = record.get("y")
        if x is None or y is None:
            continue
        try:
            int(x)
            int(y)
        except (TypeError, ValueError):
            continue
        records.append(record)

    return records


def _build_hq_position_map(game_path: str, side: str) -> dict[int, tuple[int, int, str]]:
    positions: dict[int, tuple[int, int, str]] = {}
    for record in _load_ground_unit_records(game_path, side):
        if str(record.get("unit_type_name") or "").upper() != "HQ":
            continue

        record_id = _safe_int(record.get("record_id"), 0)
        x = _safe_int(record.get("end_of_day_x"), 0)
        y = _safe_int(record.get("end_of_day_y"), 0)
        if record_id <= 0 or x < 1 or y < 1:
            continue

        positions[record_id] = (x, y, str(record.get("name") or "HQ"))

    return positions


def _load_minefield_records(game_path: str, side: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    expected_side = "ALLIED" if side == "allies" else "JAPAN"
    for record in _load_json_records(_minefields_path(game_path, side)):
        x = record.get("x")
        y = record.get("y")
        if x is None or y is None:
            continue
        try:
            x_value = int(x)
            y_value = int(y)
        except (TypeError, ValueError):
            continue
        if x_value < 1 or y_value < 1:
            continue

        record_side = str(record.get("side") or "").strip().upper()
        if record_side and record_side != expected_side:
            continue

        records.append(record)

    return records


def _bbox_polygon(points: list[tuple[int, int]], pad: int = 1) -> list[list[int]]:
    min_x = max(1, min(point[0] for point in points) - pad)
    max_x = min(GAME_COLS, max(point[0] for point in points) + pad)
    min_y = max(1, min(point[1] for point in points) - pad)
    max_y = min(GAME_ROWS, max(point[1] for point in points) + pad)
    return [[min_x, min_y], [max_x, min_y], [max_x, max_y], [min_x, max_y]]


def _cross(o: tuple[int, int], a: tuple[int, int], b: tuple[int, int]) -> int:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _convex_hull(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
    unique_points = sorted(set(points))
    if len(unique_points) <= 1:
        return unique_points

    lower: list[tuple[int, int]] = []
    for point in unique_points:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)

    upper: list[tuple[int, int]] = []
    for point in reversed(unique_points):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)

    return lower[:-1] + upper[:-1]


def _pad_polygon_outward(points: list[tuple[int, int]], pad: float = 1.0) -> list[tuple[int, int]]:
    if not points:
        return []

    cx = sum(point[0] for point in points) / len(points)
    cy = sum(point[1] for point in points) / len(points)
    expanded: list[tuple[int, int]] = []

    for x, y in points:
        dx = x - cx
        dy = y - cy
        length = math.hypot(dx, dy)
        if length <= 0.0001:
            ex = x
            ey = y
        else:
            ex = x + (dx / length) * pad
            ey = y + (dy / length) * pad

        ex_i = max(1, min(GAME_COLS, int(round(ex))))
        ey_i = max(1, min(GAME_ROWS, int(round(ey))))
        if not expanded or expanded[-1] != (ex_i, ey_i):
            expanded.append((ex_i, ey_i))

    if len(expanded) < 3:
        return points
    return expanded


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_compass_degrees(value: Any) -> float:
    degrees = _safe_float(value, 0.0) % 360.0
    if degrees < 0:
        degrees += 360.0
    return degrees


def _ratio(amount: int, required: int) -> float:
    if required <= 0:
        return 999.0 if amount > 0 else 0.0
    return amount / required


def _base_supply_marker_style(
    supply: int,
    supply_required: int,
    supply_ratio: float,
    fuel: int,
    fuel_required: int,
    fuel_ratio: float,
) -> tuple[str, str]:
    # Gold-star rule requires both high ratio and meaningful absolute surplus.
    supply_overstock = supply_ratio > 2.0 and (supply - supply_required) > 5000
    fuel_overstock = fuel_ratio > 2.0 and (fuel - fuel_required) > 5000
    if supply_overstock or fuel_overstock:
        return "star", "gold"

    # Fallback coloring after star check:
    # green requires both resources >80% with no upper cap.
    supply_healthy = supply_ratio > 0.8
    fuel_healthy = fuel_ratio > 0.8
    supply_low = supply_ratio < 0.8
    fuel_low = fuel_ratio < 0.8

    if supply_healthy and fuel_healthy:
        return "circle", "green"
    if supply_low and fuel_low:
        return "circle", "red"
    return "circle", "yellow"


def _load_threats_payload(game_path: str, side: str) -> dict[str, Any]:
    threats_path = _threats_path(game_path, side)
    if not threats_path.exists():
        return {}

    payload: Any = None
    for encoding in ("utf-8", "utf-8-sig", "utf-16", "cp1252"):
        try:
            payload = json.loads(threats_path.read_text(encoding=encoding))
            break
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue

    if isinstance(payload, dict):
        return payload
    return {}


def _threat_position(record: dict[str, Any]) -> tuple[int, int] | None:
    position = record.get("position")
    if not isinstance(position, dict):
        return None

    x = position.get("x")
    y = position.get("y")
    if x is None or y is None:
        return None

    try:
        return int(x), int(y)
    except (TypeError, ValueError):
        return None


def get_available_overlays() -> list[dict[str, str]]:
    return [
        {
            "id": "regions",
            "name": "Operating Regions",
            "description": "Allied and Japanese operating-theater boundaries",
        },
        {
            "id": "invasions",
            "name": "Invasions",
            "description": "White star markers for amphibious assaults and pre-invasion actions from combatreport.txt",
        },
        {
            "id": "taskforces",
            "name": "Taskforces",
            "description": "Taskforce movement paths (solid: start->end, dotted: end->target)",
        },
        {
            "id": "subpatrols",
            "name": "Subpatrols",
            "description": "Subpatrol destination markers (2-hex green circles)",
        },
        {
            "id": "threats",
            "name": "Threats",
            "description": "Threat overlays for subs, surface forces, carriers, and threat areas",
        },
        {
            "id": "base-supply",
            "name": "Base Supply",
            "description": "Base supply circles sized by on-hand supply",
        },
        {
            "id": "logistics-taskforces",
            "name": "Logistics Taskforces",
            "description": "Cargo and tanker taskforce movement paths",
        },
        {
            "id": "hq-coverage",
            "name": "HQ Coverage",
            "description": "Six-hex command-radius circles for HQ overlays by map mode",
        },
        {
            "id": "air-search",
            "name": "Air Search",
            "description": "Airgroup search sectors using aircraft range and search arcs",
        },
        {
            "id": "air-asw",
            "name": "Air ASW",
            "description": "Airgroup ASW sectors using aircraft range and ASW arcs",
        },
        {
            "id": "air-attack",
            "name": "Air Attack Range",
            "description": "Dashed attack-range circles for bomber and strike airgroups",
        },
        {
            "id": "air-hq-link",
            "name": "Airgroup HQ Link",
            "description": "Dashed cyan lines from airgroups to their linked HQ when separated",
        },
        {
            "id": "sea-minefields",
            "name": "Sea Minefields",
            "description": "Black X markers centered on friendly minefield hexes",
        },
        {
            "id": "land-unit-hq-link",
            "name": "Unit HQ Link",
            "description": "Dashed yellow lines from ground units to their attached HQ when separated",
        },
        {
            "id": "land-planning",
            "name": "Planning",
            "description": "Dashed red lines from ground units to their preparation target when separated",
        },
    ]


def _is_air_attack_range_type(record: dict[str, Any]) -> bool:
    aircraft_type = str(record.get("aircraft_type_name") or "").strip().upper()
    normalized_aircraft_type = "".join(ch for ch in aircraft_type if ch.isalnum())
    aircraft_name = str(record.get("aircraft_name") or "").strip().upper()
    composite = f"{aircraft_type} {aircraft_name}"

    if aircraft_type in AIR_ATTACK_BOMBER_TYPE_CODES or normalized_aircraft_type in AIR_ATTACK_BOMBER_TYPE_CODES:
        return True

    if "LIGHT" in composite and "BOMB" in composite:
        return True
    if "MEDIUM" in composite and "BOMB" in composite:
        return True
    if "HEAVY" in composite and "BOMB" in composite:
        return True

    for keyword in ("ATTACK", "DIVE", "TORPEDO"):
        if keyword in composite:
            return True

    return False


def get_regions_overlay(map_width: int, map_height: int) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    for feature in _REGION_FEATURES:
        features.append(
            {
                "name": feature["name"],
                "abbr": feature["abbr"],
                "color": feature["color"],
                "border": feature["border"],
                "polygon": feature["polygon"],
            }
        )

    return {
        "overlay_id": "regions",
        "overlay_name": "Operating Regions",
        "type": "polygon",
        "cols": GAME_COLS,
        "rows": GAME_ROWS,
        "map_width": map_width,
        "map_height": map_height,
        "features": features,
    }


def get_invasions_overlay(game_path: str, map_width: int, map_height: int) -> dict[str, Any]:
    grouped: dict[tuple[int, int], dict[str, Any]] = {}

    for record in _load_invasion_records(game_path):
        center = record["center"]
        key = (int(center[0]), int(center[1]))
        feature = grouped.get(key)
        if feature is None:
            feature = {
                "name": str(record.get("name") or "Invasion"),
                "center": [key[0], key[1]],
                "marker_shape": "star",
                "marker_fill": "rgba(255,255,255,0.96)",
                "marker_stroke": "rgba(255,255,255,0.96)",
                "report_count": 0,
                "invasion_types": [],
                "titles": [],
            }
            grouped[key] = feature

        invasion_type = str(record.get("invasion_type") or "Invasion")
        title = str(record.get("title") or "").strip()
        feature["report_count"] = int(feature["report_count"]) + 1
        if invasion_type not in feature["invasion_types"]:
            feature["invasion_types"].append(invasion_type)
        if title and title not in feature["titles"]:
            feature["titles"].append(title)

    features = sorted(grouped.values(), key=lambda item: (int(item["center"][1]), int(item["center"][0]), str(item["name"])))
    return {
        "overlay_id": "invasions",
        "overlay_name": "Invasions",
        "type": "invasions",
        "cols": GAME_COLS,
        "rows": GAME_ROWS,
        "map_width": map_width,
        "map_height": map_height,
        "features": features,
    }


def get_taskforces_overlay(game_path: str, side: str, map_width: int, map_height: int) -> dict[str, Any]:
    lines: list[dict[str, Any]] = []
    for record in _load_taskforce_records(game_path, side):
        mission = str(record.get("mission") or "")
        mission_upper = mission.upper()
        if mission_upper in {"SUBPATROL", "CARGO", "TANKER"}:
            continue
        start_of_day = [int(record["start_of_day_x"]), int(record["start_of_day_y"])]
        end_of_day = [int(record["end_of_day_x"]), int(record["end_of_day_y"])]
        lines.append(
            {
                "name": record.get("flagship_name") or "TF",
                "mission": mission,
                "start": start_of_day,
                "end": end_of_day,
                "target": [int(record["target_x"]), int(record["target_y"])],
                "start_of_day_valid": start_of_day != end_of_day,
            }
        )

    return {
        "overlay_id": "taskforces",
        "overlay_name": "Taskforces",
        "type": "taskforces",
        "cols": GAME_COLS,
        "rows": GAME_ROWS,
        "map_width": map_width,
        "map_height": map_height,
        "features": lines,
    }


def get_subpatrols_overlay(game_path: str, side: str, map_width: int, map_height: int) -> dict[str, Any]:
    circles: list[dict[str, Any]] = []
    for record in _load_taskforce_records(game_path, side):
        mission = str(record.get("mission") or "")
        if mission.upper() != "SUBPATROL":
            continue
        circles.append(
            {
                "name": record.get("flagship_name") or "SUBPATROL",
                "center": [int(record["target_x"]), int(record["target_y"])],
                "radius_hexes": 2,
            }
        )

    return {
        "overlay_id": "subpatrols",
        "overlay_name": "Subpatrols",
        "type": "subpatrols",
        "cols": GAME_COLS,
        "rows": GAME_ROWS,
        "map_width": map_width,
        "map_height": map_height,
        "features": circles,
    }


def get_base_supply_overlay(game_path: str, side: str, map_width: int, map_height: int) -> dict[str, Any]:
    circles: list[dict[str, Any]] = []
    for record in _load_base_records(game_path, side):
        supply = _safe_int(record.get("supply"), 0)
        supply_required = _safe_int(record.get("supply_needed"), 0)
        fuel = _safe_int(record.get("fuel"), 0)
        fuel_required = _safe_int(record.get("fuel_needed"), 0)

        supply_ratio = _ratio(supply, supply_required)
        fuel_ratio = _ratio(fuel, fuel_required)
        marker_shape, marker_color = _base_supply_marker_style(
            supply,
            supply_required,
            supply_ratio,
            fuel,
            fuel_required,
            fuel_ratio,
        )

        circles.append(
            {
                "name": str(record.get("name") or "Base"),
                "center": [int(record["x"]), int(record["y"])],
                "supply": supply,
                "supply_required": supply_required,
                "fuel": fuel,
                "fuel_required": fuel_required,
                "supply_ratio": supply_ratio,
                "fuel_ratio": fuel_ratio,
                "marker_shape": marker_shape,
                "marker_color": marker_color,
            }
        )

    return {
        "overlay_id": "base-supply",
        "overlay_name": "Base Supply",
        "type": "base-supply",
        "cols": GAME_COLS,
        "rows": GAME_ROWS,
        "map_width": map_width,
        "map_height": map_height,
        "features": circles,
    }


def get_logistics_taskforces_overlay(game_path: str, side: str, map_width: int, map_height: int) -> dict[str, Any]:
    lines: list[dict[str, Any]] = []
    mission_counts = {key: 0 for key in LOGISTICS_TF_MISSIONS}
    for record in _load_taskforce_records(game_path, side):
        mission = str(record.get("mission") or "").upper()
        colors = LOGISTICS_TF_MISSIONS.get(mission)
        if colors is None:
            continue
        mission_counts[mission] += 1
        start_of_day = [int(record["start_of_day_x"]), int(record["start_of_day_y"])]
        end_of_day = [int(record["end_of_day_x"]), int(record["end_of_day_y"])]
        lines.append(
            {
                "name": record.get("flagship_name") or "TF",
                "mission": mission,
                # Logistics view plots from current position (end_of_day) to destination.
                "start": end_of_day,
                "end": end_of_day,
                "target": [int(record["target_x"]), int(record["target_y"])],
                "current": end_of_day,
                "start_of_day": start_of_day,
                "start_of_day_valid": start_of_day != end_of_day,
                **colors,
            }
        )

    return {
        "overlay_id": "logistics-taskforces",
        "overlay_name": "Logistics Taskforces",
        "type": "taskforces",
        "cols": GAME_COLS,
        "rows": GAME_ROWS,
        "map_width": map_width,
        "map_height": map_height,
        "mission_types": [mission for mission, count in mission_counts.items() if count > 0],
        "features": lines,
    }


def get_hq_overlay(game_path: str, side: str, map_width: int, map_height: int, mode: str) -> dict[str, Any]:
    normalized_mode = str(mode or "").strip().lower()
    allowed_kinds = HQ_OVERLAY_KINDS.get(normalized_mode, set())

    circles: list[dict[str, Any]] = []
    for record in _load_ground_unit_records(game_path, side):
        if str(record.get("unit_type_name") or "").upper() != "HQ":
            continue

        hq_kind = str(record.get("hq_kind") or "").strip().lower()
        if hq_kind not in allowed_kinds:
            continue

        circles.append(
            {
                "name": str(record.get("name") or "HQ"),
                "base_name": str(record.get("stationed_at_base_name") or "Unknown Base"),
                "hq_kind": hq_kind,
                "center": [int(record["end_of_day_x"]), int(record["end_of_day_y"])],
                "radius_hexes": 3,
            }
        )

    circles.sort(key=lambda feature: (str(feature.get("hq_kind") or ""), str(feature.get("name") or "")))

    title_mode = normalized_mode.title() or "HQ"
    return {
        "overlay_id": f"{normalized_mode}-hq",
        "overlay_name": f"{title_mode} HQ Overlay",
        "type": "hq-coverage",
        "cols": GAME_COLS,
        "rows": GAME_ROWS,
        "map_width": map_width,
        "map_height": map_height,
        "hq_kinds": sorted(allowed_kinds),
        "features": circles,
    }


def get_unit_hq_link_overlay(game_path: str, side: str, map_width: int, map_height: int) -> dict[str, Any]:
    records = _load_ground_unit_records(game_path, side)
    hq_positions: dict[int, tuple[int, int, str]] = {}

    for record in records:
        if str(record.get("unit_type_name") or "").upper() != "HQ":
            continue

        record_id = _safe_int(record.get("record_id"), 0)
        x = _safe_int(record.get("end_of_day_x"), 0)
        y = _safe_int(record.get("end_of_day_y"), 0)
        if record_id <= 0 or x < 1 or y < 1:
            continue
        hq_positions[record_id] = (x, y, str(record.get("name") or "HQ"))

    features: list[dict[str, Any]] = []
    for record in records:
        if str(record.get("unit_type_name") or "").upper() == "HQ":
            continue

        attached_hq_id = _safe_int(record.get("attached_hq_id"), 0)
        hq_position = hq_positions.get(attached_hq_id)
        if hq_position is None:
            continue

        unit_x = _safe_int(record.get("end_of_day_x"), 0)
        unit_y = _safe_int(record.get("end_of_day_y"), 0)
        if unit_x < 1 or unit_y < 1:
            continue

        hq_x, hq_y, hq_name = hq_position
        if unit_x == hq_x and unit_y == hq_y:
            continue

        features.append(
            {
                "name": str(record.get("name") or "Unit"),
                "unit_type_name": str(record.get("unit_type_name") or "Unknown"),
                "hq_name": str(record.get("attached_hq_name") or hq_name or "HQ"),
                "start": [unit_x, unit_y],
                "end": [hq_x, hq_y],
                "stroke_color": "rgba(255,220,60,0.98)",
            }
        )

    features.sort(key=lambda feature: (str(feature.get("hq_name") or ""), str(feature.get("name") or "")))
    return {
        "overlay_id": "land-unit-hq-link",
        "overlay_name": "Unit HQ Link",
        "type": "unit-hq-link",
        "cols": GAME_COLS,
        "rows": GAME_ROWS,
        "map_width": map_width,
        "map_height": map_height,
        "features": features,
    }


def get_planning_overlay(game_path: str, side: str, map_width: int, map_height: int) -> dict[str, Any]:
    records = _load_ground_unit_records(game_path, side)
    features: list[dict[str, Any]] = []

    for record in records:
        unit_x = _safe_int(record.get("end_of_day_x"), 0)
        unit_y = _safe_int(record.get("end_of_day_y"), 0)
        target_x = _safe_int(record.get("prep_target_x"), 0)
        target_y = _safe_int(record.get("prep_target_y"), 0)
        if unit_x < 1 or unit_y < 1 or target_x < 1 or target_y < 1:
            continue
        if unit_x == target_x and unit_y == target_y:
            continue

        features.append(
            {
                "name": str(record.get("name") or "Unit"),
                "unit_type_name": str(record.get("unit_type_name") or "Unknown"),
                "planning_name": str(record.get("prep_target_name") or "Preparation Target"),
                "prep_percent": _safe_int(record.get("prep_percent"), 0),
                "start": [unit_x, unit_y],
                "end": [target_x, target_y],
                "stroke_color": "rgba(238,78,78,0.98)",
            }
        )

    features.sort(key=lambda feature: (str(feature.get("planning_name") or ""), str(feature.get("name") or "")))
    return {
        "overlay_id": "land-planning",
        "overlay_name": "Planning",
        "type": "planning-link",
        "cols": GAME_COLS,
        "rows": GAME_ROWS,
        "map_width": map_width,
        "map_height": map_height,
        "features": features,
    }


def get_area_command_overlay(game_path: str, side: str, map_width: int, map_height: int, mode: str) -> dict[str, Any]:
    normalized_mode = str(mode or "").strip().lower()
    dataset = AREA_COMMAND_DATASET.get(normalized_mode)
    if dataset is None:
        return {
            "overlay_id": f"{normalized_mode}-area-command",
            "overlay_name": "Area Command",
            "type": "area-command",
            "cols": GAME_COLS,
            "rows": GAME_ROWS,
            "map_width": map_width,
            "map_height": map_height,
            "features": [],
        }

    records = _load_airgroup_records(game_path, side) if dataset == "airgroups" else _load_ground_unit_records(game_path, side)

    grouped: dict[str, list[tuple[int, int]]] = {}
    for record in records:
        area_command = str(record.get("area_command") or "").strip()
        if not area_command:
            continue
        if dataset == "airgroups" and area_command.lower() == "independent":
            continue

        if dataset == "airgroups":
            x = int(record["x"])
            y = int(record["y"])
        else:
            x = int(record["end_of_day_x"])
            y = int(record["end_of_day_y"])

        grouped.setdefault(area_command, []).append((x, y))

    features: list[dict[str, Any]] = []
    for area_command, points in grouped.items():
        if not points:
            continue

        hull = _convex_hull(points)
        if len(hull) < 3:
            polygon = _bbox_polygon(points, pad=1)
        else:
            padded_hull = _pad_polygon_outward(hull, pad=1.0)
            polygon = [[x, y] for x, y in padded_hull]

        features.append(
            {
                "name": area_command,
                "polygon": polygon,
                "unit_count": len(points),
            }
        )

    features.sort(key=lambda feature: str(feature.get("name") or ""))
    return {
        "overlay_id": f"{normalized_mode}-area-command",
        "overlay_name": f"{normalized_mode.title()} Area Command",
        "type": "area-command",
        "cols": GAME_COLS,
        "rows": GAME_ROWS,
        "map_width": map_width,
        "map_height": map_height,
        "features": features,
    }


def get_air_mission_overlay(game_path: str, side: str, map_width: int, map_height: int, mission_kind: str) -> dict[str, Any]:
    normalized_kind = str(mission_kind or "").strip().lower()
    config = AIR_MISSION_OVERLAYS.get(normalized_kind)
    if config is None:
        return {
            "overlay_id": f"air-{normalized_kind}",
            "overlay_name": "Air Mission",
            "type": "air-mission-sectors",
            "cols": GAME_COLS,
            "rows": GAME_ROWS,
            "map_width": map_width,
            "map_height": map_height,
            "mission_kind": normalized_kind,
            "features": [],
        }

    allocation_field = config["allocation_field"]
    arc_start_field = config["arc_start_field"]
    arc_end_field = config["arc_end_field"]

    features: list[dict[str, Any]] = []
    for record in _load_airgroup_records(game_path, side):
        allocation_pct = _safe_int(record.get(allocation_field), 0)
        if allocation_pct <= 0:
            continue

        radius_hexes = _safe_float(record.get("aircraft_range"), 0.0)
        if radius_hexes <= 0:
            continue

        x = _safe_int(record.get("x"), 0)
        y = _safe_int(record.get("y"), 0)
        if x < 1 or y < 1:
            continue

        arc_start = _normalize_compass_degrees(record.get(arc_start_field))
        arc_end = _normalize_compass_degrees(record.get(arc_end_field))
        is_full_circle = math.isclose((arc_end - arc_start) % 360.0, 0.0, abs_tol=0.001)

        features.append(
            {
                "name": str(record.get("name") or "Airgroup"),
                "aircraft_type": str(record.get("aircraft_name") or record.get("aircraft_type_name") or "Unknown"),
                "base_name": str(record.get("stationed_at_base_name") or record.get("stationed_on_ship_name") or "Unknown Base"),
                "center": [x, y],
                "radius_hexes": radius_hexes,
                "allocation_pct": allocation_pct,
                "arc_start_degrees": arc_start,
                "arc_end_degrees": arc_end,
                "is_full_circle": is_full_circle,
                "mission_kind": normalized_kind,
                "mission_label": config["mission_label"],
                "fill_color": config["fill_color"],
                "stroke_color": config["stroke_color"],
                "primary_mission_code": _safe_int(record.get("primary_mission_code"), 0),
                "secondary_mission_code": _safe_int(record.get("secondary_mission_code"), 0),
            }
        )

    features.sort(
        key=lambda feature: (
            -int(feature.get("allocation_pct") or 0),
            -float(feature.get("radius_hexes") or 0.0),
            str(feature.get("name") or ""),
        )
    )

    return {
        "overlay_id": config["overlay_id"],
        "overlay_name": config["overlay_name"],
        "type": "air-mission-sectors",
        "cols": GAME_COLS,
        "rows": GAME_ROWS,
        "map_width": map_width,
        "map_height": map_height,
        "mission_kind": normalized_kind,
        "features": features,
    }


def get_air_attack_range_overlay(game_path: str, side: str, map_width: int, map_height: int) -> dict[str, Any]:
    features: list[dict[str, Any]] = []

    for record in _load_airgroup_records(game_path, side):
        if not _is_air_attack_range_type(record):
            continue

        radius_hexes = _safe_float(record.get("aircraft_range"), 0.0)
        if radius_hexes <= 0:
            continue

        x = _safe_int(record.get("end_of_day_x"), _safe_int(record.get("end_x"), _safe_int(record.get("x"), 0)))
        y = _safe_int(record.get("end_of_day_y"), _safe_int(record.get("end_y"), _safe_int(record.get("y"), 0)))
        if x < 1 or y < 1:
            continue

        features.append(
            {
                "name": str(record.get("name") or "Airgroup"),
                "aircraft_type": str(record.get("aircraft_name") or record.get("aircraft_type_name") or "Unknown"),
                "base_name": str(record.get("stationed_at_base_name") or record.get("stationed_on_ship_name") or "Unknown Base"),
                "center": [x, y],
                "radius_hexes": radius_hexes,
                "stroke_color": "rgba(88,220,108,0.96)",
                "dash_len": 10.0,
            }
        )

    features.sort(key=lambda feature: (-float(feature.get("radius_hexes") or 0.0), str(feature.get("name") or "")))

    return {
        "overlay_id": "air-attack",
        "overlay_name": "Air Attack Range",
        "type": "air-attack-range",
        "cols": GAME_COLS,
        "rows": GAME_ROWS,
        "map_width": map_width,
        "map_height": map_height,
        "features": features,
    }


def get_airgroup_hq_link_overlay(game_path: str, side: str, map_width: int, map_height: int) -> dict[str, Any]:
    records = _load_airgroup_records(game_path, side)
    hq_positions = _build_hq_position_map(game_path, side)

    features: list[dict[str, Any]] = []
    for record in records:
        group_x = _safe_int(record.get("x"), 0)
        group_y = _safe_int(record.get("y"), 0)
        if group_x < 1 or group_y < 1:
            continue

        candidate_ids = [
            _safe_int(record.get("assigned_hq_id"), 0),
            _safe_int(record.get("local_air_hq_source_unit_id"), 0),
            _safe_int(record.get("local_fleet_hq_source_unit_id"), 0),
        ]
        candidate_names = [
            str(record.get("assigned_hq_name") or "").strip(),
            str(record.get("local_air_hq_name") or "").strip(),
            str(record.get("local_fleet_hq_name") or "").strip(),
        ]
        candidate_sources = ["assigned", "local-air", "local-fleet"]

        selected = None
        for idx, candidate_id in enumerate(candidate_ids):
            if candidate_id <= 0:
                continue
            hq_position = hq_positions.get(candidate_id)
            if hq_position is None:
                continue
            selected = (candidate_id, idx, hq_position)
            break

        if selected is None:
            continue

        _hq_id, selected_idx, (hq_x, hq_y, fallback_hq_name) = selected
        if group_x == hq_x and group_y == hq_y:
            continue

        hq_name = candidate_names[selected_idx] or fallback_hq_name or "HQ"
        features.append(
            {
                "name": str(record.get("name") or "Airgroup"),
                "aircraft_type": str(record.get("aircraft_name") or record.get("aircraft_type_name") or "Unknown"),
                "base_name": str(record.get("stationed_at_base_name") or record.get("stationed_on_ship_name") or "Unknown Base"),
                "hq_name": hq_name,
                "hq_source": candidate_sources[selected_idx],
                "start": [group_x, group_y],
                "end": [hq_x, hq_y],
                "stroke_color": "rgba(110,214,255,0.98)",
            }
        )

    features.sort(key=lambda feature: (str(feature.get("hq_name") or ""), str(feature.get("name") or "")))
    return {
        "overlay_id": "air-hq-link",
        "overlay_name": "Airgroup HQ Link",
        "type": "air-hq-link",
        "cols": GAME_COLS,
        "rows": GAME_ROWS,
        "map_width": map_width,
        "map_height": map_height,
        "features": features,
    }


def get_minefields_overlay(game_path: str, side: str, map_width: int, map_height: int) -> dict[str, Any]:
    by_hex: dict[tuple[int, int], int] = {}
    for record in _load_minefield_records(game_path, side):
        x = _safe_int(record.get("x"), 0)
        y = _safe_int(record.get("y"), 0)
        mine_count = _safe_int(record.get("mine_count"), _safe_int(record.get("number"), 0))
        if x < 1 or y < 1 or mine_count <= 0:
            continue
        by_hex[(x, y)] = by_hex.get((x, y), 0) + mine_count

    features = [
        {
            "center": [x, y],
            "mine_count": mine_count,
            "size_hexes": 0.45,
            "stroke_color": "rgba(0,0,0,0.96)",
        }
        for (x, y), mine_count in sorted(by_hex.items())
    ]

    return {
        "overlay_id": "sea-minefields",
        "overlay_name": "Sea Minefields",
        "type": "minefields",
        "cols": GAME_COLS,
        "rows": GAME_ROWS,
        "map_width": map_width,
        "map_height": map_height,
        "features": features,
    }


def get_threats_overlay(game_path: str, side: str, map_width: int, map_height: int) -> dict[str, Any]:
    payload = _load_threats_payload(game_path, side)

    feature_sets: dict[str, list[dict[str, Any]]] = {
        "sub": [],
        "surface": [],
        "carrier": [],
        "areas": [],
    }

    threat_display = {
        "sub": {
            "radius_hexes": 3.0,
            "fill_color": "rgba(255,70,70,0.32)",
            "stroke_color": "rgba(255,70,70,0.92)",
        },
        "surface": {
            "radius_hexes": 3.0,
            "fill_color": "rgba(255,220,60,0.30)",
            "stroke_color": "rgba(255,220,60,0.94)",
        },
        "carrier": {
            "radius_hexes": 6.0,
            "fill_color": "rgba(120,205,255,0.30)",
            "stroke_color": "rgba(120,205,255,0.96)",
        },
    }

    for key, threat_kind in (
        ("sub_threat_areas", "sub"),
        ("surface_threat_areas", "surface"),
        ("carrier_threat_areas", "carrier"),
    ):
        raw_items = payload.get(key)
        if not isinstance(raw_items, list):
            continue
        for record in raw_items:
            if not isinstance(record, dict):
                continue
            position = _threat_position(record)
            if position is None:
                continue
            radius_hexes = float(threat_display[threat_kind]["radius_hexes"])
            if threat_kind == "carrier":
                radius_hexes = float(record.get("display_radius_hexes") or radius_hexes)
            feature_sets[threat_kind].append(
                {
                    "center": list(position),
                    "radius_hexes": radius_hexes,
                    "score": int(record.get("threat_score") or 0),
                    "fill_color": threat_display[threat_kind]["fill_color"],
                    "stroke_color": threat_display[threat_kind]["stroke_color"],
                    "radius_source": str(record.get("display_radius_source") or ""),
                }
            )

    raw_areas = payload.get("threat_areas")

    # Fallback for older/newer schema variants where split subtype arrays are
    # absent or empty: derive subtype circles from threat_areas.threat_types.
    def derive_kind_from_areas(threat_kind: str) -> None:
        if feature_sets[threat_kind]:
            return
        if not isinstance(raw_areas, list):
            return
        for record in raw_areas:
            if not isinstance(record, dict):
                continue
            threat_types = record.get("threat_types")
            if isinstance(threat_types, str):
                normalized_types = {threat_types.strip().lower()}
            elif isinstance(threat_types, list):
                normalized_types = {
                    str(value).strip().lower()
                    for value in threat_types
                    if str(value).strip()
                }
            else:
                normalized_types = set()
            if threat_kind not in normalized_types:
                continue

            position = _threat_position(record)
            if position is None:
                continue

            radius_hexes = float(threat_display[threat_kind]["radius_hexes"])
            if threat_kind == "carrier":
                radius_hexes = float(record.get("display_radius_hexes") or radius_hexes)

            feature_sets[threat_kind].append(
                {
                    "center": list(position),
                    "radius_hexes": radius_hexes,
                    "score": int(record.get("threat_score") or 0),
                    "fill_color": threat_display[threat_kind]["fill_color"],
                    "stroke_color": threat_display[threat_kind]["stroke_color"],
                    "radius_source": str(record.get("display_radius_source") or ""),
                }
            )

    derive_kind_from_areas("sub")
    derive_kind_from_areas("surface")
    derive_kind_from_areas("carrier")

    if isinstance(raw_areas, list):
        for record in raw_areas:
            if not isinstance(record, dict):
                continue
            position = _threat_position(record)
            if position is None:
                continue
            feature_sets["areas"].append(
                {
                    "center": list(position),
                    "size_hexes": 1,
                    "score": int(record.get("threat_score") or 0),
                }
            )

    existing_carrier_centers = {tuple(feature.get("center", [0, 0])) for feature in feature_sets["carrier"]}
    for feature in _load_combat_report_carrier_supplements(game_path, side):
        center_key = tuple(feature.get("center", [0, 0]))
        if center_key in existing_carrier_centers:
            continue
        feature_sets["carrier"].append(feature)
        existing_carrier_centers.add(center_key)

    return {
        "overlay_id": "threats",
        "overlay_name": "Threats",
        "type": "threats",
        "cols": GAME_COLS,
        "rows": GAME_ROWS,
        "map_width": map_width,
        "map_height": map_height,
        "features": feature_sets,
    }


# ---------------------------------------------------------------------------
# Theater of Equipment (TOE) helpers
# ---------------------------------------------------------------------------

_OUTSIDE_THEATER = "Off-Map / Outside Theater"
_OUTSIDE_ABBR = "OUT"
_THEATER_MIN_X = 4
_THEATER_MAX_X = 226
_THEATER_MIN_Y = 5
_THEATER_MAX_Y = 201


def _classify_hex_region(x: int, y: int) -> str | None:
    """Return the theater region name for hex (x, y), or None when outside theater bounds/regions."""
    # Treat off-map/off-theater hexes as outside theater before region checks.
    if x < _THEATER_MIN_X or x > _THEATER_MAX_X or y < _THEATER_MIN_Y or y > _THEATER_MAX_Y:
        return None

    for region in _REGION_FEATURES:
        poly = region["polygon"]
        xs = [pt[0] for pt in poly]
        ys = [pt[1] for pt in poly]
        if min(xs) <= x <= max(xs) and min(ys) <= y <= max(ys):
            return str(region["name"])
    return None


def get_shipyard_data(game_path: str, side: str) -> dict[str, Any]:
    """Return damaged ships and shipyard information.
    
    Damaged ships are grouped by location (at sea via task force, or at base).
    Shipyards show tonnage capacity and ships currently under repair.
    All groups are sorted by theater region.
    """
    # Load base info for shipyards
    base_names: dict[int, str] = {}  # id -> name
    base_coords: dict[int, tuple[int, int]] = {}  # id -> (x, y)
    shipyards: dict[str, dict[str, Any]] = {}  # base_name -> {tonnage, count, ships, coords}
    
    for record in _load_json_records(_bases_path(game_path, side)):
        base_id = _safe_int(record.get("record_id"), 0)
        if base_id == 0:
            continue
        base_name = str(record.get("name") or "").strip() or f"Base {base_id}"
        base_names[base_id] = base_name
        x = _safe_int(record.get("x"), None)
        y = _safe_int(record.get("y"), None)
        if x is not None and y is not None:
            base_coords[base_id] = (x, y)

        # Support both scraper schemas:
        # 1) explicit tons (`ship_repair_capacity_tons`),
        # 2) shipyard size points (`ship_repair` or legacy `devices.*shipyard*`) where 1 point = 1000 tons.
        shipyard_tonnage = _safe_int(record.get("ship_repair_capacity_tons"), 0)
        if shipyard_tonnage <= 0:
            ship_repair_points = _safe_int(record.get("ship_repair"), 0)
            if ship_repair_points > 0:
                shipyard_tonnage = ship_repair_points * 1000
        if shipyard_tonnage <= 0:
            devices = record.get("devices", {})
            if isinstance(devices, dict):
                for device_name, device_value in devices.items():
                    if "shipyard" in str(device_name).lower():
                        device_points = _safe_int(device_value, 0)
                        if device_points > 0:
                            shipyard_tonnage = device_points * 1000
                        break

        if shipyard_tonnage > 0:
            shipyards[base_name] = {
                "tonnage": shipyard_tonnage,
                "count": 0,
                "ships": [],
                "coords": (x, y) if (x is not None and y is not None) else None,
            }

    # Load task force info for at-sea groups
    taskforces: dict[int, dict[str, Any]] = {}  # tf_id -> {flagship, x, y}
    for record in _load_json_records(_taskforces_path(game_path, side)):
        tf_id = _safe_int(record.get("record_id"), 0)
        if tf_id == 0:
            continue
        flagship = str(record.get("flagship_name") or f"TF {tf_id}").strip()
        x = _safe_int(record.get("end_of_day_x"), None)
        y = _safe_int(record.get("end_of_day_y"), None)
        taskforces[tf_id] = {"flagship": flagship, "x": x, "y": y}

    # Load ships and categorize
    at_sea_damaged: dict[str, list[dict[str, Any]]] = {}  # tf_flagship -> [ships]
    at_base_damaged: dict[str, list[dict[str, Any]]] = {}  # base_name -> [ships]

    def _first_nonempty_text(record: dict[str, Any], keys: list[str], default: str = "") -> str:
        for key in keys:
            value = record.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return default

    def _first_int(record: dict[str, Any], keys: list[str], default: int = 0) -> int:
        for key in keys:
            if key in record:
                value = _safe_int(record.get(key), default)
                if value != default:
                    return value
        return default
    
    damage_key_options = [
        "Sys",
        "Flt",
        "Eng",
        "Fire",
        "system_damage",
        "flotation_damage",
        "engine_damage",
        "fire_damage",
        "sys_damage",
        "float_damage",
        "flt_damage",
        "eng_damage",
    ]
    state_key_options = ["current_state", "repair_state", "state", "status", "location"]
    saw_any_damage_fields = False

    for record in _load_json_records(_ships_path(game_path, side)):
        if _safe_int(record.get("record_id"), 0) == 0:
            continue

        if not saw_any_damage_fields and any(key in record for key in damage_key_options):
            saw_any_damage_fields = True

        # Check if damaged (any damage type > 0)
        sys_damage = _first_int(record, ["Sys", "system_damage", "sys_damage"], 0)
        flt_damage = _first_int(record, ["Flt", "flotation_damage", "float_damage", "flt_damage"], 0)
        eng_damage = _first_int(record, ["Eng", "engine_damage", "eng_damage"], 0)
        fire_damage = _first_int(record, ["Fire", "fire_damage"], 0)
        
        if sys_damage <= 0 and flt_damage <= 0 and eng_damage <= 0 and fire_damage <= 0:
            continue

        ship_type = str(record.get("ship_class_type_name") or "UNKNOWN").strip() or "UNKNOWN"
        ship_name = str(record.get("name") or "Unknown Ship").strip()
        tonnage = _safe_int(record.get("tonnage"), _safe_int(record.get("ship_class_tonnage"), 0))

        current_state = _first_nonempty_text(
            record,
            state_key_options,
            "Damaged",
        )

        ship_info = {
            "type": ship_type,
            "name": ship_name,
            "tonnage": tonnage,
            "state": current_state,
            "sys_damage": sys_damage,
            "flt_damage": flt_damage,
            "eng_damage": eng_damage,
            "fire_damage": fire_damage,
        }

        # States indicating a ship is under repair/yard control.
        state_lc = current_state.lower()
        if "shipyard" in state_lc or "repair" in state_lc or "drydock" in state_lc:
            base_id = _safe_int(record.get("stationed_at_base_id"), 0)
            if base_id > 0:
                base_name = base_names.get(base_id, f"Base {base_id}")
                if base_name in shipyards:
                    shipyards[base_name]["ships"].append(ship_info)
                    shipyards[base_name]["count"] += 1
            continue

        # Categorize: at sea or at base
        tf_id = _safe_int(record.get("task_force_id"), 0)
        if tf_id > 0:
            # At sea in task force
            tf_info = taskforces.get(tf_id)
            if tf_info:
                flagship = tf_info["flagship"]
                if flagship not in at_sea_damaged:
                    at_sea_damaged[flagship] = []
                at_sea_damaged[flagship].append(ship_info)
        else:
            # At base (but not in shipyard)
            base_id = _safe_int(record.get("stationed_at_base_id"), 0)
            if base_id > 0:
                base_name = base_names.get(base_id, f"Base {base_id}")
                if base_name not in at_base_damaged:
                    at_base_damaged[base_name] = []
                at_base_damaged[base_name].append(ship_info)

    # Format output
    damaged_ships = []
    
    # Build a mapping from flagship name to tf_id and tf_info for quick lookup
    flagship_to_tf: dict[str, tuple[int, dict[str, Any]]] = {}
    for tf_id, tf_info in taskforces.items():
        flagship_to_tf[tf_info["flagship"]] = (tf_id, tf_info)
    
    # At-sea damaged ships
    for flagship_name in sorted(at_sea_damaged.keys(), key=lambda f: (flagship_to_tf.get(f, (-1, {}))[0], f)):
        ships = sorted(
            at_sea_damaged[flagship_name],
            key=lambda s: (-s["tonnage"], s["type"], s["name"]),
        )
        # Get TF coordinates and compute region
        region = _OUTSIDE_THEATER
        if flagship_name in flagship_to_tf:
            tf_id, tf_info = flagship_to_tf[flagship_name]
            x = tf_info.get("x")
            y = tf_info.get("y")
            if x is not None and y is not None:
                region = _classify_hex_region(x, y) or _OUTSIDE_THEATER
            display_name = f"{flagship_name} (TF{tf_id})"
        else:
            display_name = flagship_name
        
        damaged_ships.append({
            "location": "At Sea",
            "name": display_name,
            "region": region,
            "count": len(ships),
            "ships": ships,
        })

    # At-base damaged ships (excluding those in shipyard)
    for base_name in at_base_damaged.keys():
        ships = sorted(
            at_base_damaged[base_name],
            key=lambda s: (-s["tonnage"], s["type"], s["name"]),
        )
        region = _OUTSIDE_THEATER
        # Find base_id for this base_name to get coordinates
        for bid, bname in base_names.items():
            if bname == base_name and bid in base_coords:
                x, y = base_coords[bid]
                region = _classify_hex_region(x, y) or _OUTSIDE_THEATER
                break
        damaged_ships.append({
            "location": base_name,
            "name": None,
            "region": region,
            "count": len(ships),
            "ships": ships,
        })

    # Sort damaged ships by region, then by location name
    damaged_ships.sort(key=lambda g: (g["region"], g["location"] if g["name"] is None else g["name"]))

    # Format shipyards
    shipyard_list = []
    for base_name in shipyards.keys():
        yard = shipyards[base_name]
        yard["ships"].sort(key=lambda s: (-s["tonnage"], s["type"], s["name"]))
        in_repair_tonnage = sum(int(ship.get("tonnage", 0)) for ship in yard["ships"])
        x, y = (yard.get("coords") or (None, None))
        region = _OUTSIDE_THEATER
        if x is not None and y is not None:
            region = _classify_hex_region(x, y) or _OUTSIDE_THEATER
        shipyard_list.append({
            "base": base_name,
            "tonnage": yard["tonnage"],
            "in_repair_count": yard["count"],
            "in_repair_tonnage": in_repair_tonnage,
            "region": region,
            "ships": yard["ships"],
        })

    # Sort shipyards by region, then by base name
    shipyard_list.sort(key=lambda y: (y["region"], y["base"]))

    return {
        "damaged_ships": damaged_ships,
        "shipyards": shipyard_list,
        "damaged_notice": (
            "No ship damage fields were found in ships.json. Export Sys/Flt/Eng/Fire (or system/flotation/engine/fire damage) from pywitpaescraper to populate this list."
            if not damaged_ships and not saw_any_damage_fields
            else ""
        ),
    }


def get_toe_data(game_path: str, side: str) -> dict[str, Any]:
    """Return a table-of-equipment breakdown by theater region.

    Filters applied:
      - Ships:       record_id != 0  AND  task_force_id == 0 (not in a task force)
      - Airgroups:   record_id != 0  AND  loaded_on_ship_id is None
      - Ground units: record_id != 0 AND  loaded_on_ship_id is None

    Ships and airgroups at base have x=0,y=0; their position is resolved via the
    base coordinate map built from bases.json.

    Returns ``{"regions": [...]}`` where each region entry has ``name``,
    ``abbr``, and three lists (``ships``, ``airgroups``, ``ground``).
    Each list item includes ``type``, ``count``, and ``nations`` for
    expand/collapse rendering in the UI. Nation rows also include nested
    ``bases`` with per-base counts.
    "Outside Theater" appears first when populated.
    """
    region_order = [_OUTSIDE_THEATER] + [r["name"] for r in _REGION_FEATURES]
    region_abbr: dict[str, str] = {r["name"]: r["abbr"] for r in _REGION_FEATURES}
    region_abbr[_OUTSIDE_THEATER] = _OUTSIDE_ABBR

    ships_by_region: dict[str, dict[str, dict[str, dict[str, int]]]] = {name: {} for name in region_order}
    air_by_region: dict[str, dict[str, dict[str, dict[str, int]]]] = {name: {} for name in region_order}
    ground_by_region: dict[str, dict[str, dict[str, dict[str, int]]]] = {name: {} for name in region_order}

    # Build base_id lookups from bases.json for position and base naming.
    base_coords: dict[int, tuple[int, int]] = {}
    base_names: dict[int, str] = {}
    for record in _load_json_records(_bases_path(game_path, side)):
        base_id = _safe_int(record.get("record_id"), 0)
        if base_id == 0:
            continue
        try:
            bx = int(record["x"])
            by = int(record["y"])
        except (KeyError, TypeError, ValueError):
            continue
        base_coords[base_id] = (bx, by)
        base_name = str(record.get("name") or "").strip()
        base_names[base_id] = base_name or f"Base {base_id}"

    def _add_type_nation_base(
        bucket: dict[str, dict[str, dict[str, int]]],
        type_name: str,
        nation_name: str,
        base_name: str,
    ) -> None:
        type_bucket = bucket.setdefault(type_name, {})
        nation_bucket = type_bucket.setdefault(nation_name, {})
        nation_bucket[base_name] = nation_bucket.get(base_name, 0) + 1

    # --- Ships (not in a task force; position comes from stationed_at_base_id) ---
    for record in _load_json_records(_ships_path(game_path, side)):
        if _safe_int(record.get("record_id"), 0) == 0:
            continue
        if _safe_int(record.get("task_force_id"), 0) != 0:
            continue
        base_id = _safe_int(record.get("stationed_at_base_id"), 0)
        if base_id == 0:
            continue
        coords = base_coords.get(base_id)
        if coords is None:
            continue
        x, y = coords
        type_name = str(record.get("ship_class_type_name") or "UNKNOWN").strip() or "UNKNOWN"
        nation_name = str(record.get("nation") or "UNKNOWN").strip() or "UNKNOWN"
        base_name = str(record.get("stationed_at_base_name") or "").strip() or base_names.get(base_id, f"Base {base_id}")
        region = _classify_hex_region(x, y) or _OUTSIDE_THEATER
        _add_type_nation_base(ships_by_region[region], type_name, nation_name, base_name)

    # --- Airgroups (not loaded on a ship; use own x/y if set, else base_id) ---
    for record in _load_airgroup_records(game_path, side):
        if _safe_int(record.get("record_id"), 0) == 0:
            continue
        if record.get("loaded_on_ship_id") is not None:
            continue
        own_x = _safe_int(record.get("x"), 0)
        own_y = _safe_int(record.get("y"), 0)
        if own_x != 0 or own_y != 0:
            x, y = own_x, own_y
            base_id = _safe_int(record.get("base_id"), 0)
            base_name = base_names.get(base_id, f"Base {base_id}") if base_id else "In Flight / At Sea"
        else:
            base_id = _safe_int(record.get("base_id"), 0)
            if base_id == 0:
                continue
            coords = base_coords.get(base_id)
            if coords is None:
                continue
            x, y = coords
            base_name = base_names.get(base_id, f"Base {base_id}")
        type_name = str(record.get("aircraft_name") or "UNKNOWN").strip() or "UNKNOWN"
        nation_name = str(record.get("nation") or "UNKNOWN").strip() or "UNKNOWN"
        region = _classify_hex_region(x, y) or _OUTSIDE_THEATER
        _add_type_nation_base(air_by_region[region], type_name, nation_name, base_name)

    # --- Ground units (not loaded on a ship; end_of_day coords are always set) ---
    for record in _load_ground_unit_records(game_path, side):
        if _safe_int(record.get("record_id"), 0) == 0:
            continue
        if record.get("loaded_on_ship_id") is not None:
            continue
        x = _safe_int(record.get("end_of_day_x"), 0)
        y = _safe_int(record.get("end_of_day_y"), 0)
        type_name = str(record.get("unit_type_name") or "UNKNOWN").strip() or "UNKNOWN"
        nation_name = str(record.get("nation") or "UNKNOWN").strip() or "UNKNOWN"
        base_id = _safe_int(record.get("at_base_id"), 0)
        base_name = str(record.get("stationed_at_base_name") or "").strip()
        if not base_name and base_id > 0:
            base_name = base_names.get(base_id, f"Base {base_id}")
        if not base_name:
            base_name = "In Field"
        region = _classify_hex_region(x, y) or _OUTSIDE_THEATER
        _add_type_nation_base(ground_by_region[region], type_name, nation_name, base_name)

    def _format_type_breakdown(type_counts: dict[str, dict[str, dict[str, int]]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for type_name, nation_bases in type_counts.items():
            nations: list[dict[str, Any]] = []
            for nation, base_counts in sorted(nation_bases.items(), key=lambda kv: kv[0]):
                bases = [
                    {"base": base, "count": count}
                    for base, count in sorted(base_counts.items(), key=lambda kv: (-kv[1], kv[0]))
                ]
                nation_total = sum(base["count"] for base in bases)
                nations.append({"nation": nation, "count": nation_total, "bases": bases})

            nations.sort(key=lambda item: (-int(item["count"]), str(item["nation"])))
            total = sum(item["count"] for item in nations)
            items.append({"type": type_name, "count": total, "nations": nations})

        items.sort(key=lambda item: (-int(item["count"]), str(item["type"])))
        return items

    result_regions: list[dict[str, Any]] = []
    for region_name in region_order:
        ships = _format_type_breakdown(ships_by_region[region_name])
        air = _format_type_breakdown(air_by_region[region_name])
        ground = _format_type_breakdown(ground_by_region[region_name])
        if not ships and not air and not ground:
            continue
        result_regions.append(
            {
                "name": region_name,
                "abbr": region_abbr[region_name],
                "ships": ships,
                "airgroups": air,
                "ground": ground,
            }
        )

    return {"regions": result_regions}

