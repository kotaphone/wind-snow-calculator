from fastapi import FastAPI
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

import requests
import geopandas as gpd
from shapely.geometry import Point
import math
import time
import json
import os
from fastapi import Request

app = FastAPI()

# ⭐⭐⭐⭐⭐ CORS — tylko to dodaliśmy ⭐⭐⭐⭐⭐
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- LOAD GIS ----------------

snow = gpd.read_file("snow.kml", driver="LIBKML")
wind = gpd.read_file("wind.kml", driver="LIBKML")

# ⭐ CRS HARD FIX
if snow.crs is None:
    snow.set_crs(epsg=4326, inplace=True)
else:
    snow = snow.to_crs(epsg=4326)

if wind.crs is None:
    wind.set_crs(epsg=4326, inplace=True)
else:
    wind = wind.to_crs(epsg=4326)

# ⭐ spatial index warmup
snow.sindex
wind.sindex


# ---------------- GEO ----------------

def geocode(address):

    url = "https://nominatim.openstreetmap.org/search"

    params = {
        "q": address,
        "format": "json",
        "limit": 1,
        "countrycodes": "de"
    }

    headers = {
        "User-Agent": "wind-snow-calculator"
    }

    for _ in range(2):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=6)

            if r.status_code != 200:
                time.sleep(0.4)
                continue

            data = r.json()

            if len(data) == 0:
                raise Exception("Adresse nicht gefunden")

            return float(data[0]["lat"]), float(data[0]["lon"])

        except Exception:
            time.sleep(0.4)

    raise Exception("Geocoding fehlgeschlagen")


def elevation(lat, lon):

    try:
        url = f"https://api.open-elevation.com/api/v1/lookup?locations={lat},{lon}"

        for _ in range(2):
            r = requests.get(url, timeout=6)

            if r.status_code == 200:
                data = r.json()
                return data["results"][0]["elevation"]

            time.sleep(0.3)

        return 0

    except Exception:
        return 0


def get_zone(gdf, lat, lon):

    pt = Point(lon, lat)
    res = gdf[gdf.intersects(pt)]

    if len(res) > 0:
        zone = str(res.iloc[0]["Name"])

        if zone.strip().lower() == "1a*":
            return "1a"

        return zone

    return "unknown"


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

    z = zone.replace("*", "")

    if z not in base:
        return 0.85

    if elevation <= limits[z]:
        return base[z]

    A = elevation

    if z in ["1", "1a"]:
        sk = 0.19 + 0.91 * ((A + 140) / 760) ** 2
        if z == "1a":
            sk *= 1.25

    elif z in ["2", "2a"]:
        sk = 0.25 + 1.91 * ((A + 140) / 760) ** 2
        if z == "2a":
            sk *= 1.25

    elif z == "3":
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

    return snow_ground(zone, elevation) * mu_pv(angle)


# ---------------- WIND ----------------

def interp_log(h, h1, h2, v1, v2):

    if h <= h1:
        return v1

    if h >= h2:
        return v2

    r = math.log(h / h1) / math.log(h2 / h1)
    return v1 + (v2 - v1) * r


def wind_pressure(zone, height, terrain):

    if math.isnan(height):
        return 0.60

    # ✅ FIX — pusty dict zamiast błędu
    table = {}

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

        if math.isnan(roof_pitch) or math.isnan(roof_height):
            raise Exception("NaN input")

        if roof_pitch < 0 or roof_pitch > 60:
            raise Exception("roof_pitch invalid")

        if roof_height < 0 or roof_height > 30:
            raise Exception("roof_height invalid")

        lat, lon = geocode(address)
        h = elevation(lat, lon)

        snow_zone = get_zone(snow, lat, lon)
        wind_zone = get_zone(wind, lat, lon)

        snow_kn = snow_roof(snow_zone, h, roof_pitch)

        is_exceptional = "*" in snow_zone
        snow_exceptional = snow_kn * 2.3 if is_exceptional else snow_kn

        wind_kn = wind_pressure(wind_zone, roof_height, terrain)

        return {
            "snow_zone": snow_zone,
            "wind_zone": wind_zone,
            "snow_regular": round(snow_kn, 3),
            "snow_exceptional": round(snow_exceptional, 3),
            "wind_pressure": round(wind_kn, 3),
            "elevation": round(h, 1),
            "is_exceptional": is_exceptional
        }

    except Exception as e:
        return JSONResponse(status_code=200, content={"error": str(e)})


@app.get("/")
def home():
    return FileResponse("index.html")


@app.get("/ping")
def ping():
    return {"status": "ok"}
    
@app.get("/index2")
def index2():
    return FileResponse("index2.html")

# ---------------- GERÜST ----------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

@app.post("/scaffold-calc")
async def scaffold_calc(req: Request):
    try:
        data = await req.json()

        # ===== VALIDIERUNG =====
        def require(field, name):
            if field not in data or data[field] is None:
                raise Exception(f"Feld fehlt: {name}")

        require("height", "Gerüsthöhe")
        require("roof_pitch", "Dachneigung")
        require("roof_type", "Dachform")

        height = float(data["height"])
        pitch = float(data["roof_pitch"])
        roof_type = data["roof_type"]

        if height <= 0:
            raise Exception("Gerüsthöhe muss größer als 0 sein")

        if pitch < 0:
            raise Exception("Dachneigung ungültig")

        # ===== FLÄCHE =====
        area = data.get("area")

        if not area:
            length = float(data.get("length", 0))
            width = float(data.get("width", 0))

            if length <= 0 or width <= 0:
                raise Exception("Bitte Länge und Breite oder Fläche angeben")

            area = length * width

        # ===== PREISE =====
        with open("geruestpreise.txt") as f:
            prices = json.load(f)

        price_m2 = prices["grundpreise"]["pro_m2"]
        base_price = prices["grundpreise"]["grundpauschale"]

        base_cost = area * price_m2 + base_price

        security_cost = 0
        breakdown = []

        # ===== FLACHDACH =====
        if roof_type == "flat":
            attika = float(data.get("attika", 0))

            if attika <= 0:
                raise Exception("Attikahöhe fehlt")

            if attika < 0.7:
                sec_price = prices["sicherungen"]["attika_unter_0_7"]["preis_pro_m2"]
                security_cost = area * sec_price

                breakdown.append({
                    "name": "Sicherung Attika (< 0,7 m)",
                    "value": security_cost
                })

        # ===== SATTELDACH =====
        elif roof_type == "gable":
            ridge = float(data.get("ridge", 0))
            side_sec = data.get("sideSec")

            if ridge <= 0:
                raise Exception("Firsthöhe fehlt")

            if not side_sec:
                raise Exception("Seitensicherung auswählen")

            if side_sec == "yes":
                width = float(data.get("width", 0))

                if width <= 0:
                    raise Exception("Gebäudebreite fehlt")

                sec_price = prices["sicherungen"]["seitensicherung_satteldach"]["preis_pro_m2"]

                security_cost = width * ridge * 2 * sec_price

                breakdown.append({
                    "name": "Seitensicherung",
                    "value": security_cost
                })

        # ===== SUMME =====
        total = base_cost + security_cost

        if data.get("tax") == "gross":
            total *= (1 + prices["mwst"]["satz"])

        breakdown.insert(0, {
            "name": "Grundpreis Gerüst",
            "value": base_cost
        })

        return {
            "area": area,
            "base_cost": base_cost,
            "security_cost": security_cost,
            "total": total,
            "breakdown": breakdown
        }

    except Exception as e:
        return {"error": str(e)}
