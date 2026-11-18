import datetime as dt
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))

from fetch_ecmwf import collect,choose_recent_cache,priority_from_kma,request_json
from ecmwf_coverage_audit import coverage_rows,daily_counts
from ecmwf_report_gate import usable_model_rows

KST=ZoneInfo("Asia/Seoul")

def record(label,day="2026-11-19",precip=0,snow=0):
    return {"city":label,"days":{day:{"forecast_hours":23,
            "precip_mm":precip,"snowfall_cm":snow,"snowfall_valid":True}}}

class UpstreamLimitTests(unittest.TestCase):
    def test_http429_fails_immediately_without_retries_or_tls_bypass(self):
        error=urllib.error.HTTPError("https://api.open-meteo.com/v1/forecast",
                                    429,"Too Many Requests",{},None)
        with patch("fetch_ecmwf.urllib.request.urlopen",side_effect=error) as urlopen,\
             patch("fetch_ecmwf.request_with_verified_ip") as fallback,\
             patch("fetch_ecmwf.time.sleep") as sleeper:
            with self.assertRaises(urllib.error.HTTPError):
                request_json({"latitude":37.0},max_attempts=3)
        self.assertEqual(urlopen.call_count,1)
        fallback.assert_not_called()
        sleeper.assert_not_called()

    def test_global429_stops_all_model_and_gu_followup_requests(self):
        now=dt.datetime(2026,11,19,1,0,tzinfo=KST)
        cities={f"도시{i:02d}":(37.0,127.0) for i in range(23)}
        districts={f"구{i:02d}":(36.0,127.0) for i in range(14)}
        error=urllib.error.HTTPError("https://api.open-meteo.com/v1/forecast",
                                    429,"Too Many Requests",{},None)
        with patch("fetch_ecmwf.fetch_group",side_effect=error) as get:
            output=collect(now,cities=cities,districts=districts)
        self.assertEqual(get.call_count,1)
        self.assertTrue(output["rate_limited"])
        self.assertEqual(output["models"]["IFS"]["cities_attempted"],10)
        for scope,target in (("models",23),("district_models",14)):
            for name in ("IFS","AIFS Single"):
                status=output[scope][name]
                self.assertEqual(len(status["errors"]),target)
                self.assertEqual(status["cities_succeeded"],0)
                self.assertIn("RATE_LIMITED_COOLDOWN",
                              {e["reason"] for e in status["errors"]})

    def test_prioritize_kma_wet_locations_before_other_regions_in_both_models(self):
        now=dt.datetime(2026,11,19,1,0,tzinfo=KST)
        cities={"C1":(37,127),"C2":(38,128)}
        districts={"G1":(37,127)}
        names=[]
        def sample(model,chunk,when):
            names.append((model,chunk[0][0]))
            return [record(n) for n,_ in chunk],[]
        with patch("fetch_ecmwf.fetch_group",side_effect=sample):
            result=collect(now,cities=cities,districts=districts,
                           priorities=["G1","C2"])
        self.assertEqual(names[:4],[
            ("ecmwf_ifs","C2"),("ecmwf_ifs","G1"),
            ("ecmwf_aifs025_single","C2"),("ecmwf_aifs025_single","G1")])
        self.assertEqual(result["kma_wet_priority_city_count"],1)
        self.assertEqual(result["kma_wet_priority_gu_count"],1)
        self.assertEqual(result["models"]["IFS"]["cities_succeeded"],2)
        self.assertEqual(result["district_models"]["AIFS Single"]["cities_succeeded"],1)

    def test_stale_priority_file_does_not_affect_current_forecast(self):
        now=dt.datetime(2026,11,19,1,0,tzinfo=KST)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"wet.csv"
            path.write_text("date,region\n2026-11-17,old\n"
                            "2026-11-19,current\n2026-11-20,next\n"
                            "2026-11-19,current\n",encoding="utf8")
            self.assertEqual(priority_from_kma(now,path),["current","next"])

class SafeCacheTests(unittest.TestCase):
    def test_partial_run_keeps_prior_full_167_109_by_original_timestamp(self):
        now=dt.datetime(2026,11,19,1,5,tzinfo=KST)
        previous=now-dt.timedelta(minutes=30)
        labels={"models":["시1","시2"],"district_models":["구1"]}
        old={"generated_at_kst":previous.isoformat(),
             "cities_targeted":2,"districts_targeted":1,
             "models":{"IFS":{"status":"verified","cities_succeeded":2,
                                "records":[record("시1"),record("시2")]},
                       "AIFS Single":{"status":"verified","cities_succeeded":2,
                                "records":[record("시1"),record("시2")]}},
             "district_models":{"IFS":{"status":"verified","cities_succeeded":1,
                                "records":[record("구1")]},
                       "AIFS Single":{"status":"verified","cities_succeeded":1,
                                "records":[record("구1")]}}}
        current={"generated_at_kst":now.isoformat(),
                 "cities_targeted":2,"districts_targeted":1,
                 "models":{"IFS":{"status":"verified","cities_succeeded":1,
                     "records":[record("시1",precip=5)],"errors":[
                        {"city":"시2","error":"HTTP_429","reason":"API_RATE_LIMITED"}]},
                    "AIFS Single":{"status":"unavailable","records":[]}},
                 "district_models":{
                    "IFS":{"status":"unavailable","records":[]},
                    "AIFS Single":{"status":"unavailable","records":[]}}}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"cache.json"
            path.write_text(json.dumps(old),encoding="utf8")
            merged=choose_recent_cache(now,current,path)
            self.assertTrue(merged["partial_recent_reuse"])
            self.assertEqual(merged["cached_recent_succeeded"],5)
            self.assertEqual(merged["models"]["IFS"]["cities_succeeded"],2)
            self.assertEqual(merged["models"]["IFS"]["fresh_succeeded"],1)
            self.assertEqual(merged["models"]["IFS"]["cached_succeeded"],1)
            self.assertEqual(merged["models"]["AIFS Single"]["cities_succeeded"],2)
            self.assertEqual(merged["district_models"]["IFS"]["cities_succeeded"],1)
            kept=next(x for x in merged["models"]["IFS"]["records"] if x["city"]=="시2")
            self.assertEqual(kept["collected_at_kst"],previous.isoformat())
            self.assertTrue(kept["cached_from_previous_run"])
            self.assertEqual(len(json.loads(path.read_text())["models"]["IFS"]["records"]),2)
            future=now+dt.timedelta(hours=3,minutes=1)
            expired=choose_recent_cache(future,{"generated_at_kst":future.isoformat(),
                       "cities_targeted":2,"districts_targeted":1,
                       "models":{},"district_models":{}},path)
            self.assertEqual(expired.get("models"),{})

    def test_gated_model_rejects_record_with_expired_original_timestamp(self):
        now=dt.datetime(2026,11,19,1,5,tzinfo=KST)
        stale=now-dt.timedelta(hours=4)
        model={"generated_at_kst":now.isoformat(),
               "models":{"IFS":{"status":"verified","cities_succeeded":1,
                   "records":[{**record("시1",precip=5),
                               "collected_at_kst":stale.isoformat()}]}}}
        got,ok=usable_model_rows({"generated_at_kst":now.isoformat()},model)
        self.assertFalse(ok)
        self.assertEqual(got,{})

class CoverageTests(unittest.TestCase):
    def test_kma_wet_is_not_mistaken_for_ecmwf_wet_or_missing_as_zero(self):
        when="2026-11-19T01:05:00+09:00"
        date="2026-11-19"
        events=[
            {"date":date,"region":"충청남도 천안시 동남구","level":"gu",
             "forecast":"눈","precipitation":"2~8mm","snow":"1~4cm"},
            {"date":date,"region":"충청북도 옥천군","level":"gun",
             "forecast":"비·눈","precipitation":"3mm","snow":"1cm"},
            {"date":date,"region":"충청남도 아산시","level":"city",
             "forecast":"비","precipitation":"12mm","snow":"해당 없음"}]
        m={"generated_at_kst":when,
           "models":{
             "IFS":{"status":"verified","cities_succeeded":1,
                    "records":[record("충청북도 옥천군",precip=0,snow=0)],
                    "errors":[{"city":"충청남도 아산시","reason":"API_RATE_LIMITED"}]},
             "AIFS Single":{"status":"unavailable","cities_succeeded":0,
                    "records":[],"errors":[
                     {"city":"충청북도 옥천군","reason":"API_RATE_LIMITED"},
                     {"city":"충청남도 아산시","reason":"RATE_LIMITED_COOLDOWN"}]}},
           "district_models":{
             "IFS":{"status":"verified","cities_succeeded":1,
                    "records":[record("충청남도 천안시 동남구",precip=1,snow=1)]},
             "AIFS Single":{"status":"unavailable","cities_succeeded":0,
                    "records":[],"errors":[
                     {"city":"충청남도 천안시 동남구","reason":"RATE_LIMITED_COOLDOWN"}]}}}
        result=coverage_rows({"generated_at_kst":when},m,events)
        self.assertEqual(len(result),3)
        self.assertIn("예보 있음",result[0]["IFS_status"])
        self.assertEqual(result[1]["IFS_status"],"무강수")
        self.assertEqual(result[1]["IFS_precip_mm"],0)
        self.assertEqual(result[1]["AIFS_precip_mm"],"")
        self.assertIn("429",result[1]["AIFS_status"])
        self.assertEqual(result[2]["IFS_status"],"수집 제한(429)")
        counts=daily_counts(result)[date]
        self.assertEqual(counts["kma_wet"],3)
        self.assertEqual(counts["IFS_checked"],2)
        self.assertEqual(counts["IFS_missing"],1)
        self.assertEqual(counts["AIFS_checked"],0)
        self.assertEqual(counts["AIFS_missing"],3)

if __name__=="__main__":
    unittest.main()
