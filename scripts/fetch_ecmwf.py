#!/usr/bin/env python3
"""Optional independent ECMWF IFS / AIFS checks via Open-Meteo.

Uses city-centre approximate coordinates ONLY for regional cross-check, never as
an official KMA district-grid value. Failure does not block official KMA feed.
"""
import datetime as dt
import json
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
KST=ZoneInfo("Asia/Seoul")
URL="https://api.open-meteo.com/v1/forecast"
CITIES={
    "서울":(37.5665,126.9780),
    "대전":(36.3504,127.3845),
    "광주":(35.1595,126.8526),
    "부산":(35.1796,129.0756),
    "울산":(35.5384,129.3114),
    "서귀포":(33.2541,126.5601),
    "강릉":(37.7519,128.8761),
}
MODELS={"IFS":"ecmwf_ifs","AIFS Single":"ecmwf_aifs025_single"}

def fetch_one(model,city,latitude,longitude,now):
    query={"latitude":latitude,"longitude":longitude,"hourly":"precipitation,snowfall",
           "models":model,"timezone":"Asia/Seoul","forecast_hours":168}
    req=urllib.request.Request(URL+"?"+urllib.parse.urlencode(query),
       headers={"User-Agent":"KMA-ChatGPT-Bridge/1.0","Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=15) as resp:
        payload=json.loads(resp.read(350000).decode("utf-8"))
    hourly=payload.get("hourly")
    if not isinstance(hourly,dict):
        raise ValueError("MISSING_HOURLY")
    times=hourly.get("time")
    # Open-Meteo can return variable name suffixes on explicitly selected models.
    def series_for(name):
        x=hourly.get(name)
        if not isinstance(x,list):
            keys=[k for k in hourly if k.startswith(name+"_") and isinstance(hourly[k],list)]
            if len(keys)==1: x=hourly[keys[0]]
        return x
    precip=series_for("precipitation")
    snow=series_for("snowfall")
    if not isinstance(times,list) or not isinstance(precip,list) or len(times)!=len(precip) or len(times)<12:
        raise ValueError("INVALID_PRECIPITATION_SERIES")
    if snow is not None and (not isinstance(snow,list) or len(snow)!=len(times)):
        raise ValueError("INVALID_SNOWFALL_SERIES")
    window_end=now+dt.timedelta(hours=168)
    byday={}
    valid=0
    for index,string in enumerate(times):
        try:
            at=dt.datetime.fromisoformat(string)
            if at.tzinfo is None:at=at.replace(tzinfo=KST)
            at=at.astimezone(KST)
        except (ValueError,TypeError):
            continue
        if not now<=at<window_end:continue
        pv=precip[index]
        if not isinstance(pv,(int,float)) or pv<0:continue
        day=at.date().isoformat()
        obj=byday.setdefault(day,{"forecast_hours":0,"precip_mm":0.0,
                                   "snowfall_cm":0.0,"snowfall_valid":True})
        obj["forecast_hours"]+=1
        obj["precip_mm"]+=pv
        if snow is not None and isinstance(snow[index],(int,float)) and snow[index]>=0:
            obj["snowfall_cm"]+=snow[index]
        else:
            obj["snowfall_valid"]=False
        valid+=1
    if valid<12 or not byday:
        raise ValueError("NO_VALID_KOREA_FORECAST_HOURS")
    for d in byday.values():
        d["precip_mm"]=round(d["precip_mm"],1)
        if d["snowfall_valid"]:d["snowfall_cm"]=round(d["snowfall_cm"],1)
        else:d["snowfall_cm"]=None
    return {"city":city,"lat":latitude,"lon":longitude,
            "actual_grid_lat":payload.get("latitude"),"actual_grid_lon":payload.get("longitude"),
            "timezone":payload.get("timezone"),"days":byday,"hours_valid":valid}

def collect(now):
    out={"generated_at_kst":now.isoformat(),"source":"Open-Meteo ECMWF IFS/AIFS open data",
         "sample_scope":"7 approximate city-centre points; NOT all 174 KMA regions or same grids",
         "models":{}}
    for name,model_id in MODELS.items():
        ok=[];errors=[]
        for city,(lat,lon) in CITIES.items():
            try:
                ok.append(fetch_one(model_id,city,lat,lon,now))
            except Exception as exc:
                # Do not print URL or response body. No private credentials in request.
                errors.append({"city":city,"error":type(exc).__name__})
        out["models"][name]={"model_id":model_id,"status":"verified" if ok else "unavailable",
                             "cities_succeeded":len(ok),"cities_requested":len(CITIES),
                             "records":ok,"errors":errors}
    return out

def status_md(result):
    lines=["# 최신 기상자료 제공사 연결·검증 상태","",
       "- 점검 시각(KST): **"+result["generated_at_kst"]+"**",
       "- 기상청 단기 API: **자동 연결** (성공·실제 수치는 [latest.json](latest.json)에서 확인)",
       "- 기상청 중기예보·특보: 이 GitHub 수집에는 미포함, ChatGPT 예약에서 최신 공식 본문 별도 확인",
       "- AccuWeather: **미연결** (별도 인증/이용허가 필요, 임의 수집 금지)",
       "- Weathernews: **미연결** (최신 한국 지역 수치 자동 조회 경로 미검증)",
       "- The Weather Channel: **미연결** (최신 한국 지역 수치 자동 조회 경로 미검증)",
       "",
       "## ECMWF 공개 예측모델 접속 확인",
       "",
       "| 모델 | 한국 도시 중심좌표 실제 조회 | 처리 |",
       "|---|---:|---|"]
    for model,v in result["models"].items():
        lines.append("| "+model+" | "+str(v["cities_succeeded"])+"/"+str(v["cities_requested"])+
            " | "+("실제 수치 확인" if v["status"]=="verified" else "실제 수치 확인 실패")+" |")
    lines += ["","**주의:** ECMWF 모델은 기상청 API와 다른 격자·해상도에서 계산한 *결정론적 예보*입니다. 두 모델의 수치가 일치해도 적중률이 검증된 것은 아니며, 모델 강수량을 확률로 바꿀 수 없습니다.",
       "이 상태표는 임의로 민간 예보사의 최신 데이터를 채워 넣지 않습니다. 모델별 검증 성공은 [ecmwf_regional.json](ecmwf_regional.json)을 함께 확인하세요.",
       ""]
    return "\n".join(lines)

def main():
    ROOT.joinpath("data").mkdir(exist_ok=True)
    result=collect(dt.datetime.now(KST))
    ROOT.joinpath("data/ecmwf_regional.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    ROOT.joinpath("data/source_status.md").write_text(status_md(result),encoding="utf-8")
    print("ECMWF verified models:",",".join(k for k,v in result["models"].items() if v["status"]=="verified") or "none")

if __name__=="__main__":
    main()
