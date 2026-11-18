#!/usr/bin/env python3
"""Decide whether REAL verified ECMWF values warrant a PDF appendix.

Pure standard-library gate, so PDF tests run without installing ReportLab.
Never infer a forecast from a failed model, stale cache, or zero/unknown fields.
"""
import datetime as dt
from collections import defaultdict


def usable_model_rows(kma, model, scope="city"):
    if not isinstance(model, dict):
        return {}, False
    try:
        source_time=dt.datetime.fromisoformat(kma["generated_at_kst"])
        model_time=dt.datetime.fromisoformat(model["generated_at_kst"])
        if source_time.tzinfo is None or model_time.tzinfo is None:
            return {}, False
        if abs((source_time-model_time).total_seconds())>3*3600:
            return {}, False
    except (TypeError,ValueError,KeyError,AttributeError):
        return {}, False

    if scope not in ("city","district"):
        raise ValueError("UNKNOWN_ECMWF_SCOPE")
    collection_key="models" if scope=="city" else "district_models"
    out=defaultdict(dict)
    wet_days=0
    for model_name,record in (model.get(collection_key) or {}).items():
        if not isinstance(record,dict):
            continue
        if record.get("status") not in ("verified","verified_cached_recent"):
            continue
        if not record.get("cities_succeeded"):
            continue
        for item in record.get("records",[]):
            if not isinstance(item,dict) or not item.get("city"):
                continue
            for date,day in (item.get("days") or {}).items():
                if not isinstance(day,dict) or (day.get("forecast_hours") or 0)<=0:
                    continue
                precip=day.get("precip_mm")
                snow=day.get("snowfall_cm") if day.get("snowfall_valid") else None
                wet=(isinstance(precip,(int,float)) and precip>0) or (
                    isinstance(snow,(int,float)) and snow>0)
                if not wet:
                    continue
                out[(date,item["city"])][model_name]=day
                wet_days+=1

    return dict(out), wet_days>0
