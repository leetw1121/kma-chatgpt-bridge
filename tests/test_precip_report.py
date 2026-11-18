import csv
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from create_precip_report import event_for, rows, render, as_csv


def day(rain, snow, precip, date="2026-11-14"):
    return {
        "forecast_rain":rain,"forecast_snow":snow,"forecast_mixed":rain and snow,
        "precip_mm":{"comparable":True,"lower":precip,"upper":precip},
        "rain_mm":{"comparable":True,"lower":precip if rain else 0,"upper":precip if rain else 0},
        "snow_cm":{"comparable":True,"lower":2 if snow else 0,"upper":2 if snow else 0},
        "max_pop_pct":70,"from_kst":date+"T00:00:00+09:00",
        "to_kst":date+"T23:00:00+09:00","hours":24,"full_day":True
    }


class RainSnowOnlyTests(unittest.TestCase):
    def test_explicit_rain_snow_types(self):
        self.assertEqual(event_for(day(True,False,2)),"비")
        self.assertEqual(event_for(day(False,True,0)),"눈")
        self.assertEqual(event_for(day(True,True,3)),"비·눈 혼합")
        self.assertIsNone(event_for(day(False,False,0)))

    def test_all_wet_regions_no_top10_cut(self):
        locations=[]
        for index in range(20):
            locations.append({"name":"지역 "+str(index),"level":"gu","nx":50+index,
                              "ny":100,"days":{"2026-11-14":day(True,False,index+1)}})
        locations.append({"name":"마른 지역","level":"city","nx":100,"ny":100,
                          "days":{"2026-11-14":day(False,False,0)}})
        sample={"issue_at_kst":"2026-11-13T23:00:00+09:00",
                "generated_at_kst":"2026-11-14T00:12:00+09:00",
                "locations":locations,"locations_checked":21,
                "locations_succeeded":21,"unique_grids_checked":21,
                "unique_grids_succeeded":21}
        entries=rows(sample)
        self.assertEqual(len(entries),20)
        text=render(sample)
        self.assertIn("지역 19",text)
        self.assertIn("지역 0",text)
        self.assertNotIn("| 마른 지역 |",text)
        self.assertEqual(len(as_csv(sample).splitlines()),21)

    def test_all_256_levels_share_one_date_ranking_with_precip_and_snow(self):
        date="2026-11-19"
        records=[
           {"name":"서울특별시 강남구","level":"gu","nx":61,"ny":126,
            "days":{date:day(True,False,10)}},
           {"name":"경기도 평택시","level":"city","nx":62,"ny":114,
            "days":{date:day(True,False,25)}},
           {"name":"강원특별자치도 양양군","level":"gun","nx":88,"ny":138,
            "days":{date:day(False,True,10)}},
        ]
        records[2]["days"][date]["snow_cm"]={
            "comparable":True,"lower":2,"upper":7}
        sample={"locations":records,"issue_at_kst":"2026-11-18T23:00:00+09:00",
                "generated_at_kst":"2026-11-19T00:00:00+09:00",
                "locations_succeeded":3,"locations_checked":3,
                "unique_grids_succeeded":3,"unique_grids_checked":3}
        got=rows(sample)
        self.assertEqual([x["region"] for x in got],
                         ["경기도 평택시","강원특별자치도 양양군",
                          "서울특별시 강남구"])
        self.assertEqual(got[1]["precipitation"],"10mm")
        self.assertEqual(got[1]["snow"],"2~7cm (범위)")
        self.assertEqual(got[2]["snow"],"해당 없음")
        content=render(sample)
        self.assertIn("시·구·군 통합 · 3곳",content)
        self.assertEqual(content.count("### "+date+" · 시·구·군 통합"),1)
        self.assertIn("| 총강수(mm) | 신적설(cm) |",content)
        self.assertEqual(len(as_csv(sample).splitlines()),4)

    def test_unknown_amount_does_not_mean_snow_or_rain(self):
        blank={"precip_mm":{"comparable":False,"lower":None,"upper":None},
               "snow_cm":{"comparable":False,"lower":None,"upper":None},
               "forecast_rain":False,"forecast_snow":False}
        self.assertIsNone(event_for(blank))
        blank["forecast_snow"]=True
        self.assertEqual(event_for(blank),"눈")


if __name__=="__main__":
    unittest.main()
