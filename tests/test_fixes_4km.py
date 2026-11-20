#!/usr/bin/env python
"""
Self-contained regression tests (synthetic data only, no downloads) for the
fixes made after the 4 km North Slope test run (Nov 2026):

  1  region create --resolution (scalar or tuple)
  2  CMIP6 georeferencing (stale GeoTransform) and NaN-initialised warps
  4  inclusive baseline years
  5  CMIP6 calendars other than noleap; wrap-aware lon selection
  8  robust multiplicative correction factors
  10 cf_units based unit handling for correction factors
  13 SoilGrids nodata masking and depth weighting
  14 alpine CMTs (CMT20/CMT21), flagged unmapped classes
  16 integer veg_class / drainage_class storage
  18 no fixed /tmp paths in from_vegetation
"""
import warnings

import cftime
import geopandas as gpd
import numpy as np
import pandas as pd
import pyproj
import pytest
import shapely
import xarray as xr
from osgeo import gdal

from temds import corrections, climate_variables, gdal_tools, pangeo_tools
from temds.datasources import cmip6, soil_texture
from temds.datasources.dataset import TEMDataset, YearlyDataset
from temds.datasources.timeseries import YearlyTimeSeries
from temds.region.mask import Mask, normalize_resolution
from temds.region.region import Region
from temds.region import tools as region_tools

# 200 x 200 km North Slope box used in the cluster test (EPSG:6931, 4 km)
NS_BOX = (-1116000, 1936000, -916000, 2136000)
EASE = 'EPSG:6931'


def ns_boundary(box=NS_BOX):
    return gpd.GeoDataFrame(geometry=[shapely.box(*box)], crs=EASE)


def ns_region(res=4000, box=NS_BOX):
    return Region(ns_boundary(box), resolution=res, name='northslope')


# --------------------------------------------------------------------- 1
@pytest.mark.parametrize('res', [4000, 4000.0, np.float64(4000), (4000, -4000), [4000, 4000]])
def test_mask_from_extent_accepts_scalar_and_pair(res):
    mask = Mask.from_extent(ns_boundary(), res)
    assert mask.shape == (50, 50)
    assert mask.transform == (NS_BOX[0], 4000.0, 0.0, NS_BOX[3], 0.0, -4000.0)
    assert (mask.raster.ReadAsArray() == 1).all()


def test_normalize_resolution_and_align():
    assert normalize_resolution(10) == (10.0, 10.0)
    assert normalize_resolution((10, -20)) == (10.0, 20.0)
    with pytest.raises(TypeError):
        normalize_resolution('4000')
    with pytest.raises(ValueError):
        normalize_resolution(0)
    g = gpd.GeoSeries([shapely.box(1, 1, 9, 9)], crs=EASE)
    a = region_tools.align_to_resolution(g, (4, -4))
    assert tuple(a.total_bounds) == (0, 0, 12, 12)


def test_region_with_non_multiple_extent():
    # 201 km wide -> aligned outward to 204 km (51 px)
    box = (NS_BOX[0], NS_BOX[1], NS_BOX[2] + 1000, NS_BOX[3])
    mask = Mask.from_extent(ns_boundary(box), 4000)
    assert mask.shape == (51, 50)


# --------------------------------------------------------------------- 2
def _linear_field(lon, lat):
    """A field that bilinear interpolation reproduces exactly"""
    return 250.0 + 0.8 * lat + 0.5 * lon


def _write_fake_cmip6(path, year=2000, calendar='noleap', stale_gt=True):
    """Write tas/pr/rsds/huss/psl files like `download cmip6-daily` used to
    (0..360 longitudes, an identity GeoTransform on spatial_ref)."""
    lon = np.arange(180.0, 232.5, 1.25)          # 0..360 convention
    lat = np.arange(60.0, 80.1, 0.942408)        # CESM2-like spacing
    times = xr.date_range(f'{year}-01-01', f'{year}-12-31', freq='D',
                          calendar=calendar, use_cftime=True)
    shape = (times.size, lat.size, lon.size)
    LON, LAT = np.meshgrid(((lon + 180) % 360) - 180, lat)
    base = _linear_field(LON, LAT)
    fields = {
        'tas': np.broadcast_to(base, shape),
        'pr': np.full(shape, 1e-5),
        'rsds': np.full(shape, 100.0),
        'huss': np.full(shape, 0.003),
        'psl': np.full(shape, 101000.0),
    }
    for var, data in fields.items():
        ds = xr.Dataset(
            {var: (('time', 'lat', 'lon'), data.astype('float32'))},
            coords={'time': times, 'lat': lat, 'lon': lon},
        )
        ds.coords['lon_bnds'] = (('lon', 'bnds'), np.stack([lon - 0.625, lon + 0.625], axis=1))
        if stale_gt:
            ds.coords['spatial_ref'] = xr.DataArray(
                0, attrs={'crs_wkt': pyproj.CRS('EPSG:4326').to_wkt(),
                          'spatial_ref': pyproj.CRS('EPSG:4326').to_wkt(),
                          'GeoTransform': '0.0 1.0 0.0 0.0 0.0 1.0'})
            ds[var].attrs['grid_mapping'] = 'spatial_ref'
        ds.to_netcdf(path / f'cmip6-day-FAKE-historical-{var}.nc')


def test_transform_from_coords():
    ds = xr.Dataset(coords={'lon': np.arange(-10.0, 10.0, 2.5),
                            'lat': np.arange(60.0, 70.0, 0.5)})
    t = gdal_tools.transform_from_coords(ds)
    assert t.a == pytest.approx(2.5) and t.c == pytest.approx(-11.25)
    assert t.e == pytest.approx(0.5) and t.f == pytest.approx(59.75)
    irregular = xr.Dataset(coords={'x': [0.0, 1.0, 3.0], 'y': [0.0, 1.0, 2.0]})
    with pytest.warns(UserWarning):
        gdal_tools.transform_from_coords(irregular)


@pytest.mark.parametrize('calendar', ['noleap', 'proleptic_gregorian'])
def test_cmip6_georeferencing_matches_independent_interpolation(tmp_path, calendar):
    _write_fake_cmip6(tmp_path, calendar=calendar)
    yd = YearlyDataset.from_cmip6(2000, tmp_path)
    assert yd.dataset.time.size == 365
    # transform derived from the shifted/sorted coordinates, not the stale one
    t = yd.dataset.rio.transform()
    assert t.a == pytest.approx(1.25)
    assert t.c == pytest.approx(-180.0 - 0.625)

    region = ns_region()
    region.import_datasource('cmip', yd)
    out = region.data['cmip'].dataset['tair_avg'].isel(time=0).values

    X, Y = np.meshgrid(region.data['cmip'].dataset.x.values,
                       region.data['cmip'].dataset.y.values)
    lon, lat = pyproj.Transformer.from_crs(EASE, 'EPSG:4326', always_xy=True).transform(X, Y)
    expected = _linear_field(lon, lat) - 273.15
    assert np.isfinite(out).all()
    assert not (out == 0).any()
    # bilinear interpolation of a linear field is exact (to float32)
    # bilinear interpolation of a linear field is exact up to GDAL's
    # approximate transformer; a 1-cell (1.25 deg) shift would be 0.6 K
    assert np.abs(out - expected).max() < 0.05


def test_clip_fills_uncovered_pixels_with_nan():
    """pixels outside the source must be NaN, not 0 / the previous variable"""
    src_crs = pyproj.CRS('EPSG:4326').to_wkt()
    source = gdal_tools.empty_dataset(4, 4, src_crs, (0, 1, 0, 4, 0, -1), 1)
    dest = gdal_tools.empty_dataset(4, 4, src_crs, (2, 1, 0, 4, 0, -1), 1)
    arrays = gdal_tools.clip_opt_2(
        dest, source,
        {'a': np.full((4, 4), 5.0, 'float32'), 'b': np.full((4, 4), 7.0, 'float32')},
        'near', True, False,
    )
    assert np.all(arrays['b'][:, :2] == 7.0)
    assert np.isnan(arrays['b'][:, 2:]).all()   # was 0 / 5.0 before


def test_get_extent_densified_and_pole():
    r = ns_region()
    naive = ns_boundary().to_crs(4326).total_bounds
    dense = r.get_extent(4326)
    assert dense[3] >= naive[3]  # top edge bulges north in lon/lat
    pole = Region(ns_boundary((-500000, -500000, 500000, 500000)), resolution=50000)
    assert tuple(pole.get_extent(4326, 1)[[0, 2, 3]]) == (-180, 180, 90)


# --------------------------------------------------------------------- 4
def _const_year(year, value, var='tair_avg'):
    ds = xr.Dataset(
        {var: (('time', 'y', 'x'), np.full((365, 2, 2), value, 'float32'))},
        coords={'time': np.arange(365), 'y': [1.5, 0.5], 'x': [0.5, 1.5]},
    )
    ds[var].attrs['units'] = 'celsius'
    ds.rio.write_crs(EASE, inplace=True)
    return YearlyDataset(year, ds)


def test_baseline_years_inclusive():
    ts = YearlyTimeSeries([_const_year(y, float(y)) for y in range(2000, 2004)])
    base = ts.create_climate_baseline(2000, 2002, variables=['tair_avg'])
    assert float(base['tair_avg'].mean()) == pytest.approx(2001.0)  # 2000.5 before
    assert base.attrs['baseline_end_year'] == 2002
    with pytest.raises(ValueError):
        ts.create_climate_baseline(2000, 2010, variables=['tair_avg'])


# --------------------------------------------------------------------- 5
@pytest.mark.parametrize('calendar', ['noleap', 'proleptic_gregorian', 'standard', '360_day'])
def test_time_bounds_match_any_calendar(calendar):
    times = xr.DataArray(
        xr.date_range('2014-12-25', '2015-01-05', calendar=calendar, use_cftime=True),
        dims='time')
    start = pangeo_tools._match_calendar(cftime.DatetimeNoLeap(2015, 1, 1), times)
    sel = (times >= start).values
    assert sel.sum() == 5


def test_lon_selection_wraps():
    lon = np.arange(0, 360, 10.0)
    sel = pangeo_tools.lon_selection(lon, 340, 380)
    assert set(lon[sel]) == {340, 350, 0, 10, 20}
    assert pangeo_tools.lon_selection(lon, 0, 360).all()


def test_cmip6_default_members_and_checks():
    assert cmip6.default_ensemble('MRI-ESM2-0') == 'r1i1p1f1'
    assert cmip6.default_ensemble('CESM2') == 'r4i1p1f1'
    req, opt = cmip6.check_variables(['pr', 'tas'])
    assert 'rsds' in req and 'tasmin' in opt
    items = pd.DataFrame({
        'member_id': ['r1'] * 5 + ['r4'],
        'variable_id': cmip6.REQUIRED_SOURCE_VARS + ['pr']})
    assert cmip6.members_with_variables(items, cmip6.REQUIRED_SOURCE_VARS) == ['r1']
    aliases = climate_variables.aliases_for(cmip6.NAME, 'dict')
    assert aliases['tair_min'] == 'tasmin' and aliases['wind'] == 'sfcWind'


# --------------------------------------------------------------------- 8
def test_multiplicative_factors_robust():
    baseline = xr.DataArray([1e-6, 0.0, 50.0, 2.0, np.nan])
    reference = xr.DataArray([1e-3, 0.0, 100.0, 1000.0, 5.0])
    f = corrections.LOOKUP['nirr'](baseline, reference)
    assert f.values[0] == 1.0 and f.values[1] == 1.0     # tiny baseline -> no change
    assert f.values[2] == pytest.approx(2.0)
    assert f.values[3] == corrections.MAX_FACTOR['nirr']  # clipped (was 500)
    assert np.isnan(f.values[4])


# --------------------------------------------------------------------- 10
def test_correction_units_equivalent_and_written():
    b = xr.DataArray([1.0, 2.0], attrs={'units': 'celsius'})
    r = xr.DataArray([2.0, 4.0], attrs={'units': 'degree_Celsius'})
    add = corrections.calculate('tair_avg', b, r)
    assert add.attrs['units'] == 'degree_Celsius'
    bk = xr.DataArray([274.15], attrs={'units': 'kelvin'})
    rc = xr.DataArray([2.0], attrs={'units': 'celsius'})
    assert float(corrections.calculate('tair_avg', bk, rc)) == pytest.approx(1.0)
    m = corrections.calculate(
        'prec', xr.DataArray([10.0], attrs={'units': 'mm'}),
        xr.DataArray([20.0], attrs={'units': '0.001 meter'}))
    assert m.attrs['units'] == '1' and float(m) == pytest.approx(2.0)
    with pytest.raises(ValueError):
        corrections.calculate(
            'prec', xr.DataArray([1.0], attrs={'units': 'kg'}),
            xr.DataArray([1.0], attrs={'units': 'mm'}))


# --------------------------------------------------------------------- 13
def _write_tif(path, array, gt, crs=EASE, nodata=None, dtype=gdal.GDT_Int16):
    drv = gdal.GetDriverByName('GTiff')
    ds = drv.Create(str(path), array.shape[1], array.shape[0], 1, dtype)
    ds.SetGeoTransform(gt)
    ds.SetProjection(pyproj.CRS(crs).to_wkt())
    band = ds.GetRasterBand(1)
    if nodata is not None:
        band.SetNoDataValue(nodata)
    band.WriteArray(array)
    ds.FlushCache()
    del ds


def test_depth_weighted_percent():
    layers = [np.array([200.0]), np.array([300.0]), np.array([400.0])]
    got = soil_texture.depth_weighted_percent(layers, [15, 30, 40])
    old = (200 * 15 + 300 * 30 + 400 * 40) / (150 + 300 + 400)
    assert got[0] == pytest.approx(old)


def test_soil_texture_nodata_masked(tmp_path):
    region = ns_region()
    # 1 km source grid covering the region; top-left 20x20 km is nodata
    n = 200
    gt = (NS_BOX[0], 1000, 0, NS_BOX[3], 0, -1000)
    for i, X in enumerate(['clay', 'sand', 'silt']):
        for depth in soil_texture.DEPTHS:
            a = np.full((n, n), (i + 2) * 100, 'int16')
            a[:20, :20] = soil_texture.NODATA
            _write_tif(tmp_path / soil_texture.file_name(X, depth), a, gt,
                       nodata=soil_texture.NODATA)
    st = TEMDataset.from_soil_texture(tmp_path, region, tmp_path)
    clay = st.dataset['pct_clay'].values
    assert np.isnan(clay[:5, :5]).all()          # was -3276.8
    assert np.nanmin(clay) == pytest.approx(20.0)
    assert np.isfinite(clay[10:, 10:]).all()


# --------------------------------------------------------------------- 16
def test_integer_variables_roundtrip(tmp_path):
    ds = xr.Dataset(
        {'veg_class': (('y', 'x'), np.array([[4, 5], [0, -9999]], 'int32')),
         'tair': (('y', 'x'), np.ones((2, 2), 'float32'))},
        coords={'y': [1.5, 0.5], 'x': [0.5, 1.5]})
    ds.rio.write_crs(EASE, inplace=True)
    TEMDataset(ds).save(tmp_path / 'veg.nc')
    back = xr.open_dataset(tmp_path / 'veg.nc', mask_and_scale=False)
    assert back['veg_class'].dtype == np.int32
    assert back['veg_class'].attrs['_FillValue'] == -9999
    assert back['tair'].dtype == np.float32


# ------------------------------------------------------------ 14, 16, 18
def _fake_veg_inputs(tmp_path, region):
    """Tiny land cover + class csv + political + ecoregion layers"""
    minx, miny, maxx, maxy = NS_BOX
    # 1 km land cover: left half 'Prostrate-shrub tundra' (61),
    # right half 'Graminoid tundra' (63), one block of 'Cedar forest' (22)
    lc = np.full((200, 200), 61, 'int16')
    lc[:, 100:] = 63
    lc[150:, :40] = 22
    _write_tif(tmp_path / 'lc.tif', lc, (minx, 1000, 0, maxy, 0, -1000))
    pd.DataFrame({'value': [0, 22, 61, 63],
                  'classname ': ['Unclassified', 'Cedar forest',
                                 'Prostrate-shrub tundra', 'Graminoid tundra'],
                  'groupname': ['u', 'n', 's', 'h']}).to_csv(tmp_path / 'classes.csv', index=False)
    whole = shapely.box(minx - 1e5, miny - 1e5, maxx + 1e5, maxy + 1e5)
    pol = gpd.GeoDataFrame(
        {'shapeName': ['Alaska'], 'shapeGroup': ['USA'], 'shapeType': ['ADM1'],
         'shapeID': ['x']}, geometry=[whole], crs=EASE).to_crs(4326)
    pol.to_file(tmp_path / 'pol.gpkg')
    eco_cols = dict(OBJECTID=[1, 2], BIOME_NUM=[11, 11], ECO_BIOME_=['a', 'b'],
                    NNH=[1, 1], ECO_ID=[1, 2], SHAPE_LENG=[1, 1], SHAPE_AREA=[1, 1],
                    NNH_NAME=['n', 'n'], COLOR=['c', 'c'], COLOR_BIO=['c', 'c'],
                    COLOR_NNH=['c', 'c'], LICENSE=['l', 'l'],
                    BIOME_NAME=['Tundra', 'Tundra'], REALM=['Nearctic', 'Nearctic'],
                    ECO_NAME=['Arctic foothills tundra', 'Brooks-British Range tundra'])
    eco = gpd.GeoDataFrame(
        eco_cols,
        geometry=[shapely.box(minx - 1e5, miny - 1e5, maxx + 1e5, (miny + maxy) / 2),
                  shapely.box(minx - 1e5, (miny + maxy) / 2, maxx + 1e5, maxy + 1e5)],
        crs=EASE).to_crs(4326)
    eco.to_file(tmp_path / 'eco.gpkg')


def test_vegetation_alpine_int_and_tempdir(tmp_path, monkeypatch):
    region = ns_region()
    topo = xr.Dataset(
        {'drainage_class': (('y', 'x'), np.zeros((50, 50), 'int32'))},
        coords={'y': NS_BOX[3] - 2000 - 4000 * np.arange(50),
                'x': NS_BOX[0] + 2000 + 4000 * np.arange(50)})
    topo.rio.write_crs(EASE, inplace=True)
    region.data['topo'] = TEMDataset(topo)
    _fake_veg_inputs(tmp_path, region)

    # any write to a fixed /tmp/*_temp_from_veg.tif path fails the test
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    monkeypatch.setenv('TMPDIR', str(scratch))
    import tempfile
    monkeypatch.setattr(tempfile, 'tempdir', None)

    veg = TEMDataset.from_vegetation(
        tmp_path / 'lc.tif', tmp_path / 'classes.csv', tmp_path / 'pol.gpkg',
        tmp_path / 'eco.gpkg', region, tmp_path)
    vc = veg.dataset['veg_class'].values
    assert vc.dtype == np.int32
    found = set(np.unique(vc))
    # 'Brooks-British Range tundra' contains 'range' -> alpine
    assert 20 in found      # alpine shrub tundra (never assigned before)
    assert 21 in found      # alpine tussock tundra
    assert 4 in found and 5 in found
    assert 0 in found       # Cedar forest -> CMT00 (flagged in the log)
    assert list(scratch.iterdir()) == []   # scratch dir cleaned up
