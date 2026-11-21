"""
CLI for downloading
-------------------

TODO: 
    - era5 bounds options, cmpi6 bounds.
    - commands or options to submit er5 requests and let user return and download later?
    - better start_year end_year for cmip6 
    - cmip6 overwrite
"""
from pathlib import Path

from typer import Typer, Argument, Option, Context, Exit
from typing import Annotated
import xarray as xr
import cftime

# from .. import cdsapi_tools
from ..datasources import era5_daily, cmip6, topo, worldclim
from ..region.region import Region

from .. import pangeo_tools, file_tools

from joblib import Parallel, delayed, parallel_config

from . import common

HELP = """Tools to download data"""

app = Typer(help=HELP, no_args_is_help=True)

NAME = 'Download'



@app.command()
def ERA5_daily(
        context: Context,
        destination: common.DESTINATION_DIR,
        years: Annotated[tuple[int, int], Argument(help="Range of years to download ERA5 daily data for.")] = None,
        bounds: Annotated[tuple[float, float, float, float], Option(help="CDS area as NORTH WEST SOUTH EAST (degrees). Overrides the --use-region extent. Default without a region: 90 -180 30 180")] = None,
        buffer_degrees: Annotated[float, Option(help="Buffer in degrees added around the --use-region extent (snapped outward to the 0.25 degree ERA5 grid)")] = era5_daily.DEFAULT_BUFFER_DEGREES,
    ):
    """Downloads ERA5 daily reanalysis data from ECMWF.

    With --use-region only the region's lon/lat extent plus --buffer-degrees
    is requested, which is far faster than the default 30-90N band.
    """
    log = context.obj.log
    overwrite = context.obj.overwrite
    cleanup = context.obj.cleanup
    # parallel = context.obj.parallel
    # n_process = context.obj.n_process
    log.info('Starting download of ERA5-daily data.')

    ## always get the years aas a range. 
    years = common.years_as_range_check(years, True, [1940,2025])

    if bounds is not None:
        area = [float(b) for b in bounds]
    elif context.obj.region:
        extent = context.obj.region.get_extent(4326)
        area = era5_daily.bounds_from_extent(extent, buffer_degrees)
        log.info(f'Region lon/lat extent {tuple(round(float(e), 3) for e in extent)}')
    else:
        area = list(era5_daily.DEFAULT_BOUNDS)
        log.warn(
            f'No region or --bounds given: requesting the default area {area} '
            '(N, W, S, E). This is slow; pass --use-region or --bounds.'
        )
    log.info(f'CDS area (N, W, S, E): {area}')

    log.info(f'Processing years: {years}')
    # return

    log.info(f'Downloading from { era5_daily.COLLECTION_ID }.')
    for year in years:
        
        for variable in era5_daily.API_VARIABLES:
            yearly_files = []
            api_var = era5_daily.API_VARIABLES[variable]['name']
            save_to_final = destination/f'{year}-{api_var}.nc'
            log.info(f'.. Downloading {variable} for {year}.')
            if save_to_final.exists() and not overwrite:
                log.info(f'.... Yearly file exists, download skipped.')
                continue
            # client, requests = era5_daily.submit_variable_year(variable, year)

            def download_helper(date):
                year_h = date.year
                month = date.month
                day = date.day
                log.info(f'.... Downloading partial {variable} for {year_h}-{month:02}-{day:02}.')
                # file, status = era5_daily.download_variable_for_date(
                #     destination, variable, date, overwrite=False
                # )
                file, status = era5_daily.download_variable_for_year_month(
                    destination, variable, year_h, month, bounds=area,
                    overwrite=False
                )
                if status == 'skipped':
                    log.info(f'...... File exists, download skipped.')
                else:
                    log.info(f'...... Download complete.')
                return file

            try:
                with parallel_config(backend="loky", n_jobs=context.obj.get_n_process(), verbose=1):
                    yearly_files = Parallel()(
                        delayed(download_helper)(date) for date in xr.date_range(
                            f'{year}-01-01', f'{year}-12-31', freq = 'MS'
                        )
                    )
            except era5_daily.ERA5DownloadError as e:
                log.error(f'.... Download failed: {e}')
                raise Exit(1)


            log.info(f'.... Merging partials to {save_to_final.name}')
            datasets = [xr.open_dataset(f) for f in sorted(yearly_files)]
            final = xr.concat(datasets, dim='valid_time')
            final.to_netcdf(save_to_final)
            final.close()
            [ds.close() for ds in datasets]
            if cleanup:
                log.info(f'.... Cleanup: removing partials {[f.name for f in yearly_files]}.')
                [file.unlink(file) for file in yearly_files]
        
    log.info('Download of ERA5-daily data complete!')

@app.command()
def CMIP6_daily(
        context: Context,
        destination: common.DESTINATION_DIR,
        experiment: Annotated[str, Argument(help=f"Name of CMIP6 experiment from {cmip6.EXPERIMENTS}")],
        source_model: Annotated[str, Argument(help="Name of CMIP6 model that provides daily data (i.e. CESM2)")],
        years: Annotated[tuple[int, int], Argument(help="Start and end of years to download data for. Will default to full range of experiment provided (See Note on 'years').")] = None,
        ensemble: Annotated[str, Option(help=f"CMIP6 ensemble/member_id. Defaults per model: {cmip6.DEFAULT_ENSEMBLES}, otherwise {cmip6.DEFAULT_ENSEMBLE}")] = None,
        buffer_cells: Annotated[float, Option(help="Number of native GCM grid cells to download around the region so coarse cells fully cover its edges")] = 2,
        allow_missing: Annotated[bool, Option(help=f"Download even if required variables ({', '.join(cmip6.REQUIRED_SOURCE_VARS)}) are missing for the member")] = False,
    ):
    """This command downloads CMIP6 daily data from Pangeo. 
    
    Note on `years`
        - For historical experiments these values must be between 1850 and 2014 inclusive.
        - For projected experiments these values must be between 2015 and 2100 inclusive.
        - The start year must be less than or equal to end year
        - When not provided values will default to the appropriate minimum or maximum value.   
    """ 
    #TODO: implement overwrite flag to protect existing data
    log = context.obj.log 
    log.info('Starting download of cmip6-daily data.')
    if experiment not in cmip6.EXPERIMENTS:
        log.error(f'bad experiment try one of {cmip6.EXPERIMENTS} ')
        return

    start_year, end_year = years if years else (None, None)
    if experiment == 'historical':
        start_year = 1901 if start_year is None else start_year
        end_year = 2014 if end_year is None else end_year
        if start_year < 1850:
            log.error(f"Start year must be greater than or equal to  1850 for historical experiments. Was given {start_year}.")
            return
        if end_year > 2014:
            log.error(f"End year must be less than or equal to than 2014 for historical experiments. Was given {end_year}.")
            raise Exit(1)
    else:
        start_year = 2015 if start_year is None else start_year
        end_year =  2100 if end_year is None else end_year
        if start_year < 2015:
            log.error(f"Start year must be greater than or equal to  2015 for projected (ssp) experiments. Was given {start_year}.")
            return
        if end_year > 2100:
            log.error(f"End year must be less than or equal to than 2100 for projected (ssp) experiments. Was given {end_year}.")
            raise Exit(1)
    if start_year > end_year:
        log.error(f"Start year must be less than or equal to End year. Start year was {start_year}, and end year {end_year}.")
        return
    log.info(f'For years {start_year} - {end_year}')
    # Calendar-neutral bounds; pangeo_tools.download converts them to the
    # model's own calendar (noleap, proleptic_gregorian, 360_day, ...).
    time_bounds = (
        cftime.datetime(start_year, 1, 1, calendar='standard'),
        cftime.datetime(end_year+1, 1, 1, calendar='standard')
    )
    if ensemble is None:
        ensemble = cmip6.default_ensemble(source_model)
    log.info(f'Searching catalog for daily {source_model}, {experiment}, {ensemble}')
    all_members = cmip6.search_pangeo(source_model, experiment, None)
    items = all_members[all_members['member_id'] == ensemble]
    log.info(f'.. Found {len(items)} items: {sorted(items["variable_id"])}')

    missing_required, missing_optional = cmip6.check_variables(items['variable_id'])
    if missing_optional:
        tem_names = {'tasmin': 'tair_min', 'tasmax': 'tair_max', 'sfcWind': 'wind'}
        log.warn(
            f'{source_model} {experiment} {ensemble} has no daily '
            f'{missing_optional} on Pangeo; '
            f'{[tem_names.get(v, v) for v in missing_optional]} '
            'will not be produced from CMIP6.'
        )
    if missing_required:
        suggest = cmip6.members_with_variables(all_members, cmip6.REQUIRED_SOURCE_VARS)
        msg = (
            f'{source_model} {experiment} {ensemble} is missing required '
            f'daily variables {missing_required}. '
            f'Members with all of {cmip6.REQUIRED_SOURCE_VARS}: {suggest or "none"}. '
            'Pass one with --ensemble, or use --allow-missing to download anyway.'
        )
        if allow_missing:
            log.warn(msg)
        else:
            log.error(msg)
            raise Exit(1)

    spatial_bounds = (0, 30, 360, 90)
    if context.obj.region:
        area = context.obj.region

        spatial_bounds = list(area.get_extent(4326, 1))
        # pangeo data is 0..360; pangeo_tools.download handles the wrap
        spatial_bounds[0]+=360
        spatial_bounds[2]+=360
        spatial_bounds = tuple(spatial_bounds)

    log.info(f'Target bounds are {spatial_bounds} (+{buffer_cells} GCM cells buffer)')
    for row in items.index:
        meta = items.loc[row].to_dict() 
        log.info(
            f"Downloading {meta['source_id']}-{meta['experiment_id']}-{meta['variable_id']}"
        )
        save_to = destination/f"cmip6-day-{meta['source_id']}-{meta['experiment_id']}-{meta['variable_id']}.nc"
        log.info(f'.. saving to {save_to}')
        if not save_to.exists():
            ds = pangeo_tools.download(
                save_to,
                meta['zstore'], 
                time_bounds, 
                spatial_bounds,
                zlib= True, complevel= 9,
                buffer_cells=buffer_cells,
            )
            ds.close()
    log.info('Download of cmip6-daily data complete!')


@app.command()
def WorldClim(
        context: Context,
        destination: common.DESTINATION_DIR,
        variables: Annotated[str, Option(help=f"Comma separated WorldClim variables to download from {worldclim.VARS}, or 'all'")] = 'all',
        resolution: Annotated[str, Option(help=f"WorldClim resolution, one of {worldclim.RESOLUTIONS}")] = '30s',
        version: Annotated[str, Option(help="WorldClim version")] = '2.1',
    ):
    """Downloads WorldClim (v2.1) 1970-2000 monthly climate normals, the
    high resolution reference used for delta downscaling.

    Archives are saved as-is (about 19 GB for all variables at 30s) and
    are read directly by `preprocess worldclim` without extracting them.
    Existing archives are skipped unless --overwrite is given.
    """
    log = context.obj.log
    overwrite = context.obj.overwrite
    in_vars = worldclim.VARS if variables == 'all' else [v.strip() for v in variables.split(',')]
    bad = [v for v in in_vars if v not in worldclim.VARS]
    if bad or resolution not in worldclim.RESOLUTIONS:
        log.error(f'Invalid variables {bad} or resolution {resolution}. Variables: {worldclim.VARS}; resolutions: {worldclim.RESOLUTIONS}')
        raise Exit(1)
    log.info(f'Downloading WorldClim {version} {resolution} {in_vars} to {destination}')
    files = worldclim.download(destination, in_vars, version, resolution, overwrite, log)
    log.info(f'WorldClim download complete: {[str(f) for f in files.values()]}')


@app.command()
def elevation(
        context: Context,
        destination: common.DESTINATION_DIR,
        # url: Annotated[str, Option(help='URL to an elevation data.')] = topo.URL 
    ):
    """This command downloads the the mn75_grd elevation from the USGS data 
    used in creating the TEM input topography dataset
    """
    #TODO: Is there a case were a user may want to download a different 
    # elevation data set to use
    log = context.obj.log
    overwrite = context.obj.overwrite
    cleanup = context.obj.cleanup
    log.info('downloading elevation')
    url = topo.URL
    data_path = file_tools.download(url, destination, overwrite)

    log.info('extracting')
    file_tools.extract(data_path, data_path.parent)/data_path.stem 

    log.info('Download of elevation data complete!')
