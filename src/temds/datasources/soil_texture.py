#!/usr/bin/env python



NAME = "soil_texture"

# These URLs go to a web dav directory listing and then we need to download 
# specific files from w/in the directory listings...
# See the file_tools.download_all_files(..) function

# check this out, might be able to do w/o downloads: just VRT over webdav!
# https://docs.isric.org/globaldata/soilgrids/WebDav.html#programmatical-access

urlclay='https://files.isric.org/soilgrids/latest/data_aggregated/1000m/clay/'
urlsand='https://files.isric.org/soilgrids/latest/data_aggregated/1000m/sand/'
urlsilt='https://files.isric.org/soilgrids/latest/data_aggregated/1000m/silt/'

# expected folder name in the downloaded data directory once source files 
# have been unzipped, etc...
soil_texture = "soiltexture" 


import numpy as np

## SoilGrids layers used and their thickness (cm)
DEPTHS = {'15-30cm': 15, '30-60cm': 30, '60-100cm': 40}

## SoilGrids nodata for the integer layers
NODATA = -32768

## files needed from each of the urls above
FILE_REGEX = r'^(clay|sand|silt)_(15-30|30-60|60-100)cm_mean_1000\.tif$'


def file_name(var, depth):
    """SoilGrids 1 km file name, e.g. clay_15-30cm_mean_1000.tif"""
    return f'{var}_{depth}_mean_1000.tif'


def depth_weighted_percent(layers, thicknesses):
    """Thickness weighted mean of SoilGrids layers converted to percent.

    Parameters
    ----------
    layers: list[np.ndarray]
        layers in g/kg (SoilGrids units), NaN for nodata
    thicknesses: list[float]
        layer thickness, same order as `layers`

    Returns
    -------
    np.ndarray
        percent (g/kg / 10). NaN where any layer is NaN.
    """
    layers = np.asarray(layers, dtype=float)
    w = np.asarray(thicknesses, dtype=float).reshape((-1,) + (1,) * (layers.ndim - 1))
    gkg = (layers * w).sum(axis=0) / w.sum()
    return gkg / 10.0


#gfres=50000

