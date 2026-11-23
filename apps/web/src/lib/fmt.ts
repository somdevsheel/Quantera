// Display formatting. Missing values show as an em dash, never as zero.

const dash = "—";
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function num(v: unknown, digits = 2): string {
  return isNum(v) ? v.toLocaleString("en-IN", { maximumFractionDigits: digits,
                                                minimumFractionDigits: digits }) : dash;
}

export function int(v: unknown): string {
  return isNum(v) ? Math.round(v).toLocaleString("en-IN") : dash;
}

export function pct(v: unknown, digits = 1, signed = false): string {
  if (!isNum(v)) return dash;
  const s = (v * 100).toFixed(digits);
  return `${signed && v > 0 ? "+" : ""}${s}%`;
}

export function inr(v: unknown, digits = 0): string {
  if (!isNum(v)) return dash;
  return `₹${v.toLocaleString("en-IN", { maximumFractionDigits: digits,
                                         minimumFractionDigits: digits })}`;
}

/** ₹ in crore / lakh for big amounts. */
export function inrShort(v: unknown): string {
  if (!isNum(v)) return dash;
  const a = Math.abs(v);
  if (a >= 1e7) return `₹${(v / 1e7).toFixed(2)} Cr`;
  if (a >= 1e5) return `₹${(v / 1e5).toFixed(2)} L`;
  return inr(v);
}

export function score(v: unknown): string {
  return isNum(v) ? v.toFixed(0) : dash;
}

export function date(v: unknown): string {
  if (typeof v !== "string" || !v) return dash;
  const d = new Date(v.length === 10 ? `${v}T00:00:00` : v);
  return Number.isNaN(d.getTime()) ? v : d.toLocaleDateString("en-IN", {
    day: "2-digit", month: "short", year: "numeric" });
}

export function dateTime(v: unknown): string {
  if (typeof v !== "string" || !v) return dash;
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString("en-IN", {
    day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function signClass(v: unknown): string {
  return isNum(v) ? (v > 0 ? "pos" : v < 0 ? "neg" : "") : "";
}

export function label(v: string): string {
  return v.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
}
