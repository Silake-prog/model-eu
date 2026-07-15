"""Electricity load projection — country-level load curve scaling.

This module was previously ``demandforge/load_projection.py`` (single file).
It is now part of the ``load_projection`` package to accommodate the new
hydrogen projection submodule alongside it.

Public API:
    project_load_curve(reference_year, country, target_year, ...) -> pd.DataFrame
"""
from __future__ import annotations

import logging
import os
import pandas as pd
import urllib.request
import urllib.error
from typing import Optional

import pyarrow

from demandforge import RESULTS_DIR


def _get_combined_load_data(country: str, year: int) -> pd.DataFrame:
    """Retrieves the combined load data for a given country and year.

    Downloads the data from a remote URL if it's not available locally.

    Args:
        country (str): The country code (e.g., 'DE').
        year (int): The reference year.

    Returns:
        pd.DataFrame: A pandas DataFrame with the combined load data.
    """
    file_name = f"combined_load_{country}_{year}.parquet"
    data_dir = RESULTS_DIR / "combined_load"
    data_dir.mkdir(parents=True, exist_ok=True)
    file_path = data_dir / file_name
    url = f"https://storage.googleapis.com/demandforge/{file_name}"

    def _download_atomic() -> None:
        # Download to a per-process temp then atomically replace. Prevents the
        # concurrent-download race (many SLURM jobs share this cache dir) that
        # left TRUNCATED parquets — which the projection turned into implausible
        # peak spikes (e.g. SE-2021/2022 → 409 GW).
        tmp = file_path.with_name(f"{file_name}.{os.getpid()}.tmp")
        try:
            urllib.request.urlretrieve(url, tmp)
            os.replace(tmp, file_path)  # atomic on the same filesystem
        except Exception:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
            raise

    def _load_valid() -> pd.DataFrame:
        df = pd.read_parquet(file_path)
        # Integrity: a full year is 8760/8784 hours. Fewer rows = truncated download
        # (the exact failure that made winter_thermosensitive sum ≈ 0 → runaway scaling).
        if len(df) < 8000:
            raise ValueError(f"combined_load '{file_name}' truncated: {len(df)} rows (<8000).")
        return df

    for attempt in (1, 2):
        if not file_path.is_file():
            logging.info(f"'{file_path}' not found. Downloading (atomic) from {url} ...")
            try:
                _download_atomic()
            except urllib.error.URLError as e:
                logging.error(f"Failed to download {url}. Error: {e}")
                raise
        try:
            return _load_valid()
        except Exception as e:
            logging.warning(
                f"combined_load '{file_name}' invalid on attempt {attempt} ({e}); "
                f"discarding cache and re-downloading."
            )
            try:
                file_path.unlink()
            except FileNotFoundError:
                pass
    raise ValueError(f"combined_load '{file_name}' still invalid after re-download; aborting to avoid a corrupt profile.")

def _calculate_total_energy_from_growth_rate(initial_energy: float, growth_rate: float, reference_year: int, target_year: int) -> float:
    """Calculates the total energy for a target year based on a growth rate."""
    return initial_energy * (1 + growth_rate) ** (target_year - reference_year)

def project_load_curve(
    reference_year: int,
    country: str,
    target_year: int,
    reference_ev_year: int = 2026,
    total_baseload_energy_target: Optional[float] = None,
    total_winter_thermosensitive_energy_target: Optional[float] = None,
    total_summer_thermosensitive_energy_target: Optional[float] = None,
    total_ev_energy_target: Optional[float] = None,
    baseload_yearly_growth_rate: Optional[float] = None,
    winter_thermosensitive_yearly_growth_rate: Optional[float] = None,
    summer_thermosensitive_yearly_growth_rate: Optional[float] = None,
    ev_yearly_growth_rate: Optional[float] = None
) -> pd.DataFrame:
    """Projects future country-level load curves based on a reference year and scaling factors.

    Args:
        reference_year (int): The year of the reference load curve data.
        country (str): The country for which to project the load.
        target_year (int): The year for which to project the load curve.
        reference_ev_year (int): The specific year to use for the reference EV load profile.
        total_baseload_energy_target (Optional[float]): Total baseload energy for the target year.
        total_winter_thermosensitive_energy_target (Optional[float]): Total winter thermosensitive energy for the target year.
        total_summer_thermosensitive_energy_target (Optional[float]): Total summer thermosensitive energy for the target year.
        total_ev_energy_target (Optional[float]): Total electric vehicle energy for the target year.
        baseload_yearly_growth_rate (Optional[float]): Yearly growth rate for the baseload.
        winter_thermosensitive_yearly_growth_rate (Optional[float]): Yearly growth rate for winter thermosensitive load.
        summer_thermosensitive_yearly_growth_rate (Optional[float]): Yearly growth rate for summer thermosensitive load.
        ev_yearly_growth_rate (Optional[float]): Yearly growth rate for electric vehicle load.

    Returns:
        pd.DataFrame: A pandas DataFrame with the projected total load curve.
    """
    try:
        df = _get_combined_load_data(country, reference_year)
    except pyarrow.ArrowInvalid:
        raise ValueError(f"Load data is unavailable for country '{country}' and reference year '{reference_year}'.")

    ev_col = f'ev_load_{reference_ev_year}'
    if ev_col not in df.columns:
        raise ValueError(f"EV load column '{ev_col}' not found in the data.")
    df['ev_load'] = df[ev_col]

    # Rename columns for easier processing
    df_processed = df[['baseload', 'winter_thermosensitive_load', 'summer_thermosensitive_load', 'ev_load']].copy()
    df_processed.columns = ['baseload', 'winter_thermosensitive', 'summer_thermosensitive', 'ev']

    targets = {}
    categories_info = {
        'baseload': {
            'col_name': 'baseload',
            'target_arg': total_baseload_energy_target,
            'growth_rate_arg': baseload_yearly_growth_rate
        },
        'winter_thermosensitive': {
            'col_name': 'winter_thermosensitive',
            'target_arg': total_winter_thermosensitive_energy_target,
            'growth_rate_arg': winter_thermosensitive_yearly_growth_rate
        },
        'summer_thermosensitive': {
            'col_name': 'summer_thermosensitive',
            'target_arg': total_summer_thermosensitive_energy_target,
            'growth_rate_arg': summer_thermosensitive_yearly_growth_rate
        },
        'ev': {
            'col_name': 'ev',
            'target_arg': total_ev_energy_target,
            'growth_rate_arg': ev_yearly_growth_rate
        }
    }

    for category, info in categories_info.items():
        target_val = info['target_arg']
        growth_rate_val = info['growth_rate_arg']
        col_name = info['col_name']

        if target_val is not None:
            targets[category] = target_val
            logging.debug(f"Using target energy for {category}: {target_val}")
        elif growth_rate_val is not None:
            initial_energy = df_processed[col_name].sum()
            calculated_target = _calculate_total_energy_from_growth_rate(
                initial_energy, growth_rate_val, reference_year, target_year
            )
            targets[category] = calculated_target
            logging.debug(f"Using growth rate for {category}. Initial energy: {initial_energy}, Growth rate: {growth_rate_val}, Calculated target: {calculated_target}")
        else:
            raise ValueError(f"Neither total energy target nor yearly growth rate provided for {category}.")

    df_proj = df_processed.copy()

    n_hours = len(df_proj)
    for category, target_energy in targets.items():
        reference_energy = df_proj[category].sum()
        ref_peak = df_proj[category].max()
        ref_mean = reference_energy / n_hours if n_hours else 0.0
        # A reference component can be DEGENERATE for a given weather year: either
        # (near-)zero energy, or a spike-like profile that is essentially noise --
        # e.g. Nordic summer cooling, which is a handful of isolated hours on a
        # near-zero base (peak/mean can exceed 500). Scaling such a shape by
        # target/reference amplifies the noise into a spurious system peak (the
        # SE-2017 108 GW artifact). In that case spread the target energy uniformly:
        # it preserves the (small) component energy without inventing a peak.
        peaky = ref_mean > 0 and (ref_peak / ref_mean) > 100.0
        if reference_energy <= 0 or peaky:
            if target_energy and target_energy > 0:
                df_proj[f'{category}_projected'] = target_energy / n_hours   # flat
                logging.warning(
                    f"Degenerate reference for '{category}' (energy={reference_energy:.3e}, "
                    f"peak/mean={ref_peak/ref_mean if ref_mean else float('nan'):.0f}); "
                    f"distributing target {target_energy:.3e} uniformly instead of scaling noise."
                )
            else:
                df_proj[f'{category}_projected'] = 0.0
            continue
        scaling_factor = target_energy / reference_energy
        df_proj[f'{category}_projected'] = df_proj[category] * scaling_factor
        logging.debug(f"Scaled {category} with factor {scaling_factor}. Reference energy: {reference_energy}, Target energy: {target_energy}")

    df_proj['total_load_projected'] = df_proj[[
        'baseload_projected',
        'winter_thermosensitive_projected',
        'summer_thermosensitive_projected',
        'ev_projected'
    ]].sum(axis=1)

    # Peak-sanity backstop. A degenerate reference component (sum ≈ 0 from a corrupt/
    # truncated profile) gives a runaway scaling_factor and a physically impossible peak.
    # A valid electrified 2050 profile has peak/mean ≈ 2–4; the SE corruption hit ≈ 20.
    # Fail loud rather than silently emit a spike (e.g. the 409 GW SE artifact).
    _tot = df_proj['total_load_projected']
    _mean = float(_tot.mean())
    if _mean > 0 and float(_tot.max()) / _mean > 8.0:
        raise ValueError(
            f"project_load_curve: implausible peak/mean={float(_tot.max())/_mean:.1f} for "
            f"'{country}'/{reference_year} (peak={float(_tot.max()):.0f}); likely a corrupt "
            f"reference profile — refusing to emit a spike."
        )

    return df_proj
