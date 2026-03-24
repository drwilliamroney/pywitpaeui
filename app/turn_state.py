from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import subprocess
import sys

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TurnState:
    turn_in_progress: bool = False
    last_event: str = ""
    turn_completed_at: str = ""
    pwstool_last_status: str = "not-run"
    pwstool_last_message: str = ""
    pwstool_last_run_at: str = ""
    game_date: str = "-"
    game_turn: str = "-"
    scenario_name: str = "-"


class SaveTurnTracker:
    def __init__(self, game_path: Path) -> None:
        self._game_path = game_path
        self._save_dir = game_path / "SAVE"
        self._wpae002 = self._save_dir / "wpae002.pws"
        self._wpae000 = self._save_dir / "wpae000.pws"
        # Baseline mtimes at startup so we only react to changes that occur
        # after the UI process is running.
        self._seen_start_mtime: float | None = self._mtime(self._wpae002)
        self._seen_end_mtime: float | None = self._mtime(self._wpae000)
        self._state = TurnState()

        # If startup occurs mid-turn (start file exists but completion file is
        # absent/older), reflect that state immediately.
        if self._seen_start_mtime is not None and (
            self._seen_end_mtime is None or self._seen_start_mtime > self._seen_end_mtime
        ):
            self._state.turn_in_progress = True
            self._state.last_event = "Turn processing in progress"

    @property
    def state(self) -> TurnState:
        return self._state

    def _mtime(self, path: Path) -> float | None:
        if not path.exists():
            return None
        try:
            return path.stat().st_mtime
        except OSError:
            return None

    def _set_pwstool_status(self, status: str, message: str) -> None:
        self._state.pwstool_last_status = status
        self._state.pwstool_last_message = message
        self._state.pwstool_last_run_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    def _side_output_dir(self, side: str) -> Path:
        return self._save_dir / ("ALLIED" if side == "allies" else "JAPAN")

    def _load_turn_metadata(self, side: str) -> None:
        turn_json = self._side_output_dir(side) / "turn.json"
        if not turn_json.exists():
            return
        try:
            payload = json.loads(turn_json.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to parse turn metadata from %s", turn_json, exc_info=True)
            return

        game_date = payload.get("game_date")
        game_turn = payload.get("game_turn")
        scenario_name = payload.get("scenario_name")

        self._state.game_date = str(game_date) if game_date else "-"
        self._state.game_turn = str(game_turn) if game_turn is not None else "-"
        self._state.scenario_name = str(scenario_name) if scenario_name else "-"

    def should_run_pwstool_on_startup(self) -> bool:
        """
        Check if pwstool should be run on startup. Returns True if:
        - Start file exists (turn has started)
        - End file is missing or older than start file (turn hasn't completed yet or data is stale)
        """
        if self._seen_start_mtime is None:
            return False
        if self._seen_end_mtime is None or self._seen_start_mtime > self._seen_end_mtime:
            return True
        return False

    def _run_pwstool(self, side: str, pwstool_path: Path) -> None:
        bat_path = pwstool_path / "run_scraper.bat"
        if not bat_path.exists():
            self._set_pwstool_status(
                "failed",
                f"Missing launcher: {bat_path}",
            )
            logger.warning("Post-turn pwstool launcher not found: %s", bat_path)
            return

        if not self._wpae002.exists() or not self._wpae000.exists():
            self._set_pwstool_status(
                "failed",
                "Required SAVE files are missing for pwstool invocation",
            )
            logger.warning("Skipping pwstool run; required save files are missing")
            return

        side_flag = "--allied" if side == "allies" else "--japan"
        side_output_dir = self._side_output_dir(side)
        side_output_dir.mkdir(parents=True, exist_ok=True)

        before_mtimes: dict[Path, float] = {}
        for path in side_output_dir.glob("*.json*"):
            try:
                before_mtimes[path] = path.stat().st_mtime
            except OSError:
                continue

        command = [
            "cmd", "/c", str(bat_path),
            "--dll-dir",
            str(self._game_path),
            "--start-of-day-file",
            str(self._wpae002),
            "--end-of-day-file",
            str(self._wpae000),
            side_flag,
            "--output-dir",
            str(side_output_dir),
        ]

        logger.info("Running post-turn pwstool command for side='%s'", side)
        self._set_pwstool_status("running", "Post-turn pwstool execution in progress")

        try:
            result = subprocess.run(
                command,
                cwd=str(pwstool_path),
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
        except Exception as error:
            self._set_pwstool_status("failed", f"pwstool execution error: {error}")
            logger.exception("Post-turn pwstool invocation failed")
            return

        if result.returncode == 0:
            changed_files: list[str] = []
            for path in side_output_dir.glob("*.json*"):
                try:
                    current_mtime = path.stat().st_mtime
                except OSError:
                    continue
                previous_mtime = before_mtimes.get(path)
                if previous_mtime is None or current_mtime > previous_mtime:
                    changed_files.append(path.name)

            if changed_files:
                self._set_pwstool_status(
                    "success",
                    (
                        f"pwstool completed successfully; updated {len(changed_files)} file(s) "
                        f"in {side_output_dir}"
                    ),
                )
                self._load_turn_metadata(side)
            else:
                self._set_pwstool_status(
                    "failed",
                    (
                        f"pwstool exited successfully but no new/updated JSON files were found in "
                        f"{side_output_dir}"
                    ),
                )
            logger.info("Post-turn pwstool completed successfully")
            return

        stderr_tail = (result.stderr or "").strip()
        stdout_tail = (result.stdout or "").strip()
        detail = stderr_tail or stdout_tail or "No output"
        self._set_pwstool_status("failed", f"pwstool exited with {result.returncode}: {detail[:280]}")
        logger.warning("Post-turn pwstool failed with code %s", result.returncode)

    def update(self, side: str, pwstool_path: Path) -> TurnState:
        self._load_turn_metadata(side)
        start_mtime = self._mtime(self._wpae002)
        end_mtime = self._mtime(self._wpae000)

        if start_mtime is not None and start_mtime != self._seen_start_mtime:
            self._seen_start_mtime = start_mtime
            self._state.turn_in_progress = True
            self._state.last_event = "Turn processing in progress"
            self._state.turn_completed_at = ""
            self._state.pwstool_last_status = "pending"
            self._state.pwstool_last_message = "Updating is pending until turn completion"

        if end_mtime is not None and end_mtime != self._seen_end_mtime:
            self._seen_end_mtime = end_mtime
            self._state.turn_in_progress = False
            self._state.last_event = "Turn complete"
            self._state.turn_completed_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
            self._run_pwstool(side, pwstool_path)

        return self._state
