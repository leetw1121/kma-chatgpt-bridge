import datetime as dt
import sys
import unittest
from unittest.mock import patch
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from fetch_kma import issues, interval, daily_summary, load_locations, build, render, complete_report, complete_csv

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

    def test_complete_output_contains_dry_and_unavailable_regions(self):
        now=dt.datetime(2026,11,13,0,12,tzinfo=ZoneInfo("Asia/Seoul"))
        base={"schema_version":"1.1","issue_at_kst":now.isoformat(),
              "generated_at_kst":now.isoformat(),"locations_succeeded":2,
              "locations_checked":2,"unique_grids_succeeded":2,"unique_grids_checked":2}
        rainy={"from_kst":"2026-11-13T00:00:00+09:00",
               "to_kst":"2026-11-13T23:00:00+09:00","hours":24,"full_day":True,
               "max_pop_pct":80,
               "precip_mm":{"comparable":True,"lower":2,"upper":4},
               "rain_mm":{"comparable":True,"lower":2,"upper":4},
               "snow_cm":{"comparable":True,"lower":0,"upper":0}}
        dry={"from_kst":"2026-11-13T00:00:00+09:00",
             "to_kst":"2026-11-13T23:00:00+09:00","hours":24,"full_day":True,
             "max_pop_pct":10,
             "precip_mm":{"comparable":True,"lower":0,"upper":0},
             "rain_mm":{"comparable":True,"lower":0,"upper":0},
             "snow_cm":{"comparable":True,"lower":0,"upper":0}}
        base["locations"]=[
             {"name":"서울특별시 강남구","level":"gu","nx":61,"ny":126,
              "days":{"2026-11-13":rainy}},
             {"name":"서울특별시 종로구","level":"gu","nx":60,"ny":127,
              "days":{"2026-11-13":dry}}]
        full=complete_report(base)
        self.assertIn("서울특별시 강남구",full)
        self.assertIn("서울특별시 종로구",full)
        self.assertIn("2~4mm",full)
        self.assertIn("0mm",full)
        table=complete_csv(base)
        self.assertEqual(len(table.splitlines()),17)  # 2 regions x 8 calendar dates in a 168h window + header
        self.assertIn("not_provided",table)
        self.assertIn("서울특별시 종로구",table)
        self.assertIn("서울특별시 강남구",table)

    def test_sample_locations(self):
        self.assertGreaterEqual(len(load_locations()),5)

if __name__=="__main__":
    unittest.main()
