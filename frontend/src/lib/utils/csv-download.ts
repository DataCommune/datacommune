/**
 * Standalone CSV download helper — zero legacy dependencies.
 * Extracted verbatim from the former csv.ts (lines 40-56) so the L2 export
 * (FICHE-03) can reuse it independently of ResolveResult / workspace / datasets,
 * unblocking the safe removal of the legacy modules (09-05, SHELL-02).
 */

import { neutralizeCsvValue } from "./csv-escape";

function escape(v: unknown): string {
  // CSV / formula injection neutralization (OWASP, D-12, T-10-07, audit S13):
  // delegated to the shared helper so backend and frontend exports share one
  // trigger set. Done BEFORE the comma/quote/newline quoting below so a
  // neutralized field is still quoted when needed.
  const s = String(neutralizeCsvValue(v) ?? "");
  return s.includes(",") || s.includes('"') || s.includes("\n")
    ? `"${s.replace(/"/g, '""')}"`
    : s;
}

export function downloadCsv(
  headers: string[],
  rows: unknown[][],
  filename: string,
) {
  const csv = [headers, ...rows]
    .map((row) => row.map(escape).join(","))
    .join("\n");
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
