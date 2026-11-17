#!/usr/bin/env python3
"""Optional independent ECMWF IFS / AIFS checks via Open-Meteo.

Uses one model reference point per KMA city-level group, never an official
KMA district-grid forecast. Failure must not block official KMA PDF updates.
"""
import concurrent.futures
import datetime as dt
import json
import socket
import ssl
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from zoneinfo import ZoneInfo
from ecmwf_city_locations import build_city_locations

ROOT=Path(__file__).resolve().parents[1]
KST=ZoneInfo("Asia/Seoul")
URL="https://api.open-meteo.com/v1/forecast"
MODELS={"IFS":"ecmwf_ifs","AIFS Single":"ecmwf_aifs025_single"}

def request_json(params, max_attempts=3, timeout=18):
    """Retry timeouts and transient HTTP 429/5xx; never retry unsupported 400s."""
    req=urllib.request.Request(URL+"?"+urllib.parse.urlencode(params),
        headers={"User-Agent":"KMA-ChatGPT-Bridge/2.0","Accept":"application/json"})
    for attempt in range(max_attempts):
        try:
            with urllib.request.urlopen(req,timeout=timeout) as resp:
                return json.loads(resp.read(350000).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            retryable=exc.code in (408,425,429,500,502,503,504)
            if not retryable or attempt+1==max_attempts:
                raise
            retry_after=exc.headers.get("Retry-After","") if exc.headers else ""
            try:
                delay=min(8,max(1,int(retry_after)))
            except (ValueError,TypeError):
                delay=min(8,2**attempt)
        except (urllib.error.URLError, TimeoutError, ConnectionError, ssl.SSLError):
            if attempt+1==max_attempts:
                raise
            delay=min(8,2**attempt)
        time.sleep(delay)
    raise RuntimeError("UNREACHABLE_RETRY_LOOP")


def classify_error(exc):
    """Structured public diagnostics without URLs, tokens or response bodies."""
    if isinstance(exc,urllib.error.HTTPError):
        return {"error":"HTTP_"+str(exc.code),"reason":"API_HTTP_STATUS",
                "status_code":exc.code}
    reason=exc.reason if isinstance(exc,urllib.error.URLError) else exc
    if isinstance(reason,socket.gaierror):
        category="DNS_RESOLUTION"
    elif "handshake" in str(reason).lower() and "timed out" in str(reason).lower():
        category="TLS_HANDSHAKE_TIMEOUT"
    elif isinstance(reason,(TimeoutError,socket.timeout)) or "timed out" in str(reason).lower():
        category="NETWORK_TIMEOUT"
    elif isinstance(reason,ssl.SSLError):
        category="TLS_CONNECTION"
    elif isinstance(reason,ConnectionRefusedError):
        category="CONNECTION_REFUSED"
    elif isinstance(reason,ConnectionResetError):
        category="CONNECTION_RESET"
    else:
        category="NETWORK_OR_RESPONSE_ERROR"
    return {"error":type(exc).__name__,"reason":category,
            "reason_type":type(reason).__name__,
            "errno":getattr(reason,"errno",None)}


def decode_model_location(payload,city,latitude,longitude,now):
    """Validate actual hourly values separately for each city in a batched response."""
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

def fetch_group(model_id,candidates,now):
    """Fetch up to 10 coordinate pairs with one API request.
    
    Open-Meteo returns an ordered JSON list for multiple locations; never
    attribute a location's values to another when result counts differ.
    """
    query={"latitude":",".join(str(coords[0]) for _,coords in candidates),
           "longitude":",".join(str(coords[1]) for _,coords in candidates),
           "hourly":"precipitation,snowfall","models":model_id,
           "timezone":"Asia/Seoul","forecast_hours":168}
    try:
        payload=request_json(query,max_attempts=2,timeout=12)
    except urllib.error.HTTPError as exc:
        if exc.code!=400:
            raise
        query["hourly"]="precipitation"
        payload=request_json(query,max_attempts=2,timeout=12)
    if len(candidates)==1 and isinstance(payload,dict):
        payload=[payload]
    if not isinstance(payload,list) or len(payload)!=len(candidates):
        raise ValueError("BATCH_RESPONSE_COUNT_MISMATCH")
    succeeded=[]
    errors=[]
    for (city,(lat,lon)),data in zip(candidates,payload):
        try:
            succeeded.append(decode_model_location(data,city,lat,lon,now))
        except Exception as exc:
            errors.append({"city":city,**classify_error(exc)})
    return succeeded,errors

def fetch_one(model,city,latitude,longitude,now):
    query={"latitude":latitude,"longitude":longitude,"hourly":"precipitation,snowfall",
           "models":model,"timezone":"Asia/Seoul","forecast_hours":168}
    try:
        payload=request_json(query)
    except urllib.error.HTTPError as exc:
        if exc.code!=400:
            raise
        # AIFS products may not expose snowfall; retry precipitation only.
        query["hourly"]="precipitation"
        payload=request_json(query)
    return decode_model_location(payload,city,latitude,longitude,now)

BATCH_SIZE=10
MAX_COLLECTION_SECONDS=165
MAX_MODEL_SECONDS=78

def collect(now,cities=None):
    """Try every city in small multi-location batches, keeping partial successes.

    A per-run wall-clock budget prevents intermittent Open-Meteo TLS stalls
    from delaying the official KMA PDF past its reminder window.
    """
    cities=build_city_locations() if cities is None else cities
    if not cities:
        raise ValueError("NO_ECMWF_CITY_POINTS")
    started=time.monotonic()
    out={"generated_at_kst":now.isoformat(),
         "source":"Open-Meteo ECMWF IFS/AIFS open data",
         "sample_scope":f"{len(cities)} KMA city groups represented by approximate inverse-DFS grid points; not official city centres, not the 174 district grids",
         "location_method":"mean of DISTINCT KMA 5km gu/grid representative cell centres within each city label",
         "cities_targeted":len(cities),"models":{}}
    all_candidates=sorted(cities.items())
    for name,model_id in MODELS.items():
        model_started=time.monotonic()
        good=[]
        errors=[]
        attempted=0
        batches_attempted=0
        for i in range(0,len(all_candidates),BATCH_SIZE):
            chunk=all_candidates[i:i+BATCH_SIZE]
            if (time.monotonic()-started>MAX_COLLECTION_SECONDS or
                time.monotonic()-model_started>MAX_MODEL_SECONDS):
                errors.extend({"city":city,"error":"NOT_REQUESTED",
                               "reason":"GLOBAL_TIME_BUDGET_EXCEEDED"}
                              for city,_ in chunk)
                continue
            attempted+=len(chunk)
            batches_attempted+=1
            try:
                successes,failures=fetch_group(model_id,chunk,now)
                good.extend(successes)
                errors.extend(failures)
                # If a batch response omitted/broke a subset, retry only
                # those cities individually, within the time budget.
                for failed in failures:
                    if time.monotonic()-started>MAX_COLLECTION_SECONDS:
                        break
                    if failed["reason"]=="API_HTTP_STATUS":
                        continue
                    city=failed["city"]
                    try:
                        lat,lon=cities[city]
                        replacement=fetch_one(model_id,city,lat,lon,now)
                        good.append(replacement)
                        errors=[e for e in errors if e is not failed]
                    except Exception:
                        pass
            except Exception as exc:
                diagnostic=classify_error(exc)
                errors.extend({"city":city,**diagnostic} for city,_ in chunk)
                # A malformed batch must not stop any other city groups.
                # Do not multiply a TLS outage into ten single-city timeouts.
                if isinstance(exc,(ValueError,TypeError)) and len(chunk)>1:
                    for city,(lat,lon) in chunk:
                        if (time.monotonic()-started>MAX_COLLECTION_SECONDS or
                            time.monotonic()-model_started>MAX_MODEL_SECONDS):
                            break
                        try:
                            good.append(fetch_one(model_id,city,lat,lon,now))
                            errors=[e for e in errors if e["city"]!=city]
                        except Exception:
                            pass
        # Confirm each city appears once even after retried rows.
        unique={item["city"]:item for item in good}
        good=[unique[k] for k in sorted(unique)]
        issues={}
        for err in errors:
            issues[err["reason"]]=issues.get(err["reason"],0)+1
        out["models"][name]={"model_id":model_id,
            "status":"verified" if good else "unavailable",
            "cities_succeeded":len(good),"cities_requested":len(cities),
            "cities_attempted":attempted,
            "batches_attempted":batches_attempted,
            "records":good,"errors":errors}
        print("ECMWF",name,"success",len(good),"/",len(cities),
              "attempted",attempted,"batches",batches_attempted,
              "diagnostics",issues,flush=True)
    return out


def status_md(result):
    lines=["# 최신 기상자료 제공사 연결·검증 상태","",
       "- 점검 시각(KST): **"+result["generated_at_kst"]+"**",
       "- 기상청 단기 API: **자동 연결** (성공·실제 수치는 [latest.json](latest.json)에서 확인)",
       "- 기상청 중기예보·특보: 이 GitHub 수집에는 미포함; 공식 중기 수치가 없으면 임의의 시별 정량값을 생성하지 않음",
       ("- ECMWF: 최근 성공자료를 재사용했습니다 (신규 조회 실패, 이전 성공 수집시각을 반드시 확인)" if result.get("fallback_recent_success") else "- ECMWF: 이번 실행의 실제 신규 수집 결과"),
       "",
       "## ECMWF 공개 예측모델 — 최신 한국 도시 수치 확인에 성공한 모델만 표시",
       "",
       "| 모델 | 한국 시 단위 근사 대표좌표 실제 조회 | 처리 |",
       "|---|---:|---|"]
    verified=0
    for model,v in result["models"].items():
        if v["status"] not in ("verified","verified_cached_recent") or v["cities_succeeded"]<=0:continue
        verified+=1
        lines.append("| "+model+" | "+str(v["cities_succeeded"])+"/"+str(v["cities_requested"])+
            " | 실제 한국 수치 확인 |")
    if not verified:
        lines.append("| 확인된 독립 모델 없음 | — | 최신 수치 미확보 · 비교 생략 |")
    # Record why a model failed, without printing sensitive URLs or response bodies.
    for model,info in result["models"].items():
        failed=[err for err in info.get("errors",[])
                if err.get("error")!="NOT_REQUESTED"]
        if failed:
            causes={}
            for item in failed:
                reason=item.get("reason","UNKNOWN_ERROR")
                causes[reason]=causes.get(reason,0)+1
            summary=", ".join(k+" "+str(v)+"건" for k,v in sorted(causes.items()))
            lines.append("- "+model+" 실패 진단: 요청 "+
                str(info.get("cities_attempted",len(failed)))+
                "개 도시, 원인 "+summary+
                (" (첫 요청 연결 불가로 남은 도시 생략)" if any(
                    e.get("error")=="NOT_REQUESTED" for e in info.get("errors",[])) else ""))
    lines += ["","**좌표 주의:** 시 명칭별 참고 지점은 기상청 격자 대표점의 역변환·평균으로 만든 근사 위치이며 행정구역 전체 평균이나 공식 시 중심점이 아닙니다.", "**주의:** ECMWF 모델은 기상청 API와 다른 격자·해상도에서 계산한 *결정론적 예보*입니다. 두 모델의 수치가 일치해도 적중률이 검증된 것은 아니며, 모델 강수량을 확률로 바꿀 수 없습니다.",
       "현재 자동 조회에 연결하지 않은 예보 제공사는 표와 알림에서 제외합니다. 모델별 검증 성공은 [ecmwf_regional.json](ecmwf_regional.json)을 함께 확인하세요.",
       ""]
    return "\n".join(lines)

def main():
    ROOT.joinpath("data").mkdir(exist_ok=True)
    now=dt.datetime.now(KST)
    result=collect(now)
    last_good=ROOT/"data/ecmwf_last_verified.json"
    verified=any(v.get("status")=="verified" and v.get("cities_succeeded",0)>0
                 for v in result.get("models",{}).values())
    if verified:
        last_good.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    elif last_good.is_file():
        # Never invent forecast values: reuse only actual successful past API data,
        # with its original collection timestamp, for a STRICT maximum of 3 hours.
        try:
            old=json.loads(last_good.read_text(encoding="utf-8"))
            age=(now-dt.datetime.fromisoformat(old["generated_at_kst"])).total_seconds()
            if 0<=age<=3*3600 and any(
                v.get("status")=="verified" and v.get("cities_succeeded",0)>0
                for v in old.get("models",{}).values()):
                old["fallback_recent_success"]=True
                old["last_attempt_at_kst"]=now.isoformat()
                old["failed_latest_attempt"]=True
                for v in old["models"].values():
                    if v.get("status")=="verified":
                        v["status"]="verified_cached_recent"
                result=old
        except (ValueError,KeyError,TypeError,OSError):
            pass
    ROOT.joinpath("data/ecmwf_regional.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    ROOT.joinpath("data/source_status.md").write_text(status_md(result),encoding="utf-8")
    print("ECMWF verified models:",",".join(k for k,v in result["models"].items() if v["status"] in ("verified","verified_cached_recent")) or "none",
          "cached_recent=",bool(result.get("fallback_recent_success")))

if __name__=="__main__":
    main()
