import datetime as dt
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from check_freshness import needs_refresh, needs_ecmwf_retry, decide_run
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

    def add_valid_snapshot_locations(self):
        self.forecast["locations"]=[{"name":"지역"+str(i),"level":"gu",
                                      "days":{}} for i in range(174)]
        self.write()

    def add_current_report_outputs(self):
        self.add_valid_snapshot_locations()
        pdf=self.directory/"rain_snow_latest.pdf"
        self.manifest.update({
            "scope":"city_only","coverage_scope":"city_district_county",
            "pdf_layout_version":"unified_kma_ecmwf_v1_coverage_audit",
            "source_generated_at_kst":self.forecast["generated_at_kst"],
            "source_issue_at_kst":self.forecast["issue_at_kst"],
            "regions_checked":self.forecast["locations_checked"],
            "regions_succeeded":self.forecast["locations_succeeded"],
            "pdf_size_bytes":pdf.stat().st_size,
            "pdf_generated_at_utc":"2026-11-14T03:15:00+00:00",
            "kma_unified_date_rows":1,"ecmwf_kma_coverage_rows":1,
        })
        self.write()
        for name in ("precipitation_only.csv","city_precipitation.csv",
                     "ecmwf_kma_coverage.csv","ecmwf_kma_coverage.md",
                     "model_agreement_7day.md"):
            (self.directory/name).write_text("valid\n",encoding="utf-8")
        (self.directory/"alert_status.json").write_text(json.dumps({
            "status_generated_at_kst":"2026-11-14T12:19:00+09:00",
            "source_generated_at_kst":self.forecast["generated_at_kst"],
            "pdf_generated_at_kst":"2026-11-14T12:15:00+09:00",
            "pdf_size_bytes":pdf.stat().st_size,
        }),encoding="utf-8")

    def test_explicit_recovery_marker_allows_one_push_refresh(self):
        self.add_valid_snapshot_locations()
        with patch("check_freshness.os.getenv",
                   side_effect=lambda name,default=None:
                       "true" if name=="KMA_ONE_SHOT_RECOVERY" else default):
            plan=decide_run(self.now,"push",self.root)
        self.assertTrue(plan["needed"])
        self.assertFalse(plan["render_only"])
        self.assertEqual(plan["reason"],"explicit_one_shot_kma_recovery")

    def test_push_on_current_issue_rebuilds_report_without_any_provider_calls(self):
        self.add_valid_snapshot_locations()
        plan=decide_run(self.now,"push",self.root)
        self.assertFalse(plan["needed"])
        self.assertTrue(plan["render_only"])
        self.assertFalse(plan["ecmwf_fetch"])
        self.assertFalse(plan["ecmwf_retry"])

    def test_push_with_newer_kma_issue_does_not_fetch_or_regenerate_current_pdf(self):
        self.forecast["issue_at_kst"]="2026-11-14T05:00:00+09:00"
        self.manifest["source_issue_at_kst"]=self.forecast["issue_at_kst"]
        self.add_current_report_outputs()
        self.assertTrue(needs_refresh(self.now,self.root)[0])
        plan=decide_run(self.now,"push",self.root)
        self.assertFalse(plan["needed"])
        self.assertFalse(plan["render_only"])
        self.assertFalse(plan["status_only"])
        self.assertFalse(plan["ecmwf_fetch"])
        self.assertEqual(plan["reason"],"code_change_no_data_rebuild")

    def test_current_code_push_only_refreshes_status_json_not_pdf(self):
        self.add_current_report_outputs()
        alert_path=self.directory/"alert_status.json"
        alert=json.loads(alert_path.read_text(encoding="utf-8"))
        alert.pop("status_generated_at_kst")
        alert_path.write_text(json.dumps(alert),encoding="utf-8")
        plan=decide_run(self.now,"push",self.root)
        self.assertFalse(plan["needed"])
        self.assertFalse(plan["render_only"])
        self.assertTrue(plan["status_only"])
        self.assertFalse(plan["ecmwf_fetch"])
        self.assertTrue(plan["reason"].startswith("code_change_status_only"))

    def test_pdf_timestamp_drift_repairs_status_only_without_rebuilding_pdf(self):
        self.add_current_report_outputs()
        alert_path=self.directory/"alert_status.json"
        alert=json.loads(alert_path.read_text(encoding="utf-8"))
        alert["pdf_generated_at_kst"]="2026-11-14T12:05:00+09:00"
        alert_path.write_text(json.dumps(alert),encoding="utf-8")
        plan=decide_run(self.now,"push",self.root)
        self.assertFalse(plan["needed"])
        self.assertFalse(plan["render_only"])
        self.assertTrue(plan["status_only"])
        self.assertFalse(plan["ecmwf_fetch"])

    def test_code_push_rebuilds_only_when_pdf_layout_version_changes(self):
        self.add_current_report_outputs()
        self.manifest["pdf_layout_version"]="old_layout"
        self.write()
        plan=decide_run(self.now,"push",self.root)
        self.assertFalse(plan["needed"])
        self.assertTrue(plan["render_only"])
        self.assertFalse(plan["status_only"])
        self.assertFalse(plan["ecmwf_fetch"])

    def test_invalid_kma_snapshot_cannot_be_used_for_offline_report(self):
        self.forecast["locations"]=[{"name":"중복","level":"gu","days":{}}]*174
        self.write()
        plan=decide_run(self.now,"push",self.root)
        self.assertFalse(plan["render_only"])
        self.assertNotEqual(plan["reason"],"code_change_report_only")

    def test_schedule_with_missing_model_only_retries_ecmwf(self):
        plan=decide_run(self.now,"schedule",self.root)
        self.assertFalse(plan["needed"])
        self.assertFalse(plan["render_only"])
        self.assertTrue(plan["ecmwf_retry"])
        self.assertTrue(plan["ecmwf_fetch"])

    def test_manual_preserves_kma_refresh_but_honors_model_cooldown(self):
        result={"generated_at_kst":"2026-11-14T12:14:00+09:00",
                "rate_limited":True,
                "last_attempt_at_kst":"2026-11-14T12:14:00+09:00",
                "cities_targeted":167,"districts_targeted":109,
                "models":{},"district_models":{}}
        (self.directory/"ecmwf_regional.json").write_text(
            json.dumps(result),encoding="utf8")
        plan=decide_run(self.now,"workflow_dispatch",self.root)
        self.assertTrue(plan["needed"])
        self.assertFalse(plan["ecmwf_fetch"])
        self.assertEqual(plan["ecmwf_reason"],"provider_429_cooldown")

    def test_new_kma_issue_still_refreshes_while_429_blocks_model(self):
        self.forecast["issue_at_kst"]="2026-11-14T05:00:00+09:00"
        self.write()
        result={"generated_at_kst":"2026-11-14T12:15:00+09:00",
                "rate_limited":True,
                "last_attempt_at_kst":"2026-11-14T12:15:00+09:00",
                "cities_targeted":167,"districts_targeted":109,
                "models":{},"district_models":{}}
        (self.directory/"ecmwf_regional.json").write_text(
            json.dumps(result),encoding="utf8")
        plan=decide_run(self.now,"schedule",self.root)
        self.assertTrue(plan["needed"])
        self.assertFalse(plan["ecmwf_fetch"])

    def test_complete_but_older_than_three_hours_is_not_current_model(self):
        valid={"generated_at_kst":"2026-11-14T08:00:00+09:00",
               "last_attempt_at_kst":"2026-11-14T08:00:00+09:00",
               "cities_targeted":167,"districts_targeted":109,
               "models":{k:{"cities_requested":167,"cities_succeeded":167}
                         for k in ("IFS","AIFS Single")},
               "district_models":{k:{"cities_requested":109,"cities_succeeded":109}
                                  for k in ("IFS","AIFS Single")}}
        (self.directory/"ecmwf_regional.json").write_text(
            json.dumps(valid),encoding="utf8")
        self.assertEqual(needs_ecmwf_retry(self.now,self.root),
                         (True,"model_data_stale"))

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
