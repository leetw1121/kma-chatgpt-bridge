import ast
import datetime as dt
import sys
import unittest
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from create_precip_report import rows as precipitation_rows
from ecmwf_report_gate import combined_model_rows

def get_function(name):
    tree=ast.parse((ROOT/"scripts/create_city_pdf.py").read_text(encoding="utf8"))
    selected=[item for item in tree.body
              if isinstance(item,ast.FunctionDef) and item.name==name]
    if len(selected)!=1:
        raise AssertionError("Missing PDF layout function: "+name)
    code=compile(ast.Module(body=selected,type_ignores=[]),"<pdf-layout>","exec")
    captures={"headings":[],"tables":[]}
    env={
        "precipitation_rows":precipitation_rows,
        "combined_model_rows":combined_model_rows,
        "defaultdict":defaultdict,
        "P":lambda text,style:captures["headings"].append(text) or text,
        "PageBreak":lambda:"PAGE_BREAK",
        "Spacer":lambda *a:None,
        "table":lambda headers,values,widths,style:
           captures["tables"].append((headers,values,widths)) or "TABLE"
    }
    exec(code,env)
    return env[name],captures

def day(precip,snow,forecasts=("rain",)):
    return {"forecast_rain":"rain" in forecasts,
            "forecast_snow":"snow" in forecasts,
            "forecast_mixed":False,
            "precip_mm":{"comparable":True,"lower":precip,"upper":precip},
            "snow_cm":{"comparable":True,"lower":snow,"upper":snow},
            "rain_mm":{"comparable":True,"lower":precip,"upper":precip},
            "from_kst":"2026-11-19T00:00:00+09:00",
            "to_kst":"2026-11-19T23:00:00+09:00",
            "hours":24,"full_day":True,"max_pop_pct":60}

class UnifiedPDFLayoutTests(unittest.TestCase):
    def test_one_kma_date_table_contains_city_gu_gun_with_both_metrics(self):
        date="2026-11-19"
        loc=[
            {"name":"충청북도 청주시 상당구","level":"gu","nx":69,"ny":106,
             "days":{date:day(12,2,("rain","snow"))}},
            {"name":"경기도 여주시","level":"city","nx":71,"ny":121,
             "days":{date:day(20,0)}},
            {"name":"강원특별자치도 양양군","level":"gun","nx":88,"ny":138,
             "days":{date:day(12,8,("snow",))}}
        ]
        sample={"locations":loc}
        render,log=get_function("kma_unified_tables")
        story=render(sample,{"title":None,"small":None,"sec":None},
                     precipitation_rows(sample))
        self.assertTrue(story)
        self.assertEqual(len(log["tables"]),1)
        headers,items,widths=log["tables"][0]
        self.assertEqual(headers,["지역","구분","형태","총강수 mm",
                                  "신적설 cm","최대 POP","예보시간"])
        self.assertEqual([item[1] for item in items],["군","구","시"])
        self.assertEqual([item[3] for item in items],["12mm","12mm","20mm"])
        self.assertEqual([item[4] for item in items],["8cm","2cm","해당 없음"])
        self.assertEqual(sum(widths),174)
        self.assertIn("시·구·군 통합 비·눈 3건", " ".join(log["headings"]))

    def test_one_ecmwf_date_table_contains_city_gu_gun_and_both_models(self):
        date="2026-11-19"
        when="2026-11-19T00:35:00+09:00"
        def d(rain,snow):
            return {"forecast_hours":24,"precip_mm":rain,"snowfall_cm":snow,
                    "snowfall_valid":True}
        values={
            "generated_at_kst":when,"cities_targeted":167,
            "districts_targeted":109,
            "models":{
                "IFS":{"status":"verified","cities_succeeded":2,
                       "records":[
                         {"city":"경기도 여주시","days":{date:d(8,0)}},
                         {"city":"강원특별자치도 양양군","days":{date:d(6,2)}}]},
                "AIFS Single":{"status":"verified","cities_succeeded":2,
                       "records":[
                         {"city":"경기도 여주시","days":{date:d(5,0)}},
                         {"city":"강원특별자치도 양양군","days":{date:d(7,3)}}]}},
            "district_models":{
                "IFS":{"status":"verified","cities_succeeded":1,
                      "records":[{"city":"충청북도 청주시 상당구",
                                  "days":{date:d(11,4)}}]},
                "AIFS Single":{"status":"verified","cities_succeeded":1,
                      "records":[{"city":"충청북도 청주시 상당구",
                                  "days":{date:d(10,2)}}]}}}
        render,log=get_function("model_section")
        story,stats=render({"generated_at_kst":when},values,{
            "title":None,"small":None,"sec":None})
        self.assertEqual(story[0],"PAGE_BREAK")
        self.assertEqual(stats,{"rows":3,"city":True,"district":True})
        self.assertEqual(len(log["tables"]),1)
        headers,items,widths=log["tables"][0]
        self.assertEqual(headers,["지역","구분","IFS 강수 mm","IFS 눈 cm",
                                  "AIFS 강수 mm","AIFS 눈 cm"])
        self.assertEqual([x[1] for x in items],["구","시·군","시·군"])
        self.assertEqual([x[2:6] for x in items],
                         [["11","4","10","2"],["6","2","7","3"],
                          ["8","0","5","0"]])
        self.assertEqual(sum(widths),174)

if __name__=="__main__":
    unittest.main()
