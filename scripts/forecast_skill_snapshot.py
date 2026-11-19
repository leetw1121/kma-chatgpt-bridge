#!/usr/bin/env python3
"""Compact forecast snapshot for future station-observation verification.

Snapshotting does not imply accuracy or fetch observations. Archive generated
output in GitHub Actions artifacts, not repository commit history.
"""
import datetime as dt
import json
import sys
from pathlib import Path

from create_precip_report import ROOT, event_for, positive
from ecmwf_report_gate import usable_model_rows

KST=dt.timezone(dt.timedelta(hours=9))


def kma_occurrence(day):
    event=event_for(day)
    if event:
        return True
    precip=day.get("precip_mm")
    if (isinstance(precip,dict) and precip.get("comparable")
        and precip.get("lower")==0 and precip.get("upper")==0):
        return False
    return None


def as_float(x):
    return x if isinstance(x,(int,float)) and not isinstance(x,bool) and x>=0 else None


def snapshot_rows(kma,model):
    """Includes dry predictions, never fills unknowns; no location averages."""
    source_time=kma["generated_at_kst"]
    rows=[]
    for place in kma.get("locations") or []:
        for valid_date,day in (place.get("days") or {}).items():
            total=day.get("precip_mm") or {}
            snow=day.get("snow_cm") or {}
            rows.append({
                "model":"KMA_SHORT","source_time_kst":kma["issue_at_kst"],
                "retrieved_at_kst":source_time,
                "region":place["name"],"level":place.get("level"),
                "valid_date":valid_date,
                "forecast_hours":day.get("hours",0),
                "full_day":bool(day.get("full_day")),
                "predicted_precipitation":kma_occurrence(day),
                "predicted_snow":bool(day.get("forecast_snow") or
                                      day.get("forecast_mixed") or positive(snow)),
                "precip_lower_mm":total.get("lower") if total.get("comparable") else None,
                "precip_upper_mm":total.get("upper") if total.get("comparable") else None,
                "snow_lower_cm":snow.get("lower") if snow.get("comparable") else None,
                "snow_upper_cm":snow.get("upper") if snow.get("comparable") else None,
                "snow_origin":"KMA_official_forecast",
            })
    if model:
        for scope,level in (("city","city_county"),("district","gu")):
            index,_=usable_model_rows(kma,model,scope=scope,include_dry=True)
            for (date,region),series in index.items():
                for name,day in series.items():
                    rain=as_float(day.get("precip_mm"))
                    snow=as_float(day.get("snowfall_cm")) if day.get("snowfall_valid") else None
                    rows.append({
                        "model":name,"source_time_kst":None,
                        # This is API retrieval time, NOT the model initialization.
                        "retrieved_at_kst":model["generated_at_kst"],
                        "region":region,"level":level,"valid_date":date,
                        "forecast_hours":day.get("forecast_hours",0),
                        "full_day":day.get("forecast_hours")==24,
                        "predicted_precipitation":(
                            (rain>0 or (snow is not None and snow>0)) if rain is not None
                            else True if snow is not None and snow>0
                            else False if snow==0 else None),
                        "predicted_snow":snow>0 if snow is not None else None,
                        "precip_lower_mm":rain,"precip_upper_mm":rain,
                        "snow_lower_cm":snow,"snow_upper_cm":snow,
                        "snow_origin":"Open-Meteo_model_derived",
                    })
    keys=set()
    for x in rows:
        k=(x["model"],x["retrieved_at_kst"],x["valid_date"],x["region"])
        if k in keys:
            raise ValueError("DUPLICATE_SNAPSHOT_MODEL_REGION_DATE")
        keys.add(k)
    return sorted(rows,key=lambda x:(x["valid_date"],x["region"],x["model"]))


def write_snapshot(rows,path):
    path=Path(path)
    path.parent.mkdir(exist_ok=True,parents=True)
    with path.open("w",encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r,ensure_ascii=False,separators=(",",":"))+"\n")
    return path


def main():
    data=ROOT/"data"
    kma=json.loads((data/"latest.json").read_text(encoding="utf-8"))
    model_file=data/"ecmwf_regional.json"
    model=json.loads(model_file.read_text(encoding="utf-8")) if model_file.exists() else None
    destination=Path(sys.argv[1]) if len(sys.argv)>1 else data/"forecast_skill_snapshot.jsonl"
    saved=write_snapshot(snapshot_rows(kma,model),destination)
    print("FORECAST_SKILL_SNAPSHOT",saved,saved.stat().st_size)


if __name__=="__main__":
    main()
