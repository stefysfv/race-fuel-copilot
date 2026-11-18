"""Pure fueling engine. Callers pass the course and the athlete in, and get a plan back.

- GAP uses the Minetti 2002 running-cost polynomial. The athlete's flat
  effort pace is multiplied by C(grade) / C(0) to get time on that slope.
- Digestive zones: green from -8% to +5%, yellow up to +10% or down to -12%,
  and red outside that. A feed may move up to 6 minutes to reach green, then
  yellow. If the whole window is red, that window takes a 15 g liquid
  micro-dose and the rest of its carbs are forfeited. Windows do not share
  a carb debt.
- Aid-station packs, including Start at 0 km, are
  leg hours x hourly target x 1.15.
- Fluid and sodium come from air temperature and body weight.
- Live GPS snaps to the nearest point on the track. Remaining splits are
  stretched by actual_time / predicted_time.
- GI Rescue pauses calories, then eases back with half doses.
"""
