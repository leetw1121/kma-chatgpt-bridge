#!/usr/bin/env python3
"""Verified-TLS fallback for Open-Meteo when the default DNS edge stalls.

Never change the URL hostname or disable certificate checks. If a DNS
resolver sends GitHub runners to an unhealthy edge, query independent
DNS-over-HTTPS resolvers and connect to another *resolved address* while
keeping SNI and the TLS certificate hostname at api.open-meteo.com.

Network failure, invalid certificate, or DNS failure remains an explicit
failure; no synthetic forecast values are returned.
"""
import http.client
import ipaddress
import json
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache

API_HOST="api.open-meteo.com"
_DOH=(
    "https://cloudflare-dns.com/dns-query?name=api.open-meteo.com&type=A",
    "https://dns.google/resolve?name=api.open-meteo.com&type=A",
)
_MAX_PAYLOAD=350000

@lru_cache(maxsize=1)
def verified_dns_addresses():
    """Return globally routable A records provided by public DoH over TLS."""
    addresses=[]
    for url in _DOH:
        req=urllib.request.Request(url,headers={
            "Accept":"application/dns-json","User-Agent":"KMA-ChatGPT-Bridge/2.1"})
        try:
            with urllib.request.urlopen(req,timeout=3) as response:
                if response.status!=200:
                    continue
                answer=json.loads(response.read(16000).decode("utf-8"))
            if answer.get("Status")!=0:
                continue
            for row in answer.get("Answer",[]):
                if row.get("type")!=1 or not isinstance(row.get("data"),str):
                    continue
                addr=ipaddress.ip_address(row["data"])
                if addr.version==4 and addr.is_global:
                    key=str(addr)
                    if key not in addresses:addresses.append(key)
        except (OSError,ValueError,TypeError,KeyError,UnicodeError):
            continue
    # Favor a different edge from the system's DNS result, if possible.
    try:
        local={a[4][0] for a in socket.getaddrinfo(API_HOST,443,type=socket.SOCK_STREAM)}
    except OSError:
        local=set()
    return tuple(sorted(addresses,key=lambda ip:(ip in local,addresses.index(ip))))

def request_with_verified_ip(url,timeout=4,max_ips=3):
    """Fetch Open-Meteo HTTPS JSON via a different resolved IP without weakening TLS.

    Only the fixed official hostname is allowed. SNI, Host, CA verification,
    and hostname verification use that name, never the direct IP address.
    """
    parsed=urllib.parse.urlsplit(url)
    if (parsed.scheme!="https" or parsed.hostname!=API_HOST or
        parsed.port not in (None,443) or not parsed.path.startswith("/v1/")):
        raise ValueError("INVALID_OPEN_METEO_URL")
    addresses=verified_dns_addresses()[:max_ips]
    if not addresses:
        raise OSError("NO_ALTERNATE_DNS_ADDRESSES")
    last=None
    for ip in addresses:
        context=ssl.create_default_context()
        conn=http.client.HTTPSConnection(API_HOST,timeout=timeout,context=context)
        try:
            raw=socket.create_connection((ip,443),timeout=timeout)
            try:
                conn.sock=context.wrap_socket(raw,server_hostname=API_HOST)
            except BaseException:
                raw.close()
                raise
            target=parsed.path+("?" + parsed.query if parsed.query else "")
            conn.request("GET",target,headers={
                "Host":API_HOST,"Accept":"application/json",
                "User-Agent":"KMA-ChatGPT-Bridge/2.1","Connection":"close"})
            response=conn.getresponse()
            if response.status>=400:
                raise urllib.error.HTTPError(url,response.status,response.reason,response.headers,None)
            if response.status!=200:
                raise ValueError("UNEXPECTED_OPEN_METEO_STATUS")
            payload=response.read(_MAX_PAYLOAD+1)
            if len(payload)>_MAX_PAYLOAD:
                raise ValueError("OPEN_METEO_RESPONSE_TOO_LARGE")
            return json.loads(payload.decode("utf-8"))
        except urllib.error.HTTPError:
            # A real HTTP 429/4xx is a provider decision, not a TLS edge
            # outage. Do not route around quotas or unsupported parameters.
            raise
        except (OSError,ValueError,UnicodeError) as exc:
            last=exc
        finally:
            conn.close()
    raise last or OSError("VERIFIED_TLS_DNS_FALLBACK_FAILED")
