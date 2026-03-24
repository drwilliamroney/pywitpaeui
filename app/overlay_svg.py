from __future__ import annotations

import math
from html import escape
from pathlib import Path
from typing import Any

from app.coordinate_transform import GameHexTransform


class OverlaySvgRenderer:
    """Render overlays as SVG documents in bitmap pixel space."""

    def __init__(self, game_dir: Path, map_width: int, map_height: int) -> None:
        self.game_dir = game_dir
        self.map_width = map_width
        self.map_height = map_height

        self.transform = GameHexTransform(map_width=map_width, map_height=map_height)
        self.gamehex_to_pixel_step_x = self.transform.gamehex_to_pixel_step_x
        self.gamehex_to_pixel_step_y = self.transform.gamehex_to_pixel_step_y
        self.scale = max(1.0, min(map_width / 1400.0, map_height / 900.0))
        self.line_w = max(2.0, 2.0 * self.scale)
        self.font_size = max(14.0, map_width / 120.0)
        self.marker_r = max(4.0, 4.0 * self.scale)
        self.dash_len = max(8.0, 8.0 * self.scale)

    def gamehex_to_pixel(self, game_hex_x: int, game_hex_y: int) -> tuple[float, float]:
        """Top-left corner of the hex cell. Use for region polygon vertices."""
        return self.transform.gamehex_to_pixel(game_hex_x, game_hex_y)

    def gamehex_to_hex_center(self, game_hex_x: int, game_hex_y: int) -> tuple[float, float]:
        """Visual center of the hex cell. Use for all point-on-hex overlays."""
        return self.transform.gamehex_to_hex_center(game_hex_x, game_hex_y)

    def _svg_start(self) -> str:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.map_width}" height="{self.map_height}" '
            f'viewBox="0 0 {self.map_width} {self.map_height}">'
        )

    @staticmethod
    def _svg_end() -> str:
        return "</svg>"

    @staticmethod
    def _parse_rgba(rgba: str, fallback: tuple[int, int, int, float]) -> tuple[int, int, int, float]:
        try:
            text = rgba.strip()
            if not text.startswith("rgba(") or not text.endswith(")"):
                return fallback
            parts = [p.strip() for p in text[5:-1].split(",")]
            r = int(parts[0])
            g = int(parts[1])
            b = int(parts[2])
            a = float(parts[3])
            return r, g, b, max(0.0, min(1.0, a))
        except Exception:
            return fallback

    def render_regions_svg(self, regions_data: list[dict[str, Any]]) -> str:
        parts: list[str] = [self._svg_start()]

        for feature in regions_data:
            polygon = feature.get("polygon", [])
            if not polygon or len(polygon) < 3:
                continue

            # Keep regions on hex top-left vertices (do not switch to center).
            points = [self.gamehex_to_pixel(int(x), int(y)) for x, y in polygon]
            points_str = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)

            fr, fg, fb, fa = self._parse_rgba(str(feature.get("color", "")), (100, 100, 100, 0.25))
            br, bg, bb, ba = self._parse_rgba(str(feature.get("border", "")), (100, 100, 100, 0.7))

            parts.append(
                f'<polygon points="{points_str}" '
                f'fill="rgb({fr},{fg},{fb})" fill-opacity="{fa:.3f}" '
                f'stroke="rgb({br},{bg},{bb})" stroke-opacity="{ba:.3f}" '
                f'stroke-width="{self.line_w:.2f}"/>'
            )

            label = escape(str(feature.get("name") or "").strip())
            if label:
                cx = sum(x for x, _ in points) / len(points)
                cy = sum(y for _, y in points) / len(points)
                parts.append(
                    f'<text x="{cx:.2f}" y="{cy:.2f}" text-anchor="middle" dominant-baseline="middle" '
                    f'font-family="Segoe UI, Arial, sans-serif" font-size="{self.font_size:.2f}" '
                    f'fill="rgba(255,255,255,0.95)" stroke="rgba(0,0,0,0.78)" '
                    f'stroke-width="{max(1.0, self.line_w * 0.5):.2f}" paint-order="stroke fill">{label}</text>'
                )

        parts.append(self._svg_end())
        return "".join(parts)

    def render_taskforces_svg(self, taskforces_data: list[dict[str, Any]]) -> str:
        parts: list[str] = [self._svg_start()]

        for feature in taskforces_data:
            start = feature.get("start")
            end = feature.get("end")
            target = feature.get("target")
            if not (start and end and target):
                continue

            # Taskforce points are point-on-hex, so they must stay centered.
            sx, sy = self.gamehex_to_hex_center(int(start[0]), int(start[1]))
            ex, ey = self.gamehex_to_hex_center(int(end[0]), int(end[1]))
            tx, ty = self.gamehex_to_hex_center(int(target[0]), int(target[1]))
            solid_color = escape(str(feature.get("solid_color") or "rgba(255,165,0,1)"))
            dash_color = escape(str(feature.get("dash_color") or "rgba(255,210,80,1)"))
            marker_fill = escape(str(feature.get("marker_fill") or "rgba(100,255,100,1)"))
            marker_outline = escape(str(feature.get("marker_outline") or "rgba(0,200,0,1)"))

            parts.append(
                f'<line x1="{sx:.2f}" y1="{sy:.2f}" x2="{ex:.2f}" y2="{ey:.2f}" '
                f'stroke="{solid_color}" stroke-width="{self.line_w:.2f}" '
                f'stroke-linecap="round"/>'
            )
            parts.append(
                f'<line x1="{ex:.2f}" y1="{ey:.2f}" x2="{tx:.2f}" y2="{ty:.2f}" '
                f'stroke="{dash_color}" stroke-width="{self.line_w:.2f}" '
                f'stroke-linecap="round" stroke-dasharray="{self.dash_len:.2f},{self.dash_len:.2f}"/>'
            )
            parts.append(
                f'<circle cx="{sx:.2f}" cy="{sy:.2f}" r="{self.marker_r:.2f}" '
                f'fill="{marker_fill}" stroke="{marker_outline}" '
                f'stroke-width="{max(1.0, self.line_w * 0.5):.2f}"/>'
            )

        parts.append(self._svg_end())
        return "".join(parts)

    def render_link_lines_svg(self, link_data: list[dict[str, Any]]) -> str:
        parts: list[str] = [self._svg_start()]

        for feature in link_data:
            start = feature.get("start")
            end = feature.get("end")
            if not (start and end):
                continue

            sx, sy = self.gamehex_to_hex_center(int(start[0]), int(start[1]))
            ex, ey = self.gamehex_to_hex_center(int(end[0]), int(end[1]))
            stroke_color = escape(str(feature.get("stroke_color") or "rgba(255,220,60,0.98)"))
            parts.append(
                f'<line x1="{sx:.2f}" y1="{sy:.2f}" x2="{ex:.2f}" y2="{ey:.2f}" '
                f'stroke="{stroke_color}" stroke-width="{self.line_w:.2f}" '
                f'stroke-linecap="round" stroke-dasharray="{self.dash_len:.2f},{self.dash_len:.2f}"/>'
            )

        parts.append(self._svg_end())
        return "".join(parts)

    def render_base_supply_svg(self, base_supply_data: list[dict[str, Any]]) -> str:
        parts: list[str] = [self._svg_start()]

        marker_colors = {
            "green": ("rgba(64,220,96,0.92)", "rgba(24,120,44,1)"),
            "yellow": ("rgba(255,220,60,0.92)", "rgba(165,130,10,1)"),
            "red": ("rgba(238,78,78,0.92)", "rgba(150,20,20,1)"),
            "gold": ("rgba(255,215,0,0.96)", "rgba(180,130,0,1)"),
        }

        for feature in base_supply_data:
            center = feature.get("center")
            if not center:
                continue

            cx, cy = self.gamehex_to_hex_center(int(center[0]), int(center[1]))
            shape = str(feature.get("marker_shape") or "circle").lower()
            color_key = str(feature.get("marker_color") or "yellow").lower()
            fill_color, stroke_color = marker_colors.get(color_key, marker_colors["yellow"])
            if shape == "star":
                points = self._star_points(cx, cy, outer_radius=50.0, inner_radius=22.0)
                points_str = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
                parts.append(
                    f'<polygon points="{points_str}" fill="{fill_color}" stroke="{stroke_color}" '
                    f'stroke-width="{max(1.0, self.line_w * 0.5):.2f}"/>'
                )
            else:
                parts.append(
                    f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="25.00" '
                    f'fill="{fill_color}" stroke="{stroke_color}" '
                    f'stroke-width="{max(1.0, self.line_w * 0.5):.2f}"/>'
                )

        parts.append(self._svg_end())
        return "".join(parts)

    def render_invasions_svg(self, invasions_data: list[dict[str, Any]]) -> str:
        parts: list[str] = [self._svg_start()]

        for feature in invasions_data:
            center = feature.get("center")
            if not center:
                continue

            cx, cy = self.gamehex_to_hex_center(int(center[0]), int(center[1]))
            fill_color = escape(str(feature.get("marker_fill") or "rgba(255,255,255,0.96)"))
            stroke_color = escape(str(feature.get("marker_stroke") or "rgba(255,255,255,0.96)"))
            outer_radius = 2.5 * ((self.gamehex_to_pixel_step_x + self.gamehex_to_pixel_step_y) / 2.0)
            inner_radius = outer_radius * 0.44
            points = self._star_points(cx, cy, outer_radius=outer_radius, inner_radius=inner_radius)
            points_str = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
            parts.append(
                f'<polygon points="{points_str}" fill="{fill_color}" stroke="{stroke_color}" '
                f'stroke-width="{max(0.6, self.line_w * 0.35):.2f}" stroke-linejoin="round"/>'
            )

        parts.append(self._svg_end())
        return "".join(parts)

    def render_hq_coverage_svg(self, hq_coverage_data: list[dict[str, Any]]) -> str:
        parts: list[str] = [self._svg_start()]
        parts.append(
            """
<defs>
  <pattern id="hq-crosshatch" patternUnits="userSpaceOnUse" width="14" height="14">
    <rect width="14" height="14" fill="rgb(46,132,69)" fill-opacity="0.16"/>
    <path d="M-2,14 L14,-2 M0,16 L16,0" stroke="rgb(98,220,120)" stroke-opacity="0.58" stroke-width="2"/>
    <path d="M-2,0 L14,16 M0,-2 L16,14" stroke="rgb(98,220,120)" stroke-opacity="0.58" stroke-width="2"/>
  </pattern>
</defs>
""".strip()
        )

        for feature in hq_coverage_data:
            center = feature.get("center")
            if not center:
                continue

            cx, cy = self.gamehex_to_hex_center(int(center[0]), int(center[1]))
            radius_hexes = float(feature.get("radius_hexes", 3))
            radius_px = radius_hexes * ((self.gamehex_to_pixel_step_x + self.gamehex_to_pixel_step_y) / 2.0)
            parts.append(
                f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{radius_px:.2f}" '
                f'fill="url(#hq-crosshatch)" stroke="rgba(88,220,108,0.98)" '
                f'stroke-width="{max(1.0, self.line_w * 0.75):.2f}"/>'
            )

        parts.append(self._svg_end())
        return "".join(parts)

    def render_area_command_svg(self, area_command_data: list[dict[str, Any]]) -> str:
        parts: list[str] = [self._svg_start()]
        parts.append(
            """
<defs>
  <pattern id="area-command-crosshatch" patternUnits="userSpaceOnUse" width="14" height="14">
    <rect width="14" height="14" fill="rgb(46,132,69)" fill-opacity="0.16"/>
    <path d="M-2,14 L14,-2 M0,16 L16,0" stroke="rgb(98,220,120)" stroke-opacity="0.58" stroke-width="2"/>
    <path d="M-2,0 L14,16 M0,-2 L16,14" stroke="rgb(98,220,120)" stroke-opacity="0.58" stroke-width="2"/>
  </pattern>
</defs>
""".strip()
        )

        for feature in area_command_data:
            polygon = feature.get("polygon")
            if not polygon or len(polygon) < 3:
                continue

            points = [self.gamehex_to_pixel(int(x), int(y)) for x, y in polygon]
            points_str = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
            parts.append(
                f'<polygon points="{points_str}" '
                f'fill="url(#area-command-crosshatch)" stroke="rgba(88,220,108,0.98)" '
                f'stroke-width="{max(1.0, self.line_w * 0.75):.2f}"/>'
            )

            label = escape(str(feature.get("name") or "").strip())
            if label:
                cx = sum(x for x, _ in points) / len(points)
                cy = sum(y for _, y in points) / len(points)
                parts.append(
                    f'<text x="{cx:.2f}" y="{cy:.2f}" text-anchor="middle" dominant-baseline="middle" '
                    f'font-family="Segoe UI, Arial, sans-serif" font-size="{self.font_size:.2f}" '
                    f'fill="rgba(255,255,255,0.96)" stroke="rgba(0,0,0,0.78)" '
                    f'stroke-width="{max(1.0, self.line_w * 0.5):.2f}" paint-order="stroke fill">{label}</text>'
                )

        parts.append(self._svg_end())
        return "".join(parts)

    def render_subpatrols_svg(self, subpatrols_data: list[dict[str, Any]]) -> str:
        parts: list[str] = [self._svg_start()]

        for feature in subpatrols_data:
            center = feature.get("center")
            if not center:
                continue
            radius_hexes = float(feature.get("radius_hexes", 1))
            # Circle centers are point-on-hex and must use hex center alignment.
            cx, cy = self.gamehex_to_hex_center(int(center[0]), int(center[1]))
            radius_px = radius_hexes * ((self.gamehex_to_pixel_step_x + self.gamehex_to_pixel_step_y) / 2.0)
            parts.append(
                f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{radius_px:.2f}" '
                f'fill="none" stroke="rgba(100,255,100,0.95)" '
                f'stroke-width="{self.line_w:.2f}"/>'
            )

        parts.append(self._svg_end())
        return "".join(parts)

    def render_threats_svg(self, threats_data: dict[str, Any]) -> str:
        parts: list[str] = [self._svg_start()]

        for key, default_fill, default_stroke in (
            ("sub", "rgba(255,70,70,0.32)", "rgba(255,70,70,0.92)"),
            ("surface", "rgba(255,220,60,0.30)", "rgba(255,220,60,0.94)"),
            ("carrier", "rgba(120,205,255,0.30)", "rgba(120,205,255,0.96)"),
        ):
            for feature in threats_data.get(key, []):
                center = feature.get("center")
                if not center:
                    continue
                radius_hexes = float(feature.get("radius_hexes", 1))
                cx, cy = self.gamehex_to_hex_center(int(center[0]), int(center[1]))
                radius_px = radius_hexes * ((self.gamehex_to_pixel_step_x + self.gamehex_to_pixel_step_y) / 2.0)
                fill_color = escape(str(feature.get("fill_color") or default_fill))
                stroke_color = escape(str(feature.get("stroke_color") or default_stroke))
                parts.append(
                    f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{radius_px:.2f}" '
                    f'fill="{fill_color}" stroke="{stroke_color}" '
                    f'stroke-width="{max(1.0, self.line_w * 0.75):.2f}"/>'
                )

        for feature in threats_data.get("areas", []):
            center = feature.get("center")
            if not center:
                continue
            size_hexes = float(feature.get("size_hexes", 1))
            cx, cy = self.gamehex_to_hex_center(int(center[0]), int(center[1]))
            arm = size_hexes * ((self.gamehex_to_pixel_step_x + self.gamehex_to_pixel_step_y) / 2.0)
            parts.append(
                f'<line x1="{(cx-arm):.2f}" y1="{(cy-arm):.2f}" x2="{(cx+arm):.2f}" y2="{(cy+arm):.2f}" '
                f'stroke="rgba(220,90,90,0.95)" stroke-width="{self.line_w:.2f}"/>'
            )
            parts.append(
                f'<line x1="{(cx+arm):.2f}" y1="{(cy-arm):.2f}" x2="{(cx-arm):.2f}" y2="{(cy+arm):.2f}" '
                f'stroke="rgba(220,90,90,0.95)" stroke-width="{self.line_w:.2f}"/>'
            )

        parts.append(self._svg_end())
        return "".join(parts)

    def render_air_mission_sectors_svg(self, mission_data: list[dict[str, Any]]) -> str:
        parts: list[str] = [self._svg_start()]
        parts.append(
            """
<defs>
  <pattern id="air-search-hatch" patternUnits="userSpaceOnUse" width="12" height="12">
    <rect width="12" height="12" fill="rgb(46,132,69)" fill-opacity="0.15"/>
    <path d="M-2,12 L12,-2 M0,14 L14,0" stroke="rgb(98,220,120)" stroke-opacity="0.62" stroke-width="2"/>
    <path d="M-2,0 L12,14 M0,-2 L14,12" stroke="rgb(98,220,120)" stroke-opacity="0.62" stroke-width="2"/>
  </pattern>
  <pattern id="air-asw-hatch" patternUnits="userSpaceOnUse" width="12" height="12">
    <rect width="12" height="12" fill="rgb(175,56,115)" fill-opacity="0.14"/>
    <path d="M-2,12 L12,-2 M0,14 L14,0" stroke="rgb(255,126,185)" stroke-opacity="0.64" stroke-width="2"/>
    <path d="M-2,0 L12,14 M0,-2 L14,12" stroke="rgb(255,126,185)" stroke-opacity="0.64" stroke-width="2"/>
  </pattern>
</defs>
""".strip()
        )

        for feature in mission_data:
            center = feature.get("center")
            if not center:
                continue

            cx, cy = self.gamehex_to_hex_center(int(center[0]), int(center[1]))
            radius_hexes = float(feature.get("radius_hexes") or 0.0)
            if radius_hexes <= 0:
                continue

            radius_px = radius_hexes * ((self.gamehex_to_pixel_step_x + self.gamehex_to_pixel_step_y) / 2.0)
            mission_kind = str(feature.get("mission_kind") or "").strip().lower()
            is_asw = mission_kind == "asw"
            hatch_id = "air-asw-hatch" if is_asw else "air-search-hatch"
            default_stroke = "rgba(255,126,185,0.96)" if is_asw else "rgba(88,220,108,0.95)"
            stroke_color = escape(str(feature.get("stroke_color") or default_stroke))
            start_degrees = float(feature.get("arc_start_degrees") or 0.0)
            end_degrees = float(feature.get("arc_end_degrees") or 0.0)
            is_full_circle = bool(feature.get("is_full_circle"))

            if is_full_circle:
                parts.append(
                    f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{radius_px:.2f}" '
                    f'fill="url(#{hatch_id})" stroke="{stroke_color}" '
                    f'stroke-width="{max(1.0, self.line_w * 0.5):.2f}"/>'
                )
                continue

            points = self._sector_polygon_points(cx, cy, radius_px, start_degrees, end_degrees)
            points_str = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
            parts.append(
                f'<polygon points="{points_str}" fill="url(#{hatch_id})" stroke="{stroke_color}" '
                f'stroke-width="{max(1.0, self.line_w * 0.5):.2f}" stroke-linejoin="round"/>'
            )

        parts.append(self._svg_end())
        return "".join(parts)

    def render_air_attack_ranges_svg(self, attack_data: list[dict[str, Any]]) -> str:
        parts: list[str] = [self._svg_start()]

        for feature in attack_data:
            center = feature.get("center")
            if not center:
                continue

            cx, cy = self.gamehex_to_hex_center(int(center[0]), int(center[1]))
            radius_hexes = float(feature.get("radius_hexes") or 0.0)
            if radius_hexes <= 0:
                continue

            radius_px = radius_hexes * ((self.gamehex_to_pixel_step_x + self.gamehex_to_pixel_step_y) / 2.0)
            stroke_color = escape(str(feature.get("stroke_color") or "rgba(88,220,108,0.96)"))
            dash_len = max(6.0, float(feature.get("dash_len") or self.dash_len))

            parts.append(
                f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{radius_px:.2f}" '
                f'fill="none" stroke="{stroke_color}" '
                f'stroke-width="{max(1.0, self.line_w * 0.75):.2f}" stroke-dasharray="{dash_len:.2f},{dash_len:.2f}"/>'
            )

        parts.append(self._svg_end())
        return "".join(parts)

    def render_minefields_svg(self, minefield_data: list[dict[str, Any]]) -> str:
        parts: list[str] = [self._svg_start()]
        arm_length = max(6.0, ((self.gamehex_to_pixel_step_x + self.gamehex_to_pixel_step_y) / 2.0) * 0.22)
        stroke_width = max(2.5, self.line_w)

        for feature in minefield_data:
            center = feature.get("center")
            if not center:
                continue

            cx, cy = self.gamehex_to_hex_center(int(center[0]), int(center[1]))
            stroke_color = escape(str(feature.get("stroke_color") or "rgba(0,0,0,0.96)"))

            parts.append(
                f'<line x1="{(cx-arm_length):.2f}" y1="{(cy-arm_length):.2f}" x2="{(cx+arm_length):.2f}" y2="{(cy+arm_length):.2f}" '
                f'stroke="{stroke_color}" stroke-width="{stroke_width:.2f}" stroke-linecap="round"/>'
            )
            parts.append(
                f'<line x1="{(cx+arm_length):.2f}" y1="{(cy-arm_length):.2f}" x2="{(cx-arm_length):.2f}" y2="{(cy+arm_length):.2f}" '
                f'stroke="{stroke_color}" stroke-width="{stroke_width:.2f}" stroke-linecap="round"/>'
            )

        parts.append(self._svg_end())
        return "".join(parts)

    @staticmethod
    def _star_points(cx: float, cy: float, outer_radius: float, inner_radius: float) -> list[tuple[float, float]]:
        points: list[tuple[float, float]] = []
        for i in range(10):
            angle = (i * 36.0 - 90.0) * math.pi / 180.0
            radius = outer_radius if i % 2 == 0 else inner_radius
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
        return points

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
