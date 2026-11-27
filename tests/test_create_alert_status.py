import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from create_alert_status import compute_status,slot_for_issue
KST=ZoneInfo("Asia/Seoul")

class AlertReadyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pdf=Path(self.temp.name)/"forecast.pdf"
        self.pdf.write_bytes(b"%PDF-"+b"a"*3000)
        self.size=self.pdf.stat().st_size
        self.now=dt.datetime(2026,11,16,19,35,tzinfo=KST)
        self.forecast={
            "issue_at_kst":"2026-11-16T17:00:00+09:00",
            "generated_at_kst":"2026-11-16T18:14:23+09:00",
            "locations_checked":174,"locations_succeeded":174,
        }
        self.manifest={
            "scope":"city_only",
            "source_issue_at_kst":self.forecast["issue_at_kst"],
            "source_generated_at_kst":self.forecast["generated_at_kst"],
            "pdf_generated_at_utc":"2026-11-16T09:15:41+00:00",
            "pdf_size_bytes":self.size,
        }

    def test_actual_success_case_18_30_even_if_reminder_delayed(self):
        status=compute_status(self.forecast,self.manifest,self.pdf,self.now)
        self.assertTrue(status["pdf_ready"])
        self.assertEqual(status["ready_for_alert_slot_kst"],"2026-11-16T18:30:00+09:00")
        self.assertEqual(status["source_issue_at_kst"],"2026-11-16T17:00:00+09:00")
        self.assertEqual(status["status_generated_at_kst"],self.now.isoformat())
        self.assertGreaterEqual(
            dt.datetime.fromisoformat(status["status_generated_at_kst"]),
            dt.datetime.fromisoformat(status["pdf_generated_at_kst"]))

    def test_pdf_generated_after_alert_slot_must_not_claim_slot_ready(self):
        self.manifest["pdf_generated_at_utc"]="2026-11-16T09:45:41+00:00"
        status=compute_status(self.forecast,self.manifest,self.pdf,self.now)
        self.assertTrue(status["checks_passed"])
        self.assertFalse(status["pdf_ready"])
        self.assertTrue(status["late_pdf_for_alert_slot"])
        self.assertFalse(status["pdf_generated_before_alert_slot"])
        self.assertIsNone(status["ready_for_alert_slot_kst"])
        self.assertIn("pdf_generated_before_alert_slot",status["reason"])

    def test_pdf_prepared_before_slot_keeps_readiness_when_alert_runs_late(self):
        status=compute_status(self.forecast,self.manifest,self.pdf,self.now)
        self.assertTrue(status["pdf_ready"])
        self.assertFalse(status["late_pdf_for_alert_slot"])
        self.assertTrue(status["pdf_generated_before_alert_slot"])

    def test_valid_intermediate_issue_is_not_false_success_or_pipeline_failure(self):
        self.forecast["issue_at_kst"]="2026-11-16T14:00:00+09:00"
        self.manifest["source_issue_at_kst"]=self.forecast["issue_at_kst"]
        status=compute_status(self.forecast,self.manifest,self.pdf,self.now)
        self.assertTrue(status["checks_passed"])
        self.assertFalse(status["pdf_ready"])
        self.assertIsNone(status["ready_for_alert_slot_kst"])

    def test_previous_day_23h_issue_is_midnight_30_slot(self):
        slot,expect=slot_for_issue(dt.datetime(2026,11,16,23,tzinfo=KST))
        self.assertEqual(slot.isoformat(),"2026-11-17T00:30:00+09:00")
        self.assertEqual(expect,23)

    def test_old_pdf_manifest_is_rejected(self):
        self.manifest["source_generated_at_kst"]="2026-11-16T12:00:00+09:00"
        self.assertFalse(compute_status(self.forecast,self.manifest,self.pdf,self.now)["pdf_ready"])

    def test_partial_kma_is_rejected(self):
        self.forecast["locations_succeeded"]=173
        self.assertFalse(compute_status(self.forecast,self.manifest,self.pdf,self.now)["checks_passed"])

    def test_pdf_not_present_is_rejected(self):
        self.pdf.unlink()
        self.assertFalse(compute_status(self.forecast,self.manifest,self.pdf,self.now)["checks_passed"])

if __name__=="__main__":
    unittest.main()
