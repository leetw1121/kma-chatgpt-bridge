#!/usr/bin/env python3
"""Evidence-aware seven-day IFS/AIFS comparison, NOT an accuracy forecast.

Only compare verified, non-stale model values. Counts of agreement cannot
establish hit rate without future independent observations. Keep all 276
city/county/gu reference labels, including dry and missing values.
"""
import datetime as dt
import json
from collections import Counter
from pathlib import Path

from create_precip_report import ROOT, rows as kma_precip_rows
from ecmwf_city_locations import build_city_locations, build_district_locations
from ecmwf_report_gate import usable_model_rows

KST=dt.timezone(dt.timedelta(hours=9))


def usable_metric(day,field):
    if not isinstance(day,dict):
        return None
    value=day.get(field)
    if field=="snowfall_cm" and not day.get("snowfall_valid"):
        return None
    return value if isinstance(value,(int,float)) and not isinstance(value,bool) and value>=0 else None


def wet_class(day):
    """True/False only on actual usable model data; unknown stays None."""
    rain=usable_metric(day,"precip_mm")
    snow=usable_metric(day,"snowfall_cm")
    if rain is None and snow is None:
        return None
    if (rain is not None and rain>0) or (snow is not None and snow>0):
        return True
    # Total precipitation includes water equivalent of snowfall.
    if rain==0 or (rain is None and snow==0):
        return False
    return None


def agreement_by_date(kma,model,city_labels=None,gu_labels=None):
    """Seven local calendar dates D+0..D+6; NOT exact hourly lead windows.

    Requires independent 276 label mapping by default, not a set of only
    successful/wet locations. Never convert an unavailable model to zero.
    """
    generated=dt.datetime.fromisoformat(kma["generated_at_kst"])
    if generated.tzinfo is None:
        raise ValueError("KMA_GENERATED_TIME_NEEDS_ZONE")
    today=generated.astimezone(KST).date()
    if city_labels is None:
        city_labels=list(build_city_locations())
    if gu_labels is None:
        gu_labels=list(build_district_locations())
    city_labels=list(city_labels)
    gu_labels=list(gu_labels)
    if len(set(city_labels))!=len(city_labels) or len(set(gu_labels))!=len(gu_labels):
        raise ValueError("DUPLICATE_MODEL_LOCATION")
    if set(city_labels)&set(gu_labels):
        raise ValueError("SCOPE_COLLISION")
    labels=city_labels+gu_labels
    if not labels:
        raise ValueError("NO_MODEL_LOCATIONS")

    city,_=usable_model_rows(kma,model,scope="city",include_dry=True)
    district,_=usable_model_rows(kma,model,scope="district",include_dry=True)
    kma_counts=Counter(x["date"] for x in kma_precip_rows(kma))
    kma_dates={day for location in kma.get("locations",[])
               for day in (location.get("days") or {})}
    daily=[]
    for offset in range(7):
        date=(today+dt.timedelta(days=offset)).isoformat()
        tallies=Counter()
        for scope,scope_labels,actual in (("city",city_labels,city),
                                          ("gu",gu_labels,district)):
            for label in scope_labels:
                series=actual.get((date,label),{})
                a=series.get("IFS")
                b=series.get("AIFS Single")
                aw=wet_class(a)
                bw=wet_class(b)
                if aw is None or bw is None:
                    tallies["unverified"]+=1
                    continue
                if (a.get("forecast_hours")!=24 or b.get("forecast_hours")!=24):
                    # D+0 / final horizon days often have only some hours.
                    # Never call a short dry interval a full-day dry forecast
                    # or include it in full-day agreement denominators.
                    tallies["partial_pairs"]+=1
                    continue
                if aw and bw:
                    tallies["both_wet"]+=1
                elif aw or bw:
                    tallies["one_wet"]+=1
                else:
                    tallies["both_dry"]+=1
                asnow=usable_metric(a,"snowfall_cm")
                bsnow=usable_metric(b,"snowfall_cm")
                if asnow is not None and bsnow is not None:
                    if asnow>0 and bsnow>0:
                        tallies["both_snow"]+=1
                    elif (asnow>0)!=(bsnow>0):
                        tallies["one_snow"]+=1

        total=len(labels)
        checked=tallies["both_wet"]+tallies["one_wet"]+tallies["both_dry"]
        if checked+tallies["partial_pairs"]+tallies["unverified"]!=total:
            raise ValueError("MODEL_PAIR_ACCOUNTING_FAILED")
        daily.append({
            "date":date,
            "days_ahead":offset,
            "model_regions_targeted":total,
            "pairs_checked":checked,
            "pairs_partial":tallies["partial_pairs"],
            "pairs_unverified":tallies["unverified"],
            "both_wet":tallies["both_wet"],
            "one_wet":tallies["one_wet"],
            "both_dry":tallies["both_dry"],
            "both_snow":tallies["both_snow"],
            "one_snow":tallies["one_snow"],
            # Not 0 if official KMA has not forecast this day.
            "kma_wet":kma_counts[date] if date in kma_dates else None,
        })
    return daily


def markdown_report(daily,kma):
    lines=[
        "# 날짜별 비·눈 모델 일치 현황 (적중률이 아닙니다)",
        "",
        "- 기존 기상청 256개 구·시·군과 ECMWF IFS·AIFS 각 167개 시·군 + 109개 구 수집을 변경하지 않습니다.",
        "- 기상청 단기예보는 실제 제공한 날짜까지만 표시하며, 0~6시간용 기상청 초단기예보는 아직 별도로 수집하지 않습니다.",
        "- JMA MSM 약 5km/4일 모델은 운영 데이터에 포함하지 않습니다. 강수량 비교 후 성능을 검증해야 하며, JMA의 적설량은 Open-Meteo가 추정한 값입니다.",
        "- IFS/AIFS는 서로 다른 독립 관측이 아니라 상관관계가 있는 예측 모델입니다. 두 모델이 일치해도 적중률 또는 확률을 의미하지 않습니다.",
        "- KMA 발표범위 밖의 날짜는 KMA 비·눈 지역 수를 '미제공'으로 표시합니다. 미확인과 무강수를 구분합니다.",
        "- D+0~D+6은 한국시간 **달력 날짜 기준**이며, 엄밀한 0~6시간 또는 96~168시간 시작·종료 구간이 아닙니다.",
        "- **하루 24시간을 모두 제공한 IFS/AIFS 쌍**만 강수·무강수·눈 일치 건수에 포함합니다. 일부 시간만 제공한 쌍은 '일부기간'으로 별도 집계하며, 미조회와 구별합니다.",
        "",
        "기상청 자료 생성: "+kma["generated_at_kst"],
        "",
        "| 날짜 | 기상청 비·눈 지역 | 전체일 쌍/대상 | 양쪽 강수 | 한쪽만 강수 | 양쪽 무강수 | 양쪽 눈 | 한쪽만 눈 | 일부기간 쌍 | 쌍 미확인 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for x in daily:
        kma_num=str(x["kma_wet"]) if x["kma_wet"] is not None else "미제공"
        lines.append(
            f"| {x['date']} | {kma_num} | {x['pairs_checked']}/{x['model_regions_targeted']} | "
            f"{x['both_wet']} | {x['one_wet']} | {x['both_dry']} | "
            f"{x['both_snow']} | {x['one_snow']} | {x['pairs_partial']} | {x['pairs_unverified']} |"
        )
    lines.extend([
        "",
        "## 실전 활용 원칙",
        "",
        "- 0~6시간: 공식 기상청 초단기예보를 별도 검증한 후 추가하는 것이 타당합니다. 현재 이 구간의 시작·종료 시점을 정확히 예측한다고 주장하지 않습니다.",
        "- 약 6~96시간: 공식 기상청 단기예보(제공 구간에 한함)를 기준으로 IFS/AIFS를 독립적으로 비교합니다. JMA MSM은 검증 전 시험 모델입니다.",
        "- 약 96~168시간: IFS/AIFS를 모두 보며 경향과 불일치를 확인하되, AIFS를 일률적으로 우선하거나 눈 수치를 합산하지 않습니다.",
        "- 모델 성능 우열을 확정하려면 동일 지역·날짜·예보시차에 대응하는 **독립 관측자료**와 과거 예보 스냅샷으로 적중·오보·누락을 검증해야 합니다.",
        "",
    ])
    return "\n".join(lines)


def main(root=ROOT):
    data=root/"data"
    kma=json.loads((data/"latest.json").read_text(encoding="utf-8"))
    model_path=data/"ecmwf_regional.json"
    model=json.loads(model_path.read_text(encoding="utf-8")) if model_path.exists() else None
    counts=agreement_by_date(kma,model)
    target=data/"model_agreement_7day.md"
    target.write_text(markdown_report(counts,kma),encoding="utf-8")
    print("MODEL_AGREEMENT_REPORT_PASS",json.dumps([
        {key:x[key] for key in ("date","pairs_checked","pairs_unverified","both_wet","both_snow")}
        for x in counts],ensure_ascii=False))
    return counts


if __name__=="__main__":
    main()
