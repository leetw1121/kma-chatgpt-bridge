import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from check_freshness import needs_refresh
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
