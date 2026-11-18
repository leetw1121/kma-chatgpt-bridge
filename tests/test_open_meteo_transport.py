import datetime as dt
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock,patch
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from open_meteo_transport import (API_HOST,verified_dns_addresses,
                                 request_with_verified_ip)
from fetch_ecmwf import (collect,request_json,choose_recent_cache,
                         has_verified_records,status_md)

KST=ZoneInfo("Asia/Seoul")

class MockResponse(io.BytesIO):
    status=200

class TransportTests(unittest.TestCase):
    def tearDown(self):
        verified_dns_addresses.cache_clear()

    def test_resolver_uses_only_public_ipv4_and_prefers_different_edge(self):
        first={"Status":0,"Answer":[
            {"type":1,"data":"51.81.167.44"},
            {"type":1,"data":"167.114.211.86"},
            {"type":1,"data":"127.0.0.1"},
            {"type":28,"data":"::1"}]}
        second={"Status":0,"Answer":[{"type":1,"data":"167.114.211.86"}]}
        with patch("open_meteo_transport.urllib.request.urlopen",
                   side_effect=[MockResponse(json.dumps(first).encode()),
                                MockResponse(json.dumps(second).encode())]),\
             patch("open_meteo_transport.socket.getaddrinfo",
                   return_value=[(2,1,6,"",("51.81.167.44",443))]):
            self.assertEqual(verified_dns_addresses(),
                             ("167.114.211.86","51.81.167.44"))

    def test_rejects_any_untrusted_domain_before_opening_socket(self):
        for url in ("https://evil.example/v1/forecast",
                    "http://api.open-meteo.com/v1/forecast",
                    "https://api.open-meteo.com:8443/v1/forecast",
                    "https://api.open-meteo.com/admin"):
            with self.assertRaises(ValueError):
                request_with_verified_ip(url)

    def test_tls_hostname_verification_is_preserved_on_resolved_address(self):
        fake_conn=MagicMock()
        fake_conn.getresponse.return_value.status=200
        fake_conn.getresponse.return_value.read.return_value=b'{"hourly":{}}'
        fake_context=MagicMock()
        raw=MagicMock()
        with patch("open_meteo_transport.verified_dns_addresses",
                   return_value=("167.114.211.86",)),\
             patch("open_meteo_transport.http.client.HTTPSConnection",
                   return_value=fake_conn),\
             patch("open_meteo_transport.socket.create_connection",
                   return_value=raw) as connect,\
             patch("open_meteo_transport.ssl.create_default_context",
                   return_value=fake_context):
            result=request_with_verified_ip(
                "https://api.open-meteo.com/v1/forecast?latitude=37",timeout=4)
        self.assertEqual(result,{"hourly":{}})
        connect.assert_called_once_with(("167.114.211.86",443),timeout=4)
        fake_context.wrap_socket.assert_called_once_with(
            raw,server_hostname=API_HOST)
        fake_conn.request.assert_called_once()
        self.assertEqual(fake_conn.request.call_args.kwargs["headers"]["Host"],
                         API_HOST)

    def test_fallback_after_tls_failure_recovers_a_real_response(self):
        with patch("fetch_ecmwf.urllib.request.urlopen",
                   side_effect=urllib.error.URLError(
                       TimeoutError("TLS handshake timed out"))),\
             patch("fetch_ecmwf.request_with_verified_ip",
                   return_value={"hourly":{"time":["test"]}}) as alternate:
            body=request_json({"latitude":37.5},max_attempts=2,timeout=2)
        self.assertIn("hourly",body)
        self.assertEqual(alternate.call_count,1)

    def test_non_retryable_http_400_never_uses_other_edges(self):
        error=urllib.error.HTTPError("https://api.open-meteo.com/v1/forecast",
                                   400,"invalid",None,None)
        with patch("fetch_ecmwf.urllib.request.urlopen",side_effect=error),\
             patch("fetch_ecmwf.request_with_verified_ip") as alternate:
            with self.assertRaises(urllib.error.HTTPError):
                request_json({"latitude":37.5},max_attempts=2)
        alternate.assert_not_called()

class RecoveryTests(unittest.TestCase):
    def test_network_circuit_stops_unnecessary_batches_but_attempts_both_models(self):
        now=dt.datetime(2026,11,18,23,0,tzinfo=KST)
        cities={f"도시{i:02d}":(36,128) for i in range(50)}
        districts={f"구{i:02d}":(37,127) for i in range(25)}
        outage=urllib.error.URLError(TimeoutError("TLS handshake timed out"))
        with patch("fetch_ecmwf.fetch_group",side_effect=outage) as fetch:
            result=collect(now,cities=cities,districts=districts)
        self.assertEqual(fetch.call_count,6)
        for label in ("IFS","AIFS Single"):
            city=result["models"][label]
            gu=result["district_models"][label]
            self.assertTrue(city["circuit_open"])
            self.assertTrue(gu["circuit_open"])
            self.assertEqual(city["cities_attempted"],20)
            self.assertEqual(gu["cities_attempted"],10)
            self.assertEqual(len(city["errors"]),50)
            self.assertEqual(len(gu["errors"]),25)
            self.assertIn("NETWORK_CIRCUIT_OPEN",status_md(result))

    def test_scope_mismatch_cannot_reuse_old_167_only_cache(self):
        now=dt.datetime(2026,11,18,23,0,tzinfo=KST)
        fresh={"generated_at_kst":now.isoformat(),"cities_targeted":167,
               "districts_targeted":109,"models":{},"district_models":{}}
        old={"generated_at_kst":now.isoformat(),"cities_targeted":167,
             "models":{"IFS":{"status":"verified","cities_succeeded":1,
                              "records":[{"city":"서울특별시"}]}}}
        with tempfile.TemporaryDirectory() as temp:
            cache=Path(temp)/"cached.json"
            cache.write_text(json.dumps(old),encoding="utf8")
            chosen=choose_recent_cache(now,fresh,cache)
            self.assertIs(chosen,fresh)

    def test_district_only_success_is_cached_then_reused_with_original_timestamp(self):
        now=dt.datetime(2026,11,18,23,0,tzinfo=KST)
        successful={"generated_at_kst":now.isoformat(),"cities_targeted":167,
                    "districts_targeted":109,"models":{},
                    "district_models":{"IFS":{"status":"verified",
                       "cities_succeeded":1,"cities_requested":109,
                       "records":[{"city":"서울특별시 강남구"}]}}}
        later=now+dt.timedelta(minutes=20)
        failing={"generated_at_kst":later.isoformat(),"cities_targeted":167,
                 "districts_targeted":109,"models":{},"district_models":{}}
        with tempfile.TemporaryDirectory() as temp:
            cache=Path(temp)/"cached.json"
            self.assertTrue(has_verified_records(successful))
            self.assertIs(choose_recent_cache(now,successful,cache),successful)
            self.assertTrue(cache.exists())
            cached=choose_recent_cache(later,failing,cache)
            self.assertTrue(cached["fallback_recent_success"])
            self.assertEqual(cached["generated_at_kst"],now.isoformat())
            self.assertEqual(cached["district_models"]["IFS"]["status"],
                             "verified_cached_recent")
            self.assertEqual(cached["last_attempt_at_kst"],later.isoformat())
            after_expiry=choose_recent_cache(
                now+dt.timedelta(hours=4),failing,cache)
            self.assertIs(after_expiry,failing)

if __name__=="__main__":
    unittest.main()
