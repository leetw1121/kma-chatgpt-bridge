import datetime as dt
import sys
import unittest
import urllib.error
from unittest.mock import patch, Mock
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from fetch_kma import issues, interval, daily_summary, load_locations, build, render, complete_report, complete_csv, one_page

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

    def test_socket_read_timeout_is_retried_instead_of_cancelling_collect(self):
        class Response:
            def __init__(self, fail):
                self.fail=fail
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,size):
                if self.fail:
                    raise TimeoutError("The read operation timed out")
                return (b'{"response":{"header":{"resultCode":"00"},'
                        b'"body":{"items":{"item":[{"category":"PCP"}]},'
                        b'"totalCount":1}}}')
        now=dt.datetime(2026,11,20,11,tzinfo=ZoneInfo("Asia/Seoul"))
        with patch("fetch_kma.urllib.request.urlopen",
                   side_effect=[Response(True),Response(False)]) as api,\\
             patch("fetch_kma.time.sleep") as delay:
            rows,total=one_page("private-key",now,60,127,max_attempts=2)
        self.assertEqual(api.call_count,2)
        self.assertEqual(delay.call_count,1)
        self.assertEqual(total,1)
        self.assertEqual(rows[0]["category"],"PCP")

    def test_repeated_read_timeouts_are_safe_diagnostic_not_uncaught(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,n):raise TimeoutError("secret url must never be logged")
        now=dt.datetime(2026,11,20,11,tzinfo=ZoneInfo("Asia/Seoul"))
        with patch("fetch_kma.urllib.request.urlopen",return_value=Response()) as api,\\
             patch("fetch_kma.time.sleep"):
            with self.assertRaisesRegex(ValueError,"^KMA_READ_TIMEOUT$") as exc:
                one_page("SENSITIVE-PRIVATE-KEY",now,60,127,max_attempts=2)
        self.assertEqual(api.call_count,2)
        self.assertNotIn("SENSITIVE",str(exc.exception))

    def test_first_grid_timeout_tries_different_grid_on_current_issue(self):
        now=dt.datetime(2026,11,20,11,45,tzinfo=ZoneInfo("Asia/Seoul"))
        places=[
            {"name":"가시","level":"city","nx":60,"ny":127},
            {"name":"나군","level":"gun","nx":61,"ny":127},
            {"name":"다구","level":"gu","nx":62,"ny":127}]
        rows=[{"fcstDate":"20261120","fcstTime":"1200",
               "category":"PCP","fcstValue":"1mm"}]
        def getter(secret,issue,nx,ny,**kwargs):
            if nx==60:
                raise ValueError("KMA_READ_TIMEOUT")
            return list(rows)
        with patch("fetch_kma.all_pages",side_effect=getter) as get:
            result=build("private-key",places,now)
        self.assertEqual(result["issue_at_kst"],issues(now)[0].isoformat())
        self.assertEqual(result["locations_succeeded"],2)
        self.assertEqual(result["unique_grids_succeeded"],2)
        self.assertEqual(result["failed_grid_reasons"]["KMA_READ_TIMEOUT"],1)
        self.assertEqual([x["name"] for x in result["locations"]],
                         ["나군","다구"])
        self.assertGreaterEqual(get.call_count,3)

    def test_upstream_429_does_not_probe_second_grid_or_retry(self):
        now=dt.datetime(2026,11,20,11,45,tzinfo=ZoneInfo("Asia/Seoul"))
        locations=[{"name":"가시","level":"city","nx":60,"ny":127},
                   {"name":"나군","level":"gun","nx":61,"ny":127}]
        with patch("fetch_kma.all_pages",side_effect=ValueError("HTTP_STATUS_429")) as getter:
            with self.assertRaisesRegex(ValueError,"KMA_PROVIDER_RATE_LIMITED"):
                build("PRIVATE",locations,now)
        self.assertEqual(getter.call_count,1)

    def test_sample_locations(self):
        self.assertEqual(len(load_locations()),256)
        self.assertEqual(sum(x["level"]=="gun" for x in load_locations()),82)

if __name__=="__main__":
    unittest.main()
