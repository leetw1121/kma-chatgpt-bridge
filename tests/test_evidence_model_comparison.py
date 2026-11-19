import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from model_agreement_7day import agreement_by_date, markdown_report, wet_class
from forecast_skill_snapshot import snapshot_rows, write_snapshot
from forecast_skill_verification import evaluate, skill, read_snapshots, read_observations


NOW="2026-11-20T06:30:00+09:00"
DAY="2026-11-20"
NEXT="2026-11-21"


def model_day(precip, snow=0.0,valid=True):
    return {"forecast_hours":24,"precip_mm":precip,
            "snowfall_cm":snow,"snowfall_valid":valid}


def sample_model():
    return {
        "generated_at_kst":NOW,
        "models":{
            "IFS":{"status":"verified","cities_succeeded":3,"records":[
                {"city":"시1","days":{DAY:model_day(5,2)}},
                {"city":"시2","days":{DAY:model_day(0,0)}},
                {"city":"시3","days":{DAY:model_day(3,2)}}]},
            "AIFS Single":{"status":"verified","cities_succeeded":3,"records":[
                {"city":"시1","days":{DAY:model_day(0,0)}},
                {"city":"시2","days":{DAY:model_day(0,0)}},
                {"city":"시3","days":{DAY:model_day(4,1)}}]}},
        "district_models":{
            "IFS":{"status":"verified","cities_succeeded":1,"records":[
                {"city":"구1","days":{DAY:model_day(2,0)}}]},
            "AIFS Single":{"status":"unavailable","cities_succeeded":0,"records":[]}}
    }


class SevenDayAgreementTests(unittest.TestCase):
    def test_only_actual_verified_pairs_are_counted(self):
        kma={"generated_at_kst":NOW,"locations":[]}
        a=agreement_by_date(kma,sample_model(),
                            city_labels=["시1","시2","시3"],gu_labels=["구1"])
        self.assertEqual(len(a),7)
        day=a[0]
        self.assertEqual(day["model_regions_targeted"],4)
        self.assertEqual(day["pairs_checked"],3)
        self.assertEqual(day["both_wet"],1)
        self.assertEqual(day["one_wet"],1)
        self.assertEqual(day["both_dry"],1)
        self.assertEqual(day["both_snow"],1)
        self.assertEqual(day["one_snow"],1)
        self.assertEqual(day["pairs_unverified"],1)
        self.assertIsNone(day["kma_wet"])
        self.assertEqual(a[1]["pairs_unverified"],4)
        self.assertEqual(a[1]["both_dry"],0)
        report=markdown_report(a,kma)
        self.assertIn("적중률이 아닙니다",report)
        self.assertIn("기상청 초단기예보는 아직 별도로 수집하지 않습니다",report)
        self.assertIn("미제공",report)

    def test_partial_day_is_not_counted_as_full_day_dry_agreement(self):
        model=sample_model()
        model["models"]["IFS"]["records"][1]["days"][DAY]["forecast_hours"]=6
        counts=agreement_by_date({"generated_at_kst":NOW,"locations":[]},
                                 model,city_labels=["시1","시2","시3"],
                                 gu_labels=["구1"])
        day=counts[0]
        self.assertEqual(day["pairs_checked"],2)
        self.assertEqual(day["pairs_partial"],1)
        self.assertEqual(day["pairs_unverified"],1)
        self.assertEqual(day["both_dry"],0)
        self.assertEqual(day["both_wet"],1)
        self.assertEqual(day["one_wet"],1)
        self.assertIn("일부기간 쌍",markdown_report(counts,{"generated_at_kst":NOW}))

    def test_snapshot_does_not_rearchive_cached_or_unfetched_models(self):
        sample=sample_model()
        item=sample["models"]["IFS"]["records"][0]
        item["collected_at_kst"]="2026-11-20T05:12:00+09:00"
        sample["models"]["IFS"]["records"][1]["cached_from_previous_run"]=True
        sample["models"]["AIFS Single"]["status"]="verified_cached_recent"
        kma={"generated_at_kst":NOW,"issue_at_kst":NOW,"locations":[]}
        only_models=snapshot_rows(kma,sample,include_kma=False,include_ecmwf=True)
        self.assertEqual(len(only_models),3)
        self.assertEqual({x["model"] for x in only_models},{"IFS"})
        city1=next(x for x in only_models if x["region"]=="시1")
        self.assertEqual(city1["retrieved_at_kst"],"2026-11-20T05:12:00+09:00")
        self.assertFalse(any(x["region"]=="시2" for x in only_models))
        self.assertEqual(snapshot_rows(kma,sample,include_kma=False,
                                       include_ecmwf=False),[])
        with tempfile.TemporaryDirectory() as folder:
            output=write_snapshot([],Path(folder)/"zero.jsonl")
            self.assertTrue(output.exists())
            self.assertEqual(output.stat().st_size,0)

    def test_unknown_snow_and_missing_model_not_dry(self):
        self.assertTrue(wet_class(model_day(1,None,False)))
        self.assertFalse(wet_class(model_day(0,None,False)))
        self.assertIsNone(wet_class({"precip_mm":None,"snowfall_cm":None,
                                    "snowfall_valid":False}))
        a=agreement_by_date({"generated_at_kst":NOW,"locations":[]},None,
                            city_labels=["시1"],gu_labels=[])
        self.assertEqual(a[0]["pairs_unverified"],1)
        self.assertEqual(a[0]["both_dry"],0)

    def test_duplicate_and_cross_scope_label_cannot_be_hidden(self):
        with self.assertRaisesRegex(ValueError,"DUPLICATE_MODEL_LOCATION"):
            agreement_by_date({"generated_at_kst":NOW,"locations":[]},None,
                              city_labels=["시1","시1"],gu_labels=[])
        with self.assertRaisesRegex(ValueError,"SCOPE_COLLISION"):
            agreement_by_date({"generated_at_kst":NOW,"locations":[]},None,
                              city_labels=["시1"],gu_labels=["시1"])

    def test_snapshot_captures_both_model_and_kma_without_fake_snow(self):
        dry_kma_day={
            "hours":24,"full_day":True,
            "forecast_rain":False,"forecast_snow":False,"forecast_mixed":False,
            "precip_mm":{"comparable":True,"lower":0,"upper":0},
            "snow_cm":{"comparable":False,"lower":None,"upper":None},
        }
        kma={"issue_at_kst":"2026-11-19T23:00:00+09:00",
             "generated_at_kst":NOW,"locations":[{
                "name":"시1","level":"city","days":{NEXT:dry_kma_day}
             }]}
        records=snapshot_rows(kma,sample_model())
        self.assertEqual(len(records),8)
        only_kma=next(r for r in records if r["model"]=="KMA_SHORT")
        self.assertFalse(only_kma["predicted_precipitation"])
        self.assertIsNone(only_kma["predicted_snow"])
        sample=next(r for r in records if r["model"]=="IFS" and r["region"]=="시1")
        self.assertTrue(sample["predicted_precipitation"])
        self.assertEqual(sample["snow_origin"],"Open-Meteo_model_derived")
        self.assertIsNone(sample["source_time_kst"])
        with tempfile.TemporaryDirectory() as tmp:
            filename=write_snapshot(records,Path(tmp)/"snapshot.jsonl")
            self.assertEqual(len(read_snapshots(filename)),len(records))


class SkillVerificationTests(unittest.TestCase):
    def setUp(self):
        self.as_of=dt.datetime(2026,11,21,0,5,tzinfo=dt.timezone(dt.timedelta(hours=9)))
        self.pred=[]
        for region,actual_wet in (("A",True),("B",False)):
            for model in ("KMA_SHORT","IFS","AIFS Single"):
                predicted=(actual_wet or (model=="IFS" and region=="B"))
                self.pred.append({
                    "retrieved_at_kst":"2026-11-19T18:00:00+09:00",
                    "valid_date":DAY,"region":region,"model":model,
                    "forecast_hours":24,"full_day":True,
                    "predicted_precipitation":predicted,"predicted_snow":None
                })
        self.truth={
            (DAY,"A"):{"rain":4.5,"snow":None,"source":"AWS measured"},
            (DAY,"B"):{"rain":0.0,"snow":None,"source":"AWS measured"}}

    def test_all_models_compared_on_identical_observed_examples(self):
        results=evaluate(self.pred,self.truth,self.as_of)
        kma=results["D0-3","rain_or_snow","KMA_SHORT"]
        ifs=results["D0-3","rain_or_snow","IFS"]
        self.assertEqual(kma["n"],2)
        self.assertEqual(kma["hits"],1)
        self.assertEqual(kma["correct_negatives"],1)
        self.assertEqual(ifs["false_alarms"],1)
        self.assertEqual(ifs["precision"],0.5)
        self.assertEqual(results["D0-3","snow","KMA_SHORT"]["n"],0)
        self.assertFalse(kma["sample_sufficient_for_preliminary_review"])

    def test_missing_one_model_excludes_case_from_paired_skill(self):
        pred=[p for p in self.pred if not (p["model"]=="AIFS Single" and
                                          p["region"]=="A")]
        results=evaluate(pred,self.truth,self.as_of)
        self.assertEqual(results["D0-3","rain_or_snow","IFS"]["n"],1)
        self.assertEqual(results["D0-3","rain_or_snow","KMA_SHORT"]["n"],1)

    def test_partial_day_future_prediction_not_eligible(self):
        copy=[dict(p) for p in self.pred]
        copy[0]["full_day"]=False
        results=evaluate(copy,self.truth,self.as_of)
        self.assertEqual(results["D0-3","rain_or_snow","IFS"]["n"],1)

    def test_observation_file_requires_independent_source_and_full_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            filepath=Path(tmp)/"observations.csv"
            filepath.write_text(
                "valid_date,region,coverage_hours,observed_precip_mm,"
                "observed_snow_cm,observation_source\n"
                "2026-11-20,A,24,4.5,,AWS measured\n"
                "2026-11-20,B,23,0,,AWS measured\n",
                encoding="utf-8")
            observations=read_observations(filepath)
            self.assertEqual(len(observations),1)
            self.assertIsNone(observations[(DAY,"A")]["snow"])
            filepath.write_text(
                "valid_date,region,coverage_hours,observed_precip_mm,"
                "observed_snow_cm,observation_source\n"
                "2026-11-20,A,24,0,0,forecast\n",encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"INDEPENDENT_MEASURED_SOURCE_REQUIRED"):
                read_observations(filepath)


if __name__=="__main__":
    unittest.main()
