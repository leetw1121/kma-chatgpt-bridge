#!/usr/bin/env python3
"""Decide whether REAL verified ECMWF values warrant a PDF appendix.

Pure standard-library gate, so PDF tests run without installing ReportLab.
Never infer a forecast from a failed model, stale cache, or zero/unknown fields.
"""
import datetime as dt
from collections import defaultdict


def usable_model_rows(kma, model, scope="city", include_dry=False):
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
                if not wet and not include_dry:
                    continue
                out[(date,item["city"])][model_name]=day
                if wet:
                    wet_days+=1

    return dict(out), wet_days>0

def combined_model_rows(kma,model):
    """Validated IFS/AIFS city/county + gu points combined per date/label.

    All verified dry model values are kept while constructing the joined rows,
    so e.g. IFS=0 does not become "unavailable" when AIFS predicts rain.
    Show only regions with actual nonzero rain/snow from at least one model.
    Both scopes must independently satisfy the same freshness gate.
    """
    city,city_wet=usable_model_rows(kma,model,scope="city",include_dry=True)
    district,district_wet=usable_model_rows(kma,model,scope="district",include_dry=True)
    combined={}
    for source,records in (("city_county",city),("gu",district)):
        for key,model_values in records.items():
            row=combined.setdefault(key,{"scope":source,"series":{}})
            if row["scope"]!=source:
                raise ValueError("DUPLICATE_ECMWF_CITY_AND_GU_LABEL")
            for name,day in model_values.items():
                if name in row["series"]:
                    raise ValueError("DUPLICATE_ECMWF_MODEL_DAY")
                row["series"][name]=day

    def numeric(value):
        return isinstance(value,(int,float)) and not isinstance(value,bool) and value>=0

    def wet(series):
        return any(
            (numeric(day.get("precip_mm")) and day["precip_mm"]>0) or
            (day.get("snowfall_valid") and numeric(day.get("snowfall_cm"))
             and day["snowfall_cm"]>0)
            for day in series.values())
    filtered=[
        {"date":date,"region":region,"scope":row["scope"],"series":row["series"]}
        for (date,region),row in combined.items() if wet(row["series"])
    ]
    def key(row):
        series=row["series"].values()
        precip=[d["precip_mm"] for d in series if numeric(d.get("precip_mm"))]
        snow=[d["snowfall_cm"] for d in row["series"].values()
              if d.get("snowfall_valid") and numeric(d.get("snowfall_cm"))]
        return (row["date"],-max(precip,default=0),-max(snow,default=0),
                row["region"])
    filtered.sort(key=key)
    return filtered,city_wet,district_wet
