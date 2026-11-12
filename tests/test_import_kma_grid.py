import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from import_kma_grid import convert, region_name

class KmaGridImportTests(unittest.TestCase):
    def test_convert_verified_grid_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            src=Path(directory)/"grid.csv"
            dst=Path(directory)/"locations.csv"
            src.write_text("1단계,2단계,3단계,격자 X,격자 Y\n"
                "서울특별시,강남구,역삼동,61,126\n"
                "서울특별시,강남구,삼성동,61,126\n"
                "경기도,수원시영통구,영통동,62,121\n"
                "전라남도,무안군,삼향읍,55,70\n",encoding="utf8")
            self.assertEqual(convert(src,dst),2)
            with dst.open(encoding="utf8",newline="") as stream:
                records=list(csv.DictReader(stream))
            self.assertEqual(len(records),2)
            self.assertIn("경기도 수원시 영통구",[r["name"] for r in records])
            self.assertNotIn("전라남도 무안군",[r["name"] for r in records])

    def test_invalid_mapping_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            src=Path(directory)/"invalid.csv"
            src.write_text("province,district,x,y\nSeoul,Jongno,60,127\n",encoding="utf8")
            with self.assertRaises(ValueError):
                convert(src,Path(directory)/"out.csv")

    def test_gun_not_gu(self):
        self.assertIsNone(region_name("강원특별자치도","평창군"))

if __name__=="__main__":
    unittest.main()
