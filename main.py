from fastapi import FastAPI
import requests
import geopandas as gpd
from shapely.geometry import Point

app = FastAPI()

snow = gpd.read_file("snow.kml", driver="LIBKML")
wind = gpd.read_file("wind.kml", driver="LIBKML")

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
        raise Exception("Address not found")

    return float(data[0]["lat"]), float(data[0]["lon"])

def elevation(lat, lon):
    url = f"https://api.open-elevation.com/api/v1/lookup?locations={lat},{lon}"
    r = requests.get(url).json()
    return r["results"][0]["elevation"]

def get_zone(gdf, lat, lon):
    pt = Point(lon, lat)
    res = gdf[gdf.contains(pt)]
    if len(res) > 0:
        return res.iloc[0]["Name"]
    return "unknown"

@app.get("/calc")
def calc(address: str, roof_pitch: float, roof_height: float):

    lat, lon = geocode(address)
    h = elevation(lat, lon)

    snow_zone = get_zone(snow, lat, lon)
    wind_zone = get_zone(wind, lat, lon)

    # proste wzory demo
    snow_load = 0.8 + roof_pitch * 0.01
    wind_load = 0.5 + roof_height * 0.02

    return {
        "snow_zone": snow_zone,
        "wind_zone": wind_zone,
        "snow_load": round(snow_load,2),
        "wind_load": round(wind_load,2),
        "elevation": h
    }
