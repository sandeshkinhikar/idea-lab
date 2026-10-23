import ee
import geemap
import os

# 1. Authenticate and Initialize Earth Engine
project_id = 'dataset-510306'

try:
    ee.Initialize(project=project_id)
except Exception as e:
    ee.Authenticate()
    ee.Initialize(project=project_id)

os.makedirs("data/raw", exist_ok=True)

# 2. Define Expanded Nagpur Bounding Box (~20km radius from Zero Mile)
nagpur_roi = ee.Geometry.Rectangle([78.89, 20.96, 79.29, 21.33])

print("Fetching Sentinel-2 Greenery (NDVI)...")
s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
    .filterBounds(nagpur_roi) \
    .filterDate('2023-04-01', '2023-05-31') \
    .filterMetadata('CLOUDY_PIXEL_PERCENTAGE', 'less_than', 10) \
    .median()
ndvi = s2.normalizedDifference(['B8', 'B4']).rename('NDVI')
geemap.ee_export_image(ndvi, filename="data/raw/nagpur_ndvi.tif", scale=10, region=nagpur_roi)

print("Fetching Landsat 8 Heat Map (LST)...")
l8 = ee.ImageCollection("LANDSAT/LC08/C02/T1_L2") \
    .filterBounds(nagpur_roi) \
    .filterDate('2023-04-01', '2023-05-31') \
    .median()
# Convert thermal band to Celsius
lst = l8.select('ST_B10').multiply(0.00341802).add(149.0).subtract(273.15).rename('LST')
geemap.ee_export_image(lst, filename="data/raw/nagpur_lst.tif", scale=30, region=nagpur_roi)

print("Fetching SRTM Elevation (Slope)...")
srtm = ee.Image('USGS/SRTMGL1_003').clip(nagpur_roi)
slope = ee.Terrain.slope(srtm).rename('Slope')
geemap.ee_export_image(slope, filename="data/raw/nagpur_slope.tif", scale=30, region=nagpur_roi)

print("Fetching WorldPop Population Density...")
pop = ee.ImageCollection("WorldPop/GP/100m/pop") \
    .filterBounds(nagpur_roi) \
    .filterDate('2020-01-01', '2021-01-01') \
    .mean().rename('Population')
geemap.ee_export_image(pop, filename="data/raw/nagpur_pop.tif", scale=100, region=nagpur_roi)

print("All satellite rasters downloaded successfully to data/raw/")
