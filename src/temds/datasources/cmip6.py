"""
CMIP 6
------
metadata for cmip6 data

"""
import numpy as np
from cf_units import Unit

from .. import climate_variables 
from ..constants import SECONDS_PER_DAY

from .. import pangeo_tools


NAME = 'CMIP 6'


CITATION = (

)


## REGISTER CLIMATE VARIABLES
climate_variables.register('tair_avg', NAME, 'tas')
climate_variables.register('prec', NAME, 'pr')
climate_variables.register('nirr', NAME, 'rsds')
climate_variables.register('spfh', NAME, 'huss')
climate_variables.register('psl', NAME, 'psl')
climate_variables.register('tair_min', NAME, 'tasmin')
climate_variables.register('tair_max', NAME, 'tasmax')
climate_variables.register('wind', NAME, 'sfcWind')

climate_variables.register_source_unit('tair_avg', NAME, Unit('kelvin'))
climate_variables.register_source_unit('tair_min', NAME, Unit('kelvin'))
climate_variables.register_source_unit('tair_max', NAME, Unit('kelvin'))

## the source unit is kg m-2 s-2 which i think is the same as mm/s
## so this should work
climate_variables.register_source_unit('prec', NAME, Unit(f'{SECONDS_PER_DAY} mm'))

VARS = climate_variables.aliases_for(NAME)
SOURCE_VARS = [v for v in VARS if v not in []]

## Source variables needed to produce the core TEM inputs
## (tair_avg, prec, nirr and, with topo, vapo from psl + huss + tas).
REQUIRED_SOURCE_VARS = ['tas', 'pr', 'rsds', 'huss', 'psl']
## Downloaded when the model/member provides them (tair_min, tair_max, wind).
OPTIONAL_SOURCE_VARS = ['tasmin', 'tasmax', 'sfcWind']

EXPERIMENTS = ['historical', 'ssp126', 'ssp245', 'ssp370', 'ssp585']
# DEFAULT_EXPERIMENTS = EXPERIMENTS
DEFAULT_MODELS=['ACCESS-CM2','MRI-ESM2-0', 'CESM2']

## Default ensemble member per model. Chosen (Pangeo catalog, Nov 2026) as
## the member with daily tas/pr/rsds/huss/psl for historical and ssp245/585:
##   ACCESS-CM2, MRI-ESM2-0: r1i1p1f1 (r4i1p1f1 only has `pr`/tasmax/tasmin)
##   CESM2: r4i1p1f1 (r1i1p1f1 has no ssp daily data)
## NOTE: on Pangeo CESM2 historical has no daily tasmin/tasmax, and CESM2
## ssp126/ssp370 and MRI-ESM2-0 ssp370 have no daily rsds.
DEFAULT_ENSEMBLES = {
    'ACCESS-CM2': 'r1i1p1f1',
    'MRI-ESM2-0': 'r1i1p1f1',
    'CESM2': 'r4i1p1f1',
}
DEFAULT_ENSEMBLE = "r1i1p1f1"


def default_ensemble(source_model):
    """Default ensemble member (member_id) for `source_model`"""
    return DEFAULT_ENSEMBLES.get(source_model, DEFAULT_ENSEMBLE)


def check_variables(available):
    """Compare available source variables with what downscaling needs

    Parameters
    ----------
    available: iterable[str]
        CMIP6 variable_ids found

    Returns
    -------
    missing_required: list[str]
    missing_optional: list[str]
    """
    available = set(available)
    missing_required = [v for v in REQUIRED_SOURCE_VARS if v not in available]
    missing_optional = [v for v in OPTIONAL_SOURCE_VARS if v not in available]
    return missing_required, missing_optional


def members_with_variables(items, variables):
    """List member_ids in a catalog search result having all `variables`

    Parameters
    ----------
    items: pd.DataFrame
        result of search_pangeo (with ensemble=None)
    variables: list[str]

    Returns
    -------
    list[str]
    """
    if len(items) == 0:
        return []
    have = items.groupby('member_id')['variable_id'].apply(set)
    return sorted(m for m, v in have.items() if set(variables) <= v)

def callback_psl_to_vapo(dataset, logger, **kwargs):
    """Add `vapo` (kPa) from sea level pressure, specific humidity, air
    temperature and elevation (from the region's preprocessed topo).

    Requires psl, huss/spfh and tas/tair_avg in `dataset`. If any is
    missing a warning is logged and `dataset` is returned without vapo.
    """
    func_name = 'cmip6.callback_psl_to_vapo'
    elevation = kwargs['elevation']
    needed = {'psl': ['psl'], 'tas': ['tas', 'tair_avg'], 'huss': ['huss', 'spfh']}
    absent = [k for k, names in needed.items() if not any(n in dataset for n in names)]
    if absent:
        logger.warn(f'{func_name}: cannot calculate vapo, missing {absent}')
        return dataset
 
    logger.info(f'{func_name}: Calculating pres from psl, elevation and, air temp')
    psl = dataset['psl']
    try:
        tas = dataset['tas'] 
    except KeyError:
        tas = dataset['tair_avg']
    pres = climate_variables.calculate_pres_from_psl(psl,tas, elevation)
    try:
        spfh = dataset['huss']
    except KeyError:
        spfh = dataset['spfh']

    logger.info(f'{func_name}: Calculating vapo kPa')
    dataset['vapo'] = climate_variables.calculate_vapo(pres, spfh)
    unit = climate_variables.CLIMATE_VARIABLES['vapo'].std_unit.name
    v_name = climate_variables.CLIMATE_VARIABLES['vapo'].name
    dataset['vapo'].attrs.update(units=unit, name=v_name)
    return dataset
    

def search_pangeo(source_model, experiment, ensemble):
    """Search pangeo for daily CMIP6 data. If ensemble is None, all
    members are returned."""
    parameters = {
        "experiment_id": [experiment],
        "table_id": ["day"],
        "variable_id": climate_variables.aliases_for('CMIP 6'),
        "source_id": [source_model],
    }
    if ensemble is not None:
        parameters["member_id"] = [ensemble]
    cat = pangeo_tools.connect()
    items = pangeo_tools.search(cat, parameters).df
    return items
