"""Quarterly, content-addressed RailGo route geometry assets."""

from __future__ import annotations

import hashlib
import html
import json
import math
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Sequence, Tuple

from agent.psw import AgentState
from tools.rail.rail_store import RailStore, railstore
from tools.rail.railgo_client import RailGoContractError, RailGoTemporaryError, fetch_map_line_v2


Point = Tuple[float, float]
_MAX_DISPLAY_POINTS = 2_000


def _quarter(value: datetime | None = None) -> str:
    value = value or datetime.now()
    return f"{value.year}-Q{((value.month - 1) // 3) + 1}"


def _path_fingerprint(store: RailStore, train: str) -> str:
    payload = store.get_path(train) or {}
    body = payload.get("train") if isinstance(payload.get("train"), dict) else payload
    timetable = body.get("timetable") if isinstance(body, dict) else []
    stations = []
    for stop in timetable or []:
        if isinstance(stop, dict):
            stations.append(str(stop.get("stationName") or stop.get("station") or stop.get("name") or ""))
    numbers = body.get("numberFull") if isinstance(body, dict) else []
    raw = json.dumps({"numbers": numbers or [train], "stations": stations}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24] if stations else ""


def _valid_point(value: Any) -> Point | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        lon, lat = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    if not (70 <= lon <= 140 and 10 <= lat <= 60):
        return None
    return lon, lat


def _transform_lat(x: float, y: float) -> float:
    result = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    result += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    result += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    result += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return result


def _transform_lon(x: float, y: float) -> float:
    result = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    result += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    result += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    result += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return result


def gcj02_to_wgs84(lon: float, lat: float) -> Point:
    a, ee = 6378245.0, 0.00669342162296594323
    dlat = _transform_lat(lon - 105.0, lat - 35.0)
    dlon = _transform_lon(lon - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - ee * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * math.pi)
    dlon = (dlon * 180.0) / (a / sqrtmagic * math.cos(radlat) * math.pi)
    return lon * 2 - (lon + dlon), lat * 2 - (lat + dlat)


def _distance_to_segment(point: Point, start: Point, end: Point) -> float:
    x, y = point
    x1, y1 = start
    x2, y2 = end
    if start == end:
        return math.hypot(x - x1, y - y1)
    t = max(0.0, min(1.0, ((x - x1) * (x2 - x1) + (y - y1) * (y2 - y1)) / ((x2 - x1) ** 2 + (y2 - y1) ** 2)))
    return math.hypot(x - (x1 + t * (x2 - x1)), y - (y1 + t * (y2 - y1)))


def _douglas_peucker(points: Sequence[Point], epsilon: float) -> List[Point]:
    if len(points) <= 2:
        return list(points)
    best_distance, best_index = 0.0, 0
    for index in range(1, len(points) - 1):
        distance = _distance_to_segment(points[index], points[0], points[-1])
        if distance > best_distance:
            best_distance, best_index = distance, index
    if best_distance <= epsilon:
        return [points[0], points[-1]]
    left = _douglas_peucker(points[: best_index + 1], epsilon)
    right = _douglas_peucker(points[best_index:], epsilon)
    return left[:-1] + right


def simplify_segments(segments: List[Dict[str, Any]], maximum: int = _MAX_DISPLAY_POINTS) -> List[Dict[str, Any]]:
    total = sum(len(item["points"]) for item in segments)
    if total <= maximum:
        return segments
    epsilon = 0.0005
    simplified = segments
    for _ in range(12):
        simplified = [{**item, "points": _douglas_peucker(item["points"], epsilon)} for item in segments]
        if sum(len(item["points"]) for item in simplified) <= maximum:
            return simplified
        epsilon *= 1.75
    return simplified


def _haversine(a: Point, b: Point) -> float:
    radius = 6371.0088
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlon, dlat = lon2 - lon1, lat2 - lat1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return radius * 2 * math.asin(math.sqrt(value))


def _svg(points: Sequence[Point], train: str) -> str:
    if not points:
        return ""
    width, height, pad = 900, 360, 28
    lons, lats = [p[0] for p in points], [p[1] for p in points]
    min_lon, max_lon, min_lat, max_lat = min(lons), max(lons), min(lats), max(lats)
    lon_span, lat_span = max(max_lon - min_lon, 0.001), max(max_lat - min_lat, 0.001)
    plotted = [
        (pad + (lon - min_lon) / lon_span * (width - pad * 2), height - pad - (lat - min_lat) / lat_span * (height - pad * 2))
        for lon, lat in points
    ]
    path = " ".join(("M" if i == 0 else "L") + f" {x:.1f} {y:.1f}" for i, (x, y) in enumerate(plotted))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{html.escape(train)} route fallback"><rect width="100%" height="100%" rx="22" fill="#f4f7f2"/>'
        f'<path d="{path}" fill="none" stroke="#176b4d" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="{plotted[0][0]:.1f}" cy="{plotted[0][1]:.1f}" r="8" fill="#e4512b"/>'
        f'<circle cx="{plotted[-1][0]:.1f}" cy="{plotted[-1][1]:.1f}" r="8" fill="#e4512b"/>'
        f'<text x="28" y="32" font-size="17" font-family="sans-serif" fill="#18342a">{html.escape(train)} · offline route</text></svg>'
    )


def parse_map_payload(data: Dict[str, Any], train: str) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise RailGoContractError("route map data is not an object")
    stations = []
    for item in data.get("stations") or []:
        if not isinstance(item, dict):
            continue
        for name, value in item.items():
            point = _valid_point(value)
            if point:
                stations.append({"name": str(name), "gcj02": point, "wgs84": gcj02_to_wgs84(*point)})

    segments = []
    for name, segment in (data.get("train") or {}).items():
        if not isinstance(segment, dict):
            continue
        points = [point for raw in (segment.get("line") or []) if (point := _valid_point(raw))]
        if len(points) >= 2:
            try:
                index = int(segment.get("index"))
            except (TypeError, ValueError):
                index = 10**9
            segments.append({"name": str(name), "index": index, "points": points})
    segments.sort(key=lambda item: item["index"])
    if not segments:
        raise RailGoContractError("route map has no valid line segments")
    raw_point_count = sum(len(item["points"]) for item in segments)
    segments = simplify_segments(segments)
    display_point_count = sum(len(item["points"]) for item in segments)

    all_gcj: List[Point] = []
    features = []
    for segment in segments:
        all_gcj.extend(segment["points"])
        features.append(
            {
                "type": "Feature",
                "properties": {"name": segment["name"], "index": segment["index"]},
                "geometry": {"type": "LineString", "coordinates": [gcj02_to_wgs84(*point) for point in segment["points"]]},
            }
        )
    for station in stations:
        features.append(
            {
                "type": "Feature",
                "properties": {"name": station["name"], "kind": "station"},
                "geometry": {"type": "Point", "coordinates": station["wgs84"]},
            }
        )
    polyline_km = sum(_haversine(a, b) for item in segments for a, b in zip(item["points"], item["points"][1:]))
    direct_km = _haversine(all_gcj[0], all_gcj[-1]) if len(all_gcj) >= 2 else 0.0
    summary = {
        "train": train,
        "coordinate_source": "RailGo GCJ-02; converted to WGS-84 for OSM display",
        "station_count": len(stations),
        "stations": [item["name"] for item in stations],
        "segment_count": len(segments),
        "segments": [item["name"] for item in segments],
        "raw_point_count": raw_point_count,
        "display_point_count": display_point_count,
        "estimated_polyline_km": round(polyline_km, 1),
        "estimated_direct_km": round(direct_km, 1),
        "estimated_tortuosity": round(polyline_km / direct_km, 3) if direct_km else None,
        "distance_note": "Coordinate estimate, not railway operating mileage.",
    }
    geojson = {"type": "FeatureCollection", "features": features}
    digest = hashlib.sha256(json.dumps(geojson, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "content_hash": digest,
        "geojson": geojson,
        "summary": summary,
        "raw_metadata": {"coordinate_system": "GCJ-02", "raw_point_count": raw_point_count},
        "fallback_svg": _svg([gcj02_to_wgs84(*point) for point in all_gcj], train),
    }


class RouteAssetService:
    def __init__(self, store: RailStore = railstore, fetcher: Callable[[str], Dict[str, Any]] = fetch_map_line_v2):
        self.store = store
        self.fetcher = fetcher

    def get_route(self, train: str, psw: Any = None) -> Dict[str, Any]:
        train = str(train or "").strip().upper()
        quarter = _quarter()
        path_fingerprint = _path_fingerprint(self.store, train)
        cached = self.store.get_map_line_cache(train)
        fingerprint_matches = not path_fingerprint or not (cached or {}).get("path_fingerprint") or cached.get("path_fingerprint") == path_fingerprint
        if cached and cached.get("certificate_quarter") == quarter and fingerprint_matches:
            asset = self.store.get_route_asset(cached["content_hash"])
            if asset:
                if psw:
                    psw.set_state(AgentState.ROUTE_ASSET_READY, f"quarterly route asset hit {train}")
                return self._result(train, asset, cached.get("source_json") or {}, "fresh")
        try:
            payload = self.fetcher(train)
            data = payload.get("data") if isinstance(payload, dict) else None
            parsed = parse_map_payload(data if isinstance(data, dict) else {}, train)
        except RailGoTemporaryError:
            if cached:
                asset = self.store.get_route_asset(cached["content_hash"])
                if asset:
                    return self._result(train, asset, cached.get("source_json") or {}, "stale")
            raise
        source = payload.get("_railgo") if isinstance(payload, dict) else {}
        self.store.save_route_asset(
            train, quarter, path_fingerprint, parsed["content_hash"], parsed["geojson"],
            parsed["summary"], parsed["raw_metadata"], parsed["fallback_svg"], source,
        )
        asset = self.store.get_route_asset(parsed["content_hash"])
        if not asset:
            raise RailGoContractError("route asset could not be stored")
        if psw:
            psw.set_state(AgentState.ROUTE_ASSET_READY, f"route asset ready {train}:{parsed['content_hash'][:12]}")
        return self._result(train, asset, source or {}, "network")

    @staticmethod
    def _result(train: str, asset: Dict[str, Any], source: Dict[str, Any], cache_status: str) -> Dict[str, Any]:
        summary = dict(asset.get("summary_json") or {})
        return {
            "evidence": summary,
            "source": source,
            "cache_status": cache_status,
            "artifacts": [{
                "type": "route_map",
                "asset_id": asset["content_hash"],
                "caption": f"{train} 交互线路图",
                "source": "RailGo + OpenStreetMap",
                "summary": summary,
            }],
        }


route_asset_service = RouteAssetService()


__all__ = ["RouteAssetService", "gcj02_to_wgs84", "parse_map_payload", "route_asset_service", "simplify_segments"]
