#!/usr/bin/env python3
"""Build gu/city representative grids using official second-stage rows of KMA CSV/XLSX.

Source: KMA short-range forecast API user guide, grid/latlon appendix.
This is an opt-in offline import, not scraping or an assumed set of coordinates.
"""
import argparse
import csv
import re
from pathlib import Path

def norm(value):
    return re.sub(r"\s+", "", str(value or "")).lower().replace("(", "").replace(")", "")

def columns(head):
    h=[norm(v) for v in head]
    aliases=[
        ["1단계","시도","시도명","시도명칭"],
        ["2단계","시군구","시군구명","시군구명칭"],
        ["3단계","읍면동","동리","동명"],
        ["격자x","nx","gridx","격자x좌표"],
        ["격자y","ny","gridy","격자y좌표"],
    ]
    found=[]
    for variants in aliases:
        col=next((i for i,v in enumerate(h) if v in variants),None)
        if col is None: raise ValueError("MISSING_KMA_GRID_COLUMNS")
        found.append(col)
    return found

def read_records(path):
    if path.suffix.lower()==".csv":
        for enc in ("utf-8-sig","cp949","euc-kr"):
            try:
                with path.open(encoding=enc,newline="") as stream:
                    rows=list(csv.reader(stream))
                break
            except UnicodeError:
                continue
        else: raise ValueError("INVALID_CSV_ENCODING")
    elif path.suffix.lower()==".xlsx":
        try:
            import openpyxl
        except ImportError as err:
            raise RuntimeError("XLSX_REQUIRES_OPENPYXL") from err
        wb=openpyxl.load_workbook(path,read_only=True,data_only=True)
        rows=[list(row) for sheet in wb.worksheets for row in sheet.iter_rows(values_only=True)]
        wb.close()
    else: raise ValueError("NOT_CSV_OR_XLSX")
    for idx,head in enumerate(rows[:30]):
        try:
            positions=columns(head)
            break
        except ValueError:
            pass
    else: raise ValueError("MISSING_KMA_GRID_HEADER")
    for row in rows[idx+1:]:
        if len(row)>max(positions):
            yield ([str(v or "").strip() for v in row],positions)

def region_name(province,district):
    province=str(province or "").strip()
    district=str(district or "").strip()
    if not province or province=="이어도": return None
    if province=="세종특별자치시" and (not district or district==province):
        return province,"city"
    if not district or district.endswith("군"): return None
    if district.endswith("구"):
        match=re.match(r"^(.*?시)\s*(.+구)$",district)
        if match: return province+" "+match[1]+" "+match[2],"gu"
        return province+" "+district,"gu"
    if district.endswith("시"):
        return province+" "+district,"city"
    return None

def convert(source,destination):
    groups={}
    for row,positions in read_records(source):
        a,b,third,x,y=positions
        # KMA already publishes one official representative grid for each second-stage unit.
        # All subordinate dong/eup/myeon rows must be ignored, NOT counted as votes.
        if row[third].strip(): continue
        label=region_name(row[a],row[b])
        if not label: continue
        try: coords=(int(float(row[x])),int(float(row[y])))
        except ValueError: continue
        if not (1<=coords[0]<=149 and 1<=coords[1]<=253): continue
        old=groups.get(label)
        if old is not None and old!=coords:
            raise ValueError("CONFLICTING_OFFICIAL_GRID_FOR_"+label[0])
        groups[label]=coords
    if not groups: raise ValueError("NO_VALID_KMA_GRID")
    destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open("w",encoding="utf-8",newline="") as stream:
        writer=csv.writer(stream)
        writer.writerow(("name","level","nx","ny"))
        for (name,level),grid in sorted(groups.items()):
            writer.writerow((name,level,*grid))
    return len(groups)

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("kma_grid_mapping",type=Path,help="Official KMA appendix CSV or XLSX")
    parser.add_argument("--out",type=Path,default=Path(__file__).resolve().parents[1]/"locations.csv")
    args=parser.parse_args()
    print("representative_locations=",convert(args.kma_grid_mapping,args.out))
