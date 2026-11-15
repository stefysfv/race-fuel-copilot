/** Offline snap-to-track.

Fuel timing stays in the Python engine. This module only mirrors the
equirectangular projection in `fuel_engine.snap_to_track`, using the plan
segments already on the phone, so a GPS fix can still move the marker when
`/api/recalibrate` cannot be reached.
*/

const METERS_PER_DEG_LAT = 111320;
const OFF_COURSE_M = 75;

function toLocal(lat, lon, originLat, originLon) {
  const metersPerDegLon = METERS_PER_DEG_LAT * Math.cos((originLat * Math.PI) / 180);
  return [(lon - originLon) * metersPerDegLon, (lat - originLat) * METERS_PER_DEG_LAT];
}

export function snapToSegments(segments, latitude, longitude) {
  if (!segments?.length) {
    throw new Error("No saved course to snap to");
  }
  const originLat = segments[0].start_lat;
  const originLon = segments[0].start_lon;
  const [px, py] = toLocal(latitude, longitude, originLat, originLon);
  let best = null;

  for (const segment of segments) {
    const [ax, ay] = toLocal(segment.start_lat, segment.start_lon, originLat, originLon);
    const [bx, by] = toLocal(segment.end_lat, segment.end_lon, originLat, originLon);
    const dx = bx - ax;
    const dy = by - ay;
    const lengthSq = dx * dx + dy * dy;
    const fraction = lengthSq <= 1e-9
      ? 0
      : Math.min(1, Math.max(0, ((px - ax) * dx + (py - ay) * dy) / lengthSq));
    const offCourseM = Math.hypot(px - ax - fraction * dx, py - ay - fraction * dy);
    if (best && offCourseM >= best.offCourseM) {
      continue;
    }
    const distanceM = segment.start_distance_m + fraction * (segment.end_distance_m - segment.start_distance_m);
    best = {
      latitude: segment.start_lat + fraction * (segment.end_lat - segment.start_lat),
      longitude: segment.start_lon + fraction * (segment.end_lon - segment.start_lon),
      distanceM,
      offCourseM,
      onCourse: offCourseM <= OFF_COURSE_M,
      segment,
    };
  }
  return best;
}

export function segmentAtMinute(segments, elapsedMin) {
  if (!segments?.length) {
    return null;
  }
  return segments.find((segment) => elapsedMin >= segment.start_minute && elapsedMin < segment.end_minute)
    || segments[segments.length - 1];
}
