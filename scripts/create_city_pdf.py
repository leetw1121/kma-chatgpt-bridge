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

from city_forecast import group_rows, rank, fmt, ROOT
from create_precip_report import rows as precipitation_rows
from ecmwf_report_gate import usable_model_rows

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
            story.append(P(date+" | "+label+" | "+str(len(ranked))+"개 시·군",st["sec"]))
            rows=[]
            for i,r in enumerate(ranked,1):
                pop=str(r["max_pop"])+"%" if r["max_pop"] is not None else "미발표"
                period=r["from_kst"][11:16]+"-"+r["to_kst"][11:16]+("" if r["full_day"] else " 일부")
                method="구 격자 "+str(r["grid_count"])+"곳 중 최대" if r["gu_count"] else r["method"]
                rows.append([str(i),r["city"],fmt(r[metric],unit),pop,period+" / "+method])
            story.append(table(["참고","시·군","예보량","최대 POP","기간 / 근거"],rows,[12,54,32,22,54],st))
            story.append(Spacer(1,4))
    if not dates:story.append(P("정량 단기예보에서 비·눈 예상 시·군이 없습니다.",st["note"]))
    return story,records

def kma_gu_tables(kma,st):
    """Show each verified KMA gu forecast separately, retaining city summaries."""
    district_rows=[r for r in precipitation_rows(kma) if r["level"]=="gu"]
    if not district_rows:
        return [],0
    groups=defaultdict(list)
    for r in district_rows:
        groups[r["date"]].append(r)
    story=[PageBreak(),P("기상청 구(區)별 비·눈 상세",st["title"]),
           P("기상청 공식 구 대표 5km 격자 예보입니다. 시별 요약에서 합쳐진 구도 개별 표기합니다. 비·눈이 예보되지 않은 구는 표시하지 않습니다.",st["small"]),
           P("동일 격자를 공유하는 구는 같은 예보를 갖습니다. 구 전체 평균이나 구 안의 최대값이 아닙니다.",st["small"])]
    for date,day_rows in sorted(groups.items()):
        story.append(P(date+" | 기상청 구별 비·눈 "+str(len(day_rows))+"건",st["sec"]))
        values=[]
        for r in day_rows:
            period=r["from_kst"][11:16]+"-"+r["to_kst"][11:16]
            if r["period"]!="24시간":
                period+=" 일부"
            name=r["region"]+(" (동일격자)" if r["shared_grid"] else "")
            values.append([name,r["forecast"],r["precipitation"],r["snow"],
                           r["max_hourly_pop"],period])
        story.append(table(["구 지역","형태","총강수 mm","신적설 cm","최대 POP","예보시간"],
                           values,[55,22,29,25,20,23],st))
        story.append(Spacer(1,4))
    return story,len(district_rows)

def model_section(kma,model,st,scope="city"):
    lookup,good=usable_model_rows(kma,model,scope=scope)
    # No successful rain/snow forecast rows => NO ECMWF title, empty page or disclaimer.
    if not good:
        return [],False
    detail=scope=="district"
    unit="구" if detail else "시·군"
    total_key="districts_targeted" if detail else "cities_targeted"
    story=[PageBreak(),P("7일 "+unit+"별 비·눈 참고: ECMWF IFS · AIFS",st["title"]),
      P("기상청 단기예보 이후의 전망을 보완하는 별도 모델입니다. Open-Meteo가 제공한 실제 ECMWF 모델 수치를 사용합니다.",st["small"]),
      P("모델 지점: 전국 "+unit+" 단위 "+str((model or {}).get(total_key,
        max((v.get("cities_requested",0) for v in
             (model or {}).get("district_models" if detail else "models",{}).values()),default=0)))+
        "개 대상 중 실제 조회에 성공한 근사 격자 참고점입니다. 기상청 공식 격자에서 위경도를 역변환했으며 "
        "행정구역 전체 평균 또는 공식 중심점이 아닙니다.",st["small"]),
      P("모델 수집: "+str((model or {}).get("generated_at_kst","조회 실패"))+
        (" | 최신 요청 실패, 최근 3시간 이내 성공본 재사용" if (model or {}).get("fallback_recent_success") else " | 이번 실행 신규 조회")+
        " | 1일 미만 시간대는 일부 기간 누적량입니다.",st["small"])]
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
        story.append(P(date+" | ECMWF "+unit+"별 검증된 모델 총강수량 참고",st["sec"]))
        story.append(table([unit,"IFS 강수 mm","AIFS 강수 mm","IFS 눈 cm","AIFS 눈 cm"],
                           rows,[50,34,34,28,28],st))
        story.append(Spacer(1,4))
    story.append(P("Open-Meteo.com 제공 ECMWF 모델 참고 (CC BY 4.0). 총강수량은 눈을 포함하는 수분량으로 "
       "비 강수만을 뜻하지 않습니다. 기상청과 상호 대체하거나 평균하지 않습니다.",st["small"]))
    return story,True

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
        P("날짜별 시·군 요약 + 구별 상세 · 실제 비·눈 예보 지역만",st["sub"]),
        P("기상청 발표: "+kma["issue_at_kst"]+" | 수집: "+kma["generated_at_kst"],st["sub"]),
        P("원본 기상청 격자 조회 "+str(kma["locations_succeeded"])+"/"+
           str(kma["locations_checked"])+"개 구·시·군 (고유 격자 "+
           str(kma.get("unique_grids_succeeded","-"))+"개). 시·군 요약과 구별 상세를 모두 제공합니다.",st["note"]),
        P("주의: 구가 있는 도시는 구 대표 격자 예보 중 최대 범위를 도시의 참고값으로 사용합니다. "
          "강수량을 더하거나 시 전체의 실제 최대 강수량으로 주장하지 않습니다.",st["small"]),
        P("예보량 **상한값이 큰 순의 참고 정렬**입니다. 범위가 겹치면 우열은 확정되지 않습니다. "
          "총강수량(mm)은 눈을 포함하며, 신적설(cm)은 별도로 표시합니다.",st["small"]),
        P("기상청 단기 API에서 실제 제공하는 날짜만 수록합니다. 7일 전체의 시·군별 정량 예보가 아니며, "
          "장기 기간의 미제공 강수량·신적설량을 생성하지 않습니다.",st["small"]),
    ]
    kma_story,records=kma_city_tables(kma,st)
    story.extend(kma_story)
    gu_story,gu_count=kma_gu_tables(kma,st)
    story.extend(gu_story)
    if model is not None:
        append,city_verified=model_section(kma,model,st)
        story.extend(append)
        extra,district_verified=model_section(kma,model,st,scope="district")
        story.extend(extra)
        verified=city_verified or district_verified
    else:
        verified=False
        city_verified=False
        district_verified=False
    doc.has_ecmwf_appendix=verified
    doc.build(story)
    if filename.stat().st_size<1000 or filename.read_bytes()[:5]!=b"%PDF-":
        raise RuntimeError("Invalid PDF header or empty output")
    return {"kma_city_date_rows":len(records),
            "kma_city_names":len({x["city"] for x in records}),
            "kma_gu_date_rows":gu_count,
            "ecmwf_gu_models_included":bool(district_verified),
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
