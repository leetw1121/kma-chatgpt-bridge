#!/usr/bin/env python3
"""Publish ONLY KMA forecast rain/snow regions, never truncate to TOP 10.

Not a substitute for KMA mid-range forecasts; keep unprovided periods absent.
"""
import csv
import datetime as dt
import io
import json
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

KST=ZoneInfo("Asia/Seoul")
ROOT=Path(__file__).resolve().parents[1]


def estimate(v,unit):
    if not isinstance(v,dict) or not v.get("comparable") or v.get("lower") is None:
        return "정량 미제공"
    lo=v["lower"];hi=v.get("upper")
    if hi is None:return f"{lo}{unit} 이상"
    if lo==hi:return f"{lo}{unit}"
    return f"{lo}~{hi}{unit} (범위)"


def positive(v):
    if not isinstance(v,dict) or not v.get("comparable"):return False
    lo=v.get("lower");hi=v.get("upper")
    return (lo is not None and lo>0) or (hi is not None and hi>0)


def event_for(day):
    """Include forecast rain/snow signals, NOT dry/unknown rows."""
    rain=bool(day.get("forecast_rain",False))
    snow=bool(day.get("forecast_snow",False))
    mixed=bool(day.get("forecast_mixed",False))
    if mixed: return "비·눈 혼합"
    if rain and snow: return "비·눈"
    if snow or positive(day.get("snow_cm")): return "눈"
    if rain: return "비"
    if positive(day.get("rain_mm")): return "비"
    if positive(day.get("precip_mm")):
        # PCP measures total rain/snow water equivalent. Do not assume rain.
        return "강수(비·눈 구분 미확인)"
    return None


def rows(data):
    """Every *forecast precipitation* region/date, no arbitrary ranking cutoff."""
    shared=defaultdict(list)
    for place in data["locations"]:
        shared[(place["nx"],place["ny"])].append(place["name"])
    result=[]
    for place in data["locations"]:
        for date,day in place["days"].items():
            kind=event_for(day)
            if not kind: continue
            result.append({
                "date":date,"level":place["level"],"region":place["name"],
                "forecast":kind,
                "precipitation":estimate(day.get("precip_mm"),"mm"),
                "rain":estimate(day.get("rain_mm"),"mm") if "비" in kind else "해당 없음",
                "snow":estimate(day.get("snow_cm"),"cm") if "눈" in kind else "해당 없음",
                "max_hourly_pop":(str(day["max_pop_pct"])+"%" if day.get("max_pop_pct") is not None else "미발표"),
                "from_kst":day["from_kst"],"to_kst":day["to_kst"],
                "slots":day["hours"],"period":("24시간" if day.get("full_day") else "일부 시각"),
                "nx":place["nx"],"ny":place["ny"],
                "shared_grid":len(shared[(place["nx"],place["ny"])])>1,
                "sort_lower":(day.get("precip_mm",{}) or {}).get("lower"),
                "sort_snow":(day.get("snow_cm",{}) or {}).get("lower"),
            })
    # All rain/snow forecast locations retained, sorted within each date/group.
    result.sort(key=lambda r:(r["date"], r["level"]!="gu",
                -(r["sort_snow"] or 0) if r["forecast"]=="눈" else -(r["sort_lower"] or 0),
                r["region"]))
    return result


def render(data):
    events=rows(data)
    counts=defaultdict(lambda:{"gu":0,"city":0})
    for x in events:counts[x["date"]][x["level"]]+=1
    lines=[
        "# 🌧️❄️ 전국 비·눈 예상지역 전체 목록",
        "",
        "- **이번 수집에서 확인된 비·눈 예보 지역만 표시**합니다. 비·눈 예보가 없는 지역, 단순 미발표 지역은 표시하지 않습니다.",
        "- 기상청 공식 API 발표(KST): **"+data["issue_at_kst"]+"**",
        "- GitHub 자동 수집 완료(KST): **"+data["generated_at_kst"]+"**",
        "- 조회 성공: **"+str(data["locations_succeeded"])+"/"+str(data["locations_checked"])+"개 지역**, 고유 격자 **"+str(data.get("unique_grids_succeeded","?"))+"/"+str(data.get("unique_grids_checked","?"))+"개**",
        "- 비·눈 예상 지역/날짜 행: **"+str(len(events))+"건** (서로 다른 날짜의 같은 지역은 중복하여 나타납니다)",
        "- 구·시의 **대표 격자** 예보입니다. 같은 격자 공유 지역은 동일한 수치이고, 구 전체 평균·최대값을 뜻하지 않습니다.",
        "- 기상청 PCP는 비·눈의 총강수량(mm), SNO는 신적설(cm)입니다. POP는 해당 기간 중 시각별 최대 확률로서 하루 전체 확률이 아닙니다.",
        "- 강수량의 숫자 범위가 겹치면 정확한 순위를 확정할 수 없습니다. 일부 시간대는 하루 24시간 강수량과 비교할 수 없습니다.",
        "- 공식 단기예보가 제공하지 않는 날짜의 지역별 정량값은 지어내지 않으며, 7일 중기 전망은 별도의 기상청 중기예보를 확인해야 합니다.",
        "",
        "## 날짜별 비·눈 예상 모든 지역",
        "",
    ]
    groups=defaultdict(list)
    for x in events:groups[(x["date"],x["level"])].append(x)
    for (date,level),values in sorted(groups.items()):
        head="구(區)" if level=="gu" else "구가 없는 시(市)"
        lines += ["### "+date+" · "+head+" · "+str(len(values))+"곳","",
            "| 지역 | 비·눈 형태 | 총강수(mm) | 비 강수(mm) | 신적설(cm) | 최고 시각별 POP | 예보 시간 |",
            "|---|---|---:|---:|---:|---:|---|"]
        for x in values:
            name=x["region"]+(" (공유 격자)" if x["shared_grid"] else "")
            times=x["from_kst"][11:16]+"~"+x["to_kst"][11:16]+" ("+str(x["slots"])+"개 시각, "+x["period"]+")"
            lines.append("| "+name+" | "+x["forecast"]+" | "+x["precipitation"]+" | "+x["rain"]+" | "+x["snow"]+" | "+x["max_hourly_pop"]+" | "+times+" |")
        lines.append("")
    if not events:
        lines += ["현재 최신 KMA 단기 API에 **실제 강수나 눈 예보가 확인된 지역이 없습니다.** 중기/특보는 별도 확인이 필요합니다.",""]
    lines += ["## 실제 수집 성공한 예보 출처","",
        "- **기상청 KMA 단기예보 API**: 전국 구·시 대표 격자 최신 자료",
        "- **ECMWF IFS·AIFS**: 실제 한국 도시 데이터 조회에 성공한 모델에 한하여 [교차검증 상태](source_status.md) 확인",
        "",
        "[비·눈 예상지역 CSV 전체 보기](precipitation_only.csv)",
        "",
        "본문에는 무강수 지역과 현재 조회할 수 없는 예보 제공사를 포함하지 않습니다.",""]
    return "\n".join(lines)


def as_csv(data):
    values=rows(data)
    cols=["date","level","region","forecast","precipitation","rain","snow",
          "max_hourly_pop","from_kst","to_kst","slots","period","nx","ny","shared_grid"]
    output=io.StringIO()
    w=csv.DictWriter(output,fieldnames=cols,extrasaction="ignore",lineterminator="\n")
    w.writeheader()
    for v in values:w.writerow(v)
    return output.getvalue()


def main():
    base=ROOT/"data"
    data=json.loads((base/"latest.json").read_text(encoding="utf-8"))
    base.joinpath("precipitation_only.md").write_text(render(data),encoding="utf-8")
    base.joinpath("precipitation_only.csv").write_text(as_csv(data),encoding="utf-8-sig")
    print("Only wet/snow forecast rows:",len(rows(data)),"of",data["locations_succeeded"],"collected regions")

if __name__=="__main__":
    main()
