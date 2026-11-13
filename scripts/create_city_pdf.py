#!/usr/bin/env python3
"""City-level precipitation PDF: ranked KMA days plus real 7-day IFS/AIFS city samples."""
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

from city_forecast import group_rows, rank, fmt, ROOT

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
    canvas.drawString(18*mm,12.5*mm,"기상청 공식 예보 + ECMWF 모델 참고 | 시 단위")
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

def kma_city_tables(data,st):
    records=group_rows(data)
    dates=sorted({r["date"] for r in records})
    story=[]
    for date in dates:
        subset=[r for r in records if r["date"]==date]
        for kind,label,metric,unit in (("rain","비 · 총강수량","precip","mm"),
                                        ("snow","눈 · 신적설","snow_cm","cm")):
            ranked=rank(subset,kind)
            if not ranked:continue
            story.append(P(date+" | "+label+" | "+str(len(ranked))+"개 도시",st["sec"]))
            rows=[]
            for i,r in enumerate(ranked,1):
                pop=str(r["max_pop"])+"%" if r["max_pop"] is not None else "미발표"
                period=r["from_kst"][11:16]+"-"+r["to_kst"][11:16]+("" if r["full_day"] else " 일부")
                method="구 격자 "+str(r["grid_count"])+"곳 중 최대" if r["gu_count"] else "시 대표 격자"
                rows.append([str(i),r["city"],fmt(r[metric],unit),pop,period+" / "+method])
            story.append(table(["참고","시","예보량","최대 POP","기간 / 근거"],rows,[12,54,32,22,54],st))
            story.append(Spacer(1,4))
    if not dates:story.append(P("정량 단기예보에서 비·눈 예상 도시가 없습니다.",st["note"]))
    return story,records

def verified_model_rows(kma,model):
    """Only include model series actually fetched with fresh, compatible timestamps."""
    try:
        kt=dt.datetime.fromisoformat(kma["generated_at_kst"])
        mt=dt.datetime.fromisoformat(model["generated_at_kst"])
        if abs((mt-kt).total_seconds())>10800:return {},False
    except (KeyError,ValueError,TypeError):
        return {},False
    out=defaultdict(dict)
    good=False
    for model_name,record in (model.get("models") or {}).items():
        if record.get("status")!="verified" or not record.get("cities_succeeded"):continue
        good=True
        for item in record.get("records",[]):
            for date,day in item.get("days",{}).items():
                if day.get("forecast_hours",0)>0:
                    out[(date,item["city"])][model_name]=day
    return out,good

def model_section(kma,model,st):
    lookup,good=verified_model_rows(kma,model or {})
    story=[PageBreak(),P("7일 비·눈 참고: ECMWF IFS · AIFS",st["title"]),
      P("기상청 단기예보 이후의 전망을 보완하는 별도 모델입니다. Open-Meteo가 제공한 실제 ECMWF 모델 수치를 사용합니다.",st["small"]),
      P("모델 지점은 한국 7개 도시 중심의 참고 좌표입니다. 기상청 구·시 대표 격자와 일치하지 않으며 174개 시의 예보가 아닙니다.",st["small"]),
      P("모델 수집: "+str((model or {}).get("generated_at_kst","조회 실패"))+" | 1일 미만 시간대는 일부 기간 누적량입니다.",st["small"])]
    if not good:
        story.append(P("실제 최신 수치를 확인한 ECMWF 모델이 없으므로 비교표를 싣지 않았습니다.",st["note"]))
        return story,False
    dates=sorted({day for (day,_) in lookup})
    for date in dates:
        todays=[(city,series) for (d,city),series in lookup.items() if d==date]
        selected=[(city,series) for city,series in todays
                  if any((v.get("precip_mm") or 0)>0 or (v.get("snowfall_cm") or 0)>0
                         for v in series.values())]
        if not selected:continue
        def amount(series,which):
            v=series.get(which)
            return (v.get("precip_mm") or 0) if v else 0
        selected.sort(key=lambda x:(-max(amount(x[1],"IFS"),amount(x[1],"AIFS Single")),x[0]))
        rows=[]
        for city,series in selected:
            def value(model_name,field):
                if model_name not in series:return "미제공"
                d=series[model_name]
                v=d.get(field)
                if field=="snowfall_cm" and not d.get("snowfall_valid"):return "미제공"
                if v is None:return "미제공"
                amount=f"{v:g}" if isinstance(v,(int,float)) else str(v)
                if d.get("forecast_hours",0)<24:amount+=" ·일부"
                return amount
            rows.append([city,value("IFS","precip_mm"),value("AIFS Single","precip_mm"),
                         value("IFS","snowfall_cm"),value("AIFS Single","snowfall_cm")])
        story.append(P(date+" | ECMWF 두 모델의 총강수량 내림차순",st["sec"]))
        story.append(table(["도시","IFS 강수 mm","AIFS 강수 mm","IFS 눈 cm","AIFS 눈 cm"],
                           rows,[50,34,34,28,28],st))
        story.append(Spacer(1,4))
    story.append(P("ECMWF 총강수량은 눈을 포함하는 수분량으로 비 강수만을 뜻하지 않습니다. 강수량은 기상청과 상호 대체하거나 평균하지 않습니다.",st["small"]))
    return story,True

def create(kma,model,filename):
    fonts()
    st=styles()
    filename.parent.mkdir(exist_ok=True,parents=True)
    doc=BaseDocTemplate(str(filename),pagesize=A4,leftMargin=18*mm,rightMargin=18*mm,
        topMargin=19*mm,bottomMargin=24*mm,title="전국 시 단위 7일 비 눈 예보",author="KMA-ECMWF Bridge")
    frame=Frame(doc.leftMargin,doc.bottomMargin,doc.width,doc.height,
                leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)
    doc.addPageTemplates(PageTemplate(id="normal",frames=frame,onPage=page))
    story=[
        P("전국 시 단위 7일 비·눈 예보",st["title"]),
        P("날짜별 비·눈이 예보된 도시만 · 강수량/신적설량 많은 순",st["sub"]),
        P("기상청 발표: "+kma["issue_at_kst"]+" | 수집: "+kma["generated_at_kst"],st["sub"]),
        P("원본 기상청 격자 조회 "+str(kma["locations_succeeded"])+"/"+
           str(kma["locations_checked"])+"개 구·시 (고유 격자 "+
           str(kma.get("unique_grids_succeeded","-"))+"개). 시별로 통합해 중복 구를 생략합니다.",st["note"]),
        P("주의: 구가 있는 도시는 구 대표 격자 예보 중 최대 범위를 도시의 참고값으로 사용합니다. "
          "강수량을 더하거나 시 전체의 실제 최대 강수량으로 주장하지 않습니다.",st["small"]),
        P("예보량 **상한값이 큰 순의 참고 정렬**입니다. 범위가 겹치면 우열은 확정되지 않습니다. "
          "총강수량(mm)은 눈을 포함하며, 신적설(cm)은 별도로 표시합니다.",st["small"]),
        P("기상청 공식 단기 API의 제공 기간 이후는 지역별 mm/cm 미제공입니다. "
          "기상청 공식 7일 중기예보는 예약 알림에서 권역별로 따로 알려드립니다.",st["small"]),
    ]
    kma_story,records=kma_city_tables(kma,st)
    story.extend(kma_story)
    if model is not None:
        append,verified=model_section(kma,model,st)
        story.extend(append)
    else:verified=False
    doc.build(story)
    if filename.stat().st_size<1000 or filename.read_bytes()[:5]!=b"%PDF-":
        raise RuntimeError("Invalid PDF header or empty output")
    return {"kma_city_date_rows":len(records),
            "kma_city_names":len({x["city"] for x in records}),
            "ecmwf_models_included":bool(verified),
            "pdf_size_bytes":filename.stat().st_size}

def main():
    data=json.loads((ROOT/"data/latest.json").read_text(encoding="utf-8"))
    model_file=ROOT/"data/ecmwf_regional.json"
    model=json.loads(model_file.read_text(encoding="utf-8")) if model_file.exists() else None
    stats=create(data,model,OUTPUT)
    manifest={
        "pdf_file":"data/rain_snow_latest.pdf","scope":"city_only",
        "source":"KMA getVilageFcst, optional independently verified ECMWF IFS/AIFS",
        "source_issue_at_kst":data["issue_at_kst"],
        "source_generated_at_kst":data["generated_at_kst"],
        "model_generated_at_kst":model.get("generated_at_kst") if model else None,
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
