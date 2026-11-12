import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from import_kma_grid import convert, region_name

class KmaGridImportTests(unittest.TestCase):
    def test_use_only_official_second_stage_grid(self):
        with tempfile.TemporaryDirectory() as directory:
            src=Path(directory)/"grid.csv"
            dst=Path(directory)/"locations.csv"
            src.write_text("1단계,2단계,3단계,격자 X,격자 Y\n"
                "서울특별시,강남구,,61,126\n"
                "서울특별시,강남구,역삼동,62,127\n"
                "서울특별시,강남구,삼성동,62,127\n"
                "경기도,수원시영통구,,62,121\n"
                "경기도,수원시영통구,영통동,63,121\n"
                "전라남도,무안군,,55,70\n"
                "세종특별자치시,,,66,103\n"
                "세종특별자치시,세종특별자치시,,66,103\n",encoding="utf8")
            self.assertEqual(convert(src,dst),3)
            with dst.open(encoding="utf8",newline="") as stream:
                records={r["name"]:r for r in csv.DictReader(stream)}
            self.assertEqual(len(records),3)
            self.assertEqual((records["서울특별시 강남구"]["nx"],records["서울특별시 강남구"]["ny"]),("61","126"))
            self.assertIn("경기도 수원시 영통구",records)
            self.assertIn("세종특별자치시",records)

    def test_conflicting_representatives_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            src=Path(directory)/"grid.csv"
            src.write_text("1단계,2단계,3단계,격자 X,격자 Y\n"
                           "서울특별시,강남구,,61,126\n"
                           "서울특별시,강남구,,62,126\n",encoding="utf8")
            with self.assertRaises(ValueError):
                convert(src,Path(directory)/"out.csv")

    def test_invalid_mapping_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            src=Path(directory)/"invalid.csv"
            src.write_text("province,district,x,y\nSeoul,Jongno,60,127\n",encoding="utf8")
            with self.assertRaises(ValueError):
                convert(src,Path(directory)/"out.csv")

    def test_gun_not_gu(self):
        self.assertIsNone(region_name("강원특별자치도","평창군"))

if __name__=="__main__":
    unittest.main()
