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


# ------------------------------------------------ CDS errors are surfaced
def test_download_error_carries_cds_message(tmp_path, monkeypatch):
    def boom(where, collection_id, request):
        raise RuntimeError('403 Client Error: required licences not accepted')
    monkeypatch.setattr(era5_daily.cdsapi_tools, 'download', boom)
    with pytest.raises(era5_daily.ERA5DownloadError, match='licences not accepted') as info:
        era5_daily.download_variable_for_year_month(tmp_path, 'tair_avg', 2000, 2, bounds=[71, -158, 67, -149])
    assert '2m_temperature 2000-02' in str(info.value)
    assert isinstance(info.value.__cause__, RuntimeError)


def test_download_passes_area_and_skips_existing(tmp_path, monkeypatch):
    seen = {}
    def fake(where, collection_id, request):
        seen.update(request)
        where.write_text('x')
    monkeypatch.setattr(era5_daily.cdsapi_tools, 'download', fake)
    f, status = era5_daily.download_variable_for_year_month(tmp_path, 'prec', 2000, 1, bounds=[71, -158, 67, -149])
    assert status == 'complete' and seen['area'] == [71, -158, 67, -149]
    assert seen['variable'] == ['total_precipitation'] and seen['daily_statistic'] == 'daily_sum'
    f, status = era5_daily.download_variable_for_year_month(tmp_path, 'prec', 2000, 1)
    assert status == 'skipped'


# ------------------------------------------- exact preprocess file names
def test_find_yearly_files_ignores_partials(tmp_path):
    for v in era5_daily.API_VARIABLES:
        (tmp_path / era5_daily.yearly_file_name(v, 2000)).write_text('x')
    (tmp_path / '2000-01-2m_temperature.nc').write_text('partial')
    (tmp_path / '2001-2m_temperature.nc').write_text('other year')
    (tmp_path / 'era5_probe_2000-07-01_t2m.nc').write_text('probe')
    files = era5_daily.find_yearly_files(tmp_path, 2000)
    assert sorted(f.name for f in files) == sorted([
        '2000-2m_dewpoint_temperature.nc', '2000-2m_temperature.nc',
        '2000-surface_solar_radiation_downwards.nc', '2000-total_precipitation.nc'])


def test_find_yearly_files_reports_missing(tmp_path):
    (tmp_path / '2000-2m_temperature.nc').write_text('x')
    with pytest.raises(FileNotFoundError, match='total_precipitation'):
        era5_daily.find_yearly_files(tmp_path, 2000)


# --------------------------------------------------- leap-day / noleap
def synthetic_downloads(year=2000, ny=3, nx=4):
    time = pd.date_range(f'{year}-01-01', f'{year}-12-31', freq='D')
    lat = np.linspace(70.5, 70.0, ny)
    lon = np.linspace(-155, -154.25, nx)
    shape = (len(time), ny, nx)
    doy = np.arange(len(time))[:, None, None] * np.ones(shape)
    coords = dict(valid_time=time, latitude=lat, longitude=lon)
    dims = ('valid_time', 'latitude', 'longitude')
    mk = lambda name, data: xr.Dataset({name: (dims, data)}, coords=coords)
    return [
        mk('t2m', 273.15 + doy),             # K -> degC == day index
        mk('d2m', np.full(shape, 273.15)),   # 0 degC dewpoint
        mk('ssrd', np.full(shape, 86400.0 * 100)),  # J/m2/day -> 100 W/m2
        mk('tp', np.full(shape, 0.002)),     # m/day -> 2 mm/day
    ]


def test_merge_for_year_drops_feb29_and_converts_units():
    merged = era5_daily.merge_for_year(2000, synthetic_downloads(2000))
    ds = merged.dataset
    assert ds.sizes['time'] == 365
    assert ds.time.dt.calendar == 'noleap'
    assert not ((ds.time.dt.month == 2) & (ds.time.dt.day == 29)).any()
    # Mar 1 is day index 60 in the 366-day input (Feb 29 = 59 removed),
    # and must be the 60th (0-based 59) step of the noleap year.
    assert float(ds['tair_avg'].isel(time=59)[0, 0]) == pytest.approx(60.0)
    assert float(ds['tair_avg'].isel(time=-1)[0, 0]) == pytest.approx(365.0)  # Dec 31 kept
    np.testing.assert_allclose(ds['prec'].values, 2.0)
    np.testing.assert_allclose(ds['nirr'].values, 100.0)
    np.testing.assert_allclose(ds['vapo'].values, 0.61078, rtol=1e-6)


def test_merge_for_year_non_leap_unchanged():
    ds = era5_daily.merge_for_year(2001, synthetic_downloads(2001)).dataset
    assert ds.sizes['time'] == 365
    assert float(ds['tair_avg'].isel(time=-1)[0, 0]) == pytest.approx(364.0)


# ------------------------------------- preprocess era5-daily --use-region
def write_synthetic_downloads(where, year=2000):
    """Yearly files on a 0.25 deg grid covering the North Slope box + buffer"""
    time = pd.date_range(f'{year}-01-01', f'{year}-12-31', freq='D')
    lat = np.arange(71.75, 67.25 - 0.01, -0.25)
    lon = np.arange(-157.75, -149.0 + 0.01, 0.25)
    shape = (len(time), len(lat), len(lon))
    coords = dict(valid_time=time, latitude=lat, longitude=lon)
    dims = ('valid_time', 'latitude', 'longitude')
    doy = np.arange(len(time))[:, None, None] * np.ones(shape)
    values = {'t2m': 263.15 + 0 * doy, 'd2m': np.full(shape, 260.0),
              'ssrd': np.full(shape, 86400.0 * 50), 'tp': np.full(shape, 0.001)}
    names = {'t2m': 'tair_avg', 'd2m': 'dewpoint', 'ssrd': 'nirr', 'tp': 'prec'}
    for src, std in names.items():
        xr.Dataset({src: (dims, values[src].astype('float32'))}, coords=coords)\
            .to_netcdf(where / era5_daily.yearly_file_name(std, year))


def test_preprocess_era5_use_region_imports_to_region(tmp_path):
    from typer.testing import CliRunner
    from temds.cli.main import app
    import yaml

    bnd = tmp_path / 'boundary.geojson'
    gpd.GeoDataFrame(geometry=[shapely.box(*NS_BOX)], crs=EASE).to_file(bnd)
    region_dir = tmp_path / 'northslope'
    runner = CliRunner()
    res = runner.invoke(app, ['region', 'create', str(region_dir), str(bnd), '--resolution', '4000'])
    assert res.exit_code == 0, res.output

    downloads = tmp_path / 'downloads'
    downloads.mkdir()
    write_synthetic_downloads(downloads)
    res = runner.invoke(app, [
        '--use-region', str(region_dir), '--no-load-all',
        'preprocess', 'era5-daily', str(tmp_path / 'unused'), str(downloads), '2000', '2000'])
    assert res.exit_code == 0, res.output

    assert not (region_dir / 'ERA5-').exists()
    out = region_dir / 'era5' / 'era5-2000.nc'
    assert out.exists()
    manifest = yaml.safe_load((region_dir / 'manifest.yml').read_text())
    assert 'era5' in manifest['data']
    with xr.open_dataset(out) as ds:
        assert (ds.sizes['y'], ds.sizes['x'], ds.sizes['time']) == (50, 50, 365)
        np.testing.assert_allclose(ds['tair_avg'].values, -10.0, atol=1e-4)
        np.testing.assert_allclose(ds['prec'].values, 1.0, atol=1e-4)


# ----------------------------------------- single-year TEM climate export
def region_daily_dataset(year=2000, value=-10.0):
    import cftime
    time = xr.date_range(f'{year}-01-01', periods=365, freq='D', calendar='noleap', use_cftime=True)
    x = NS_BOX[0] + 2000 + 4000 * np.arange(50)
    y = NS_BOX[3] - 2000 - 4000 * np.arange(50)
    shape = (365, 50, 50)
    ds = xr.Dataset(
        {v: (('time', 'y', 'x'), np.full(shape, val, dtype='float32'))
         for v, val in dict(tair_avg=value, prec=1.0, nirr=80.0, vapo=0.3).items()},
        coords=dict(time=time, y=y, x=x))
    for v, u in dict(tair_avg='celsius', prec='mm', nirr='W/m^2', vapo='kPa').items():
        ds[v].attrs['units'] = u
    ds.attrs['TEMDS_version'] = 'test'
    ds.rio.write_crs(EASE, inplace=True)
    return ds


def test_export_single_year_climate(tmp_path):
    from temds.datasources.dataset import YearlyDataset
    from temds.datasources.timeseries import YearlyTimeSeries
    r = ns_region()
    r.data['era5-downscaled'] = YearlyTimeSeries([YearlyDataset(2000, region_daily_dataset())])
    where = tmp_path / 'northslope'
    where.mkdir()
    assert r.export_TEM('cru_climate', where, cru_item='era5-downscaled') == 0
    with xr.open_dataset(where / 'tem_export' / 'era5-downscaled-historic-climate.nc') as ds:
        assert ds.sizes['time'] == 12
        assert ds.attrs['source_data_version'] == 'test'
        np.testing.assert_allclose(ds['tair'].values, -10.0)
        # prec is summed: noleap month lengths
        np.testing.assert_allclose(ds['precip'].mean(('x', 'y')).values,
                                   [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31])


def test_export_warns_on_missing_units(tmp_path):
    from temds.datasources.dataset import YearlyDataset
    from temds.datasources.timeseries import YearlyTimeSeries
    ds = region_daily_dataset()
    del ds['vapo'].attrs['units']
    r = ns_region()
    r.data['era5-downscaled'] = YearlyTimeSeries([YearlyDataset(2000, ds)])
    (tmp_path / 'northslope').mkdir()
    assert r.export_TEM('cru_climate', tmp_path / 'northslope', cru_item='era5-downscaled') == 0
    assert any('No units for variable: vapo' in m.text for m in r.logger.data)


# ----------------------------------------------------------- unit labels
def test_merge_for_year_unit_labels():
    ds = era5_daily.merge_for_year(2001, synthetic_downloads(2001)).dataset
    assert {v: ds[v].attrs['units'] for v in ['tair_avg', 'prec', 'nirr', 'vapo']} == \
        {'tair_avg': 'celsius', 'prec': 'mm', 'nirr': 'W/m^2', 'vapo': 'kPa'}
    from cf_units import Unit
    from temds import climate_variables
    for v in ['tair_avg', 'prec', 'nirr', 'vapo']:  # still equal to std units
        assert Unit(ds[v].attrs['units']) == climate_variables.temds_units_for(v)


def test_export_tem_unit_labels(tmp_path):
    from temds.datasources.dataset import YearlyDataset
    from temds.datasources.timeseries import YearlyTimeSeries
    ds = region_daily_dataset()
    ds['prec'].attrs['units'] = '0.001 meter'
    r = ns_region()
    r.data['era5-downscaled'] = YearlyTimeSeries([YearlyDataset(2000, ds)])
    (tmp_path / 'northslope').mkdir()
    r.export_TEM('cru_climate', tmp_path / 'northslope', cru_item='era5-downscaled')
    with xr.open_dataset(tmp_path / 'northslope/tem_export/era5-downscaled-historic-climate.nc') as out:
        assert {v: out[v].attrs['units'] for v in ['tair', 'precip', 'nirr', 'vapor_press']} == \
            {'tair': 'celsius', 'precip': 'mm/month', 'nirr': 'W/m^2', 'vapor_press': 'kPa'}


# ---------------------------------------------------- era5_climate export
def test_export_era5_climate_option(tmp_path):
    from temds.constants import TEMDS_DATASET_NAMES, TEMDS_DEFAULT_EXPORT_NAMES
    from temds.datasources.dataset import YearlyDataset
    from temds.datasources.timeseries import YearlyTimeSeries
    assert 'era5_climate' in TEMDS_DATASET_NAMES
    assert 'era5_climate' not in TEMDS_DEFAULT_EXPORT_NAMES
    r = ns_region()
    r.data['era5-test'] = YearlyTimeSeries([YearlyDataset(2000, region_daily_dataset(value=-5.0))])
    (tmp_path / 'northslope').mkdir()
    assert r.export_TEM('era5_climate', tmp_path / 'northslope', era5_item='era5-test') == 0
    with xr.open_dataset(tmp_path / 'northslope/tem_export/era5-test-historic-climate.nc') as ds:
        assert ds.attrs['dataset_name'] == 'era5_climate'
        np.testing.assert_allclose(ds['tair'].values, -5.0)
