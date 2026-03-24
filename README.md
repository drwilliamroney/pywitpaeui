> **AI First Research Notice**
> This repository — along with its companion [PyWITPAEScraper](https://github.com/drwilliamroney/pywitpaescraper) — is **100% built by GitHub Copilot** and constitutes a scientific test of the emerging *AI First* working pattern for software developers, conducted by **William N. Roney, ScD**. No human-authored source code was written; all design decisions, implementations, refactors, and repository operations were performed through natural-language instruction to the AI agent.

# PyWITPAEUI

A local web UI for [War in the Pacific: Admiral's Edition](https://www.matrixgames.com/game/war-in-the-pacific-admirals-edition).
Reads JSON exports produced by [PyWITPAEScraper](https://github.com/drwilliamroney/pywitpaescraper)
and presents them as interactive map overlays, data tables, and summary views — one side at a time.

## Requirements

- **Python 3.10+** (64-bit, standard interpreter)
- **Git** — to clone/update the PyWITPAEScraper dependency at startup
- **War in the Pacific: Admiral's Edition** installed locally
- A 32-bit Python 3 interpreter for the scraper (handled automatically by `bootstrap_scraper.bat` / `run_scraper.bat`)

## Quick Start

```bat
run_ui.bat
```

That's it. The script will:

1. Create a `.venv` virtual environment (64-bit Python) if one does not exist.
2. Install Python dependencies from `requirements.txt`.
3. Ensure `deps/pywitpaescraper` exists: clone it on first run, otherwise `git pull --ff-only origin main`.
4. Run scraper bootstrap to prepare 32-bit Python runtime/dependencies.
5. Prompt for two values then launch the server.

### Prompts

| Prompt | Default | Description |
|---|---|---|
| `Run as [allies/japan]` | `allies` | Which side's data to load |
| `Game save directory path` | `C:\Matrix Games\War in the Pacific Admiral's Edition` | Root installation/save directory |

The scraper path is resolved automatically to `deps\pywitpaescraper` — no manual input required.

Once running, the UI opens automatically at **http://127.0.0.1:8080/**.

### Environment Variables (advanced / CI)

You can bypass the interactive prompts by setting these before launching:

| Variable | Description |
|---|---|
| `APP_SIDE` | `allies` or `japan` |
| `APP_GAME_PATH` | Path to the game installation / save directory |
| `APP_PWSTOOL_PATH` | Path to the PyWITPAEScraper directory (defaults to `deps/pywitpaescraper`) |

## Views

### Theater Map (`/map`)
Full Pacific theater map assembled from the game's tile art (falls back to a placeholder if tiles are not found). Interactive SVG overlays can be toggled on or off:

- **Theater regions** — named operational zones (North Pacific, Central Pacific, South Pacific, etc.)
- **Task forces** — movement lines showing start → end → target positions
- **Invasions** — amphibious assault vectors
- **Submarine patrols** — sub patrol areas
- **Threat hexes** — threat-level heat map

### Logistics (`/logistics`)
Map mode focused on supply. Shows base supply health (healthy / strained / low) and logistics task force routes.

### Combat (`/combat`)
Summary of the most recent combat report: battle groups, engagement types, losses, and computed metrics. Monitors save-file timestamps and re-runs the scraper automatically when a new turn is detected.

### Sea (`/sea`)
Map overlays for naval operations: HQ linkages, task force positions, and sea-area command boundaries.

### Land (`/land`)
Map overlays for ground operations: ground unit positions, land-area command boundaries, and HQ links.

### Air (`/air`)
Map overlays for air operations: air group positions, search/ASW mission arcs, attack ranges, and HQ links.

### Operations (`/operations`)
Combined operational planning overlay: task forces, invasions, patrols, threats, and area commands in one view.

### Table of Equipment — TOE (`/toe`)
Collapsible breakdown of ships, air groups, and ground units organised by theater region and unit type.

### Shipyard (`/shipyard`)
Repair status dashboard: lists damaged ships by base, classifies them as in-shipyard, under repair ship, or at pier, and shows repair capacity versus demand per port.

### Debug Data (`/data/<file>.json`)
Raw JSON viewer for each exported dataset (ships, bases, airgroups, ground_units, taskforces, threats). Includes computed aggregate metrics, filterable row tables, and auto-refresh.

## Project Structure

```
run_ui.bat                  # Entry point — bootstraps venv and launches server
requirements.txt            # Python dependencies (FastAPI, Uvicorn, Pillow, etc.)
app/
  main.py                   # FastAPI application and all route handlers
  overlays.py               # Overlay data builders (regions, threats, task forces, …)
  overlay_svg.py            # SVG overlay renderer
  overlay_renderer.py       # PIL/Pillow overlay renderer (PNG fallback)
  map_assembly.py           # Assembles base map from game tile BMPs
  coordinate_transform.py   # Game-hex ↔ pixel coordinate transform
  turn_state.py             # Save-file watcher; triggers scraper on new turns
  game_data.py              # Shared game data helpers
  static/                   # Served static files (generated map.png lives here)
templates/                  # Jinja2 HTML templates
tests/
  test_ui_app.py            # Integration tests (FastAPI TestClient)
deps/
  pywitpaescraper/          # Runtime-managed git clone of the save-file scraper
```

## Dependency: PyWITPAEScraper

The scraper lives at `deps/pywitpaescraper` as a runtime-managed Git clone.
`run_ui.bat` keeps it on `origin/main` (clone on first run, then pull latest on startup).

The scraper requires a **32-bit Python 3** interpreter because it loads a 32-bit Windows DLL.
`deps/pywitpaescraper/bootstrap_scraper.bat` prepares that runtime and `run_scraper.bat`
uses it to execute the exporter.
