#!/usr/bin/env python3
"""City-level KMA rain/snow PDF, optional VERIFIED 7-day ECMWF appendix only."""
import datetime as dt
import json
from collections import defaultdict
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph,
                               Spacer, Table, TableStyle, PageBreak)

from city_forecast import group_rows, ROOT
from create_precip_report import rows as precipitation_rows
from ecmwf_report_gate import combined_model_rows

OUTPUT=ROOT/"data/rain_snow_latest.pdf"
INK=colors.HexColor("#143147")
MID=colors.HexColor("#247BA4")
PALE=colors.HexColor("#EFF5F8")
SECONDARY=colors.HexColor("#50677B")

def fonts():
    regular=Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf")
    bold=Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf")
    if not regular.is_file():raise RuntimeError("fonts-nanum package is required")
    pdfmetrics.registerFont(TTFont("Ko",str(regular)))
    pdfmetrics.registerFont(TTFont("KoBold",str(bold if bold.exists() else regular)))
    pdfmetrics.registerFontFamily("Ko",normal="Ko",bold="KoBold")

def styles():
    return {
        "title":ParagraphStyle("title",fontName="KoBold",fontSize=17.5,leading=24,textColor=INK,spaceAfter=7),
        "sub":ParagraphStyle("sub",fontName="Ko",fontSize=8.5,leading=13,textColor=SECONDARY,spaceAfter=4),
        "sec":ParagraphStyle("sec",fontName="KoBold",fontSize=11.6,leading=16,textColor=INK,spaceBefore=13,spaceAfter=6,keepWithNext=True),
        "small":ParagraphStyle("small",fontName="Ko",fontSize=7.7,leading=11.5,textColor=SECONDARY,spaceAfter=5),
        "th":ParagraphStyle("th",fontName="KoBold",fontSize=7.8,leading=10.8,textColor=colors.white),
        "td":ParagraphStyle("td",fontName="Ko",fontSize=7.6,leading=11,wordWrap="CJK",textColor=INK),
        "note":ParagraphStyle("note",fontName="Ko",fontSize=8.5,leading=13,textColor=INK,spaceAfter=5),
    }

def P(s,style):
    return Paragraph(escape(str(s)),style)

def page(canvas,doc):
    canvas.saveState()
    w,h=A4
    canvas.setStrokeColor(colors.HexColor("#DCE8EE"))
    canvas.line(18*mm,17*mm,w-18*mm,17*mm)
    canvas.setFillColor(SECONDARY)
    canvas.setFont("Ko",7.5)
    canvas.drawString(18*mm,12.5*mm,
        "기상청 단기예보 + ECMWF 모델 참고 | 시·구·군 단위"
        if getattr(doc,"has_ecmwf_appendix",False)
        else "기상청 공식 단기예보 | 시·구·군 단위")
    canvas.drawRightString(w-18*mm,12.5*mm,str(doc.page)+"쪽")
    canvas.restoreState()

def table(headers,values,widths,st):
    data=[[P(x,st["th"]) for x in headers]]
    data.extend([[P(item,st["td"]) for item in record] for record in values])
    t=Table(data,colWidths=[x*mm for x in widths],hAlign="LEFT",repeatRows=1,splitByRow=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),INK),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,PALE]),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("LEFTPADDING",(0,0),(-1,-1),5),
        ("RIGHTPADDING",(0,0),(-1,-1),4),
        ("TOPPADDING",(0,0),(-1,-1),6),
        ("BOTTOMPADDING",(0,0),(-1,-1),6),
        ("LINEBELOW",(0,0),(-1,0),0.4,MID),
    ]))
    return t

def kma_unified_tables(data,st,records=None):
    """One date-sorted KMA view: gu, city and county; PCP and SNO together."""
    records=precipitation_rows(data) if records is None else records
    if not records:
        return [P("이번 단기예보에서 비·눈 예상 시·구·군이 확인되지 않았습니다.",st["note"])]
    by_date=defaultdict(list)
    for r in records:
        by_date[r["date"]].append(r)
    story=[P("기상청 시·구·군별 비·눈 통합",st["title"]),
           P("기상청 공식 구·시·군 대표 격자. 신적설량이 있는 곳은 적설량 많은 순, 적설 0 또는 미제공 지역은 강수량 많은 순입니다.",st["small"])]
    for date,day_rows in sorted(by_date.items()):
        story.append(P(date+" | 시·구·군 통합 비·눈 "+str(len(day_rows))+"건",st["sec"]))
        values=[]
        for r in day_rows:
            period=r["from_kst"][11:16]+"-"+r["to_kst"][11:16]
            if r["period"]!="24시간":
                period+=" 일부"
            level={"gu":"구","city":"시","gun":"군"}.get(r["level"],"?")
            region=r["region"]+(" (동일격자)" if r["shared_grid"] else "")
            values.append([region,level,r["forecast"],r["precipitation"],
                           r["snow"],r["max_hourly_pop"],period])
        story.append(table(
            ["지역","구분","형태","총강수 mm","신적설 cm","최대 POP","예보시간"],
            values,[48,12,22,30,25,16,21],st))
        story.append(Spacer(1,5))
    return story


def model_section(kma,model,st):
    """One ECMWF table per date with city/county AND gu in the same ranking."""
    rows,has_city,has_gu=combined_model_rows(kma,model)
    stats={"rows":len(rows),"city":bool(has_city),"district":bool(has_gu)}
    if not rows:
        return [],stats
    story=[
      PageBreak(),P("7일 비·눈 참고: ECMWF IFS · AIFS",st["title"]),
      P("전국 시·구·군 통합 참고. IFS와 AIFS 각각의 총강수량·신적설을 같은 지역 행에 표시합니다.",st["small"]),
      P("대상: 기존 시·군 "+str(model.get("cities_targeted",0))+
        "개 + 구 "+str(model.get("districts_targeted",0))+
        "개 참고 지점. 실제 조회 성공한 모델값만 사용하며 구가 없는 곳과 구별 지점은 서로 다른 참고 좌표입니다.",st["small"]),
      P("정렬: IFS/AIFS에서 적설량이 있는 지역을 우선해 많은 순, 적설 0 또는 미제공이면 총강수량 많은 순. "
        "모델 수치 합산·평균이 아니고 실제 많은 순위를 확정하는 비교도 아닙니다.",st["small"]),
      P("모델 수집: "+str(model.get("generated_at_kst","미확인"))+
        (" | 최근 3시간 이내 성공본 사용" if model.get("fallback_recent_success") else " | 신규 수집")+
        " | 24시간보다 짧은 기간은 '일부' 표시",st["small"])]
    by_date=defaultdict(list)
    for row in rows:
        by_date[row["date"]].append(row)
    def value(series,name,field):
        day=series.get(name)
        if not isinstance(day,dict):
            return "미제공"
        number=day.get(field)
        if (field=="snowfall_cm" and not day.get("snowfall_valid")) or (
            not isinstance(number,(int,float)) or isinstance(number,bool) or number<0):
            return "미제공"
        amount=f"{number:g}"
        if day.get("forecast_hours",0)<24:
            amount+=" 일부"
        return amount
    for date,selected in sorted(by_date.items()):
        story.append(P(date+" | ECMWF 시·구·군 비·눈 "+str(len(selected))+"곳",st["sec"]))
        values=[]
        for row in selected:
            series=row["series"]
            scope="구" if row["scope"]=="gu" else "시·군"
            values.append([
                row["region"],scope,
                value(series,"IFS","precip_mm"),value(series,"IFS","snowfall_cm"),
                value(series,"AIFS Single","precip_mm"),value(series,"AIFS Single","snowfall_cm")])
        story.append(table(
            ["지역","구분","IFS 강수 mm","IFS 눈 cm","AIFS 강수 mm","AIFS 눈 cm"],
            values,[58,16,29,22,29,20],st))
        story.append(Spacer(1,5))
    story.append(P("Open-Meteo.com 제공 ECMWF 모델 참고 (CC BY 4.0). 총강수량은 비·눈을 합한 물의 양(mm)이며 "
        "신적설(cm)과 단위가 다릅니다. 기상청 공식 격자값·행정구역 전체 평균과 같지 않습니다. "
        "실제 제공되지 않은 수치는 0이 아닌 '미제공'으로 표시합니다.",st["small"]))
    return story,stats

def create(kma,model,filename):
    fonts()
    st=styles()
    filename.parent.mkdir(exist_ok=True,parents=True)
    doc=BaseDocTemplate(str(filename),pagesize=A4,leftMargin=18*mm,rightMargin=18*mm,
        topMargin=19*mm,bottomMargin=24*mm,title="전국 시·구·군 단위 비·눈 예보",author="KMA-ECMWF Bridge")
    frame=Frame(doc.leftMargin,doc.bottomMargin,doc.width,doc.height,
                leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)
    doc.addPageTemplates(PageTemplate(id="normal",frames=frame,onPage=page))
    story=[
        P("전국 시·구·군 단위 비·눈 예보",st["title"]),
        P("시·구·군 통합 · 지역마다 총강수량과 신적설량 함께 표시",st["sub"]),
        P("기상청 발표: "+kma["issue_at_kst"]+" | 수집: "+kma["generated_at_kst"],st["sub"]),
        P("원본 기상청 격자 조회 "+str(kma["locations_succeeded"])+"/"+
           str(kma["locations_checked"])+"개 구·시·군 (고유 격자 "+
           str(kma.get("unique_grids_succeeded","-"))+"개). 시·구·군은 날짜별 하나의 표에서 함께 표시합니다.",st["note"]),
        P("기상청 각 구·시·군의 공식 대표 격자 예보만 표시합니다. 같은 격자를 공유하는 지역은 같은 값을 가지며, "
          "시 전체의 평균·최대값이나 지역별 합산값이 아닙니다.",st["small"]),
        P("신적설(cm)이 0보다 큰 지역을 우선해 적설량 상한 큰 순으로 정렬합니다. 적설량이 0 또는 미제공이면 총강수량(mm) 상한 큰 순으로 정렬합니다. "
          "상한이 없는 '이상' 범위는 확인된 하한으로 정렬하며, 범위가 겹치면 우열은 확정되지 않습니다. "
          "총강수량은 눈을 포함하는 수분량으로 신적설(cm)과 단위가 다릅니다.",st["small"]),
        P("기상청 공식 단기 API에서 실제 제공하는 날짜만 수록합니다. 7일 전체의 시·구·군별 정량값을 "
          "생성하지 않습니다. 비·눈이 예보되지 않은 지역은 표에서 제외합니다.",st["small"]),
    ]
    kma_records=precipitation_rows(kma)
    story.extend(kma_unified_tables(kma,st,records=kma_records))
    city_records=group_rows(kma)  # legacy manifest metrics only
    model_stats={"rows":0,"city":False,"district":False}
    if model is not None:
        appendix,model_stats=model_section(kma,model,st)
        story.extend(appendix)
    verified=model_stats["city"] or model_stats["district"]
    doc.has_ecmwf_appendix=verified
    doc.build(story)
    if filename.stat().st_size<1000 or filename.read_bytes()[:5]!=b"%PDF-":
        raise RuntimeError("Invalid PDF header or empty output")
    return {"kma_city_date_rows":len(city_records),
            "kma_city_names":len({x["city"] for x in city_records}),
            "kma_gu_date_rows":sum(r["level"]=="gu" for r in kma_records),
            "kma_unified_date_rows":len(kma_records),
            "ecmwf_unified_date_rows":model_stats["rows"],
            "pdf_layout_version":"unified_kma_ecmwf_v1",
            "ecmwf_gu_models_included":model_stats["district"],
            "ecmwf_models_included":bool(verified),
            "pdf_size_bytes":filename.stat().st_size}

def main():
    data=json.loads((ROOT/"data/latest.json").read_text(encoding="utf-8"))
    model_file=ROOT/"data/ecmwf_regional.json"
    model=json.loads(model_file.read_text(encoding="utf-8")) if model_file.exists() else None
    stats=create(data,model,OUTPUT)
    manifest={
        "pdf_file":"data/rain_snow_latest.pdf","scope":"city_only",
        "coverage_scope":"city_district_county",
        "scope_compatibility_note":"legacy city_only retained for deployed Cloudflare and ChatGPT validators",
        "county_regions_succeeded":sum(p["level"]=="gun" for p in data.get("locations",[])),
        "source":("KMA getVilageFcst + verified ECMWF IFS/AIFS"
                  if stats["ecmwf_models_included"] else "KMA getVilageFcst only"),
        "source_issue_at_kst":data["issue_at_kst"],
        "source_generated_at_kst":data["generated_at_kst"],
        "model_generated_at_kst":(
            model.get("generated_at_kst") if model and stats["ecmwf_models_included"] else None),
        "pdf_generated_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
        "regions_checked":data["locations_checked"],
        "regions_succeeded":data["locations_succeeded"],
        **stats,
    }
    (ROOT/"data/pdf_manifest.json").write_text(
         json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("City-level PDF published:",stats)

if __name__=="__main__":
    main()
