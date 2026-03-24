"""Parse game data files to extract configuration values."""

from __future__ import annotations

import logging
import struct
from pathlib import Path

logger = logging.getLogger(__name__)


def load_pwshex_offsets(game_dir: Path) -> tuple[int, int]:
    """
    Load hex grid offsets from game offset data file.

    Returns (game_map_origin_pixel_offset_x, game_map_origin_pixel_offset_y).
    Defaults to (0, 0) if file not found or cannot be parsed.
    """
    candidates = [
        game_dir / "pwhexe.dat",
        game_dir / "pwshex.dat",
        game_dir / "ART" / "pwhexe.dat",
        game_dir / "ART" / "pwshex.dat",
    ]

    pwshex_path: Path | None = None
    for candidate in candidates:
        if candidate.exists():
            pwshex_path = candidate
            break

    if pwshex_path is None:
        logger.warning("No hex offset data file found under %s; using default offsets (0, 0)", game_dir)
        return 0, 0

    try:
        with open(pwshex_path, "rb") as f:
            data = f.read()

        if len(data) < 8:
            logger.warning("%s is too small (%d bytes); using default offsets (0, 0)", pwshex_path.name, len(data))
            return 0, 0

        # Two 4-byte little-endian signed ints: origin pixel offset for game hex grid.
        game_map_origin_pixel_offset_x, game_map_origin_pixel_offset_y = struct.unpack("<ii", data[:8])
        logger.info(
            "Loaded %s game-map-origin pixel offsets: x=%d, y=%d",
            pwshex_path.name,
            game_map_origin_pixel_offset_x,
            game_map_origin_pixel_offset_y,
        )
        return game_map_origin_pixel_offset_x, game_map_origin_pixel_offset_y

    except struct.error as e:
        logger.warning("Failed to unpack %s: %s; using default offsets (0, 0)", pwshex_path.name, e)
        return 0, 0
    except Exception as e:
        logger.exception("Unexpected error reading %s: %s; using default offsets (0, 0)", pwshex_path.name, e)
        return 0, 0
