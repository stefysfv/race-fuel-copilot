"""Uploaded GPX: friendly errors, elevation repair, tracks, and waypoints.

Catalog races stay on `parse_gpx_xml`. These fixtures never rewrite a golden.
"""

from __future__ import annotations

import pytest

from backend.engine.entry import generate_plan_json, inspect_gpx_json
from backend.engine.gpx_course import (
    FLAT_ELE,
    NOT_XML,
    NO_COURSE,
    PARTIAL_ELE,
    TOO_BIG,
    build_upload,
    too_few_points,
    too_many_points,
)
from backend.gpx_parser import parse_gpx_xml
from backend.models import GPX_XML_MAX_CHARS

METERS_PER_DEG = 6_371_000.0 * 3.141592653589793 / 180.0


def _gpx(body: str, version: str = "1.1", xmlns: str | None = None) -> str:
    namespace = xmlns if xmlns is not None else f"http://www.topografix.com/GPX/1/{version[-1]}"
    return (
        f'<?xml version="1.0"?>'
        f'<gpx version="{version}" xmlns="{namespace}">{body}</gpx>'
    )


def _north_points(count: int, step_m: float = 20.0, elevation: float | None = 1000.0, start_lat: float = 42.5) -> str:
    rows = []
    lat = start_lat
    for index in range(count):
        ele = "" if elevation is None else f"<ele>{elevation + index}</ele>"
        rows.append(f'<trkpt lat="{lat:.6f}" lon="23.300000">{ele}</trkpt>')
        lat += step_m / METERS_PER_DEG
    return "".join(rows)


@pytest.fixture
def course_60() -> str:
    """GPX 1.1 track with 60 points and an elevation on every point."""

    return _gpx(f"<trk><name>Ridge</name><trkseg>{_north_points(60)}</trkseg></trk>")


@pytest.fixture
def course_60_v10() -> str:
    return _gpx(
        f"<trk><name>Old</name><trkseg>{_north_points(60)}</trkseg></trk>",
        version="1.0",
        xmlns="http://www.topografix.com/GPX/1/0",
    )


@pytest.fixture
def route_only() -> str:
    points = _north_points(60).replace("trkpt", "rtept")
    return _gpx(f"<rte><name>Road</name>{points}</rte>")


@pytest.fixture
def not_xml() -> str:
    return "<gpx><trk><trkpt></gpx>"


@pytest.fixture
def no_points() -> str:
    return _gpx("<metadata><name>Empty</name></metadata>")


@pytest.fixture
def ten_points() -> str:
    return _gpx(f"<trk><trkseg>{_north_points(10)}</trkseg></trk>")


@pytest.fixture
def two_tracks() -> str:
    short = f"<trk><name>Spur</name><trkseg>{_north_points(60, step_m=10)}</trkseg></trk>"
    long = f"<trk><name>Main</name><trkseg>{_north_points(80, step_m=25)}</trkseg></trk>"
    return _gpx(short + long)


@pytest.fixture
def split_segments() -> str:
    """Two segments of one track with a 500 m hole between them."""

    first = _north_points(30, step_m=20)
    second = _north_points(30, step_m=20, start_lat=42.5 + (30 * 20 + 500) / METERS_PER_DEG)
    return _gpx(f"<trk><name>Broken</name><trkseg>{first}</trkseg><trkseg>{second}</trkseg></trk>")


@pytest.fixture
def missing_elevation() -> str:
    return _gpx(f"<trk><trkseg>{_north_points(60, elevation=None)}</trkseg></trk>")


@pytest.fixture
def partial_elevation() -> str:
    """One interior hole of two points, 20 m apart, between known heights."""

    rows = []
    lat = 42.5
    for index in range(60):
        if index in {20, 21}:
            ele = ""
        else:
            ele = f"<ele>{1000 + index}</ele>"
        rows.append(f'<trkpt lat="{lat:.6f}" lon="23.3">{ele}</trkpt>')
        lat += 10 / METERS_PER_DEG
    return _gpx(f"<trk><trkseg>{''.join(rows)}</trkseg></trk>")


@pytest.fixture
def long_elevation_gap() -> str:
    rows = []
    lat = 42.5
    for index in range(60):
        ele = "" if 10 <= index <= 25 else f"<ele>{1500}</ele>"
        rows.append(f'<trkpt lat="{lat:.6f}" lon="23.3">{ele}</trkpt>')
        lat += 20 / METERS_PER_DEG
    return _gpx(f"<trk><trkseg>{''.join(rows)}</trkseg></trk>")


def test_not_xml_is_a_friendly_error(not_xml: str) -> None:
    with pytest.raises(ValueError, match=NOT_XML):
        build_upload(not_xml)


def test_no_track_or_route_is_a_friendly_error(no_points: str) -> None:
    with pytest.raises(ValueError, match=NO_COURSE):
        build_upload(no_points)


def test_fewer_than_50_points_is_rejected(ten_points: str) -> None:
    with pytest.raises(ValueError, match=too_few_points(10)):
        build_upload(ten_points)


def test_gpx_1_1_track_is_accepted(course_60: str) -> None:
    report = inspect_gpx_json({"gpx_track": "longest"}, {"races": []}, course_60)
    assert report["distance_km"] > 1
    assert report["tracks"][0]["name"] == "Ridge"
    assert report["tracks"][0]["points"] == 60


def test_gpx_1_0_track_is_accepted(course_60_v10: str) -> None:
    report = inspect_gpx_json({}, {"races": []}, course_60_v10)
    assert report["distance_km"] > 1
    assert report["highest_m"] >= 1000


def test_route_only_is_accepted_when_there_is_no_track(route_only: str) -> None:
    report = inspect_gpx_json({}, {"races": []}, route_only)
    assert report["tracks"][0]["kind"] == "route"
    assert report["tracks"][0]["name"] == "Road"
    assert report["distance_km"] > 1


def test_multiple_tracks_default_to_the_longest_and_can_merge(two_tracks: str) -> None:
    report = inspect_gpx_json({}, {"races": []}, two_tracks)
    assert [item["name"] for item in report["tracks"]] == ["Spur", "Main"]
    assert report["selected"] == 1
    merged = inspect_gpx_json({"gpx_track": "merge"}, {"races": []}, two_tracks)
    assert merged["selected"] == "merge"
    assert merged["distance_km"] > report["distance_km"]


def test_segment_gap_over_200_m_warns(split_segments: str) -> None:
    report = inspect_gpx_json({}, {"races": []}, split_segments)
    assert any("jumps" in note for note in report["warnings"])


def test_oversized_text_is_rejected() -> None:
    with pytest.raises(ValueError, match=TOO_BIG):
        build_upload("x" * (GPX_XML_MAX_CHARS + 1))


def test_more_than_80000_raw_points_is_rejected() -> None:
    body = _north_points(80_001, step_m=5)
    with pytest.raises(ValueError, match=too_many_points(80_001)):
        build_upload(_gpx(f"<trk><trkseg>{body}</trkseg></trk>"))


def test_missing_elevation_is_flat_and_warns(missing_elevation: str) -> None:
    report = inspect_gpx_json({}, {"races": []}, missing_elevation)
    assert FLAT_ELE in report["warnings"]
    assert report["elevation_gain_m"] == 0


def test_small_elevation_gap_is_filled(partial_elevation: str) -> None:
    ready = build_upload(partial_elevation)
    assert any(note.startswith("Elevation was missing on 2 points") for note in ready.warnings)
    assert PARTIAL_ELE.format(count=2) in ready.warnings
    # Index 20 sits one 20 m step into a 40 m hole between 1019 and 1022.
    assert ready.profile[20].elevation_m == pytest.approx(1020.0, abs=0.2)


def test_long_elevation_gap_holds_the_last_known_height(long_elevation_gap: str) -> None:
    ready = build_upload(long_elevation_gap)
    assert PARTIAL_ELE.format(count=16) in ready.warnings
    assert ready.profile[18].elevation_m == pytest.approx(1500.0)


def test_dense_upload_is_thinned_and_catalog_reader_is_not() -> None:
    dense = _gpx(f"<trk><trkseg>{_north_points(400, step_m=2)}</trkseg></trk>")
    raw = parse_gpx_xml(dense)
    ready = build_upload(dense)
    assert len(raw) == 400
    assert len(ready.profile) < len(raw)
    assert len(ready.profile) > 10


def test_wide_course_is_not_thinned_so_custom_goldens_keep_their_points() -> None:
    wide = _gpx(f"<trk><trkseg>{_north_points(80, step_m=50, elevation=1500)}</trkseg></trk>")
    assert [(point.lat, point.lon, point.elevation_m) for point in build_upload(wide).profile] == [
        (point.lat, point.lon, point.elevation_m) for point in parse_gpx_xml(wide)
    ]


def test_distance_override_scales_the_plan(course_60: str) -> None:
    measured = build_upload(course_60).distance_km
    plan = generate_plan_json(
        {"gpx_xml": course_60, "distance_override_km": 10, "athlete": {"target_time_minutes": 300}},
        {"races": []},
        course_60,
    )
    assert measured != pytest.approx(10)
    assert plan["distance_km"] == pytest.approx(10, abs=0.05)
    assert plan["declared_distance_km"] == pytest.approx(10)


def _aid_file(points: str, waypoints: str) -> str:
    return _gpx(f"{waypoints}<trk><name>Loop</name><trkseg>{points}</trkseg></trk>")


def test_waypoint_codes_match_and_prose_as_does_not() -> None:
    start = 42.5
    step = 20 / METERS_PER_DEG
    as_lat = start + 5 * step
    hut_lat = start + 20 * step
    pp_lat = start + 40 * step
    waypoints = (
        f'<wpt lat="{as_lat:.6f}" lon="23.3"><name>AS1</name></wpt>'
        f'<wpt lat="{hut_lat:.6f}" lon="23.3"><name>Hut</name><cmt>CP2 water</cmt></wpt>'
        f'<wpt lat="{pp_lat:.6f}" lon="23.3"><name>ПП1</name></wpt>'
        f'<wpt lat="{start:.6f}" lon="23.3"><name>Ridge</name><cmt>as we climb the ridge</cmt></wpt>'
        f'<wpt lat="{start + 2 * step:.6f}" lon="23.3"><name>Старт Финал</name></wpt>'
    )
    report = inspect_gpx_json({}, {"races": []}, _aid_file(_north_points(60), waypoints))
    names = [item["name"] for item in report["waypoints"]]
    assert "AS1" in names
    assert "Hut" in names
    assert "ПП1" in names
    assert "Ridge" not in names
    assert "Старт Финал" not in names
    assert not any("Ridge" in note or "Старт" in note for note in report["warnings"])
    as1 = next(item for item in report["waypoints"] if item["name"] == "AS1")
    assert as1["has_food"] is True
    hut = next(item for item in report["waypoints"] if item["name"] == "Hut")
    assert hut["has_food"] is False
    assert hut["has_water"] is True


def test_off_course_duplicate_and_cap_warn_only_for_matched_waypoints() -> None:
    points = _north_points(220, step_m=40)
    step = 40 / METERS_PER_DEG
    on_lat = 42.5 + 8 * step
    near_lat = 42.5 + 9 * step
    later = 42.5 + 30 * step
    waypoints = [
        f'<wpt lat="{on_lat:.6f}" lon="23.3"><name>Aid 1</name></wpt>',
        f'<wpt lat="{near_lat:.6f}" lon="23.3"><name>Aid 2</name></wpt>',
        f'<wpt lat="{on_lat:.6f}" lon="23.36"><name>Aid Far</name></wpt>',
        f'<wpt lat="{later:.6f}" lon="23.3"><name>Photo</name></wpt>',
    ]
    for index in range(41):
        lat = 42.5 + (40 + index * 4) * step
        waypoints.append(f'<wpt lat="{lat:.6f}" lon="23.3"><name>CP{index}</name></wpt>')
    report = inspect_gpx_json({}, {"races": []}, _aid_file(points, "".join(waypoints)))
    notes = report["warnings"]
    assert any("Aid Far" in note and "off the track" in note for note in notes)
    assert any("Aid 2" in note and "within 100 m" in note for note in notes)
    assert any("40 stations" in note for note in notes)
    assert not any("Photo" in note for note in notes)
    assert len(report["waypoints"]) == 40


def test_implausible_gain_is_flagged() -> None:
    rows = []
    lat = 42.5
    for index in range(60):
        rows.append(f'<trkpt lat="{lat:.6f}" lon="23.3"><ele>{1000 + index * 30}</ele></trkpt>')
        lat += 20 / METERS_PER_DEG
    report = inspect_gpx_json({}, {"races": []}, _gpx(f"<trk><trkseg>{''.join(rows)}</trkseg></trk>"))
    assert any("unusually steep" in note for note in report["warnings"])
    assert report["profile"]
    assert report["highest_m"] > report["profile"][0]["ele"]
