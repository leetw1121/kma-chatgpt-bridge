#!/usr/bin/env python3
"""Fail closed on mismatched generated weather artifacts BEFORE GitHub publication.

No network or PDF rendering libraries are needed. Validate the actual KMA
source, CSV, ECMWF per-region records and model-only appendix against the PDF
manifest. Partial ECMWF/partial county availability is permitted when it is
correctly labelled; silently missing KMA wet rows are not.
"""
import csv
import datetime as dt
import io
import json
from collections import Counter
from pathlib import Path

from create_precip_report import ROOT, as_csv as kma_csv, rows as kma_rows
from ecmwf_coverage_audit import csv_report as audit_csv, coverage_rows, daily_counts
from ecmwf_report_gate import combined_model_rows
from model_agreement_7day import agreement_by_date, markdown_report


def read_rows(text):
    return list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))


def require(value,explanation):
    if not value:
        raise ValueError("ARTIFACT_INTEGRITY: "+explanation)


def validate(root=ROOT):
    data=root/"data"
    kma=json.loads((data/"latest.json").read_text(encoding="utf-8"))
    manifest=json.loads((data/"pdf_manifest.json").read_text(encoding="utf-8"))
    alert=json.loads((data/"alert_status.json").read_text(encoding="utf-8"))
    model_path=data/"ecmwf_regional.json"
    model=json.loads(model_path.read_text(encoding="utf-8")) if model_path.exists() else None

    locations=kma.get("locations") or []
    names=[p["name"] for p in locations]
    require(len(names)==len(set(names)),"duplicate KMA region labels")
    require(len(locations)==kma["locations_succeeded"],"KMA success count differs from locations")
    require(kma["locations_checked"]>=len(locations),"KMA target count smaller than successes")

    mapping=root/"locations.csv"
    if mapping.exists():
        expected={r["name"] for r in read_rows(mapping.read_text(encoding="utf-8-sig"))}
        require(len(expected)==kma["locations_checked"],"KMA official mapping count differs from source")
        require(set(names)<=expected,"KMA source includes unknown/unmapped locations")

    expected_kma=read_rows(kma_csv(kma))
    actual_kma=read_rows((data/"precipitation_only.csv").read_text(encoding="utf-8-sig"))
    require(actual_kma==expected_kma,"KMA precipitation CSV missing, reordered or stale")
    events=kma_rows(kma)
    require(len(events)==len(expected_kma),"KMA precipitation row count mismatch")

    expected_coverage=coverage_rows(kma,model,events)
    audit_expected=read_rows(audit_csv(expected_coverage))
    audit_actual=read_rows((data/"ecmwf_kma_coverage.csv").read_text(encoding="utf-8-sig"))
    require(audit_expected==audit_actual,"per-region ECMWF audit differs from validated forecasts")
    require(len(audit_actual)==len(expected_kma),"KMA wet-region audit is not exhaustive")

    require(manifest.get("scope")=="city_only","legacy alert scope incompatible")
    require(manifest.get("coverage_scope")=="city_district_county","missing district/county coverage scope")
    require(manifest.get("pdf_layout_version")=="unified_kma_ecmwf_v1_coverage_audit",
            "PDF layout lacks mandatory KMA-model coverage audit")
    require(manifest.get("source_generated_at_kst")==kma["generated_at_kst"],"PDF source not latest KMA")
    require(manifest.get("source_issue_at_kst")==kma["issue_at_kst"],"PDF KMA issue mismatch")
    require(manifest.get("regions_succeeded")==kma["locations_succeeded"],"PDF KMA region count mismatch")
    require(manifest.get("kma_unified_date_rows")==len(events),"PDF KMA row count mismatch")
    require(manifest.get("ecmwf_kma_coverage_rows")==len(expected_coverage),
            "PDF missing KMA-to-ECMWF comparison rows")
    require(manifest.get("ecmwf_kma_coverage_by_date")==daily_counts(expected_coverage),
            "PDF KMA-to-ECMWF day counts inconsistent")

    agreement=agreement_by_date(kma,model)
    comparison=(data/"model_agreement_7day.md")
    require(comparison.exists(),"missing model agreement report")
    require(comparison.read_text(encoding="utf-8")==markdown_report(agreement,kma),
            "7-day model agreement report does not match verified sources")
    require(manifest.get("model_agreement_dates")==len(agreement),
            "PDF model agreement date count mismatch")
    require(manifest.get("model_agreement_pairs_checked")==
            sum(x["pairs_checked"] for x in agreement),
            "PDF model agreement checked pairs mismatch")
    require(manifest.get("model_agreement_pairs_partial")==
            sum(x["pairs_partial"] for x in agreement),
            "PDF model agreement partial pairs mismatch")

    modeled,_,_=combined_model_rows(kma,model)
    require(manifest.get("ecmwf_unified_date_rows")==len(modeled),
            "PDF actual-model positive-row count mismatch")

    if model:
        for scope,count in (("models",model.get("cities_targeted")),
                            ("district_models",model.get("districts_targeted"))):
            source=model.get(scope) or {}
            for name,entry in source.items():
                records=entry.get("records") or []
                labels=[p.get("city") for p in records]
                require(len(labels)==len(set(labels)),"duplicate ECMWF city in "+scope+"/"+name)
                require(len(labels)==entry.get("cities_succeeded"),
                        "ECMWF count differs from actual data: "+scope+"/"+name)
                require(len(records)<=count,"ECMWF exceeds declared scope: "+scope+"/"+name)
                cached=sum(bool(p.get("cached_from_previous_run")) for p in records)
                if "cached_succeeded" in entry:
                    require(cached==entry["cached_succeeded"],
                            "ECMWF cache provenance count mismatch: "+scope+"/"+name)
                for p in records:
                    if not p.get("cached_from_previous_run"):
                        continue
                    require("collected_at_kst" in p,"cached ECMWF region missing original timestamp")
                    collected=dt.datetime.fromisoformat(p["collected_at_kst"])
                    generated=dt.datetime.fromisoformat(model["generated_at_kst"])
                    require(collected.tzinfo is not None and generated.tzinfo is not None
                            and 0<=(generated-collected).total_seconds()<=3*3600,
                            "ECMWF cached region outside 3-hour window")

    pdf=data/"rain_snow_latest.pdf"
    require(pdf.is_file() and pdf.stat().st_size>=1000,"PDF missing or tiny")
    require(pdf.stat().st_size==manifest.get("pdf_size_bytes"),"PDF size differs from manifest")
    with pdf.open("rb") as stream:
        require(stream.read(5)==b"%PDF-","invalid PDF signature")
    require(alert.get("source_generated_at_kst")==kma["generated_at_kst"],
            "alert status does not match KMA source")
    require(alert.get("pdf_size_bytes")==pdf.stat().st_size,
            "alert status does not match published PDF")
    require(alert.get("checks_passed") is True,"alert status failed document-integrity checks")

    return {"kma_checked":kma["locations_checked"],
            "kma_success":kma["locations_succeeded"],
            "kma_wet_date_rows":len(events),
            "audit_date_rows":len(expected_coverage),
            "model_positive_date_rows":len(modeled),
            "model_cached_rows":sum(
                entry.get("cached_succeeded",0)
                for key in ("models","district_models")
                for entry in (model.get(key) or {}).values()) if model else 0}


def main():
    status=validate()
    print("ARTIFACT_INTEGRITY_PASS",json.dumps(status,ensure_ascii=False,sort_keys=True))


if __name__=="__main__":
    main()
