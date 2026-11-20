import intake
import intake_esm # I think this needs to be here
import fsspec
import xarray as xr
import cftime
import numpy as np
from pathlib import Path

CMIP6_CATALOG_URL = "https://storage.googleapis.com/cmip6/pangeo-cmip6.json"


def connect(url=CMIP6_CATALOG_URL):
    """Connect to pango catalog via intake esm

    Parameters
    ----------
    url: str
        url to json catalog

    Returns
    -------
    intake_esm.core.esm_datastore
    """
    return intake.open_esm_datastore(url)

def search(catalog, parameters):
    """Search catalog for scenarios/models/variables/etc.

    Parameters
    ----------
    catalog: intake_esm.core.esm_datastore
        catalog of available data
    parameters: dict
        Search parameters 
        example:
        parameters = {
            "experiment_id": [ 'ssp126','ssp245', 'ssp370', 'ssp585' ],
            "table_id": ["day"],
            "variable_id": ['tas'],
            "member_id": ["r4i1p1f1"],
            "source_id": ["CESM2"],
            "grid_label": ["gn"],
        }

    Returns
    -------
    intake_esm.core.esm_datastore
        catalog with search applied
    """
    return catalog.search(**parameters)

def _match_calendar(bound, time_coord):
    """Return `bound` as a cftime datetime in the calendar of `time_coord`.

    CMIP6 models use different calendars (CESM2: noleap, MRI-ESM2-0 and
    ACCESS-CM2: proleptic_gregorian, UKESM: 360_day, ...). cftime objects of
    different calendars can not be compared, so the bounds are rebuilt in the
    dataset's own calendar.
    """
    calendar = getattr(time_coord.dt, 'calendar', 'standard')
    return cftime.datetime(
        bound.year, bound.month, bound.day, calendar=calendar
    )


def lon_selection(lon, minx, maxx):
    """Boolean index of longitudes in [minx, maxx], wrap-around aware.

    Works for 0..360 and -180..180 longitudes and for bounds that cross the
    0/360 seam (e.g. minx=350, maxx=380). If the requested width is 360 or
    more every longitude is selected.
    """
    lon = np.asarray(lon)
    width = maxx - minx
    if width >= 360:
        return np.ones(lon.shape, dtype=bool)
    return ((lon - minx) % 360) <= width


def grid_spacing(coord):
    """Largest spacing (absolute) between consecutive 1-D coordinate values"""
    c = np.asarray(coord, dtype=float)
    if c.size < 2:
        return 0.0
    return float(np.max(np.abs(np.diff(c))))


def download(save_to, zstore, time_bounds, spatial_bounds, **kwargs):
    """Download data

    Parameters
    ----------
    save_to: Path
        Where to save locally 
    zstore: str
        Gs id od zarr to connect to
    time_bounds: tuple(cftime, cftime)
        Bounds on time variable to save 
        Lower bound is inclusive, and upper bound is exclusive. Only
        year/month/day are used, they are converted to the calendar of the
        dataset, so any cftime (or datetime) type may be passed.
    spatial_bounds: tuple(float, float, float, float)
        spatial bounds to save
        (minx, miny, maxx, maxy)
    **kwargs: dict
        'climate_encoding': dict
            custom climate encoding for saved .nc files,
            if not provided encoding is generated from other
            kwargs
        'missing_value': float, default 1.e+20
        'fill_value': float, default 1.e+20
            values set as _FillValue, and missing_value in netCDF variable
            headers
        'overwrite': bool
            when true overwrite existing files
        'zlib': bool
            When True compression is used in encoding
        'complevel': int
            Compression level for 'zlib'
        'extra_attrs': dict
            any extra attributes to add to `dataset` before saving
            as .nc file
        'buffer_cells': float, default 2
            Number of native grid cells to add around `spatial_bounds`.
            Cells are selected by their centre, so without a buffer the
            coarse cells do not cover the edges of the target region and
            bilinear interpolation has no neighbours there.
    
    Returns
    -------
    xr.Dataset
    """
    def lookup(kw, ke, de):
        return kw[ke] if ke in kw else de

    time_coder = xr.coding.times.CFDatetimeCoder(use_cftime=True)
    ds = xr.open_zarr(
        fsspec.get_mapper(zstore, token="anon", access="read_only"),
        consolidated=True,
        decode_times=time_coder,
    )
    start = _match_calendar(time_bounds[0], ds.time)
    end = _match_calendar(time_bounds[1], ds.time)
    time_idx = np.logical_and(ds.time >= start, ds.time < end).values

    buffer_cells = lookup(kwargs, 'buffer_cells', 2)
    minx, miny, maxx, maxy = spatial_bounds
    buf_x = buffer_cells * grid_spacing(ds.lon)
    buf_y = buffer_cells * grid_spacing(ds.lat)
    minx, maxx = minx - buf_x, maxx + buf_x
    miny, maxy = max(miny - buf_y, -90), min(maxy + buf_y, 90)

    sidx_x = lon_selection(ds.lon.values, minx, maxx)
    sidx_y = np.logical_and(ds.lat.values >= miny, ds.lat.values <= maxy)

    print('clipping to extents')
    ds = ds.isel(time=time_idx, lon=sidx_x, lat=sidx_y)

    fill_value = lookup(kwargs, 'fill_value', 1.0e+20 )
    missing_value = lookup(kwargs, 'missing_value', 1.0e+20 )
    compress = lookup(kwargs, 'use_zlib', True)
    complevel = lookup(kwargs, 'complevel', 9)
    overwrite = lookup(kwargs, 'overwrite', False)
    extra_attrs = lookup(kwargs, 'extra_attrs', {})

    if 'climate_encoding' in kwargs:            
        climate_enc = kwargs['climate_encoding']
    else:
        climate_enc = {
            '_FillValue':fill_value, 
            'missing_value':missing_value, 
            'zlib': compress, 'complevel': complevel # USE COMPRESSION?
        }

    for var in ds.data_vars:
        if var.endswith('_bnds'):
            continue
        ds[var].rio.update_encoding(climate_enc, inplace=True)

    ds.coords['lon'] = (ds.coords['lon'] + 180) % 360 - 180
    if 'lon_bnds' in ds.coords:
        ds.coords['lon_bnds'] = (ds.coords['lon_bnds'] + 180) % 360 - 180
    elif 'lon_bnds' in ds:
        ds['lon_bnds'] = (ds['lon_bnds'] + 180) % 360 - 180
    ds = ds.sortby('lon')

    # Set spatial dims *before* writing the CRS and write the transform
    # from the final coordinates; writing the CRS first stored an identity
    # GeoTransform ("0 1 0 0 0 1") that later mis-georeferenced the data.
    if 'spatial_ref' in ds.coords:
        ds = ds.drop_vars('spatial_ref')
    ds = ds.rio.set_spatial_dims(x_dim='lon', y_dim='lat')
    ds = ds.rio.write_crs('EPSG:4326')
    ds['spatial_ref'].attrs.pop('GeoTransform', None)
    ds.attrs.update(extra_attrs)

    print('saving')
    if  not Path(save_to).exists() or overwrite:
        Path(save_to).parent.mkdir(parents=True, exist_ok=True)
        Path(save_to).unlink(missing_ok=True)
        ds.to_netcdf(save_to, engine="netcdf4")
    else:
        raise FileExistsError(
            f'The file {save_to} exists and `overwrite` is False'
        )
    return ds
