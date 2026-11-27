#!/usr/bin/env python3
"""Publish one tiny deterministic PDF status for each scheduled ChatGPT reminder.

Only official KMA issue hours 23,05,11,17 can satisfy the next matching
00:30/06:30/12:30/18:30 reminder; intermediate issues 02/08/14/20 cannot
be advertised as the latest for a reminder. No network access needed.
"""
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
KST=ZoneInfo("Asia/Seoul")
PDF=ROOT/"data/rain_snow_latest.pdf"
STATUS=ROOT/"data/alert_status.json"

def slot_for_issue(issue):
    issue=issue.astimezone(KST)
    h=issue.hour
    if h in (20,23):
        return dt.datetime.combine(issue.date()+dt.timedelta(days=1),
                                   dt.time(0,30,tzinfo=KST)),23
    if h in (2,5):
        return dt.datetime.combine(issue.date(),dt.time(6,30,tzinfo=KST)),5
    if h in (8,11):
        return dt.datetime.combine(issue.date(),dt.time(12,30,tzinfo=KST)),11
    if h in (14,17):
        return dt.datetime.combine(issue.date(),dt.time(18,30,tzinfo=KST)),17
    raise ValueError("UNRECOGNIZED_KMA_ISSUE_HOUR")

def baseline_coverage_safe(forecast):
    """Preserve all 174 existing city/gu areas, allow county-only partial results."""
    checked=forecast.get("locations_checked",0)
    succeeded=forecast.get("locations_succeeded",0)
    if checked<100 or succeeded<100 or succeeded>checked:
        return False
    if succeeded==checked:
        return True
    if checked<256:
        return False
    locations=forecast.get("locations") or []
    return (len(locations)==succeeded and
            sum(item.get("level")=="gu" for item in locations)>=109 and
            sum(item.get("level")=="city" for item in locations)>=65)


def compute_status(forecast,manifest,pdf_path,now=None):
    now=now or dt.datetime.now(KST)
    issue=dt.datetime.fromisoformat(forecast["issue_at_kst"]).astimezone(KST)
    generated=dt.datetime.fromisoformat(forecast["generated_at_kst"]).astimezone(KST)
    pdf_time=dt.datetime.fromisoformat(manifest["pdf_generated_at_utc"]).astimezone(KST)
    slot,expected_hour=slot_for_issue(issue)
    report={
        "schema_version":1,
        "pdf_ready":False,
        "ready_for_alert_slot_kst":None,
        "expected_kma_issue_hour_kst":expected_hour,
        "source_issue_at_kst":issue.isoformat(),
        "source_generated_at_kst":generated.isoformat(),
        "pdf_generated_at_kst":pdf_time.isoformat(),
        # Timestamp of this readiness evaluation, distinct from the KMA
        # retrieval and PDF build timestamps. This lets delayed ChatGPT
        # reminder runs distinguish a newer published slot from a missing
        # update for their original scheduled slot.
        "status_generated_at_kst":now.astimezone(KST).isoformat(),
        "pdf_size_bytes":manifest.get("pdf_size_bytes"),
        "regions_succeeded":forecast.get("locations_succeeded"),
        "regions_checked":forecast.get("locations_checked"),
        "county_succeeded":sum(item.get("level")=="gun" for item in forecast.get("locations",[])),
        "coverage_partial":forecast.get("locations_succeeded")!=forecast.get("locations_checked"),
        # Do not advertise a PDF generated AFTER its intended alert slot as
        # ready for that already-missed slot. This is not a delivery receipt:
        # GitHub publication and the ChatGPT alert are separate actions.
        "pdf_generated_before_alert_slot":pdf_time<=slot,
        "late_pdf_for_alert_slot":pdf_time>slot,
    }
    checks={
      "kma_issue_matches_alert":issue.hour==expected_hour and issue.minute==0,
      "pdf_generated_before_alert_slot":pdf_time<=slot,
      "correct_pdf_scope":manifest.get("scope")=="city_only",
      "pdf_matches_source":manifest.get("source_issue_at_kst")==forecast["issue_at_kst"]
                             and manifest.get("source_generated_at_kst")==forecast["generated_at_kst"],
      "times_ordered":issue<=generated<=pdf_time<=now+dt.timedelta(minutes=3),
      "baseline_regions_safe":baseline_coverage_safe(forecast),
      "valid_pdf":pdf_path.is_file() and pdf_path.stat().st_size>=1000
                  and pdf_path.stat().st_size==manifest.get("pdf_size_bytes")
                  and pdf_path.read_bytes()[:5]==b"%PDF-",
    }
    # Intermediate official 02/08/14/20 issues are valid KMA products,
    # but insufficient for the NEXT reminder, which requires 05/11/17/23.
    # They must not cause the otherwise healthy collection job to fail.
    structure_ok=all(value for key,value in checks.items()
                     if key not in ("kma_issue_matches_alert",
                                    "pdf_generated_before_alert_slot"))
    report["checks_passed"]=structure_ok
    report["pdf_ready"]=(structure_ok and checks["kma_issue_matches_alert"]
                         and checks["pdf_generated_before_alert_slot"])
    if report["pdf_ready"]:
        report["ready_for_alert_slot_kst"]=slot.isoformat()
    else:
        report["reason"]=",".join(key for key,val in checks.items() if not val)
    return report

def main():
    forecast=json.loads((ROOT/"data/latest.json").read_text(encoding="utf-8"))
    manifest=json.loads((ROOT/"data/pdf_manifest.json").read_text(encoding="utf-8"))
    status=compute_status(forecast,manifest,PDF)
    STATUS.write_text(json.dumps(status,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("PDF reminder status:",status["pdf_ready"],
          "slot:",status["ready_for_alert_slot_kst"],
          "issue:",status["source_issue_at_kst"])
    if not status["checks_passed"]:
        raise SystemExit("PDF_STATUS_INVALID: "+status.get("reason","unknown"))

if __name__=="__main__":
    main()
