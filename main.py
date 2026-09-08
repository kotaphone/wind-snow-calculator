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

    # -------- 1. Nominatim --------
    try:
        url = "https://nominatim.openstreetmap.org/search"

        params = {
            "q": address,
            "format": "json",
            "limit": 1,
            "countrycodes": "de"
        }

        headers = {
            "User-Agent": "WindSnowCalculator/1.0",
            "Accept": "application/json"
        }

        r = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=10
        )

        if r.status_code == 200:
            data = r.json()

            if len(data) > 0:
                return (
                    float(data[0]["lat"]),
                    float(data[0]["lon"])
                )

    except Exception:
        pass


    # -------- 2. Photon fallback --------
    try:
        url = "https://photon.komoot.io/api/"

        params = {
            "q": address,
            "limit": 1
        }

        r = requests.get(
            url,
            params=params,
            timeout=10
        )

        if r.status_code == 200:
            data = r.json()

            features = data.get("features", [])

            if len(features) > 0:
                coords = features[0]["geometry"]["coordinates"]

                lon = float(coords[0])
                lat = float(coords[1])

                return lat, lon

    except Exception:
        pass


    raise Exception("Geocoding fehlgeschlagen")


def elevation(lat, lon):

    errors = []

    # -------- 1. OpenTopoData --------
    try:
        url = f"https://api.opentopodata.org/v1/eudem25m?locations={lat},{lon}"
        r = requests.get(url, timeout=5)

        if r.status_code == 200:
            data = r.json()

            if "results" in data and len(data["results"]) > 0:
                return data["results"][0]["elevation"]

        errors.append("opentopodata failed")

    except Exception as e:
        errors.append(f"opentopodata error: {e}")

    # -------- 2. Open-Meteo --------
    try:
        url = f"https://api.open-meteo.com/v1/elevation?latitude={lat}&longitude={lon}"
        r = requests.get(url, timeout=5)

        if r.status_code == 200:
            data = r.json()

            if "elevation" in data:
                return data["elevation"][0]

        errors.append("open-meteo failed")

    except Exception as e:
        errors.append(f"open-meteo error: {e}")

    # -------- 3. Open-Elevation --------
    try:
        url = f"https://api.open-elevation.com/api/v1/lookup?locations={lat},{lon}"
        r = requests.get(url, timeout=5)

        if r.status_code == 200:
            data = r.json()

            if "results" in data and len(data["results"]) > 0:
                return data["results"][0]["elevation"]

        errors.append("open-elevation failed")

    except Exception as e:
        errors.append(f"open-elevation error: {e}")

    # -------- FAIL --------
    raise Exception("Elevation API failed: " + " | ".join(errors))


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

    table = {
        "2": {
            "Geländekategorie I": {5: 0.890, 11: 1.034, 15: 1.097, 20: 1.159},
            "Geländekategorie II": {5: 0.695, 11: 0.839, 15: 0.904, 20: 0.969},
            "Geländekategorie III": {5: 0.586, 11: 0.644, 15: 0.709, 20: 0.775},
            "Geländekategorie IV": {5: 0.508, 11: 0.508, 15: 0.508, 20: 0.567},
            "Gemischtes Profil I": {5: 0.586, 11: 0.688, 15: 0.722, 20: 0.858},
            "Gemischtes Profil II": {5: 0.745, 11: 0.922, 15: 1.002, 20: 1.083},
            "Gemischtes Profil III": {5: 1.315, 11: 1.527, 15: 1.620, 20: 1.711},
        },

        "1": {
            "Geländekategorie I": {5: 0.721, 11: 0.838, 15: 0.889, 20: 0.939},
            "Geländekategorie II": {5: 0.563, 11: 0.680, 15: 0.732, 20: 0.785},
            "Geländekategorie III": {5: 0.475, 11: 0.521, 15: 0.574, 20: 0.628},
            "Geländekategorie IV": {5: 0.411, 11: 0.411, 15: 0.411, 20: 0.459},
            "Gemischtes Profil I": {5: 0.745, 11: 0.557, 15: 0.625, 20: 0.695},
            "Gemischtes Profil II": {5: 0.604, 11: 0.747, 15: 0.812, 20: 0.878},
            "Gemischtes Profil III": {5: 1.315, 11: 1.527, 15: 1.620, 20: 1.711},
        },

        "3": {
            "Geländekategorie I": {5: 1.077, 11: 1.251, 15: 1.327, 20: 1.402},
            "Geländekategorie II": {5: 0.841, 11: 1.016, 15: 1.094, 20: 1.172},
            "Geländekategorie III": {5: 0.709, 11: 0.779, 15: 0.858, 20: 0.938},
            "Geländekategorie IV": {5: 0.615, 11: 0.614, 15: 0.615, 20: 0.686},
            "Gemischtes Profil I": {5: 0.709, 11: 0.832, 15: 0.934, 20: 1.039},
            "Gemischtes Profil II": {5: 0.902, 11: 1.115, 15: 1.213, 20: 1.311},
            "Gemischtes Profil III": {5: 1.315, 11: 1.527, 15: 1.620, 20: 1.711},
        },

        "4": {
            "Geländekategorie I": {5: 1.282, 11: 1.489, 15: 1.580, 20: 1.668},
            "Geländekategorie II": {5: 1.000, 11: 1.209, 15: 1.302, 20: 1.395},
            "Geländekategorie III": {5: 0.844, 11: 0.927, 15: 1.021, 20: 1.116},
            "Geländekategorie IV": {5: 0.731, 11: 0.731, 15: 0.731, 20: 0.817},
            "Gemischtes Profil I": {5: 0.844, 11: 0.991, 15: 1.111, 20: 1.236},
            "Gemischtes Profil II": {5: 1.073, 11: 1.328, 15: 1.444, 20: 1.560},
            "Gemischtes Profil III": {5: 1.315, 11: 1.528, 15: 1.620, 20: 1.711},
        }
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
def calc(lat: float, lon: float, roof_pitch: float, roof_height: float, terrain: str):

    try:

        if math.isnan(roof_pitch) or math.isnan(roof_height):
            raise Exception("NaN input")

        if roof_pitch < 0 or roof_pitch > 60:
            raise Exception("roof_pitch invalid")

        if roof_height < 0 or roof_height > 30:
            raise Exception("roof_height invalid")

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

        require("height", "Traufhöhe")
        require("roof_type", "Dachform")

        height = float(data.get("height", 0))
        length = float(data.get("length", 0))
        width = float(data.get("width", 0))
        roof_type = data.get("roof_type")

        ridge = float(data.get("ridge", 0))
        side_sec = data.get("sideSec")
        tax = data.get("tax")

        if height <= 0:
            raise Exception("Traufhöhe muss größer als 0 sein")

        if length <= 0:
            raise Exception("Gerüstlänge fehlt")

        # ===== PREISE LADEN =====
        with open(os.path.join(BASE_DIR, "geruestpreise.txt")) as f:
            prices = json.load(f)

        # ===== PREIS PRO 3m JE NACH HÖHE =====
        if height <= 4:
            base_unit = prices["preise_pro_3m"]["bis_4m"]
        elif height <= 6:
            base_unit = prices["preise_pro_3m"]["bis_6m"]
        elif height <= 8:
            base_unit = prices["preise_pro_3m"]["bis_8m"]
        else:
            base_unit = prices["preise_pro_3m"]["bis_10m"]

        # ===== EINHEITEN (3m SYSTEM) =====
        units = max(1, round(length / 3))

        # ===== LÄNGENFAKTOR =====
        if length <= 10:
            factor = prices["faktoren"]["bis_10m"]
        elif length <= 20:
            factor = prices["faktoren"]["bis_20m"]
        elif length <= 30:
            factor = prices["faktoren"]["bis_30m"]
        else:
            factor = prices["faktoren"]["ueber_30m"]

        base_cost = units * base_unit * factor

        breakdown = [{
            "name": f"Grundgerüst ({units} × 3m)",
            "value": base_cost
        }]

        # ===== SEITENSICHERUNG =====
        security_cost = 0

        if roof_type == "gable" and side_sec == "yes":

            if width <= 0:
                raise Exception("Ortganglänge fehlt")

            # mały ortgang → pauschal
            if width <= 5:
                security_cost = prices["seitensicherung"]["pauschal_bis_5m"]

            # duży ortgang → liczony jak rusztowanie
            else:
                sec_units = max(1, round(width / 3))
                security_cost = sec_units * base_unit * 0.5

            breakdown.append({
                "name": "Seitensicherung",
                "value": security_cost
            })

        # ===== SUMME =====
        total = base_cost + security_cost

        if tax == "gross":
            total *= (1 + prices["mwst"]["satz"])

        return {
            "length": length,
            "base_cost": round(base_cost, 2),
            "security_cost": round(security_cost, 2),
            "total": round(total, 2),
            "breakdown": breakdown
        }

    except Exception as e:
        return {"error": str(e)}
