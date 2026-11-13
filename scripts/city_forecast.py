#!/usr/bin/env python3
"""City-only KMA rainfall/snowfall reports.

For a city that has gu, aggregate UNIQUE gu representative grids by taking
the maximum across their official reported ranges. This is *not* a citywide
precipitation measurement or sum, nor a forecast for every point in the city.
"""
import csv
import datetime as dt
import io
import json
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def city_name(place):
    name=place["name"]
    return name.rsplit(" ",1)[0] if place["level"]=="gu" else name


def metric_max(values, key):
    """Conservative max-of-grid interval, not sum and not a new KMA forecast."""
    selected=[]
    for record in values:
        x=record.get(key) or {}
        if x.get("comparable") and x.get("lower") is not None:
            selected.append(x)
    if not selected:
        return {"lower":None,"upper":None,"comparable":False,"partial":bool(values)}
    # A null upper bound represents 'at least', NOT zero or a point estimate.
    lower=max(x["lower"] for x in selected)
    upper=None if any(x.get("upper") is None for x in selected) else max(x["upper"] for x in selected)
    return {"lower":round(lower,2),"upper":round(upper,2),
            "comparable":True,"partial":len(selected)!=len(values)}


def group_rows(data):
    grouped=defaultdict(dict)
    for place in data["locations"]:
        name=city_name(place)
        for date,day in place["days"].items():
            key=(date,name)
            grid=(place["nx"],place["ny"])
            grouped[key][grid]={"place":place,"day":day}
    rows=[]
    for (date,name),unique in grouped.items():
        records=list(unique.values())
        days=[record["day"] for record in records]
        rain=any(d.get("forecast_rain") for d in days)
        snow=any(d.get("forecast_snow") for d in days)
        mixed=any(d.get("forecast_mixed") for d in days)
        if not rain and not snow:
            continue
        total=metric_max(days,"precip_mm")
        rainfall=metric_max(days,"rain_mm")
        snowfall=metric_max(days,"snow_cm")
        pop=[d.get("max_pop_pct") for d in days if d.get("max_pop_pct") is not None]
        # If some gu have different valid time windows, mark it as partial.
        starts=[d["from_kst"] for d in days]
        ends=[d["to_kst"] for d in days]
        full=all(d.get("full_day") for d in days) and len(set(starts))==1 and len(set(ends))==1
        label="시 대표격자" if len(records)==1 and records[0]["place"]["level"]=="city" else "시내 구 대표격자 중 최대치"
        rows.append({
            "date":date,"city":name,"rain":rain,"snow":snow,"mixed":mixed,
            "precip":total,"rain_mm":rainfall,"snow_cm":snowfall,
            "max_pop":max(pop) if pop else None,
            "method":label,"gu_count":sum(v["place"]["level"]=="gu" for v in records),
            "grid_count":len(records),"source_names":[v["place"]["name"] for v in records],
            "from_kst":min(starts),"to_kst":max(ends),"full_day":full,
            "data_partial":total["partial"] or snowfall["partial"] or not full,
        })
    return sorted(rows,key=lambda x:(x["date"],x["city"]))


def rank(rows, weather):
    """Descending published upper estimate, then lower bound; ties are not definitive."""
    metric="precip" if weather=="rain" else "snow_cm"
    subset=[r for r in rows if r["rain" if weather=="rain" else "snow"]]
    def key(r):
        v=r[metric]
        valid=v["comparable"] and v["lower"] is not None
        # No valid numeric amount sorts after regions with a published amount.
        high=v["upper"] if valid and v["upper"] is not None else (float("inf") if valid else -1)
        low=v["lower"] if valid else -1
        return (-high,-low,r["city"])
    return sorted(subset,key=key)


def fmt(v,unit):
    if not v["comparable"] or v["lower"] is None:return "정량 미제공"
    lower=v["lower"];upper=v["upper"]
    if upper is None:return f"{lower:g}{unit} 이상(상한 없음)"
    if upper==lower:return f"{lower:g}{unit}"
    return f"{lower:g}~{upper:g}{unit}"


def city_markdown(data):
    events=group_rows(data)
    days=sorted({r["date"] for r in events})
    lines=["# 전국 시 단위 비·눈 예보 — 강수·신적설량 내림차순","",
        "- 기상청 발표: "+data["issue_at_kst"],
        "- 자동 수집: "+data["generated_at_kst"],
        "- 원본 조회 성공: "+str(data["locations_succeeded"])+"/"+str(data["locations_checked"])+" 구·시",
        "- 구는 시 단위로 합쳤습니다. 구가 있는 시는 **구별 대표 격자 중 가장 큰 정량 예보 범위**로 비교하며 시 전체 평균·합계가 아닙니다.",
        "- **참고 정렬**: 상한 기준 큰 순. 범위가 겹치면 실제 순위는 확정할 수 없습니다.",
        "- 비·눈 예상이 없는 시는 제외하며, 기상청 단기 API가 제공하지 않는 7일 구별 정량값은 생성하지 않습니다.",""]
    for day in days:
        lines+=["## "+day,""]
        for weather,label,field,unit in (("rain","비·총강수량","precip","mm"),
                                          ("snow","눈·신적설","snow_cm","cm")):
            subset=rank([r for r in events if r["date"]==day],weather)
            if not subset:continue
            lines+=["### "+label+" · "+str(len(subset))+"개 시","",
              "| 참고순서 | 시 | 예보량 | 최고 시각별 POP | 범위/좌표 근거 |",
              "|---:|---|---:|---:|---|"]
            for i,row in enumerate(subset,1):
                note=row["method"]+(" · 일부 시각" if not row["full_day"] else "")
                lines.append("| "+str(i)+" | "+row["city"]+" | "+fmt(row[field],unit)+
                   " | "+(str(row["max_pop"])+"%" if row["max_pop"] is not None else "미발표")+
                   " | "+note+" |")
            lines.append("")
    if not events:lines.append("조회한 단기예보 범위 내 비·눈 예상 도시 없음.")
    return "\n".join(lines)+"\n"


def city_csv(data):
    records=group_rows(data)
    cols=["date","city","rain_forecast","snow_forecast","mixed","precip_lower_mm",
        "precip_upper_mm","snow_lower_cm","snow_upper_cm","max_hourly_pop",
        "method","unique_grids","gu_count","from_kst","to_kst","full_day"]
    out=io.StringIO()
    w=csv.DictWriter(out,fieldnames=cols,lineterminator="\n")
    w.writeheader()
    for r in records:
        w.writerow({"date":r["date"],"city":r["city"],"rain_forecast":int(r["rain"]),
         "snow_forecast":int(r["snow"]),"mixed":int(r["mixed"]),
         "precip_lower_mm":r["precip"]["lower"],"precip_upper_mm":r["precip"]["upper"],
         "snow_lower_cm":r["snow_cm"]["lower"],"snow_upper_cm":r["snow_cm"]["upper"],
         "max_hourly_pop":r["max_pop"],"method":r["method"],
         "unique_grids":r["grid_count"],"gu_count":r["gu_count"],
         "from_kst":r["from_kst"],"to_kst":r["to_kst"],"full_day":int(r["full_day"])})
    return out.getvalue()


def main():
    data=json.loads((ROOT/"data/latest.json").read_text(encoding="utf-8"))
    out=ROOT/"data"
    out.joinpath("city_precipitation.md").write_text(city_markdown(data),encoding="utf-8")
    out.joinpath("city_precipitation.csv").write_text(city_csv(data),encoding="utf-8-sig")
    print("City-level precipitation records:",len(group_rows(data)))


if __name__=="__main__":
    main()
