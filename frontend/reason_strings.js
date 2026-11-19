/** English for each engine reason code. Params are numbers, so this map can be translated later. */

function num(params, key, digits = 1) {
  const value = Number(params?.[key]);
  if (!Number.isFinite(value)) return "—";
  return value.toFixed(digits);
}

function absNum(params, key, digits = 1) {
  const value = Number(params?.[key]);
  if (!Number.isFinite(value)) return "—";
  return Math.abs(value).toFixed(digits);
}

const ON_TIME = {
  short: () => "On time",
  detail: () => "This stop is on its slot at the full window.",
};

/** Flat lookup. Each code has a short line and a longer line. */
export const REASON_TEXT = {
  SHIFTED_EARLIER: {
    short: (params) => `${absNum(params, "minutes")} min earlier`,
    detail: (params) => `Moved ${num(params, "minutes")} min onto grade ${num(params, "grade_percent")}% (segment ${num(params, "segment_index", 0)}).`,
  },
  SHIFTED_LATER: {
    short: (params) => `${absNum(params, "minutes")} min later`,
    detail: (params) => `Moved ${num(params, "minutes")} min onto grade ${num(params, "grade_percent")}% (segment ${num(params, "segment_index", 0)}).`,
  },
  YELLOW_HALF: {
    short: () => "Half dose",
    detail: (params) => `Grade ${num(params, "grade_percent")}% is yellow, so this window is ${num(params, "carbs_g", 0)} g.`,
  },
  SKIPPED_RED_ZONE: {
    short: () => "Red micro-dose",
    detail: (params) => `Grade ${num(params, "grade_percent")}% stayed red. ${num(params, "carbs_g", 0)} g now, ${num(params, "forfeited_g", 0)} g left behind.`,
  },
  GI_RESCUE_NIL: {
    short: () => "Rescue sip",
    detail: (params) => `${num(params, "minutes_into_rescue", 0)} min into GI Rescue, water and salt only.`,
  },
  GI_RESCUE_EASE: {
    short: () => "Rescue ease",
    detail: (params) => `${num(params, "carbs_g", 0)} g while the stomach settles.`,
  },
  GI_RESCUE_RED: {
    short: () => "Rescue, still red",
    detail: (params) => `Grade ${num(params, "grade_percent")}% is still red, so this rescue sip has no calories.`,
  },
  GI_RESCUE_HALF: {
    short: () => "Rescue half-size",
    detail: (params) => `Half size while GI Rescue eases off (${num(params, "carbs_g", 0)} g).`,
  },
  QUIET_WINDOW: {
    short: () => "Pre-race meal",
    detail: (params) => `Water only until minute ${num(params, "quiet_until_min", 0)}.`,
  },
  DESCENT_LOCK: {
    short: () => "Fast descent",
    detail: (params) => `Leg averages ${num(params, "grade_percent")}%, so this trail dose is a sip.`,
  },
  AID_RESTOCK: {
    short: () => "Station plate",
    detail: (params) => `Eat ${num(params, "plate_carbs_g", 0)} g at the table.`,
  },
  AID_COVERED: {
    short: () => "Covered by the plate",
    detail: (params) => `The station plate is ${num(params, "plate_carbs_g", 0)} g and covers ${num(params, "covered_g", 0)} g of this snack.`,
  },
  OPENING_SOLID: {
    short: () => "Opening solid",
    detail: (params) => `One small solid in the first 90 minutes (${num(params, "carbs_g", 0)} g).`,
  },
  SOLID_SLOT: {
    short: () => "Solid on the gap",
    detail: (params) => `A solid on the 90 minute gap (${num(params, "carbs_g", 0)} g).`,
  },
  SAVOURY_SLOT: {
    short: () => "Savoury solid",
    detail: (params) => `Savoury food from minute ${num(params, "switch_min", 0)} (${num(params, "carbs_g", 0)} g).`,
  },
  PORTION: {
    short: () => "Your portion",
    detail: (params) => `The vest portion replaces the window (${num(params, "carbs_g", 0)} g).`,
  },
  NO_VEST_FOOD: {
    short: () => "Nothing in the vest",
    detail: (params) => `${num(params, "forfeited_g", 0)} g dropped because the vest has no food.`,
  },
  NO_CLIMB_FOOD: {
    short: () => "No climb food",
    detail: (params) => `Grade ${num(params, "grade_percent")}% and no gel or drink mix, so ${num(params, "forfeited_g", 0)} g is dropped.`,
  },
  PACKED_VEST: {
    short: () => "Packed vest",
    detail: (params) => `A packed piece replaces this window (${num(params, "carbs_g", 0)} g).`,
  },
  SHORT_RACE_BITE: {
    short: () => "Short-race bite",
    detail: (params) => `The one mid-race bite (${num(params, "carbs_g", 0)} g).`,
  },
  SHORT_RACE_SIP: {
    short: () => "Flask reminder",
    detail: () => "A hydration sip on the short-race grid.",
  },
  UNPLACED: {
    short: () => "No ground in the window",
    detail: () => "No segment inside ±6 min, so this window is not fed.",
  },
};

function line(reason, kind) {
  const entry = REASON_TEXT[reason?.code];
  if (!entry) return reason?.code || "";
  return entry[kind](reason.params || {});
}

export function reasonShort(reason) {
  return line(reason, "short");
}

export function reasonDetail(reason) {
  return line(reason, "detail");
}

/** One short why for the stop list. An empty list means the stop did not move or shrink. */
export function shortWhy(stop) {
  const reasons = stop?.reasons || [];
  if (!reasons.length) return ON_TIME.short();
  return reasons.map(reasonShort).join(" · ");
}

export function detailLines(stop) {
  const reasons = stop?.reasons || [];
  if (!reasons.length) return [ON_TIME.detail()];
  return reasons.map(reasonDetail);
}

export function forfeitText(item) {
  const grams = Number(item?.carbs_g);
  const amount = Number.isFinite(grams) ? `${grams.toFixed(0)} g left behind` : "Left behind";
  return `${amount}. ${reasonDetail(item)}`;
}
