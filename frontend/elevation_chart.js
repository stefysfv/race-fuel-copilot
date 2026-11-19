/** Chart.js elevation profile, fuel-stop markers, and hollow forfeited-dose markers.

Zone colors are the same hex values as `style.css`.
*/

import { forfeitText } from "./reason_strings.js";
import { distanceToDisplay, distanceUnit, elevationToDisplay, elevationUnit } from "./units.js";

export const ZONE = {
  green: "#10B981",
  yellow: "#F59E0B",
  red: "#EF4444",
};

function distanceKmAtMinute(segments, minute) {
  const segment = segments.find((item) => minute >= item.start_minute && minute <= item.end_minute)
    || segments[segments.length - 1];
  const span = segment.end_minute - segment.start_minute;
  const fraction = span <= 0 ? 0 : Math.min(1, Math.max(0, (minute - segment.start_minute) / span));
  return (segment.start_distance_m + fraction * (segment.end_distance_m - segment.start_distance_m)) / 1000;
}

function elevationAt(segments, distanceKm) {
  const distanceM = distanceKm * 1000;
  const segment = segments.find((item) => distanceM >= item.start_distance_m && distanceM <= item.end_distance_m)
    || segments[segments.length - 1];
  const span = segment.end_distance_m - segment.start_distance_m;
  const fraction = span <= 0 ? 0 : (distanceM - segment.start_distance_m) / span;
  return segment.elevation_start_m + Math.min(1, Math.max(0, fraction)) * (segment.elevation_end_m - segment.elevation_start_m);
}

/** Destroys the previous chart and returns the new one so the map can resize it. */
export function drawElevationChart(canvas, previous, segments, fuelStops, forfeited = []) {
  const elevation = [{
    x: distanceToDisplay(segments[0].start_distance_m / 1000),
    y: elevationToDisplay(segments[0].elevation_start_m),
  }];
  for (const segment of segments) {
    elevation.push({
      x: distanceToDisplay(segment.end_distance_m / 1000),
      y: elevationToDisplay(segment.elevation_end_m),
    });
  }
  const fuel = fuelStops.map((stop) => ({
    x: distanceToDisplay(stop.distance_km),
    y: elevationToDisplay(elevationAt(segments, stop.distance_km)),
    zone: stop.zone,
  }));
  const missed = (forfeited || []).map((item) => {
    const xKm = distanceKmAtMinute(segments, item.at_min);
    return {
      x: distanceToDisplay(xKm),
      y: elevationToDisplay(elevationAt(segments, xKm)),
      text: forfeitText(item),
    };
  });

  if (previous) {
    previous.destroy();
  }
  return new Chart(canvas, {
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
        {
          type: "scatter",
          label: "Forfeited",
          data: missed,
          pointRadius: 6,
          pointHoverRadius: 8,
          pointBackgroundColor: "rgba(0,0,0,0)",
          pointBorderColor: "#334155",
          pointBorderWidth: 2,
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
              if (item.dataset.label === "Forfeited") return item.raw.text;
              return `Elevation: ${item.parsed.y.toFixed(0)} ${elevationUnit()} at ${item.parsed.x.toFixed(1)} ${distanceUnit()}`;
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
