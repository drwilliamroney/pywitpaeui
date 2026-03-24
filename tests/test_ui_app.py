from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.overlays import get_shipyard_data


class UITests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self._old_pwstool_python = os.environ.get("APP_PWSTOOL_PYTHON")
        os.environ["APP_PWSTOOL_PYTHON"] = sys.executable
        self._runtime_env_keys = ("APP_GAME_PATH", "APP_PWSTOOL_PATH", "APP_SIDE")
        self._old_runtime_env = {key: os.environ.get(key) for key in self._runtime_env_keys}
        self._isolated_runtime_dir = tempfile.TemporaryDirectory()
        self._set_runtime_env(Path(self._isolated_runtime_dir.name))

        app.state.turn_trackers = {}
        app.state.map_assembly = None
        app.state.map_assembly_key = None
        app.state.overlay_cache = {"json": {}, "svg": {}}
        app.state.overlay_cache_context_key = ""
        app.state.overlay_cache_generated_at = ""
        app.state.overlay_cache_pwstool_run_at = ""
        app.state.overlay_refresh_status = "not-started"
        app.state.overlay_refresh_message = "Overlay cache not generated yet"

        original_get = self.client.get

        def get_with_runtime_env(url: str, *args, **kwargs):
            params = kwargs.get("params") or {}
            game_path = params.get("game_path")
            if game_path:
                self._set_runtime_env(
                    Path(str(game_path)),
                    side=str(params.get("side") or "allies"),
                    pwstool_path=Path(str(params.get("pwstool_path"))) if params.get("pwstool_path") else None,
                )
            return original_get(url, *args, **kwargs)

        self.client.get = get_with_runtime_env

    def tearDown(self) -> None:
        self._isolated_runtime_dir.cleanup()

        if self._old_pwstool_python is None:
            os.environ.pop("APP_PWSTOOL_PYTHON", None)
        else:
            os.environ["APP_PWSTOOL_PYTHON"] = self._old_pwstool_python

        for key, value in self._old_runtime_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _set_runtime_env(self, game_dir: Path, side: str = "allies", pwstool_path: Path | None = None) -> None:
        os.environ["APP_GAME_PATH"] = str(game_dir)
        os.environ["APP_PWSTOOL_PATH"] = str(pwstool_path or (game_dir / "missing_tool"))
        os.environ["APP_SIDE"] = side

    def test_map_page_renders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            (game_dir / "SAVE").mkdir(parents=True, exist_ok=True)
            response = self.client.get(
                "/map",
                params={
                    "game_path": str(game_dir),
                    "pwstool_path": str(game_dir / "missing_tool"),
                    "side": "allies",
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("Theater Map", response.text)
            assert 'id="overlayLegend"' in response.text

    def test_map_page_lists_json_tabs_excluding_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            allied_dir = game_dir / "SAVE" / "ALLIED"
            japan_dir = game_dir / "SAVE" / "JAPAN"
            allied_dir.mkdir(parents=True, exist_ok=True)
            japan_dir.mkdir(parents=True, exist_ok=True)

            (allied_dir / "airgroups.json").write_text('{"rows": []}', encoding="utf-8")
            (allied_dir / "bases.json").write_text('{"rows": []}', encoding="utf-8")
            (allied_dir / "ground_units.json").write_text('{"rows": []}', encoding="utf-8")
            (allied_dir / "ships.json").write_text('{"rows": []}', encoding="utf-8")
            (allied_dir / "taskforces.json").write_text('{"rows": []}', encoding="utf-8")
            (allied_dir / "threats.json").write_text('{"threats": []}', encoding="utf-8")
            (allied_dir / "turn.json").write_text('{"game_turn": 1}', encoding="utf-8")
            (japan_dir / "intel_cache.json").write_text('{"entries": []}', encoding="utf-8")
            (japan_dir / "threats.json").write_text('{"threats": [{"level": 3}]}', encoding="utf-8")

            response = self.client.get(
                "/map",
                params={
                    "game_path": str(game_dir),
                    "pwstool_path": str(game_dir / "missing_tool"),
                    "side": "allies",
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("Debug", response.text)
            self.assertNotIn("Intel Cache", response.text)
            self.assertIn("/data/airgroups.json", response.text)
            self.assertNotIn("/data/turn.json", response.text)

    def test_data_page_renders_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            allied_dir = game_dir / "SAVE" / "ALLIED"
            allied_dir.mkdir(parents=True, exist_ok=True)
            (allied_dir / "threats.json").write_text(
                '{"updated_at": "1941-12-08", "threats": [{"hex": "101,77", "level": 2}]}',
                encoding="utf-8",
            )

            response = self.client.get(
                "/data/threats.json",
                params={
                    "game_path": str(game_dir),
                    "pwstool_path": str(game_dir / "missing_tool"),
                    "side": "allies",
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("Threats", response.text)
            self.assertIn("updated_at", response.text)
            self.assertIn("threats", response.text)
            self.assertIn("Auto Refresh", response.text)
            self.assertIn("Filter Rows", response.text)
            self.assertIn("Threat Rows", response.text)
            self.assertIn("Computed Metrics", response.text)
            self.assertIn("Avg Threat Level", response.text)

    def test_tuned_metrics_for_core_datasets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            allied_dir = game_dir / "SAVE" / "ALLIED"
            allied_dir.mkdir(parents=True, exist_ok=True)

            (allied_dir / "airgroups.json").write_text(
                '{"records": [{"name": "1st FG", "aircraft_type": "F4F", "ready": 12, "damaged": 2, "pilot_experience": 68, "mission": "CAP"}]}',
                encoding="utf-8",
            )
            (allied_dir / "bases.json").write_text(
                '{"records": [{"name": "Pearl", "x": 120, "y": 65, "airfield": 7, "port": 9, "supply": 50000, "fuel": 70000, "owner": "ALLIES"}]}',
                encoding="utf-8",
            )
            (allied_dir / "ground_units.json").write_text(
                '{"records": [{"name": "27th Div", "type": "INF", "x": 60, "y": 80, "fatigue": 18, "disruption": 9, "experience": 62, "mode": "COMBAT"}]}',
                encoding="utf-8",
            )
            (allied_dir / "ships.json").write_text(
                '{"records": [{"name": "USS Example", "ship_type": "CA", "class": "NEW ORLEANS", "x": 110, "y": 70, "system_damage": 3, "flotation_damage": 1, "fire_damage": 0, "speed": 28}]}',
                encoding="utf-8",
            )

            airgroups_response = self.client.get(
                "/data/airgroups.json",
                params={
                    "game_path": str(game_dir),
                    "pwstool_path": str(game_dir / "missing_tool"),
                    "side": "allies",
                },
            )
            self.assertEqual(airgroups_response.status_code, 200)
            self.assertIn("Avg Pilot Experience", airgroups_response.text)

            bases_response = self.client.get(
                "/data/bases.json",
                params={
                    "game_path": str(game_dir),
                    "pwstool_path": str(game_dir / "missing_tool"),
                    "side": "allies",
                },
            )
            self.assertEqual(bases_response.status_code, 200)
            self.assertIn("Avg Airfield Size", bases_response.text)

            ground_response = self.client.get(
                "/data/ground_units.json",
                params={
                    "game_path": str(game_dir),
                    "pwstool_path": str(game_dir / "missing_tool"),
                    "side": "allies",
                },
            )
            self.assertEqual(ground_response.status_code, 200)
            self.assertIn("Avg Fatigue", ground_response.text)

            ships_response = self.client.get(
                "/data/ships.json",
                params={
                    "game_path": str(game_dir),
                    "pwstool_path": str(game_dir / "missing_tool"),
                    "side": "allies",
                },
            )
            self.assertEqual(ships_response.status_code, 200)
            self.assertIn("Avg System Damage", ships_response.text)

    def test_shipyard_data_classifies_repair_states_and_alt_damage_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            allied_dir = game_dir / "SAVE" / "ALLIED"
            allied_dir.mkdir(parents=True, exist_ok=True)

            (allied_dir / "bases.json").write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "record_id": 1,
                                "name": "Pearl Harbor",
                                "devices": {"Shipyard": 15},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            (allied_dir / "taskforces.json").write_text('{"records": []}', encoding="utf-8")

            (allied_dir / "ships.json").write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "record_id": 101,
                                "name": "Detroit",
                                "ship_class_type_name": "CL",
                                "stationed_at_base_id": 1,
                                "state": "readiness",
                                "system_damage": 10,
                                "flotation_damage": 0,
                                "engine_damage": 0,
                                "fire_damage": 0,
                                "tonnage": 7000,
                            },
                            {
                                "record_id": 102,
                                "name": "Raleigh",
                                "ship_class_type_name": "CL",
                                "stationed_at_base_id": 1,
                                "current_state": "pier",
                                "Sys": 4,
                                "Flt": 1,
                                "Eng": 0,
                                "Fire": 0,
                                "tonnage": 7250,
                            },
                            {
                                "record_id": 103,
                                "name": "Pennsylvania",
                                "ship_class_type_name": "BB",
                                "stationed_at_base_id": 1,
                                "current_state": "shipyard",
                                "Sys": 20,
                                "Flt": 10,
                                "Eng": 3,
                                "Fire": 0,
                                "tonnage": 31400,
                            },
                            {
                                "record_id": 104,
                                "name": "Helena",
                                "ship_class_type_name": "CL",
                                "stationed_at_base_id": 1,
                                "current_state": "repair ship",
                                "sys_damage": 8,
                                "float_damage": 2,
                                "eng_damage": 1,
                                "fire_damage": 0,
                                "tonnage": 9500,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            shipyard = get_shipyard_data(str(game_dir), "allies")

            damaged = shipyard["damaged_ships"]
            self.assertEqual(len(damaged), 1)
            self.assertEqual(damaged[0]["location"], "Pearl Harbor")
            self.assertEqual(damaged[0]["count"], 2)
            self.assertEqual([ship["name"] for ship in damaged[0]["ships"]], ["Raleigh", "Detroit"])
            self.assertEqual(damaged[0]["ships"][0]["state"], "pier")
            self.assertEqual(damaged[0]["ships"][0]["sys_damage"], 4)
            self.assertEqual(damaged[0]["ships"][0]["flt_damage"], 1)
            self.assertEqual(damaged[0]["ships"][0]["eng_damage"], 0)
            self.assertEqual(damaged[0]["ships"][0]["fire_damage"], 0)

            yards = shipyard["shipyards"]
            self.assertEqual(len(yards), 1)
            self.assertEqual(yards[0]["base"], "Pearl Harbor")
            self.assertEqual(yards[0]["in_repair_count"], 2)
            self.assertEqual(yards[0]["in_repair_tonnage"], 40900)
            self.assertEqual([ship["name"] for ship in yards[0]["ships"]], ["Pennsylvania", "Helena"])
            self.assertEqual(yards[0]["ships"][1]["state"], "repair ship")
            self.assertEqual(yards[0]["ships"][1]["sys_damage"], 8)
            self.assertEqual(yards[0]["ships"][1]["flt_damage"], 2)
            self.assertEqual(yards[0]["ships"][1]["eng_damage"], 1)
            self.assertEqual(yards[0]["ships"][1]["fire_damage"], 0)

    def test_shipyard_data_formats_at_sea_taskforce_name_with_tf_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            allied_dir = game_dir / "SAVE" / "ALLIED"
            allied_dir.mkdir(parents=True, exist_ok=True)

            (allied_dir / "bases.json").write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "record_id": 1,
                                "name": "Pearl Harbor",
                                "ship_repair": 30,
                                "ship_repair_capacity_tons": 30000,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (allied_dir / "taskforces.json").write_text(
                json.dumps({"records": [{"record_id": 405, "flagship_name": "Lexington"}]}),
                encoding="utf-8",
            )
            (allied_dir / "ships.json").write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "record_id": 3005,
                                "name": "Enterprise",
                                "ship_class_type_name": "CV",
                                "task_force_id": 405,
                                "stationed_at_base_id": 1,
                                "Sys": 5,
                                "Flt": 0,
                                "Eng": 0,
                                "Fire": 0,
                                "tonnage": 19875,
                                "current_state": "readiness",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            shipyard = get_shipyard_data(str(game_dir), "allies")
            damaged = shipyard["damaged_ships"]
            self.assertEqual(len(damaged), 1)
            self.assertEqual(damaged[0]["location"], "At Sea")
            self.assertEqual(damaged[0]["name"], "Lexington (TF405)")

    def test_shipyard_data_uses_ship_repair_capacity_tons_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            allied_dir = game_dir / "SAVE" / "ALLIED"
            allied_dir.mkdir(parents=True, exist_ok=True)

            (allied_dir / "bases.json").write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "record_id": 1,
                                "name": "Pearl Harbor",
                                "ship_repair": 30,
                                "ship_repair_capacity_tons": 30000,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (allied_dir / "taskforces.json").write_text('{"records": []}', encoding="utf-8")
            (allied_dir / "ships.json").write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "record_id": 100,
                                "name": "USS Test",
                                "ship_class_type_name": "DD",
                                "stationed_at_base_id": 1,
                                "task_force_id": 0,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            shipyard = get_shipyard_data(str(game_dir), "allies")
            self.assertEqual(len(shipyard["shipyards"]), 1)
            self.assertEqual(shipyard["shipyards"][0]["base"], "Pearl Harbor")
            self.assertEqual(shipyard["shipyards"][0]["tonnage"], 30000)
            self.assertIn("No ship damage fields were found", shipyard["damaged_notice"])

    def test_combat_page_groups_reports_by_location_and_orders_by_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            save_dir = game_dir / "SAVE"
            allied_dir = save_dir / "ALLIED"
            allied_dir.mkdir(parents=True, exist_ok=True)

            self._set_runtime_env(game_dir)

            (allied_dir / "bases.json").write_text(
                json.dumps(
                    {
                        "records": [
                            {"name": "Pearl Harbor"},
                            {"name": "Midway Island"},
                            {"name": "Hilo"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            (save_dir / "combatreport.txt").write_text(
                "\n".join(
                    [
                        "AFTER ACTION REPORTS FOR Dec 07, 41",
                        "--------------------------------------------------------------------------------",
                        "Amphibious Assault at Midway Island (158,91)",
                        "",
                        "TF 216 troops unloading over beach at Midway Island, 158,91",
                        "--------------------------------------------------------------------------------",
                        "Morning Air attack on Pearl Harbor , at 180,107",
                        "",
                        "Japanese aircraft",
                        "      B5N2 Kate x 18",
                        "",
                        "Allied Ships",
                        "      BB Nevada, Bomb hits 2",
                        "--------------------------------------------------------------------------------",
                        "Morning Air attack on Pearl Harbor , at 180,107",
                        "",
                        "Japanese aircraft",
                        "      D3A1 Val x 12",
                        "--------------------------------------------------------------------------------",
                        "Morning Air attack on Pearl Harbor , at 180,107",
                        "",
                        "Japanese aircraft",
                        "      B5N2 Kate x 9",
                        "--------------------------------------------------------------------------------",
                        "Ground combat at Hilo (183,111)",
                        "",
                        "Allied Bombardment attack",
                        "--------------------------------------------------------------------------------",
                        "Pre-Invasion action off Colombo (12,34)",
                        "",
                        "Japanese Ships",
                        "      CA Tone, Shell hits 1",
                    ]
                ),
                encoding="utf-8",
            )

            response = self.client.get("/combat")

            self.assertEqual(response.status_code, 200)
            self.assertIn("Combat", response.text)
            self.assertIn("Midway Island, 158,91", response.text)
            self.assertIn("Amphibious Invasion", response.text)
            self.assertIn("Pearl Harbor, 180,107", response.text)
            self.assertIn("Air Attack (x3)", response.text)
            self.assertIn("Hilo, 183,111", response.text)
            self.assertIn("Ground Combat", response.text)
            self.assertIn("Colombo, 12,34", response.text)
            self.assertIn("Pre-Invasion Action", response.text)

            midway_index = response.text.index("Midway Island, 158,91")
            pearl_index = response.text.index("Pearl Harbor, 180,107")
            hilo_index = response.text.index("Hilo, 183,111")
            colombo_index = response.text.index("Colombo, 12,34")
            self.assertLess(midway_index, pearl_index)
            self.assertLess(pearl_index, hilo_index)
            self.assertLess(hilo_index, colombo_index)

    def test_combat_page_limits_ground_combat_to_selected_side_bases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            save_dir = game_dir / "SAVE"
            allied_dir = save_dir / "ALLIED"
            allied_dir.mkdir(parents=True, exist_ok=True)

            self._set_runtime_env(game_dir)

            (allied_dir / "bases.json").write_text(
                json.dumps({"records": [{"name": "Midway Island"}]}),
                encoding="utf-8",
            )

            (save_dir / "combatreport.txt").write_text(
                "\n".join(
                    [
                        "AFTER ACTION REPORTS FOR Dec 07, 41",
                        "--------------------------------------------------------------------------------",
                        "Ground combat at Midway Island (158,91)",
                        "",
                        "Allied Bombardment attack",
                        "--------------------------------------------------------------------------------",
                        "Ground combat at Coal Harbour (204,49)",
                        "",
                        "Allied Bombardment attack",
                    ]
                ),
                encoding="utf-8",
            )

            response = self.client.get("/combat")

            self.assertEqual(response.status_code, 200)
            self.assertIn("Midway Island, 158,91", response.text)
            self.assertNotIn("Coal Harbour, 204,49", response.text)

    def test_combat_page_prefers_base_name_over_ship_label_at_base_hex(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            save_dir = game_dir / "SAVE"
            allied_dir = save_dir / "ALLIED"
            allied_dir.mkdir(parents=True, exist_ok=True)

            self._set_runtime_env(game_dir)

            (allied_dir / "bases.json").write_text(
                json.dumps({"records": [{"name": "Kota Bharu", "x": 51, "y": 75}]}),
                encoding="utf-8",
            )

            (save_dir / "combatreport.txt").write_text(
                "\n".join(
                    [
                        "AFTER ACTION REPORTS FOR Dec 07, 41",
                        "--------------------------------------------------------------------------------",
                        "Pre-Invasion action off Kota Bharu (51,75)",
                        "",
                        "Japanese Ships",
                        "      BB Haruna",
                    ]
                ),
                encoding="utf-8",
            )

            response = self.client.get("/combat")

            self.assertEqual(response.status_code, 200)
            self.assertIn("Kota Bharu, 51,75", response.text)
            self.assertNotIn("BB Haruna, 51,75", response.text)

    def test_legacy_data_route_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            japan_dir = game_dir / "SAVE" / "JAPAN"
            japan_dir.mkdir(parents=True, exist_ok=True)
            (japan_dir / "threats.json").write_text('{"threats": []}', encoding="utf-8")

            response = self.client.get(
                "/data/japan/threats.json",
                params={
                    "game_path": str(game_dir),
                    "pwstool_path": str(game_dir / "missing_tool"),
                    "side": "allies",
                },
            )
            self.assertEqual(response.status_code, 404)

    def test_regions_overlay_endpoint(self) -> None:
        response = self.client.get("/api/overlays")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload[0]["id"], "regions")
        self.assertEqual(payload[1]["id"], "invasions")
        self.assertEqual(payload[2]["id"], "taskforces")
        self.assertEqual(payload[3]["id"], "subpatrols")
        self.assertEqual(payload[4]["id"], "threats")
        self.assertIn("air-search", [item["id"] for item in payload])
        self.assertIn("air-asw", [item["id"] for item in payload])
        self.assertIn("air-attack", [item["id"] for item in payload])
        self.assertIn("air-hq-link", [item["id"] for item in payload])
        self.assertIn("sea-minefields", [item["id"] for item in payload])

    def test_invasions_overlay_endpoint_aggregates_combat_report_titles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            save_dir = game_dir / "SAVE"
            save_dir.mkdir(parents=True, exist_ok=True)

            self._set_runtime_env(game_dir)

            (save_dir / "combatreport.txt").write_text(
                "\n".join(
                    [
                        "AFTER ACTION REPORTS FOR Dec 07, 41",
                        "--------------------------------------------------------------------------------",
                        "Amphibious Assault at Kota Bharu (51,75)",
                        "",
                        "Japanese assault force lands",
                        "--------------------------------------------------------------------------------",
                        "Pre-Invasion action off Kota Bharu (51,75)",
                        "",
                        "Japanese Ships",
                        "      BB Haruna",
                        "--------------------------------------------------------------------------------",
                        "Pre-Invasion action off Aparri (44,70)",
                    ]
                ),
                encoding="utf-8",
            )

            response = self.client.get("/api/overlays/invasions")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["overlay_id"], "invasions")
            self.assertEqual(len(payload["features"]), 2)
            kota_bharu = next(feature for feature in payload["features"] if feature["center"] == [51, 75])
            self.assertEqual(kota_bharu["report_count"], 2)
            self.assertIn("Amphibious Assault", kota_bharu["invasion_types"])
            self.assertIn("Pre-Invasion Action", kota_bharu["invasion_types"])

            svg_response = self.client.get("/api/overlays/invasions.svg")
            self.assertEqual(svg_response.status_code, 200)
            self.assertIn('fill="rgba(255,255,255,0.96)"', svg_response.text)

    def test_theater_map_shows_invasions_toggle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            (game_dir / "SAVE").mkdir(parents=True, exist_ok=True)

            response = self.client.get(
                "/map",
                params={
                    "game_path": str(game_dir),
                    "pwstool_path": str(game_dir / "missing_tool"),
                    "side": "allies",
                },
            )

            self.assertEqual(response.status_code, 200)
            self.assertIn("Invasions", response.text)
            self.assertIn("/api/overlays/invasions.svg", response.text)

    def test_sea_minefields_overlay_endpoint_filters_to_selected_side(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            side_dir = game_dir / "SAVE" / "ALLIED"
            side_dir.mkdir(parents=True, exist_ok=True)
            (side_dir / "minefields.json").write_text(
                json.dumps(
                    [
                        {
                            "x": 120,
                            "y": 65,
                            "mine_count": 240,
                            "side": "ALLIED",
                        },
                        {
                            "x": 121,
                            "y": 66,
                            "mine_count": 120,
                            "side": "JAPAN",
                        },
                        {
                            "x": 120,
                            "y": 65,
                            "mine_count": 60,
                            "side": "ALLIED",
                        },
                    ]
                ),
                encoding="utf-8",
            )

            old_game_path = os.environ.get("APP_GAME_PATH")
            old_side = os.environ.get("APP_SIDE")
            old_pwstool = os.environ.get("APP_PWSTOOL_PATH")
            os.environ["APP_GAME_PATH"] = str(game_dir)
            os.environ["APP_SIDE"] = "allies"
            os.environ["APP_PWSTOOL_PATH"] = str(game_dir / "missing_tool")

            try:
                response = self.client.get("/api/overlays/sea-minefields")
            finally:
                if old_game_path is None:
                    os.environ.pop("APP_GAME_PATH", None)
                else:
                    os.environ["APP_GAME_PATH"] = old_game_path
                if old_side is None:
                    os.environ.pop("APP_SIDE", None)
                else:
                    os.environ["APP_SIDE"] = old_side
                if old_pwstool is None:
                    os.environ.pop("APP_PWSTOOL_PATH", None)
                else:
                    os.environ["APP_PWSTOOL_PATH"] = old_pwstool

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["overlay_id"], "sea-minefields")
            self.assertEqual(len(payload["features"]), 1)
            self.assertEqual(payload["features"][0]["center"], [120, 65])
            self.assertEqual(payload["features"][0]["mine_count"], 300)

    def test_air_search_overlay_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            side_dir = game_dir / "SAVE" / "ALLIED"
            side_dir.mkdir(parents=True, exist_ok=True)
            (side_dir / "airgroups.json").write_text(
                json.dumps(
                    [
                        {
                            "name": "VP-1",
                            "aircraft_type_name": "PBY",
                            "stationed_at_base_name": "Pearl Harbor",
                            "x": 120,
                            "y": 65,
                            "aircraft_range": 12,
                            "percent_search": 60,
                            "search_arc_start": 45,
                            "search_arc_end": 135,
                            "percent_asw": 0,
                        },
                        {
                            "name": "Unused",
                            "aircraft_type_name": "PBY",
                            "stationed_at_base_name": "Pearl Harbor",
                            "x": 120,
                            "y": 65,
                            "aircraft_range": 12,
                            "percent_search": 0,
                            "search_arc_start": 0,
                            "search_arc_end": 0,
                        },
                    ]
                ),
                encoding="utf-8",
            )

            old_game_path = os.environ.get("APP_GAME_PATH")
            old_side = os.environ.get("APP_SIDE")
            old_pwstool = os.environ.get("APP_PWSTOOL_PATH")
            os.environ["APP_GAME_PATH"] = str(game_dir)
            os.environ["APP_SIDE"] = "allies"
            os.environ["APP_PWSTOOL_PATH"] = str(game_dir / "missing_tool")

            try:
                response = self.client.get("/api/overlays/air-search")
            finally:
                if old_game_path is None:
                    os.environ.pop("APP_GAME_PATH", None)
                else:
                    os.environ["APP_GAME_PATH"] = old_game_path
                if old_side is None:
                    os.environ.pop("APP_SIDE", None)
                else:
                    os.environ["APP_SIDE"] = old_side
                if old_pwstool is None:
                    os.environ.pop("APP_PWSTOOL_PATH", None)
                else:
                    os.environ["APP_PWSTOOL_PATH"] = old_pwstool

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["overlay_id"], "air-search")
            self.assertEqual(len(payload["features"]), 1)
            self.assertEqual(payload["features"][0]["center"], [120, 65])
            self.assertEqual(payload["features"][0]["allocation_pct"], 60)
            self.assertEqual(payload["features"][0]["arc_start_degrees"], 45.0)
            self.assertEqual(payload["features"][0]["arc_end_degrees"], 135.0)
            self.assertFalse(payload["features"][0]["is_full_circle"])

    def test_air_hq_link_overlay_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            side_dir = game_dir / "SAVE" / "ALLIED"
            side_dir.mkdir(parents=True, exist_ok=True)
            (side_dir / "ground_units.json").write_text(
                json.dumps(
                    [
                        {
                            "record_id": 501,
                            "unit_type_name": "HQ",
                            "name": "5th Air HQ",
                            "end_of_day_x": 140,
                            "end_of_day_y": 80,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (side_dir / "airgroups.json").write_text(
                json.dumps(
                    [
                        {
                            "name": "1st Fighter Sq",
                            "aircraft_type_name": "FI",
                            "stationed_at_base_name": "Noumea",
                            "x": 100,
                            "y": 60,
                            "assigned_hq_id": 501,
                            "assigned_hq_name": "5th Air HQ",
                        },
                        {
                            "name": "2nd Recon Sq",
                            "aircraft_type_name": "PBY",
                            "stationed_at_base_name": "Luganville",
                            "x": 110,
                            "y": 66,
                            "local_air_hq_source_unit_id": 501,
                            "local_air_hq_name": "5th Air HQ",
                        },
                        {
                            "name": "Already Co-Located",
                            "aircraft_type_name": "FI",
                            "stationed_at_base_name": "HQ Base",
                            "x": 140,
                            "y": 80,
                            "assigned_hq_id": 501,
                            "assigned_hq_name": "5th Air HQ",
                        },
                    ]
                ),
                encoding="utf-8",
            )

            old_game_path = os.environ.get("APP_GAME_PATH")
            old_side = os.environ.get("APP_SIDE")
            old_pwstool = os.environ.get("APP_PWSTOOL_PATH")
            os.environ["APP_GAME_PATH"] = str(game_dir)
            os.environ["APP_SIDE"] = "allies"
            os.environ["APP_PWSTOOL_PATH"] = str(game_dir / "missing_tool")

            try:
                response = self.client.get("/api/overlays/air-hq-link")
            finally:
                if old_game_path is None:
                    os.environ.pop("APP_GAME_PATH", None)
                else:
                    os.environ["APP_GAME_PATH"] = old_game_path
                if old_side is None:
                    os.environ.pop("APP_SIDE", None)
                else:
                    os.environ["APP_SIDE"] = old_side
                if old_pwstool is None:
                    os.environ.pop("APP_PWSTOOL_PATH", None)
                else:
                    os.environ["APP_PWSTOOL_PATH"] = old_pwstool

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["overlay_id"], "air-hq-link")
            self.assertEqual(len(payload["features"]), 2)
            self.assertEqual(payload["features"][0]["end"], [140, 80])
            self.assertEqual(payload["features"][1]["end"], [140, 80])

    def test_air_asw_overlay_endpoint_uses_full_circle_when_arc_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            side_dir = game_dir / "SAVE" / "JAPAN"
            side_dir.mkdir(parents=True, exist_ok=True)
            (side_dir / "airgroups.json").write_text(
                json.dumps(
                    [
                        {
                            "name": "Chitose Ku T-1",
                            "aircraft_type_name": "FP",
                            "stationed_at_base_name": "Ominato",
                            "x": 119,
                            "y": 54,
                            "aircraft_range": 8,
                            "percent_asw": 40,
                            "asw_arc_start": 0,
                            "asw_arc_end": 0,
                            "percent_search": 0,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            old_game_path = os.environ.get("APP_GAME_PATH")
            old_side = os.environ.get("APP_SIDE")
            old_pwstool = os.environ.get("APP_PWSTOOL_PATH")
            os.environ["APP_GAME_PATH"] = str(game_dir)
            os.environ["APP_SIDE"] = "japan"
            os.environ["APP_PWSTOOL_PATH"] = str(game_dir / "missing_tool")

            try:
                response = self.client.get("/api/overlays/air-asw")
            finally:
                if old_game_path is None:
                    os.environ.pop("APP_GAME_PATH", None)
                else:
                    os.environ["APP_GAME_PATH"] = old_game_path
                if old_side is None:
                    os.environ.pop("APP_SIDE", None)
                else:
                    os.environ["APP_SIDE"] = old_side
                if old_pwstool is None:
                    os.environ.pop("APP_PWSTOOL_PATH", None)
                else:
                    os.environ["APP_PWSTOOL_PATH"] = old_pwstool

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["overlay_id"], "air-asw")
            self.assertEqual(len(payload["features"]), 1)
            self.assertEqual(payload["features"][0]["radius_hexes"], 8.0)
            self.assertEqual(payload["features"][0]["allocation_pct"], 40)
            self.assertTrue(payload["features"][0]["is_full_circle"])

    def test_air_attack_overlay_endpoint_filters_to_attack_bomber_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            side_dir = game_dir / "SAVE" / "ALLIED"
            side_dir.mkdir(parents=True, exist_ok=True)
            (side_dir / "airgroups.json").write_text(
                json.dumps(
                    [
                        {
                            "name": "SBD Group",
                            "aircraft_type_name": "DB",
                            "aircraft_name": "SBD-3 Dauntless",
                            "stationed_at_base_name": "Noumea",
                            "x": 140,
                            "y": 90,
                            "aircraft_range": 6,
                        },
                        {
                            "name": "B-17 Group",
                            "aircraft_type_name": "HB",
                            "aircraft_name": "B-17E Fortress",
                            "stationed_at_base_name": "Townsville",
                            "x": 80,
                            "y": 130,
                            "aircraft_range": 14,
                        },
                        {
                            "name": "CAP Fighter",
                            "aircraft_type_name": "F",
                            "aircraft_name": "F4F Wildcat",
                            "stationed_at_base_name": "Suva",
                            "x": 120,
                            "y": 75,
                            "aircraft_range": 4,
                        },
                    ]
                ),
                encoding="utf-8",
            )

            old_game_path = os.environ.get("APP_GAME_PATH")
            old_side = os.environ.get("APP_SIDE")
            old_pwstool = os.environ.get("APP_PWSTOOL_PATH")
            os.environ["APP_GAME_PATH"] = str(game_dir)
            os.environ["APP_SIDE"] = "allies"
            os.environ["APP_PWSTOOL_PATH"] = str(game_dir / "missing_tool")

            try:
                response = self.client.get("/api/overlays/air-attack")
            finally:
                if old_game_path is None:
                    os.environ.pop("APP_GAME_PATH", None)
                else:
                    os.environ["APP_GAME_PATH"] = old_game_path
                if old_side is None:
                    os.environ.pop("APP_SIDE", None)
                else:
                    os.environ["APP_SIDE"] = old_side
                if old_pwstool is None:
                    os.environ.pop("APP_PWSTOOL_PATH", None)
                else:
                    os.environ["APP_PWSTOOL_PATH"] = old_pwstool

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["overlay_id"], "air-attack")
            self.assertEqual(len(payload["features"]), 2)
            names = {feature["name"] for feature in payload["features"]}
            self.assertIn("SBD Group", names)
            self.assertIn("B-17 Group", names)
            self.assertNotIn("CAP Fighter", names)

    def test_air_attack_overlay_endpoint_includes_longb_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            side_dir = game_dir / "SAVE" / "ALLIED"
            side_dir.mkdir(parents=True, exist_ok=True)
            (side_dir / "airgroups.json").write_text(
                json.dumps(
                    [
                        {
                            "name": "11th BG/26th BS",
                            "aircraft_type_name": "LongB",
                            "aircraft_name": "B-18A Bolo",
                            "stationed_at_base_name": "Pearl Harbor",
                            "x": 180,
                            "y": 107,
                            "aircraft_range": 9,
                        },
                        {
                            "name": "19th BG/38th RS",
                            "aircraft_type_name": "LongB",
                            "aircraft_name": "B-17D Fortress",
                            "stationed_at_base_name": "Pearl Harbor",
                            "x": 180,
                            "y": 107,
                            "aircraft_range": 17,
                        },
                    ]
                ),
                encoding="utf-8",
            )

            old_game_path = os.environ.get("APP_GAME_PATH")
            old_side = os.environ.get("APP_SIDE")
            old_pwstool = os.environ.get("APP_PWSTOOL_PATH")
            os.environ["APP_GAME_PATH"] = str(game_dir)
            os.environ["APP_SIDE"] = "allies"
            os.environ["APP_PWSTOOL_PATH"] = str(game_dir / "missing_tool")

            try:
                response = self.client.get("/api/overlays/air-attack")
            finally:
                if old_game_path is None:
                    os.environ.pop("APP_GAME_PATH", None)
                else:
                    os.environ["APP_GAME_PATH"] = old_game_path
                if old_side is None:
                    os.environ.pop("APP_SIDE", None)
                else:
                    os.environ["APP_SIDE"] = old_side
                if old_pwstool is None:
                    os.environ.pop("APP_PWSTOOL_PATH", None)
                else:
                    os.environ["APP_PWSTOOL_PATH"] = old_pwstool

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            names = {feature["name"] for feature in payload["features"]}
            self.assertIn("11th BG/26th BS", names)
            self.assertIn("19th BG/38th RS", names)

    def test_taskforces_overlay_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            side_dir = game_dir / "SAVE" / "ALLIED"
            side_dir.mkdir(parents=True, exist_ok=True)
            (side_dir / "taskforces.json").write_text(
                "{\"start_of_day_x\": 10, \"start_of_day_y\": 20, \"end_of_day_x\": 11, \"end_of_day_y\": 22, \"target_x\": 15, \"target_y\": 30, \"flagship_name\": \"TF Alpha\", \"mission\": \"PATROL\"}\n"
                "{\"start_of_day_x\": 40, \"start_of_day_y\": 50, \"end_of_day_x\": 41, \"end_of_day_y\": 52, \"target_x\": 45, \"target_y\": 60, \"flagship_name\": \"TF Sub\", \"mission\": \"SUBPATROL\"}\n",
                encoding="utf-8",
            )

            response = self.client.get(
                "/api/overlays/taskforces",
                params={"game_path": str(game_dir), "side": "allies", "pwstool_path": str(game_dir / "missing_tool")},
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["overlay_id"], "taskforces")
            self.assertEqual(len(payload["features"]), 1)
            self.assertEqual(payload["features"][0]["start"], [10, 20])
            self.assertEqual(payload["features"][0]["end"], [11, 22])
            self.assertEqual(payload["features"][0]["target"], [15, 30])

    def test_subpatrols_overlay_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            side_dir = game_dir / "SAVE" / "ALLIED"
            side_dir.mkdir(parents=True, exist_ok=True)
            (side_dir / "taskforces.json").write_text(
                "{\"start_of_day_x\": 10, \"start_of_day_y\": 20, \"end_of_day_x\": 11, \"end_of_day_y\": 22, \"target_x\": 15, \"target_y\": 30, \"flagship_name\": \"TF Alpha\", \"mission\": \"PATROL\"}\n"
                "{\"start_of_day_x\": 40, \"start_of_day_y\": 50, \"end_of_day_x\": 41, \"end_of_day_y\": 52, \"target_x\": 45, \"target_y\": 60, \"flagship_name\": \"TF Sub\", \"mission\": \"SUBPATROL\"}\n",
                encoding="utf-8",
            )

            response = self.client.get(
                "/api/overlays/subpatrols",
                params={"game_path": str(game_dir), "side": "allies", "pwstool_path": str(game_dir / "missing_tool")},
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["overlay_id"], "subpatrols")
            self.assertEqual(len(payload["features"]), 1)
            self.assertEqual(payload["features"][0]["center"], [45, 60])
            self.assertEqual(payload["features"][0]["radius_hexes"], 2)

    def test_taskforces_overlay_accepts_json_array_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            side_dir = game_dir / "SAVE" / "ALLIED"
            side_dir.mkdir(parents=True, exist_ok=True)
            (side_dir / "taskforces.json").write_text(
                json.dumps(
                    [
                        {
                            "start_of_day_x": 10,
                            "start_of_day_y": 20,
                            "end_of_day_x": 11,
                            "end_of_day_y": 22,
                            "target_x": 15,
                            "target_y": 30,
                            "flagship_name": "TF Alpha",
                            "mission": "PATROL",
                        },
                        {
                            "start_of_day_x": 40,
                            "start_of_day_y": 50,
                            "end_of_day_x": 41,
                            "end_of_day_y": 52,
                            "target_x": 45,
                            "target_y": 60,
                            "flagship_name": "TF Sub",
                            "mission": "SUBPATROL",
                        },
                    ],
                    indent=2,
                ),
                encoding="utf-8",
            )

            response = self.client.get(
                "/api/overlays/taskforces",
                params={"game_path": str(game_dir), "side": "allies", "pwstool_path": str(game_dir / "missing_tool")},
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(len(payload["features"]), 1)
            self.assertEqual(payload["features"][0]["start"], [10, 20])

            response = self.client.get(
                "/api/overlays/subpatrols",
                params={"game_path": str(game_dir), "side": "allies", "pwstool_path": str(game_dir / "missing_tool")},
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(len(payload["features"]), 1)
            self.assertEqual(payload["features"][0]["center"], [45, 60])

    def test_threats_overlay_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            side_dir = game_dir / "SAVE" / "ALLIED"
            side_dir.mkdir(parents=True, exist_ok=True)
            (side_dir / "threats.json").write_text(
                json.dumps(
                    {
                        "sub_threat_areas": [{"position": {"x": 11, "y": 12}, "threat_score": 4}],
                        "surface_threat_areas": [{"position": {"x": 21, "y": 22}, "threat_score": 6}],
                        "carrier_threat_areas": [{"position": {"x": 31, "y": 32}, "threat_score": 9, "display_radius_hexes": 6.0, "display_radius_source": "enemy-carrier"}],
                        "threat_areas": [{"position": {"x": 41, "y": 42}, "threat_score": 7}],
                    }
                ),
                encoding="utf-8",
            )

            response = self.client.get(
                "/api/overlays/threats",
                params={"game_path": str(game_dir), "side": "allies", "pwstool_path": str(game_dir / "missing_tool")},
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["overlay_id"], "threats")
            self.assertEqual(payload["features"]["sub"][0]["center"], [11, 12])
            self.assertEqual(payload["features"]["sub"][0]["radius_hexes"], 3.0)
            self.assertEqual(payload["features"]["surface"][0]["center"], [21, 22])
            self.assertEqual(payload["features"]["surface"][0]["radius_hexes"], 3.0)
            self.assertEqual(payload["features"]["carrier"][0]["center"], [31, 32])
            self.assertEqual(payload["features"]["carrier"][0]["radius_hexes"], 6.0)
            self.assertEqual(payload["features"]["carrier"][0]["radius_source"], "enemy-carrier")
            self.assertEqual(payload["features"]["areas"][0]["center"], [41, 42])
            self.assertEqual(payload["features"]["areas"][0]["size_hexes"], 1)

    def test_turn_metadata_from_side_turn_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            side_dir = game_dir / "SAVE" / "ALLIED"
            side_dir.mkdir(parents=True, exist_ok=True)
            (side_dir / "turn.json").write_text(
                "{\"game_date\": \"12/25/41\", \"game_turn\": 42, \"scenario_name\": \"Test Scenario\"}",
                encoding="utf-8",
            )

            response = self.client.get(
                "/api/game-state",
                params={"game_path": str(game_dir), "side": "allies", "pwstool_path": str(game_dir / "missing_tool")},
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["game_date"], "12/25/41")
            self.assertEqual(payload["game_turn"], "42")
            self.assertEqual(payload["scenario_name"], "Test Scenario")

    def test_turn_start_then_end_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            save_dir = game_dir / "SAVE"
            save_dir.mkdir(parents=True, exist_ok=True)
            pwstool_dir = game_dir / "pwstool"
            pwstool_dir.mkdir(parents=True, exist_ok=True)

            fake_tool = pwstool_dir / "pywitpaescraper.py"
            fake_tool.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "args = sys.argv\n"
                "out_dir = Path(args[args.index('--output-dir') + 1])\n"
                "out_dir.mkdir(parents=True, exist_ok=True)\n"
                "(out_dir / 'turn.json').write_text('{\"ok\": true}', encoding='utf-8')\n"
                "raise SystemExit(0)\n",
                encoding="utf-8",
            )

            start_file = save_dir / "wpae002.pws"
            end_file = save_dir / "wpae000.pws"

            start_file.write_text("start", encoding="utf-8")

            start_response = self.client.get(
                "/api/game-state",
                params={
                    "game_path": str(game_dir),
                    "side": "allies",
                    "pwstool_path": str(pwstool_dir),
                },
            )
            self.assertEqual(start_response.status_code, 200)
            self.assertTrue(start_response.json()["turn_in_progress"])

            time.sleep(1.1)
            end_file.write_text("end", encoding="utf-8")
            os.utime(end_file, None)

            end_response = self.client.get(
                "/api/game-state",
                params={
                    "game_path": str(game_dir),
                    "side": "allies",
                    "pwstool_path": str(pwstool_dir),
                },
            )
            self.assertEqual(end_response.status_code, 200)
            payload = end_response.json()
            self.assertFalse(payload["turn_in_progress"])
            self.assertNotEqual(payload["turn_completed_at"], "")
            self.assertEqual(payload["pwstool_last_status"], "success")
            self.assertTrue((save_dir / "ALLIED" / "turn.json").exists())

    def test_pwstool_failure_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game_dir = Path(tmp_dir)
            save_dir = game_dir / "SAVE"
            save_dir.mkdir(parents=True, exist_ok=True)

            (save_dir / "wpae002.pws").write_text("start", encoding="utf-8")
            self.client.get(
                "/api/game-state",
                params={
                    "game_path": str(game_dir),
                    "side": "allies",
                    "pwstool_path": str(game_dir / "missing_tool"),
                },
            )

            time.sleep(1.1)
            (save_dir / "wpae000.pws").write_text("end", encoding="utf-8")
            os.utime(save_dir / "wpae000.pws", None)

            end_response = self.client.get(
                "/api/game-state",
                params={
                    "game_path": str(game_dir),
                    "side": "allies",
                    "pwstool_path": str(game_dir / "missing_tool"),
                },
            )
            self.assertEqual(end_response.status_code, 200)
            payload = end_response.json()
            self.assertEqual(payload["pwstool_last_status"], "failed")
            self.assertIn("Missing script", payload["pwstool_last_message"])


if __name__ == "__main__":
    unittest.main()
