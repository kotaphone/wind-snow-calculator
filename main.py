from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

import requests
import geopandas as gpd
from shapely.geometry import Point
import math

app = FastAPI()

# ⭐⭐⭐⭐⭐ CORS ⭐⭐⭐⭐⭐
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

snow = gpd.read_file("snow.kml", driver="LIBKML")
wind = gpd.read_file("wind.kml", driver="LIBKML")

# ---------------- GEO ----------------

def geocode(address):

    url = "https://nominatim.openstreetmap.org/search"

    params = {
        "q": address,
        "format": "json",
        "limit": 1
    }

    headers = {
        "User-Agent": "wind-snow-calculator"
    }

    r = requests.get(url, params=params, headers=headers, timeout=10)

    data = r.json()

    if len(data) == 0:
        raise Exception("Adresse nicht gefunden")

    return float(data[0]["lat"]), float(data[0]["lon"])


def elevation(lat, lon):

    try:
        url = f"https://api.open-elevation.com/api/v1/lookup?locations={lat},{lon}"
        r = requests.get(url, timeout=10)

        if r.status_code != 200:
            return 0

        data = r.json()

        return data["results"][0]["elevation"]

    except:
        return 0


def get_zone(gdf, lat, lon):

    pt = Point(lon, lat)

    res = gdf[gdf.contains(pt)]

    if len(res) > 0:
        return str(res.iloc[0]["Name"])

    return "unknown"


# ---------------- NORMALIZE SNOW ZONE ----------------

def normalize_snow_zone(zone):

    # ⭐⭐⭐ SPECJALNY WYJĄTEK ⭐⭐⭐
    if zone == "1a*":
        return "1a", False

    # Norddeutsches Flachland
    if zone in ["1*", "2*"]:
        return zone.replace("*", ""), True

    # normalne strefy
    return zone.replace("*", ""), False


# ---------------- SCHNEE ----------------

def snow_ground(zone, elevation):

    base = {
        "1": 0.65,
        "1a": 0.81,
        "2": 0.85,
        "2a": 1.06,
        "3": 1.10
    }

    limits = {
        "1": 400,
        "1a": 400,
        "2": 285,
        "2a": 285,
        "3": 255
    }

    if zone not in base:
        return 0.85

    if elevation <= limits[zone]:
        return base[zone]

    A = elevation

    if zone in ["1", "1a"]:
        sk = 0.19 + 0.91 * ((A + 140) / 760) ** 2
        if zone == "1a":
            sk *= 1.25

    elif zone in ["2", "2a"]:
        sk = 0.25 + 1.91 * ((A + 140) / 760) ** 2
        if zone == "2a":
            sk *= 1.25

    elif zone == "3":
        sk = 0.31 + 2.91 * ((A + 140) / 760) ** 2

    return sk


def mu_pv(angle):

    if angle <= 30:
        return 0.8

    if angle <= 45:
        return 0.8 - (angle - 30) * 0.0266667

    if angle <= 60:
        return 0.4 - (angle - 45) * 0.0266667

    return 0


def snow_roof(zone, elevation, angle):

    sk_ground = snow_ground(zone, elevation)
    mu = mu_pv(angle)

    return sk_ground * mu


# ---------------- WIND ----------------

def interp_log(h, h1, h2, v1, v2):

    if h <= h1:
        return v1

    if h >= h2:
        return v2

    r = math.log(h / h1) / math.log(h2 / h1)

    return v1 + (v2 - v1) * r


def wind_pressure(zone, height, terrain):

    table = {
        # (tabela bez zmian — zostawiam skróconą dla czytelności)
    }

    zone = zone.replace("*", "")

    if zone not in table:
        return 0.60

    terrain_table = table[zone].get(terrain)

    if terrain_table is None:
        return 0.60

    heights = sorted(terrain_table.keys())

    for i in range(len(heights) - 1):

        h1 = heights[i]
        h2 = heights[i + 1]

        if height <= h2:

            v1 = terrain_table[h1]
            v2 = terrain_table[h2]

            return interp_log(height, h1, h2, v1, v2)

    return terrain_table[heights[-1]]


# ---------------- API ----------------

@app.get("/calc")
def calc(address: str, roof_pitch: float, roof_height: float, terrain: str):

    try:

        lat, lon = geocode(address)
        h = elevation(lat, lon)

        snow_zone_raw = get_zone(snow, lat, lon)
        wind_zone = get_zone(wind, lat, lon)

        snow_zone, is_exceptional = normalize_snow_zone(snow_zone_raw)

        snow_kn = snow_roof(snow_zone, h, roof_pitch)

        snow_exceptional = snow_kn * 2.3 if is_exceptional else snow_kn

        return {
            "snow_zone": snow_zone,
            "wind_zone": wind_zone,
            "snow_regular": round(snow_kn, 3),
            "snow_exceptional": round(snow_exceptional, 3),
            "wind_pressure": round(wind_pressure(wind_zone, roof_height, terrain), 3),
            "elevation": round(h, 1),
            "is_exceptional": is_exceptional
        }

    except Exception:

        return JSONResponse(
            status_code=200,
            content={"error": "Serverfehler — Eingabedaten prüfen"}
        )
