import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from check_freshness import needs_refresh, needs_ecmwf_retry
from fetch_kma import KST

class FreshnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.directory=self.root/"data"
        self.directory.mkdir()
        self.now=dt.datetime(2026,11,14,12,20,tzinfo=KST)
        self.forecast={"issue_at_kst":"2026-11-14T11:00:00+09:00",
                       "generated_at_kst":"2026-11-14T11:50:00+09:00",
                       "locations_checked":174,"locations_succeeded":174}
        self.manifest={"scope":"city_only","source_generated_at_kst":self.forecast["generated_at_kst"]}
        self.write()

    def write(self):
        (self.directory/"latest.json").write_text(json.dumps(self.forecast),encoding="utf-8")
        (self.directory/"pdf_manifest.json").write_text(json.dumps(self.manifest),encoding="utf-8")
        (self.directory/"rain_snow_latest.pdf").write_bytes(b"%PDF-"+b"X"*1024)

    def test_skip_exact_current_forecast_and_pdf(self):
        self.assertEqual(needs_refresh(self.now,self.root),(False,"already_current"))

    def test_ecmwf_only_recovery_when_kma_pdf_is_fresh(self):
        self.assertFalse(needs_refresh(self.now,self.root)[0])
        self.assertEqual(needs_ecmwf_retry(self.now,self.root),
                         (True,"missing_model_output"))
        incomplete={
            "generated_at_kst":"2026-11-14T12:10:00+09:00",
            "cities_targeted":167,"districts_targeted":109,
            "models":{"IFS":{"cities_requested":167,"cities_succeeded":0}},
            "district_models":{"IFS":{"cities_requested":109,"cities_succeeded":0}}}
        path=self.directory/"ecmwf_regional.json"
        path.write_text(json.dumps(incomplete),encoding="utf8")
        self.assertEqual(needs_ecmwf_retry(self.now,self.root),
                         (True,"partial_ecmwf_coverage"))
        incomplete["last_attempt_at_kst"]="2026-11-14T12:18:00+09:00"
        path.write_text(json.dumps(incomplete),encoding="utf8")
        self.assertEqual(needs_ecmwf_retry(self.now,self.root),
                         (False,"recent_model_attempt"))
        self.assertEqual(needs_refresh(self.now,self.root),(False,"already_current"))

    def test_completed_model_targets_do_not_trigger_repeat(self):
        ready={"generated_at_kst":"2026-11-14T12:00:00+09:00",
               "cities_targeted":167,"districts_targeted":109}
        ready["models"]={name:{"status":"verified","cities_requested":167,
                               "cities_succeeded":167}
                         for name in ("IFS","AIFS Single")}
        ready["district_models"]={name:{"status":"verified",
                                        "cities_requested":109,"cities_succeeded":109}
                                  for name in ("IFS","AIFS Single")}
        (self.directory/"ecmwf_regional.json").write_text(
            json.dumps(ready),encoding="utf8")
        self.assertEqual(needs_ecmwf_retry(self.now,self.root),
                         (False,"all_models_complete"))

    def test_full_coverage_from_old_cache_still_requests_recovery(self):
        cached={"generated_at_kst":"2026-11-14T11:00:00+09:00",
                "last_attempt_at_kst":"2026-11-14T12:10:00+09:00",
                "fallback_recent_success":True,"cities_targeted":167,
                "districts_targeted":109}
        cached["models"]={name:{"status":"verified_cached_recent",
                                "cities_requested":167,"cities_succeeded":167}
                          for name in ("IFS","AIFS Single")}
        cached["district_models"]={name:{"status":"verified_cached_recent",
                                         "cities_requested":109,"cities_succeeded":109}
                                   for name in ("IFS","AIFS Single")}
        (self.directory/"ecmwf_regional.json").write_text(
            json.dumps(cached),encoding="utf8")
        self.assertEqual(needs_ecmwf_retry(self.now,self.root),
                         (True,"cached_model_needs_fresh_sample"))
        self.assertEqual(needs_refresh(self.now,self.root),
                         (False,"already_current"))

    def test_older_issue_must_refresh(self):
        self.forecast["issue_at_kst"]="2026-11-14T05:00:00+09:00"
        self.write()
        self.assertEqual(needs_refresh(self.now,self.root),(True,"new_kma_issue"))

    def test_missing_pdf_must_refresh(self):
        (self.directory/"rain_snow_latest.pdf").unlink()
        self.assertEqual(needs_refresh(self.now,self.root),(True,"missing_output"))

    def test_out_of_sync_pdf_must_refresh(self):
        self.manifest["source_generated_at_kst"]="2026-11-14T06:26:00+09:00"
        self.write()
        self.assertEqual(needs_refresh(self.now,self.root),(True,"pdf_out_of_sync"))

    def test_partial_coverage_must_refresh(self):
        self.forecast["locations_succeeded"]=173
        self.write()
        self.assertEqual(needs_refresh(self.now,self.root),(True,"partial_region_coverage"))

if __name__=="__main__":
    unittest.main()
