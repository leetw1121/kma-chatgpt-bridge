#!/usr/bin/env python3
"""Evaluate archived model forecasts against separately obtained observations.

No live API calls. Observation files must be supplied from a trusted measured
source for the same geographic representative point. Never compute 'skill'
against the KMA forecast or a different provider forecast as if observed.
"""
import csv
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path

FORECAST_MODELS=("KMA_SHORT","IFS","AIFS Single")


def number(value):
    try:
        if value in (None,""):
            return None
        result=float(value)
        if result<0 or result!=result or result==float("inf"):
            return None
        return result
    except (ValueError,TypeError):
        return None


def read_observations(path):
    """Expected CSV: valid_date,region,coverage_hours,observed_precip_mm,
    observed_snow_cm,observation_source.
    Blank observed_snow_cm means snow score unavailable, never '0 cm'.
    """
    result={}
    with Path(path).open(encoding="utf-8-sig",newline="") as f:
        for row in csv.DictReader(f):
            key=(row["valid_date"],row["region"])
            if key in result:
                raise ValueError("DUPLICATE_OBSERVATION")
            source=(row.get("observation_source") or "").strip()
            if not source or source.lower() in ("forecast","kma_short","ifs","aifs"):
                raise ValueError("INDEPENDENT_MEASURED_SOURCE_REQUIRED")
            if number(row.get("coverage_hours"))!=24:
                continue
            result[key]={
                "rain":number(row.get("observed_precip_mm")),
                "snow":number(row.get("observed_snow_cm")),
                "source":source,
            }
    return result


def read_snapshots(path):
    rows=[]
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                item=json.loads(line)
                if item.get("model") not in FORECAST_MODELS:
                    continue
                rows.append(item)
    return rows


def skill(hits,misses,false_alarms,correct_negatives):
    def ratio(a,b):
        return round(a/b,4) if b else None
    return {
        "n":hits+misses+false_alarms+correct_negatives,
        "hits":hits,"misses":misses,"false_alarms":false_alarms,
        "correct_negatives":correct_negatives,
        "CSI":ratio(hits,hits+misses+false_alarms),
        "recall_POD":ratio(hits,hits+misses),
        "precision":ratio(hits,hits+false_alarms),
        "false_alarm_ratio":ratio(false_alarms,hits+false_alarms),
    }


def evaluate(snapshots,observations,as_of,threshold_mm=0.1,threshold_snow_cm=0.1):
    """Paired, complete-day categorical verification; no confidence claims.

    Only score observations that have finished and full-day forecasts issued
    prior to the verified period. A different model's missing entry NEVER
    counts as a 'no-event'. Display sample size before drawing conclusions.
    """
    if as_of.tzinfo is None:
        raise ValueError("TIMEZONE_REQUIRED")
    candidates=defaultdict(dict)
    for forecast in snapshots:
        if not forecast.get("full_day") or forecast.get("forecast_hours")!=24:
            continue
        model=forecast.get("model")
        if model not in FORECAST_MODELS:
            continue
        date=forecast.get("valid_date")
        region=forecast.get("region")
        if (date,region) not in observations:
            continue
        try:
            start=dt.datetime.combine(dt.date.fromisoformat(date),dt.time(0),
                                      tzinfo=dt.timezone(dt.timedelta(hours=9)))
            source=dt.datetime.fromisoformat(forecast["retrieved_at_kst"])
            if source.tzinfo is None or source>=start or start+dt.timedelta(days=1)>as_of:
                continue
        except (ValueError,TypeError,KeyError):
            continue
        lead_days=(start-source.astimezone(start.tzinfo)).total_seconds()/86400
        horizon="D0-3" if lead_days<4 else "D4-7" if lead_days<=7 else None
        if horizon is None:
            continue
        key=(horizon,date,region)
        if model in candidates[key]:
            # Multiple competing initializations cannot be silently mixed.
            # Pick the latest forecast retrieval BEFORE the target day's start.
            old=candidates[key][model]
            old_time=dt.datetime.fromisoformat(old["retrieved_at_kst"])
            if source<=old_time:
                continue
        candidates[key][model]=forecast

    scores={}
    for horizon in ("D0-3","D4-7"):
        models=("KMA_SHORT","IFS","AIFS Single") if horizon=="D0-3" else ("IFS","AIFS Single")
        # Every model must forecast the SAME observation points; otherwise
        # skill differences would be contaminated by coverage differences.
        keys=[key for key,values in candidates.items()
              if key[0]==horizon and all(m in values for m in models)]
        for kind,observed_key,threshold,pred_key in (
            ("rain_or_snow","rain",threshold_mm,"predicted_precipitation"),
            ("snow","snow",threshold_snow_cm,"predicted_snow")):
            paired=[]
            for key in keys:
                observed=observations[key[1],key[2]].get(observed_key)
                if observed is None:
                    continue
                values=candidates[key]
                if any(values[m].get(pred_key) not in (True,False) for m in models):
                    continue
                paired.append((observed>=threshold,values))
            for model in models:
                hits=misses=false_alarms=correct_negatives=0
                for measured,values in paired:
                    predicted=values[model][pred_key]
                    if measured and predicted:hits+=1
                    elif measured:misses+=1
                    elif predicted:false_alarms+=1
                    else:correct_negatives+=1
                row=skill(hits,misses,false_alarms,correct_negatives)
                row["sample_sufficient_for_preliminary_review"]=row["n"]>=30
                scores[horizon,kind,model]=row
    return scores


def main():
    if len(sys.argv)!=3:
        raise SystemExit("Usage: python scripts/forecast_skill_verification.py " 
                         "archive.jsonl independent_observations.csv")
    predictions=read_snapshots(sys.argv[1])
    observations=read_observations(sys.argv[2])
    results=evaluate(predictions,observations,dt.datetime.now(dt.timezone.utc))
    for (horizon,kind,model),metric in sorted(results.items()):
        print(json.dumps({"horizon":horizon,"kind":kind,"model":model,
                          **metric},ensure_ascii=False))
    if not results or all(metric["n"]==0 for metric in results.values()):
        print("NO_PAIRED_INDEPENDENT_OBSERVATIONS; no accuracy claim.")


if __name__=="__main__":
    main()
