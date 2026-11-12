#!/usr/bin/env python3
"""KMA village forecast -> safe public GitHub feed. Python stdlib only."""
import csv
import concurrent.futures
import datetime as dt
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
ROOT = Path(__file__).resolve().parents[1]
API = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
HOURS = (2, 5, 8, 11, 14, 17, 20, 23)
FIELDS = ("PCP", "SNO", "POP", "PTY")

def issues(now):
    """Use the latest expected published forecast and three fallbacks."""
    cutoff = now.astimezone(KST) - dt.timedelta(minutes=18)
    result = []
    for delta in (0, 1, 2):
        d = cutoff.date() - dt.timedelta(days=delta)
        for h in reversed(HOURS):
            at = dt.datetime.combine(d, dt.time(h, tzinfo=KST))
            if at <= cutoff:
                result.append(at)
    return sorted(result, reverse=True)[:4]

def one_page(secret, issue, nx, ny, page=1):
    args = {"serviceKey": secret, "dataType": "JSON", "pageNo": page,
            "numOfRows": 1500, "base_date": issue.strftime("%Y%m%d"),
            "base_time": issue.strftime("%H%M"), "nx": nx, "ny": ny}
    url = API + "?" + urllib.parse.urlencode(args)
    request = urllib.request.Request(url, headers={"Accept": "application/json",
                                                    "User-Agent": "KMA-Weather-Bridge/1.0"})
    for trial in range(3):
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                body = json.loads(response.read(10000000).decode("utf-8-sig"))
            if str(body["response"]["header"]["resultCode"]) != "00":
                raise ValueError("KMA_API_ERROR_" + re.sub("[^0-9A-Za-z]","",str(body["response"]["header"]["resultCode"])))
            data = body["response"]["body"]
            rows = (data.get("items") or {}).get("item") or []
            return (rows if isinstance(rows,list) else [rows]), int(data["totalCount"])
        except urllib.error.HTTPError as exc:
            if exc.code in (429,500,502,503,504) and trial < 2:
                time.sleep(2*(trial+1))
                continue
            raise ValueError("HTTP_STATUS_"+str(exc.code)) from None
        except urllib.error.URLError:
            if trial < 2:
                time.sleep(2*(trial+1))
                continue
            raise ValueError("NETWORK_ERROR") from None
        except (KeyError,TypeError,json.JSONDecodeError,UnicodeError):
            raise ValueError("INVALID_KMA_RESPONSE") from None

def all_pages(secret, issue, nx, ny):
    rows,total = one_page(secret,issue,nx,ny)
    for p in range(2,math.ceil(total/1500)+1):
        more,_ = one_page(secret,issue,nx,ny,p)
        rows.extend(more)
    if len(rows) != total or total < 50:
        raise ValueError("PARTIAL_KMA_RESPONSE")
    return rows

def interval(value, category):
    s = str(value).replace(" ","").strip()
    unit = "mm" if category == "PCP" else "cm"
    if s in ("강수없음","적설없음","없음","0","0.0","0mm","0cm","0.0mm","0.0cm"):
        return 0.0,0.0
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(?:mm|cm)?",s)
    if m: return float(m[1]),float(m[1])
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(?:mm|cm)미만",s)
    if m: return 0.0,float(m[1])
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(?:~|～|-)(\d+(?:\.\d+)?)(?:mm|cm)",s)
    if m: return float(m[1]),float(m[2])
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(?:mm|cm)이상",s)
    if m: return float(m[1]),None
    return None,None

def daily_summary(rows,now):
    window_start = now.astimezone(KST)
    window_end = window_start + dt.timedelta(hours=168)
    times = defaultdict(dict)
    for row in rows:
        cat = row.get("category")
        if cat not in FIELDS: continue
        try:
            at = dt.datetime.strptime(str(row["fcstDate"])+str(row["fcstTime"]).zfill(4),"%Y%m%d%H%M").replace(tzinfo=KST)
        except (ValueError,KeyError,TypeError):
            continue
        if window_start <= at < window_end:
            times[at][cat] = row.get("fcstValue")
    byday = defaultdict(list)
    for at in sorted(times):
        byday[at.date().isoformat()].append((at,times[at]))
    result = {}
    for day,slots in byday.items():
        contiguous = int((slots[-1][0]-slots[0][0]).total_seconds()/3600)+1 == len(slots)
        full_day=(len(slots)==24 and slots[0][0].hour==0 and slots[-1][0].hour==23 and contiguous)
        result[day] = {"from_kst":slots[0][0].isoformat(),"to_kst":slots[-1][0].isoformat(),
                       "hours":len(slots),"full_day":full_day,"max_pop_pct":None}
        pop=[]
        for _,data in slots:
            try: pop.append(int(data["POP"]))
            except (KeyError,ValueError,TypeError): pass
        if pop: result[day]["max_pop_pct"]=max(pop)
        for cat,name,unit in (("PCP","precip_mm","mm"),("SNO","snow_cm","cm")):
            lo=0.0;hi=0.0;valid=contiguous
            for _,data in slots:
                if cat not in data:
                    valid=False
                    continue
                low,high=interval(data[cat],cat)
                if low is None:
                    valid=False
                    continue
                lo+=low
                if hi is not None:
                    hi=None if high is None else hi+high
            result[day][name]={"unit":unit,"comparable":bool(valid),
                                "lower":round(lo,2) if valid else None,
                                "upper":round(hi,2) if valid and hi is not None else None}
        # PCP is TOTAL precipitation (including water-equivalent snow), not rain-only.
        # Keep rain_mm for old consumers, but suppress it whenever snow/mixed type is forecast.
        pty_values={str(row.get("PTY","")).strip() for _,row in slots}
        has_frozen_precip=any(code in {"2","3","6","7"} for code in pty_values)
        original=result[day]["precip_mm"]
        result[day]["rain_mm"]=(dict(original) if not has_frozen_precip else
            {"unit":"mm","comparable":False,"lower":None,"upper":None,
             "reason":"mixed_or_snow_precipitation"})
        result[day]["precip_includes_snow"]=has_frozen_precip
    return result

def load_locations():
    with (ROOT/"locations.csv").open(encoding="utf-8-sig",newline="") as file:
        lines=list(csv.DictReader(file))
    if not lines: raise ValueError("NO_LOCATIONS")
    locations=[]
    for row in lines:
        nx,ny=int(row["nx"]),int(row["ny"])
        if row["level"] not in ("gu","city") or not (1<=nx<=149 and 1<=ny<=253):
            raise ValueError("INVALID_LOCATION")
        locations.append({"name":row["name"],"level":row["level"],"nx":nx,"ny":ny})
    return locations

def build(secret,locations,now):
    warnings=[]
    issue=None
    first=None
    for candidate in issues(now):
        try:
            first=all_pages(secret,candidate,locations[0]["nx"],locations[0]["ny"])
            issue=candidate
            break
        except ValueError as exc:
            warnings.append("issue_"+candidate.strftime("%Y%m%d%H%M")+"_"+str(exc))
    if issue is None: raise ValueError("NO_AVAILABLE_FORECAST_ISSUE")
    grids=sorted({(place["nx"],place["ny"]) for place in locations})
    first_grid=(locations[0]["nx"],locations[0]["ny"])
    cache={first_grid:first}
    failed={}
    # Unique grid fetches only; bounded parallelism keeps the 10,000-call/day API quota safe.
    remaining=[g for g in grids if g!=first_grid]
    if remaining:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures={pool.submit(all_pages,secret,issue,*grid):grid for grid in remaining}
            for future in concurrent.futures.as_completed(futures):
                grid=futures[future]
                try:
                    cache[grid]=future.result()
                except ValueError as exc:
                    failed[grid]=str(exc)
    collected=[]
    for place in locations:
        grid=(place["nx"],place["ny"])
        if grid in cache:
            collected.append({**place,"days":daily_summary(cache[grid],now)})
        else:
            warnings.append("location_failed_"+place["name"]+"_"+failed.get(grid,"UNKNOWN_ERROR"))
    if not collected: raise ValueError("NO_SUCCESSFUL_LOCATIONS")
    finished=dt.datetime.now(KST)
    # The run can succeed partially, but report exact coverage and prevent silent gaps.
    return {"schema_version":"1.1","source":"KMA getVilageFcst",
            "generated_at_kst":finished.isoformat(),
            "collection_started_at_kst":now.astimezone(KST).isoformat(),
            "issue_at_kst":issue.isoformat(),
            "coverage_note":"Only returned KMA short-range forecast hours; not 168h of district totals.",
            "ranking_note":"Representative grids only. Duplicate districts share the same forecast; intervals can overlap.",
            "locations_checked":len(locations),"locations_succeeded":len(collected),
            "unique_grids_checked":len(grids),"unique_grids_succeeded":len(cache),
            "warnings":warnings[:30],"locations":collected}

def render(data):
    lines=["# 기상청 구·시 단기예보 — 확인된 대표 격자만 비교","",
      "- 수집 시각(KST): **"+data["generated_at_kst"]+"**",
      "- 기상청 발표 기준(KST): **"+data["issue_at_kst"]+"**",
      "- 조회 성공: "+str(data["locations_succeeded"])+"/"+str(data["locations_checked"])+" 지역 / 고유 격자 "+str(data.get("unique_grids_succeeded","?"))+"/"+str(data.get("unique_grids_checked","?"))+"개",
      "- 주의: 구·시 전체 평균이 아닌 대표 격자 예보입니다. 조회하지 않은 격자에 대한 전국 전수 순위가 아닙니다.",
      "- 시간별 강수량/적설의 범주를 합산한 것은 범위이며 정확한 단일 수치가 아닙니다.",
      "- 같은 5km 격자를 공유하는 구는 하나의 예보로 묶습니다. 예보량 범위가 겹치면 실제 우열은 확정할 수 없습니다.",
      "- 표의 강수량(PCP)은 비/눈을 포함한 총강수량입니다. 눈이 포함된 시점은 비만의 양으로 단정하지 않습니다.",
      "- '최고 시각별 강수확률'은 해당 기간 중 최대 한 시각의 확률이며 일별 누적 확률이 아닙니다.",
      "- 표의 실제 시간 범위만 유효합니다. 7일 전체 구별 정량 예보가 아닙니다.",""]
    # Multiple districts can share one 5km grid: never count them as independent forecasts.
    groups=defaultdict(dict)
    for place in data["locations"]:
        if place["level"] not in ("gu","city"):continue
        for day,values in place["days"].items():
            key=(day,place["level"],values["from_kst"],values["to_kst"],values["hours"])
            grid=(place["nx"],place["ny"])
            if grid in groups[key]:
                merged,previous=groups[key][grid]
                merged["name"] += " · " + place["name"] + " (동일 격자)"
            else:
                groups[key][grid]=({**place},values)
    for key,grid_entries in sorted(groups.items(),key=lambda item:(item[0][0],item[0][1]!="gu",item[0][2])):
        entries=list(grid_entries.values())
        day,level,start,end,hours=key
        tier_name="구(區)" if level=="gu" else "시(市)"
        lines+=["## "+day+" / "+tier_name+" · "+start[11:16]+"~"+end[11:16]+" KST ("+str(hours)+"개 시각)",""]
        for col,title in (("precip_mm","🌧️ 강수량(비·눈 포함)"),("snow_cm","❄️ 신적설")):
            sortable=[]
            for place,values in entries:
                val=values[col]
                if not val["comparable"] or val["lower"] is None:continue
                if (val["upper"] if val["upper"] is not None else val["lower"])<=0:continue
                sortable.append((place,values,val))
            sortable.sort(key=lambda x:(x[2]["lower"],x[2]["upper"] if x[2]["upper"] is not None else float("inf")),reverse=True)
            complete_day=(hours==24 and start[11:16]=="00:00" and end[11:16]=="23:00")
            label="24시간 전체" if complete_day else "일부 시각만"
            lines+=["### "+title+" · 조회된 고유 격자 "+str(len(sortable))+"개 · "+label,""]
            if sortable:
                lines+=["예보량 범위가 겹치는 지역의 선후 순위는 확정할 수 없습니다.",""]
                lines+=["| 참고순서 | 구·시 대표 격자 | 예보량 | 최고 시각별 강수확률 |",
                        "|---:|---|---:|---:|"]
                last_range=None
                shown_rank=0
                for i,(place,values,val) in enumerate(sortable[:10],1):
                    value_range=(val["lower"],val["upper"])
                    if value_range != last_range: shown_rank=i
                    last_range=value_range
                    low,high=val["lower"],val["upper"];unit=val["unit"]
                    amount=(str(low)+unit+" 이상(상한 미확인)" if high is None else
                            str(low)+unit if low==high else str(low)+"~"+str(high)+unit+"(범위)")
                    prob=values["max_pop_pct"]
                    lines.append("| "+str(shown_rank)+" | "+place["name"]+" | "+amount+" | "+(str(prob)+"%" if prob is not None else "미확인")+" |")
            else: lines.append("정량적으로 비교 가능한 비/눈 예보 지역 없음.")
            lines.append("")
    if data["warnings"]:
        lines+=["## 조회 주의","",str(len(data["warnings"]))+"건의 발표본/지역 조회 실패가 있습니다.",""]
    lines+=["---","인증키를 포함하지 않는 공개 예보 자료입니다.",""]
    return "\n".join(lines)

def main():
    secret=os.getenv("KMA_SERVICE_KEY","").strip()
    if not secret:
        print("Missing KMA_SERVICE_KEY GitHub Actions secret",file=sys.stderr)
        return 2
    try:
        forecast=build(secret,load_locations(),dt.datetime.now(KST))
        directory=ROOT/"data"
        directory.mkdir(exist_ok=True)
        temp=directory/"latest.json.tmp"
        temp.write_text(json.dumps(forecast,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        temp.replace(directory/"latest.json")
        (directory/"summary.md").write_text(render(forecast),encoding="utf-8")
        print("KMA data collected: "+str(forecast["locations_succeeded"])+"/"+str(forecast["locations_checked"]))
        return 0
    except (ValueError,OSError) as exc:
        # Do not log URLs: the request URL contains a secret.
        print("KMA collection failed: "+str(exc),file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
