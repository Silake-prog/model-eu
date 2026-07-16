# Extending the model

This model is built to be extended along four axes. Each has **one place** to change.
The golden snapshot (`tests/golden/`) proves existing scenarios still reproduce
bit-identically after any change here, so extend with confidence.

> Architecture in one line: a **scenario string** → `clever/scenario/parse.py` (parsers)
> + `clever/scenario/registry.py` (flag vocabulary) → `clever/overrides.py` (resolved
> values) + `clever/inputs.py` (static tables) → `clever/model.py` builds a
> `pommes_craft.EnergyModel` → `clever/runner.py` solves + writes NetCDF.

## Add a new lever (scenario flag)

Example: a `_myLever42` knob.

1. **Parser** — add a pure function in `clever/scenario/parse.py`:
   ```python
   def _parse_my_lever(s: str) -> int | None:
       m = _re.search(r"_myLever(\d+)(?:_|$)", s)
       return int(m.group(1)) if m else None
   ```
2. **Registry** — declare it once in `clever/scenario/registry.py`:
   ```python
   Flag("myLever", r"myLever\d+", "int", "what it changes", _p._parse_my_lever),
   ```
   and add the field to `ScenarioSpec` + `parse_scenario`. That is all validation needs —
   no `_VALID_SCENARIOS` edit. Regenerate the docs: `python docs/gen_flags_doc.py`.
3. **Wire the value** — read it where it applies (in `clever/overrides.py` for a resolved
   global, or in `clever/model.py` where the LP is built).

## Add a new technology

Technology mappings are static tables in `clever/inputs.py`:
`CLEVER_CAPACITY_TO_MODEL`, `CLEVER_NON_ENR_TO_MODEL`, `MODELTECH_TO_EOLES`,
`EOLES_LIFETIME`, `FUEL_ADDER_2050`, `CLEVER_VRE_SPECS`. Add the tech to the relevant
maps; if it is investable, add it to `EXPANDABLE_MODEL_TECHS` (in `clever/overrides.py`,
since expandability can be scenario-gated). The generic reference builder
`supplyforge/supplyforge/create_pommes_craft_model.py` keeps its own clean
`DISPATCHABLE_TECH_DICT` / `INTERMITTENT_TECH_DICT` — use those as the template for a
dataset-neutral tech registry.

## Add a region / country

The country set is data-driven from `AREA_MAP` (+ `MANUAL_INTERCONNECTIONS`,
`HYDRO_PEMMDB`, per-country tables) in `clever/inputs.py`. Adding a region means adding
its rows there and supplying its data. Non-EU regions already work through this seam:
`clever/mena_imports.py` adds MA/DZ/TN/LY behind the `_menaOptim` flag. The provider
`country_set()` hook (see below) is the intended single entry point for region membership.

## Add a data source (provider)

CLEVER is **one case study**, not the model's identity. A *provider* supplies inputs and
builds the `pommes_craft.EnergyModel`:

- Contract: `clever/providers/base.py` — the `ModelProvider` protocol
  (`scenario_spec()`, `country_set()`, `fetch_inputs()`, `build_model()`).
- CLEVER adapter: `clever/providers/clever.py` — `CleverProvider`, a thin delegate to the
  existing `create_multi_country_model_from_clever` + `fetch.py`/`process.py`/`demand.py`.
- A new dataset (ERAA, TYNDP, custom CSVs) is a new provider returning the same
  `EnergyModel`. `supplyforge/supplyforge/create_pommes_craft_model.py` (ERAA-driven)
  already fits the contract and is the reference for `clever/providers/eraa.py`.

## Verifying a change

Everything is validated **without a solver** (no Gurobi/data/`pommes_craft` needed here):

```bash
python tests/golden/snapshot_constants.py --check   # inputs still bit-identical
python tests/test_registry.py                        # flags validate; spec == globals
python tests/test_smoke_build.py                     # builds the LP if deps present, else skips
```

Never regenerate `tests/golden/baseline_hashes.json` except in a deliberate, reviewed
commit — it is the reproduction contract.
