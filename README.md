# TEM Input Downscaling Tools

## Table of Contents

- [Description](#description)
- [Data sources](#data-sources)
- [Processing chain](#processing-chain)
- [Downscaling method](#downscaling-method)
- [Use](#use)
- [Installation](#installation)
- [Tests](#tests)


## Description

The `TEMdownscale` tool (`temds`) is used for downscaling and preparing 
model-ready inputs and forcing data. The tool is designed as a modular set of 
helper sub-tools that can be chained toegether for different use cases. Also the 
modularity helps for diagnosing and addressing issues found in data preparation 
process. Typically the data prep process contains many steps, starting with
downloading raw assests, then transforming these assets (cropping, projection,
formatting, etc) so that they can be combined for numerical reasons (i.e.
downscaling) and exported into model-ready formats.


## Data sources

| Data | Used for | Where it comes from | Access | Command |
|---|---|---|---|---|
| [WorldClim 2.1](https://www.worldclim.org/data/worldclim21.html) monthly normals 1970-2000, 30 arc-second (~1 km): tavg, tmin, tmax, prec, srad, wind, vapr | high resolution reference climate for the delta method | `https://geodata.ucdavis.edu/climate/worldclim/2_1/base/wc2.1_30s_<var>.zip` (~19 GB for all 7) | public | `download worldclim`, `preprocess worldclim` (reads the zips in place via GDAL `/vsizip/`) |
| [CRU-JRA v2.5](https://catalogue.ceda.ac.uk/uuid/43ce517d74624a5ebf6eec5330cd18d5) 6-hourly, 0.5 deg | historical forcing (resampled to daily) | CEDA, `dap.ceda.ac.uk/.../cru_jra/cru_jra_2.5/` | CEDA account with CRU-JRA access and a CEDA certificate | `temds.CloudShellBucketFiller` (no CLI download command yet) |
| [ERA5 daily statistics](https://cds.climate.copernicus.eu/datasets/derived-era5-single-levels-daily-statistics) | alternative historical forcing | Copernicus Climate Data Store (`derived-era5-single-levels-daily-statistics`); hourly ERA5 via Google Earth Engine in `era5_hourly_gee.py` | CDS account, accepted licence, Personal Access Token (`~/.ecmwfdatastoresrc`) | `download era5-daily`, `preprocess era5-daily` |
| [CMIP6](https://pangeo-data.github.io/pangeo-cmip6-cloud/) daily (table `day`): tas, pr, rsds, huss, psl (required), tasmin, tasmax, sfcWind (optional) | projections (historical, ssp126/245/370/585) | Pangeo Google Cloud catalog `https://storage.googleapis.com/cmip6/pangeo-cmip6.json` | public (anonymous GCS) | `download cmip6-daily`, `preprocess cmip6-daily` |
| [GMTED2010](https://www.usgs.gov/coastal-changes-and-impacts/gmted2010) mean elevation 7.5 arc-second (`mn75_grd`) | topography (elevation, slope, aspect, TPI, drainage) and surface pressure for CMIP6 vapo | USGS `edcintl.cr.usgs.gov/.../GMTED/Grid_ZipFiles/mn75_grd.zip` | public | `download elevation`, `preprocess topo` |
| [SoilGrids](https://www.isric.org/explore/soilgrids) 1 km clay/sand/silt, 15-30, 30-60, 60-100 cm | soil texture (%) | ISRIC `https://files.isric.org/soilgrids/latest/data_aggregated/1000m/` | public | `downscale extra-tem-files` (expects `<data>/soiltexture/<var>_<depth>_mean_1000.tif`) |
| TEM land cover (`TEM_Landcover_V4.tif` + `TEMLandcoverClassDictionary.csv`, H. Genet) | vegetation community type (CMT) | Google Drive folder shared by H. Genet (see `datasources/vegetation.py`) | link | `downscale extra-tem-files` (`<data>/vegetation/Jan2025_TEM_Landcover2/`) |
| [RESOLVE Ecoregions 2017](https://ecoregions.appspot.com/) | ecoregion / biome / realm / alpine rules for the CMT mapping | `https://storage.googleapis.com/teow2016/Ecoregions2017.zip` | public | `<data>/mask/Ecoregions2017/` |
| [geoBoundaries CGAZ ADM1](https://www.geoboundaries.org/) | country / state rules for the CMT mapping | `https://github.com/wmgeolab/geoBoundaries/raw/main/releaseData/CGAZ/geoBoundariesCGAZ_ADM1.zip` | public | `<data>/mask/geoBoundariesCGAZ_ADM1/` |
| NOAA GML Mauna Loa annual mean CO2 | `co2.nc` | `https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_annmean_mlo.txt` (currently hard coded in `Region.export_TEM`, 1901-2024) | public | `export --which co2` |
| Fire return interval | `fri-fire.nc` | synthetic constants | - | `downscale extra-tem-files` |

CMIP6 notes (Pangeo catalog, Nov 2026): the default member is chosen per model
(`cmip6.DEFAULT_ENSEMBLES`: ACCESS-CM2 and MRI-ESM2-0 `r1i1p1f1`, CESM2
`r4i1p1f1`). `download cmip6-daily` stops if a required variable is missing for
the member and lists members that have them. CESM2 historical has no daily
tasmin/tasmax, and CESM2 ssp126/ssp370 and MRI-ESM2-0 ssp370 have no daily
rsds. `vapo` is only produced from CMIP6 when the region has `topo.nc`
(elevation is needed to turn sea level pressure into surface pressure).
Any calendar except `360_day` is supported; data is converted to `noleap`.

## Processing chain

Work happens in a *region* folder (a `manifest.yml` plus data files). With
`--use-region <dir>` the commands read and write region items by name.

```
R=northslope
# 1. region: boundary + resolution/CRS (or a mask) -> R/manifest.yml, R/mask.tif
TEMdownscale region create $R boundary.geojson --resolution 4000 --crs EPSG:6931

# 2. download (internet; credentials only for CRU-JRA and ERA5)
TEMdownscale download worldclim downloads/worldclim
TEMdownscale download elevation downloads/topo
TEMdownscale --use-region $R --no-load-all download cmip6-daily downloads/cmip6/MRI-ESM2-0/historical historical MRI-ESM2-0 1971 2000
TEMdownscale --use-region $R --no-load-all download cmip6-daily downloads/cmip6/MRI-ESM2-0/ssp245 ssp245 MRI-ESM2-0 2015 2100

# 3. preprocess: crop/reproject to the region grid, convert units and names
TEMdownscale --use-region $R preprocess topo $R/topo.nc downloads/topo/mn75_grd $R/mask.tif
TEMdownscale --use-region $R --no-load-all preprocess worldclim $R/worldclim.nc downloads/worldclim $R/mask.tif
TEMdownscale --use-region $R --no-load-all --load-item topo preprocess cmip6-daily $R/mri-historical downloads/cmip6/MRI-ESM2-0/historical 1971 2000 --name mri-historical
TEMdownscale --use-region $R --no-load-all --load-item topo preprocess cmip6-daily $R/mri-ssp245 downloads/cmip6/MRI-ESM2-0/ssp245 2015 2100 --name mri-ssp245

# 4. downscale (delta method against WorldClim)
TEMdownscale --use-region $R --no-load-all --load-item mri-historical --load-item worldclim \
  downscale delta-method mri-historical-downscaled mri-historical worldclim \
  --baseline-years 1971 2000 --baseline-name mri-historical-baseline --save-baseline True --save-correction-factors True
TEMdownscale --use-region $R --no-load-all --load-item mri-ssp245 --load-item worldclim --load-item mri-historical-baseline \
  downscale delta-method mri-ssp245-downscaled mri-ssp245 worldclim --baseline mri-historical-baseline
TEMdownscale --use-region $R --no-load-all --load-item topo downscale extra-tem-files downloads/extra $R

# 5. export TEM-ready files to R/tem_export
TEMdownscale --use-region $R --no-load-all export --format TEM --which topo,co2,cmip_climate --cmip-item mri-ssp245-downscaled $R
TEMdownscale --use-region $R --no-load-all export --format TEM --which runmask,drainage,vegetation,soiltex,fri_fire $R
```

With `--use-region`, the DESTINATION of `preprocess cmip6-daily` and
`downscale delta-method` is the region item name. `export` writes
`<item>-projected-climate.nc` / `<item>-historic-climate.nc` for
`cmip_climate` / `cru_climate` (`--cmip-item`, `--cru-item`).

## Downscaling method

Plain delta scaling (no quantile mapping):

1. A monthly baseline is built from the coarse daily data over
   `--baseline-years START END` (both inclusive): daily values are averaged
   over the years, then aggregated per month (sum for `prec`, mean otherwise).
   To match WorldClim, use years inside 1970-2000.
2. Correction factors per month and pixel: `reference - baseline` for
   temperatures (units of the reference) and `reference / baseline` for
   `prec`, `nirr`, `vapo`, `wind` (dimensionless, `units = "1"`). Units are
   compared with cf_units and converted when needed.
3. Multiplicative factors are guarded: where |baseline| is below
   `corrections.MIN_BASELINE` (nirr 1 W/m2, prec 0.1 mm/month, vapo 1e-3 kPa,
   wind 0.1 m/s) the factor is 1 (coarse value kept), and factors are clipped to
   `[0, corrections.MAX_FACTOR]` (10; 20 for prec). Without this, polar-night
   radiation baselines of ~1e-6 W/m2 gave factors of ~1e5.
4. Each daily value is corrected with its month's factor.

Soil texture is the thickness-weighted mean of the 15-30, 30-60 and 60-100 cm
SoilGrids layers (g/kg / 10 = %); the 0-15 cm layers are not used. SoilGrids
nodata pixels are NaN. Vegetation classes without a CMT rule (currently
'Cedar forest') and non-vegetated classes become CMT00 (not run); the
former are reported in the log.

## Use

A command line tool is installed with the package.


```
 Usage: TEMdownscale [OPTIONS] COMMAND [ARGS]...                                                                                                                            

 Main CLI entry point for TEMDS tools                                                                                                                                       

╭─ Options ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ --version               --no-version                  Flags if version TEMDS version should be shown. [default: no-version]                                              │
│ --log-file                                   PATH     Optional path to save log to [default: None]                                                                       │
│ --log-level                                  TEXT     Log level. Options are: INFO, DEBUG, WARN. ERROR, or NONE} [default: INFO]                                         │
│ --silent                --no-silent                   Flag to suppress printing messages to console. [default: no-silent]                                                │
│ --use-region                                 PATH     Path to a directory containing a region defined by manifest.yml [default: None]                                    │
│ --load-all              --no-load-all                 Flag to load all data for a region when --use-region is provided [default: load-all]                               │
│ --load-item                                  TEXT     Keys for items to load for region if --no-load-all is provided                                                     │
│ --parallel              --no-parallel                 Flag to enable parallel processing [default: no-parallel]                                                          │
│ --n-process                                  INTEGER  Number of parallel processes to use when --parallel is used [default: 4]                                           │
│ --overwrite             --no-overwrite                Flag to overwrite existing data [default: no-overwrite]                                                            │
│ --cleanup               --no-cleanup                  Flag to cleanup downloads by removing them [default: no-cleanup]                                                   │
│ --fail-on-warn          --no-fail-on-warn             Flag to halt program execution when a warning is generated [default: no-fail-on-warn]                              │
│ --install-completion                                  Install completion for the current shell.                                                                          │
│ --show-completion                                     Show completion for the current shell, to copy it or customize the installation.                                   │
│ --help                                                Show this message and exit.                                                                                        │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ region       Tools for region management                                                                                                                                 │
│ download     Tools to download data                                                                                                                                      │
│ preprocess   Tools to preprocess data                                                                                                                                    │
│ statistics   Tools to preprocess data                                                                                                                                    │
│ downscale    Tools to downscale data                                                                                                                                     │
│ export       Tools to export data to a model specific format.                                                                                                            │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
``` 


## Installation

### For Development

We use conda and [minconda](https://www.anaconda.com/docs/getting-started/miniconda/install#quickstart-install-instructions) for the development environment

```
conda env create -f environment.yml
conda activate temds
pip install --editable .
```

## Tests

```
pytest tests
```

`tests/test_fixes_4km.py` only uses synthetic data. The other test modules
need locally generated data in `working/` and are skipped without it.

Drawing of the process (deprecated). Google Drawing file, stored in Shared Drives > Input_Production > "high level workflow overview"

<img src="https://docs.google.com/drawings/d/e/2PACX-1vT7IkZXsBi6C3-BPnBnHbw28yEEGBfDkYfkm3bVOznsvSoZNq_cgy3AByhTyZYtAFgXv3BrZsvGNaIz/pub?w=2487&amp;h=1372">



