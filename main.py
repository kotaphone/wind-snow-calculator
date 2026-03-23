from fastapi import FastAPI
from fastapi.responses import JSONResponse
import requests
import geopandas as gpd
from shapely.geometry import Point
import math

app = FastAPI()

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
        "2": {
            "Geländekategorie I": {5: 0.890, 11: 1.034, 15: 1.097, 20: 1.159},
            "Geländekategorie II": {5: 0.695, 11: 0.839, 15: 0.904, 20: 0.969},
            "Geländekategorie III": {5: 0.586, 11: 0.644, 15: 0.709, 20: 0.775},
            "Geländekategorie IV": {5: 0.508, 11: 0.508, 15: 0.508, 20: 0.567},
            "Gemischtes Profil I": {5: 1.315, 11: 1.527, 15: 1.620, 20: 1.711},
            "Gemischtes Profil II": {5: 0.745, 11: 0.922, 15: 1.002, 20: 1.083},
            "Gemischtes Profil III": {5: 0.586, 11: 0.688, 15: 0.722, 20: 0.858},
        },

        "1": {
            "Geländekategorie I": {5: 0.721, 11: 0.838, 15: 0.889, 20: 0.939},
            "Geländekategorie II": {5: 0.563, 11: 0.680, 15: 0.732, 20: 0.785},
            "Geländekategorie III": {5: 0.475, 11: 0.521, 15: 0.574, 20: 0.628},
            "Geländekategorie IV": {5: 0.411, 11: 0.411, 15: 0.411, 20: 0.459},
            "Gemischtes Profil I": {5: 1.315, 11: 1.527, 15: 1.620, 20: 1.711},
            "Gemischtes Profil II": {5: 0.604, 11: 0.747, 15: 0.812, 20: 0.878},
            "Gemischtes Profil III": {5: 0.745, 11: 0.557, 15: 0.625, 20: 0.695},
        },

        "3": {
            "Geländekategorie I": {5: 1.077, 11: 1.251, 15: 1.327, 20: 1.402},
            "Geländekategorie II": {5: 0.841, 11: 1.016, 15: 1.094, 20: 1.172},
            "Geländekategorie III": {5: 0.709, 11: 0.779, 15: 0.858, 20: 0.938},
            "Geländekategorie IV": {5: 0.615, 11: 0.614, 15: 0.615, 20: 0.686},
            "Gemischtes Profil I": {5: 1.315, 11: 1.527, 15: 1.620, 20: 1.711},
            "Gemischtes Profil II": {5: 0.902, 11: 1.115, 15: 1.213, 20: 1.311},
            "Gemischtes Profil III": {5: 0.709, 11: 0.832, 15: 0.934, 20: 1.039},
        },

        "4": {
            "Geländekategorie I": {5: 1.282, 11: 1.489, 15: 1.580, 20: 1.668},
            "Geländekategorie II": {5: 1.000, 11: 1.209, 15: 1.302, 20: 1.395},
            "Geländekategorie III": {5: 0.844, 11: 0.927, 15: 1.021, 20: 1.116},
            "Geländekategorie IV": {5: 0.731, 11: 0.731, 15: 0.731, 20: 0.817},
            "Gemischtes Profil I": {5: 1.315, 11: 1.528, 15: 1.620, 20: 1.711},
            "Gemischtes Profil II": {5: 1.073, 11: 1.328, 15: 1.444, 20: 1.560},
            "Gemischtes Profil III": {5: 0.844, 11: 0.991, 15: 1.111, 20: 1.236},
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
def calc(address: str, roof_pitch: float, roof_height: float, terrain: str):

    try:

        lat, lon = geocode(address)
        h = elevation(lat, lon)

        snow_zone = get_zone(snow, lat, lon)
        wind_zone = get_zone(wind, lat, lon)

        snow_kn = snow_roof(snow_zone, h, roof_pitch)
        snow_exceptional = snow_kn * 2.3

        wind_kn = wind_pressure(wind_zone, roof_height, terrain)

        return {
            "snow_zone": snow_zone,
            "wind_zone": wind_zone,
            "snow_regular": round(snow_kn, 3),
            "snow_exceptional": round(snow_exceptional, 3),
            "wind_pressure": round(wind_kn, 3),
            "elevation": round(h, 1)
        }

    except Exception:

        return JSONResponse(
            status_code=200,
            content={"error": "Serverfehler — Eingabedaten prüfen"}
        )
