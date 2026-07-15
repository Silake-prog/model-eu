#!/usr/bin/env python3
"""Quick diagnostic: check what the solver actually sees after sanitisation."""
import xarray as xr
import numpy as np

ds = xr.open_dataset('results/diagnostics/input_dataset_2050.nc')

print('=== DIMENSIONS ===')
for d in sorted(ds.dims):
    print(f'  {d}: {ds.dims[d]}')

print()
print('=== VRE investment bounds (Solar, Wind_Onshore) ===')
for area in ['DE', 'FR', 'ES', 'IT']:
    for tech in ['Solar', 'Wind_Onshore']:
        try:
            cap_min = float(ds['conversion_power_capacity_min'].sel(area=area, conversion_tech=tech).values.flat[0])
            cap_max = float(ds['conversion_power_capacity_max'].sel(area=area, conversion_tech=tech).values.flat[0])
            inv_min = float(ds['conversion_power_capacity_investment_min'].sel(area=area, conversion_tech=tech).values.flat[0])
            inv_max = float(ds['conversion_power_capacity_investment_max'].sel(area=area, conversion_tech=tech).values.flat[0])
            print(f'{area} {tech}: cap=[{cap_min:.0f}, {cap_max:.0f}] inv=[{inv_min:.0f}, {inv_max:.0f}] gap={cap_min - inv_max:.0f}')
        except Exception:
            print(f'{area} {tech}: not found')

print()
print('=== Hydro investment bounds ===')
for area in ['DE', 'FR', 'ES']:
    for tech in ['RoR_Hydro', 'Reservoir_Hydro_Inflow', 'Reservoir_Hydro_Plant']:
        try:
            inv_min = float(ds['conversion_power_capacity_investment_min'].sel(area=area, conversion_tech=tech).values.flat[0])
            inv_max = float(ds['conversion_power_capacity_investment_max'].sel(area=area, conversion_tech=tech).values.flat[0])
            cap_min = float(ds['conversion_power_capacity_min'].sel(area=area, conversion_tech=tech).values.flat[0])
            print(f'{area} {tech}: cap_min={cap_min:.0f} inv=[{inv_min:.0f}, {inv_max:.0f}]')
        except Exception:
            print(f'{area} {tech}: not found')

print()
print('=== H2 resources & transport ===')
print('Resources:', list(ds.resource.values))
if 'transport_tech' in ds.dims:
    print('Transport techs:', list(ds.transport_tech.values))
    if 'transport_resource' in ds:
        print('Transport resource:', ds['transport_resource'].values)
else:
    print('No transport_tech dimension')
if 'link' in ds.dims:
    links = list(ds.link.values)
    print(f'Links ({len(links)}):', links[:10], '...' if len(links) > 10 else '')

print()
print('=== NaN in key variables ===')
for v in [
    'conversion_power_capacity_investment_max',
    'conversion_power_capacity_investment_min',
    'storage_energy_to_power_ratio',
    'conversion_end_of_life',
    'storage_end_of_life',
]:
    if v in ds:
        arr = ds[v].values
        if np.issubdtype(arr.dtype, np.number):
            n_nan = int(np.isnan(arr).sum())
            print(f'  {v}: shape={arr.shape} NaN={n_nan}')
        else:
            print(f'  {v}: shape={arr.shape} dtype={arr.dtype}')

print()
print('=== All conversion techs per area ===')
if 'conversion_tech' in ds.dims:
    techs = list(ds.conversion_tech.values)
    print(f'  Total: {len(techs)} — {techs}')

print()
print('=== Spillage & load shedding ===')
for v in ['spillage_max_capacity', 'spillage_cost', 'load_shedding_cost']:
    if v in ds:
        vals = ds[v].values
        print(f'  {v}: {vals}')

print()
print('=== Storage techs ===')
if 'storage_tech' in ds.dims:
    stechs = list(ds.storage_tech.values)
    print(f'  Total: {len(stechs)} — {stechs}')
    if 'storage_energy_to_power_ratio' in ds:
        ratio = ds['storage_energy_to_power_ratio']
        for st in stechs:
            try:
                val = float(ratio.sel(storage_tech=st).values.flat[0])
                print(f'    {st}: E/P ratio = {val}')
            except Exception:
                print(f'    {st}: E/P ratio = (error reading)')
