
"""
Vegetation Datasource Module
------------------------------

Metadata for vegetation dataset

"""

NAME = 'vegetation'

# citation for vegetation  dataset
CITATION = ()

# TODO: would be better to find some web source for these things rather than hardcode...
# Bu here these are the expected paths once things are extracted/unzipped inside
# a download directory.
political_shp_path = "mask/geoBoundariesCGAZ_ADM1/geoBoundariesCGAZ_ADM1.shp"
eco_shp_path = "mask/Ecoregions2017/Ecoregions2017.shp"

land_cover_path = "vegetation/Jan2025_TEM_Landcover2/TEM_Landcover_V4.tif"
land_cover_classification = "vegetation/Jan2025_TEM_Landcover2/TEMLandcoverClassDictionary.csv"


# Old IEM map is not used...
#url_iem_veg = 'https://data.snap.uaf.edu/data/IEM/Inputs/ancillary/land_cover/v_0_4/iem_vegetation_model_input_v0_4.tif'

# Instead we have a Google Drive link to a folder from HG
#https://drive.google.com/drive/folders/15QNWuPZ-m-j_JrLogM3QzqodLaDxBtBU?usp=share_link
# That when downloaded we put in `working/00-download/vegetation/`


## TEM_Landcover_V4 classes that are intentionally mapped to CMT00 (not run
## by TEM). Any other class that ends up as CMT00 is reported by
## TEMDataset.from_vegetation. As of TEM_Landcover_V4 that is only
## 'Cedar forest' (value 22): it has no community rule yet.
## TODO(known issue): 'Cedar forest' (class 22) is deliberately left as CMT00
## (not run) and flagged with a warning, pending a decision on its CMT
## (decided Nov 2026: leave for now). Add a rule in
## TEMDataset.from_vegetation once the CMT is chosen; see README "Known issues".
NON_VEGETATED_CLASSES = ['Unclassified', 'Developed', 'Snow/Ice', 'Other', 'N/A']
