import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from validate_generated_outputs import validate
from model_agreement_7day import agreement_by_date, markdown_report


class GeneratedOutputIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.data=self.root/"data"
        self.data.mkdir()
        shutil.copy2(ROOT/"locations.csv",self.root/"locations.csv")
        for name in (
            "latest.json","pdf_manifest.json","alert_status.json",
            "precipitation_only.csv","ecmwf_regional.json",
            "ecmwf_kma_coverage.csv","rain_snow_latest.pdf"):
            shutil.copy2(ROOT/"data"/name,self.data/name)
        kma=json.loads((self.data/"latest.json").read_text(encoding="utf-8"))
        alert_path=self.data/"alert_status.json"
        alert=json.loads(alert_path.read_text(encoding="utf-8"))
        alert.setdefault("status_generated_at_kst",alert["pdf_generated_at_kst"])
        alert_path.write_text(json.dumps(alert,ensure_ascii=False),encoding="utf-8")
        model=json.loads((self.data/"ecmwf_regional.json").read_text(encoding="utf-8"))
        (self.data/"model_agreement_7day.md").write_text(
            markdown_report(agreement_by_date(kma,model),kma),encoding="utf-8")
        manifest_path=self.data/"pdf_manifest.json"
        manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
        records=agreement_by_date(kma,model)
        manifest["model_agreement_dates"]=len(records)
        manifest["model_agreement_pairs_checked"]=sum(x["pairs_checked"] for x in records)
        manifest["model_agreement_pairs_partial"]=sum(x["pairs_partial"] for x in records)
        manifest_path.write_text(json.dumps(manifest,ensure_ascii=False),encoding="utf-8")

    def test_consistent_published_256_region_snapshot_passes(self):
        result=validate(self.root)
        self.assertEqual(result["kma_checked"],256)
        self.assertEqual(result["kma_success"],256)
        self.assertEqual(result["kma_wet_date_rows"],result["audit_date_rows"])

    def test_status_timestamp_before_pdf_prevents_publication(self):
        path=self.data/"alert_status.json"
        alert=json.loads(path.read_text(encoding="utf-8"))
        alert["status_generated_at_kst"]="2026-11-20T00:00:00+09:00"
        path.write_text(json.dumps(alert),encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"readiness was evaluated before the PDF"):
            validate(self.root)

    def test_status_timestamp_without_timezone_prevents_publication(self):
        path=self.data/"alert_status.json"
        alert=json.loads(path.read_text(encoding="utf-8"))
        alert["status_generated_at_kst"]="2026-11-20T00:00:00"
        path.write_text(json.dumps(alert),encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"alert readiness timestamps must include timezone"):
            validate(self.root)

    def test_edited_model_agreement_is_rejected_before_publication(self):
        path=self.data/"model_agreement_7day.md"
        path.write_text("검증되지 않은 높은 적중률 100%",encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"7-day model agreement report"):
            validate(self.root)

    def test_wrong_partial_day_model_pair_count_prevents_publication(self):
        path=self.data/"pdf_manifest.json"
        manifest=json.loads(path.read_text(encoding="utf-8"))
        manifest["model_agreement_pairs_partial"]+=1
        path.write_text(json.dumps(manifest),encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"PDF model agreement partial pairs"):
            validate(self.root)

    def test_wrong_pdf_model_pair_count_prevents_publication(self):
        path=self.data/"pdf_manifest.json"
        manifest=json.loads(path.read_text(encoding="utf-8"))
        manifest["model_agreement_pairs_checked"]+=1
        path.write_text(json.dumps(manifest),encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"PDF model agreement checked pairs"):
            validate(self.root)

    def test_missing_audit_region_prevents_publication(self):
        path=self.data/"ecmwf_kma_coverage.csv"
        lines=path.read_text(encoding="utf-8-sig").splitlines()
        path.write_text("\n".join(lines[:-1])+"\n",encoding="utf-8-sig")
        with self.assertRaisesRegex(ValueError,"per-region ECMWF audit"):
            validate(self.root)

    def test_mismatched_pdf_model_count_prevents_publication(self):
        path=self.data/"pdf_manifest.json"
        m=json.loads(path.read_text(encoding="utf-8"))
        m["ecmwf_unified_date_rows"]+=1
        path.write_text(json.dumps(m),encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"actual-model positive-row count"):
            validate(self.root)

    def test_invalid_pdf_bytes_prevent_publication(self):
        path=self.data/"rain_snow_latest.pdf"
        data=path.read_bytes()
        path.write_bytes(b"NOTPDF"+data[6:])
        with self.assertRaisesRegex(ValueError,"invalid PDF signature"):
            validate(self.root)

    def test_kma_region_or_label_tampering_prevents_publication(self):
        path=self.data/"latest.json"
        kma=json.loads(path.read_text(encoding="utf-8"))
        kma["locations"][0]["name"]="가짜 지역"
        path.write_text(json.dumps(kma,ensure_ascii=False),encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"unknown/unmapped"):
            validate(self.root)


if __name__=="__main__":
    unittest.main()
