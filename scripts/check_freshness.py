#!/usr/bin/env python3
"""Decide whether to refresh forecasts, rebuild reports, or repair status only.

Scheduled/manual forecast refresh behavior remains source-driven. Ordinary
code pushes do not overwrite a valid PDF just to rerun the renderer; a report
rebuild requires missing outputs or a changed PDF layout version.
"""
import csv
import datetime as dt
import json
import os
from pathlib import Path

from fetch_kma import KST, issues

ROOT=Path(__file__).resolve().parents[1]
EXPECTED_PDF_LAYOUT_VERSION="unified_kma_ecmwf_v1_coverage_audit"
REQUIRED_REPORT_FILES=(
    "precipitation_only.csv",
    "city_precipitation.csv",
    "ecmwf_kma_coverage.csv",
    "ecmwf_kma_coverage.md",
    "model_agreement_7day.md",
)

def needs_refresh(now, root=ROOT):
    data=root/"data"
    latest=data/"latest.json"
    manifest=data/"pdf_manifest.json"
    pdf=data/"rain_snow_latest.pdf"
    if not (latest.is_file() and manifest.is_file() and pdf.is_file()):
        return True,"missing_output"
    try:
        forecast=json.loads(latest.read_text(encoding="utf-8"))
        summary=json.loads(manifest.read_text(encoding="utf-8"))
        mapping=root/"locations.csv"
        if mapping.exists():
            with mapping.open(encoding="utf-8-sig",newline="") as stream:
                n=sum(1 for _ in csv.DictReader(stream))
            if forecast.get("locations_checked")!=n:
                return True,"region_scope_changed"
        expected=issues(now)[0]
        issued=dt.datetime.fromisoformat(forecast["issue_at_kst"]).astimezone(KST)
        generated=dt.datetime.fromisoformat(forecast["generated_at_kst"]).astimezone(KST)
        if issued < expected:
            return True,"new_kma_issue"
        if summary.get("scope")!="city_only":
            return True,"incorrect_pdf_scope"
        if summary.get("source_generated_at_kst") != forecast["generated_at_kst"]:
            return True,"pdf_out_of_sync"
        if (now-generated).total_seconds() < 0 or (now-generated).total_seconds() > 4*3600:
            return True,"stale_data"
        checked=forecast.get("locations_checked",0)
        succeeded=forecast.get("locations_succeeded",0)
        if checked<100 or succeeded<checked:
            return True,"partial_region_coverage"
        if pdf.stat().st_size<1000 or pdf.open("rb").read(5)!=b"%PDF-":
            return True,"invalid_pdf"
        return False,"already_current"
    except (ValueError,KeyError,TypeError,OSError,IndexError):
        return True,"invalid_data"

def needs_report_rebuild(root=ROOT):
    """Rebuild on code pushes only when report artifacts/layout are obsolete.

    A code-only deployment must not regenerate an already-current PDF after
    its alert slot: doing so changes pdf_generated_at_kst and can turn a
    previously on-time alert into a false late/not-ready status.
    """
    data=root/"data"
    try:
        forecast=json.loads((data/"latest.json").read_text(encoding="utf-8"))
        manifest=json.loads((data/"pdf_manifest.json").read_text(encoding="utf-8"))
        pdf=data/"rain_snow_latest.pdf"
        if not pdf.is_file():
            return True,"missing_pdf"
        if manifest.get("pdf_layout_version")!=EXPECTED_PDF_LAYOUT_VERSION:
            return True,"pdf_layout_changed"
        if manifest.get("scope")!="city_only" or manifest.get("coverage_scope")!="city_district_county":
            return True,"pdf_scope_changed"
        if manifest.get("source_generated_at_kst")!=forecast.get("generated_at_kst"):
            return True,"pdf_source_mismatch"
        if manifest.get("source_issue_at_kst")!=forecast.get("issue_at_kst"):
            return True,"pdf_issue_mismatch"
        if manifest.get("regions_checked")!=forecast.get("locations_checked") or manifest.get("regions_succeeded")!=forecast.get("locations_succeeded"):
            return True,"pdf_region_counts_mismatch"
        if pdf.stat().st_size<1000 or pdf.stat().st_size!=manifest.get("pdf_size_bytes"):
            return True,"invalid_pdf_size"
        with pdf.open("rb") as stream:
            if stream.read(5)!=b"%PDF-":
                return True,"invalid_pdf_signature"
        for name in REQUIRED_REPORT_FILES:
            if not (data/name).is_file() or (data/name).stat().st_size==0:
                return True,"missing_report_file_"+name
        if manifest.get("ecmwf_kma_coverage_rows")!=manifest.get("kma_unified_date_rows"):
            return True,"coverage_audit_rows_mismatch"
        return False,"reports_current"
    except (OSError,ValueError,KeyError,TypeError):
        return True,"invalid_report_metadata"


def needs_alert_status_refresh(root=ROOT):
    """Refresh only the small status JSON if it predates the status schema.

    This deliberately leaves the already-published PDF and its original
    creation time untouched.
    """
    data=root/"data"
    try:
        forecast=json.loads((data/"latest.json").read_text(encoding="utf-8"))
        manifest=json.loads((data/"pdf_manifest.json").read_text(encoding="utf-8"))
        status=json.loads((data/"alert_status.json").read_text(encoding="utf-8"))
        checked=dt.datetime.fromisoformat(status["status_generated_at_kst"])
        if checked.tzinfo is None:
            return True,"status_timestamp_missing_timezone"
        if status.get("source_generated_at_kst")!=forecast.get("generated_at_kst"):
            return True,"status_source_mismatch"
        if status.get("pdf_generated_at_kst") is None:
            return True,"status_pdf_timestamp_missing"
        if status.get("pdf_size_bytes")!=manifest.get("pdf_size_bytes"):
            return True,"status_pdf_size_mismatch"
        return False,"status_current"
    except (OSError,ValueError,KeyError,TypeError):
        return True,"status_needs_refresh"


def needs_ecmwf_retry(now,root=ROOT):
    """Retry external models when KMA PDF is current but ECMWF is incomplete.

    Never re-fetch KMA or accept stale KMA source solely for a model retry.
    Calls are rate-limited by the last model collection attempt (>=7 min).
    """
    path=root/"data/ecmwf_regional.json"
    if not path.is_file():
        return True,"missing_model_output"
    try:
        result=json.loads(path.read_text(encoding="utf-8"))
        last_attempt=result.get("last_attempt_at_kst",result["generated_at_kst"])
        attempted=dt.datetime.fromisoformat(last_attempt).astimezone(KST)
        elapsed=(now-attempted).total_seconds()
        if elapsed<0:
            return False,"invalid_future_model_timestamp"
        if result.get("rate_limited"):
            # Respect upstream HTTP 429 instead of triggering fresh national
            # requests every ten minutes. Retry-After takes precedence;
            # otherwise a conservative 30-minute cooling period.
            delay=max(30*60,result.get("retry_after_seconds",0))
            if elapsed<delay:
                return False,"provider_429_cooldown"
        if elapsed<7*60:
            return False,"recent_model_attempt"
        if (result.get("cities_targeted")!=167 or
            result.get("districts_targeted")!=109):
            return True,"model_region_scope_changed"
        if result.get("fallback_recent_success") or result.get("partial_recent_reuse"):
            # Cached locations are not confirmed fresh provider responses;
            # schedule retries after the appropriate cooldown.
            return True,"cached_model_needs_fresh_sample"
        generated=dt.datetime.fromisoformat(result["generated_at_kst"]).astimezone(KST)
        if (now-generated).total_seconds()>3*3600:
            return True,"model_data_stale"
        for collection,target in (("models",167),("district_models",109)):
            for model in ("IFS","AIFS Single"):
                entry=(result.get(collection) or {}).get(model) or {}
                if (entry.get("cities_requested")!=target or
                    entry.get("cities_succeeded",0)<target):
                    return True,"partial_ecmwf_coverage"
        return False,"all_models_complete"
    except (ValueError,KeyError,TypeError,OSError):
        return True,"invalid_model_status"

def can_render_existing_snapshot(root):
    """A code-only push may reuse valid source even if a new issue is due.

    The normal scheduled run remains responsible for freshness. Never treat
    an invalid/missing KMA JSON as a usable report source.
    """
    path=root/"data/latest.json"
    try:
        source=json.loads(path.read_text(encoding="utf-8"))
        labels=source.get("locations")
        checked=source["locations_checked"]
        succeeded=source["locations_succeeded"]
        if (not isinstance(labels,list) or checked<100 or succeeded<100
            or succeeded>checked or len(labels)!=succeeded):
            return False
        observed=[entry["name"] for entry in labels]
        if len(observed)!=len(set(observed)):
            return False
        if (dt.datetime.fromisoformat(source["generated_at_kst"]).tzinfo is None
            or dt.datetime.fromisoformat(source["issue_at_kst"]).tzinfo is None):
            return False
        mapping=root/"locations.csv"
        if mapping.is_file():
            with mapping.open(encoding="utf-8-sig",newline="") as stream:
                expected={r["name"] for r in csv.DictReader(stream)}
            if len(expected)!=checked or not set(observed)<=expected:
                return False
        return True
    except (OSError,ValueError,KeyError,TypeError):
        return False


def decide_run(now,event_name,root=ROOT):
    """Separate provider collection, report rebuilding and status-only repair.

    A valid code push does not regenerate a current PDF. It rebuilds reports
    only if the published layout/output set is obsolete; if just the alert
    status schema is old, it refreshes alert_status.json without touching the
    PDF creation timestamp. Scheduled/manual collection behavior is unchanged.
    """
    render_only=False
    status_only=False
    if event_name=="workflow_dispatch":
        kma_needed,reason=True,"manual_kma_refresh"
    elif (event_name=="push" and
          os.getenv("KMA_ONE_SHOT_RECOVERY","false").lower()=="true"):
        kma_needed,reason=True,"explicit_one_shot_kma_recovery"
    elif event_name=="push":
        if can_render_existing_snapshot(root):
            rebuild,why=needs_report_rebuild(root)
            if rebuild:
                kma_needed,reason=False,"code_change_requires_report_rebuild:"+why
                render_only=True
            else:
                kma_needed=False
                status_only,status_reason=needs_alert_status_refresh(root)
                reason=("code_change_status_only:"+status_reason if status_only
                        else "code_change_no_data_rebuild")
        else:
            kma_needed,reason=True,"invalid_snapshot_force_refresh"
    else:
        kma_needed,reason=needs_refresh(now,root)
    model_needed,model_reason=needs_ecmwf_retry(now,root)
    model_fetch=bool(model_needed and (kma_needed or event_name=="schedule"))
    model_retry=bool(not kma_needed and event_name=="schedule" and model_fetch)
    return {
        "needed":kma_needed,
        "ecmwf_retry":model_retry,
        "ecmwf_fetch":model_fetch,
        "render_only":render_only,
        "status_only":status_only,
        "reason":reason,
        "ecmwf_reason":model_reason
    }

def main():
    now=dt.datetime.now(KST)
    plan=decide_run(now,os.getenv("GITHUB_EVENT_NAME","schedule"))
    for k,v in plan.items():
        print(k+"="+(str(v).lower() if isinstance(v,bool) else str(v)))

if __name__=="__main__":
    main()
