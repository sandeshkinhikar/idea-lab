#!/usr/bin/env python3
"""Additive tile-export fallback for the preserved Earth Engine downloader.

Uses the same Nagpur ROI, image collections, periods, bands and scale factors as
scripts/1_download_rasters.py. It does not modify that original script. Every
GeoTIFF is mosaicked from small tiles and atomically replaced only after the
full raster has been downloaded and validated.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT / "data" / "raw"
ROI = (78.89, 20.96, 79.29, 21.33)


def _products(ee):
    roi = ee.Geometry.Rectangle(list(ROI))
    s2 = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
          .filterBounds(roi).filterDate("2023-04-01", "2023-05-31")
          .filterMetadata("CLOUDY_PIXEL_PERCENTAGE", "less_than", 10).median())
    l8 = (ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
          .filterBounds(roi).filterDate("2023-04-01", "2023-05-31").median())
    srtm = ee.Image("USGS/SRTMGL1_003").clip(roi)
    pop = (ee.ImageCollection("WorldPop/GP/100m/pop")
           .filterBounds(roi).filterDate("2020-01-01", "2021-01-01").mean())
    return [
        ("nagpur_ndvi.tif", s2.normalizedDifference(["B8", "B4"]).rename("NDVI"), 10),
        ("nagpur_lst.tif", l8.select("ST_B10").multiply(0.00341802).add(149.0).subtract(273.15).rename("LST"), 30),
        ("nagpur_slope.tif", ee.Terrain.slope(srtm).rename("Slope"), 30),
        ("nagpur_pop.tif", pop.rename("Population"), 100),
    ]


def _tiles(ee, splits: int):
    min_lon, min_lat, max_lon, max_lat = ROI
    for row in range(splits):
        south = min_lat + (max_lat - min_lat) * row / splits
        north = min_lat + (max_lat - min_lat) * (row + 1) / splits
        for col in range(splits):
            west = min_lon + (max_lon - min_lon) * col / splits
            east = min_lon + (max_lon - min_lon) * (col + 1) / splits
            yield row, col, ee.Geometry.Rectangle([west, south, east, north])


def _mosaic(tile_paths: list[Path], output_path: Path, rasterio, merge):
    if not tile_paths:
        raise RuntimeError(f"Earth Engine did not create any tiles for {output_path.name}.")
    datasets = []
    try:
        for path in tile_paths:
            if not path.is_file() or path.stat().st_size <= 4:
                raise RuntimeError(f"Earth Engine tile is missing or empty: {path.name}")
            datasets.append(rasterio.open(path))
        mosaic, transform = merge(datasets)
        profile = datasets[0].profile.copy()
        profile.update(driver="GTiff", height=mosaic.shape[1], width=mosaic.shape[2], transform=transform, count=mosaic.shape[0], compress="deflate", tiled=True)
        part = output_path.with_name(output_path.name + ".part.tif")
        with rasterio.open(part, "w", **profile) as target:
            target.write(mosaic)
        if part.stat().st_size <= 4:
            raise RuntimeError(f"Merged output is empty: {part.name}")
        os.replace(part, output_path)
    finally:
        for dataset in datasets:
            dataset.close()


def main() -> int:
    try:
        import ee
        import geemap
        import rasterio
        from rasterio.merge import merge
    except ImportError as exc:
        raise RuntimeError("Chunked Earth Engine export needs earthengine-api, geemap, and rasterio. Install `requirements-pipeline.txt`.") from exc
    project = os.getenv("EE_PROJECT_ID", "dataset-510306")
    try:
        ee.Initialize(project=project)
    except Exception as exc:
        raise RuntimeError(f"Earth Engine initialization/authentication failed for project {project!r}: {exc}. Run `earthengine authenticate` or configure a service-account credential before retrying.") from exc
    try:
        splits = int(os.getenv("GEE_TILE_SPLITS", "4"))
    except ValueError as exc:
        raise RuntimeError("GEE_TILE_SPLITS must be an integer between 2 and 20.") from exc
    if not 2 <= splits <= 20:
        raise RuntimeError("GEE_TILE_SPLITS must be between 2 and 20.")
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    tiles = list(_tiles(ee, splits))
    for name, image, scale in _products(ee):
        print(f"Exporting {name} in {len(tiles)} tiles at {scale} m; ROI={ROI}.", flush=True)
        with tempfile.TemporaryDirectory(prefix=f"{name}.tiles.", dir=OUTPUTS) as tmp_dir:
            temp = Path(tmp_dir)
            tile_paths = []
            for row, col, geometry in tiles:
                path = temp / f"tile_{row:02d}_{col:02d}.tif"
                try:
                    geemap.ee_export_image(image.clip(geometry), filename=str(path), scale=scale, region=geometry, file_per_band=False)
                except Exception as exc:
                    raise RuntimeError(f"Tile {row + 1}/{splits} × {col + 1}/{splits} failed for {name}: {exc}. Check Earth Engine credentials, project access, and quotas.") from exc
                if path.is_file() and path.stat().st_size > 4:
                    tile_paths.append(path)
                else:
                    raise RuntimeError(f"Earth Engine did not return a valid tile for {name} at row {row}, column {col}.")
                print(f"  Tile row={row + 1}/{splits}, column={col + 1}/{splits}: {path.stat().st_size} bytes.", flush=True)
            destination = OUTPUTS / name
            _mosaic(tile_paths, destination, rasterio, merge)
            with rasterio.open(destination) as verified:
                if verified.count < 1 or verified.width < 1 or verified.height < 1:
                    raise RuntimeError(f"Mosaic verification failed for {destination}.")
                print(f"Verified {destination.relative_to(ROOT)} ({verified.width}×{verified.height}, {destination.stat().st_size} bytes).", flush=True)
    print("All four chunk-mosaicked GeoTIFFs verified.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
