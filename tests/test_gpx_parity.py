"""The ElementTree reader must match gpxpy on the bundled courses.

This comparison runs only when gpxpy is installed. It was the gate before
gpxpy left requirements.txt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.gpx_parser import parse_gpx_xml
from backend.models import TrackPoint

gpxpy = pytest.importorskip("gpxpy")

GPX_DIR = Path(__file__).resolve().parents[1] / "frontend" / "data" / "gpx"
BUNDLED = (
    "salomon-vitosha-12.gpx",
    "salomon-vitosha-27.gpx",
    "salomon-vitosha-40.gpx",
    "vitosha-100.gpx",
)


def _gpxpy_points(xml_text: str) -> list[tuple[float, float, float]]:
    """The previous parser: every track point, or route points when there is no track."""

    gpx = gpxpy.parse(xml_text)
    raw = [point for track in gpx.tracks for segment in track.segments for point in segment.points]
    if not raw:
        raw = [point for route in gpx.routes for point in route.points]
    return [
        (point.latitude, point.longitude, 0.0 if point.elevation is None else float(point.elevation))
        for point in raw
    ]


def _ours(xml_text: str) -> list[tuple[float, float, float]]:
    points = parse_gpx_xml(xml_text)
    return [(point.lat, point.lon, point.elevation_m) for point in points]


def test_bundled_gpx_files_match_gpxpy() -> None:
    for name in BUNDLED:
        xml_text = (GPX_DIR / name).read_text(encoding="utf-8")
        previous = _gpxpy_points(xml_text)
        current = _ours(xml_text)
        assert len(current) == len(previous), name
        for index, (left, right) in enumerate(zip(previous, current)):
            assert left == right, f"{name} point {index}"


@pytest.mark.parametrize(
    "xml_text",
    [
        """<?xml version="1.0"?>
        <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
          <trk><trkseg>
            <trkpt lat="42.5" lon="23.3"><ele>100</ele></trkpt>
            <trkpt lat="42.6" lon="23.4"></trkpt>
          </trkseg></trk>
          <rte><rtept lat="1" lon="2"><ele>9</ele></rtept><rtept lat="3" lon="4"><ele>8</ele></rtept></rte>
        </gpx>""",
        """<?xml version="1.0"?>
        <gpx version="1.0" xmlns="http://www.topografix.com/GPX/1/0">
          <trk><trkseg>
            <trkpt lat="42.1" lon="23.1"><ele>10.5</ele></trkpt>
            <trkpt lat="42.2" lon="23.2"><ele></ele></trkpt>
          </trkseg></trk>
        </gpx>""",
        """<?xml version="1.0"?>
        <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
          <rte>
            <rtept lat="42.0" lon="23.0"></rtept>
            <rtept lat="42.1" lon="23.1"><ele>4</ele></rtept>
          </rte>
        </gpx>""",
    ],
)
def test_namespace_route_and_missing_elevation_match_gpxpy(xml_text: str) -> None:
    assert _ours(xml_text) == _gpxpy_points(xml_text)


def test_missing_elevation_is_zero_without_gpxpy() -> None:
    points = parse_gpx_xml(
        """<?xml version="1.0"?>
        <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
          <trk><trkseg>
            <trkpt lat="42.5" lon="23.3"></trkpt>
            <trkpt lat="42.6" lon="23.4"><ele></ele></trkpt>
          </trkseg></trk>
        </gpx>"""
    )
    assert points == [
        TrackPoint(lat=42.5, lon=23.3, elevation_m=0.0),
        TrackPoint(lat=42.6, lon=23.4, elevation_m=0.0),
    ]
