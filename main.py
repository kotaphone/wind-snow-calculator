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


# ---------------- SNOW ENGINEERING ----------------

def snow_ground(zone, elevation):
    """
    DIN EN 1991-1-3 Germany
    """

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
        return 0.8

    if elevation <= limits[z]:
        return base[z]

    # height formula
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


def mu(angle):
    if angle <= 30:
        return 0.8
    elif angle <= 60:
        return 0.8 * (60 - angle) / 30
    else:
        return 0


def snow_roof(zone, elevation, angle):

    sk_ground = snow_ground(zone, elevation)

    m = mu(angle)

    # roof reduction (PV planner like)
    reduction = 0.5

    sk_roof = sk_ground * m * reduction

    return sk_roof


# ---------------- WIND (simple realistic) ----------------

def wind_pressure(zone, height):

    base = {
        "1": 0.5,
        "2": 0.65,
        "3": 0.8,
        "4": 0.95
    }

    if zone not in base:
        vb = 0.65
    else:
        vb = base[zone]

    qp = vb * (1 + height / 20)

    return qp


# ---------------- API ----------------

@app.get("/calc")
def calc(address: str, roof_pitch: float, roof_height: float):

    lat, lon = geocode(address)
    h = elevation(lat, lon)

    snow_zone = get_zone(snow, lat, lon)
    wind_zone = get_zone(wind, lat, lon)

    snow_kn = snow_roof(snow_zone, h, roof_pitch)

    exceptional_kn = snow_kn * 2.3

    # konwersja na N/m²
    snow_regular = snow_kn * 1000
    snow_exceptional = exceptional_kn * 1000

    wind_kn = wind_pressure(wind_zone, roof_height)
    wind_n = wind_kn * 1000

    return {
        "snow_zone": snow_zone,
        "wind_zone": wind_zone,
        "snow_regular": round(snow_regular, 2),
        "snow_exceptional": round(snow_exceptional, 2),
        "wind_pressure": round(wind_n, 2),
        "elevation": round(h, 1)
    }
