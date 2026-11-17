#!/usr/bin/env python3
"""Non-secret GitHub runner network probe for weather API failures."""
import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HOSTS=["api.open-meteo.com","open-meteo.com","api.github.com"]

def check_dns(host):
    t=time.monotonic()
    try:
        addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
        versions=sorted({("IPv6" if v[0]==socket.AF_INET6 else "IPv4") for v in addresses})
        print(json.dumps({"phase":"dns","host":host,"result":"ok","ip_families":versions,
                          "count":len(addresses),"elapsed_ms":round((time.monotonic()-t)*1000)}),flush=True)
    except Exception as exc:
        print(json.dumps({"phase":"dns","host":host,"result":"failed",
                          "error_type":type(exc).__name__,"errno":getattr(exc,"errno",None),
                          "error_reason":str(exc)[:160]}),flush=True)

def request(label,url):
    t=time.monotonic()
    req=urllib.request.Request(url,headers={"User-Agent":"KMA-ECMWF-Network-Diagnostic/1.0",
                                           "Accept":"application/json"})
    try:
        with urllib.request.urlopen(req,timeout=10) as response:
            raw=response.read(500000)
            try:
                document=json.loads(raw)
                keys=sorted(document) if isinstance(document,dict) else []
                valid=("hourly" in document if isinstance(document,dict) else False)
            except (ValueError,UnicodeError):
                keys=[];valid=False
            outcome={"phase":"http","name":label,"result":"ok",
                     "status":response.status,"response_bytes":len(raw),
                     "json_keys":keys[:15],"hourly_present":valid}
    except urllib.error.HTTPError as exc:
        # Error body is not printed, to avoid accidentally logging user data.
        outcome={"phase":"http","name":label,"result":"http_error","status":exc.code}
    except urllib.error.URLError as exc:
        reason=exc.reason
        outcome={"phase":"http","name":label,"result":"url_error",
                 "reason_type":type(reason).__name__,
                 "reason_errno":getattr(reason,"errno",None),
                 "reason_message":str(reason)[:180]}
    except Exception as exc:
        outcome={"phase":"http","name":label,"result":"other_error",
                 "reason_type":type(exc).__name__,"reason_message":str(exc)[:180]}
    outcome["elapsed_ms"]=round((time.monotonic()-t)*1000)
    print(json.dumps(outcome,ensure_ascii=False),flush=True)

if __name__=="__main__":
    print(json.dumps({"phase":"start","at":datetime.now(timezone.utc).isoformat(),
                      "python_ssl":ssl.OPENSSL_VERSION,
                      "proxy_env_names":sorted(k for k in os.environ if k.upper() in
                                      ("HTTP_PROXY","HTTPS_PROXY","NO_PROXY","ALL_PROXY"))}),flush=True)
    for host in HOSTS:check_dns(host)
    base="https://api.open-meteo.com/v1/forecast"
    params={"latitude":37.5665,"longitude":126.9780,"hourly":"precipitation",
            "timezone":"Asia/Seoul","forecast_hours":24}
    for label,model in [("generic_minimal",""),("ifs_minimal","ecmwf_ifs"),("aifs_minimal","ecmwf_aifs025_single")]:
        values=dict(params)
        if model:values["models"]=model
        request(label,base+"?"+urllib.parse.urlencode(values))
    request("control_github","https://api.github.com/zen")
