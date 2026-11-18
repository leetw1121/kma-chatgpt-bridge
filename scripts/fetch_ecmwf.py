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
            retryable=exc.code in (408,425,429,500,502,503,504)
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
MAX_COLLECTION_SECONDS=240
MAX_MODEL_SECONDS=78
# Gu queries are supplementary; do not spend the city's baseline budget on them.
MAX_DISTRICT_SECONDS=35
MAX_NETWORK_FAILURE_BATCHES=2
_NETWORK_FAILURES={"TLS_HANDSHAKE_TIMEOUT","NETWORK_TIMEOUT","DNS_RESOLUTION",
                   "TLS_CONNECTION","CONNECTION_REFUSED","CONNECTION_RESET",
                   "NETWORK_OR_RESPONSE_ERROR"}

def collect(now,cities=None,districts=None):
    """Try every city in small multi-location batches, keeping partial successes.

    A per-run wall-clock budget prevents intermittent Open-Meteo TLS stalls
    from delaying the official KMA PDF past its reminder window.
    """
    default_cities=cities is None
    cities=build_city_locations() if default_cities else cities
    districts=build_district_locations() if default_cities and districts is None else (districts or {})
    if not cities:
        raise ValueError("NO_ECMWF_CITY_POINTS")
    started=time.monotonic()
    out={"generated_at_kst":now.isoformat(),
         "source":"Open-Meteo ECMWF IFS/AIFS open data",
         "sample_scope":f"{len(cities)} city/county groups plus {len(districts)} independent gu labels; approximate inverse-DFS grid points, not administrative-area averages",
         "location_method":"mean of DISTINCT KMA 5km gu/grid representative cell centres within each city label",
         "district_location_method":"one inverse KMA official 5km representative grid per gu, not an area average",
         "cities_targeted":len(cities),"districts_targeted":len(districts),
         "models":{},"district_models":{}}
    all_candidates=sorted(cities.items())
    district_candidates=sorted(districts.items())
    for name,model_id in MODELS.items():
        model_started=time.monotonic()
        good=[]
        errors=[]
        attempted=0
        batches_attempted=0
        failed_network_batches=0
        circuit_open=False
        for i in range(0,len(all_candidates),BATCH_SIZE):
            chunk=all_candidates[i:i+BATCH_SIZE]
            if circuit_open:
                errors.extend({"city":city,"error":"NOT_REQUESTED",
                               "reason":"NETWORK_CIRCUIT_OPEN"} for city,_ in chunk)
                continue
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
                if successes:
                    failed_network_batches=0
                elif failures and all(e.get("reason") in _NETWORK_FAILURES for e in failures):
                    failed_network_batches+=1
                else:
                    failed_network_batches=0
                # If a batch response omitted/broke a subset, retry only
                # those cities individually, within the time budget.
                for failed in failures[:2]:
                    if (time.monotonic()-started>MAX_COLLECTION_SECONDS or
                        time.monotonic()-model_started>MAX_MODEL_SECONDS):
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
                if diagnostic["reason"] in _NETWORK_FAILURES:
                    failed_network_batches+=1
                else:
                    failed_network_batches=0
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
            if failed_network_batches>=MAX_NETWORK_FAILURE_BATCHES and not good:
                circuit_open=True
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
            "batches_attempted":batches_attempted,"circuit_open":circuit_open,
            "records":good,"errors":errors}
        print("ECMWF",name,"success",len(good),"/",len(cities),
              "attempted",attempted,"batches",batches_attempted,
              "diagnostics",issues,flush=True)
        # Supplement existing 167 city/county locations with gu only AFTER
        # completing that model's baseline requests. A bad district batch
        # cannot remove working city/county forecasts or block the KMA PDF.
        district_started=time.monotonic()
        district_good=[]
        district_errors=[]
        district_attempted=0
        district_batches=0
        district_network_failures=0
        district_circuit=False
        for j in range(0,len(district_candidates),BATCH_SIZE):
            chunk=district_candidates[j:j+BATCH_SIZE]
            if district_circuit:
                district_errors.extend({"city":label,"error":"NOT_REQUESTED",
                                        "reason":"NETWORK_CIRCUIT_OPEN"}
                                       for label,_ in chunk)
                continue
            if (time.monotonic()-started>MAX_COLLECTION_SECONDS or
                time.monotonic()-district_started>MAX_DISTRICT_SECONDS):
                district_errors.extend({"city":label,"error":"NOT_REQUESTED",
                                        "reason":"DISTRICT_TIME_BUDGET_EXCEEDED"}
                                       for label,_ in chunk)
                continue
            district_attempted+=len(chunk)
            district_batches+=1
            try:
                successes,failures=fetch_group(model_id,chunk,now)
                district_good.extend(successes)
                district_errors.extend(failures)
                if successes:
                    district_network_failures=0
                elif failures and all(x.get("reason") in _NETWORK_FAILURES for x in failures):
                    district_network_failures+=1
                else:
                    district_network_failures=0
            except Exception as exc:
                detail=classify_error(exc)
                district_errors.extend({"city":label,**detail} for label,_ in chunk)
                if detail["reason"] in _NETWORK_FAILURES:
                    district_network_failures+=1
                else:
                    district_network_failures=0
            if (district_network_failures >=
                (1 if circuit_open else MAX_NETWORK_FAILURE_BATCHES) and
                not district_good):
                district_circuit=True
        # The legacy 'models' payload and 167-target counts stay unchanged.
        distinct={row["city"]:row for row in district_good}
        out["district_models"][name]={
            "model_id":model_id,"status":"verified" if distinct else "unavailable",
            "cities_requested":len(districts),"cities_attempted":district_attempted,
            "cities_succeeded":len(distinct),"batches_attempted":district_batches,
            "circuit_open":district_circuit,"records":[distinct[k] for k in sorted(distinct)],
            "errors":district_errors}
        print("ECMWF gu",name,"success",len(distinct),"/",len(districts),
              "attempted",district_attempted,"batches",district_batches,flush=True)
    return out


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
