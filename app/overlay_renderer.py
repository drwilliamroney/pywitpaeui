"""Render overlay images with proper coordinate transformation."""

from __future__ import annotations

import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from typing import Any

from app.coordinate_transform import GameHexTransform


class OverlayRenderer:
    """Renders overlays as transparent images using gamehex-to-pixel mapping."""

    def __init__(self, game_dir: Path, map_width: int, map_height: int):
        """
        Initialize renderer with game directory and map dimensions.
        
        Args:
            game_dir: Path to game directory (for pwhexe.dat offsets)
            map_width: Width of output image in pixels
            map_height: Height of output image in pixels
        """
        self.game_dir = game_dir
        self.map_width = map_width
        self.map_height = map_height

        self.transform = GameHexTransform(map_width=map_width, map_height=map_height)
        self.gamehex_to_pixel_step_x = self.transform.gamehex_to_pixel_step_x
        self.gamehex_to_pixel_step_y = self.transform.gamehex_to_pixel_step_y
        self._display_scale = max(1.0, min(map_width / 1400.0, map_height / 900.0))
        self._line_w = max(2, int(round(2 * self._display_scale)))
        self._marker_r = max(4, int(round(4 * self._display_scale)))
        self._dash_len = max(8, int(round(8 * self._display_scale)))
        self._region_font = self._load_region_font()
        self._region_stroke = max(2, int(self._region_font_size / 12))

    def gamehex_to_pixel(self, game_hex_x: int, game_hex_y: int) -> tuple[float, float]:
        """Top-left corner of the hex cell. Use for region polygon vertices."""
        return self.transform.gamehex_to_pixel(game_hex_x, game_hex_y)

    def gamehex_to_hex_center(self, game_hex_x: int, game_hex_y: int) -> tuple[float, float]:
        """Visual center of the hex cell. Use for all point-on-hex overlays."""
        return self.transform.gamehex_to_hex_center(game_hex_x, game_hex_y)

    def render_regions(self, regions_data: list[dict[str, Any]]) -> Image.Image:
        """Render region polygons as transparent image."""
        img = Image.new("RGBA", (self.map_width, self.map_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        for feature in regions_data:
            polygon = feature.get("polygon", [])
            if not polygon or len(polygon) < 3:
                continue

            # Keep regions on hex top-left vertices (do not switch to center).
            pixels = [self.gamehex_to_pixel(hex_x, hex_y) for hex_x, hex_y in polygon]

            # Parse color and border
            color_str = feature.get("color", "rgba(100,100,100,0.25)")
            border_str = feature.get("border", "rgba(100,100,100,0.7)")

            fill_color = self._parse_rgba(color_str)
            border_color = self._parse_rgba(border_str)

            # Draw filled polygon
            if len(pixels) >= 3:
                draw.polygon(pixels, fill=fill_color, outline=border_color, width=self._line_w)

            label = str(feature.get("name") or "").strip()
            if label and len(pixels) >= 3:
                center_x = sum(point[0] for point in pixels) / len(pixels)
                center_y = sum(point[1] for point in pixels) / len(pixels)
                bbox = draw.textbbox(
                    (0, 0),
                    label,
                    font=self._region_font,
                    stroke_width=self._region_stroke,
                )
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]
                draw.text(
                    (center_x - text_w / 2, center_y - text_h / 2),
                    label,
                    font=self._region_font,
                    fill=(255, 255, 255, 242),
                    stroke_fill=(0, 0, 0, 190),
                    stroke_width=self._region_stroke,
                )

        return img

    def _load_region_font(self) -> Any:
        # Overlay is rendered at native map resolution, then downscaled in the browser.
        # Use a large native font size so labels remain readable after scaling.
        self._region_font_size = max(28, int(self.map_width / 60))
        for font_name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(font_name, self._region_font_size)
            except OSError:
                continue
        return ImageFont.load_default()

    def render_taskforces(self, taskforces_data: list[dict[str, Any]]) -> Image.Image:
        """Render taskforce movement lines as transparent image."""
        img = Image.new("RGBA", (self.map_width, self.map_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        for feature in taskforces_data:
            start = feature.get("start")
            end = feature.get("end")
            target = feature.get("target")

            if not (start and end and target):
                continue

            # Taskforce path points are point-on-hex, so they must stay centered.
            start_px = self.gamehex_to_hex_center(start[0], start[1])
            end_px = self.gamehex_to_hex_center(end[0], end[1])
            target_px = self.gamehex_to_hex_center(target[0], target[1])
            solid_color = self._parse_rgba(feature.get("solid_color", "rgba(255,165,0,1)"))
            dash_color = self._parse_rgba(feature.get("dash_color", "rgba(255,210,80,1)"))
            marker_fill = self._parse_rgba(feature.get("marker_fill", "rgba(100,255,100,1)"))
            marker_outline = self._parse_rgba(feature.get("marker_outline", "rgba(0,200,0,1)"))

            # Draw dashed lines for movement
            self._draw_dashed_line(
                draw,
                start_px,
                end_px,
                solid_color,
                width=self._line_w,
                dash_length=self._dash_len,
            )
            self._draw_dashed_line(
                draw,
                end_px,
                target_px,
                dash_color,
                width=self._line_w,
                dash_length=self._dash_len,
            )

            # Draw circles for waypoints
            radius = self._marker_r
            draw.ellipse(
                [start_px[0] - radius, start_px[1] - radius, start_px[0] + radius, start_px[1] + radius],
                fill=marker_fill,
                outline=marker_outline,
                width=max(1, self._line_w // 2),
            )

        return img

    def render_base_supply(self, base_supply_data: list[dict[str, Any]]) -> Image.Image:
        """Render base supply markers centered on bases."""
        img = Image.new("RGBA", (self.map_width, self.map_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        marker_colors: dict[str, tuple[tuple[int, int, int, int], tuple[int, int, int, int]]] = {
            "green": ((64, 220, 96, 235), (24, 120, 44, 255)),
            "yellow": ((255, 220, 60, 235), (165, 130, 10, 255)),
            "red": ((238, 78, 78, 235), (150, 20, 20, 255)),
            "gold": ((255, 215, 0, 245), (180, 130, 0, 255)),
        }

        for feature in base_supply_data:
            center = feature.get("center")
            if not center:
                continue

            center_px = self.gamehex_to_hex_center(center[0], center[1])
            shape = str(feature.get("marker_shape") or "circle").lower()
            color_key = str(feature.get("marker_color") or "yellow").lower()
            fill_color, outline_color = marker_colors.get(color_key, marker_colors["yellow"])

            if shape == "star":
                points = self._star_points(center_px[0], center_px[1], outer_radius=50.0, inner_radius=22.0)
                draw.polygon(points, fill=fill_color, outline=outline_color)
            else:
                radius_px = 25.0
                draw.ellipse(
                    [
                        center_px[0] - radius_px,
                        center_px[1] - radius_px,
                        center_px[0] + radius_px,
                        center_px[1] + radius_px,
                    ],
                    fill=fill_color,
                    outline=outline_color,
                    width=max(1, self._line_w // 2),
                )

        return img

    def render_hq_coverage(self, hq_coverage_data: list[dict[str, Any]]) -> Image.Image:
        """Render HQ command-radius circles with a green cross-hatch fill."""
        img = Image.new("RGBA", (self.map_width, self.map_height), (0, 0, 0, 0))

        for feature in hq_coverage_data:
            center = feature.get("center")
            if not center:
                continue

            center_px = self.gamehex_to_hex_center(center[0], center[1])
            radius_hexes = float(feature.get("radius_hexes", 3))
            radius_px = radius_hexes * ((self.gamehex_to_pixel_step_x + self.gamehex_to_pixel_step_y) / 2)
            self._draw_hatched_circle(
                img,
                center_px[0],
                center_px[1],
                radius_px,
                fill_color=(46, 132, 69, 42),
                hatch_color=(98, 220, 120, 148),
                outline_color=(88, 220, 108, 250),
            )

        return img

    def render_subpatrols(self, subpatrols_data: list[dict[str, Any]]) -> Image.Image:
        """Render subpatrols as circles with radius."""
        img = Image.new("RGBA", (self.map_width, self.map_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        for feature in subpatrols_data:
            center = feature.get("center")
            radius_hexes = feature.get("radius_hexes", 1)

            if not center:
                continue

            # Circle centers are point-on-hex and must use hex center alignment.
            center_px = self.gamehex_to_hex_center(center[0], center[1])

            # Match prior canvas logic: radius in hexes scales directly to pixels.
            radius_px = radius_hexes * ((self.gamehex_to_pixel_step_x + self.gamehex_to_pixel_step_y) / 2)

            # Draw circle
            draw.ellipse(
                [
                    center_px[0] - radius_px,
                    center_px[1] - radius_px,
                    center_px[0] + radius_px,
                    center_px[1] + radius_px,
                ],
                outline=(100, 255, 100, 230),
                width=self._line_w,
            )

        return img

    def render_threats(self, threats_data: dict[str, Any]) -> Image.Image:
        """Render threat circles as transparent image."""
        img = Image.new("RGBA", (self.map_width, self.map_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        for threat_type, color, features in [
            ("sub", (100, 150, 255, 235), threats_data.get("sub", [])),
            ("surface", (255, 100, 100, 235), threats_data.get("surface", [])),
            ("carrier", (255, 200, 50, 235), threats_data.get("carrier", [])),
        ]:
            for feature in features:
                center = feature.get("center")
                radius_hexes = feature.get("radius_hexes", 1)

                if not center:
                    continue

                # Threat centers are point-on-hex and must use hex center alignment.
                center_px = self.gamehex_to_hex_center(center[0], center[1])
                radius_px = radius_hexes * ((self.gamehex_to_pixel_step_x + self.gamehex_to_pixel_step_y) / 2)

                draw.ellipse(
                    [
                        center_px[0] - radius_px,
                        center_px[1] - radius_px,
                        center_px[0] + radius_px,
                        center_px[1] + radius_px,
                    ],
                    outline=color,
                    width=max(1, self._line_w // 2),
                )

        # Threat areas (X markers)
        for feature in threats_data.get("areas", []):
            center = feature.get("center")
            size_hexes = feature.get("size_hexes", 1)

            if not center:
                continue

            center_px = self.gamehex_to_hex_center(center[0], center[1])
            size_px = size_hexes * ((self.gamehex_to_pixel_step_x + self.gamehex_to_pixel_step_y) / 2)

            # Draw X
            draw.line(
                [
                    center_px[0] - size_px,
                    center_px[1] - size_px,
                    center_px[0] + size_px,
                    center_px[1] + size_px,
                ],
                fill=(220, 90, 90, 235),
                width=self._line_w,
            )
            draw.line(
                [
                    center_px[0] + size_px,
                    center_px[1] - size_px,
                    center_px[0] - size_px,
                    center_px[1] + size_px,
                ],
                fill=(220, 90, 90, 235),
                width=self._line_w,
            )

        return img

    def render_air_mission_sectors(self, mission_data: list[dict[str, Any]]) -> Image.Image:
        """Render air mission sectors using aircraft range and compass arcs."""
        img = Image.new("RGBA", (self.map_width, self.map_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        for feature in mission_data:
            center = feature.get("center")
            if not center:
                continue

            center_px = self.gamehex_to_hex_center(int(center[0]), int(center[1]))
            radius_hexes = float(feature.get("radius_hexes") or 0.0)
            if radius_hexes <= 0:
                continue

            radius_px = radius_hexes * ((self.gamehex_to_pixel_step_x + self.gamehex_to_pixel_step_y) / 2.0)
            fill_color = self._parse_rgba(str(feature.get("fill_color") or "rgba(74,160,255,0.18)"))
            stroke_color = self._parse_rgba(str(feature.get("stroke_color") or "rgba(74,160,255,0.90)"))
            start_degrees = float(feature.get("arc_start_degrees") or 0.0)
            end_degrees = float(feature.get("arc_end_degrees") or 0.0)
            is_full_circle = bool(feature.get("is_full_circle"))

            if is_full_circle:
                draw.ellipse(
                    [
                        center_px[0] - radius_px,
                        center_px[1] - radius_px,
                        center_px[0] + radius_px,
                        center_px[1] + radius_px,
                    ],
                    fill=fill_color,
                    outline=stroke_color,
                    width=max(1, self._line_w // 2),
                )
                continue

            sector_points = self._sector_polygon_points(center_px[0], center_px[1], radius_px, start_degrees, end_degrees)
            if len(sector_points) < 3:
                continue

            draw.polygon(sector_points, fill=fill_color)
            draw.line(sector_points + [sector_points[0]], fill=stroke_color, width=max(1, self._line_w // 2))

        return img

    @staticmethod
    def _parse_rgba(rgba_str: str) -> tuple[int, int, int, int]:
        """Parse rgba(r,g,b,a) string to (r, g, b, a) tuple."""
        try:
            # Extract numbers from "rgba(r,g,b,a)"
            parts = rgba_str.strip().replace("rgba(", "").replace(")", "").split(",")
            r = int(parts[0].strip())
            g = int(parts[1].strip())
            b = int(parts[2].strip())
            a = int(float(parts[3].strip()) * 255)  # Convert 0-1 to 0-255
            return (r, g, b, a)
        except (ValueError, IndexError):
            return (100, 100, 100, 64)  # Default gray

    @staticmethod
    def _star_points(cx: float, cy: float, outer_radius: float, inner_radius: float) -> list[tuple[float, float]]:
        points: list[tuple[float, float]] = []
        for i in range(10):
            angle = (i * 36.0 - 90.0) * 3.141592653589793 / 180.0
            radius = outer_radius if i % 2 == 0 else inner_radius
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
        return points

    @staticmethod
    def _draw_dashed_line(draw: ImageDraw.ImageDraw, start: tuple[float, float], end: tuple[float, float], color: tuple[int, int, int, int], width: int = 1, dash_length: int = 5) -> None:
        """Draw a dashed line."""
        x1, y1 = start
        x2, y2 = end
        
        distance = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        if distance == 0:
            return
        
        dx = (x2 - x1) / distance
        dy = (y2 - y1) / distance
        
        steps = int(distance / dash_length)
        for i in range(0, steps, 2):
            x1_dash = x1 + dx * i * dash_length
            y1_dash = y1 + dy * i * dash_length
            x2_dash = x1 + dx * (i + 1) * dash_length
            y2_dash = y1 + dy * (i + 1) * dash_length
            draw.line([x1_dash, y1_dash, x2_dash, y2_dash], fill=color, width=width)

    def _draw_hatched_circle(
        self,
        image: Image.Image,
        cx: float,
        cy: float,
        radius: float,
        *,
        fill_color: tuple[int, int, int, int],
        hatch_color: tuple[int, int, int, int],
        outline_color: tuple[int, int, int, int],
    ) -> None:
        draw = ImageDraw.Draw(image)
        bbox = [cx - radius, cy - radius, cx + radius, cy + radius]
        draw.ellipse(bbox, fill=fill_color)

        mask = Image.new("L", image.size, 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse(bbox, fill=255)

        hatch = Image.new("RGBA", image.size, (0, 0, 0, 0))
        hatch_draw = ImageDraw.Draw(hatch)
        spacing = max(10, int(round(radius / 3)))
        left = int(cx - radius) - spacing * 2
        right = int(cx + radius) + spacing * 2
        top = int(cy - radius) - spacing * 2
        bottom = int(cy + radius) + spacing * 2

        for offset in range(left - bottom, right - top + spacing, spacing):
            hatch_draw.line([(offset + top, top), (offset + bottom, bottom)], fill=hatch_color, width=2)
            hatch_draw.line([(offset + top, bottom), (offset + bottom, top)], fill=hatch_color, width=2)

        masked_hatch = Image.composite(hatch, Image.new("RGBA", image.size, (0, 0, 0, 0)), mask)
        image.alpha_composite(masked_hatch)

        draw = ImageDraw.Draw(image)
        draw.ellipse(bbox, outline=outline_color, width=max(1, self._line_w // 2))

    @staticmethod
    def _compass_endpoint(cx: float, cy: float, radius: float, bearing_degrees: float) -> tuple[float, float]:
        angle = math.radians(bearing_degrees)
        return (cx + math.sin(angle) * radius, cy - math.cos(angle) * radius)

    def _sector_polygon_points(
        self,
        cx: float,
        cy: float,
        radius: float,
        start_degrees: float,
        end_degrees: float,
    ) -> list[tuple[float, float]]:
        sweep = (end_degrees - start_degrees) % 360.0
        if math.isclose(sweep, 0.0, abs_tol=0.001):
            sweep = 360.0

        steps = max(12, int(round(sweep / 10.0)))
        points: list[tuple[float, float]] = [(cx, cy)]
        for index in range(steps + 1):
            bearing = (start_degrees + (sweep * index / steps)) % 360.0
            points.append(self._compass_endpoint(cx, cy, radius, bearing))
        return points
