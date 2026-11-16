import { describe, it, expect } from "vitest";
import { neutralizeCsvValue, FORMULA_TRIGGERS } from "./csv-escape";

// Audit S13 / FA-02 — a cell starting with = + - @ TAB CR is parsed as a formula
// by Excel / LibreOffice / Google Sheets and can execute when the file is opened.
describe("neutralizeCsvValue()", () => {
  it.each(FORMULA_TRIGGERS)(
    "prefixes an apostrophe on a leading %j",
    (trigger) => {
      const payload = `${trigger}cmd|' /C calc'!A0`;
      expect(neutralizeCsvValue(payload)).toBe(`'${payload}`);
    },
  );

  it("leaves a plain value untouched", () => {
    expect(neutralizeCsvValue("Nantes")).toBe("Nantes");
    expect(neutralizeCsvValue("user@example.com")).toBe("user@example.com");
  });

  it("leaves a trigger that is not in first position untouched", () => {
    expect(neutralizeCsvValue("a=b")).toBe("a=b");
  });

  it("maps null and undefined to the empty string", () => {
    expect(neutralizeCsvValue(null)).toBe("");
    expect(neutralizeCsvValue(undefined)).toBe("");
  });

  it("returns non-string values unchanged so they keep their type", () => {
    expect(neutralizeCsvValue(42)).toBe(42);
    expect(neutralizeCsvValue(-1)).toBe(-1);
  });
});
