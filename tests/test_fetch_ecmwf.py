import datetime as dt
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from fetch_ecmwf import fetch_one, status_md

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

if __name__=="__main__":
    unittest.main()
