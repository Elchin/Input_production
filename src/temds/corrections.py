"""
Correction Factor functions
---------------------------

original bash script code is in each docstring


# this file is based on line 105 in downscaling.sh
   ## Compute the corrections
    # computing CRU-JRA vapor pressure based on Murray, F. W. 1967. 
    # “On the Computation of Saturation Vapor Pressure.” J. Appl. Meteor. 6 (1): 203–4 ; 
    # Shaman, J., and M. Kohn. 2009. “Absolute Humidity Modulates Influenza Survival, 
    # Transmission, and Seasonality.” PNAS 106 (9): 3243–8)
"""
import copy
import numpy as np

from . import climate_variables

def generic_delta_add(baseline, reference):
    """Generic correction factor calculation for additive
    delta downscaling  

    Parameters
    ----------
    baseline: xr.DataArray
        Baseline climate calculated from data to be downscaled
    reference: xr.DataArray
        High resolution climate reference
    
    Returns
    -------
    xr.DataArray
        correction factors
    """
    return reference - baseline 

## Robust multiplicative correction factors
## ----------------------------------------
## A multiplicative factor is reference / baseline. Where the coarse baseline
## is (close to) zero this ratio is meaningless: e.g. in polar night the CMIP6
## nirr baseline is ~1e-6 W/m^2 while WorldClim srad is ~1e-3, giving factors
## of ~1e5 that blow up any future (non-zero) radiation in those months.
##
## Choice made here (documented in README "Downscaling method"):
##   * where |baseline| < MIN_BASELINE[var] the factor is 1 ("no change"),
##     i.e. the coarse value is passed through. The downscaled climatology
##     then differs from the reference by at most ~MIN_BASELINE there.
##   * all remaining factors are clipped to [0, MAX_FACTOR[var]].
## NaN baselines stay NaN. Both limits can be overridden per call.
##
## Units are those of the *baseline* (monthly mean, or monthly sum for prec).
MIN_BASELINE = {
    'nirr': 1.0,     # W/m^2, monthly mean incoming shortwave
    'prec': 0.1,     # mm/month, monthly total
    'vapo': 1e-3,    # kPa
    'wind': 0.1,     # m/s
}
DEFAULT_MIN_BASELINE = 1e-6
MAX_FACTOR = {
    'nirr': 10.0,
    'prec': 20.0,
    'vapo': 10.0,
    'wind': 10.0,
}
DEFAULT_MAX_FACTOR = 20.0


def generic_delta_mul(baseline, reference, min_baseline=DEFAULT_MIN_BASELINE,
                      max_factor=DEFAULT_MAX_FACTOR):
    """Generic correction factor calculation for multiplicative
    delta downscaling  

    Parameters
    ----------
    baseline: xr.DataArray
        Baseline climate calculated from data to be downscaled
    reference: xr.DataArray
        High resolution climate reference
    min_baseline: float, optional
        Baselines with an absolute value smaller than this get a factor of
        1 (no change) instead of reference/baseline.
    max_factor: float or None, optional
        Upper limit for factors; None disables clipping.
    
    Returns
    -------
    xr.DataArray
        correction factors (dimensionless)
    """
    baseline = copy.deepcopy(baseline)
    small = np.abs(baseline) < min_baseline  # False for NaN
    safe_baseline = baseline.where(~small, 1)
    factors = reference / safe_baseline
    factors = factors.where(~small, 1.0)
    if max_factor is not None:
        factors = factors.clip(min=0, max=max_factor)
    return factors


def _mul_for(var):
    """Multiplicative factor function with the thresholds for `var`"""
    def fn(baseline, reference, **kwargs):
        kwargs.setdefault('min_baseline', MIN_BASELINE.get(var, DEFAULT_MIN_BASELINE))
        kwargs.setdefault('max_factor', MAX_FACTOR.get(var, DEFAULT_MAX_FACTOR))
        return generic_delta_mul(baseline, reference, **kwargs)
    fn.__name__ = f'generic_delta_mul[{var}]'
    fn.method = 'multiplicative'
    return fn


generic_delta_add.method = 'additive'
generic_delta_mul.method = 'multiplicative'


LOOKUP = {
    'temperature': generic_delta_add,
    'precipitation': _mul_for('prec'),
    'vapor-pressure': _mul_for('vapo'),
    'radiation': _mul_for('nirr'),
    'wind-speed': _mul_for('wind'),
    'tair_min': generic_delta_add,
    'tair_max': generic_delta_add,
    'tair_avg': generic_delta_add,
    'prec': _mul_for('prec'),  
    'nirr': _mul_for('nirr'),
    'vapo': _mul_for('vapo'),
    'wind': _mul_for('wind'),
} 


def calculate(var, baseline, reference, logger=None):
    """Calculate correction factors for `var` with unit handling.

    Units are compared with cf_units (so 'celsius' == 'degree_Celsius',
    'W/m^2' == 'kilogram-second^-3', 'mm' == '0.001 meter'). If they are
    different but convertible the baseline is converted to the reference
    units first; if they are not convertible a ValueError is raised.

    The result carries a `units` attribute: the reference units for
    additive factors, '1' (dimensionless) for multiplicative factors, and
    a `correction_method` attribute.

    Parameters
    ----------
    var: str
        key in LOOKUP
    baseline, reference: xr.DataArray
    logger: temds.logger.Logger, optional

    Returns
    -------
    xr.DataArray
    """
    func = LOOKUP[var]
    b_units = baseline.attrs.get('units')
    r_units = reference.attrs.get('units')
    if not (b_units and r_units):
        if logger:
            logger.warn(
                f'Variable {var} is missing units in the baseline ({b_units!r}) '
                f'or reference ({r_units!r}); factors are computed as-is.'
            )
    elif not climate_variables.units_equivalent(b_units, r_units):
        try:
            converted = climate_variables.convert_units(
                baseline.values, b_units, r_units
            )
        except ValueError as e:
            raise ValueError(
                f'Units for {var} are incompatible: baseline {b_units!r}, '
                f'reference {r_units!r} ({e})'
            )
        if logger:
            logger.info(f'Converting {var} baseline from {b_units} to {r_units}')
        baseline = baseline.copy(data=converted)

    current = func(baseline, reference)
    # keep long_name, standard_name, etc. from the baseline
    attrs = dict(baseline.attrs)
    method = getattr(func, 'method', None)
    if method == 'multiplicative':
        attrs['units'] = '1'
    elif r_units:
        attrs['units'] = r_units
    if method:
        attrs['correction_method'] = method
    current.attrs = attrs
    current.name = var
    return current
