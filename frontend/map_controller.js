/** Leaflet course map. The elevation chart lives in elevation_chart.js. */

import { ZONE, drawElevationChart } from "./elevation_chart.js";
import { shortWhy } from "./reason_strings.js";

const TILES = "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png";
const ATTRIBUTION = "© OpenTopoMap · OSM";

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[character]));
}

/** Point and segment at a distance along the planned slices. */
export function positionOnCourse(segments, distanceM) {
  if (!segments?.length) {
    return null;
  }
  const last = segments[segments.length - 1];
  const segment = segments.find((item) => distanceM >= item.start_distance_m && distanceM <= item.end_distance_m)
    || (distanceM > last.end_distance_m ? last : segments[0]);
  const span = segment.end_distance_m - segment.start_distance_m;
  const fraction = span <= 0 ? 0 : Math.min(1, Math.max(0, (distanceM - segment.start_distance_m) / span));
  return {
    latitude: segment.start_lat + (segment.end_lat - segment.start_lat) * fraction,
    longitude: segment.start_lon + (segment.end_lon - segment.start_lon) * fraction,
    distanceM: segment.start_distance_m + fraction * segment.distance_m,
    segment,
  };
}

export function createMapView(mapElement, chartCanvas) {
  const map = L.map(mapElement, {
    zoomControl: true,
    attributionControl: true,
    dragging: true,
    touchZoom: true,
  });
  map.getContainer().style.touchAction = "none";
  map.attributionControl.setPrefix(false);
  const tileLayer = L.tileLayer(TILES, { maxZoom: 17, attribution: ATTRIBUTION });
  let tilesEnabled = false;
  function setTilesEnabled(enabled) {
    const want = Boolean(enabled);
    if (want === tilesEnabled) return;
    tilesEnabled = want;
    if (want) tileLayer.addTo(map);
    else tileLayer.removeFrom(map);
  }
  setTilesEnabled(typeof navigator === "undefined" || navigator.onLine !== false);
  map.setView([42.56, 23.29], 12);

  let realMarker = null;
  const routeLayer = L.layerGroup().addTo(map);
  const aidLayer = L.layerGroup().addTo(map);
  const fuelLayer = L.layerGroup().addTo(map);
  let runner = null;
  let chart = null;
  let lastBounds = null;

  function setCourse({ segments, aidStations, fuelStops, forfeited }) {
    routeLayer.clearLayers();
    aidLayer.clearLayers();
    fuelLayer.clearLayers();
    if (!segments?.length) {
      return;
    }

    const bounds = [];
    for (const segment of segments) {
      const latLngs = [
        [segment.start_lat, segment.start_lon],
        [segment.end_lat, segment.end_lon],
      ];
      bounds.push(latLngs[0], latLngs[1]);
      L.polyline(latLngs, {
        color: ZONE[segment.zone] || ZONE.green,
        weight: 5,
        opacity: 0.95,
      }).addTo(routeLayer);
    }

    for (const station of aidStations || []) {
      L.circleMarker([station.lat, station.lon], {
        radius: 7,
        color: "#0B0F17",
        weight: 2,
        fillColor: "#F8FAFC",
        fillOpacity: 1,
      }).bindPopup(
        `<strong>${escapeHtml(station.name)}</strong><br>${station.distance_km.toFixed(1)} km · ${Math.round(station.elevation_m)} m`,
      ).addTo(aidLayer);
      bounds.push([station.lat, station.lon]);
    }

    for (const stop of fuelStops || []) {
      L.circleMarker([stop.latitude, stop.longitude], {
        radius: 5,
        color: "#0B0F17",
        weight: 1,
        fillColor: ZONE[stop.zone] || ZONE.green,
        fillOpacity: 0.95,
      }).bindPopup(
        `<strong>${escapeHtml(stop.zone)} · ${stop.carbs_g.toFixed(0)} g</strong><br>${escapeHtml(shortWhy(stop))}<br>${escapeHtml(stop.reason)}`,
      ).addTo(fuelLayer);
    }

    if (bounds.length) {
      lastBounds = bounds;
      map.fitBounds(bounds, { padding: [24, 24] });
    }
    chart = drawElevationChart(chartCanvas, chart, segments, fuelStops || [], forfeited || []);
  }

  function setRunner(latitude, longitude, pan = true) {
    const latLng = [latitude, longitude];
    if (!runner) {
      runner = L.marker(latLng, {
        icon: L.divIcon({ className: "runner-marker", iconSize: [18, 18], iconAnchor: [9, 9] }),
        zIndexOffset: 800,
      }).addTo(map);
    } else {
      runner.setLatLng(latLng);
    }
    if (pan) {
      map.panTo(latLng);
    }
  }

  function setRealLocation(latitude, longitude) {
    const latLng = [latitude, longitude];
    if (!realMarker) {
      realMarker = L.marker(latLng, {
        icon: L.divIcon({
          className: "real-location-wrap",
          html: '<div class="real-location"></div>',
          iconSize: [22, 22],
          iconAnchor: [11, 11],
        }),
        zIndexOffset: 1000,
        title: "My Real Location",
      }).addTo(map);
      realMarker.bindTooltip("My Real Location", { direction: "top" });
    } else {
      realMarker.setLatLng(latLng);
    }
  }

  function centerOnMe() {
    if (!realMarker) {
      return false;
    }
    map.setView(realMarker.getLatLng(), Math.max(map.getZoom(), 14));
    return true;
  }

  function resize() {
    map.invalidateSize();
    if (lastBounds) {
      map.fitBounds(lastBounds, { padding: [24, 24] });
    }
    if (chart) {
      chart.resize();
    }
  }

  return { setCourse, setRunner, setRealLocation, centerOnMe, resize, setTilesEnabled };
}
