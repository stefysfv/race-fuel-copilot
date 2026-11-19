"""FastAPI entry point for the fueling co-pilot.

Routes:
- GET  /api/races           stored races and aid stations (GPX is loaded on selection)
- POST /api/generate-plan   build a plan from a race id or a GPX upload
- POST /api/preview-zones   classify the course only, for the red-share readout
- POST /api/recalibrate     snap a live GPS fix and rebuild the remaining plan
- GET  /                   the mobile PWA in `frontend/`

Run from the project root:

    uvicorn backend.main:app --host 0.0.0.0 --port 8000

`0.0.0.0` is what lets a phone on the same Wi-Fi open the app. API routes are
registered before the static mount, so `/api/...` is never swallowed by the PWA.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

if __name__ == "__main__" and __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from backend.adapters.catalog import get_race, load_race_catalog, load_race_catalog_dict
from backend.adapters.gpx_files import read_gpx_text
from backend.engine.entry import generate_plan_json, inspect_gpx_json, preview_zones_json, recalibrate_json
from backend.models import (
    GeneratePlanRequest,
    GPX_XML_MAX_CHARS,
    RaceCatalog,
    RacePlan,
    RecalibrateRequest,
    RecalibrateResponse,
    ZonePreview,
)


def allowed_origins() -> list[str]:
    """Origins that may call this API from another site.

    `ALLOWED_ORIGINS` is a comma-separated list. Unset or blank allows none.
    The PWA is served by this same process, so local dev and Render need no entry.
    """

    raw = os.environ.get("ALLOWED_ORIGINS", "")
    return [part.strip() for part in raw.split(",") if part.strip()]


app = FastAPI(
    title="Trail & Ultra Race Fueling Co-Pilot",
    version="0.1.0",
    summary="Terrain-aware carbohydrate, fluid, and sodium plans for trail races.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
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


class GpxInspectBody(BaseModel):
    """A GPX upload to sanity-check. The plan is a separate request."""

    model_config = ConfigDict(extra="forbid")
    gpx_xml: str = Field(min_length=1, max_length=GPX_XML_MAX_CHARS)
    gpx_track: str = Field(default="longest", max_length=20)


@app.post("/api/inspect-gpx")
def inspect_gpx(body: GpxInspectBody) -> dict:
    """Distance, elevation, and aid waypoints for an upload. No fuel plan."""

    try:
        return inspect_gpx_json({"gpx_track": body.gpx_track}, {"races": []}, body.gpx_xml)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _gpx_text(body: GeneratePlanRequest | RecalibrateRequest) -> str | None:
    """Read the catalog GPX, or pass through an upload. The engine only sees text."""

    if body.race_id and body.race_id.strip():
        race = get_race(body.race_id)
        if len(race.profile) >= 2 or not race.gpx_file:
            return None
        return read_gpx_text(race.gpx_file)
    return body.gpx_xml


def _unknown_race(exc: ValueError, race_id: str | None) -> HTTPException | None:
    """Keep the old 404 when the catalog has no such race."""

    if race_id and str(exc).startswith(f"Unknown race_id '{race_id}'"):
        return HTTPException(status_code=404, detail=str(exc))
    return None


@app.post("/api/preview-zones", response_model=ZonePreview)
def preview_zones(body: GeneratePlanRequest) -> ZonePreview:
    """Classify the course. Sliders use this instead of building a full plan."""

    try:
        payload = preview_zones_json(body.model_dump(mode="json"), load_race_catalog_dict(), _gpx_text(body))
        return ZonePreview.model_validate(payload)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown race_id '{body.race_id}'") from None
    except ValueError as exc:
        missing = _unknown_race(exc, body.race_id)
        if missing is not None:
            raise missing from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/generate-plan", response_model=RacePlan)
def create_plan(body: GeneratePlanRequest) -> RacePlan:
    """Build a full-race fueling plan."""

    try:
        payload = generate_plan_json(body.model_dump(mode="json"), load_race_catalog_dict(), _gpx_text(body))
        return RacePlan.model_validate(payload)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown race_id '{body.race_id}'") from None
    except ValueError as exc:
        missing = _unknown_race(exc, body.race_id)
        if missing is not None:
            raise missing from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/recalibrate", response_model=RecalibrateResponse)
def recalibrate_plan(body: RecalibrateRequest) -> RecalibrateResponse:
    """Snap a GPS fix to the track and return the plan for the ground still ahead."""

    try:
        payload = recalibrate_json(body.model_dump(mode="json"), load_race_catalog_dict(), _gpx_text(body))
        return RecalibrateResponse.model_validate(payload)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown race_id '{body.race_id}'") from None
    except ValueError as exc:
        missing = _unknown_race(exc, body.race_id)
        if missing is not None:
            raise missing from exc
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
