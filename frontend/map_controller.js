/** Leaflet course map and the Chart.js elevation / fuel timeline.

Zone colors are the same hex values as `style.css`.
*/

const ZONE = {
  green: "#10B981",
  yellow: "#F59E0B",
  red: "#EF4444",
};

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

function elevationAt(segments, distanceKm) {
  const distanceM = distanceKm * 1000;
  const segment = segments.find((item) => distanceM >= item.start_distance_m && distanceM <= item.end_distance_m)
    || segments[segments.length - 1];
  const span = segment.end_distance_m - segment.start_distance_m;
  const fraction = span <= 0 ? 0 : (distanceM - segment.start_distance_m) / span;
  return segment.elevation_start_m + Math.min(1, Math.max(0, fraction)) * (segment.elevation_end_m - segment.elevation_start_m);
}

export function createMapView(mapElement, chartCanvas) {
  const map = L.map(mapElement, { zoomControl: true, attributionControl: true });
  map.attributionControl.setPrefix(false);
  L.tileLayer(TILES, { maxZoom: 17, attribution: ATTRIBUTION }).addTo(map);
  map.setView([42.56, 23.29], 12);

  let realMarker = null;
  const routeLayer = L.layerGroup().addTo(map);
  const aidLayer = L.layerGroup().addTo(map);
  const fuelLayer = L.layerGroup().addTo(map);
  let runner = null;
  let chart = null;
  let lastBounds = null;

  function setCourse({ segments, aidStations, fuelStops }) {
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
        `<strong>${escapeHtml(stop.zone)} · ${stop.carbs_g.toFixed(0)} g</strong><br>${escapeHtml(stop.reason)}`,
      ).addTo(fuelLayer);
    }

    if (bounds.length) {
      lastBounds = bounds;
      map.fitBounds(bounds, { padding: [24, 24] });
    }
    drawChart(segments, fuelStops || []);
  }

  function drawChart(segments, fuelStops) {
    const elevation = [{
      x: segments[0].start_distance_m / 1000,
      y: segments[0].elevation_start_m,
    }];
    for (const segment of segments) {
      elevation.push({ x: segment.end_distance_m / 1000, y: segment.elevation_end_m });
    }
    const fuel = fuelStops.map((stop) => ({
      x: stop.distance_km,
      y: elevationAt(segments, stop.distance_km),
      zone: stop.zone,
    }));

    if (chart) {
      chart.destroy();
    }
    chart = new Chart(chartCanvas, {
      type: "line",
      data: {
        datasets: [
          {
            label: "Elevation (m)",
            data: elevation,
            borderColor: "#FF5500",
            segment: {
              borderColor: (context) => ZONE[segments[Math.min(context.p0DataIndex, segments.length - 1)].zone],
            },
            borderWidth: 3,
            pointRadius: 0,
            tension: 0.05,
          },
          {
            type: "scatter",
            label: "Fuel stop",
            data: fuel,
            pointRadius: 5,
            pointBackgroundColor: fuel.map((point) => ZONE[point.zone]),
            pointBorderColor: "#0B0F17",
            pointBorderWidth: 1,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label(item) {
                return `${item.dataset.label}: ${item.parsed.y.toFixed(0)} m at ${item.parsed.x.toFixed(1)} km`;
              },
            },
          },
        },
        layout: { padding: { top: 4, right: 8, bottom: 0, left: 0 } },
        scales: {
          x: {
            type: "linear",
            ticks: { color: "#64748B", maxTicksLimit: 6, font: { size: 10 } },
            title: { display: false },
            grid: { color: "rgba(15,23,42,0.06)" },
          },
          y: {
            ticks: { color: "#64748B", maxTicksLimit: 4, font: { size: 10 } },
            title: { display: false },
            grid: { color: "rgba(15,23,42,0.06)" },
          },
        },
      },
    });
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

  return { setCourse, setRunner, setRealLocation, centerOnMe, resize };
}
