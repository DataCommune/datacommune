/**
 * Shared CSV / spreadsheet formula-injection neutralization (audit S13 / FA-02).
 *
 * A spreadsheet (Excel, LibreOffice, Google Sheets) treats a cell whose first
 * character is one of these triggers as a formula and may execute it when the
 * file is opened. Any export fed by user-controlled text must run its cell
 * values through neutralizeCsvValue() before writing them.
 *
 * Mirrors `api/backend/app/common/csv_escape.py` — keep the trigger set in sync.
 */

/** Leading TAB and CR also start a formula in some importers, not just the operators. */
export const FORMULA_TRIGGERS = ["=", "+", "-", "@", "\t", "\r"] as const;

/**
 * Prefix an apostrophe when a text cell would be parsed as a formula.
 *
 * null/undefined become the empty string. Non-string values (numbers, dates)
 * are returned unchanged: they carry no attacker-controlled text and keeping
 * their type lets the caller format them as it already does.
 */
export function neutralizeCsvValue<T>(value: T): T | string {
  if (value === null || value === undefined) return "";
  if (typeof value !== "string") return value;
  return FORMULA_TRIGGERS.some((t) => value.startsWith(t))
    ? `'${value}`
    : value;
}
