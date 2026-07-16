# Implementation prompt — Add a PECD 4.2 ⇄ ERA5 weather-data source switch to `supplyforge`

> **Audience:** an autonomous coding agent with full read/write access to the `supplyforge`
> repository. **Goal:** make the *weather-dependent* model inputs **configurable by source**.
> Renewable (wind & solar) capacity factors must be selectable between three sources —
> the existing **ENTSO-E** observed path, a new in-repo **raw-ERA5** engine (`atlite`), and the
> **Pan-European Climate Database v4.2 (PECD 4.2)** CDS product — via a `res_source` config key.
> Hydro inflows (reservoir + run-of-river) are selected **separately** via `hydro_source`
> (ENTSO-E reconstruction or PECD), because raw ERA5 cannot practically produce inflows. You will
> add new fetch + process modules for the ERA5 and PECD sources and wire them into the Snakemake
> workflow and config — **without breaking the existing ENTSO-E path** and **with particular
> methodological care for hydro**.
>
> This document is the specification. Read it fully before writing code. Where it says
> **"DECISION"**, the choice is genuinely consequential — implement the stated default, but
> surface the decision in your PR description so the maintainer can override it.

---

## 0. Operating rules

1. **Do not regress the existing pipeline.** The ENTSO-E path
   (`generation.py` → `capacity_factor.py` / `availability.py`; `hydro_storage.py` +
   `generation.py` → `hydro_inflow.py`) must keep working byte-for-byte when the new source is
   not selected. Add, branch, and namespace — do not rewrite working modules in place beyond
   what is specified.
2. **Preserve the downstream data contract.** `create_pommes_craft_model.py` reads canonical
   Parquet files via `supplyforge.utils._get_input_data_file(country, year, input_file)`, i.e.
   `RESULTS_DIR/<input_file>/<input_file>_<COUNTRY>_<year>.parquet`. Any PECD output you produce
   must be consumable by the *unmodified* model-assembly functions (`add_intermittent_tech`,
   `add_reservoir_hydro`) — same paths, same columns, same dtypes — unless this document
   explicitly authorises a model-side change.
3. **Follow repo conventions:** Polars for tabular transforms, Parquet outputs, Google/NumPy
   docstrings with a module-level methodology block (mirror the style of
   `process/capacity_factor.py` and `process/eraa_capacity_factor.py`), secrets via `.env` +
   `python-dotenv` (never hard-coded), and the **fail-soft** convention: on missing/empty
   inputs, write a *valid empty Parquet with the correct schema* (or `touch()` where the existing
   sibling does) and log a warning instead of crashing the DAG.
4. **No silent science.** Every methodological assumption (unit conversion, weekly→hourly
   disaggregation, zero-clipping, zone→country aggregation, climate-year mapping) must be stated
   in the module docstring and logged at runtime.
5. **Cite the data.** PECD 4.2 lives on the Copernicus Climate Data Store
   (dataset id `sis-energy-pecd`); record the version (`pecd4_2`), origin, model/scenario, and
   spatial level used in every output file's metadata or an accompanying `download.txt`.
6. **Repository access & boundaries.** All code you write lands in **`supplyforge`** only.
   The sibling **`demandforge`** package is a **read-only reference** — do not modify it.
   - The **PECD path** (`res_source: pecd` and/or `hydro_source: pecd`) needs **no** `demandforge`
     access: PECD delivers pre-aggregated zonal data, so no country geometries are required.
   - The **raw-ERA5 engine** (`res_source: era5`, §4.9) needs **per-country geometries / bounding
     boxes** to build `atlite` cutouts. These live in `demandforge` (`country_borders.py` generates
     `country_borders.gpkg`; `era5.py` exposes `get_country_bbox` / `filter_geometries_to_europe`
     and the `{"GR":"EL"}` remap). `supplyforge` itself has **no** geopackage or bbox helper (only a
     config comment referencing `CNTR_ID` from `country_borders.gpkg`). Therefore: **request
     read access to the `demandforge` folder** and reuse those helpers/the geopackage, OR — if
     `demandforge` is unavailable — source country geometries independently from a public dataset
     (GISCO / Eurostat NUTS or Natural Earth, both `atlite`/`geopandas`-compatible) and replicate
     the GR→EL remap. State in the PR which route you took. `supplyforge` must **not** take a hard
     import dependency on `demandforge`; copy/port the small helpers instead.

---

## 1. Repository architecture you are modifying

`supplyforge` is a Snakemake workflow + a `pommes_craft` model-assembly API. Two stages:

**fetch** (`workflow/rules/fetch.smk`, scripts in `supplyforge/fetch/`) →
**process** (`workflow/rules/process.smk`, scripts in `supplyforge/process/`) →
canonical Parquet in `RESULTS_DIR/<type>/<type>_<COUNTRY>_<year>.parquet` →
`create_pommes_craft_model.py` builds the `EnergyModel`.

`RESULTS_DIR` is resolved in `supplyforge/__init__.py` (package `results/` dir, or
`$SUPPLYFORGE_DATA`, or platform user-data dir). Config is `config/config.yaml`
(`countries`, `years`).

### The two weather-dependent chains you are adding a source to

**(A) Intermittent renewable capacity factors** — `process/capacity_factor.py`
`calculate_intermittent_capacity_factors(country, year)`:
- Inputs: `generation_<C>_<y>.parquet` (ENTSO-E, wide, tuple-named cols like
  `('Solar','Actual Aggregated')`) and `installed_capacities_<C>_<y>.parquet`.
- Computes per intermittent plant type and hour: `CF = generation / installed_capacity`
  (else 0), resampled to hourly UTC.
- Plant types handled: `Solar`, `Wind Offshore`, `Wind Onshore`,
  `Hydro Run-of-river and poundage`.
- **Output schema (canonical):** columns `plant_type` (str), `hour` (datetime, UTC),
  `capacity_factor` (f64), `installed_capacity` (f64), `generation_mw` (f64).
- The prospective sibling `process/eraa_capacity_factor.py` writes a **different** schema to the
  *same* `capacity_factors/` directory: `hour` (int 0–8759), `plant_type` (categorical), `WS`
  (categorical weather scenario), `capacity_factor` (f64). **Both are valid** because the
  consumer tolerates either (see below).

**(B) Reservoir hydro inflow** — `process/hydro_inflow.py`
`generate_hourly_inflow_dataset(...)`:
- Inputs: hourly reservoir generation `('Hydro Water Reservoir','Actual Aggregated')` from
  `generation_*`, and weekly `storage_mwh` from `hydro_storage_*`.
- Water-balance reconstruction: `ΔS_w = S_w − S_{w-1}`; weekly inflow `I_w = G_w + ΔS_w`;
  linearly interpolate weekly→hourly, **clip negatives to 0**, convert weekly energy to a power
  rate `I_t^MW = Ĩ_t^{MWh/week} / 168`.
- Forces exactly 52 Monday week-starts (`create_week_start`), heuristically trims a 52/53/51-row
  stock table, full-year hourly UTC index.
- **Output schema (canonical):** columns `inflow_MW` (f64), `timestamp` (datetime, UTC), 8760 rows.

### How the consumer uses these (the contract you must honour)

`create_pommes_craft_model.py`:
- `add_intermittent_tech(area, tech_name, plant_type, capacity, reference_year, ws=None, ...)`:
  reads `capacity_factors_<C>_<ref_year>.parquet` via `_get_input_data_file`; filters
  `pl.col("plant_type") == plant_type` (falling back to `== tech_name`); **if `ws` is not None**
  it additionally filters `pl.col("WS") == ws` and **raises** if there is no `WS` column. Then it
  pads/truncates to exactly 8760 rows, renames `capacity_factor`→`availability`, and injects it
  as a `ConversionTechnology.availability`. `INTERMITTENT_TECH_DICT` maps
  `{"Solar":"Solar", "Wind_Onshore":"Wind Onshore", "Wind_Offshore":"Wind Offshore",
  "RoR_Pondage":"Hydro Run-of-river and poundage"}`.
- `add_reservoir_hydro(area, installed_capacities, inflows, ..., country, eraa_year=2026)`:
  turbine capacity from `installed_capacities["Hydro Water Reservoir"]`; **reservoir energy
  (MWh) storage capacity from ERAA `Storage.xlsx`** via `get_storage_energy_capacities(country,
  "Reservoir", "Hydro", eraa_year)` (NOT from inflow data); the inflow series is turned into a
  **shape-normalised** must-run profile: `availability = inflow_MW / inflow_MW.max()` and
  `power_capacity = inflow_MW.max()` on the `reservoir_water` resource. **Key consequence:** only
  the *shape* (temporal profile) of the inflow matters downstream; the absolute MWh scale
  cancels. Your PECD inflow must therefore deliver a correct **temporal profile**; exact unit
  calibration is secondary (but still log it).
- `add_pumped_hydro(...)`: PHS turbine capacity from `installed_capacities["Hydro Pumped
  Storage"]`, **energy capacity from ERAA** (`CL pumping` + `OL pumping`). **No inflow is used
  for PHS today.**

The model's reservoir is the `reservoir_water`-resource storage + must-run inflow + dispatchable
plant triplet (cf. POMMES lake state equation `S_{t+1}−S_t = I_t − P_t − spill`). The PECD
reservoir **inflow** `I_t` is exactly the must-run inflow signal.

---

## 2. PECD 4.2 — authoritative data spec (from the v4.2 Product User Guide)

**Access:** Copernicus CDS, dataset id **`sis-energy-pecd`**, Python client **`cdsapi`**, endpoint
`https://cds.climate.copernicus.eu/api`. Requires a CDS account + API key in `~/.cdsapirc` (or
passed explicitly), and **per-dataset Terms & Conditions acceptance**. The version is an explicit
request field: **`pecd_version: "pecd4_2"`** (do **not** use the deprecated `pecd4_1`).

**Origin / temporal period (this is the "ERA5 vs projection" axis):**
- `temporal_period: ["historical"]`, `origin: ["era5_reanalysis"]` → **ERA5 reanalysis stream**,
  hourly, **1950–near-present**, updated monthly. ← *this is the "ERA5 reanalysis" option*.
- `temporal_period: ["future_projections"]` (note the **plural**), `origin: [<model>]` →
  **2015–2100**. **The climate model is the `origin` value** (one of `cmcc_cm2_sr5`, `ec_earth3`,
  `mpi_esm1_2_hr`, `awi_cm_1_1_mr`, `bcc_csm2_mr`, `mri_esm2_0`) — there is **no** separate
  `climate_model` request field. Add `emission_scenario: [<ssp>]` with one of `ssp1_2_6`,
  `ssp2_4_5`, `ssp3_7_0`, `ssp5_8_5`.
  ⚠ For **`bcc_csm2_mr` + `ssp2_4_5` wind**, and for MENA climate aggregations, request
  `file_version: ["fv2"]` (the reprocessed files); otherwise `fv1`.

**Energy variables & codes (this dataset is *not* capacity-factor-only):**

| Code | Meaning | Type | Native res. | Aggregation |
|------|---------|------|-------------|-------------|
| `SPV` | Solar PV capacity factor | CF ∈[0,1] | hourly | ORIG/NUT0/NUT2/SZON/PEON/P2ON |
| `WON` | Wind onshore capacity factor | CF | hourly | **PEON/P2ON** (NUT0 deprecated) |
| `WOF` | Wind offshore capacity factor | CF | hourly | **PEOF/P2OF** (NUT0 deprecated) |
| `CSP` | Concentrated solar CF | CF | hourly | PEON/P2ON |
| `HRI` | **Reservoir inflow energy** | Energy (MWh) | **weekly** | **SZON** |
| `HRG` | Reservoir generation energy | Energy | weekly | SZON |
| `HRR` | Run-of-river inflow energy | Energy | weekly | SZON |
| `HRO` | Run-of-river generation energy | Energy | weekly | SZON |
| `HPI` | RoR-with-pondage inflow energy | Energy | weekly | SZON |
| `HPO` | RoR-with-pondage generation energy | Energy | weekly | SZON |
| `HOL` | Open-loop pumped-storage inflow energy | Energy | weekly | SZON |

Closed-loop pumped storage has **no output variable** (used only internally for efficiency).
There is **no reservoir storage-volume (MWh capacity) variable** — PECD gives inflow/generation
*flows*, not reservoir size. ⇒ **Keep taking reservoir & PHS energy capacities from ERAA
`Storage.xlsx`.**

**Spatial levels:** `NUT0` (country), `NUT2`, `SZON` (onshore bidding zones — ENTSO-E shapefile),
`SZOF` (offshore BZ), `PEON/PEOF` (ERAA2025 wind zones), `P2ON/P2OF` (ERAA2026 wind zones),
`ORIG/BIAS` (gridded NetCDF). **Hydro is SZON-only. Wind is PEON/PEOF or P2ON/P2OF only**
(country-level WON/WOF was removed — see §5 known issues). SZON zone codes are **bidding zones,
not ISO-2 countries**: a country may map to several (`DKE1/DKW1`, `ITxx`, `NOS1/NOS2/NOS3`,
`SE01..04`, `LUx`, …) — you must define and apply a country→zone(s) mapping.

**Formats:** `SPV` has gridded NetCDF + CSV; **all other energy variables are CSV (aggregated)
only**. CSV layout: `pd.read_csv(fn, comment="#", index_col="Date")`, **rows = timestamps**
(`Date` index, e.g. `2020-01-01 00:00:00 … 2020-12-31 23:00:00`), **columns = zone codes**
(`AL00, AT01, …`); values = CF (wind/solar) or energy (hydro). **One file = one climate year**
(leap years have **8784** hourly rows). `#`-prefixed header carries units/metadata.

**Filename convention** (underscore positional, 23 fields), e.g. existing onshore wind:
`H_ERA5_ECMW_T639_WON_NA---_Pecd_PEON_S202001010000_E202012312300_CFR_TIM_01h_COM_noc_org_30_NA---_ReGrB_PhM04_PECD4.2_fv1.csv`.
Useful fields: pos0 `H`/`P`, pos7 spatial level, pos8/9 start/end, pos16 technology code, pos17
SSP, pos18 `ReGrA/ReGrB`, pos20 `PECD4.2`, pos21 `fv1`/`fv2`.

**Exact CDS API request fields** (verified against the live download form — treat the form's
**"Show API request code"** panel as ground truth for the precise enum strings, since they differ
from both the human labels and the data short-codes):

| Request field | Values (examples) | Notes |
|---|---|---|
| `pecd_version` | `pecd4_2` | never `pecd4_1` |
| `temporal_period` | `historical` \| `future_projections` | **plural** for projections |
| `origin` | `era5_reanalysis` \| `cmcc_cm2_sr5` \| `ec_earth3` \| `mpi_esm1_2_hr` \| `awi_cm_1_1_mr` \| `bcc_csm2_mr` \| `mri_esm2_0` | model = origin for projections |
| `emission_scenario` | `ssp1_2_6` \| `ssp2_4_5` \| `ssp3_7_0` \| `ssp5_8_5` | projections only |
| `variable` | `wind_power_onshore_capacity_factor`, `wind_power_offshore_capacity_factor`, `solar_photovoltaic_generation_capacity_factor`, `concentrated_solar_generation_capacity_factor`, `hydropower_reservoir_inflow`, `hydropower_run_of_river_inflow`, `hydropower_run_of_river_with_pondage_inflow`, `hydropower_open_loop_pumped_storage_inflow`, `hydropower_reservoir_generation`, `hydropower_run_of_river_generation`, `hydropower_run_of_river_with_pondage_generation` | **long names** (the HRI/WON/SPV short-codes are data/filename codes, not API values) |
| `technology` | `"30"` (onshore existing), `"20"` (offshore existing), `"60"`–`"63"` (SPV), … | only for WON/WOF/SPV/CSP |
| `energy_scenario` | `resource_grade_a` \| `resource_grade_b` | wind installation-area assumption (ReGrA/ReGrB) |
| `spatial_resolution` | `0_25_degree`, `city`, `nuts_0`, `nuts_2`, `peon`, `peof`, `p2on`, `p2of`, `szon`, `szof` | **confirm exact strings in the form**; energy CSV variables use the zone strings, not `0_25_degree` |
| `year` | `"2020"`, `"2050"`, … | 1950–near-present (hist) / 2015–2100 (proj) |
| `month` | `"01"`…`"12"` | the form marks Month required — include the months you need |
| `file_version` | `fv1` \| `fv2` | use `fv2` where §issues require |
| `area` | `[N, W, S, E]` e.g. `[75, -31, 18, 45]` | **gridded sub-region only**; aggregated CSV is always whole-region |

**Example 1 — ERA5 historical onshore-wind CF, bidding-zone aggregated (the `pecd` RES path):**
```python
import cdsapi
client = cdsapi.Client()  # reads ~/.cdsapirc
request = {
    "pecd_version": "pecd4_2",
    "temporal_period": ["historical"],
    "origin": ["era5_reanalysis"],
    "variable": ["wind_power_onshore_capacity_factor"],
    "technology": ["30"],                      # 30 = existing onshore fleet
    "energy_scenario": ["resource_grade_b"],   # ReGrB
    "spatial_resolution": ["peon"],            # aggregated CSV (no `area`)
    "year": ["2020"],
    "month": ["01","02","03","04","05","06","07","08","09","10","11","12"],
    "file_version": ["fv1"],
}
client.retrieve("sis-energy-pecd", request).download()
```

**Example 2 — future-projection request (real form output, climate fields, gridded):**
```python
request = {
    "pecd_version": "pecd4_2",
    "temporal_period": ["future_projections"],
    "origin": ["cmcc_cm2_sr5"],                # the climate model
    "emission_scenario": ["ssp5_8_5"],
    "variable": ["100m_wind_speed", "10m_wind_speed", "2m_temperature",
                 "surface_solar_radiation_downwards", "total_precipitation", "city_coords"],
    "spatial_resolution": ["0_25_degree", "city"],
    "year": ["2050"],
    "month": ["12"],
    "file_version": ["fv1"],
    "area": [75, -31, 18, 45],                 # gridded sub-region only
}
client.retrieve("sis-energy-pecd", request).download()
```

For the PECD energy variables we need (wind/solar CF + hydro inflow), use the **aggregated**
spatial levels (`peon`/`p2on`, `peof`/`p2of`, `szon`) → CSV, **no `area`**, and add `technology`
(+ `energy_scenario` for wind). Build one request per (variable-family, spatial level, year[s]).

**Hydro derivation caveats to surface in docstrings/logs:** inflows are a **statistical
(Random-Forest) reconstruction** from weekly temperature & precipitation, trained on TSO /
ENTSO-E TP / PECDv3.1 data; **reservoir inflow is clipped at zero**; some zones carry **temporary
multiplicative corrections** (PUG Table 2.10) and IC-rescaling (AL/CH/HU/PL/PT) that mix climate
and installed-capacity effects; open-loop inflow (HOL) for some zones comes from PECDv3.1 and may
be inconsistent with generation. The authors state inflows are **not fully validated** — log a
clear provenance note.

---

## 3. DECISION D1 — RESOLVED by maintainer: build **both** ERA5 and PECD as configurable sources

The user wants to **configure the model to choose between ERA5 and PECD**. Both are therefore
in scope as first-class, user-selectable sources. The repository currently has **no ERA5 code on
the supply side** (the only ERA5 usage is `demandforge/era5.py`, which fetches raw
`reanalysis-era5-single-levels` 2 m temperature for population-weighted demand — *not* a
renewable-conversion engine). You will build a genuine raw-ERA5 renewable engine **and** the PECD
4.2 path, both landing in the same canonical schema and switched by config.

Here "**ERA5**" means a **raw-ERA5 engine built in-repo**: download ERA5 climate fields and
convert them into wind/solar capacity factors yourself (via `atlite`), distinct from PECD's
pre-computed product. "**PECD**" means the PECD 4.2 CDS product (its ERA5-historical stream and/or
its CMIP6 projection stream).

### The three sources for wind & solar (`res_source`)

| `res_source` | Wind & solar capacity factors come from | New code |
|---|---|---|
| `entsoe` | Observed ENTSO-E generation ÷ installed capacity (existing `capacity_factor.py`). Default; unchanged. | none |
| `era5` | **Raw ERA5 → `atlite` conversion** in-repo (this work item; see §4.9). | `fetch/era5_res.py`, `process/era5_capacity_factor.py` |
| `pecd` | PECD 4.2 CDS product, ERA5-historical (`origin: era5_reanalysis`) or CMIP6 projection. | `fetch/pecd.py`, `process/pecd_capacity_factor.py` |

For `pecd`, `pecd.temporal_period` selects `historical` (ERA5 stream, `origin: era5_reanalysis`)
vs `future_projections` (set `origin` to a climate-model id + `emission_scenario`).

### Hydro is selected separately — read this (it is the "careful with hydro" crux)

**A raw-ERA5 engine cannot practically produce hydro inflows** (runoff→reservoir/RoR inflow needs
catchment routing + plant-level calibration; even PECD discarded a physical hydrological model and
used a statistical reconstruction). So hydro **must not** be tied to `res_source = era5`. Give
hydro its **own** selector:

| `hydro_source` | Reservoir inflow + RoR come from |
|---|---|
| `entsoe` | Existing water-balance reconstruction (`hydro_inflow.py`) + ENTSO-E RoR CF. |
| `pecd` | PECD `HRI` (reservoir inflow) + PECD-derived RoR CF (§4.5). |
| `auto` (default) | Follow `res_source` when it can supply hydro: `pecd`→`pecd`, `entsoe`→`entsoe`; **when `res_source = era5`, fall back to `entsoe`** (and log it loudly). |

`res_source` and `hydro_source` are **orthogonal** — any combination is valid. Reservoir & PHS
**energy capacities (MWh)** always come from ERAA `Storage.xlsx` regardless (neither ERA5 nor PECD
provides reservoir volume).

**Cost note for the maintainer (already discussed):** the `era5` engine's *integration* is cheap
because the source abstraction below makes it a drop-in; the real effort is the conversion's
**bias-correction and validation** (ERA5 wind is biased in complex terrain). Deliver a working
`atlite` wind+solar engine (§4.9); treat bias-correction/validation as explicitly tracked work,
not an afterthought.

**Reuse, don't fork (`demandforge` precedent):** `demandforge` already depends on **`cdsapi`** and
already fetches raw ERA5 per-country by bounding box, month-by-month, concatenated to NetCDF
(`demandforge/era5.py`, `country_borders.py`, `population_weighted_temperature.py`). Reuse that
fetch pattern, the `country_borders.gpkg` bounding-box helper, the `.cdsapirc`/key handling, and
the `{"GR":"EL"}` remap for the `era5_res.py` fetcher.

---

## 4. What to build

### 4.1 Config (`config/config.yaml`)

Add a self-contained, backward-compatible block. **Both selectors absent ⇒ `entsoe` everywhere**,
i.e. current behaviour is preserved untouched.

```yaml
# --- Source selectors (orthogonal) ---
res_source: entsoe            # wind & solar CFs:  entsoe | era5 | pecd   (default entsoe = current behaviour)
hydro_source: auto            # reservoir inflow + RoR:  auto | entsoe | pecd  (auto→entsoe here)
                              #   auto = follow res_source if it can supply hydro,
                              #          else fall back to entsoe (the era5 case)

# --- Raw-ERA5 engine (used iff res_source == era5) ---
era5:
  climate_years: [2008, 2009, 2012]     # ERA5 weather years to build CFs for
  hub_height_m: 100
  turbine_onshore: Vestas_V112_3MW      # atlite/OEDB power-curve name (representative)
  turbine_offshore: NREL_ReferenceTurbine_5MW_offshore
  pv_panel: CSi                         # atlite panel model
  pv_orientation: {slope: 35, azimuth: 180}
  land_use_masks: true                  # exclude protected/urban/steep/water cells
  bias_correction: gwa2                 # gwa2 | none — Global Wind Atlas delta correction for wind
                                        #   (strongly recommended; ERA5 wind is biased in complex terrain)

# --- PECD 4.2 (used iff res_source == pecd OR hydro_source == pecd) ---
# Keys below map 1:1 to CDS API request fields (see §2). Use the exact API enum strings.
pecd:
  pecd_version: pecd4_2
  temporal_period: historical       # historical | future_projections   (NB plural for projections)
  origin: era5_reanalysis           # era5_reanalysis  OR a model id for projections
                                     #   (cmcc_cm2_sr5|ec_earth3|mpi_esm1_2_hr|awi_cm_1_1_mr|bcc_csm2_mr|mri_esm2_0)
  emission_scenario: null           # required iff temporal_period == future_projections
                                     #   (ssp1_2_6|ssp2_4_5|ssp3_7_0|ssp5_8_5)
  file_version: fv1                 # fv1 | fv2  (use fv2 for bcc_csm2_mr+ssp2_4_5 wind, MENA)
  spatial_resolution_wind: peon     # peon|p2on onshore; peof|p2of offshore
  spatial_resolution_solar: szon    # szon|peon|nuts_0...
  # hydro is SZON-only (fixed)
  wind_technology_onshore: "30"     # existing fleet
  wind_technology_offshore: "20"
  energy_scenario: resource_grade_b # ReGrA/ReGrB → resource_grade_a|resource_grade_b
  climate_years: [2008, 2009, 2012] # → CDS `year`; PECD climate/weather years to fetch & expose
  variables_hydro: [hydropower_reservoir_inflow, hydropower_run_of_river_inflow,
                    hydropower_run_of_river_with_pondage_inflow,
                    hydropower_open_loop_pumped_storage_inflow]   # API long names (codes: HRI/HRR/HPI/HOL)
  use_hol_for_phs: false            # DECISION D4 (default off)
  ror_source: pecd                  # DECISION D3 (RESOLVED: pecd) — how to build RoR CF under pecd
```

Keep the `era5.climate_years` and `pecd.climate_years` lists able to take the **same** value so a
scenario can be run climate-consistently across sources and with the demand side (see §7).

Define the **country→PECD-zone mapping** in code (a module-level dict, reusing/aligning with the
bidding-zone logic already in `fetch/day_ahead_prices.COUNTRY_BIDDING_ZONE_MAPPING` and ENTSO-E
mappings where possible). Multi-zone countries: **sum** energy variables (hydro) and
**capacity-weighted-average** capacity factors across the country's zones (see §4.4 wind).

### 4.2 New fetch module — `supplyforge/fetch/pecd.py`

Mirror the *role* of `fetch/eraa_study.py` (a staging fetcher) but use `cdsapi`. Requirements:

- Module-level methodology docstring (PECD spec, provenance, caveats — as in §2).
- `fetch_pecd_data(...)` that, driven by config, issues the minimal set of CDS requests for the
  selected `pecd.temporal_period`, variables, technologies, spatial levels, and `climate_years`, and
  stages raw files under `RESULTS_DIR/pecd_study/` with a `download.txt` manifest (mirror
  `eraa_study.py`). Cache: skip download if the target raw file already exists.
- **Auth:** read `~/.cdsapirc` if present, else `CDSAPI_URL`/`CDSAPI_KEY` from `.env`. If neither,
  log a clear actionable error (how to register, accept T&C). Never hard-code a key.
- **Robustness:** wrap each `client.retrieve(...)` in try/except; on failure log and continue
  (fail-soft per source/variable/year), so one missing zone/year doesn't kill the DAG.
- **Respect known issues:** request `file_version: fv2` where applicable (`bcc_csm2_mr`+`ssp2_4_5`
  wind; MENA climate aggregations); never
  request WON/WOF at `nut0`.
- Snakemake entry point at bottom guarded by `if __name__ == "__main__":` reading
  `snakemake.params`/`config`, with a standalone debug fallback (follow the existing pattern).

Output of this module is **raw staged PECD CSVs** (+ manifest) — *not* model-ready Parquet.
That harmonisation is §4.3/§4.4.

### 4.3 New process module — `supplyforge/process/pecd_capacity_factor.py`

Analogue of `eraa_capacity_factor.py`. Convert staged PECD CSVs into the canonical
`capacity_factors_<C>_<year>.parquet`, **one file per (country, climate_year)**, with a schema
the *unmodified* `add_intermittent_tech` accepts. Use the **WS-style climate-year column** so the
existing `ws=` selector works for free:

- Schema: `hour` (int 0–8759 **or** datetime UTC — match whichever the consumer path expects;
  simplest and consumer-safe is to follow the ERAA schema: `hour` int, `plant_type` categorical,
  `WS` categorical = the PECD **climate year** as a string, `capacity_factor` f64).
- Map PECD codes → `plant_type` labels used by `INTERMITTENT_TECH_DICT`:
  `SPV→"Solar"`, `WON→"Wind Onshore"`, `WOF→"Wind Offshore"`. (RoR handled in §4.5.)
- **Wind country aggregation (NUT0 deprecated):** for each country, aggregate its PEON/P2ON
  (onshore) / PEOF/P2OF (offshore) zone columns to a single national CF using
  **installed-capacity weights** (replicate the PUG's NUTS0 notebook method; weights from the
  PECD onshore/offshore run workbooks, or from `installed_capacities_*` as a pragmatic proxy —
  document which). Do not simple-average raw zone CFs.
- **Solar:** prefer SZON or NUT0 directly; if multi-zone, capacity-weight.
- **Leap years:** PECD CSVs have 8784 rows in leap years. Normalise to the repo's 8760 convention
  (the consumer pads/truncates, but do the trim deterministically here and document it — drop
  Feb 29 or the trailing 24 h consistently with how the rest of supplyforge treats 8760).
- Clip/flag `capacity_factor` to `[0,1]` (log values outside, as the existing modules tolerate
  >1 but you should report it).
- Fail-soft: empty/missing → valid empty Parquet with the schema + warning.

### 4.4 New process module — `supplyforge/process/pecd_hydro_inflow.py` (HYDRO — handle with care)

Convert PECD **reservoir inflow `HRI`** (weekly energy, SZON) into the canonical
`inflow_<C>_<year>.parquet` (`inflow_MW`, `timestamp`, 8760 rows, UTC). This **replaces** the
ENTSO-E water-balance reconstruction when a PECD source is selected. Steps:

1. Load the country's HRI weekly series; **sum across the country's SZON zones**.
2. Build the year's hourly UTC index (`{year}-01-01 00:00` → `{year+1}-01-01 00:00`, left-closed,
   8760 h). Reuse `hydro_inflow.create_week_start` / the existing weekly→hourly interpolation so
   the **temporal alignment matches the ENTSO-E path exactly** (52 Monday week-starts, linear
   interpolation, `bfill`, clip ≥0, `/168` to get MW). Factor the shared interpolation helper out
   of `hydro_inflow.py` and import it (don't copy-paste) where reasonable.
3. **Units:** HRI is weekly energy (MWh). Convert to a power rate the same way
   (`MWh/week ÷ 168 → MW`). Remember the consumer **normalises by the max**, so the *profile* is
   what matters; still log the absolute scale and note PECD's temporary correction factors /
   IC-rescaling caveats for affected zones.
4. **Negatives:** PECD already zero-clips reservoir inflow; clip again defensively and log if any
   appear.
5. Fail-soft empty schema on missing data.

**Why HRI is preferable here:** it is a *natural inflow* reconstruction, which is precisely the
must-run `reservoir_water` inflow the POMMES reservoir triplet expects — arguably more physical
than the ENTSO-E `I_w = G_w + ΔS_w` balance (which conflates inflow with dispatch decisions).
State this in the docstring.

**Do NOT** attempt to get reservoir energy *capacity* from PECD — keep
`get_storage_energy_capacities(...)` reading ERAA `Storage.xlsx`. Leave `add_reservoir_hydro` and
`add_pumped_hydro` **unchanged** (subject to D4).

### 4.5 DECISION D3 — Run-of-river under PECD — **RESOLVED by maintainer: use PECD**

The ENTSO-E path treats RoR (`Hydro Run-of-river and poundage` ↔ `RoR_Pondage`) as an
**intermittent capacity factor** (`generation/installed`, hourly). PECD provides RoR as **weekly
energy** (`HRO/HPO` generation, `HRR/HPI` inflow) — there is **no hourly RoR CF** and weekly
resolution loses the sub-weekly variability that matters for run-of-river.

**Decision (confirmed): `ror_source: pecd`.** When a PECD source is selected, derive the RoR
capacity factor from PECD generation:
`CF_week = (HRO + HPO) / (C_inst_RoR × 168)`, summed across the country's SZON zones, interpolated
weekly→hourly (same helper as §4.4), clipped to `[0,1]`, and emitted under
`plant_type = "Hydro Run-of-river and poundage"` in the *same* `capacity_factors_*` file so the
unmodified `add_intermittent_tech` picks it up. `C_inst_RoR` is the national RoR installed
capacity from `installed_capacities_*`. **Loudly document** the weekly-resolution caveat (no
diurnal/short-term RoR variability vs. the ENTSO-E hourly series).

Still implement `ror_source` as a config key so the ENTSO-E-RoR hybrid (`ror_source: entsoe`)
remains available, but **`pecd` is the default and the maintainer's chosen value.**

### 4.6 DECISION D4 — Open-loop PHS inflow (`HOL`)

Today PHS has **no inflow** (energy capacity from ERAA, turbine from ENTSO-E). PECD `HOL` is a
natural inflow to open-loop pumped storage. **Default: do not change PHS modelling** (`use_hol_for_phs:
false`) to avoid altering established semantics; still *fetch* HOL so it's available. If enabled
later, it would require a model-side change in `add_pumped_hydro` (add a `reservoir_water`-style
inflow to the open-loop component) — out of scope unless the maintainer asks.

### 4.7 Climate-year ↔ `reference_year` mapping (DECISION D2)

The repo keys everything by a single `(country, year)`. **Default mapping:** treat each PECD
**climate year** as one `reference_year`, i.e. write
`capacity_factors_<C>_<climate_year>.parquet` and `inflow_<C>_<climate_year>.parquet`. This reuses
`_get_input_data_file(country, climate_year, ...)` with **zero model-side change**. For
**projections**, pin a single `origin` (climate model) + `emission_scenario` per workflow run (from config);
the on-disk filename stays `(country, year)`. If a full ensemble must coexist on disk, namespace
the model/SSP into the climate-year token (e.g. `…_<year>_<model>_<ssp>.parquet`) **and** extend
`_get_input_data_file` accordingly — but only if requested. The PECD climate year is also encoded
in the `WS` column (§4.3) so multiple years can live in one file and be selected via the existing
`add_intermittent_tech(ws=...)` argument.

### 4.8 Snakemake wiring

- Add `rule fetch_pecd` (analogous to `fetch_eraa`; output e.g. `results/pecd_study/download.txt`)
  and `rule fetch_era5_res` (raw ERA5 fields for the `era5` engine; output e.g.
  `results/era5_res/era5_res_{country}_{year}.nc` or an `atlite` cutout marker) to `fetch.smk`.
- Make the **capacity-factor** process rule source-aware on `config["res_source"]` and the
  **hydro-inflow** process rule source-aware on the resolved `config["hydro_source"]` (`auto`
  resolved as in §3). Cleanest idiom: read the config value and **dispatch the `script:`**
  (or `input:`) for `calculate_capacity_factors` / `calculate_hydro_inflow` to the matching module
  — via a thin wrapper script that imports the right implementation, a Python `def` chosen at
  rule-definition time, or rules disambiguated by `ruleorder`/conditional `include`. Keep the
  **output paths identical** (`capacity_factors_*`, `inflow_*`) so `rule all` and the GCS upload
  are untouched.
- The two selectors are independent: e.g. `res_source: era5` + `hydro_source: entsoe` must run the
  ERA5 CF engine *and* the ENTSO-E inflow reconstruction in the same DAG.
- Ensure `rule all` still requests the same canonical targets; the *content* changes with the
  config, not the target names.

### 4.9 The raw-ERA5 engine (`res_source: era5`) — wind & solar via `atlite`

Build an in-repo engine that converts raw ERA5 climate fields into wind/solar capacity factors.
**Use `atlite`** (the PyPSA-ecosystem standard) rather than hand-rolling power curves — it builds
an ERA5 "cutout," extrapolates wind to hub height, applies turbine power curves and a PV model,
and aggregates grid cells to a geometry with land-use availability. Scope of this engine:
**wind onshore, wind offshore, and solar PV only.** It does **not** produce hydro (see §3).

**`fetch/era5_res.py`** — fetch the ERA5 fields `atlite` needs (100 m wind components, 2 m wind,
surface solar radiation downwards, 2 m temperature, optionally pressure/influx) for each country's
bounding box and each `era5.climate_years`, and persist an `atlite` cutout (or NetCDF) under
`RESULTS_DIR/era5_res/`. **Reuse `demandforge/era5.py`'s conventions**: `cdsapi`/`atlite` CDS auth,
the `country_borders.gpkg` bbox helper, month-by-month download + concat, the `{"GR":"EL"}` remap,
and the empty-file fallback. Cache: skip if the cutout already exists.

**`process/era5_capacity_factor.py`** — from the cutout, compute hourly CFs and write the canonical
`capacity_factors_<C>_<climate_year>.parquet`, **schema-identical to the PECD/ERAA output** (§4.3:
`hour`, `plant_type` ∈ `INTERMITTENT_TECH_DICT` values, `WS` = climate year, `capacity_factor`),
so the unmodified `add_intermittent_tech` consumes it. Specifics:

- **Wind:** `cutout.wind(turbine=<era5.turbine_onshore>, ...)` and offshore with
  `turbine_offshore`, hub height from config; aggregate cells to the country with **land-use
  availability masks** (`era5.land_use_masks`) and capacity weighting; emit `Wind Onshore` /
  `Wind Offshore`.
- **Solar:** `cutout.pv(panel=<era5.pv_panel>, orientation=<era5.pv_orientation>)`; emit `Solar`.
- **Bias correction (`era5.bias_correction: gwa2`):** apply a Global Wind Atlas delta/ratio
  correction to wind before the power curve (or to the resulting wind CF), because uncorrected
  ERA5 wind is systematically biased, especially in mountainous terrain (Alps, Norway, Balkans).
  Allow `none` but **default `gwa2`** and warn if `none` is used.
- **RoR under `res_source: era5`:** the raw engine does not model run-of-river; RoR follows
  `hydro_source` (i.e. ENTSO-E RoR CF by default). Do **not** fabricate a RoR CF from ERA5.
- 8760 normalisation, leap-year trim, `[0,1]` clip/flag, fail-soft empty Parquet — same rules as
  §4.3.

**Dependencies:** `atlite` (pulls `xarray`, `rasterio`, `geopandas`, `dask`, `scipy`); add to
`pyproject.toml` and the workflow/all envs. Document the GWA data source for bias correction.

**Validation is part of this deliverable, not optional:** compare the engine's national annual CF
means and seasonal/diurnal profiles against ENTSO-E observed and (where available) PECD for at
least FR + DE + a mountainous country (CH/AT/NO); report bias before/after GWA correction. A raw
engine that isn't validated is not done.

---

## 5. Pitfalls & known issues (PECD + repo) — enumerate and handle

1. **Leap years:** PECD hourly CSV = 8784 rows; pipeline = 8760. Trim deterministically; document.
2. **Bidding-zone vs ISO-2:** SZON ≠ country (DKE1/DKW1, ITxx, NOS1-3, SE01-04, LUx…). Implement
   and unit-test the country→zone mapping; sum hydro energy, capacity-weight CFs.
   - **Greece code clash:** Greece is **`EL`** in NUTS/PECD but **`GR`** in ENTSO-E and in this
     repo's `config.yaml`. `demandforge/era5.py` already hard-codes `{"GR":"EL"}` — replicate that
     remap. Watch for other NUTS-vs-ENTSO-E divergences (UK↔GB, etc.).
3. **NUT0 wind deprecated:** never request WON/WOF at `nut0`; aggregate from PEON/PEOF/P2ON/P2OF
   with installed-capacity weights (PUG §5.1 method).
4. **`fv2` reprocessed files:** prefer `file_version: fv2` for `bcc_csm2_mr`+`ssp2_4_5` wind and
   MENA climate aggregations.
5. **Timezone:** confirm PECD `Date` is UTC and align to the repo's UTC convention; don't
   double-localise.
6. **Units:** hydro = energy (MWh) weekly; wind/solar = CF (0–1) hourly. Don't mix.
7. **`touch()` vs empty Parquet:** match the sibling module's fail-soft artifact so Snakemake and
   downstream readers don't choke.
8. **Provenance leakage:** PECD inflow IC-rescaling (AL/CH/HU/PL/PT) and correction factors mean
   projection−historical differences mix climate + IC effects — log a warning for those zones.
9. **CDS limits/T&C:** requests can queue/throttle; downloads can be large; T&C must be accepted
   per dataset. Handle gracefully, document the one-time setup.
10. **Schema parity:** the PECD `capacity_factors_*` schema must satisfy *both* consumer code
    paths (`plant_type` filter, optional `WS` filter, 8760 padding). Test against the actual
    `add_intermittent_tech` logic.

---

## 6. Validation & tests (required — do not skip)

Add tests under `supplyforge/tests/` and a verification pass:

1. **Schema/contract tests:** PECD `capacity_factors_*` and `inflow_*` have the exact columns &
   dtypes the consumer expects; 8760-hour conformance after normalisation; `plant_type` labels
   exactly match `INTERMITTENT_TECH_DICT` values; `WS`/climate-year column present and selectable.
2. **Physical sanity:** CFs in `[0,1]` (flag exceedances); reservoir inflow ≥ 0; monotonic UTC
   timestamps; no all-NaN national series for a country known to have hydro.
3. **Zone-mapping tests:** multi-zone countries (DK, IT, NO, SE, LU) aggregate correctly (sum for
   energy, capacity-weight for CF).
4. **End-to-end (all sources):** `create_model("FR", <climate_year>, model_year)` builds
   successfully for each `res_source` ∈ {`entsoe`, `era5`, `pecd`} (with mock/staged inputs),
   producing the reservoir triplet and intermittent techs without touching model code.
5. **Selector orthogonality:** confirm `res_source: era5` + `hydro_source: entsoe` runs both the
   ERA5 CF engine and the ENTSO-E inflow reconstruction; confirm `hydro_source: auto` falls back to
   `entsoe` when `res_source: era5`.
6. **Cross-source overlap check (scientific):** for one country/year, compare annual wind/solar CF
   means and seasonal/diurnal profiles across `entsoe`, `era5` (before & after GWA correction), and
   `pecd`; and compare seasonal hydro-inflow profiles for `entsoe` vs `pecd`. Report correlation
   and bias (expect broad agreement, not identity — different methods). Put this in a short
   `notebooks/`/`scripts/` validation artefact, not a hard assert.
7. Run the existing test suite to confirm **no regression** of the ENTSO-E path. Prefer running
   the verification in a subagent/CI job and reporting diffs.

---

## 7. Relationship to `demandforge` (cross-cutting — read before deciding scope)

The supply-side PECD switch is a **`supplyforge`** task; **no `demandforge` change is required**
to deliver it. But two methodological couplings matter and must be documented (not necessarily
implemented now):

1. **Climate-year consistency (important).** `demandforge` derives demand from a chosen ERA5
   **weather year** (raw ERA5 temperature → population-weighted T → thermosensitive load). If
   `supplyforge` serves renewable CFs and hydro inflows from a *different* PECD climate year, the
   demand and supply sides of a POMMES scenario become climatically inconsistent (e.g. a cold-snap
   demand year paired with a wind-rich supply year). **Expose the PECD `climate_year` such that it
   can be set equal to the demand-side ERA5 year**, and state this coupling in the docs. A shared
   scenario config (one `weather_year`/`climate_year` consumed by both packages) is the clean
   long-term design — propose it, don't silently implement it.

2. **Optional future harmonisation of temperature (out of scope now).** PECD 4.2 also provides
   `TAW` (population-weighted temperature, SZON) and `TA`. In principle these could *replace*
   `demandforge`'s bespoke `era5.py` + GHSL pipeline with a single CDS source shared by both
   packages. This is an attractive simplification but a **separate refactor** — only flag it as a
   recommendation; do not undertake it as part of this work item.

Reuse, don't fork: `cdsapi` client setup, the `.cdsapirc`/key handling, the country-bounding-box
and `country_borders.gpkg` conventions, and the `{"GR":"EL"}` remap all already exist in
`demandforge/era5.py` and `country_borders.py` — mirror them for consistency across the two
packages.

---

## 8. Deliverables (PR contents)

- `supplyforge/fetch/pecd.py` (new) + `supplyforge/process/pecd_capacity_factor.py` (new) +
  `supplyforge/process/pecd_hydro_inflow.py` (new); shared weekly→hourly helper factored from
  `hydro_inflow.py`.
- `supplyforge/fetch/era5_res.py` (new) + `supplyforge/process/era5_capacity_factor.py` (new) —
  the raw-ERA5 `atlite` engine (§4.9).
- `config/config.yaml` block (§4.1) + `config_*.yaml` variants kept consistent.
- `workflow/rules/fetch.smk` + `process.smk` source-aware wiring (§4.8).
- **Dependencies:** add `cdsapi` to `pyproject.toml` runtime `dependencies` (currently absent; note
  it is already in `ci/envs/environment-doc.yaml` only) and to `environment-all.yaml`,
  `environment-workflow.yaml`, `environment-test.yaml`. For the `era5` engine add **`atlite`**
  (which pulls `xarray`, `rasterio`, `geopandas`, `dask`, `scipy`) to the same files. Only add
  `netcdf4` explicitly if not transitively present. For PECD, staying on the CSV (aggregated)
  products avoids needing NetCDF readers (recommended).
- `.env`/docs note for `CDSAPI_URL`/`CDSAPI_KEY` + T&C; update `README.md` and `AGENTS.md`
  "Adding a New Data Source" / data-sources sections.
- Tests (§6).
- **PR description must explicitly list decisions D1–D4 and the value chosen**, so the maintainer
  can override.

---

## 9. Out of scope (unless the maintainer explicitly asks)

- **Hydro inflow from raw ERA5** (runoff routing / hydrological model). The `era5` engine is
  wind+solar only; hydro always comes from `hydro_source` (ENTSO-E or PECD). See §3.
- Any **`demandforge`** code change, including replacing its ERA5+GHSL temperature path with PECD
  `TAW` (see §7 — recommend only).
- Changing `add_reservoir_hydro` / `add_pumped_hydro` modelling semantics (beyond feeding the
  existing inflow contract), except the optional D4 HOL enhancement.
- Reservoir storage-volume sourcing from anything other than ERAA `Storage.xlsx`.
