import datetime as dt
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from city_forecast import group_rows, rank, fmt, city_csv

def day(rain=True,snow=False,low=0,high=5):
    return {
        "forecast_rain":rain,"forecast_snow":snow,"forecast_mixed":False,
        "precip_mm":{"comparable":True,"lower":low,"upper":high},
        "rain_mm":{"comparable":True,"lower":low,"upper":high},
        "snow_cm":{"comparable":True,"lower":1 if snow else 0,"upper":3 if snow else 0},
        "max_pop_pct":60,"from_kst":"2026-11-14T00:00:00+09:00",
        "to_kst":"2026-11-14T23:00:00+09:00","hours":24,"full_day":True}

class CityForecastTests(unittest.TestCase):
    def test_max_gu_grids_not_sum(self):
        loc=[
          {"name":"대전광역시 동구","level":"gu","nx":67,"ny":100,"days":{"2026-11-14":day(low=10,high=20)}},
          {"name":"대전광역시 중구","level":"gu","nx":67,"ny":100,"days":{"2026-11-14":day(low=10,high=20)}},
          {"name":"대전광역시 서구","level":"gu","nx":68,"ny":100,"days":{"2026-11-14":day(low=15,high=35)}},
          {"name":"제주특별자치도 제주시","level":"city","nx":52,"ny":38,"days":{"2026-11-14":day(low=30,high=45)}},
        ]
        data={"locations":loc,"generated_at_kst":"2026-11-13T11:00:00+09:00",
              "issue_at_kst":"2026-11-13T08:00:00+09:00","locations_succeeded":4,"locations_checked":4}
        result=group_rows(data)
        self.assertEqual(len(result),2)
        metro=next(r for r in result if r["city"]=="대전광역시")
        self.assertEqual(metro["precip"]["lower"],15)
        self.assertEqual(metro["precip"]["upper"],35)
        self.assertEqual(metro["grid_count"],2)
        self.assertEqual(metro["gu_count"],2)
        self.assertEqual(rank(result,"rain")[0]["city"],"제주특별자치도 제주시")
        self.assertEqual(len(city_csv(data).splitlines()),3)

    def test_snow_and_rain_are_ranked_separately(self):
        loc=[
          {"name":"강원특별자치도 원주시","level":"city","nx":76,"ny":122,"days":{"2026-11-14":day(False,True,0,3)}},
          {"name":"강원특별자치도 춘천시","level":"city","nx":73,"ny":134,"days":{"2026-11-14":day(True,False,0,2)}},
        ]
        items=group_rows({"locations":loc})
        self.assertEqual(len(rank(items,"snow")),1)
        self.assertEqual(len(rank(items,"rain")),1)
        self.assertEqual(fmt(rank(items,"snow")[0]["snow_cm"],"cm"),"1~3cm")

    def test_unknown_precip_does_not_become_zero(self):
        x=day()
        x["precip_mm"]={"comparable":False,"lower":None,"upper":None}
        rows=group_rows({"locations":[{"name":"부산광역시 해운대구","level":"gu",
               "nx":99,"ny":76,"days":{"2026-11-14":x}}]})
        self.assertEqual(fmt(rows[0]["precip"],"mm"),"정량 미제공")

if __name__=="__main__":
    unittest.main()
