import datetime as dt
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from fetch_ecmwf import fetch_one, status_md, request_json, classify_error, collect

class ECMWFModelTests(unittest.TestCase):
    def test_verified_model_values_are_read_not_invented(self):
        start=dt.datetime(2026,11,13,0,0,tzinfo=ZoneInfo("Asia/Seoul"))
        times=[(start+dt.timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(24)]
        response={"latitude":37.56,"longitude":126.97,"timezone":"Asia/Seoul",
                  "hourly":{"time":times,"precipitation":[1]*24,"snowfall":[0]*24}}
        with patch("fetch_ecmwf.urllib.request.urlopen",return_value=io.BytesIO(json.dumps(response).encode())):
            result=fetch_one("ecmwf_ifs","서울",37.5665,126.9780,start)
        self.assertEqual(result["hours_valid"],24)
        self.assertEqual(result["days"]["2026-11-13"]["precip_mm"],24)
        self.assertEqual(result["days"]["2026-11-13"]["snowfall_cm"],0)

    def test_model_status_never_claims_unconnected_provider_success(self):
        result={"generated_at_kst":"2026-11-13T00:00:00+09:00","models":{
             "IFS":{"cities_succeeded":0,"cities_requested":7,"status":"unavailable"},
             "AIFS Single":{"cities_succeeded":2,"cities_requested":7,"status":"verified"}}}
        text=status_md(result)
        self.assertNotIn("0/7",text)
        self.assertIn("2/7",text)
        self.assertNotIn("AccuWeather",text)
        self.assertNotIn("IFS | 0/7",text)
        self.assertIn("AIFS Single | 2/7",text)

    def test_tls_handshake_timeout_is_explicitly_classified(self):
        import urllib.error
        error=urllib.error.URLError(TimeoutError("_ssl.c:1001: The handshake operation timed out"))
        result=classify_error(error)
        self.assertEqual(result["reason"],"TLS_HANDSHAKE_TIMEOUT")
        self.assertEqual(result["reason_type"],"TimeoutError")

    def test_transient_tls_timeout_retries_then_recovers(self):
        import urllib.error
        good=io.BytesIO(b'{"hourly": {"time": []}}')
        with patch("fetch_ecmwf.urllib.request.urlopen",
                   side_effect=[urllib.error.URLError(TimeoutError("handshake timed out")), good]) as mock_url, \
             patch("fetch_ecmwf.time.sleep") as mock_sleep:
            payload=request_json({"latitude":37.5,"longitude":127.0},max_attempts=2)
        self.assertIn("hourly",payload)
        self.assertEqual(mock_url.call_count,2)
        self.assertEqual(mock_sleep.call_count,1)

    def test_preflight_failure_skips_other_six_cities_instead_of_14_timeouts(self):
        import urllib.error
        now=dt.datetime(2026,11,17,11,0,tzinfo=ZoneInfo("Asia/Seoul"))
        with patch("fetch_ecmwf.fetch_one",
                   side_effect=urllib.error.URLError(TimeoutError("TLS handshake timed out"))) as mocked:
            result=collect(now)
        self.assertEqual(mocked.call_count,2)
        for name in ("IFS","AIFS Single"):
            model=result["models"][name]
            self.assertEqual(model["cities_attempted"],1)
            self.assertEqual(model["cities_succeeded"],0)
            self.assertEqual(sum(x["reason"]=="PREFLIGHT_FAILED" for x in model["errors"]),6)
            self.assertEqual(model["errors"][0]["reason"],"TLS_HANDSHAKE_TIMEOUT")

    def test_preflight_success_keeps_seven_cities(self):
        now=dt.datetime(2026,11,17,11,0,tzinfo=ZoneInfo("Asia/Seoul"))
        def dummy(model,city,latitude,longitude,now):
            return {"city":city,"days":{},"hours_valid":24}
        with patch("fetch_ecmwf.fetch_one",side_effect=dummy) as mocked:
            result=collect(now)
        self.assertEqual(mocked.call_count,14)
        self.assertEqual(result["models"]["IFS"]["cities_succeeded"],7)
        self.assertEqual(result["models"]["AIFS Single"]["cities_succeeded"],7)


if __name__=="__main__":
    unittest.main()
