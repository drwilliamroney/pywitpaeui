"""Shared gamehex-to-pixel coordinate transform used by map overlays."""

from __future__ import annotations

from app.overlays import GAME_COLS, GAME_ROWS


class GameHexTransform:
    """Convert game hex coordinates to native map pixel coordinates.

    Two variants:
        gamehex_to_pixel(x, y)      -> top-left corner: (hex-1) * step
        gamehex_to_hex_center(x, y) -> visual center:   (hex-1) * step + step/2

    Regions use top-left (boundary polygon vertices map to hex corners).
    All point-on-hex overlays (circles, lines, markers) use hex_center.
    """

    def __init__(self, map_width: int, map_height: int) -> None:
        self.map_width = map_width
        self.map_height = map_height
        self.gamehex_to_pixel_step_x = map_width / (GAME_COLS - 1)
        self.gamehex_to_pixel_step_y = map_height / (GAME_ROWS - 1)

    def gamehex_to_pixel(self, game_hex_x: int, game_hex_y: int) -> tuple[float, float]:
        """Top-left corner of the hex cell. Use for region polygon vertices."""
        # Alignment contract (do not change): regions are drawn from hex top-left vertices.
        # Any tweak here will shift region polygons relative to other overlays.
        pixel_x = (game_hex_x - 1) * self.gamehex_to_pixel_step_x
        pixel_y = (game_hex_y - 1) * self.gamehex_to_pixel_step_y
        return pixel_x, pixel_y

    def gamehex_to_hex_center(self, game_hex_x: int, game_hex_y: int) -> tuple[float, float]:
        """Visual center of the hex cell. Use for all point-on-hex overlays."""
        # Alignment contract (do not change): point overlays must use geometric half-step.
        # This is intentionally step/2, not file-based offsets.
        pixel_x = (game_hex_x - 1) * self.gamehex_to_pixel_step_x + self.gamehex_to_pixel_step_x / 2
        pixel_y = (game_hex_y - 1) * self.gamehex_to_pixel_step_y + self.gamehex_to_pixel_step_y / 2
        return pixel_x, pixel_y
