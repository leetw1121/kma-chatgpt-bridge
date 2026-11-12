import csv
import tempfile
import unittest
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from import_kma_grid import convert

class OfficialNationalGridTests(unittest.TestCase):
    def test_official_20260930_district_and_city_coverage(self):
        source=ROOT/"assets"/"kma_grid_mapping.csv"
        self.assertTrue(source.is_file(),"Missing official 2026-09-30 KMA grid mapping")
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/"locations.csv"
            self.assertEqual(convert(source,output),174)
            with output.open(encoding="utf-8",newline="") as stream:
                generated={tuple(sorted(r.items())) for r in csv.DictReader(stream)}
        with (ROOT/"locations.csv").open(encoding="utf-8",newline="") as stream:
            actual={tuple(sorted(r.items())) for r in csv.DictReader(stream)}
        self.assertEqual(actual,generated,"Output locations mismatch from official representative grids")
        parsed=[dict(row) for row in [dict(v) for v in generated]]
        self.assertEqual(sum(x["level"]=="gu" for x in parsed),109)
        self.assertEqual(sum(x["level"]=="city" for x in parsed),65)
        self.assertEqual(len({(x["nx"],x["ny"]) for x in parsed}),144)

if __name__=="__main__":
    unittest.main()
