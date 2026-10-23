import os
import osmnx as ox
import geopandas as gpd
import pandas as pd
from rasterstats import zonal_stats
import warnings
from sklearn.preprocessing import MinMaxScaler

warnings.filterwarnings('ignore')

print("1. Downloading Nagpur Road Network (Vector data)...")
# CHANGED: dist=5000 is now dist=20000 to include outer highways and ring roads
G = ox.graph_from_point((21.1458, 79.0882), dist=20000, network_type='all')
nodes, edges = ox.graph_to_gdfs(G)
edges = edges.reset_index()

# Convert to UTM Zone 44N (meters) to draw the 10m buffer
edges_proj = edges.to_crs(epsg=32644)
buffered_geoms = edges_proj.geometry.buffer(10)

# THE FIX: Convert the buffered polygons BACK to EPSG:4326 (degrees) 
# so they match the satellite images downloaded from Earth Engine.
edges_buffered_gdf = gpd.GeoDataFrame(geometry=buffered_geoms, crs=32644)
edges_ready_for_raster = edges_buffered_gdf.to_crs(epsg=4326)

print("2. Extracting Real Satellite Data for every street (Raster to Vector)...")
def extract_raster_stats(raster_path):
    if not os.path.exists(raster_path):
        print(f"  [!] Missing {raster_path}. Did the download script finish?")
        return [0] * len(edges)
    
    # We now use 'edges_ready_for_raster' which is in degrees
    stats = zonal_stats(edges_ready_for_raster, raster_path, stats="mean", all_touched=True)
    return [s['mean'] if s['mean'] is not None else 0 for s in stats]

edges['lst'] = extract_raster_stats("data/raw/nagpur_lst.tif")
edges['ndvi'] = extract_raster_stats("data/raw/nagpur_ndvi.tif")
edges['slope'] = extract_raster_stats("data/raw/nagpur_slope.tif")
edges['population'] = extract_raster_stats("data/raw/nagpur_pop.tif")

edges['pm'] = 65.0 

print("3. Calculating AHP Suitability Scores...")
df = pd.DataFrame(edges[['osmid', 'lst', 'ndvi', 'pm', 'population', 'slope']])
df = df.rename(columns={'osmid': 'edge_id'})
df['edge_id'] = df['edge_id'].astype(str)

features = ["lst", "ndvi", "pm", "population", "slope"]
scaler = MinMaxScaler()
df_norm = pd.DataFrame(scaler.fit_transform(df[features]), columns=features)

df["composite_suitability_score"] = (
    (1 - df_norm["lst"]) * 0.35 +
    df_norm["ndvi"] * 0.25 +
    (1 - df_norm["pm"]) * 0.20 +
    (1 - df_norm["population"]) * 0.10 +
    (1 - df_norm["slope"]) * 0.10
)

os.makedirs("data/processed", exist_ok=True)
df.to_csv("data/processed/edge_suitability.csv", index=False)
print("Success! Real dataset saved to data/processed/edge_suitability.csv")
