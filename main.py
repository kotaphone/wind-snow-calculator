from fastapi import FastAPI
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
    params = {"q": address, "format": "json", "limit": 1}
    headers = {"User-Agent": "wind-snow-calculator"}

    r = requests.get(url, params=params, headers=headers, timeout=10)
    data = r.json()

    if len(data) == 0:
        raise Exception("Address not found")

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


# ---------------- SNOW (PV TOOL MODEL) ----------------

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


# ⭐ PV realistic sliding model (IDENTICAL behaviour)

def mu_pv(angle):

    if angle <= 30:
        return 0.8

    if angle <= 45:
        return 0.8 - (angle - 30) * 0.0266667   # gives 0.4 at 45°

    if angle <= 60:
        return 0.4 - (angle - 45) * 0.0266667

    return 0


def snow_roof(zone, elevation, angle):

    sk_ground = snow_ground(zone, elevation)
    mu = mu_pv(angle)

    return sk_ground * mu


# ---------------- WIND (PV TOOL LIKE) ----------------

import math

def interp_log(h, h1, h2, v1, v2):

    if h <= h1:
        return v1
    if h >= h2:
        return v2

    r = math.log(h / h1) / math.log(h2 / h1)

    return v1 + (v2 - v1) * r


def wind_pressure(zone, height, terrain):

    # ====== TABELA PV TOOL ======

    table = {
        "2": {
            "Geländekategorie I": {5: 890, 11: 1034, 15: 1097, 20: 1159},
            "Geländekategorie II": {5: 695, 11: 839, 15: 904, 20: 969},
            "Geländekategorie III": {5: 586, 11: 644, 15: 709, 20: 775},
            "Geländekategorie IV": {5: 508, 11: 508, 15: 508, 20: 567},
            "Gemischtes Profil I": {5: 1315, 11: 1527, 15: 1620, 20: 1711},
            "Gemischtes Profil II": {5: 745, 11: 922, 15: 1002, 20: 1083},
            "Gemischtes Profil III": {5: 586, 11: 688, 15: 722, 20: 858},
        },

        "1": {
            "Geländekategorie I": {5: 721, 11: 838, 15: 889, 20: 939},
            "Geländekategorie II": {5: 563, 11: 680, 15: 732, 20: 785},
            "Geländekategorie III": {5: 475, 11: 521, 15: 574, 20: 628},
            "Geländekategorie IV": {5: 411, 11: 411, 15: 411, 20: 459},
            "Gemischtes Profil I": {5: 1315, 11: 1527, 15: 1620, 20: 1711},
            "Gemischtes Profil II": {5: 604, 11: 747, 15: 812, 20: 878},
            "Gemischtes Profil III": {5: 745, 11: 557, 15: 625, 20: 695},
        },

        "3": {
            "Geländekategorie I": {5: 1077, 11: 1251, 15: 1327, 20: 1402},
            "Geländekategorie II": {5: 841, 11: 1016, 15: 1094, 20: 1172},
            "Geländekategorie III": {5: 709, 11: 779, 15: 858, 20: 938},
            "Geländekategorie IV": {5: 615, 11: 614, 15: 615, 20: 686},
            "Gemischtes Profil I": {5: 1315, 11: 1527, 15: 1620, 20: 1711},
            "Gemischtes Profil II": {5: 902, 11: 1115, 15: 1213, 20: 1311},
            "Gemischtes Profil III": {5: 709, 11: 832, 15: 934, 20: 1039},
        },

        "4": {
            "Geländekategorie I": {5: 1282, 11: 1489, 15: 1580, 20: 1668},
            "Geländekategorie II": {5: 1000, 11: 1209, 15: 1302, 20: 1395},
            "Geländekategorie III": {5: 844, 11: 927, 15: 1021, 20: 1116},
            "Geländekategorie IV": {5: 731, 11: 731, 15: 731, 20: 817},
            "Gemischtes Profil I": {5: 1315, 11: 1528, 15: 1620, 20: 1711},
            "Gemischtes Profil II": {5: 1073, 11: 1328, 15: 1444, 20: 1560},
            "Gemischtes Profil III": {5: 844, 11: 991, 15: 1111, 20: 1236},
        }
    }

    zone = zone.replace("*", "")

    if zone not in table:
        return 600

    terrain_table = table[zone].get(terrain)

    if terrain_table is None:
        return 600

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

    lat, lon = geocode(address)
    h = elevation(lat, lon)

    snow_zone = get_zone(snow, lat, lon)
    wind_zone = get_zone(wind, lat, lon)

    snow_kn = snow_roof(snow_zone, h, roof_pitch)

    snow_regular = snow_kn * 1000
    snow_exceptional = snow_regular * 2.3

    wind_n = wind_pressure(wind_zone, roof_height, terrain)

    return {
        "snow_zone": snow_zone,
        "wind_zone": wind_zone,
        "snow_regular": round(snow_regular, 2),
        "snow_exceptional": round(snow_exceptional, 2),
        "wind_pressure": round(wind_n, 2),
        "elevation": round(h, 1)
    }
