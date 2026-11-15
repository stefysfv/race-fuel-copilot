"""FastAPI entry point for the fueling co-pilot.

Routes:
- GET  /api/races           stored races and aid stations (GPX is loaded on selection)
- POST /api/generate-plan   build a plan from a race id or a GPX upload
- POST /api/recalibrate     snap a live GPS fix and rebuild the remaining plan
- GET  /                   the mobile PWA in `frontend/`

Run from the project root:

    uvicorn backend.main:app --host 0.0.0.0 --port 8000

`0.0.0.0` is what lets a phone on the same Wi-Fi open the app. API routes are
registered before the static mount, so `/api/...` is never swallowed by the PWA.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.fuel_engine import generate_plan, generate_plan_for_race, get_race, load_race_catalog, recalibrate
from backend.gpx_parser import attach_track, parse_gpx_xml
from backend.models import GeneratePlanRequest, Race, RaceCatalog, RacePlan, RecalibrateRequest, RecalibrateResponse

app = FastAPI(
    title="Trail & Ultra Race Fueling Co-Pilot",
    version="0.1.0",
    summary="Terrain-aware carbohydrate, fluid, and sodium plans for trail races.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/races", response_model=RaceCatalog)
def list_races() -> RaceCatalog:
    """Return every stored race and its aid stations.

    The GPX track is not inlined here. It is read from `gpx_file` when a plan
    is built, so the phone is not sent every raw coordinate up front.
    """

    return RaceCatalog(races=load_race_catalog())


def _race_with_track(race_id: str) -> Race:
    """Load one catalog race and slice-ready points from its GPX file."""

    return attach_track(get_race(race_id))


@app.post("/api/generate-plan", response_model=RacePlan)
def create_plan(body: GeneratePlanRequest) -> RacePlan:
    """Build a full-race fueling plan."""

    try:
        if body.race_id:
            return generate_plan_for_race(_race_with_track(body.race_id), body.athlete)
        profile = parse_gpx_xml(body.gpx_xml or "")
        distance_km = 0.0
        return generate_plan(
            race_id="uploaded",
            race_name="Uploaded GPX",
            declared_distance_km=distance_km,
            aid_stations=[],
            profile=profile,
            athlete=body.athlete,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown race_id '{body.race_id}'") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/recalibrate", response_model=RecalibrateResponse)
def recalibrate_plan(body: RecalibrateRequest) -> RecalibrateResponse:
    """Snap a GPS fix to the track and return the plan for the ground still ahead."""

    try:
        if body.race_id:
            race = _race_with_track(body.race_id)
            return recalibrate(
                race_id=race.id,
                race_name=race.name,
                declared_distance_km=race.distance_km,
                aid_stations=race.aid_stations,
                profile=race.profile,
                athlete=body.athlete,
                latitude=body.latitude,
                longitude=body.longitude,
                elapsed_min=body.elapsed_min,
                gi_rescue=body.gi_rescue,
            )
        profile = parse_gpx_xml(body.gpx_xml or "")
        return recalibrate(
            race_id="uploaded",
            race_name="Uploaded GPX",
            declared_distance_km=0.0,
            aid_stations=[],
            profile=profile,
            athlete=body.athlete,
            latitude=body.latitude,
            longitude=body.longitude,
            elapsed_min=body.elapsed_min,
            gi_rescue=body.gi_rescue,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown race_id '{body.race_id}'") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class FrontendFiles(StaticFiles):
    """PWA files. The shell is not cached for long, so a reload sees new JS."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if path in {"", "index.html", "sw.js", "manifest.json", "app.js"}:
            response.headers["Cache-Control"] = "no-cache"
        return response


def _mount_frontend() -> None:
    """Serve `frontend/` at `/` once the PWA shell exists."""

    frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
    if (frontend_dir / "index.html").is_file():
        app.mount("/", FrontendFiles(directory=frontend_dir, html=True), name="frontend")


_mount_frontend()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
