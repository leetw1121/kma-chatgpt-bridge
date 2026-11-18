#!/usr/bin/env python3
"""Optional independent ECMWF IFS / AIFS checks via Open-Meteo.

Uses one model reference point per KMA city-level group, never an official
KMA district-grid forecast. Failure must not block official KMA PDF updates.
"""
import concurrent.futures
import csv
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
from ecmwf_city_locations import build_city_locations, build_district_locations
from open_meteo_transport import request_with_verified_ip

ROOT=Path(__file__).resolve().parents[1]
KST=ZoneInfo("Asia/Seoul")
URL="https://api.open-meteo.com/v1/forecast"
MODELS={"IFS":"ecmwf_ifs","AIFS Single":"ecmwf_aifs025_single"}

def request_json(params, max_attempts=2, timeout=6):
    """Retry timeouts and transient HTTP 429/5xx; never retry unsupported 400s."""
    url=URL+"?"+urllib.parse.urlencode(params)
    req=urllib.request.Request(url,
        headers={"User-Agent":"KMA-ChatGPT-Bridge/2.1","Accept":"application/json"})
    for attempt in range(max_attempts):
        try:
            with urllib.request.urlopen(req,timeout=timeout) as resp:
                return json.loads(resp.read(350000).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # HTTP 429 means the provider has explicitly throttled this
            # client. Never hammer it with an immediate retry or DNS bypass.
            retryable=exc.code in (408,425,500,502,503,504)
            if not retryable or attempt+1==max_attempts:
                raise
            retry_after=exc.headers.get("Retry-After","") if exc.headers else ""
            try:
                delay=min(8,max(1,int(retry_after)))
            except (ValueError,TypeError):
                delay=min(8,2**attempt)
        except (urllib.error.URLError, TimeoutError, ConnectionError, ssl.SSLError) as primary_error:
            # An Open-Meteo edge may hang at TCP/TLS while another DNS A
            # record is healthy. Keep the original HTTPS hostname / SNI and
            # verify the certificate against the normal CA store.
            try:
                answer=request_with_verified_ip(url,timeout=3,max_ips=3)
                print("ECMWF verified-TLS alternate edge recovered",flush=True)
                return answer
            except (OSError,ValueError,UnicodeError):
                # Alternate DNS can also fail; no fake successful payload.
                if attempt+1==max_attempts:
                    raise primary_error
                delay=min(2,2**attempt)
        time.sleep(delay)
    raise RuntimeError("UNREACHABLE_RETRY_LOOP")


def classify_error(exc):
    """Structured public diagnostics without URLs, tokens or response bodies."""
    if isinstance(exc,urllib.error.HTTPError):
        return {"error":"HTTP_"+str(exc.code),
                "reason":"API_RATE_LIMITED" if exc.code==429 else "API_HTTP_STATUS",
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
MAX_COLLECTION_SECONDS=240
MAX_MODEL_SECONDS=78
# Gu queries are supplementary; do not spend the city's baseline budget on them.
MAX_DISTRICT_SECONDS=35
MAX_NETWORK_FAILURE_BATCHES=2
_NETWORK_FAILURES={"TLS_HANDSHAKE_TIMEOUT","NETWORK_TIMEOUT","DNS_RESOLUTION",
                   "TLS_CONNECTION","CONNECTION_REFUSED","CONNECTION_RESET",
                   "NETWORK_OR_RESPONSE_ERROR"}

def priority_from_kma(now,path=ROOT/"data/precipitation_only.csv"):
    """KMA forecast-positive labels guide *coverage*, never ECMWF values.

    Current-day first, then future days, preserving the KMA snowfall-first
    order. Stale or malformed CSV simply disables this optional prioritizer.
    """
    try:
        ordered=[]
        seen=set()
        with Path(path).open(encoding="utf-8-sig",newline="") as stream:
            reader=csv.DictReader(stream)
            if not {"date","region"}.issubset(reader.fieldnames or []):
                return []
            for row in reader:
                date=dt.date.fromisoformat(row["date"])
                if not now.date()<=date<now.date()+dt.timedelta(days=8):
                    continue
                label=row["region"]
                if label and label not in seen:
                    ordered.append(label)
                    seen.add(label)
        return ordered
    except (OSError,ValueError,KeyError,TypeError):
        return []


def collect(now,cities=None,districts=None,priorities=None):
    """Independent real-model sampling, KMA-wet-first and 429-safe.

    No missing model value is filled from KMA. When supplied, KMA priority
    labels are queried across BOTH models and BOTH geographic scopes before
    lower-priority areas. All 167+109 points remain targets. A provider 429
    stops ALL further requests in that invocation, rather than multiplying
    daily quota failures; future runs can complete the omitted points.
    """
    default_cities=cities is None
    cities=build_city_locations() if default_cities else cities
    districts=build_district_locations() if default_cities and districts is None else (districts or {})
    if not cities:
        raise ValueError("NO_ECMWF_CITY_POINTS")
    if priorities is None:
        priorities=priority_from_kma(now) if default_cities else []
    prioritised=list(dict.fromkeys(priorities))
    scopes={"models":dict(cities),"district_models":dict(districts)}
    ordered={key:sorted(items.items()) for key,items in scopes.items()}
    selected={}
    remaining={}
    for scope,points in ordered.items():
        lookup=dict(points)
        selected[scope]=[(label,lookup[label]) for label in prioritised if label in lookup]
        selected_names={label for label,_ in selected[scope]}
        remaining[scope]=[(label,coords) for label,coords in points if label not in selected_names]

    def batches(points):
        return [points[i:i+BATCH_SIZE] for i in range(0,len(points),BATCH_SIZE)]

    schedule=[]
    priority_batches={scope:batches(points) for scope,points in selected.items()}
    for i in range(max((len(x) for x in priority_batches.values()),default=0)):
        for name in MODELS:
            for scope in ("models","district_models"):
                if i<len(priority_batches[scope]):
                    schedule.append((name,scope,priority_batches[scope][i]))
    for name in MODELS:
        for scope in ("models","district_models"):
            schedule.extend((name,scope,chunk) for chunk in batches(remaining[scope]))

    started=time.monotonic()
    out={
        "generated_at_kst":now.isoformat(),
        "source":"Open-Meteo ECMWF IFS/AIFS open data",
        "sample_scope":f"{len(cities)} city/county groups plus {len(districts)} independent gu labels; approximate inverse-DFS grid points, not administrative-area averages",
        "location_method":"mean of DISTINCT KMA 5km gu/grid representative cell centres within each city label",
        "district_location_method":"one inverse KMA official 5km representative grid per gu, not an area average",
        "cities_targeted":len(cities),"districts_targeted":len(districts),
        "kma_wet_priority_city_count":len(selected["models"]),
        "kma_wet_priority_gu_count":len(selected["district_models"]),
        "rate_limited":False,"models":{},"district_models":{},
    }
    state={}
    for name in MODELS:
        for scope in scopes:
            state[name,scope]={"records":{},"errors":{},"attempted":set(),
                               "batches":0,"network_errors":0,"circuit":False,
                               "started":None}
    rate_limited=False
    retry_after=None

    for name,scope,chunk in schedule:
        st=state[name,scope]
        if st["started"] is None:
            st["started"]=time.monotonic()
        if rate_limited:
            reason="RATE_LIMITED_COOLDOWN"
        elif st["circuit"]:
            reason="NETWORK_CIRCUIT_OPEN"
        elif time.monotonic()-started>=MAX_COLLECTION_SECONDS:
            reason="GLOBAL_TIME_BUDGET_EXCEEDED"
        elif (time.monotonic()-st["started"]>=
              (MAX_MODEL_SECONDS if scope=="models" else MAX_DISTRICT_SECONDS)):
            reason=("MODEL_TIME_BUDGET_EXCEEDED" if scope=="models" else
                    "DISTRICT_TIME_BUDGET_EXCEEDED")
        else:
            reason=None
        if reason is not None:
            for label,_ in chunk:
                st["errors"].setdefault(label,{"city":label,"error":"NOT_REQUESTED",
                                                "reason":reason})
            continue
        st["batches"]+=1
        st["attempted"].update(label for label,_ in chunk)
        try:
            successes,failures=fetch_group(MODELS[name],chunk,now)
            for item in successes:
                st["records"][item["city"]]=item
                st["errors"].pop(item["city"],None)
            for error in failures:
                st["errors"][error["city"]]=error
            if successes:
                st["network_errors"]=0
            elif failures and all(err.get("reason") in _NETWORK_FAILURES
                                  for err in failures):
                st["network_errors"]+=1
            else:
                st["network_errors"]=0
            if any(err.get("reason")=="API_RATE_LIMITED" for err in failures):
                rate_limited=True
            # Retry a small number of malformed individual locations, but
            # never re-request a 429 or repeat an entire failed HTTP batch.
            for error in failures[:2]:
                if rate_limited or error.get("reason")=="API_HTTP_STATUS":
                    continue
                if (time.monotonic()-started>=MAX_COLLECTION_SECONDS or
                    time.monotonic()-st["started"]>=MAX_MODEL_SECONDS):
                    break
                label=error["city"]
                try:
                    lat,lon=scopes[scope][label]
                    item=fetch_one(MODELS[name],label,lat,lon,now)
                    st["records"][label]=item
                    st["errors"].pop(label,None)
                except Exception as exc:
                    diag=classify_error(exc)
                    st["errors"][label]={"city":label,**diag}
                    if diag["reason"]=="API_RATE_LIMITED":
                        rate_limited=True
                        break
        except Exception as exc:
            diag=classify_error(exc)
            for label,_ in chunk:
                st["errors"][label]={"city":label,**diag}
            if diag["reason"]=="API_RATE_LIMITED":
                rate_limited=True
                try:
                    raw=exc.headers.get("Retry-After") if exc.headers else None
                    if raw is not None:retry_after=min(3600,max(0,int(raw)))
                except (TypeError,ValueError):
                    retry_after=None
            elif diag["reason"] in _NETWORK_FAILURES:
                st["network_errors"]+=1
            else:
                st["network_errors"]=0
            if isinstance(exc,(ValueError,TypeError)) and len(chunk)>1:
                for label,(lat,lon) in chunk[:2]:
                    if time.monotonic()-started>=MAX_COLLECTION_SECONDS:
                        break
                    try:
                        item=fetch_one(MODELS[name],label,lat,lon,now)
                        st["records"][label]=item
                        st["errors"].pop(label,None)
                    except Exception as sub_exc:
                        diag2=classify_error(sub_exc)
                        st["errors"][label]={"city":label,**diag2}
                        if diag2["reason"]=="API_RATE_LIMITED":
                            rate_limited=True
                            break
        if st["network_errors"]>=MAX_NETWORK_FAILURE_BATCHES and not st["records"]:
            st["circuit"]=True

    for name,model_id in MODELS.items():
        for scope,points in scopes.items():
            st=state[name,scope]
            records=[st["records"][label] for label in sorted(st["records"])]
            errors=[st["errors"][label] for label in sorted(st["errors"])]
            out[scope][name]={
                "model_id":model_id,
                "status":"verified" if records else "unavailable",
                "cities_requested":len(points),
                "cities_attempted":len(st["attempted"]),
                "cities_succeeded":len(records),
                "batches_attempted":st["batches"],
                "circuit_open":st["circuit"],
                "records":records,"errors":errors}
            print("ECMWF",scope,name,"success",len(records),"/",len(points),
                  "attempted",len(st["attempted"]),"batches",st["batches"],
                  "429",rate_limited,flush=True)
    out["rate_limited"]=rate_limited
    if retry_after is not None:
        out["retry_after_seconds"]=retry_after
    if not rate_limited:
        recover_missing_after_service_restored(out,now,ordered["models"],
                                               ordered["district_models"],started)
    return out

MAX_RECOVERY_SECONDS=55

def recover_missing_after_service_restored(out,now,cities,districts,started):
    """Second chance AFTER both models: a later successful batch can prove
    that an earlier TLS outage was transient. Protect the 240-second overall
    ECMWF budget; never retry already successful coordinates.
    """
    # Do not spend more requests during a genuinely persistent total outage.
    if not any(v.get("cities_succeeded",0)>0
               for scope in ("models","district_models")
               for v in (out.get(scope) or {}).values()):
        return
    recovery_started=time.monotonic()
    for name,model_id in MODELS.items():
        for scope,points in (("models",cities),("district_models",districts)):
            existing=out[scope][name]
            known={row["city"]:row for row in existing.get("records",[])}
            missing=[(label,coords) for label,coords in points if label not in known]
            if not missing:
                continue
            errors={e["city"]:e for e in existing.get("errors",[])}
            attempted_labels=set(known)
            attempted_labels.update(e["city"] for e in errors.values()
                                    if e.get("error")!="NOT_REQUESTED")
            retries=0
            recovered=0
            network_failures=0
            for i in range(0,len(missing),BATCH_SIZE):
                if (time.monotonic()-started>=MAX_COLLECTION_SECONDS or
                    time.monotonic()-recovery_started>=MAX_RECOVERY_SECONDS):
                    break
                chunk=missing[i:i+BATCH_SIZE]
                retries+=1
                attempted_labels.update(label for label,_ in chunk)
                try:
                    successes,failures=fetch_group(model_id,chunk,now)
                    if successes:
                        network_failures=0
                    elif failures and all(e.get("reason") in _NETWORK_FAILURES
                                          for e in failures):
                        network_failures+=1
                    else:
                        network_failures=0
                    for label,_ in chunk:
                        errors.pop(label,None)
                    for item in successes:
                        known[item["city"]]=item
                        recovered+=1
                    for error in failures:
                        errors[error["city"]]=error
                except Exception as exc:
                    info=classify_error(exc)
                    network_failures=(network_failures+1
                        if info["reason"] in _NETWORK_FAILURES else 0)
                    for label,_ in chunk:
                        errors[label]={"city":label,**info}
                if network_failures>=MAX_NETWORK_FAILURE_BATCHES:
                    break
            existing["records"]=[known[k] for k in sorted(known)]
            existing["cities_succeeded"]=len(known)
            existing["status"]="verified" if known else "unavailable"
            existing["cities_attempted"]=len(attempted_labels)
            existing["batches_attempted"]+=retries
            existing["recovery_batches"]=retries
            existing["recovery_succeeded"]=recovered
            existing["errors"]=[errors[k] for k in sorted(errors)]
            if recovered:
                existing["circuit_open"]=False
            print("ECMWF second chance",name,scope,
                  "recovered",recovered,"total",len(known),"/",len(points),
                  "recovery_batches",retries,flush=True)


def status_md(result):
    lines=["# 최신 기상자료 제공사 연결·검증 상태","",
        "- 점검 시각(KST): **"+result["generated_at_kst"]+"**",
        "- 기상청 단기 API: **독립 수집** (최신 결과: [latest.json](latest.json))",
        "- 기상청 중기예보·특보: 이 수집에 미포함",
        "- ECMWF: 구와 시·군을 독립 조회하며 실제 성공자료만 표기합니다.",
        "",
        "| 범위 | 모델 | 성공/대상 | 실제 시도 | 성공 상태 |",
        "|---|---|---:|---:|---|"]
    for scope,source in (("시·군",result.get("models") or {}),
                         ("구",result.get("district_models") or {})):
        for name,item in source.items():
            count=item.get("cities_succeeded",0)
            requested=item.get("cities_requested",0)
            attempted=item.get("cities_attempted",0)
            status=item.get("status","unavailable")
            valid=status in ("verified","verified_cached_recent") and count>0
            label=("최근 성공자료 재사용" if status=="verified_cached_recent"
                   else "신규 수집 성공" if valid else "수치 미확보")
            lines.append(f"| {scope} | {name} | {count}/{requested} | "
                         f"{attempted}/{requested} | {label} |")
            counts={}
            for error in item.get("errors") or []:
                reason=error.get("reason","UNKNOWN")
                counts[reason]=counts.get(reason,0)+1
            if counts:
                line=", ".join(f"{k} {v}건" for k,v in sorted(counts.items()))
                lines.append(f"- {scope} {name} 미수집 원인: {line}")
            if item.get("recovery_succeeded",0)>0:
                lines.append(f"- {scope} {name}: 다른 지역의 연결 회복 후 "
                             f"{item['recovery_succeeded']}곳 재시도 복구 성공")
            if item.get("circuit_open"):
                lines.append(f"- {scope} {name}: 연속 네트워크 실패로 회로 차단 "
                             "(추가 지역 요청 생략, 기상청 PDF 계속 생성)")
    if result.get("fallback_recent_success"):
        lines += ["", "- 최신 ECMWF 수집이 모두 실패하여 최근 3시간 이내 확인된 성공본만 재사용했습니다.",
                  "- 최근 성공자료 수집: "+result["generated_at_kst"],
                  "- 최근 실패 시도: "+result.get("last_attempt_at_kst","미확인")]
    lines += ["",
      "**좌표 주의:** 시·군 참고 지점은 기상청 구 격자의 역변환·평균 좌표, 구 참고 지점은 해당 구 대표 격자의 역변환 좌표입니다. 실제 ECMWF 격자·행정구역 평균과 다릅니다.",
      "**모델 주의:** IFS와 AIFS의 수치를 확률로 바꾸거나 기상청 예보와 평균하지 않습니다. 조회 실패를 0mm로 취급하지 않습니다.",
      "실제 수집 데이터는 [ecmwf_regional.json](ecmwf_regional.json)에서 확인하세요.",""]
    return "\n".join(lines)

def has_verified_records(result):
    """One valid original-scope OR district model is enough to save a cache."""
    return any(item.get("status")=="verified" and
               item.get("cities_succeeded",0)>0 and item.get("records")
               for key in ("models","district_models")
               for item in (result.get(key) or {}).values())

def choose_recent_cache(now,result,last_good):
    """Use only matching 167+109 scopes and original timestamps, never old 167-only data."""
    if has_verified_records(result):
        last_good.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",
                             encoding="utf-8")
        return result
    if not last_good.is_file():
        return result
    try:
        old=json.loads(last_good.read_text(encoding="utf-8"))
        old_time=dt.datetime.fromisoformat(old["generated_at_kst"])
        if old_time.tzinfo is None or now.tzinfo is None:
            return result
        age=(now-old_time).total_seconds()
        if not 0<=age<=3*3600:
            return result
        if (old.get("cities_targeted")!=result["cities_targeted"] or
            old.get("districts_targeted")!=result["districts_targeted"] or
            not has_verified_records(old)):
            return result
        old["fallback_recent_success"]=True
        old["last_attempt_at_kst"]=now.isoformat()
        old["failed_latest_attempt"]=True
        # Expose failed *new* attempt for diagnostics; retain old results
        # and their original collection timestamp for report freshness checks.
        old["latest_attempt_failures"]={
            scope:{name:{"attempted":v.get("cities_attempted",0),
                         "error_counts":{
                             reason:sum(e.get("reason")==reason for e in v.get("errors",[]))
                             for reason in sorted({e.get("reason","UNKNOWN")
                                                   for e in v.get("errors",[])})}}
                   for name,v in (result.get(scope) or {}).items()}
            for scope in ("models","district_models")}
        for scope in ("models","district_models"):
            for v in old.get(scope,{}).values():
                if v.get("status")=="verified":
                    v["status"]="verified_cached_recent"
        return old
    except (ValueError,KeyError,TypeError,OSError):
        return result

def main():
    ROOT.joinpath("data").mkdir(exist_ok=True)
    now=dt.datetime.now(KST)
    result=collect(now)
    last_good=ROOT/"data/ecmwf_last_verified.json"
    result=choose_recent_cache(now,result,last_good)
    ROOT.joinpath("data/ecmwf_regional.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    ROOT.joinpath("data/source_status.md").write_text(status_md(result),encoding="utf-8")
    print("ECMWF verified models:",",".join(k for k,v in result["models"].items() if v["status"] in ("verified","verified_cached_recent")) or "none",
          "cached_recent=",bool(result.get("fallback_recent_success")))

if __name__=="__main__":
    main()
