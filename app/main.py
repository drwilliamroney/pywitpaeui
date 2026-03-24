import logging
import os
import json
import re
import time
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from rich.logging import RichHandler

from app.map_assembly import MapAssembly
from app.overlay_renderer import OverlayRenderer
from app.overlay_svg import OverlaySvgRenderer
from app.overlays import (
    get_air_attack_range_overlay,
    get_airgroup_hq_link_overlay,
    get_air_mission_overlay,
    get_area_command_overlay,
    get_available_overlays,
    get_base_supply_overlay,
    get_hq_overlay,
    get_invasions_overlay,
    get_logistics_taskforces_overlay,
    get_minefields_overlay,
    get_planning_overlay,
    get_regions_overlay,
    get_shipyard_data,
    get_subpatrols_overlay,
    get_taskforces_overlay,
    get_threats_overlay,
    get_toe_data,
    get_unit_hq_link_overlay,
)
from app.turn_state import SaveTurnTracker, TurnState

DEFAULT_SIDE = "allies"
DEFAULT_GAME_PATH = r"C:\Matrix Games\War in the Pacific Admiral's Edition"
DEFAULT_PWSTOOL_PATH = str(Path(__file__).resolve().parent.parent / "deps" / "pywitpaescraper")
GAME_COLS = 232
GAME_ROWS = 205
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
MAP_PATH = STATIC_DIR / "map.png"

STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="UI")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

ORDERED_DATASETS = [
    "airgroups.json",
    "bases.json",
    "ground_units.json",
    "minefields.json",
    "ships.json",
    "taskforces.json",
    "threats.json",
]
ORDERED_DATASET_SET = set(ORDERED_DATASETS)
COMBAT_REPORT_FILE_NAME = "combatreport.txt"
COMBAT_SEPARATOR_PATTERN = re.compile(r"^-{20,}\s*$")
COMBAT_SHIP_CLASS_PATTERN = re.compile(r"^\s+(CVE|CVL|CV|BB|BC|CA)\b", re.MULTILINE)
COMBAT_COORDS_PATTERN = re.compile(r"\((\d+),(\d+)\)|at\s+(\d+),(\d+)", re.IGNORECASE)
COMBAT_CARRIER_AIRCRAFT_PATTERN = re.compile(
    r"\b(?:"
    r"A6M\d*S?|B5N\d*|B6N\d*|B7A\d*|D3A\d*|D4Y\d*|"
    r"F2A-\d+|F4F-\d+|F4U-\d+[A-Z]?|F6F-\d+|TBF-\d+|TBM-\d+|SBD-\d+|SB2C-\d+|"
    r"Sea\s+Hurricane|Fulmar|Albacore|Barracuda|Swordfish|Seafire|Martlet|"
    r"Avenger|Dauntless|Helldiver|Wildcat|Corsair|Hellcat|Kate|Val"
    r")\b",
    re.IGNORECASE,
)
COMBAT_TYPE_PRIORITY = {
    "Amphibious Invasion": 0,
    "Surface Action": 1,
    "Air Attack": 2,
    "Ground Combat": 3,
    "Pre-Invasion Action": 4,
}
COMBAT_CATEGORY_PRIORITY = {
    "amphibious": 0,
    "capital-ships": 1,
    "carrier-air": 2,
    "ground-base": 3,
    "heavy-cruiser": 4,
}


def configure_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True)],
    )
    return logging.getLogger("ui")


logger = configure_logging()
app.state.turn_trackers = {}
app.state.map_assembly = None
app.state.map_assembly_key = None
app.state.overlay_cache = {"json": {}, "svg": {}}
app.state.overlay_cache_context_key = ""
app.state.overlay_cache_generated_at = ""
app.state.overlay_cache_pwstool_run_at = ""
app.state.overlay_refresh_status = "not-started"
app.state.overlay_refresh_message = "Overlay cache not generated yet"
app.state.startup_pwstool_bootstrap_keys = set()


def normalize_side(value: str) -> str:
    normalized = (value or DEFAULT_SIDE).strip().lower()
    if normalized not in {"allies", "japan"}:
        logger.warning("Invalid side '%s'; defaulting to '%s'", value, DEFAULT_SIDE)
        return DEFAULT_SIDE
    return normalized


def _get_runtime_config() -> tuple[str, str, str]:
    selected_side = normalize_side(os.getenv("APP_SIDE", DEFAULT_SIDE))
    selected_game_path = os.getenv("APP_GAME_PATH", DEFAULT_GAME_PATH)
    selected_pwstool_path = os.getenv("APP_PWSTOOL_PATH", DEFAULT_PWSTOOL_PATH)
    return selected_side, selected_game_path, selected_pwstool_path


def _ensure_map(game_path: str) -> MapAssembly:
    assembly = MapAssembly(game_dir=Path(game_path))
    assembly.save(MAP_PATH)
    return assembly


def _get_map_assembly(game_path: str) -> MapAssembly:
    key = str(Path(game_path).resolve()).lower()
    cached_key: str | None = getattr(app.state, "map_assembly_key", None)
    cached_assembly: MapAssembly | None = getattr(app.state, "map_assembly", None)
    if cached_assembly is not None and cached_key == key:
        return cached_assembly

    assembly = _ensure_map(game_path)
    app.state.map_assembly = assembly
    app.state.map_assembly_key = key
    return assembly


def _render_version(assembly: MapAssembly) -> str:
    # Shared token for map + overlays so browser fetches a consistent generation.
    return f"{assembly.width}x{assembly.height}-{int(time.time() * 1000)}"


def _png_response(payload: bytes) -> Response:
    return Response(
        content=payload,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


def _svg_response(payload: str) -> Response:
    return Response(
        content=payload,
        media_type="image/svg+xml",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


def _overlay_source_signature(side: str, game_path: str) -> str:
    base_path = Path(game_path)
    side_key = "ALLIED" if normalize_side(side) == "allies" else "JAPAN"
    watched_paths = [
        base_path / "SAVE" / COMBAT_REPORT_FILE_NAME,
        base_path / "SAVE" / side_key / "threats.json",
        base_path / "SAVE" / side_key / "taskforces.json",
        base_path / "SAVE" / side_key / "airgroups.json",
        base_path / "SAVE" / side_key / "ground_units.json",
        base_path / "SAVE" / side_key / "bases.json",
        base_path / "SAVE" / side_key / "minefields.json",
    ]

    parts: list[str] = []
    for path in watched_paths:
        try:
            stat = path.stat()
            parts.append(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size}")
        except OSError:
            parts.append(f"{path.name}:missing")

    return "|".join(parts)


def _overlay_context_key(side: str, game_path: str) -> str:
    normalized_side = normalize_side(side)
    resolved_path = str(Path(game_path).resolve()).lower()
    source_signature = _overlay_source_signature(normalized_side, game_path)
    return f"{normalized_side}|{resolved_path}|{source_signature}"


def _build_overlay_cache_payloads(selected_side: str, selected_game_path: str) -> tuple[dict[str, Any], dict[str, str]]:
    assembly = _get_map_assembly(selected_game_path)
    renderer = OverlaySvgRenderer(Path(selected_game_path), assembly.width, assembly.height)

    json_payloads: dict[str, Any] = {
        "regions": get_regions_overlay(assembly.width, assembly.height),
        "invasions": get_invasions_overlay(selected_game_path, assembly.width, assembly.height),
        "taskforces": get_taskforces_overlay(selected_game_path, selected_side, assembly.width, assembly.height),
        "subpatrols": get_subpatrols_overlay(selected_game_path, selected_side, assembly.width, assembly.height),
        "threats": get_threats_overlay(selected_game_path, selected_side, assembly.width, assembly.height),
        "base-supply": get_base_supply_overlay(selected_game_path, selected_side, assembly.width, assembly.height),
        "logistics-taskforces": get_logistics_taskforces_overlay(selected_game_path, selected_side, assembly.width, assembly.height),
        "sea-hq": get_hq_overlay(selected_game_path, selected_side, assembly.width, assembly.height, "sea"),
        "sea-minefields": get_minefields_overlay(selected_game_path, selected_side, assembly.width, assembly.height),
        "air-hq": get_hq_overlay(selected_game_path, selected_side, assembly.width, assembly.height, "air"),
        "land-hq": get_hq_overlay(selected_game_path, selected_side, assembly.width, assembly.height, "land"),
        "land-unit-hq-link": get_unit_hq_link_overlay(selected_game_path, selected_side, assembly.width, assembly.height),
        "land-planning": get_planning_overlay(selected_game_path, selected_side, assembly.width, assembly.height),
        "air-area-command": get_area_command_overlay(selected_game_path, selected_side, assembly.width, assembly.height, "air"),
        "land-area-command": get_area_command_overlay(selected_game_path, selected_side, assembly.width, assembly.height, "land"),
        "air-search": get_air_mission_overlay(selected_game_path, selected_side, assembly.width, assembly.height, "search"),
        "air-asw": get_air_mission_overlay(selected_game_path, selected_side, assembly.width, assembly.height, "asw"),
        "air-attack": get_air_attack_range_overlay(selected_game_path, selected_side, assembly.width, assembly.height),
        "air-hq-link": get_airgroup_hq_link_overlay(selected_game_path, selected_side, assembly.width, assembly.height),
    }

    svg_payloads: dict[str, str] = {
        "regions": renderer.render_regions_svg(json_payloads["regions"]["features"]),
        "invasions": renderer.render_invasions_svg(json_payloads["invasions"]["features"]),
        "taskforces": renderer.render_taskforces_svg(json_payloads["taskforces"]["features"]),
        "subpatrols": renderer.render_subpatrols_svg(json_payloads["subpatrols"]["features"]),
        "threats": renderer.render_threats_svg(json_payloads["threats"]["features"]),
        "threats-sub": renderer.render_threats_svg({"sub": json_payloads["threats"]["features"].get("sub", []), "surface": [], "carrier": [], "areas": []}),
        "threats-surface": renderer.render_threats_svg({"sub": [], "surface": json_payloads["threats"]["features"].get("surface", []), "carrier": [], "areas": []}),
        "threats-carrier": renderer.render_threats_svg({"sub": [], "surface": [], "carrier": json_payloads["threats"]["features"].get("carrier", []), "areas": []}),
        "threats-areas": renderer.render_threats_svg({"sub": [], "surface": [], "carrier": [], "areas": json_payloads["threats"]["features"].get("areas", [])}),
        "base-supply": renderer.render_base_supply_svg(json_payloads["base-supply"]["features"]),
        "logistics-taskforces": renderer.render_taskforces_svg(json_payloads["logistics-taskforces"]["features"]),
        "sea-hq": renderer.render_hq_coverage_svg(json_payloads["sea-hq"]["features"]),
        "sea-minefields": renderer.render_minefields_svg(json_payloads["sea-minefields"]["features"]),
        "air-hq": renderer.render_hq_coverage_svg(json_payloads["air-hq"]["features"]),
        "land-hq": renderer.render_hq_coverage_svg(json_payloads["land-hq"]["features"]),
        "land-unit-hq-link": renderer.render_link_lines_svg(json_payloads["land-unit-hq-link"]["features"]),
        "land-planning": renderer.render_link_lines_svg(json_payloads["land-planning"]["features"]),
        "air-area-command": renderer.render_area_command_svg(json_payloads["air-area-command"]["features"]),
        "land-area-command": renderer.render_area_command_svg(json_payloads["land-area-command"]["features"]),
        "air-search": renderer.render_air_mission_sectors_svg(json_payloads["air-search"]["features"]),
        "air-asw": renderer.render_air_mission_sectors_svg(json_payloads["air-asw"]["features"]),
        "air-attack": renderer.render_air_attack_ranges_svg(json_payloads["air-attack"]["features"]),
        "air-hq-link": renderer.render_link_lines_svg(json_payloads["air-hq-link"]["features"]),
    }

    return json_payloads, svg_payloads


def _refresh_overlay_cache(selected_side: str, selected_game_path: str, reason: str, pwstool_run_at: str = "") -> bool:
    context_key = _overlay_context_key(selected_side, selected_game_path)
    app.state.overlay_refresh_status = "running"
    app.state.overlay_refresh_message = f"Generating overlay cache ({reason})..."

    try:
        json_payloads, svg_payloads = _build_overlay_cache_payloads(selected_side, selected_game_path)
    except Exception as error:
        app.state.overlay_refresh_status = "failed"
        app.state.overlay_refresh_message = f"Overlay cache refresh failed: {error}"
        logger.exception("Overlay cache generation failed")
        return False

    app.state.overlay_cache = {"json": json_payloads, "svg": svg_payloads}
    app.state.overlay_cache_context_key = context_key
    app.state.overlay_cache_generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    if pwstool_run_at:
        app.state.overlay_cache_pwstool_run_at = pwstool_run_at
    app.state.overlay_refresh_status = "success"
    app.state.overlay_refresh_message = f"Overlay cache refreshed ({reason})"
    return True


def _ensure_overlay_cache_for_context(selected_side: str, selected_game_path: str) -> None:
    context_key = _overlay_context_key(selected_side, selected_game_path)
    cache_context_key = str(getattr(app.state, "overlay_cache_context_key", ""))
    cache_json = getattr(app.state, "overlay_cache", {}).get("json", {})
    if cache_context_key != context_key or not cache_json:
        _refresh_overlay_cache(selected_side, selected_game_path, "initial-load")


def _refresh_overlay_cache_after_turn_if_needed(selected_side: str, selected_game_path: str, state: Any) -> None:
    _ensure_overlay_cache_for_context(selected_side, selected_game_path)

    pwstool_status = str(getattr(state, "pwstool_last_status", "") or "").lower()
    pwstool_run_at = str(getattr(state, "pwstool_last_run_at", "") or "").strip()
    if pwstool_status != "success" or not pwstool_run_at:
        return

    last_applied_run_at = str(getattr(app.state, "overlay_cache_pwstool_run_at", "") or "").strip()
    if pwstool_run_at != last_applied_run_at:
        _refresh_overlay_cache(selected_side, selected_game_path, "post-turn", pwstool_run_at=pwstool_run_at)


def _get_cached_overlay_json(overlay_key: str, selected_side: str, selected_game_path: str) -> dict[str, Any]:
    _ensure_overlay_cache_for_context(selected_side, selected_game_path)
    payload = getattr(app.state, "overlay_cache", {}).get("json", {}).get(overlay_key)
    if payload is not None:
        return payload
    _refresh_overlay_cache(selected_side, selected_game_path, f"cache-miss:{overlay_key}")
    payload = getattr(app.state, "overlay_cache", {}).get("json", {}).get(overlay_key)
    if payload is None:
        raise HTTPException(status_code=503, detail=f"Overlay cache unavailable for {overlay_key}")
    return payload


def _get_cached_overlay_svg(overlay_key: str, selected_side: str, selected_game_path: str) -> str:
    _ensure_overlay_cache_for_context(selected_side, selected_game_path)
    payload = getattr(app.state, "overlay_cache", {}).get("svg", {}).get(overlay_key)
    if payload is not None:
        return payload
    _refresh_overlay_cache(selected_side, selected_game_path, f"cache-miss:{overlay_key}")
    payload = getattr(app.state, "overlay_cache", {}).get("svg", {}).get(overlay_key)
    if payload is None:
        raise HTTPException(status_code=503, detail=f"Overlay SVG cache unavailable for {overlay_key}")
    return payload


def _get_turn_tracker(game_path: str) -> SaveTurnTracker:
    key = str(Path(game_path).resolve()).lower()
    trackers: dict[str, SaveTurnTracker] = app.state.turn_trackers
    if key not in trackers:
        trackers[key] = SaveTurnTracker(Path(game_path))
    return trackers[key]


def _startup_pwstool_bootstrap_key(side: str, game_path: str, pwstool_path: str) -> str:
    normalized_side = normalize_side(side)
    resolved_game_path = str(Path(game_path).resolve()).lower()
    resolved_pwstool_path = str(Path(pwstool_path).resolve()).lower()
    return f"{normalized_side}|{resolved_game_path}|{resolved_pwstool_path}"


def _ensure_startup_pwstool_bootstrap(
    selected_side: str,
    selected_game_path: str,
    selected_pwstool_path: str,
) -> TurnState:
    tracker = _get_turn_tracker(selected_game_path)
    state = tracker.update(selected_side, Path(selected_pwstool_path))

    bootstrap_key = _startup_pwstool_bootstrap_key(
        selected_side,
        selected_game_path,
        selected_pwstool_path,
    )
    bootstrap_keys: set[str] = app.state.startup_pwstool_bootstrap_keys
    if bootstrap_key in bootstrap_keys:
        return state

    bootstrap_keys.add(bootstrap_key)
    app.state.overlay_refresh_status = "running"
    app.state.overlay_refresh_message = "Bootstrapping scraper and rebuilding game data..."
    tracker._run_pwstool(selected_side, Path(selected_pwstool_path))
    state = tracker.state
    _refresh_overlay_cache_after_turn_if_needed(selected_side, selected_game_path, state)
    return state


def _side_folder_key(side: str) -> str:
    return "allied" if side == "allies" else "japan"


def _discover_data_files(side: str, game_path: str) -> list[tuple[str, Path]]:
    save_root = Path(game_path) / "SAVE"
    folder_key = _side_folder_key(side)
    folder_path = save_root / folder_key.upper()
    if not folder_path.exists():
        return []

    discovered: list[tuple[str, Path]] = []
    for file_name in ORDERED_DATASETS:
        json_path = folder_path / file_name
        if json_path.exists():
            discovered.append((folder_key, json_path))

    return discovered


def _tab_label(folder_key: str, file_name: str) -> str:
    stem = Path(file_name).stem.replace("_", " ").replace("-", " ").strip()
    title = " ".join(word.capitalize() for word in stem.split()) or file_name
    return title


def _build_nav_sections(
    side: str,
    game_path: str,
    active_section_id: str,
    active_item_id: str | None = None,
) -> list[dict[str, Any]]:
    debug_items: list[dict[str, str | bool]] = []
    for folder_key, json_path in _discover_data_files(side, game_path):
        file_name = json_path.name
        item_id = f"data:{folder_key}:{file_name.lower()}"
        debug_items.append(
            {
                "id": item_id,
                "label": _tab_label(folder_key, file_name),
                "href": f"/data/{file_name}",
                "active": active_item_id == item_id,
            }
        )

    debug_href = debug_items[0]["href"] if debug_items else "#"
    return [
        {
            "id": "map",
            "label": "Theater Map",
            "href": "/map",
            "active": active_section_id == "map",
            "children": [],
        },
        {
            "id": "combat",
            "label": "Combat",
            "href": "/combat",
            "active": active_section_id == "combat",
            "children": [],
        },
        {
            "id": "logistics",
            "label": "Logistics",
            "href": "/logistics",
            "active": active_section_id == "logistics",
            "children": [],
        },
        {
                "id": "sea",
                "label": "Sea",
                "href": "/sea",
                "active": active_section_id == "sea",
                "children": [],
            },
            {
                "id": "air",
                "label": "Air",
                "href": "/air",
                "active": active_section_id == "air",
                "children": [],
            },
            {
            "id": "land",
            "label": "Land",
            "href": "/land",
            "active": active_section_id == "land",
            "children": [],
        },
        {
            "id": "operations",
            "label": "Operations",
            "href": "/operations",
            "active": active_section_id == "operations",
            "children": [],
        },
        {
            "id": "toe",
            "label": "TOE",
            "href": "/toe",
            "active": active_section_id == "toe",
            "children": [],
        },
        {
            "id": "shipyard",
            "label": "Shipyard",
            "href": "/shipyard",
            "active": active_section_id == "shipyard",
            "children": [],
        },
        {
            "id": "debug",
            "label": "Debug",
            "href": debug_href,
            "active": active_section_id == "debug",
            "children": debug_items,
        },
    ]


def _render_map_page(
    request: Request,
    *,
    nav_section_id: str,
    map_mode: str,
):
    selected_side, selected_game_path, selected_pwstool_path = _get_runtime_config()
    assembly = _get_map_assembly(selected_game_path)
    state = _ensure_startup_pwstool_bootstrap(
        selected_side,
        selected_game_path,
        selected_pwstool_path,
    )

    _refresh_overlay_cache_after_turn_if_needed(selected_side, selected_game_path, state)

    request.app.state.map_assembly = assembly

    logger.info(
        "Rendering %s page for side='%s' game_path='%s' pwstool_path='%s'",
        map_mode,
        selected_side,
        selected_game_path,
        selected_pwstool_path,
    )

    return templates.TemplateResponse(
        request,
        "map.html",
        {
            "side": selected_side,
            "map_available": assembly.from_tiles,
            "turn_in_progress": state.turn_in_progress,
            "turn_completed_at": state.turn_completed_at,
            "pwstool_last_status": state.pwstool_last_status,
            "pwstool_last_message": state.pwstool_last_message,
            "overlay_refresh_status": app.state.overlay_refresh_status,
            "overlay_refresh_message": app.state.overlay_refresh_message,
            "game_date": state.game_date,
            "game_turn": state.game_turn,
            "scenario_name": state.scenario_name,
            "nav_sections": _build_nav_sections(selected_side, selected_game_path, nav_section_id),
            "render_version": _render_version(assembly),
            "map_mode": map_mode,
        },
    )


def _summarize_value(value: object, max_len: int = 120) -> str:
    if isinstance(value, (dict, list)):
        try:
            text = json.dumps(value, ensure_ascii=True)
        except TypeError:
            text = str(value)
    else:
        text = str(value)

    if len(text) <= max_len:
        return text
    return f"{text[:max_len]}..."


def _load_json_payload(path: Path) -> tuple[object, str]:
    raw_text = path.read_text(encoding="utf-8")

    try:
        return json.loads(raw_text), "json"
    except json.JSONDecodeError:
        pass

    records: list[object] = []
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if records:
        return records, "json-lines"

    raise ValueError("Unable to parse file as JSON content")


def _normalize_lookup_name(value: object) -> str:
    text = str(value or "").strip().upper()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text).split())


def _parse_combat_coords(title: str) -> str:
    match = COMBAT_COORDS_PATTERN.search(title)
    if not match:
        return ""
    groups = [value for value in match.groups() if value]
    if len(groups) >= 2:
        return f"{groups[0]},{groups[1]}"
    return ""


def _extract_combat_location(title: str) -> str:
    patterns = (
        r"^Amphibious Assault at (?P<name>.+?) \(\d+,\d+\)$",
        r"^Ground combat at (?P<name>.+?) \(\d+,\d+\)$",
        r"^Pre-Invasion action off (?P<name>.+?) \(\d+,\d+\)(?:.*)?$",
        r"^(?:Morning|Afternoon|Night|Day) Air attack on TF, near (?P<name>.+?) at \d+,\d+$",
        r"^(?:Morning|Afternoon|Night|Day) Air attack on (?P<name>.+?)\s*, at \d+,\d+(?:.*)?$",
    )
    for pattern in patterns:
        match = re.match(pattern, title)
        if match:
            return str(match.group("name") or "").strip()
    return ""


def _extract_focus_ship(report_text: str) -> str:
    lines = report_text.splitlines()
    in_ship_block = False
    for line in lines:
        stripped = line.strip()
        if stripped in {"Japanese Ships", "Allied Ships"}:
            in_ship_block = True
            continue
        if in_ship_block:
            if not stripped:
                continue
            if not line.startswith("      "):
                in_ship_block = False
                continue
            ship_line = stripped.split(",", 1)[0].strip()
            return ship_line
    return ""


def _load_side_base_names(side: str, game_path: str) -> set[str]:
    base_path = Path(game_path) / "SAVE" / _side_folder_key(side).upper() / "bases.json"
    if not base_path.exists():
        return set()

    try:
        payload, _ = _load_json_payload(base_path)
    except (OSError, ValueError):
        return set()

    records = _extract_list_of_objects(payload)
    if not records:
        return set()

    base_names: set[str] = set()
    for item in records:
        name = str(item.get("name") or item.get("base_name") or "").strip()
        if name:
            base_names.add(_normalize_lookup_name(name))
    return base_names


def _load_side_base_names_by_coords(side: str, game_path: str) -> dict[str, str]:
    base_path = Path(game_path) / "SAVE" / _side_folder_key(side).upper() / "bases.json"
    if not base_path.exists():
        return {}

    try:
        payload, _ = _load_json_payload(base_path)
    except (OSError, ValueError):
        return {}

    records = _extract_list_of_objects(payload)
    if not records:
        return {}

    base_names_by_coords: dict[str, str] = {}
    for item in records:
        name = str(item.get("name") or item.get("base_name") or "").strip()
        coords = _parse_hex_coords(item)
        if name and coords is not None:
            base_names_by_coords[f"{coords[0]},{coords[1]}"] = name
    return base_names_by_coords


def _parse_combat_report_sections(raw_text: str) -> tuple[str, list[dict[str, Any]]]:
    heading = ""
    sections: list[dict[str, Any]] = []
    chunk_lines: list[str] = []

    def flush_chunk() -> None:
        nonlocal heading, chunk_lines
        content = "\n".join(chunk_lines).strip()
        chunk_lines = []
        if not content:
            return

        title = next((line.strip() for line in content.splitlines() if line.strip()), "")
        if not title:
            return
        if title.upper().startswith("AFTER ACTION REPORTS FOR "):
            heading = title
            return

        sections.append(
            {
                "title": title,
                "location": _extract_combat_location(title),
                "coords": _parse_combat_coords(title),
                "focus_ship": _extract_focus_ship(content),
                "content": content,
            }
        )

    for raw_line in raw_text.splitlines():
        line = raw_line.rstrip("\r")
        if COMBAT_SEPARATOR_PATTERN.match(line.strip()):
            flush_chunk()
            continue
        chunk_lines.append(line)
    flush_chunk()
    return heading, sections


def _combat_type_label(title: str) -> str:
    if title.startswith("Amphibious Assault at "):
        return "Amphibious Invasion"
    if title.startswith("Ground combat at "):
        return "Ground Combat"
    if "Air attack" in title:
        return "Air Attack"
    if title.startswith("Pre-Invasion action off "):
        return "Pre-Invasion Action"
    return "Surface Action"


def _classify_combat_category(section: dict[str, Any], side_base_names: set[str]) -> str | None:
    title = str(section.get("title") or "")
    content = str(section.get("content") or "")
    normalized_location = _normalize_lookup_name(section.get("location") or "")
    ship_classes = {match.upper() for match in COMBAT_SHIP_CLASS_PATTERN.findall(content)}
    has_carrier_aircraft = "carrier aircraft" in content.lower() or COMBAT_CARRIER_AIRCRAFT_PATTERN.search(content) is not None

    if title.startswith("Amphibious Assault at "):
        return "amphibious"
    if ship_classes.intersection({"CV", "CVL", "CVE", "BB", "BC"}):
        return "capital-ships"
    if has_carrier_aircraft:
        return "carrier-air"
    if title.startswith("Ground combat at ") and (not side_base_names or normalized_location in side_base_names):
        return "ground-base"
    if "CA" in ship_classes:
        return "heavy-cruiser"
    return None


def _combat_where_label(section: dict[str, Any], base_names_by_coords: dict[str, str]) -> str:
    title = str(section.get("title") or "")
    location = str(section.get("location") or "").strip()
    coords = str(section.get("coords") or "").strip()
    focus_ship = str(section.get("focus_ship") or "").strip()
    base_name = str(base_names_by_coords.get(coords) or "").strip()

    if base_name:
        label = base_name
    elif " Air attack on TF" in f" {title}" and focus_ship:
        label = focus_ship
    elif location:
        label = location
    elif focus_ship:
        label = focus_ship
    else:
        label = title

    if coords:
        return f"{label}, {coords}"
    return label


def _summarize_group_types(reports: list[dict[str, Any]]) -> str:
    type_counts: dict[str, int] = {}
    for report in reports:
        type_label = str(report.get("type_label") or "Combat")
        type_counts[type_label] = type_counts.get(type_label, 0) + 1

    ordered_types = sorted(type_counts.items(), key=lambda item: (COMBAT_TYPE_PRIORITY.get(item[0], 99), item[0]))
    summary_parts: list[str] = []
    for type_label, count in ordered_types:
        if count > 1:
            summary_parts.append(f"{type_label} (x{count})")
        else:
            summary_parts.append(type_label)
    return ", ".join(summary_parts)


def _load_major_combat_report_view(side: str, game_path: str) -> dict[str, Any]:
    report_path = Path(game_path) / "SAVE" / COMBAT_REPORT_FILE_NAME
    if not report_path.exists():
        return {
            "report_path": report_path,
            "report_heading": "Combat report unavailable",
            "groups": [],
            "cards": [],
            "note": "No combatreport.txt file was found under SAVE.",
        }

    try:
        raw_text = report_path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        return {
            "report_path": report_path,
            "report_heading": "Combat report unavailable",
            "groups": [],
            "cards": [],
            "note": f"Failed to read combatreport.txt: {error}",
        }

    report_heading, sections = _parse_combat_report_sections(raw_text)
    side_base_names = _load_side_base_names(side, game_path)
    base_names_by_coords = _load_side_base_names_by_coords(side, game_path)
    groups_by_key: dict[str, dict[str, Any]] = {}
    original_group_order: list[str] = []

    for index, section in enumerate(sections):
        category_id = _classify_combat_category(section, side_base_names)
        if category_id is None:
            continue

        report = {
            "title": str(section.get("title") or ""),
            "type_label": _combat_type_label(str(section.get("title") or "")),
            "category_id": category_id,
            "content": str(section.get("content") or ""),
            "sequence": index,
        }

        group_key = _normalize_lookup_name(section.get("location") or section.get("focus_ship") or section.get("title") or "")
        coords = str(section.get("coords") or "")
        if coords:
            group_key = f"{group_key}|{coords}"

        if group_key not in groups_by_key:
            groups_by_key[group_key] = {
                "id": f"combat-group-{len(groups_by_key) + 1}",
                "where_label": _combat_where_label(section, base_names_by_coords),
                "reports": [],
                "top_priority": COMBAT_CATEGORY_PRIORITY[category_id],
                "first_sequence": index,
            }
            original_group_order.append(group_key)

        group = groups_by_key[group_key]
        group["reports"].append(report)
        group["top_priority"] = min(int(group["top_priority"]), COMBAT_CATEGORY_PRIORITY[category_id])

    groups: list[dict[str, Any]] = []
    for group_key in original_group_order:
        group = groups_by_key[group_key]
        reports = sorted(group["reports"], key=lambda item: (COMBAT_CATEGORY_PRIORITY[item["category_id"]], item["sequence"]))
        groups.append(
            {
                "id": group["id"],
                "where_label": group["where_label"],
                "type_summary": _summarize_group_types(reports),
                "reports": reports,
                "top_priority": group["top_priority"],
                "first_sequence": group["first_sequence"],
            }
        )

    groups.sort(key=lambda item: (item["top_priority"], item["first_sequence"], item["where_label"]))

    cards = [
        {"label": "Locations", "value": str(len(groups))},
        {"label": "Reports", "value": str(sum(len(group["reports"]) for group in groups))},
        {
            "label": "Amphibious Invasions",
            "value": str(sum(report["type_label"] == "Amphibious Invasion" for group in groups for report in group["reports"])),
        },
        {
            "label": "Air Attacks",
            "value": str(sum(report["type_label"] == "Air Attack" for group in groups for report in group["reports"])),
        },
        {
            "label": "Ground Combats",
            "value": str(sum(report["type_label"] == "Ground Combat" for group in groups for report in group["reports"])),
        },
    ]

    note = ""
    if not side_base_names:
        note = "Bases.json was unavailable for the selected side, so all ground combat entries are included."
    elif not groups:
        note = "No major combat reports matched the selected filters."

    return {
        "report_path": report_path,
        "report_heading": report_heading or "After Action Reports",
        "groups": groups,
        "cards": cards,
        "note": note,
    }


def _extract_list_of_objects(payload: object) -> list[dict[str, Any]] | None:
    if isinstance(payload, list) and payload and all(isinstance(item, dict) for item in payload):
        return payload

    if isinstance(payload, dict):
        for key in ("threats", "taskforces", "entries", "records", "items", "data"):
            candidate = payload.get(key)
            if isinstance(candidate, list) and candidate and all(isinstance(item, dict) for item in candidate):
                return candidate

    return None


def _to_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.lstrip("-").isdigit():
        return int(text)
    return None


def _fmt_float(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _parse_hex_coords(record: dict[str, Any]) -> tuple[int, int] | None:
    for key_x, key_y in (
        ("x", "y"),
        ("target_x", "target_y"),
        ("end_of_day_x", "end_of_day_y"),
        ("start_of_day_x", "start_of_day_y"),
    ):
        x = _to_int(record.get(key_x))
        y = _to_int(record.get(key_y))
        if x is not None and y is not None:
            return x, y

    raw_hex = record.get("hex")
    if raw_hex is not None:
        text = str(raw_hex).strip()
        if "," in text:
            left, right = text.split(",", 1)
            x = _to_int(left.strip())
            y = _to_int(right.strip())
            if x is not None and y is not None:
                return x, y

    return None


def _region_from_hex(x: int, y: int) -> str:
    if y < 55 and x < 88:
        return "CBI"
    if y < 55 and x >= 88:
        return "NOPAC"
    if 55 <= y <= GAME_ROWS and x < 40:
        return "IO"
    if 55 <= y < 79 and 40 <= x < 96:
        return "PHIL"
    if 79 <= y < 100 and 40 <= x < 96:
        return "NEI"
    if 100 <= y <= GAME_ROWS and 40 <= x < 106:
        return "SWPAC"
    if 55 <= y < 100 and x >= 96:
        return "CENPAC"
    if 100 <= y <= GAME_ROWS and x >= 106:
        return "SOPAC"
    return "OTHER"


def _append_count_metrics(metrics: list[dict[str, str]], prefix: str, counts: dict[str, int], top_n: int = 4) -> None:
    for key, count in sorted(counts.items(), key=lambda pair: pair[1], reverse=True)[:top_n]:
        metrics.append({"label": f"{prefix}: {key}", "value": str(count)})


def _build_custom_layout(file_name: str, payload: object) -> dict[str, Any]:
    stem = Path(file_name).stem.lower()
    records = _extract_list_of_objects(payload)
    cards: list[dict[str, str]] = []
    metrics: list[dict[str, str]] = []
    preferred_columns: list[str] = []
    note = ""

    if stem == "threats":
        preferred_columns = ["hex", "level", "type", "source", "detected_at", "name"]
        note = "Threat-oriented layout with quick severity summary."
        if records is not None:
            levels = [level for level in (_to_int(item.get("level")) for item in records) if level is not None]
            type_counts: dict[str, int] = {}
            source_counts: dict[str, int] = {}
            region_counts: dict[str, int] = {}

            for item in records:
                type_name = str(item.get("type") or "UNKNOWN").upper()
                type_counts[type_name] = type_counts.get(type_name, 0) + 1

                source_name = str(item.get("source") or "UNKNOWN").upper()
                source_counts[source_name] = source_counts.get(source_name, 0) + 1

                coords = _parse_hex_coords(item)
                if coords is not None:
                    region = _region_from_hex(coords[0], coords[1])
                    region_counts[region] = region_counts.get(region, 0) + 1

            avg_level = (sum(levels) / len(levels)) if levels else 0.0
            severe = sum(level >= 3 for level in levels)
            cards = [
                {"label": "Threat Rows", "value": str(len(records))},
                {"label": "High Threat (>=2)", "value": str(sum(level >= 2 for level in levels))},
                {"label": "Max Level", "value": str(max(levels) if levels else 0)},
            ]
            metrics = [
                {"label": "Avg Threat Level", "value": _fmt_float(avg_level)},
                {"label": "Severe Threats (>=3)", "value": str(severe)},
            ]
            _append_count_metrics(metrics, "Type", type_counts)
            _append_count_metrics(metrics, "Source", source_counts)
            _append_count_metrics(metrics, "Region", region_counts)

    elif stem == "taskforces":
        preferred_columns = ["flagship_name", "mission", "start_of_day_x", "start_of_day_y", "target_x", "target_y"]
        note = "Taskforce-focused layout with mission distribution."
        if records is not None:
            by_mission: dict[str, int] = {}
            region_counts: dict[str, int] = {}
            moving_count = 0
            total_leg_distance = 0.0

            for item in records:
                mission = str(item.get("mission") or "UNKNOWN").upper()
                by_mission[mission] = by_mission.get(mission, 0) + 1

                start_x = _to_int(item.get("start_of_day_x"))
                start_y = _to_int(item.get("start_of_day_y"))
                end_x = _to_int(item.get("end_of_day_x"))
                end_y = _to_int(item.get("end_of_day_y"))
                if None not in {start_x, start_y, end_x, end_y}:
                    dx = float(end_x - start_x)
                    dy = float(end_y - start_y)
                    leg = (dx * dx + dy * dy) ** 0.5
                    total_leg_distance += leg
                    if leg > 0:
                        moving_count += 1

                coords = _parse_hex_coords(item)
                if coords is not None:
                    region = _region_from_hex(coords[0], coords[1])
                    region_counts[region] = region_counts.get(region, 0) + 1

            top_mission = "-"
            if by_mission:
                top_mission = max(by_mission.items(), key=lambda pair: pair[1])[0]

            avg_leg = total_leg_distance / len(records) if records else 0.0
            subpatrol_count = by_mission.get("SUBPATROL", 0)

            cards = [
                {"label": "Taskforce Rows", "value": str(len(records))},
                {"label": "Mission Types", "value": str(len(by_mission))},
                {"label": "Top Mission", "value": top_mission},
            ]
            metrics = [
                {"label": "Moving This Phase", "value": str(moving_count)},
                {"label": "Avg Start->End Distance", "value": _fmt_float(avg_leg)},
                {"label": "SUBPATROL Missions", "value": str(subpatrol_count)},
            ]
            _append_count_metrics(metrics, "Mission", by_mission)
            _append_count_metrics(metrics, "Region", region_counts)

    elif stem.startswith("intel_cache"):
        preferred_columns = ["hex", "side", "kind", "spotted_at", "source", "confidence"]
        note = "Intel cache layout for quick reconnaissance review."
        if records is not None:
            kind_counts: dict[str, int] = {}
            source_counts: dict[str, int] = {}
            confidence_values: list[int] = []

            for item in records:
                kind = str(item.get("kind") or item.get("type") or "UNKNOWN").upper()
                kind_counts[kind] = kind_counts.get(kind, 0) + 1

                source = str(item.get("source") or "UNKNOWN").upper()
                source_counts[source] = source_counts.get(source, 0) + 1

                confidence = _to_int(item.get("confidence"))
                if confidence is not None:
                    confidence_values.append(confidence)

            avg_conf = (sum(confidence_values) / len(confidence_values)) if confidence_values else 0.0
            cards = [
                {"label": "Intel Rows", "value": str(len(records))},
                {"label": "Fields", "value": str(len(records[0].keys()) if records else 0)},
            ]
            metrics = [
                {"label": "Avg Confidence", "value": _fmt_float(avg_conf)},
                {"label": "Confidence Samples", "value": str(len(confidence_values))},
            ]
            _append_count_metrics(metrics, "Kind", kind_counts)
            _append_count_metrics(metrics, "Source", source_counts)

    elif stem == "airgroups":
        preferred_columns = [
            "name",
            "aircraft_type",
            "aircraft",
            "ready",
            "damaged",
            "pilot_experience",
            "mission",
            "base_name",
        ]
        note = "Airgroup readiness and pilot quality highlights."
        if records is not None:
            ready_vals = [_to_int(item.get("ready")) for item in records]
            damage_vals = [_to_int(item.get("damaged")) for item in records]
            exp_vals = [
                _to_int(item.get("pilot_experience") or item.get("experience") or item.get("avg_exp"))
                for item in records
            ]

            ready_total = sum(value for value in ready_vals if value is not None)
            damaged_total = sum(value for value in damage_vals if value is not None)
            exp_clean = [value for value in exp_vals if value is not None]

            mission_counts: dict[str, int] = {}
            type_counts: dict[str, int] = {}
            for item in records:
                mission = str(item.get("mission") or "UNKNOWN").upper()
                mission_counts[mission] = mission_counts.get(mission, 0) + 1
                aircraft_type = str(item.get("aircraft_type") or item.get("type") or "UNKNOWN").upper()
                type_counts[aircraft_type] = type_counts.get(aircraft_type, 0) + 1

            avg_exp = (sum(exp_clean) / len(exp_clean)) if exp_clean else 0.0
            cards = [
                {"label": "Airgroups", "value": str(len(records))},
                {"label": "Ready Aircraft", "value": str(ready_total)},
                {"label": "Damaged Aircraft", "value": str(damaged_total)},
            ]
            metrics = [
                {"label": "Avg Pilot Experience", "value": _fmt_float(avg_exp)},
                {"label": "Experience Samples", "value": str(len(exp_clean))},
            ]
            _append_count_metrics(metrics, "Mission", mission_counts)
            _append_count_metrics(metrics, "Aircraft Type", type_counts)

    elif stem == "bases":
        preferred_columns = [
            "name",
            "x",
            "y",
            "airfield",
            "port",
            "fort",
            "supply",
            "fuel",
            "owner",
        ]
        note = "Base infrastructure and logistics snapshot."
        if records is not None:
            air_vals = [_to_int(item.get("airfield") or item.get("airfield_size")) for item in records]
            port_vals = [_to_int(item.get("port") or item.get("port_size")) for item in records]
            supply_vals = [_to_int(item.get("supply")) for item in records]
            fuel_vals = [_to_int(item.get("fuel")) for item in records]

            air_clean = [value for value in air_vals if value is not None]
            port_clean = [value for value in port_vals if value is not None]
            supply_total = sum(value for value in supply_vals if value is not None)
            fuel_total = sum(value for value in fuel_vals if value is not None)

            region_counts: dict[str, int] = {}
            owner_counts: dict[str, int] = {}
            for item in records:
                owner = str(item.get("owner") or item.get("side") or "UNKNOWN").upper()
                owner_counts[owner] = owner_counts.get(owner, 0) + 1

                coords = _parse_hex_coords(item)
                if coords is not None:
                    region = _region_from_hex(coords[0], coords[1])
                    region_counts[region] = region_counts.get(region, 0) + 1

            cards = [
                {"label": "Bases", "value": str(len(records))},
                {"label": "Total Supply", "value": str(supply_total)},
                {"label": "Total Fuel", "value": str(fuel_total)},
            ]
            metrics = [
                {"label": "Avg Airfield Size", "value": _fmt_float((sum(air_clean) / len(air_clean)) if air_clean else 0.0)},
                {"label": "Avg Port Size", "value": _fmt_float((sum(port_clean) / len(port_clean)) if port_clean else 0.0)},
            ]
            _append_count_metrics(metrics, "Owner", owner_counts)
            _append_count_metrics(metrics, "Region", region_counts)

    elif stem == "ground_units":
        preferred_columns = ["unit_type_name", "name", "type", "x", "y", "fatigue", "disruption", "experience", "supply", "mode"]
        note = "Ground unit readiness and posture overview."
        if records is not None:
            fatigue_vals = [_to_int(item.get("fatigue")) for item in records]
            disrupt_vals = [_to_int(item.get("disruption")) for item in records]
            exp_vals = [_to_int(item.get("experience")) for item in records]

            fatigue_clean = [value for value in fatigue_vals if value is not None]
            disrupt_clean = [value for value in disrupt_vals if value is not None]
            exp_clean = [value for value in exp_vals if value is not None]

            mode_counts: dict[str, int] = {}
            type_counts: dict[str, int] = {}
            high_fatigue = 0
            for item in records:
                mode = str(item.get("mode") or item.get("status") or "UNKNOWN").upper()
                mode_counts[mode] = mode_counts.get(mode, 0) + 1
                unit_type = str(item.get("type") or "UNKNOWN").upper()
                type_counts[unit_type] = type_counts.get(unit_type, 0) + 1
                fatigue = _to_int(item.get("fatigue"))
                if fatigue is not None and fatigue >= 25:
                    high_fatigue += 1

            cards = [
                {"label": "Ground Units", "value": str(len(records))},
                {"label": "High Fatigue (>=25)", "value": str(high_fatigue)},
                {"label": "Mode Types", "value": str(len(mode_counts))},
            ]
            metrics = [
                {"label": "Avg Fatigue", "value": _fmt_float((sum(fatigue_clean) / len(fatigue_clean)) if fatigue_clean else 0.0)},
                {"label": "Avg Disruption", "value": _fmt_float((sum(disrupt_clean) / len(disrupt_clean)) if disrupt_clean else 0.0)},
                {"label": "Avg Experience", "value": _fmt_float((sum(exp_clean) / len(exp_clean)) if exp_clean else 0.0)},
            ]
            _append_count_metrics(metrics, "Mode", mode_counts)
            _append_count_metrics(metrics, "Unit Type", type_counts)

    elif stem == "ships":
        preferred_columns = [
            "name",
            "class",
            "ship_type",
            "x",
            "y",
            "system_damage",
            "flotation_damage",
            "fire_damage",
            "speed",
        ]
        note = "Fleet condition and ship-class composition snapshot."
        if records is not None:
            sys_vals = [_to_int(item.get("system_damage") or item.get("sys_damage")) for item in records]
            float_vals = [_to_int(item.get("flotation_damage") or item.get("float_damage")) for item in records]
            fire_vals = [_to_int(item.get("fire_damage")) for item in records]
            speed_vals = [_to_int(item.get("speed") or item.get("max_speed")) for item in records]

            sys_clean = [value for value in sys_vals if value is not None]
            float_clean = [value for value in float_vals if value is not None]
            fire_clean = [value for value in fire_vals if value is not None]
            speed_clean = [value for value in speed_vals if value is not None]

            type_counts: dict[str, int] = {}
            class_counts: dict[str, int] = {}
            damaged = 0
            for item in records:
                ship_type = str(item.get("ship_type") or item.get("type") or "UNKNOWN").upper()
                type_counts[ship_type] = type_counts.get(ship_type, 0) + 1
                ship_class = str(item.get("class") or "UNKNOWN").upper()
                class_counts[ship_class] = class_counts.get(ship_class, 0) + 1

                sys_dmg = _to_int(item.get("system_damage") or item.get("sys_damage")) or 0
                flt_dmg = _to_int(item.get("flotation_damage") or item.get("float_damage")) or 0
                fir_dmg = _to_int(item.get("fire_damage")) or 0
                if (sys_dmg + flt_dmg + fir_dmg) > 0:
                    damaged += 1

            cards = [
                {"label": "Ships", "value": str(len(records))},
                {"label": "Damaged Ships", "value": str(damaged)},
                {"label": "Ship Types", "value": str(len(type_counts))},
            ]
            metrics = [
                {"label": "Avg System Damage", "value": _fmt_float((sum(sys_clean) / len(sys_clean)) if sys_clean else 0.0)},
                {"label": "Avg Flotation Damage", "value": _fmt_float((sum(float_clean) / len(float_clean)) if float_clean else 0.0)},
                {"label": "Avg Fire Damage", "value": _fmt_float((sum(fire_clean) / len(fire_clean)) if fire_clean else 0.0)},
                {"label": "Avg Speed", "value": _fmt_float((sum(speed_clean) / len(speed_clean)) if speed_clean else 0.0)},
            ]
            _append_count_metrics(metrics, "Ship Type", type_counts)
            _append_count_metrics(metrics, "Class", class_counts)

    return {
        "records": records,
        "cards": cards,
        "metrics": metrics,
        "preferred_columns": preferred_columns,
        "note": note,
    }


def _build_data_view(payload: object, preferred_columns: Optional[list[str]] = None) -> dict[str, object]:
    if isinstance(payload, dict):
        rows = [{"key": key, "value": _summarize_value(value)} for key, value in payload.items()]
        return {
            "kind": "object",
            "size": len(payload),
            "object_rows": rows,
            "columns": [],
            "rows": [],
            "list_values": [],
        }

    if isinstance(payload, list):
        if payload and all(isinstance(item, dict) for item in payload):
            preferred = preferred_columns or []
            discovered: list[str] = []
            seen: set[str] = set()
            for item in payload:
                for key in item.keys():
                    if key not in seen:
                        seen.add(key)
                        discovered.append(str(key))

            columns = [col for col in preferred if col in seen]
            for column in discovered:
                if column not in columns:
                    columns.append(column)

            row_values: list[list[str]] = []
            for item in payload[:200]:
                row_values.append([_summarize_value(item.get(col, "")) for col in columns])

            return {
                "kind": "list-objects",
                "size": len(payload),
                "object_rows": [],
                "columns": columns,
                "rows": row_values,
                "list_values": [],
            }

        scalar_rows = [_summarize_value(item) for item in payload[:500]]
        return {
            "kind": "list-values",
            "size": len(payload),
            "object_rows": [],
            "columns": [],
            "rows": [],
            "list_values": scalar_rows,
        }

    return {
        "kind": "scalar",
        "size": 1,
        "object_rows": [],
        "columns": [],
        "rows": [],
        "list_values": [_summarize_value(payload)],
    }


@app.get("/")
def root():
    return RedirectResponse(
        url="/map",
        status_code=307,
    )


@app.get("/map")
def map_page(
    request: Request,
):
    return _render_map_page(request, nav_section_id="map", map_mode="theater")


@app.get("/logistics")
def logistics_page(
    request: Request,
):
    return _render_map_page(request, nav_section_id="logistics", map_mode="logistics")


@app.get("/combat")
def combat_page(
    request: Request,
):
    selected_side, selected_game_path, selected_pwstool_path = _get_runtime_config()
    state = _ensure_startup_pwstool_bootstrap(
        selected_side,
        selected_game_path,
        selected_pwstool_path,
    )

    _refresh_overlay_cache_after_turn_if_needed(selected_side, selected_game_path, state)
    combat_view = _load_major_combat_report_view(selected_side, selected_game_path)

    return templates.TemplateResponse(
        request,
        "combat.html",
        {
            "side": selected_side,
            "turn_in_progress": state.turn_in_progress,
            "turn_completed_at": state.turn_completed_at,
            "pwstool_last_status": state.pwstool_last_status,
            "pwstool_last_message": state.pwstool_last_message,
            "overlay_refresh_status": app.state.overlay_refresh_status,
            "overlay_refresh_message": app.state.overlay_refresh_message,
            "game_date": state.game_date,
            "game_turn": state.game_turn,
            "scenario_name": state.scenario_name,
            "nav_sections": _build_nav_sections(selected_side, selected_game_path, "combat"),
            "combat_heading": combat_view["report_heading"],
            "combat_report_path": str(combat_view["report_path"]),
            "combat_groups": combat_view["groups"],
            "combat_cards": combat_view["cards"],
            "combat_note": combat_view["note"],
        },
    )


@app.get("/sea")
def sea_page(
    request: Request,
):
    return _render_map_page(request, nav_section_id="sea", map_mode="sea")


@app.get("/land")
def land_page(
    request: Request,
):
    return _render_map_page(request, nav_section_id="land", map_mode="land")


@app.get("/air")
def air_page(
    request: Request,
):
    return _render_map_page(request, nav_section_id="air", map_mode="air")


@app.get("/operations")
def operations_page(
    request: Request,
):
    return _render_map_page(request, nav_section_id="operations", map_mode="operations")


@app.get("/toe")
def toe_page(request: Request):
    selected_side, selected_game_path, selected_pwstool_path = _get_runtime_config()
    state = _ensure_startup_pwstool_bootstrap(
        selected_side,
        selected_game_path,
        selected_pwstool_path,
    )

    _refresh_overlay_cache_after_turn_if_needed(selected_side, selected_game_path, state)
    toe = get_toe_data(selected_game_path, selected_side)
    return templates.TemplateResponse(
        request,
        "toe.html",
        {
            "side": selected_side,
            "turn_in_progress": state.turn_in_progress,
            "turn_completed_at": state.turn_completed_at,
            "pwstool_last_status": state.pwstool_last_status,
            "pwstool_last_message": state.pwstool_last_message,
            "overlay_refresh_status": app.state.overlay_refresh_status,
            "overlay_refresh_message": app.state.overlay_refresh_message,
            "game_date": state.game_date,
            "game_turn": state.game_turn,
            "scenario_name": state.scenario_name,
            "nav_sections": _build_nav_sections(selected_side, selected_game_path, "toe"),
            "regions": toe["regions"],
        },
    )


@app.get("/shipyard")
def shipyard_page(request: Request):
    selected_side, selected_game_path, selected_pwstool_path = _get_runtime_config()
    state = _ensure_startup_pwstool_bootstrap(
        selected_side,
        selected_game_path,
        selected_pwstool_path,
    )

    _refresh_overlay_cache_after_turn_if_needed(selected_side, selected_game_path, state)
    shipyard = get_shipyard_data(selected_game_path, selected_side)
    return templates.TemplateResponse(
        request,
        "shipyard.html",
        {
            "side": selected_side,
            "turn_in_progress": state.turn_in_progress,
            "turn_completed_at": state.turn_completed_at,
            "pwstool_last_status": state.pwstool_last_status,
            "pwstool_last_message": state.pwstool_last_message,
            "overlay_refresh_status": app.state.overlay_refresh_status,
            "overlay_refresh_message": app.state.overlay_refresh_message,
            "game_date": state.game_date,
            "game_turn": state.game_turn,
            "scenario_name": state.scenario_name,
            "nav_sections": _build_nav_sections(selected_side, selected_game_path, "shipyard"),
            "damaged_ships": shipyard["damaged_ships"],
            "shipyards": shipyard["shipyards"],
            "damaged_notice": shipyard.get("damaged_notice", ""),
        },
    )


@app.get("/data/{file_name}")
def data_page_for_side(
    request: Request,
    file_name: str,
):
    selected_side, selected_game_path, selected_pwstool_path = _get_runtime_config()
    state = _ensure_startup_pwstool_bootstrap(
        selected_side,
        selected_game_path,
        selected_pwstool_path,
    )
    _refresh_overlay_cache_after_turn_if_needed(selected_side, selected_game_path, state)
    folder_key = _side_folder_key(selected_side)

    return _render_data_page(
        request=request,
        selected_side=selected_side,
        selected_game_path=selected_game_path,
        selected_pwstool_path=selected_pwstool_path,
        folder_key=folder_key,
        file_name=file_name,
        state=state,
    )


def _render_data_page(
    request: Request,
    selected_side: str,
    selected_game_path: str,
    selected_pwstool_path: str,
    folder_key: str,
    file_name: str,
    state: Any,
):

    if Path(file_name).name != file_name or not file_name.lower().endswith(".json"):
        raise HTTPException(status_code=404, detail="Invalid file name")

    if file_name.lower() not in ORDERED_DATASET_SET:
        raise HTTPException(status_code=404, detail="Unsupported data file")

    save_dir = (Path(selected_game_path) / "SAVE" / folder_key.upper()).resolve()
    data_file = (save_dir / file_name).resolve()
    if data_file.parent != save_dir:
        raise HTTPException(status_code=404, detail="Invalid file path")
    if not data_file.exists():
        raise HTTPException(status_code=404, detail="Data file not found")

    try:
        payload, parse_mode = _load_json_payload(data_file)
    except (OSError, ValueError):
        payload, parse_mode = {"error": "Could not parse JSON content in this file."}, "unreadable"

    custom_layout = _build_custom_layout(file_name, payload)
    display_payload: object = custom_layout["records"] if custom_layout["records"] is not None else payload
    view = _build_data_view(display_payload, preferred_columns=custom_layout["preferred_columns"])
    try:
        raw_preview = json.dumps(payload, indent=2, ensure_ascii=True)
    except TypeError:
        raw_preview = str(payload)

    if len(raw_preview) > 12000:
        raw_preview = f"{raw_preview[:12000]}\n... (truncated)"

    active_tab_id = f"data:{folder_key}:{file_name.lower()}"
    return templates.TemplateResponse(
        request,
        "data.html",
        {
            "side": selected_side,
            "turn_in_progress": state.turn_in_progress,
            "turn_completed_at": state.turn_completed_at,
            "pwstool_last_status": state.pwstool_last_status,
            "pwstool_last_message": state.pwstool_last_message,
            "overlay_refresh_status": app.state.overlay_refresh_status,
            "overlay_refresh_message": app.state.overlay_refresh_message,
            "game_date": state.game_date,
            "game_turn": state.game_turn,
            "scenario_name": state.scenario_name,
            "nav_sections": _build_nav_sections(
                selected_side,
                selected_game_path,
                "debug",
                active_tab_id,
            ),
            "data_title": _tab_label(folder_key, file_name),
            "data_file": str(data_file),
            "parse_mode": parse_mode,
            "kind": view["kind"],
            "size": view["size"],
            "object_rows": view["object_rows"],
            "columns": view["columns"],
            "rows": view["rows"],
            "list_values": view["list_values"],
            "custom_cards": custom_layout["cards"],
            "custom_metrics": custom_layout["metrics"],
            "custom_note": custom_layout["note"],
            "raw_preview": raw_preview,
        },
    )


@app.get("/api/game-state", response_class=JSONResponse)
def api_game_state():
    selected_side, selected_game_path, selected_pwstool_path = _get_runtime_config()
    state = _ensure_startup_pwstool_bootstrap(
        selected_side,
        selected_game_path,
        selected_pwstool_path,
    )
    _refresh_overlay_cache_after_turn_if_needed(selected_side, selected_game_path, state)
    return JSONResponse(
        {
            "turn_in_progress": state.turn_in_progress,
            "turn_completed_at": state.turn_completed_at,
            "last_event": state.last_event,
            "pwstool_last_status": state.pwstool_last_status,
            "pwstool_last_message": state.pwstool_last_message,
            "pwstool_last_run_at": state.pwstool_last_run_at,
            "overlay_refresh_status": app.state.overlay_refresh_status,
            "overlay_refresh_message": app.state.overlay_refresh_message,
            "overlay_cache_generated_at": app.state.overlay_cache_generated_at,
            "game_date": state.game_date,
            "game_turn": state.game_turn,
            "scenario_name": state.scenario_name,
        }
    )


@app.get("/api/overlays", response_class=JSONResponse)
def api_overlays() -> JSONResponse:
    return JSONResponse(get_available_overlays())


@app.get("/api/overlays/regions", response_class=JSONResponse)
def api_regions_overlay() -> JSONResponse:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return JSONResponse(_get_cached_overlay_json("regions", selected_side, selected_game_path))


@app.get("/api/overlays/taskforces", response_class=JSONResponse)
def api_taskforces_overlay() -> JSONResponse:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return JSONResponse(_get_cached_overlay_json("taskforces", selected_side, selected_game_path))


@app.get("/api/overlays/invasions", response_class=JSONResponse)
def api_invasions_overlay() -> JSONResponse:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return JSONResponse(_get_cached_overlay_json("invasions", selected_side, selected_game_path))


@app.get("/api/overlays/subpatrols", response_class=JSONResponse)
def api_subpatrols_overlay() -> JSONResponse:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return JSONResponse(_get_cached_overlay_json("subpatrols", selected_side, selected_game_path))


@app.get("/api/overlays/threats", response_class=JSONResponse)
def api_threats_overlay() -> JSONResponse:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return JSONResponse(_get_cached_overlay_json("threats", selected_side, selected_game_path))


@app.get("/api/overlays/base-supply", response_class=JSONResponse)
def api_base_supply_overlay() -> JSONResponse:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return JSONResponse(_get_cached_overlay_json("base-supply", selected_side, selected_game_path))


@app.get("/api/overlays/logistics-taskforces", response_class=JSONResponse)
def api_logistics_taskforces_overlay() -> JSONResponse:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return JSONResponse(_get_cached_overlay_json("logistics-taskforces", selected_side, selected_game_path))


@app.get("/api/overlays/sea-hq", response_class=JSONResponse)
def api_sea_hq_overlay() -> JSONResponse:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return JSONResponse(_get_cached_overlay_json("sea-hq", selected_side, selected_game_path))


@app.get("/api/overlays/sea-minefields", response_class=JSONResponse)
def api_sea_minefields_overlay() -> JSONResponse:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return JSONResponse(_get_cached_overlay_json("sea-minefields", selected_side, selected_game_path))


@app.get("/api/overlays/air-hq", response_class=JSONResponse)
def api_air_hq_overlay() -> JSONResponse:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return JSONResponse(_get_cached_overlay_json("air-hq", selected_side, selected_game_path))


@app.get("/api/overlays/land-hq", response_class=JSONResponse)
def api_land_hq_overlay() -> JSONResponse:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return JSONResponse(_get_cached_overlay_json("land-hq", selected_side, selected_game_path))


@app.get("/api/overlays/air-area-command", response_class=JSONResponse)
def api_air_area_command_overlay() -> JSONResponse:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return JSONResponse(_get_cached_overlay_json("air-area-command", selected_side, selected_game_path))


@app.get("/api/overlays/air-search", response_class=JSONResponse)
def api_air_search_overlay() -> JSONResponse:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return JSONResponse(_get_cached_overlay_json("air-search", selected_side, selected_game_path))


@app.get("/api/overlays/air-asw", response_class=JSONResponse)
def api_air_asw_overlay() -> JSONResponse:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return JSONResponse(_get_cached_overlay_json("air-asw", selected_side, selected_game_path))


@app.get("/api/overlays/air-attack", response_class=JSONResponse)
def api_air_attack_overlay() -> JSONResponse:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return JSONResponse(_get_cached_overlay_json("air-attack", selected_side, selected_game_path))


@app.get("/api/overlays/air-hq-link", response_class=JSONResponse)
def api_air_hq_link_overlay() -> JSONResponse:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return JSONResponse(_get_cached_overlay_json("air-hq-link", selected_side, selected_game_path))


@app.get("/api/overlays/land-area-command", response_class=JSONResponse)
def api_land_area_command_overlay() -> JSONResponse:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return JSONResponse(_get_cached_overlay_json("land-area-command", selected_side, selected_game_path))


@app.get("/api/overlays/land-unit-hq-link", response_class=JSONResponse)
def api_land_unit_hq_link_overlay() -> JSONResponse:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return JSONResponse(_get_cached_overlay_json("land-unit-hq-link", selected_side, selected_game_path))


@app.get("/api/overlays/land-planning", response_class=JSONResponse)
def api_land_planning_overlay() -> JSONResponse:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return JSONResponse(_get_cached_overlay_json("land-planning", selected_side, selected_game_path))


@app.get("/api/overlays/regions.png")
def api_regions_overlay_png() -> Response:
    _selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    assembly = _get_map_assembly(selected_game_path)
    
    renderer = OverlayRenderer(Path(selected_game_path), assembly.width, assembly.height)
    regions_data = get_regions_overlay(assembly.width, assembly.height)["features"]
    
    img = renderer.render_regions(regions_data)
    
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return _png_response(buffer.getvalue())


@app.get("/api/overlays/taskforces.png")
def api_taskforces_overlay_png() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    assembly = _get_map_assembly(selected_game_path)
    
    renderer = OverlayRenderer(Path(selected_game_path), assembly.width, assembly.height)
    taskforces_data = get_taskforces_overlay(selected_game_path, selected_side, assembly.width, assembly.height)["features"]
    
    img = renderer.render_taskforces(taskforces_data)
    
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return _png_response(buffer.getvalue())


@app.get("/api/overlays/subpatrols.png")
def api_subpatrols_overlay_png() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    assembly = _get_map_assembly(selected_game_path)
    
    renderer = OverlayRenderer(Path(selected_game_path), assembly.width, assembly.height)
    subpatrols_data = get_subpatrols_overlay(selected_game_path, selected_side, assembly.width, assembly.height)["features"]
    
    img = renderer.render_subpatrols(subpatrols_data)
    
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return _png_response(buffer.getvalue())


@app.get("/api/overlays/threats.png")
def api_threats_overlay_png() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    assembly = _get_map_assembly(selected_game_path)
    
    renderer = OverlayRenderer(Path(selected_game_path), assembly.width, assembly.height)
    threats_data = get_threats_overlay(selected_game_path, selected_side, assembly.width, assembly.height)
    
    img = renderer.render_threats(threats_data["features"])
    
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return _png_response(buffer.getvalue())


@app.get("/api/overlays/regions.svg")
def api_regions_overlay_svg() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return _svg_response(_get_cached_overlay_svg("regions", selected_side, selected_game_path))


@app.get("/api/overlays/taskforces.svg")
def api_taskforces_overlay_svg() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return _svg_response(_get_cached_overlay_svg("taskforces", selected_side, selected_game_path))


@app.get("/api/overlays/invasions.svg")
def api_invasions_overlay_svg() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return _svg_response(_get_cached_overlay_svg("invasions", selected_side, selected_game_path))


@app.get("/api/overlays/subpatrols.svg")
def api_subpatrols_overlay_svg() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return _svg_response(_get_cached_overlay_svg("subpatrols", selected_side, selected_game_path))


@app.get("/api/overlays/threats.svg")
def api_threats_overlay_svg() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return _svg_response(_get_cached_overlay_svg("threats", selected_side, selected_game_path))


@app.get("/api/overlays/threats-sub.svg")
def api_threats_sub_overlay_svg() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return _svg_response(_get_cached_overlay_svg("threats-sub", selected_side, selected_game_path))


@app.get("/api/overlays/threats-surface.svg")
def api_threats_surface_overlay_svg() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return _svg_response(_get_cached_overlay_svg("threats-surface", selected_side, selected_game_path))


@app.get("/api/overlays/threats-carrier.svg")
def api_threats_carrier_overlay_svg() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return _svg_response(_get_cached_overlay_svg("threats-carrier", selected_side, selected_game_path))


@app.get("/api/overlays/threats-areas.svg")
def api_threats_areas_overlay_svg() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return _svg_response(_get_cached_overlay_svg("threats-areas", selected_side, selected_game_path))


@app.get("/api/overlays/base-supply.svg")
def api_base_supply_overlay_svg() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return _svg_response(_get_cached_overlay_svg("base-supply", selected_side, selected_game_path))


@app.get("/api/overlays/logistics-taskforces.svg")
def api_logistics_taskforces_overlay_svg() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return _svg_response(_get_cached_overlay_svg("logistics-taskforces", selected_side, selected_game_path))


@app.get("/api/overlays/sea-hq.svg")
def api_sea_hq_overlay_svg() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return _svg_response(_get_cached_overlay_svg("sea-hq", selected_side, selected_game_path))


@app.get("/api/overlays/sea-minefields.svg")
def api_sea_minefields_overlay_svg() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return _svg_response(_get_cached_overlay_svg("sea-minefields", selected_side, selected_game_path))


@app.get("/api/overlays/air-hq.svg")
def api_air_hq_overlay_svg() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return _svg_response(_get_cached_overlay_svg("air-hq", selected_side, selected_game_path))


@app.get("/api/overlays/land-hq.svg")
def api_land_hq_overlay_svg() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return _svg_response(_get_cached_overlay_svg("land-hq", selected_side, selected_game_path))


@app.get("/api/overlays/air-area-command.svg")
def api_air_area_command_overlay_svg() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return _svg_response(_get_cached_overlay_svg("air-area-command", selected_side, selected_game_path))


@app.get("/api/overlays/air-search.svg")
def api_air_search_overlay_svg() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return _svg_response(_get_cached_overlay_svg("air-search", selected_side, selected_game_path))


@app.get("/api/overlays/air-asw.svg")
def api_air_asw_overlay_svg() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return _svg_response(_get_cached_overlay_svg("air-asw", selected_side, selected_game_path))


@app.get("/api/overlays/air-attack.svg")
def api_air_attack_overlay_svg() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return _svg_response(_get_cached_overlay_svg("air-attack", selected_side, selected_game_path))


@app.get("/api/overlays/air-hq-link.svg")
def api_air_hq_link_overlay_svg() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return _svg_response(_get_cached_overlay_svg("air-hq-link", selected_side, selected_game_path))


@app.get("/api/overlays/land-area-command.svg")
def api_land_area_command_overlay_svg() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return _svg_response(_get_cached_overlay_svg("land-area-command", selected_side, selected_game_path))


@app.get("/api/overlays/land-unit-hq-link.svg")
def api_land_unit_hq_link_overlay_svg() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return _svg_response(_get_cached_overlay_svg("land-unit-hq-link", selected_side, selected_game_path))


@app.get("/api/overlays/land-planning.svg")
def api_land_planning_overlay_svg() -> Response:
    selected_side, selected_game_path, _selected_pwstool_path = _get_runtime_config()
    return _svg_response(_get_cached_overlay_svg("land-planning", selected_side, selected_game_path))
