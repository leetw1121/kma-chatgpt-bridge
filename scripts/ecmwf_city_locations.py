#!/usr/bin/env python3
"""Approximate ECMWF city points from the VERIFIED KMA 5-km representative grids.

These are NOT official city centres or exact 174 KMA forecast grid locations.
The same location classification as city_forecast.py merges all gu within a city.
"""
import csv
import math
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def grid_to_latlon(nx,ny):
    """Inverse Korean DFS/Lambert 5-km grid mapping, rounded grid-cell centres."""
    re=6371.00877/5.
    d=math.pi/180.
    sl1=30*d
    sl2=60*d
    olat=38*d
    sn=math.log(math.cos(sl1)/math.cos(sl2))/math.log(
        math.tan(math.pi/4+sl2/2)/math.tan(math.pi/4+sl1/2))
    sf=math.tan(math.pi/4+sl1/2)**sn*math.cos(sl1)/sn
    ro=re*sf/math.tan(math.pi/4+olat/2)**sn
    x=float(nx)-43
    y=ro-float(ny)+136
    ra=math.hypot(x,y)
    if ra<=0:
        raise ValueError("INVALID_GRID")
    lat=(2*math.atan((re*sf/ra)**(1/sn))-math.pi/2)/d
    lon=(math.atan2(x,y)/sn+126*d)/d
    if not (32<=lat<=40 and 124<=lon<=132):
        raise ValueError("GRID_OUTSIDE_KOREAN_DOMAIN")
    return lat,lon

def build_city_locations(source=ROOT/"locations.csv"):
    """One approximate ECMWF point for each KMA city-level display label."""
    grouped=defaultdict(set)
    with Path(source).open(encoding="utf-8-sig",newline="") as f:
        for row in csv.DictReader(f):
            if row["level"] not in ("gu","city","gun"):
                raise ValueError("UNKNOWN_REGION_LEVEL")
            city=row["name"].rsplit(" ",1)[0] if row["level"]=="gu" else row["name"]
            grouped[city].add((int(row["nx"]),int(row["ny"])))
    if not grouped:
        raise ValueError("EMPTY_KMA_CITY_MAPPING")
    points={}
    for city,grids in sorted(grouped.items()):
        coords=[grid_to_latlon(x,y) for x,y in sorted(grids)]
        points[city]=(round(sum(p[0] for p in coords)/len(coords),5),
                      round(sum(p[1] for p in coords)/len(coords),5))
    return points

def build_district_locations(source=ROOT/"locations.csv"):
    """One approximate ECMWF reference point for each official KMA gu row.

    This supplements (never replaces) the existing 167 city/county points.
    An inverse 5-km KMA grid cell is NOT a district-area mean or exact
    Open-Meteo model grid cell. Shared KMA grids may yield identical points.
    """
    points={}
    with Path(source).open(encoding="utf-8-sig",newline="") as f:
        for row in csv.DictReader(f):
            if row["level"]!="gu":
                continue
            name=row["name"]
            if name in points:
                raise ValueError("DUPLICATE_DISTRICT_LABEL_"+name)
            lat,lon=grid_to_latlon(int(row["nx"]),int(row["ny"]))
            points[name]=(round(lat,5),round(lon,5))
    if not points:
        raise ValueError("EMPTY_KMA_DISTRICT_MAPPING")
    return dict(sorted(points.items()))
