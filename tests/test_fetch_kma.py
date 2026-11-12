import datetime as dt
import sys
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from fetch_kma import issues, interval, daily_summary, load_locations

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

    def test_sample_locations(self):
        self.assertGreaterEqual(len(load_locations()),5)

if __name__=="__main__":
    unittest.main()
