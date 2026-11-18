import datetime as dt
import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from ecmwf_city_locations import build_city_locations,grid_to_latlon
from fetch_ecmwf import (
    fetch_one,fetch_group,status_md,request_json,classify_error,collect
)

KST=ZoneInfo("Asia/Seoul")

class ECMWFCityExpansionTests(unittest.TestCase):
    def test_kma_grid_to_geographic_coordinates(self):
        lat,lon=grid_to_latlon(60,127)
        self.assertAlmostEqual(lat,37.58,delta=0.1)
        self.assertAlmostEqual(lon,126.99,delta=0.1)

    def test_all_167_city_county_labels_instead_of_seven_hardcoded(self):
        points=build_city_locations()
        self.assertEqual(len(points),167)
        self.assertIn("울산광역시 울주군",points)
        self.assertIn("경기도 양평군",points)
        self.assertIn("서울특별시",points)
        self.assertIn("경기도 수원시",points)
        self.assertIn("충청북도 청주시",points)
        self.assertIn("강원특별자치도 강릉시",points)
        for city,(lat,lon) in points.items():
            self.assertTrue(32<=lat<=40,city)
            self.assertTrue(124<=lon<=132,city)

    def test_batch_json_array_is_assigned_to_correct_city(self):
        start=dt.datetime(2026,11,17,11,0,tzinfo=KST)
        times=[(start+dt.timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(24)]
        payload=[
            {"latitude":37.5,"longitude":127.0,"hourly":{
                "time":times,"precipitation":[1]*24,"snowfall":[0]*24}},
            {"latitude":35.2,"longitude":129.1,"hourly":{
                "time":times,"precipitation":[2]*24,"snowfall":[0]*24}},
        ]
        candidates=[("서울특별시",(37.5,127.0)),("부산광역시",(35.2,129.1))]
        with patch("fetch_ecmwf.request_json",return_value=payload) as mocked:
            good,errors=fetch_group("ecmwf_ifs",candidates,start)
        self.assertEqual(errors,[])
        self.assertEqual([r["city"] for r in good],["서울특별시","부산광역시"])
        self.assertEqual(sum(day["precip_mm"] for day in good[0]["days"].values()),24)
        self.assertEqual(sum(day["precip_mm"] for day in good[1]["days"].values()),48)
        args=mocked.call_args.args[0]
        self.assertEqual(args["latitude"],"37.5,35.2")
        self.assertEqual(args["longitude"],"127.0,129.1")

    def test_partial_batch_response_errors_do_not_poison_good_city(self):
        start=dt.datetime(2026,11,17,11,0,tzinfo=KST)
        times=[(start+dt.timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(24)]
        good={"hourly":{"time":times,"precipitation":[1]*24,"snowfall":[0]*24}}
        invalid={"hourly":{"time":[]}}
        with patch("fetch_ecmwf.request_json",return_value=[good,invalid]):
            okay,errors=fetch_group("ecmwf_ifs",[
                ("서울",(37.5,127.0)),("부산",(35.2,129.1))],start)
        self.assertEqual(len(okay),1)
        self.assertEqual(okay[0]["city"],"서울")
        self.assertEqual(errors[0]["city"],"부산")

    def test_bounded_batches_cover_all_points_for_both_models(self):
        start=dt.datetime(2026,11,17,11,0,tzinfo=KST)
        places={f"도시{i:02d}":(36.0,128.0) for i in range(23)}
        def fake_fetch(model,chunk,now):
            return ([{"city":city,"days":{},"hours_valid":24} for city,_ in chunk],[])
        with patch("fetch_ecmwf.fetch_group",side_effect=fake_fetch) as mock:
            result=collect(start,cities=places)
        self.assertEqual(mock.call_count,6)
        for name in ("IFS","AIFS Single"):
            self.assertEqual(result["models"][name]["cities_succeeded"],23)
            self.assertEqual(result["models"][name]["batches_attempted"],3)
            self.assertEqual(result["models"][name]["cities_attempted"],23)
        self.assertEqual(result["cities_targeted"],23)

    def test_first_batch_failure_still_collects_other_cities(self):
        now=dt.datetime(2026,11,17,11,0,tzinfo=KST)
        places={f"도시{i:02d}":(36.0,128.0) for i in range(23)}
        def fake_fetch(model,chunk,now):
            if chunk[0][0]=="도시00":
                raise urllib.error.URLError(TimeoutError("TLS handshake timed out"))
            return ([{"city":city,"days":{},"hours_valid":24} for city,_ in chunk],[])
        with patch("fetch_ecmwf.fetch_group",side_effect=fake_fetch):
            result=collect(now,cities=places)
        for name in ("IFS","AIFS Single"):
            self.assertEqual(result["models"][name]["cities_succeeded"],13)
            self.assertEqual(result["models"][name]["cities_attempted"],23)
            self.assertEqual(len(result["models"][name]["errors"]),10)
            self.assertEqual(result["models"][name]["errors"][0]["reason"],
                             "TLS_HANDSHAKE_TIMEOUT")

class ECMWFExistingSafetyTests(unittest.TestCase):
    def test_verified_model_values_are_read_not_invented(self):
        start=dt.datetime(2026,11,13,0,0,tzinfo=KST)
        times=[(start+dt.timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(24)]
        response={"latitude":37.56,"longitude":126.97,"timezone":"Asia/Seoul",
                  "hourly":{"time":times,"precipitation":[1]*24,"snowfall":[0]*24}}
        with patch("fetch_ecmwf.urllib.request.urlopen",
                   return_value=io.BytesIO(json.dumps(response).encode())):
            result=fetch_one("ecmwf_ifs","서울",37.5665,126.9780,start)
        self.assertEqual(result["hours_valid"],24)
        self.assertEqual(result["days"]["2026-11-13"]["precip_mm"],24)

    def test_model_status_never_claims_unconnected_provider_success(self):
        result={"generated_at_kst":"2026-11-13T00:00:00+09:00","models":{
             "IFS":{"cities_succeeded":0,"cities_requested":7,"status":"unavailable"},
             "AIFS Single":{"cities_succeeded":2,"cities_requested":7,"status":"verified"}}}
        text=status_md(result)
        self.assertIn("2/7",text)
        self.assertNotIn("IFS | 0/7",text)

    def test_tls_handshake_timeout_is_explicit(self):
        error=urllib.error.URLError(TimeoutError("_ssl.c:1001: The handshake operation timed out"))
        result=classify_error(error)
        self.assertEqual(result["reason"],"TLS_HANDSHAKE_TIMEOUT")

    def test_transient_tls_retry_recovers(self):
        good=io.BytesIO(b'{"hourly": {"time": []}}')
        with patch("fetch_ecmwf.urllib.request.urlopen",side_effect=[
                urllib.error.URLError(TimeoutError("handshake timed out")),good]) as mocked,\
             patch("fetch_ecmwf.time.sleep") as sleep:
            result=request_json({"latitude":37.5,"longitude":127.0},max_attempts=2)
        self.assertEqual(mocked.call_count,2)
        self.assertEqual(sleep.call_count,1)
        self.assertIn("hourly",result)

if __name__=="__main__":
    unittest.main()
