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

def wind_pressure_full(
        wind_zone,
        height,
        terrain,
        altitude=0,
        pv_factor=1.6):

    vb0_map = {
        1: 22.5,
        2: 25.0,
        3: 27.5,
        4: 30.0
    }

    z0_map = {
        "Geländekategorie I": 0.003,
        "Geländekategorie II": 0.05,
        "Geländekategorie III": 0.3,
        "Geländekategorie IV": 1.0,
        "Gemischtes Profil I": 0.01,
        "Gemischtes Profil II": 0.15,
        "Gemischtes Profil III": 0.5
    }

    vb = vb0_map.get(wind_zone, 25)

    # altitude correction (NA Germany approx)
    vb = vb * (1 + altitude / 10000)

    z0 = z0_map.get(terrain, 0.3)

    z = max(height, 5)

    kr = 0.19 * (z0 / 0.05) ** 0.07

    cr = kr * math.log(z / z0)

    vm = vb * cr

    Iv = 1.0 / (cr)

    qp = (1 + 7 * Iv) * 0.5 * 1.25 * vm**2

    qp = qp * pv_factor

    return qp / 1000

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
