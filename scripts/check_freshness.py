#!/usr/bin/env python3
"""Decide whether a scheduled run needs to refresh the forecast PDF.

Only skip when a complete published forecast/PDF already matches the latest
expected KMA village forecast base issue. An old issue, missing PDF, or
partial coverage must always refresh. Push/manual runs always refresh.
"""
import csv
import datetime as dt
import json
import os
from pathlib import Path

from fetch_kma import KST, issues

ROOT=Path(__file__).resolve().parents[1]

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

def decide_run(now,event_name,root=ROOT):
    """Separate KMA refresh, ECMWF provider calls, and report rebuilding.

    Pushes with already-current KMA regenerate reports without calling *any*
    external forecast API. This prevents code/deployment churn from causing
    Open-Meteo HTTP 429. Scheduled jobs still retry incomplete model coverage
    after a cooldown; manual jobs may force KMA refresh but respect upstream
    429 and existing recent model coverage.
    """
    if event_name=="workflow_dispatch":
        kma_needed,reason=True,"manual_kma_refresh"
    else:
        kma_needed,reason=needs_refresh(now,root)
    model_needed,model_reason=needs_ecmwf_retry(now,root)
    model_fetch=bool(model_needed and (kma_needed or event_name=="schedule"))
    model_retry=bool(not kma_needed and event_name=="schedule" and model_fetch)
    render_only=bool(event_name=="push" and not kma_needed)
    return {
        "needed":kma_needed,
        "ecmwf_retry":model_retry,
        "ecmwf_fetch":model_fetch,
        "render_only":render_only,
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
