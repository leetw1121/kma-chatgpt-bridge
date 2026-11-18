#!/usr/bin/env python3
"""Auditable KMA-wet-region vs independent ECMWF data availability.

A KMA forecast-positive location does NOT imply ECMWF rain/snow.
Never label missing model data 0 mm, or copy KMA metrics into model columns.
"""
import csv
import io
import json
from collections import defaultdict
from pathlib import Path

from create_precip_report import rows as kma_precip_rows, ROOT
from ecmwf_report_gate import usable_model_rows

SCOPE={"gu":"district","city":"city","gun":"city"}
MODELS={"IFS":"IFS","AIFS":"AIFS Single"}

def coverage_rows(kma,model,events=None):
    events=kma_precip_rows(kma) if events is None else events
    model=model if isinstance(model,dict) else {}
    indexes={}
    metadata={}
    targets={}
    problems={}
    for scope in ("city","district"):
        rows,_=usable_model_rows(kma,model,scope=scope,include_dry=True)
        indexes[scope]=rows
        source=model.get("models" if scope=="city" else "district_models",{})
        for modelname in MODELS.values():
            entry=source.get(modelname) or {}
            records=entry.get("records") or []
            for record in records:
                if not isinstance(record,dict) or not record.get("city"):
                    continue
                label=record["city"]
                targets[scope,modelname,label]=True
                metadata[scope,modelname,label]={
                    "cached":bool(record.get("cached_from_previous_run")),
                    "collected_at":record.get("collected_at_kst",
                                               model.get("generated_at_kst",""))}
            for error in entry.get("errors") or []:
                if not isinstance(error,dict) or not error.get("city"):
                    continue
                label=error["city"]
                targets[scope,modelname,label]=True
                problems[scope,modelname,label]=error.get("reason","UNKNOWN")

    output=[]
    for event in events:
        date=event["date"]
        region=event["region"]
        scope=SCOPE.get(event["level"],"city")
        row={"date":date,"region":region,"level":event["level"],
             "kma_forecast":event["forecast"],
             "kma_precipitation":event["precipitation"],
             "kma_snow":event["snow"]}
        for key,modelname in MODELS.items():
            data=indexes[scope].get((date,region),{}).get(modelname)
            meta=metadata.get((scope,modelname,region),{})
            if data is not None:
                snow=data.get("snowfall_cm") if data.get("snowfall_valid") else None
                rain=data.get("precip_mm")
                wet=((isinstance(rain,(int,float)) and rain>0) or
                     (isinstance(snow,(int,float)) and snow>0))
                note="예보 있음" if wet else "무강수" if rain==0 and snow==0 else "수치 일부 미제공"
                if meta.get("cached"):
                    note+=" · 최근 성공본"
                row[key+"_status"]=note
                row[key+"_precip_mm"]=rain if isinstance(rain,(int,float)) else ""
                row[key+"_snow_cm"]=snow if isinstance(snow,(int,float)) else ""
                row[key+"_collected_at"]=meta.get("collected_at","")
            else:
                cause=problems.get((scope,modelname,region))
                if cause:
                    note=("수집 제한(429)" if cause in ("API_RATE_LIMITED","RATE_LIMITED_COOLDOWN")
                          else "시간제한 미조회" if "BUDGET" in cause
                          else "수집 실패" if cause!="NETWORK_CIRCUIT_OPEN"
                          else "네트워크 장애 미조회")
                elif (scope,modelname,region) not in targets:
                    note="ECMWF 대상 목록/응답 없음"
                else:
                    note="해당 날짜 모델값 미제공"
                row[key+"_status"]=note
                row[key+"_precip_mm"]=""
                row[key+"_snow_cm"]=""
                row[key+"_collected_at"]=""
        output.append(row)
    return output


def daily_counts(rows):
    result=defaultdict(lambda:{"kma_wet":0,"IFS_checked":0,"IFS_missing":0,
                               "AIFS_checked":0,"AIFS_missing":0})
    for r in rows:
        d=result[r["date"]]
        d["kma_wet"]+=1
        for name in ("IFS","AIFS"):
            if r[name+"_status"].startswith(("예보 있음","무강수","수치 일부")):
                d[name+"_checked"]+=1
            else:
                d[name+"_missing"]+=1
    return dict(sorted(result.items()))


FIELDS=("date","region","level","kma_forecast","kma_precipitation","kma_snow",
        "IFS_status","IFS_precip_mm","IFS_snow_cm","IFS_collected_at",
        "AIFS_status","AIFS_precip_mm","AIFS_snow_cm","AIFS_collected_at")

def csv_report(rows):
    output=io.StringIO()
    writer=csv.DictWriter(output,fieldnames=FIELDS,lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()

def markdown_report(rows):
    summary=daily_counts(rows)
    lines=["# 기상청 비·눈 예상지역의 ECMWF 실제 확인 여부","",
           "기상청 예상지역을 ECMWF 비·눈 예보로 복제하지 않습니다. 확인된 0mm와 수집실패는 구별합니다.",
           "ECMWF는 모델별 별도 대표점 예보이므로 기상청 결과와 일치할 필요가 없습니다.","",
           "| 날짜 | 기상청 비·눈 지역 | IFS 확인 | IFS 미확인 | AIFS 확인 | AIFS 미확인 |",
           "|---|---:|---:|---:|---:|---:|"]
    for date,c in summary.items():
        lines.append(f"| {date} | {c['kma_wet']} | {c['IFS_checked']} | "
                     f"{c['IFS_missing']} | {c['AIFS_checked']} | {c['AIFS_missing']} |")
    lines.extend(["","## 지역별 확인 현황","",
                  "| 날짜 | 기상청 지역 | IFS | AIFS |",
                  "|---|---|---|---|"])
    for r in rows:
        lines.append(f"| {r['date']} | {r['region']} | {r['IFS_status']} | "
                     f"{r['AIFS_status']} |")
    lines.extend(["","[전체 확인현황 CSV](ecmwf_kma_coverage.csv)",""])
    return "\n".join(lines)

def main():
    kma=json.loads((ROOT/"data/latest.json").read_text(encoding="utf-8"))
    path=ROOT/"data/ecmwf_regional.json"
    model=json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    rows=coverage_rows(kma,model)
    (ROOT/"data/ecmwf_kma_coverage.csv").write_text(csv_report(rows),encoding="utf-8-sig")
    (ROOT/"data/ecmwf_kma_coverage.md").write_text(markdown_report(rows),encoding="utf-8")
    print("ECMWF KMA coverage audit:",daily_counts(rows),flush=True)

if __name__=="__main__":
    main()
