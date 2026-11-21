#!/usr/bin/env python
"""
Synthetic-data tests (no CDS access) for the ERA5 daily path, written after
the North Slope ERA5 test run (Nov 2026).
"""
import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import shapely
import xarray as xr

from temds.datasources import era5_daily
from temds.region.region import Region

NS_BOX = (-1116000, 1936000, -916000, 2136000)
EASE = 'EPSG:6931'


def ns_region():
    bnd = gpd.GeoDataFrame(geometry=[shapely.box(*NS_BOX)], crs=EASE)
    return Region(bnd, resolution=4000, name='northslope')


# ---------------------------------------------------- region-bounded area
def test_bounds_from_extent_buffer_and_snap():
    area = era5_daily.bounds_from_extent((-156.72, 68.31, -150.11, 70.70), 1.0)
    assert area == [71.75, -157.75, 67.25, -149.0]
    n, w, s, e = area
    for v in area:
        assert (v / 0.25) == int(v / 0.25)


def test_bounds_from_extent_clips_and_wraps():
    assert era5_daily.bounds_from_extent((-179.8, 85, 179.8, 89.9), 1) == [90.0, -180.0, 84.0, 180.0]
    # antimeridian / out of range longitudes -> all longitudes
    assert era5_daily.bounds_from_extent((-179.5, 60, -170, 65), 1)[1:4:2] == [-180.0, 180.0]


def test_bounds_from_region_contains_region():
    r = ns_region()
    minx, miny, maxx, maxy = r.get_extent(4326)
    n, w, s, e = era5_daily.bounds_from_extent((minx, miny, maxx, maxy))
    assert n >= maxy + 1 and s <= miny - 1 and w <= minx - 1 and e >= maxx + 1
    # much smaller than the old default 30-90N band
    assert (n - s) * (e - w) < 0.01 * (60 * 360)
