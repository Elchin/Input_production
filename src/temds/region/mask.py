"""
"""
from pathlib import Path
import numbers
from copy import deepcopy

from osgeo import gdal
import pyproj
import shapely
import geopandas as gpd
from affine import Affine
import numpy as np

from . import tools
from .. import gdal_tools




class NonIntersectingGeoSeriesError(Exception):
    pass


def normalize_resolution(resolution):
    """Return resolution as a positive ``(x_res, y_res)`` tuple.

    Accepts a single number (square pixels) or an ``(x_res, y_res)`` pair, as
    returned by ``Mask.resolution`` / GDAL geotransforms where the y
    resolution is usually negative.  Signs are dropped; callers apply the
    north-up convention (negative y step) themselves.
    """
    if isinstance(resolution, numbers.Real) and not isinstance(resolution, bool):
        x_res = y_res = float(resolution)
    else:
        try:
            x_res, y_res = resolution
        except (TypeError, ValueError):
            raise TypeError(
                f'resolution must be a number or an (x, y) pair, got {resolution!r}'
            )
        x_res, y_res = float(x_res), float(y_res)
    x_res, y_res = abs(x_res), abs(y_res)
    if x_res == 0 or y_res == 0:
        raise ValueError('resolution must be non-zero')
    return x_res, y_res

class Mask(object):
    """Object to manage mask for temds region objects.

    Data is stored internally as a raster with integer values.
    Values are taken mean:
        1: Cells you are interested in.
        0: Other cells
    You may be interested in cells based on if they have available data or not,
    or You may be interested in cells because they are of interest to a project
    you are working on. It pretty arbitrary, but region.Region supports
    using a Mask to set values where the mask is 0 to a no data value.


    """
    def __init__(self, mask: gdal.Dataset):
        """
        """
        self.raster = mask ## raster
        
    @classmethod
    def from_geoseries(cls, mask_series, resolution, extent_gpd = None, align_extent_to_resolution=True):
        """
        Parameters
        ----------
        mask_series: GeoSeries
            GeoSeries representing some valid area of data
        resolution: float 
        extent_gpd: GeoDataframe, Optional
            smaller extent GeoDataframe to clip lager mask to
        align_extent_to_resolution: bool
            if extent_gpd is provided align will align clipping to resolution
            gird
        
        """

        x_res, y_res = normalize_resolution(resolution)

        if extent_gpd is None:
            extent_gpd = mask_series
        elif align_extent_to_resolution:
            if not mask_series.intersects(extent_gpd).iloc[0]:
                raise NonIntersectingGeoSeriesError('extent_gpd does not intersect mask_series')

            mask_series = mask_series.intersection(extent_gpd)

            extent_gpd = tools.align_to_resolution(extent_gpd, (x_res, y_res))
        
        name = 'aoi_mask'
        layer_name = f'{name}_{extent_gpd.crs.to_epsg()}' # For shapefiles, this seems to be the filename
    
        bounds = extent_gpd.bounds.iloc[0]
    
        # Get in memory shape file representation that can be passed to 
        # the rasterization process.
        ds, layer = tools.geopandas_to_ogr_dataset(mask_series, layer_name=layer_name)
    
        opts = gdal.RasterizeOptions(
          format='MEM',
          outputBounds=(bounds['minx'], bounds['miny'], bounds['maxx'], bounds['maxy']),
          xRes=x_res,
          yRes=y_res,
          noData=0,
          burnValues=[1],
          layers=[layer.GetName()],
          outputType=gdal.GDT_Int16
        )
        rds = gdal.Rasterize('', ds, options=opts)


    
        return cls(rds)

    @classmethod
    def from_extent(cls, extent_gpd, resolution, align_extent_to_resolution=True, fill_uniform=False):
        """create mask from extent, all values are set to 1(good)

        Assumes grid resolution is uniform i.e(1000m by 1000m)
        """
        init_boundary = deepcopy(extent_gpd)

        # Accept a scalar or an (x, y) pair (PR #48); everything below works
        # with positive x/y pixel sizes.
        x_res, y_res = normalize_resolution(resolution)

        if align_extent_to_resolution:
            extent_gpd = tools.align_to_resolution(extent_gpd, (x_res, y_res))

        bounds = extent_gpd.bounds.iloc[0]
        # small epsilon guards against float round-off (e.g. 199999.9999/4000)
        n_x = int(np.floor((bounds['maxx'] - bounds['minx']) / x_res + 1e-9))
        n_y = int(np.floor((bounds['maxy'] - bounds['miny']) / y_res + 1e-9))
        rds = gdal_tools.empty_dataset(
            n_x,
            n_y,
            extent_gpd.crs.to_wkt(),
            (bounds['minx'], x_res, 0, bounds['maxy'], 0,  -y_res ),
            1,
            gdal.GDT_Int16
        )  
        as_np = rds.ReadAsArray()
        if fill_uniform:
            as_np[:] = 1
        else:
            as_np[:] = 0
            _,res_x, _, _, _, res_y = rds.GetGeoTransform()
            gt = Affine.from_gdal(*rds.GetGeoTransform())
            for col in range(rds.RasterYSize):
                for row in range(rds.RasterXSize):
                    minx, maxy = gt * (row, col)
                    maxx = minx+res_x
                    miny = maxy+res_y
                    box = shapely.box(minx, miny, maxx, maxy)
                    is_in = init_boundary.iloc[[0]].geometry.intersects(box).values[0]
                    if is_in:
                        as_np[col, row] = 1
        rds.WriteArray(as_np)

        return cls(rds)
    
    def export_gpd_extent(self):
        minx, resx, _, maxy, _, resy = self.transform
        nx, ny = self.shape
        maxx = minx + resx*nx
        miny = maxy + resy*ny

        return gpd.GeoSeries(shapely.box(minx,miny, maxx,maxy), crs=self.crs)
    
    @classmethod
    def from_file(cls, where):
        return cls(gdal.Open(where))

    @property
    def resolution(self):
        """tuple because sometimes one res is negative
        """
        return self.transform[1], self.transform[5]
    
    @property
    def transform(self):
        """
        """
        return self.raster.GetGeoTransform()
    
    @property
    def crs(self):
        """
        """
        return pyproj.CRS(self.raster.GetProjection())
    
    @property
    def shape(self):
        return self.raster.RasterXSize, self.raster.RasterYSize

    def to_file(self, where: Path):
        
        # r_aoi = self.as_raster(crs=crs)
        
        # outfolder = Path(output_dir, f"{name}")
        # outfolder.mkdir(parents=True, exist_ok=True)
        
        # crs = pyproj.CRS.from_wkt(self.raster.GetProjection()).to_epsg()
        # resolution = self.raster.GetGeoTransform()[1]
        # outfile_name = Path(outfolder, f"{name}_{crs}_{resolution}m.tiff")
        
        driver = gdal.GetDriverByName('GTiff')
        driver.CreateCopy(str(where), self.raster)


