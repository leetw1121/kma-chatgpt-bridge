import datetime as dt
import sys
import unittest
from unittest.mock import patch
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from fetch_kma import issues, interval, daily_summary, load_locations, build, render

class ForecastTests(unittest.TestCase):
    def test_issue_rollover(self):
        now=dt.datetime(2026,11,13,0,12,tzinfo=ZoneInfo("Asia/Seoul"))
        self.assertEqual(issues(now)[0].strftime("%Y%m%d%H%M"),"202611122300")

    def test_rain_and_snow_intervals(self):
        self.assertEqual(interval("1mm 미만","PCP"),(0.0,1.0))
        self.assertEqual(interval("강수없음","PCP"),(0.0,0.0))
        self.assertEqual(interval("1~3cm","SNO"),(1.0,3.0))
        self.assertEqual(interval("확인불가","PCP"),(None,None))

    def test_daily_aggregation_without_inventing_midpoints(self):
        now=dt.datetime(2026,11,12,18,tzinfo=ZoneInfo("Asia/Seoul"))
        rows=[]
        for hour,rain in ((19,"1mm 미만"),(20,"2.0mm")):
            for kind,value in (("PCP",rain),("SNO","적설없음"),("POP","80")):
                rows.append({"fcstDate":"20261112","fcstTime":f"{hour:02d}00",
                             "category":kind,"fcstValue":value})
        value=daily_summary(rows,now)["2026-11-12"]
        self.assertEqual(value["rain_mm"]["lower"],2.0)
        self.assertEqual(value["rain_mm"]["upper"],3.0)
        self.assertEqual(value["max_pop_pct"],80)

    def test_snow_is_not_counted_as_rain(self):
        now=dt.datetime(2026,11,12,18,tzinfo=ZoneInfo("Asia/Seoul"))
        rows=[
            {"fcstDate":"20261112","fcstTime":"1900","category":cat,"fcstValue":value}
            for cat,value in (("PCP","2mm"),("SNO","1cm"),("PTY","3"),("POP","80"))
        ]
        values=daily_summary(rows,now)["2026-11-12"]
        self.assertEqual(values["precip_mm"]["lower"],2.0)
        self.assertFalse(values["rain_mm"]["comparable"])
        self.assertFalse(values["full_day"])

    def test_partial_day_not_claimed_as_full_day(self):
        now=dt.datetime(2026,11,12,18,tzinfo=ZoneInfo("Asia/Seoul"))
        rows=[{"fcstDate":"20261112","fcstTime":"1900","category":"PCP","fcstValue":"1mm"}]
        d=daily_summary(rows,now)["2026-11-12"]
        self.assertEqual(d["hours"],1)
        self.assertFalse(d["full_day"])

    def test_shared_grid_fetch_once(self):
        now=dt.datetime(2026,11,12,18,tzinfo=ZoneInfo("Asia/Seoul"))
        places=[
            {"name":"서울특별시 종로구","level":"gu","nx":60,"ny":127},
            {"name":"서울특별시 중구","level":"gu","nx":60,"ny":127},
        ]
        rows=[{"fcstDate":"20261112","fcstTime":"1900","category":"PCP","fcstValue":"1mm"},
              {"fcstDate":"20261112","fcstTime":"1900","category":"SNO","fcstValue":"적설없음"},
              {"fcstDate":"20261112","fcstTime":"1900","category":"POP","fcstValue":"60"}]
        with patch("fetch_kma.all_pages",return_value=rows) as getter:
            data=build("dummy",places,now)
        self.assertEqual(getter.call_count,1)
        self.assertEqual(data["unique_grids_checked"],1)
        self.assertEqual(data["locations_succeeded"],2)
        self.assertIn("동일 격자",render(data))

    def test_sample_locations(self):
        self.assertGreaterEqual(len(load_locations()),5)

if __name__=="__main__":
    unittest.main()
