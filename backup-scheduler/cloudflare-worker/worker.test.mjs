import assert from "node:assert/strict";
import { test } from "node:test";
import { expectedIssueUTC, isFresh } from "./worker.mjs";

const time = Date.parse("2026-11-14T12:12:00+09:00");
const latest = {
  issue_at_kst: "2026-11-14T11:00:00+09:00",
  generated_at_kst: "2026-11-14T11:49:00+09:00",
  locations_checked: 174,
  locations_succeeded: 174,
};
const pdf = {
  scope: "city_only",
  source_generated_at_kst: latest.generated_at_kst,
  source_issue_at_kst: latest.issue_at_kst,
  pdf_size_bytes: 104512,
};
test("Cloudflare backup checks official expected 11:00 KST issue at 12:12", () => {
  assert.equal(expectedIssueUTC(time), Date.parse("2026-11-14T11:00:00+09:00"));
});

test("Cloudflare backup checks official 23:00 previous-day issue near midnight", () => {
  assert.equal(expectedIssueUTC(Date.parse("2026-11-14T00:12:00+09:00")),
    Date.parse("2026-11-13T23:00:00+09:00"));
});

test("already published matching city PDF skips unnecessary dispatch", () => {
  assert.deepEqual(isFresh(latest, pdf, time), { ok: true, reason: "already_current" });
});

test("older KMA issue triggers external fallback", () => {
  const old = { ...latest, issue_at_kst: "2026-11-14T05:00:00+09:00" };
  const oldManifest = { ...pdf, source_issue_at_kst: old.issue_at_kst };
  assert.equal(isFresh(old, oldManifest, time).reason, "new_kma_issue_missing");
});

test("PDF and JSON timestamp mismatch triggers fallback", () => {
  assert.equal(isFresh(latest, { ...pdf, source_generated_at_kst: "2026-11-14T06:00:00+09:00" },time).reason,
    "pdf_not_matching_forecast");
});

test("incomplete region coverage triggers fallback", () => {
  assert.equal(isFresh({ ...latest, locations_succeeded: 173 },pdf,time).reason,
    "incomplete_kma_coverage");
});

test("unexpected old data triggers fallback", () => {
  const late = Date.parse("2026-11-14T17:19:00+09:00");
  assert.equal(isFresh(latest,pdf,late).reason,"stale_or_future_data");
});

test("invalid PDF marker triggers fallback", () => {
  assert.equal(isFresh(latest,{ ...pdf, pdf_size_bytes: 0 },time).reason,"invalid_pdf_size");
});
