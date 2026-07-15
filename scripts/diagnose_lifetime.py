#!/usr/bin/env python3
"""Diagnose end_of_life values — are they derived from life_span correctly?"""
import xarray as xr
import numpy as np

ds = xr.open_dataset('results/diagnostics/input_dataset_2050.nc')

year_inv = int(ds.year_inv.values[0])
print(f"year_inv = {year_inv}")
print(f"year_dec range = {int(ds.year_dec.values.min())} .. {int(ds.year_dec.values.max())}")
print()

# Conversion end_of_life
print("=== conversion_end_of_life (should be year_inv + life_span) ===")
eol = ds['conversion_end_of_life']
techs = list(ds.conversion_tech.values)
areas_sample = ['DE', 'FR', 'ES']

for tech in techs:
    vals = []
    for area in areas_sample:
        try:
            v = int(eol.sel(conversion_tech=tech, area=area).values.flat[0])
            vals.append(v)
        except Exception:
            vals.append('?')
    # All same? Just show one
    if len(set(str(x) for x in vals)) == 1:
        implied_lifespan = vals[0] - year_inv if isinstance(vals[0], int) else '?'
        print(f"  {tech:30s}: end_of_life={vals[0]}  (implied life_span={implied_lifespan})")
    else:
        print(f"  {tech:30s}: varies by area: {dict(zip(areas_sample, vals))}")

print()
print("=== storage_end_of_life ===")
seol = ds['storage_end_of_life']
stechs = list(ds.storage_tech.values)
for stech in stechs:
    vals = []
    for area in areas_sample:
        try:
            v = int(seol.sel(storage_tech=stech, area=area).values.flat[0])
            vals.append(v)
        except Exception:
            vals.append('?')
    if len(set(str(x) for x in vals)) == 1:
        implied_lifespan = vals[0] - year_inv if isinstance(vals[0], int) else '?'
        print(f"  {stech:30s}: end_of_life={vals[0]}  (implied life_span={implied_lifespan})")
    else:
        print(f"  {stech:30s}: varies by area: {dict(zip(areas_sample, vals))}")

print()
print("=== transport_end_of_life ===")
if 'transport_end_of_life' in ds:
    teol = ds['transport_end_of_life']
    ttechs = list(ds.transport_tech.values)
    for ttech in ttechs:
        try:
            v = int(teol.sel(transport_tech=ttech).values.flat[0])
            implied = v - year_inv
            print(f"  {ttech:30s}: end_of_life={v}  (implied life_span={implied})")
        except Exception as e:
            print(f"  {ttech:30s}: error — {e}")
else:
    print("  (not present in dataset)")

print()
print("=== year_dec dimension vs end_of_life ===")
year_decs = sorted(int(y) for y in ds.year_dec.values)
print(f"  year_dec values ({len(year_decs)}): {year_decs[0]} .. {year_decs[-1]}")
# Check: does year_dec cover all end_of_life values?
all_eols = set()
for v in ['conversion_end_of_life', 'storage_end_of_life']:
    if v in ds:
        arr = ds[v].values.flatten()
        for val in arr:
            if np.isfinite(val) and val > 0:
                all_eols.add(int(val))
print(f"  Distinct end_of_life values: {sorted(all_eols)}")
missing = [e for e in all_eols if e not in year_decs]
if missing:
    print(f"  WARNING: end_of_life values NOT in year_dec: {missing}")
    print(f"  → planning_capacity mask will be False for these techs!")
else:
    print(f"  All end_of_life values are within year_dec range ✓")

print()
print("=== Conversion investment feasibility check ===")
# For each tech/area: can at least one (year_dec, year_inv) pair satisfy the mask?
# mask: (year_inv < year_dec) * (year_dec <= end_of_life) OR year_dec == end_of_life
for tech in techs:
    problems = []
    for area in list(ds.area.values):
        try:
            eol_val = int(eol.sel(conversion_tech=tech, area=area).values.flat[0])
            inv_min = float(ds['conversion_power_capacity_investment_min'].sel(
                area=area, conversion_tech=tech).values.flat[0])
            # Check if any year_dec satisfies the mask
            valid_decs = [y for y in year_decs if (year_inv < y <= eol_val) or y == eol_val]
            if inv_min > 0 and len(valid_decs) == 0:
                problems.append(f"{area}(eol={eol_val}, inv_min={inv_min:.0f})")
        except Exception:
            pass
    if problems:
        print(f"  {tech}: NO VALID PLANNING WINDOW — {problems}")
    # Just report summary for OK techs
    else:
        sample_eol = int(eol.sel(conversion_tech=tech, area='DE').values.flat[0])
        print(f"  {tech}: OK (eol={sample_eol})")
