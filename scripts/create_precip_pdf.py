#!/usr/bin/env python3
"""Create a readable, embedded-Korean-font PDF from verified KMA rain/snow data.

The PDF intentionally includes only KMA forecast precipitation events, NOT dry
regions or 7-day district estimates that the KMA short-range API lacks.
"""
import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph,
                               Spacer, Table, TableStyle, KeepTogether)

from create_precip_report import rows as precipitation_rows

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "rain_snow_latest.pdf"
FONT_PATHS = (
    Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    Path("/usr/local/share/fonts/NanumGothic.ttf"),
)
FONT_BOLD_PATHS = (
    Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
    Path("/usr/local/share/fonts/NanumGothicBold.ttf"),
)
INK = colors.HexColor("#183247")
BLUE = colors.HexColor("#1476A8")
PALE = colors.HexColor("#EFF5F8")
GRAY = colors.HexColor("#536573")


def fonts():
    regular = next((p for p in FONT_PATHS if p.is_file()), None)
    bold = next((p for p in FONT_BOLD_PATHS if p.is_file()), None)
    if not regular:
        raise RuntimeError("Install fonts-nanum on the GitHub runner before rendering PDFs")
    pdfmetrics.registerFont(TTFont("Korean", str(regular)))
    pdfmetrics.registerFont(TTFont("KoreanBold", str(bold or regular)))
    pdfmetrics.registerFontFamily("Korean",normal="Korean",bold="KoreanBold")


def styles():
    return {
        "title": ParagraphStyle("TitleKo",fontName="KoreanBold",fontSize=17.5,
            leading=25,textColor=INK,spaceAfter=8),
        "subtitle": ParagraphStyle("SubtitleKo",fontName="Korean",fontSize=9,
            leading=14,textColor=GRAY,spaceAfter=3),
        "section": ParagraphStyle("SectionKo",fontName="KoreanBold",fontSize=11.2,
            leading=16,textColor=INK,spaceBefore=13,spaceAfter=6,keepWithNext=True),
        "note": ParagraphStyle("NoteKo",fontName="Korean",fontSize=8,
            leading=12,textColor=GRAY,spaceAfter=6),
        "table": ParagraphStyle("TableKo",fontName="Korean",fontSize=7.3,
            leading=10.2,textColor=INK,wordWrap="CJK"),
        "tableHead": ParagraphStyle("TableHeadKo",fontName="KoreanBold",fontSize=7.6,
            leading=11,textColor=colors.white,alignment=TA_LEFT),
        "body": ParagraphStyle("BodyKo",fontName="Korean",fontSize=8.4,
            leading=13,textColor=INK,spaceAfter=6),
    }


def header_footer(canvas, doc):
    canvas.saveState()
    width, height = A4
    canvas.setStrokeColor(colors.HexColor("#DCE6EA"))
    canvas.line(18*mm, 17*mm, width-18*mm,17*mm)
    canvas.setFont("Korean",7.7)
    canvas.setFillColor(GRAY)
    canvas.drawString(18*mm,12.2*mm,"기상청 공식 단기예보 | 구·시 대표 5km 격자")
    canvas.drawRightString(width-18*mm,12.2*mm,f"{doc.page}쪽")
    canvas.restoreState()


def section_table(records,st,available):
    def cell(value):
        safe=str(value).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        return Paragraph(safe,st["table"])
    columns=[
      Paragraph("지역",st["tableHead"]),Paragraph("형태",st["tableHead"]),
      Paragraph("총강수(mm)",st["tableHead"]),Paragraph("신적설(cm)",st["tableHead"]),
      Paragraph("최대 POP",st["tableHead"]),Paragraph("예보시간",st["tableHead"]),
    ]
    data=[columns]
    for r in records:
        time=r["from_kst"][11:16]+"-"+r["to_kst"][11:16]
        if r["period"]!="24시간":time += " (일부)"
        name=r["region"]+(" ·동일격자" if r["shared_grid"] else "")
        snow=r["snow"] if "눈" in r["forecast"] else "-"
        data.append([cell(name),cell(r["forecast"]),cell(r["precipitation"]),
                     cell(snow),cell(r["max_hourly_pop"]),cell(time)])
    table=Table(data,colWidths=[54*mm,20*mm,24*mm,24*mm,18*mm,34*mm],
                repeatRows=1,hAlign="LEFT",splitByRow=1)
    table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),INK),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,PALE]),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("LINEBELOW",(0,0),(-1,0),0.4,INK),
        ("BOTTOMPADDING",(0,0),(-1,0),7),
        ("TOPPADDING",(0,0),(-1,0),6),
        ("LEFTPADDING",(0,0),(-1,-1),5),
        ("RIGHTPADDING",(0,0),(-1,-1),4),
        ("TOPPADDING",(0,1),(-1,-1),6),
        ("BOTTOMPADDING",(0,1),(-1,-1),6),
    ]))
    return table


def create_pdf(data,output):
    fonts()
    st=styles()
    output.parent.mkdir(parents=True,exist_ok=True)
    doc=BaseDocTemplate(str(output),pagesize=A4,leftMargin=18*mm,rightMargin=18*mm,
       topMargin=19*mm,bottomMargin=24*mm,title="전국 7일 비·눈 예보 - 공식 단기 정량",
       author="KMA forecast collector")
    frame=Frame(doc.leftMargin,doc.bottomMargin,doc.width,doc.height,
                leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)
    doc.addPageTemplates(PageTemplate(id="normal",frames=frame,onPage=header_footer))
    events=precipitation_rows(data)
    groups=defaultdict(list)
    for row in events:
        groups[(row["date"],row["level"])].append(row)
    story=[
        Paragraph("전국 7일 비·눈 예보",st["title"]),
        Paragraph("비·눈이 실제 예보된 지역만 | 기상청 공식 단기예보 전국 구·시 대표 격자",st["subtitle"]),
        Paragraph("기상청 발표: "+data["issue_at_kst"]+"  ·  자동 수집: "+data["generated_at_kst"],st["subtitle"]),
        Spacer(1,7),
    ]
    stats=Table([
        [Paragraph("조회 지역",st["tableHead"]),Paragraph("고유 격자",st["tableHead"]),Paragraph("비·눈 지역/날짜",st["tableHead"])],
        [Paragraph(str(data["locations_succeeded"])+"/"+str(data["locations_checked"]),st["body"]),
         Paragraph(str(data.get("unique_grids_succeeded","-"))+"/"+str(data.get("unique_grids_checked","-")),st["body"]),
         Paragraph(str(len(events))+"건",st["body"])]
        ],colWidths=[54*mm,54*mm,66*mm],hAlign="LEFT")
    stats.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),BLUE),
                              ("BACKGROUND",(0,1),(-1,1),PALE),
                              ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                              ("LEFTPADDING",(0,0),(-1,-1),8),
                              ("TOPPADDING",(0,0),(-1,-1),7),
                              ("BOTTOMPADDING",(0,0),(-1,-1),7)]))
    story += [stats,Spacer(1,9),
      Paragraph("이 PDF는 전국을 조회하되 비·눈이 예보된 지역만 보여줍니다. 무강수 지역은 제외합니다. "
          "기상청 단기 API가 제공하지 않는 4~7일 후의 구별 강수량·신적설량은 채우지 않았습니다.",st["note"]),
      Paragraph("총강수량(PCP)은 비·눈을 포함하는 수분량(mm)이며 신적설(SNO)은 cm입니다. "
          "강수량이 범위로 발표되면 범위를 그대로 표기합니다. POP는 해당 기간 중 최대 시각별 강수확률입니다.",st["note"]),
      Paragraph("일부 시각만 수집된 날짜는 24시간 누적량이 아닙니다. 같은 격자의 구는 같은 값을 공유합니다.",st["note"])]
    if not events:
        story.append(Paragraph("이번 발표의 정량 단기예보 범위에서 비·눈 예상 지역이 확인되지 않았습니다.",st["body"]))
    else:
        for (date,level),group in sorted(groups.items(),key=lambda g:(g[0][0],g[0][1]!="gu")):
            tier="구(區)" if level=="gu" else "구가 없는 시(市)"
            story.append(Paragraph(date+" | "+tier+" | 비·눈 예상 "+str(len(group))+"지역",st["section"]))
            story.append(section_table(group,st,doc.width))
            story.append(Spacer(1,6))
    story += [Spacer(1,12),Paragraph("출처: 기상청 공공데이터포털 단기예보 API (getVilageFcst). "
          "기상청 중기 예보와 호우·대설 특보는 ChatGPT 예약 알림에서 최신 발표를 별도로 확인합니다.",st["note"])]
    doc.build(story)
    if output.stat().st_size < 1000 or output.read_bytes()[:5]!=b"%PDF-":
        raise RuntimeError("PDF generation validation failed")
    return len(events)


def main():
    data=json.loads((ROOT/"data/latest.json").read_text(encoding="utf-8"))
    count=create_pdf(data,OUTPUT)
    manifest={
        "pdf_file":"data/rain_snow_latest.pdf",
        "source":"KMA getVilageFcst",
        "source_issue_at_kst":data["issue_at_kst"],
        "source_generated_at_kst":data["generated_at_kst"],
        "pdf_generated_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
        "regions_checked":data["locations_checked"],
        "regions_succeeded":data["locations_succeeded"],
        "wet_region_date_rows":count,
        "pdf_size_bytes":OUTPUT.stat().st_size,
    }
    ROOT.joinpath("data/pdf_manifest.json").write_text(
        json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("PDF created, KMA forecast rain/snow region/date rows:",count,
          "file_size:",OUTPUT.stat().st_size)


if __name__=="__main__":
    main()
