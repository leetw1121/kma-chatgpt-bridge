#!/usr/bin/env python3
"""Decide whether a scheduled run needs to refresh the forecast PDF.

Only skip when a complete published forecast/PDF already matches the latest
expected KMA village forecast base issue. An old issue, missing PDF, or
partial coverage must always refresh. Push/manual runs always refresh.
"""
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

def main():
    if os.getenv("GITHUB_EVENT_NAME")!="schedule":
        print("needed=true")
        print("reason=manual_or_code_change")
        return
    needed,reason=needs_refresh(dt.datetime.now(KST))
    print("needed="+str(needed).lower())
    print("reason="+reason)

if __name__=="__main__":
    main()
