"""
CLI tools for exporting data to a model specific format.
---------------------------------------------------------

TODO:
  Everything.
"""

import pathlib

from typer import Typer, Argument, Option, Context #, Option
from typing import Annotated

import temds.cli.common
import temds.constants




HELP = """Tools to export data to a model specific format."""

app = Typer(help=HELP, no_args_is_help=True, invoke_without_command=True)

NAME = 'Export'

@app.callback()
def export_model(
        context: Context,
        destination: temds.cli.common.DESTINATION_DIR,
        format: Annotated[str, Option(help="Model format to export to.")] = None,
        which: Annotated[str, Option(help="Which dataset(s) to export. Use 'all' or a comma-separated list of dataset names (see temds.constants.TEMDS_DATASET_NAMES).")] = 'all',
        from_directory: Annotated[pathlib.Path, Option(help="Directory to read data from. Will default to region directory if --use-region is provided")] = None,
        cmip_item: Annotated[str, Option(help="Region item exported as cmip_climate (written to <item>-projected-climate.nc)")] = 'cmip6-ssp245-downscaled',
        cru_item: Annotated[str, Option(help="Region item exported as cru_climate (written to <item>-historic-climate.nc)")] = 'crujra-downscaled',
        era5_item: Annotated[str, Option(help="Region item exported as era5_climate (written to <item>-historic-climate.nc). Not part of --which all")] = 'era5-downscaled',
    ):
    """This command exports data to a model specific format"""
    log = context.obj.log
    overwrite = context.obj.overwrite
    # cleanup = context.obj.cleanup
    # parallel = context.obj.parallel
    # n_process = context.obj.get_n_process()

    # This ends up being slow to check because the context is loaded first
    # which requires loading the region data. Unless you use the --no-load-all flag.
    assert format == 'TEM', "Only TEM format is currently supported. Please specify --format TEM or implement a new format (see src/temds/cli/export.py)."

    log.info(f"Exporting data to TEM format. Destination: {destination}, Which dataset(s): {which}, From directory: {from_directory}")

    r = context.obj.region
    if r is None:
        log.error("No region provided. Please provide a region with the --use-region flag to export data.")
        return

    if which == 'all':
        log.info("Exporting all datasets...")
        dataset_list = temds.constants.TEMDS_DEFAULT_EXPORT_NAMES
    else:
        dataset_list = [name.strip() for name in which.split(',')]
        invalid = [name for name in dataset_list if name not in temds.constants.TEMDS_DATASET_NAMES]
        if invalid:
            raise ValueError(f"Invalid dataset name(s) for TEM export: {invalid}. Must be one of {temds.constants.TEMDS_DATASET_NAMES}.")
        log.info(f"Exporting datasets: {dataset_list}")

    source_directory = from_directory or context.obj.region_directory or destination
    for dataset_name in dataset_list:
        ret_code = r.export_TEM(
            dataset_name=dataset_name, where=destination,
            source_directory=source_directory,
            cmip_item=cmip_item, cru_item=cru_item, era5_item=era5_item,
        )
        if ret_code:
            log.error(f"Failed to export dataset: {dataset_name}")




# Testing commands:

# Exporting....
# TEMdownscale \
#     --use-region working/02-qdm-temrs-site2a/ \
#     export --format TEM \
#     working/

# Ran into problem with veg not existing. So had to run this:
# TEMdownscale \
#   --overwrite \
#   --use-region working/02-qdm-temrs-site2a \
#   --no-load-all \
#   downscale extra-tem-files working/02-qdm-temrs-site2a/
