import ast
import datetime as dt
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from ecmwf_report_gate import usable_model_rows, combined_model_rows

KMA={"generated_at_kst":"2026-11-17T08:31:09+09:00"}
def model(status,cities,records=None,stamp="2026-11-17T08:31:09+09:00"):
    return {"generated_at_kst":stamp,"models":{
        "IFS":{"status":status,"cities_succeeded":cities,
               "records":records if records is not None else []},
        "AIFS Single":{"status":"unavailable","cities_succeeded":0,"records":[]}}}

def wet_record(precip=1.2,snow=None):
    return [{"city":"서울","days":{"2026-11-18":{"forecast_hours":24,
            "precip_mm":precip,"snowfall_cm":snow,
            "snowfall_valid":snow is not None}}}]

class GateTests(unittest.TestCase):
    def test_current_real_failure_0_of_7_does_not_enter_report(self):
        rows,ok=usable_model_rows(KMA,model("unavailable",0))
        self.assertFalse(ok)
        self.assertEqual(rows,{})

    def test_real_fetched_rain_can_enter_report(self):
        rows,ok=usable_model_rows(KMA,model("verified",7,wet_record()))
        self.assertTrue(ok)
        self.assertIn("IFS",rows[("2026-11-18","서울")])

    def test_real_fetched_snow_can_enter_report(self):
        rows,ok=usable_model_rows(KMA,model("verified",7,wet_record(0,2.5)))
        self.assertTrue(ok)

    def test_verified_dry_models_do_not_create_empty_appendix(self):
        rows,ok=usable_model_rows(KMA,model("verified",7,wet_record(0,0)))
        self.assertFalse(ok)
        self.assertFalse(rows)

    def test_stale_cached_models_are_excluded(self):
        rows,ok=usable_model_rows(KMA,model("verified_cached_recent",7,wet_record(),
                            stamp="2026-11-16T08:31:09+09:00"))
        self.assertFalse(ok)

    def test_gu_only_real_forecast_is_isolated_from_city_collection(self):
        district=wet_record(2.4,0)
        district[0]["city"]="서울특별시 강남구"
        sample=model("verified",7,wet_record(0,0))
        sample["district_models"]={
            "IFS":{"status":"verified","cities_succeeded":1,"records":district}}
        old,has_old=usable_model_rows(KMA,sample)
        gu,has_gu=usable_model_rows(KMA,sample,scope="district")
        self.assertFalse(has_old)
        self.assertEqual(old,{})
        self.assertTrue(has_gu)
        self.assertIn(("2026-11-18","서울특별시 강남구"),gu)

    def test_old_167_cache_cannot_masquerade_as_district_forecast(self):
        sample=model("verified",7,wet_record())
        rows,good=usable_model_rows(KMA,sample,scope="district")
        self.assertFalse(good)
        self.assertEqual(rows,{})
        with self.assertRaises(ValueError):
            usable_model_rows(KMA,sample,scope="unknown")

    def test_combined_date_table_preserves_dry_ifs_zero_when_aifs_is_wet(self):
        def d(mm,snow=0):
            return {"forecast_hours":24,"precip_mm":mm,"snowfall_cm":snow,
                    "snowfall_valid":True}
        day="2026-11-18"
        sample={"generated_at_kst":KMA["generated_at_kst"],
          "models":{
            "IFS":{"status":"verified","cities_succeeded":1,
                   "records":[{"city":"경기도 수원시","days":{day:d(2)}}]},
            "AIFS Single":{"status":"verified","cities_succeeded":1,
                   "records":[{"city":"경기도 수원시","days":{day:d(0)}}]}},
          "district_models":{
            "IFS":{"status":"verified","cities_succeeded":1,
                   "records":[{"city":"경기도 수원시 영통구","days":{day:d(0)}}]},
            "AIFS Single":{"status":"verified","cities_succeeded":1,
                   "records":[{"city":"경기도 수원시 영통구","days":{day:d(7,1)}}]}}}
        rows,city,gu=combined_model_rows(KMA,sample)
        self.assertEqual(len(rows),2)
        self.assertTrue(city and gu)
        self.assertEqual([r["region"] for r in rows],
                         ["경기도 수원시 영통구","경기도 수원시"])
        self.assertEqual(rows[0]["series"]["IFS"]["precip_mm"],0)
        self.assertEqual(rows[0]["scope"],"gu")
        self.assertEqual(rows[1]["scope"],"city_county")

    def test_combined_snow_first_and_unavailable_not_fake_zero(self):
        def d(mm,snow,valid=True):
            return {"forecast_hours":24,"precip_mm":mm,"snowfall_cm":snow,
                    "snowfall_valid":valid}
        day="2026-11-18"
        sample={"generated_at_kst":KMA["generated_at_kst"],
          "models":{"IFS":{"status":"verified","cities_succeeded":2,"records":[
              {"city":"충청북도 청주시","days":{day:d(5,0)}},
              {"city":"경기도 연천군","days":{day:d(5,3)}}]}},
          "district_models":{"IFS":{"status":"verified","cities_succeeded":1,"records":[
              {"city":"서울특별시 강남구","days":{day:d(0,None,False)}}]}}}
        rows,city,gu=combined_model_rows(KMA,sample)
        self.assertEqual([r["region"] for r in rows],
                         ["경기도 연천군","충청북도 청주시"])
        self.assertTrue(city)
        self.assertFalse(gu)

    def test_snow_wins_over_heavy_rain_and_missing_snow_uses_precip(self):
        date="2026-11-18"
        def d(precip,snow=None):
            return {"forecast_hours":24,"precip_mm":precip,
                    "snowfall_cm":snow,"snowfall_valid":snow is not None}
        entries=[
            ("가-눈5",d(2,5)),
            ("나-눈2",d(1,2)),
            ("다-비99-눈없음",d(99)),
            ("라-비30-눈0",d(30,0)),
            ("마-비10-눈없음",d(10)),
            ("바-눈2-비8",d(8,2)),
        ]
        sample={"generated_at_kst":KMA["generated_at_kst"],
                "models":{"IFS":{"status":"verified",
                                "cities_succeeded":len(entries),
                                "records":[{"city":name,"days":{date:value}}
                                           for name,value in entries]}},
                "district_models":{}}
        got,city,gu=combined_model_rows(KMA,sample)
        self.assertTrue(city)
        self.assertFalse(gu)
        self.assertEqual([x["region"] for x in got],
            ["가-눈5","바-눈2-비8","나-눈2",
             "다-비99-눈없음","라-비30-눈0","마-비10-눈없음"])

    def test_duplicate_model_region_across_scopes_rejected(self):
        item=wet_record()
        sample=model("verified",1,item)
        sample["district_models"]={"IFS":{
             "status":"verified","cities_succeeded":1,"records":item}}
        with self.assertRaisesRegex(ValueError,"DUPLICATE_ECMWF_CITY_AND_GU_LABEL"):
            combined_model_rows(KMA,sample)

    def test_pdf_builder_does_not_create_pagebreak_when_model_missing(self):
        # Extract only its pure section function: PDF libraries are installed
        # later in the production workflow and must not be needed for unit tests.
        source=ast.parse((ROOT/"scripts/create_city_pdf.py").read_text(encoding="utf-8"))
        funcs=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=="model_section"]
        self.assertEqual(len(funcs),1)
        calls=[]
        ns={"combined_model_rows":combined_model_rows,
            "PageBreak":lambda: calls.append("PageBreak"),
            "P":lambda text,style:text, "Spacer":lambda *a:None,
            "table":lambda *a:None, "dt":dt}
        exec(compile(ast.Module(body=funcs,type_ignores=[]),"<section>","exec"),ns)
        story,ok=ns["model_section"](KMA,model("unavailable",0),{
            "title":None,"small":None,"sec":None,"note":None})
        self.assertEqual(ok["rows"],0)
        self.assertEqual(story,[])
        self.assertEqual(calls,[])

    def test_no_blank_section_when_data_verified_but_no_precip(self):
        source=ast.parse((ROOT/"scripts/create_city_pdf.py").read_text(encoding="utf-8"))
        funcs=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=="model_section"]
        ns={"combined_model_rows":combined_model_rows,"PageBreak":lambda:"should_not_be_called",
            "P":lambda text,style:text}
        exec(compile(ast.Module(body=funcs,type_ignores=[]),"<section>","exec"),ns)
        result,ok=ns["model_section"](KMA,model("verified",7,wet_record(0,0)),
                          {"title":None,"small":None})
        self.assertEqual(result,[])
        self.assertEqual(ok["rows"],0)

if __name__=="__main__":
    unittest.main()
