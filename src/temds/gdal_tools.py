"""
gdal tools
----------

gdal helpers
"""
import warnings

import numpy as np
from osgeo import gdal
from affine import Affine

gdal.UseExceptions()

from functools import cache

@cache
def clip_gdal_opt(dest, source, resample_alg, run_primer, nd_as_array):
    no_data = np.nan
    if nd_as_array:
        no_data = [np.nan for i in range(source.RasterCount)]

    if run_primer:
        gdal.Warp(dest, source, warpOptions =['NUM_THREADS=4'],)#multithread=True)
    gdal.Warp(
        dest, source,
        srcNodata=no_data,
        dstNodata=no_data,
        resampleAlg=resample_alg,
        warpOptions =['NUM_THREADS=4'],
        # multithread=True
    )
    dest.FlushCache()
    return dest

def empty_dataset(x_dim, y_dim, projection_wkt, geotransform_tuple, 
                  bands=1, gdal_type=gdal.GDT_Float32):
    '''Explicitly create and return a new, geo-referenced, in memory, Gdal Dataset.'''

    driver = gdal.GetDriverByName('MEM')
    dest = driver.Create("", x_dim, y_dim, bands, gdal_type)
    dest.SetProjection(projection_wkt)
    dest.SetGeoTransform(geotransform_tuple)
    dest.FlushCache()

    return dest

def _spatial_dims(dataset):
    """Return (x_dim, y_dim) names for an xarray dataset"""
    if 'lon' in dataset.dims and 'lat' in dataset.dims:
        return 'lon', 'lat'
    return 'x', 'y'


def transform_from_coords(dataset, rtol=0.01):
    """Compute an affine transform from 1-D pixel centre coordinates.

    rioxarray's ``rio.transform()`` prefers a cached ``GeoTransform``
    attribute on the ``spatial_ref`` variable when one is present.  That
    cache goes stale whenever coordinates are modified after it was written
    (e.g. converting CMIP6 longitudes from 0..360 to -180..180 and sorting,
    or writing the CRS before the spatial dims are set), which silently
    mis-georeferences the data.  This function always derives the
    transform from the coordinate values themselves.

    Parameters
    ----------
    dataset: xr.Dataset or xr.DataArray
        Must have 1-D, monotonic, pixel centre coordinates for x/y (or
        lon/lat).
    rtol: float
        Relative tolerance on the coordinate spacing. Grids whose spacing
        varies more than this (e.g. Gaussian latitudes near the poles) get a
        warning because a single affine transform is then only an
        approximation.

    Returns
    -------
    affine.Affine
    """
    x_dim, y_dim = _spatial_dims(dataset)
    x = np.asarray(dataset[x_dim].values, dtype=float)
    y = np.asarray(dataset[y_dim].values, dtype=float)
    if x.ndim != 1 or y.ndim != 1 or x.size < 2 or y.size < 2:
        raise ValueError(
            'transform_from_coords needs 1-D x/y coordinates with at least 2 values'
        )

    transform_parts = []
    for name, c in ((x_dim, x), (y_dim, y)):
        d = np.diff(c)
        if not (np.all(d > 0) or np.all(d < 0)):
            raise ValueError(f'coordinate {name} is not strictly monotonic')
        res = (c[-1] - c[0]) / (c.size - 1)
        dev = np.max(np.abs(d - res)) / abs(res)
        if dev > rtol:
            warnings.warn(
                f'coordinate {name} is not regularly spaced (max deviation '
                f'{dev:.2%} of mean spacing); using mean spacing {res}'
            )
        transform_parts.append((c[0] - res / 2.0, res))

    (x0, xres), (y0, yres) = transform_parts
    return Affine(xres, 0.0, x0, 0.0, yres, y0)


def fill_nodata(dataset, value=np.nan):
    """Initialise every band of a gdal dataset to `value` (in place).

    MEM datasets are created zero-filled, and gdal.Warp only writes pixels it
    can compute; anything it skips would otherwise keep 0 (or the previous
    variable's values when a dataset is re-used for several variables).
    """
    for b in range(1, dataset.RasterCount + 1):
        dataset.GetRasterBand(b).Fill(value)
    return dataset


#cant cache dict
def clip_opt_2 (dest, source, vars_dict, resample_alg, run_primer, nd_as_array):
    data_arrays = {}
    no_data = np.nan
    if nd_as_array:
        no_data = [np.nan for i in range(source.RasterCount)]

    for var in vars_dict:
        cur = vars_dict[var]
        source.WriteArray(cur)
        source.FlushCache() ## ensures data is in gdal dataset

        # dest is re-used for every variable; make sure pixels that the warp
        # does not cover come out as nodata, not 0 or the last variable.
        fill_nodata(dest, np.nan)

        # run twice first to 'prime' the objects, other wise coastal data is
        # missing in result
        # dest = clip_gdal_opt(dest, source, resample_alg, run_primer, no_data)
        if run_primer:
            gdal.Warp(dest, source, multithread=False) # Multithread causes issues on MacOS
        gdal.Warp(
            dest, source,
            srcNodata=no_data,
            dstNodata=no_data,
            resampleAlg=resample_alg,
            multithread=False, # Multithread causes issues on MacOS
        )
        dest.FlushCache()
        
        data_arrays[var] = dest.ReadAsArray()
    return data_arrays