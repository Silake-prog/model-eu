"""
Module to create a pommes_craft EnergyModel for a given country and year.

This script leverages pre-calculated data from the Snakemake workflow for:
- Installed capacities
- Availability for dispatchable plants (e.g., nuclear)
- Capacity factors for intermittent renewables (e.g., wind, solar)
- Hydro inflows for reservoir hydro plants
"""

import logging
from pathlib import Path
import urllib

import polars as pl
from pommes_craft import (
    Demand,
    Area,
    ConversionTechnology,
    EnergyModel,
    NetImport,
    Spillage,
    StorageTechnology,
    TimeStepManager,
    EconomicHypothesis,
    LoadShedding,
    TransportTechnology,
    Link
)

from supplyforge.fetch.day_ahead_prices import COUNTRY_BIDDING_ZONE_MAPPING
from supplyforge import RESULTS_DIR
from supplyforge.fetch.eraa_study import fetch_eraa_data
from supplyforge.utils import get_electricity_import_prices, _get_input_data_file
from supplyforge.h2_profiles import build_h2_profile, build_all_sector_profiles, DEMANDFORGE_SECTORS

DISPATCHABLE_TECH_DICT = {
    "Nuclear": "Nuclear",
    "Lignite": "Lignite",
    "Coal": "Fossil Hard coal",
    "Gas": "Fossil Gas",
    "Oil": "Fossil Oil",
    "Biomass": "Biomass",
    "Waste": "Waste",
    "Other": "Other"
}
INTERMITTENT_TECH_DICT = {
    "Solar": "Solar",
    "Wind_Onshore": "Wind Onshore",
    "Wind_Offshore": "Wind Offshore",
    "RoR_Pondage": "Hydro Run-of-river and poundage",
}
DISPATCHABLE_DEFAULT_COSTS = {
    "Nuclear": 10.0,
    "Lignite": 180.0,
    "Coal": 250.0,
    "Gas": 200.0,
    "Oil": 300.0,
    "Biomass": 50.0,
    "Waste": 30.0,
    "Other": 40.0,
    "Hydrogen_power_plant": 0.,
    "Biogas": 50.0,
}

DISPATCHABLE_RAMP_RATES = {
    "Nuclear": 0.1,
    "Lignite": 0.2,
    "Coal": 0.4,
    "Gas": 0.8,
    "Oil": 1.,
    "Biomass": 1.,
    "Waste": 1.,
    "Other": 1.,
    "Biogas": 1.,
}

FIXED_COSTS = {
    "Nuclear": 120_000.0,
    "Lignite": 50_000.0,
    "Coal": 45_000.0,
    "Gas": 20_000.0,
    "Oil": 25_000.0,
    "Biomass": 60_000.0,
    "Waste": 70_000.0,
    "Other": 30_000.0,
    "Solar": 12_000.0,
    "Wind_Onshore": 35_000.0,
    "Wind_Offshore": 85_000.0,
    "RoR_Pondage": 25_000.0,
    "Pumped_Hydro": 14_000.0,
    "Reservoir_Hydro": 30_000.0,
    "Biogas": 20_000.0,
    "Electrolyser": 12_000., # future projection
    "Hydrogen_storage_power": 12_600.,
    "Hydrogen_power_plant": 45_000.0
}

# Investment costs (CAPEX) in €/MW
INVEST_COSTS = {
    "Nuclear": 7_500_000.0,   # Very high initial capital (e.g., EPR reactors)
    "Lignite": 2_000_000.0,   # High due to complex fuel handling/boiler
    "Coal": 1_800_000.0,      # Standard hard coal plant
    "Gas": 900_000.0,        # Combined Cycle (CCGT) - cheaper to build
    "Oil": 500_000.0,        # Peaking units/Engines - low CAPEX, high OPEX
    "Biomass": 3_500_000.0,   # High due to feedstock logistics and scale
    "Waste": 6_000_000.0,     # Very high due to filtration/fume treatment
    "Other": 4_000_000.0,     # Generic estimate for misc. thermal technologies
    "Reservoir_Hydro": 4_000_000.0, # High civil engineering costs (dams)
    "Solar": 700_000.0,       # Utility-scale PV (prices have dropped significantly)
    "Wind_Onshore": 1_300_000.0, # Mature technology
    "Wind_Offshore": 3_200_000.0,# Higher due to marine foundations & grid connection
    "RoR_Pondage": 3_500_000.0,  # Run-of-river hydro (civil works)
    "Pumped_Hydro": 2_000_000.0,  # Site specific, but generally high civil costs
    "Electrolyser": 155000., # future projection
    "Hydrogen_storage_energy": 5_400.,
    "Hydrogen_storage_power": 12_600.,
    "Hydrogen_power_plant": 900_000.0,
    "Biogas": 900_000.0
}

# Technical lifetime in years
LIFETIMES = {
    "Nuclear": 60,       # Modern Gen III+ plants are designed for 60 years
    "Lignite": 40,       # Standard thermal plant life
    "Coal": 40,
    "Gas": 30,           # Gas turbines have shorter lives than steam turbines
    "Oil": 30,
    "Biomass": 25,       # Corrosion can limit life compared to coal
    "Waste": 25,
    "Other": 30,
    "Reservoir_Hydro": 80, # Civil structures (dams) last very long
    "Solar": 25,         # Standard PV module warranty/life
    "Wind_Onshore": 25,  # Modern turbines
    "Wind_Offshore": 25, # Harsh marine environment limits life
    "RoR_Pondage": 60,   # Long-lived civil works
    "Pumped_Hydro": 80,   # Very long life (mostly civil engineering)
    "Electrolyser": 20, # 20 yr with stack replacement (was 10; must be multiple of lifetime_step=5)
    "Hydrogen_storage": 20,
    "Hydrogen_power_plant": 30,
    "Biogas": 30,
    "H2_pipeline": 40,
}

# ═══════════════════════════════════════════════════════════════════
# Hydrogen network cost assumptions
# ═══════════════════════════════════════════════════════════════════
H2_PIPELINE_COSTS = {
    # New dedicated H₂ pipeline (representative for ~500 km avg distance)
    "new_pipeline_invest": 500_000.,       # €/MW
    "repurposed_pipeline_invest": 150_000., # €/MW (from natural gas)
    "hurdle_cost": 5.,                      # €/MWh (compression energy + losses)
    "fixed_cost": 0.,                       # €/MW/yr
    "lifetime": 40,                         # years (must be multiple of lifetime_step)
}

# Salt cavern storage (large-scale) vs pressurised tanks
H2_STORAGE_COSTS = {
    "tank": {
        "invest_energy": 5_400.,   # €/MWh
        "invest_power": 12_600.,   # €/MW
    },
    "salt_cavern": {
        "invest_energy": 125.,     # €/MWh (50–200 range, midpoint)
        "invest_power": 12_600.,   # €/MW (same injection/withdrawal)
    },
}

# European H₂ pipeline adjacency (country pairs that can be connected)
H2_ADJACENCY: set[tuple[str, str]] = {
    ("FR", "DE"), ("FR", "BE"), ("FR", "ES"), ("FR", "IT"), ("FR", "CH"),
    ("DE", "NL"), ("DE", "BE"), ("DE", "AT"), ("DE", "PL"), ("DE", "CZ"),
    ("DE", "DK"), ("DE", "CH"),
    ("NL", "BE"),
    ("IT", "AT"), ("IT", "CH"), ("IT", "SI"), ("IT", "MT"),  # MT: Malta H2 import (island; else ~100% H2 ENS)
    ("AT", "CZ"), ("AT", "HU"), ("AT", "SI"),
    ("PL", "CZ"), ("PL", "SK"),
    ("CZ", "SK"),
    ("HU", "SK"), ("HU", "RO"),
    ("ES", "PT"),
    ("DK", "SE"), ("DK", "NO"),
    ("SE", "NO"), ("SE", "FI"),
    ("NO", "FI"),
    ("BG", "RO"), ("BG", "GR"),
    ("RO", "HU"),
    ("BE", "LU"),
    # ── 2050 horizon additions: UK & Norway subsea pipelines ─────────────
    # European Hydrogen Backbone priority corridors 6 (UK-NW Europe) + 7 (Norway).
    ("GB", "FR"), ("GB", "NL"),
    ("NO", "DE"), ("NO", "NL"),
}


# Configure logging
logger = logging.getLogger(__name__)

def _eraa_study_data(file_name: str):

    """
    Retrieves the dashboard data from the ERAA study.
    Args:
        file_name:

    Returns:

    """
    data_dir = RESULTS_DIR / "eraa_study"
    data_dir.mkdir(parents=True, exist_ok=True)
    file_path = data_dir / file_name


    if not file_path.is_file():
        logging.info(f"'{file_path}' not found. Downloading from remote URL...")
        fetch_eraa_data()

    if file_path.suffix == ".csv":
        return pl.read_csv(file_path)
    elif file_path.suffix == ".xlsx":
        return pl.read_excel(file_path)
    else:
        raise ValueError(f"Unsupported file type: {file_path.suffix}")


def get_storage_energy_capacities(
        country:str,
        storage_type:str = "Hydro",
        technology:str = "CL pumping",
        eraa_year:int = 2026
):
    """
    loads the ERAA study data on storage energy capacities and
    extract the relevant data for the given country, storage type and technology.
    Args:
        country: 2-letter country code
        storage_type: "CL pumping", "OL pumping", "Market", "Non-market", "Reservoir"
        technology: "Batteries", "Hydro"
        eraa_year:
    Returns:
        a float with the storage energy capacity in MWh

    """

    eraa_storage  = _eraa_study_data("Storage.xlsx")
    eraa_storage = (
        eraa_storage
        .filter(pl.col("MARKET_NODE").str.slice(0, 2) == country)
        .filter(pl.col("TYPE_STORAGE") == storage_type)
        .filter(pl.col("TECHNOLOGY") == technology)
        .filter(pl.col("YEAR") == eraa_year)
        .filter(pl.col('DATA_VERSION') == 'Final')
    )
    if eraa_storage.is_empty():
        return 0.
    unit = eraa_storage['UNIT'][0]
    capacity = eraa_storage['VALUE'].sum()
    if unit == "GWh":
        capacity *= 1.e3
    elif unit == "TWh":
        capacity *= 1.e6
    else:
        raise ValueError(f"Unit {unit} not recognized.")

    return capacity

def fetch_h2_demand_from_demandforge(
    bundle_name: str,
    countries: list[str],
    model_year: int,
    sectors: list[str] | None = None,
    # Infrastructure params passed through to DemandForge
    units_config: dict | None = None,
    jet_unit_names: dict | None = None,
    jet_yields: dict | None = None,
    jrc_idees_path: str | None = None,
    tyndp_path: str | None = None,
    sectoral: bool = True,
) -> dict[str, dict[str, float]] | dict[str, float]:
    """Fetch hydrogen demand from DemandForge for use in POMMES.

    Calls DemandForge's load_bundle(), filters to a single model year,
    and returns sector-decomposed demand per country.

    Args:
        bundle_name: DemandForge scenario bundle (e.g. "central", "high_h2",
            "low_h2").
        countries: ISO-2 country codes.
        model_year: The year to extract (must be within the projection range).
        sectors: Optional sector filter. If None, uses all available sectors.
        units_config: CONCAWE unit definitions (for refinery sector).
        jet_unit_names: Jet reconstruction mapping (for eSAF sector).
        jet_yields: Hydrocracker jet yields (for eSAF sector).
        jrc_idees_path: JRC-IDEES path (for steel sector).
        tyndp_path: TYNDP path (for steel sector).
        sectoral: If True (default), return sector-decomposed dict.
            If False, return aggregated dict (legacy behaviour).

    Returns:
        If ``sectoral=True``:
            ``{country_code: {sector: demand_mwh_per_yr}}``
        If ``sectoral=False``:
            ``{country_code: demand_mwh_per_yr}`` (sum across sectors)

    Raises:
        ImportError: If demandforge is not installed.
        ValueError: If model_year is outside the projection range.
    """
    try:
        from demandforge.load_projection.scenarios import load_bundle
    except ImportError:
        raise ImportError(
            "DemandForge package not found. Install it with: "
            "pip install demandforge  (or add it to your environment)"
        )

    # Run DemandForge projection
    kwargs = {
        "countries": countries,
        "reference_year": 2019,
        "target_year": max(model_year, 2050),
    }
    if sectors is not None:
        kwargs["sectors"] = sectors
    if units_config is not None:
        kwargs["units_config"] = units_config
    if jet_unit_names is not None:
        kwargs["jet_unit_names"] = jet_unit_names
    if jet_yields is not None:
        kwargs["jet_yields"] = jet_yields
    if jrc_idees_path is not None:
        kwargs["jrc_idees_path"] = jrc_idees_path
    if tyndp_path is not None:
        kwargs["tyndp_path"] = tyndp_path

    df = load_bundle(bundle_name, **kwargs)

    # Filter to model year
    df_year = df[df["year"] == model_year]
    if df_year.empty:
        raise ValueError(
            f"No data for year {model_year} in bundle '{bundle_name}'. "
            f"Available years: {sorted(df['year'].unique())}"
        )

    if sectoral:
        # Return {country: {sector: MWh/yr}}
        result = {}
        for country in countries:
            df_c = df_year[df_year["country"] == country]
            if df_c.empty:
                result[country] = {}
            else:
                result[country] = (
                    df_c.set_index("sector")["h2_demand_mwh_per_yr"].to_dict()
                )
        return result
    else:
        # Legacy: return {country: total_MWh/yr}
        return (
            df_year
            .groupby("country")["h2_demand_mwh_per_yr"]
            .sum()
            .to_dict()
        )

def add_dispatchable_tech(
    area: Area,
    tech_name: str,
    plant_type: str,
    installed_capacities: pl.DataFrame,
    variable_cost: float,
    reference_year: int,
    ramp_rate: float = 1.0,
    fixed_cost: float = 0.0,
    investment_cost: float = 0.0,
    lifetime: float = 25.0,
) -> None:
    """Adds a dispatchable technology to the area."""
    if plant_type not in installed_capacities.columns:
        logger.info(f"Skipping {tech_name}: no installed capacity found for plant type '{plant_type}'.")
        return
    availability = _get_input_data_file(area.name, reference_year, "availability")

    capacity = installed_capacities[plant_type][0]
    year_op = area.model.year_ops[0]
    hours = area.model.hours


    tech_availability = (
        availability.filter(pl.col("plant_type") == plant_type)
        .select("availability_share")
        .rename({"availability_share": "availability"})
        .with_columns(pl.col("availability").clip(0., 1.))
    )


    if tech_availability.is_empty():
        logger.warning(f"No availability data for {tech_name}. Assuming 100% availability.")
        tech_availability = pl.DataFrame({"availability": [1.0] * 8760})
    elif tech_availability.height < 8760:
        tech_availability = pl.concat([tech_availability,
                                       tech_availability[-(8760 - tech_availability.height):]],)
    elif tech_availability.height > 8760:
        tech_availability = tech_availability[:8760]


    tech_availability = (
        tech_availability
        .with_columns(hour=pl.Series(hours), year_op=pl.lit(year_op).cast(pl.Int64))
    )

    logger.info(f"Adding {tech_name} with {capacity:.0f} MW capacity.")
    with area.model.context():
        tech = ConversionTechnology(
            name=tech_name,
            factor={"electricity": 1.0},
            availability=tech_availability,
            must_run=0.,
            variable_cost=variable_cost,
            fixed_cost=fixed_cost,
            invest_cost=investment_cost,
            life_span=lifetime,
            power_capacity_max=capacity,
            power_capacity_min=capacity,
            ramp_down=ramp_rate,
            ramp_up=ramp_rate,
            early_decommissioning=True,
        )
        area.add_component(tech)


def add_intermittent_tech(
    area: Area,
    tech_name: str,
    plant_type: str,
    capacity: float,
    reference_year: int,
    ws: str = None,
    fixed_cost: float = 0.0,
    investment_cost: float = 0.0,
    lifetime: float = 25.0,
) -> None:
    """Adds an intermittent (must-run) technology to the area."""



    year_op = area.model.year_ops[0]
    hours = area.model.hours

    capacity_factors = _get_input_data_file(area.name,
                                            reference_year,
                                            "capacity_factors")
    if plant_type in capacity_factors['plant_type'].unique().to_list():
        capacity_factors = capacity_factors.filter(pl.col("plant_type") == plant_type)
    else:
        capacity_factors = capacity_factors.filter(pl.col("plant_type") == tech_name)

    if ws is not None:
        if "WS" in capacity_factors.columns:
            capacity_factors = capacity_factors.filter(pl.col("WS") == ws)
        else:
            raise ValueError(f"WS '{ws}' not found in capacity factors data. Available only for future years.")

    if capacity_factors.is_empty():
        logger.warning(f"No capacity factor data for {tech_name} in {area.name} in {reference_year}. Replacing with 0.")
        capacity_factors = pl.DataFrame({"capacity_factor": [0.0] * 8760})

    # Append last row to the end
    if capacity_factors.height < 8760:
        capacity_factors = pl.concat([capacity_factors, capacity_factors[-(8760 - capacity_factors.height):]],)
    elif capacity_factors.height > 8760:
        capacity_factors = capacity_factors[:8760]

    tech_availability = (
        capacity_factors
        .select("capacity_factor")
        .rename({"capacity_factor": "availability"})
        .with_columns(hour=pl.Series(hours), year_op=pl.lit(year_op).cast(pl.Int64))
    )


    logger.info(f"Adding {tech_name} with {capacity:.0f} MW capacity.")

    with area.model.context():
        tech = ConversionTechnology(
            name=tech_name,
            factor={"electricity": 1.0},
            availability=tech_availability,
            must_run=0.,
            variable_cost=0.0,
            fixed_cost=fixed_cost,
            invest_cost=investment_cost,
            life_span=lifetime,
            power_capacity_max=capacity,
            power_capacity_min=capacity,
            early_decommissioning=True,
        )
        area.add_component(tech)


def add_reservoir_hydro(
    area: Area,
    installed_capacities: pl.DataFrame,
    inflows: pl.DataFrame,
    fixed_cost: float,
    investment_cost: float,
    country: str,
    eraa_year: int = 2026,
    lifetime: float = 25.0,
) -> None:
    """Adds reservoir hydro components (storage, inflow, plant) to the area."""
    year_op = area.model.year_ops[0]

    plant_type = "Hydro Water Reservoir"
    if plant_type not in installed_capacities.columns:
        logger.info(f"Skipping {plant_type}: no installed capacity found for plant type '{plant_type}'.")
        return

    turbine_capacity = installed_capacities[plant_type][0]
    storage_capacity = get_storage_energy_capacities(country,
                                                     storage_type="Reservoir",
                                                     technology="Hydro",
                                                     eraa_year=eraa_year)


    if inflows.is_empty():
        logger.warning("No inflow data for Reservoir Hydro. Skipping.")
        return

    logger.info(f"Adding Reservoir Hydro with {turbine_capacity:.0f} MW turbine capacity and {storage_capacity*1.e-3:.0f} GWh storage.")

    # 1. Storage component

    with area.model.context():
        stor = StorageTechnology(
            name="lake_hydro_store",
            factor_in={"reservoir_water": -1.0},
            factor_out={"reservoir_water": 1.0},
            factor_keep={"reservoir_water": 0.0},
            life_span=lifetime,
            energy_capacity_investment_max=storage_capacity,
            energy_capacity_investment_min=storage_capacity,
            early_decommissioning=True,
        )
        area.add_component(stor)

    if inflows.height < 8760:
        inflows = pl.concat([inflows,
                             inflows[-(8760 - inflows.height):]],)
    elif inflows.height > 8760:
        inflows = inflows[:8760]

    # 2. Inflow component (must-run)
    inflow_availability = inflows.with_columns(
        pl.Series("hour", range(8760)),
        pl.lit(year_op).cast(pl.Int64).alias("year_op"),
        (pl.col("inflow_MW") / pl.col("inflow_MW").max()).alias("availability")
    ).select(["hour", "year_op", "availability"])

    inflow_capacity = inflows["inflow_MW"].max()

    with area.model.context():

        tech = ConversionTechnology(
            name="lake_hydro_inflow",
            factor={"reservoir_water": 1.0},
            availability=inflow_availability,
            must_run=1.0,
            life_span=lifetime,
            power_capacity_max=inflow_capacity,
            power_capacity_min=inflow_capacity,
            early_decommissioning=True,
        )
        area.add_component(tech)

        # 3. Power plant component (dispatchable)
        tech = ConversionTechnology(
            name="lake_hydro_plant",
            factor={"reservoir_water": -1.0, "electricity": 1.0},
            availability=1.0,
            life_span=lifetime,
            fixed_cost=fixed_cost,
            invest_cost=investment_cost,
            power_capacity_max=turbine_capacity,
            power_capacity_min=turbine_capacity,
            early_decommissioning=True,
        )
        area.add_component(tech)


def add_pumped_hydro(
    area: Area,
    installed_capacities: pl.DataFrame,
    country: str,
    fixed_cost: float,
    investment_cost: float,
    lifetime: float = 25.0,
    eraa_year: int = 2026
):

    phs_storage_capacity = (
        get_storage_energy_capacities(country,
                                      storage_type="CL pumping",
                                      technology="Hydro",
                                      eraa_year=eraa_year)
        + get_storage_energy_capacities(country,
                                        storage_type="OL pumping",
                                        technology="Hydro",
                                        eraa_year=eraa_year)
    )
    # --- Storage ---
    plant_type = "Hydro Pumped Storage"
    if plant_type not in installed_capacities.columns:
        logger.info(f"Skipping {plant_type}: no installed capacity found for plant type '{plant_type}'.")
    else:
        turbine_capacity = installed_capacities[plant_type][0]
        storage_capacity = phs_storage_capacity
        with area.model.context():

            logger.info(
                f"Adding Pumped Hydro Storage with {turbine_capacity:.0f} MW power and {storage_capacity*1.e-3:.0f} GWh storage.")
            stor = StorageTechnology(
                name="pumped_hydro_storage",
                factor_in={"electricity": -1.0},
                factor_out={"electricity": 0.8},
                factor_keep={"electricity": 0.0},
                fixed_cost_power=fixed_cost,
                invest_cost_power=investment_cost,
                energy_capacity_investment_max=storage_capacity,
                energy_capacity_investment_min=storage_capacity,
                power_capacity_investment_max=turbine_capacity,
                power_capacity_investment_min=turbine_capacity,
                life_span=lifetime,
                early_decommissioning=True,
            )
            area.add_component(stor)

def add_imports(
        area: Area,
        country: str,
        reference_year: int,
):
    energy_model = area.model
    year_op = energy_model.year_ops[0]
    hours = energy_model.hours

    net_transfer_capacities = _get_input_data_file(
        country,
        reference_year,
        "net_transfer_capacities"
    )

    connected_countries = (
        net_transfer_capacities
        .max()
        .to_dicts()[0]
    )
    for connected_country, grid_capacity in connected_countries.items():

        bzn = connected_country
        for c, bzns in COUNTRY_BIDDING_ZONE_MAPPING.items():
            if connected_country in bzns:
                connected_country = c

        try:
            day_ahead_import_prices = get_electricity_import_prices(
                country,
                reference_year,
                year_op,
                hours,
                bzn
            )
            day_ahead_export_prices = (
                day_ahead_import_prices
                .rename({"import_price": "export_price"})
            )
        except:
            logger.warning(f"No day-ahead prices found for {connected_country}. Using default 150€/MWh")
            day_ahead_export_prices = pl.DataFrame(
                {
                    "export_price": [0.] * 8760,
                    "hour": hours,
                    "year_op": [year_op] * 8760,
                 }
            )
            day_ahead_import_prices = pl.DataFrame(
                {
                    "import_price": [200.0] * 8760,
                    "hour": hours,
                    "year_op": [year_op] * 8760,
                 }
            )

        with energy_model.context():
            area_connected = Area(connected_country)
            net_import = NetImport(
                name=f"import_from_{connected_country}",
                resource="electricity",
                import_price=day_ahead_import_prices,
                export_price=day_ahead_export_prices
            )
            area_connected.add_component(net_import)

            transport_tech_dict = {
                "name": "electric_line",
                "resource": "electricity",
                "life_span": 25.,
                "invest_cost": 0.,
                "fixed_cost": 0.,
                "hurdle_costs": 20.,
                "finance_rate": 0.,
                "power_capacity_investment_max": grid_capacity,
                "power_capacity_investment_min": grid_capacity,
            }

            transport_technology = TransportTechnology(
                **transport_tech_dict
            )
            link = Link(
                name=f"link_{area.name}_{connected_country}",
                area_from=area,
                area_to=area_connected,
            )
            link.add_transport_technology(transport_technology)

            transport_technology = TransportTechnology(
                **transport_tech_dict
            )
            link = Link(
                name=f"link_{connected_country}_{area.name}",
                area_from=area_connected,
                area_to=area,
            )
            link.add_transport_technology(transport_technology)
            ls = LoadShedding(name="electricity_load_shedding", resource="electricity", max_capacity=0.)
            area_connected.add_component(ls)


    with energy_model.context():
        net_import = NetImport(
            name="water_import",
            resource="reservoir_water",
            max_yearly_energy_import=0.0)
        area.add_component(net_import)
        net_import = NetImport(
            name="electricity_import",
            resource="electricity",
            max_yearly_energy_import=0.0)
        area.add_component(net_import)

def create_model(
        country_code: str,
        reference_year: int,
        model_year:int,
        dispatchable_tech_costs: dict = None,
        dispatchable_ramp_rates: dict = None,
        fixed_costs: dict = None,
        investment_costs: dict = None,
        lifetimes: dict = None,
        lifetime_step: int = 5,
) -> None:
    """
    Creates and configures an energy model for a given country and reference year.

    The function initializes a comprehensive energy model that includes
    technologies, economic assumptions, area components, and reserve structures
    based on the input parameters and specified configurations. It supports
    customization for various costs, ramp rates, and other parameters through
    additional inputs.

    Args:
        country_code (str): Two-character code representing the country for which
            the model is being created.
        reference_year (int): The reference year used for input data such as
            installed capacities or hydrological inflows.
        model_year (int): The operating year for which the model simulates energy
            operations.
        dispatchable_tech_costs (dict, optional): Costs related to dispatchable
            technologies. If not provided, defaults to predefined values.
        dispatchable_ramp_rates (dict, optional): Ramp rates associated with
            dispatchable technologies. If not provided, defaults to predefined
            values.
        fixed_costs (dict, optional): Fixed costs for various technologies. If not
            provided, defaults to predefined values.
        investment_costs (dict, optional): Investment costs for various
            technologies. If not provided, defaults to predefined values.
        lifetimes (dict, optional): Lifetimes of technologies. If not provided, defaults to predefined values

    Returns:
        None
    """
    logger.info(f"Creating pommes_craft model for {country_code} for the year {reference_year}...")

    # --- Load pre-processed data ---
    capacities = _get_input_data_file(country_code, reference_year, "installed_capacities")
    hydro_inflows = _get_input_data_file(country_code, reference_year, "inflow")

    if dispatchable_tech_costs is None:
        dispatchable_tech_costs = DISPATCHABLE_DEFAULT_COSTS
    else:
        dispatchable_tech_costs = DISPATCHABLE_DEFAULT_COSTS | dispatchable_tech_costs

    if dispatchable_ramp_rates is None:
        dispatchable_ramp_rates = DISPATCHABLE_RAMP_RATES
    else:
        dispatchable_ramp_rates = DISPATCHABLE_RAMP_RATES | dispatchable_ramp_rates

    if fixed_costs is None:
        fixed_costs = FIXED_COSTS
    else:
        fixed_costs = FIXED_COSTS | fixed_costs

    if investment_costs is None:
        investment_costs = INVEST_COSTS
    else:
        investment_costs = INVEST_COSTS | investment_costs

    if lifetimes is None:
        lifetimes = LIFETIMES
    else:
        lifetimes = LIFETIMES | lifetimes

    for name, lt in lifetimes.items():
        if lt % lifetime_step != 0:
            raise ValueError(f"Life time must be multiple of lifetime step {lifetime_step}: {lt} for technology {name}"
                             "You can either adjust the lifetimes by technology or change lifetime_step")

    max_lifetime = max(lifetimes.values())
    # --- Initialize EnergyModel ---
    hours = list(range(8760))
    energy_model = EnergyModel(
        name=f"model_{country_code}_ref_{reference_year}",
        hours=hours,
        year_ops=[model_year],
        year_invs=[model_year],
        year_decs=list(range(model_year, model_year + max_lifetime + lifetime_step, lifetime_step)),
        modes=["base"],
        resources=["electricity", "reservoir_water"],
    )

    # --- Economic, time steps and area components ---
    with energy_model.context():
        EconomicHypothesis("eco", discount_rate=0.0, year_ref=model_year, planning_step=25)
        TimeStepManager("ts", time_step_duration=1.0, operation_year_duration=8760)
        area = Area(country_code)

    # --- Generation Technologies ---
    # Dispatchable
    for tech_name, plant_type in DISPATCHABLE_TECH_DICT.items():
        add_dispatchable_tech(
            area=area,
            tech_name=tech_name,
            plant_type=plant_type,
            installed_capacities=capacities,
            reference_year=reference_year,
            variable_cost=dispatchable_tech_costs[tech_name],
            ramp_rate=dispatchable_ramp_rates[tech_name],
            fixed_cost=fixed_costs[tech_name],
            investment_cost=investment_costs[tech_name],
            lifetime=lifetimes[tech_name]
        )

    # Intermittent
    for tech_name, plant_type in INTERMITTENT_TECH_DICT.items():
        if plant_type not in capacities.columns:
            logger.info(f"Skipping {tech_name}: no installed capacity found for plant type '{plant_type}'.")
            return

        capacity = capacities[plant_type][0]
        add_intermittent_tech(
            area=area,
            tech_name=tech_name,
            plant_type=plant_type,
            capacity=capacity,
            reference_year=reference_year,
            fixed_cost=fixed_costs[tech_name],
            investment_cost=investment_costs[tech_name],
            lifetime=lifetimes[tech_name]
        )


    # Reserver Hydro
    add_reservoir_hydro(
        area=area,
        installed_capacities=capacities,
        inflows=hydro_inflows,
        fixed_cost=fixed_costs['Reservoir_Hydro'],
        investment_cost=investment_costs['Reservoir_Hydro'],
        lifetime=lifetimes['Reservoir_Hydro'],   
        country=country_code
    )

    # Pumped hydro storage
    add_pumped_hydro(
        area,
        capacities,
        country_code,
        fixed_costs['Pumped_Hydro'],
        investment_costs['Pumped_Hydro']
    )

    # Import from interconnected countries
    add_imports(area, country_code, reference_year)

    # --- Balancing and feasibility components ---

    with energy_model.context():
        spil = Spillage(name="electricity_spillage", resource="electricity", max_capacity=0.)
        area.add_component(spil)
        ls = LoadShedding(name="electricity_load_shedding", resource="electricity", cost=30_000.)
        area.add_component(ls)

        spil = Spillage(name="reservoir_water_spillage", resource="reservoir_water", max_capacity=50.0e3)
        area.add_component(spil)
        ls = LoadShedding(name="reservoir_water_load_shedding", resource="reservoir_water", max_capacity=0.0)
        area.add_component(ls)


    return energy_model


def get_existing_capacity(countries, reference_year):

    try:
        ntc_0 = _get_input_data_file(
            countries[0],
            reference_year,
            "net_transfer_capacities"
        ).max().to_dicts()[0]
        ntc_0_to_1 = ntc_0[countries[1]]
    except:
        ntc_0_to_1 = 0.

    try:
        ntc_1 = _get_input_data_file(
            countries[1],
            reference_year,
            "net_transfer_capacities"
        ).max().to_dicts()[0]
        ntc_1_to_0 = ntc_1[countries[0]]
    except:
        ntc_1_to_0 = 0.

    return max(ntc_0_to_1, ntc_1_to_0)


def add_interconnections(
        energy_model,
        countries: list,
        capacity: float | str,
        investment_cost: float,
        reference_year: int
):
    """
    Adds interconnection between specified countries in an energy model with defined
    capacity and investment costs. Handles bidirectional transportation of electricity
    between the two countries using a transport technology configuration.

    Args:
        energy_model: An energy simulation or optimization model to which the interconnection
            is being added. This is expected to manage the context and underlying configurations.
        countries: List of exactly two country identifiers (e.g., strings) that are connected
            via the interconnection.
        capacity: A float defining the power transmission capacity of the interconnection. If
            set to 'use_existing', existing capacity data will be fetched for the given countries
            and year.
        investment_cost: Investment cost per unit of the interconnection technology.
        reference_year: Year used for determining existing capacity if 'use_existing' is
            specified for the capacity.
    """

    if capacity == 'use_existing':
        capacity = get_existing_capacity(countries, reference_year)
    elif not isinstance(capacity, float):
        raise ValueError(f"Capacity must be a float or 'use_existing', got {capacity}")

    transport_tech_dict = {
        "name": "electric_line",
        "resource": "electricity",
        "life_span": 25.,
        "invest_cost": investment_cost,
        "fixed_cost": 0.,
        "hurdle_costs": 0.,
        "finance_rate": 0.,
        "power_capacity_investment_max": capacity,
        "power_capacity_investment_min": capacity,
    }
    with energy_model.context():
        transport_technology = TransportTechnology(
            **transport_tech_dict
        )
        link = Link(
            name=f"link_{countries[0]}_{countries[1]}",
            area_from=energy_model.areas[countries[0]],
            area_to=energy_model.areas[countries[1]],
        )
        link.add_transport_technology(transport_technology)

        transport_technology = TransportTechnology(
            **transport_tech_dict
        )
        link = Link(
            name=f"link_{countries[1]}_{countries[0]}",
            area_from=energy_model.areas[countries[1]],
            area_to=energy_model.areas[countries[0]],
        )
        link.add_transport_technology(transport_technology)


def _find_component(area, name: str):
    """Look up a component by name on an ``Area`` in a POMMES-version-agnostic way.

    POMMES' ``Area.components`` has been both a list and a dict across versions.
    We normalise that here so the rest of the code doesn't care.
    """
    components = getattr(area, "components", getattr(area, "_components", None))
    if components is None:
        return None
    if isinstance(components, dict):
        return components.get(name)
    # Assume iterable of components with a ``.name`` attribute
    for c in components:
        if getattr(c, "name", None) == name:
            return c
    return None


def _component_factor_dict(component) -> dict:
    """Return ``component.factor`` as a plain ``{resource: coef}`` dict.

    Handles the three representations seen in the wild:
      * plain ``dict`` (older pommes_craft);
      * polars ``DataFrame`` with two columns (resource, value) — newer pommes_craft;
      * pandas ``Series``/``DataFrame``;
      * ``None`` / missing attribute → ``{}``.

    Avoids the ``or {}`` truthiness shortcut, which raises
    ``TypeError: the truth value of a DataFrame is ambiguous`` on polars.
    """
    raw = getattr(component, "factor", None)
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if hasattr(raw, "iter_rows"):       # polars DataFrame
        try:
            return {row[0]: row[1] for row in raw.iter_rows()}
        except Exception:
            return {}
    if hasattr(raw, "to_dict"):         # pandas
        try:
            d = raw.to_dict()
            if d and all(isinstance(v, dict) for v in d.values()):
                return {k: next(iter(v.values())) for k, v in d.items()}
            return dict(d)
        except Exception:
            return {}
    try:
        return dict(raw)
    except Exception:
        return {}


def _rewire_to_hydrogen_bus(
    component,
    electricity_factor: float = 1.0,
    hydrogen_factor: float = -2.85,
):
    """Patch a ``ConversionTechnology``'s ``factor`` to consume H₂ from the bus.

    Used to promote CLEVER's electricity-only ``Hydrogen_power_plant`` component
    (``factor={"electricity": 1.0}``) into a properly-coupled H₂ CCGT
    (``factor={"electricity": +1, "hydrogen": -2.85}``) without creating a
    duplicate component.  CLEVER's capacity discipline (existing MW from
    ``proelchyd/FLH`` plus ``EXPANSION_HEADROOM_BY_COUNTRY``) is preserved.
    """
    # Robustly normalise component.factor → plain dict.  Newer pommes_craft
    # versions store ``factor`` as a 2-column polars DataFrame (resource, value)
    # rather than a Python dict, so the old `or {}` truthiness check raises
    # `TypeError: the truth value of a DataFrame is ambiguous`.
    raw = getattr(component, "factor", None)
    if raw is None:
        factor = {}
    elif isinstance(raw, dict):
        factor = dict(raw)
    elif hasattr(raw, "iter_rows"):                # polars DataFrame
        try:
            factor = {row[0]: row[1] for row in raw.iter_rows()}
        except Exception:
            factor = {}
    elif hasattr(raw, "to_dict"):                  # pandas Series / DataFrame
        try:
            d = raw.to_dict()
            # pandas DataFrame.to_dict() → {col: {row: val}}; flatten if needed
            if d and all(isinstance(v, dict) for v in d.values()):
                # assume single-row DataFrame: take first row of each column
                factor = {k: next(iter(v.values())) for k, v in d.items()}
            else:
                factor = dict(d)
        except Exception:
            factor = {}
    else:
        try:
            factor = dict(raw)
        except Exception:
            factor = {}
    factor["electricity"] = electricity_factor
    factor["hydrogen"] = hydrogen_factor

    # In newer pommes_craft, ``factor`` is a polars DataFrame with columns
    # ``[resource, year_op, factor]`` (one row per (resource, year_op) pair),
    # not a plain dict.  Clobbering it with a dict breaks
    # ``Component.generate_component_table`` (it checks
    # ``isinstance(getattr(component, attr_name), pl.DataFrame)``), causing a
    # silent ``return None`` and a cryptic
    # ``TypeError: cannot unpack non-iterable NoneType object`` downstream.
    #
    # Rebuild by delegating to the component's own validator
    # (``process_attribute_input``), which knows the correct schema.
    try:
        new_value = component.process_attribute_input(
            "factor", factor, ["resource", "year_op"]
        )
    except Exception:
        # Fallback: construct the DataFrame ourselves using the component's
        # model to expand over ``year_op``.
        try:
            years_op = list(component.model.year_ops)
        except Exception:
            years_op = [component.model.year_ops[0]]
        rows = [
            {"resource": r, "year_op": y, "factor": v}
            for r, v in factor.items()
            for y in years_op
        ]
        new_value = pl.DataFrame(rows)

    try:
        component.factor = new_value
    except AttributeError:
        # Some POMMES versions expose ``_factor`` instead
        component._factor = new_value


def add_hydrogen(
    area,
    hydrogen_demand,
    hydrogen_capacity,
    fixed_costs,
    investment_costs,
    variable_costs,
    lifetimes,
    investable: bool = False,
    storage_type: str = "tank",
    allow_h2_trade: bool = False,
    h2pp_invest_max_mw: float | None = None,
    electrolyser_invest_max_mw: float | None = None,
    storage_power_invest_max_mw: float | None = None,
    storage_energy_invest_max_mwh: float | None = None,
    patch_existing_h2pp: bool = True,
):
    """Add a hydrogen system to a POMMES-CRAFT area.

    Supports two modes:

    **Legacy mode** (``hydrogen_demand`` is a scalar):
        Single flat demand node, fixed electrolyser/storage capacity.
        Backward-compatible with existing call sites.

    **Sectoral mode** (``hydrogen_demand`` is ``{sector: MWh/yr}``):
        One ``Demand`` node per sector with physically-motivated hourly
        profiles.  Electrolyser and storage are optionally investable
        (capacity determined by the optimiser).

    In both modes, a single ``"hydrogen"`` resource is used (Option A from
    the coupling design).  Sector attribution is preserved via named demand
    nodes (``h2_demand_ammonia``, ``h2_demand_steel``, etc.).

    Args:
        area: POMMES Area object.
        hydrogen_demand: Either a scalar (MWh/yr, legacy) or a dict
            ``{sector: MWh/yr}`` (sectoral mode).
        hydrogen_capacity: H₂ power plant capacity (MW).  Ignored when
            ``investable=True``.
        fixed_costs: Technology fixed costs dict.
        investment_costs: Technology investment costs dict.
        variable_costs: Technology variable costs dict.
        lifetimes: Technology lifetimes dict.
        investable: If True, electrolyser and storage capacities are
            determined by the optimiser (subject to the *_invest_max caps).
        storage_type: ``"tank"`` or ``"salt_cavern"``.
        allow_h2_trade: If True, do not set import/export to zero
            (inter-area H₂ transport is handled separately).
        h2pp_invest_max_mw: Cap on H₂ power-plant investment (MW).  If
            ``None`` and ``patch_existing_h2pp`` is True, defers fully to
            the CLEVER-injected ``Hydrogen_power_plant`` component.
        electrolyser_invest_max_mw: Cap on electrolyser investment (MW).
            If ``None``, uncapped.
        storage_power_invest_max_mw: Cap on H₂ storage power rating (MW).
            If ``None``, uncapped.
        storage_energy_invest_max_mwh: Cap on H₂ storage energy capacity
            (MWh).  If ``None``, uncapped.
        patch_existing_h2pp: If True and the area already has a
            ``Hydrogen_power_plant`` component (e.g. from CLEVER's non_enr
            injection), rewire its ``factor`` to draw from the hydrogen
            resource (``hydrogen: -2.85``) instead of adding a duplicate.
            Respects CLEVER's capacity discipline (existing + headroom).
    """
    energy_model = area.model
    year_op = energy_model.year_ops[0]
    hours = energy_model.hours

    # ── Determine mode: scalar (legacy) vs sectoral ──────────────
    if isinstance(hydrogen_demand, dict):
        # Sectoral mode: build per-sector demand nodes with temporal profiles
        sector_demands = {s: v for s, v in hydrogen_demand.items() if v > 0}
        total_demand = sum(sector_demands.values())
    else:
        # Legacy mode: single scalar → wrap as flat single-sector
        sector_demands = None
        total_demand = float(hydrogen_demand)

    has_industrial_demand = total_demand > 0

    # In legacy (non-investable) mode with no demand, the electrolyser sizing
    # heuristic collapses to zero — nothing useful to add.  Skip.
    if not has_industrial_demand and not investable:
        logger.info(
            f"No H₂ demand and non-investable mode for {area.name} — skipping H₂ system."
        )
        return

    # Otherwise we still add the full supply stack: even without industrial demand,
    # the electrolyser + storage + H₂ power plant provide electricity-sector
    # flexibility and can serve H₂ trade.  The optimiser will choose zero
    # investment if it's not economic.

    with energy_model.context():
        # ── 1. Electrolyser ──────────────────────────────────────
        electrolyser_kwargs = {
            "name": "electrolysis",
            # 1.43 MWh_e/MWh_H2 = 70% LHV efficiency (2050-class PEM/alkaline,
            # DEA/TYNDP). Was -1.85 (54%, 2023-vintage) until 2026-06-11 — that
            # miscalibration priced electrolysis ~30% above its 2050 cost.
            "factor": {"hydrogen": 1., "electricity": -1.43},
            "emission_factor": 0.,
            "fixed_cost": fixed_costs["Electrolyser"],
            "variable_cost": 0.,
            "life_span": lifetimes["Electrolyser"],
            "must_run": 0.,
            "availability": 1.0,
            "invest_cost": investment_costs["Electrolyser"],
            "early_decommissioning": True,
        }
        if not investable:
            # Legacy: size electrolyser to meet peak hourly demand with margin.
            # For shaped profiles, peak/avg ≈ 1.25 (steel) to 1.5 (maritime).
            # Apply 1.43 electrolyser factor (MWh_e / MWh_H₂) and 20% headroom.
            # (This branch is only reached when has_industrial_demand=True — the
            # zero-demand + non-investable case is rejected earlier.)
            avg_mw = total_demand / 8760
            peak_factor = 1.5   # conservative: covers maritime seasonality
            headroom = 1.2
            peak_mw = avg_mw * peak_factor * headroom * 1.43
            electrolyser_kwargs["power_capacity_max"] = peak_mw
            electrolyser_kwargs["power_capacity_min"] = 0.  # investable up to max
        elif electrolyser_invest_max_mw is not None:
            # Investable with an explicit cap (same pattern as CLEVER's
            # EXPANSION_HEADROOM_BY_COUNTRY for dispatchable techs).
            # MUST set power_capacity_max too — without it POMMES defaults
            # total capacity to 0, making the investment bounds useless.
            electrolyser_kwargs["power_capacity_max"] = float(electrolyser_invest_max_mw)
            electrolyser_kwargs["power_capacity_investment_max"] = float(
                electrolyser_invest_max_mw
            )
            electrolyser_kwargs["power_capacity_investment_min"] = 0.

        electrolyser = ConversionTechnology(**electrolyser_kwargs)
        area.add_component(electrolyser)

        # ── 2. Demand nodes (only if industrial H₂ demand exists) ──
        if has_industrial_demand:
            if sector_demands is not None:
                # Sectoral mode: build one profile per sector so sector-specific
                # shapes (e.g. ammonia baseload vs maritime bunker) are honoured,
                # then SUM hourly across sectors into a single Demand node.
                #
                # Why aggregate: pommes' demand table is keyed by
                # (resource, area, hour, year_op) with NO per-node column, so
                # multiple Demand components sharing ``resource="hydrogen"`` in
                # the same area collide on the same index key and produce a
                # non-unique MultiIndex in ``build_input_parameters``
                # (``ValueError: cannot convert a DataFrame with a non-unique
                # MultiIndex into xarray``).
                #
                # Per-sector attribution is preserved in the log and in the
                # upstream sector_demands dict for reporting.
                profiles = build_all_sector_profiles(sector_demands, hours, year_op)

                # Aggregate: concat all per-sector profiles and sum by
                # (hour, year_op).  Each profile_df is a polars DataFrame with
                # columns ["demand", "hour", "year_op"] spanning the same 8760
                # hours of the same year_op, so groupby-sum is well-defined.
                agg_df = None
                if profiles:
                    concat_df = pl.concat(
                        [p.select(["demand", "hour", "year_op"]) for p in profiles.values()]
                    )
                    agg_df = (
                        concat_df
                        .group_by(["hour", "year_op"])
                        .agg(pl.col("demand").sum())
                        .sort(["year_op", "hour"])
                        .select(["demand", "hour", "year_op"])
                    )

                if agg_df is not None:
                    demand = Demand(
                        name="h2_demand_total",
                        resource="hydrogen",
                        demand=agg_df,
                    )
                    area.add_component(demand)

                logger.info(
                    f"Added aggregated H₂ demand node for {area.name} "
                    f"(summed over {len(profiles)} sectors): "
                    f"{', '.join(f'{s}={d:.0f} MWh' for s, d in sector_demands.items())}"
                )
            else:
                # Legacy: single flat profile
                demand_df = pl.DataFrame({
                    "demand": [total_demand / 8760.] * 8760,
                    "hour": hours,
                    "year_op": [year_op] * 8760,
                })
                demand = Demand(
                    name="hydrogen_demand",
                    resource="hydrogen",
                    demand=demand_df,
                )
                area.add_component(demand)
        else:
            logger.info(
                f"No industrial H₂ demand for {area.name} — added supply stack only "
                f"(electrolyser + storage + H₂ power plant) for power-sector flexibility."
            )

        # ── 3. Load shedding & spillage ──────────────────────────
        load_shedding = LoadShedding(
            name="hydrogen_load_shedding",
            resource="hydrogen",
            max_capacity=0.,
        )
        area.add_component(load_shedding)

        spillage = Spillage(
            name="hydrogen_spillage",
            resource="hydrogen",
            max_capacity=0.,
        )
        area.add_component(spillage)

        # ── 4. Import/export (disabled unless H₂ trade enabled) ─
        if not allow_h2_trade:
            hydrogen_import = NetImport(
                name="hydrogen_import",
                resource="hydrogen",
                max_yearly_energy_import=0.,
                max_yearly_energy_export=0.,
            )
            area.add_component(hydrogen_import)

        # ── 5. H₂ storage ───────────────────────────────────────
        storage_costs = H2_STORAGE_COSTS.get(storage_type, H2_STORAGE_COSTS["tank"])
        storage_kwargs = dict(
            name="h2_storage",
            factor_in={"hydrogen": -1.02},
            factor_out={"hydrogen": 1.},
            factor_keep={"hydrogen": 0.},
            invest_cost_energy=storage_costs["invest_energy"],
            invest_cost_power=storage_costs["invest_power"],
            fixed_cost_power=fixed_costs["Hydrogen_storage_power"],
            dissipation=0.,
            life_span=lifetimes["Hydrogen_storage"],
        )
        if storage_power_invest_max_mw is not None:
            storage_kwargs["power_capacity_investment_max"] = float(
                storage_power_invest_max_mw
            )
            storage_kwargs["power_capacity_investment_min"] = 0.
        if storage_energy_invest_max_mwh is not None:
            storage_kwargs["energy_capacity_investment_max"] = float(
                storage_energy_invest_max_mwh
            )
            storage_kwargs["energy_capacity_investment_min"] = 0.
        hydrogen_storage = StorageTechnology(**storage_kwargs)
        area.add_component(hydrogen_storage)

        # ── 6. H₂ power plant (re-electrification) ──────────────
        # Pattern: respect CLEVER's capacity discipline.  If CLEVER already
        # injected a ``Hydrogen_power_plant`` component (from the ``proelchyd``
        # row of non_enr.csv, with capacity = production/FLH and expansion
        # bounded by ``EXPANSION_HEADROOM_BY_COUNTRY``), we patch its
        # ``factor`` to couple it to the hydrogen resource rather than adding
        # a duplicate uncoupled component.
        existing_h2pp = _find_component(area, "Hydrogen_power_plant")
        if existing_h2pp is not None and patch_existing_h2pp:
            _rewire_to_hydrogen_bus(
                existing_h2pp,
                electricity_factor=1.0,
                hydrogen_factor=-2.85,
            )
            logger.info(
                f"Patched existing CLEVER 'Hydrogen_power_plant' in {area.name} "
                f"to draw from hydrogen resource (factor electricity:+1, hydrogen:-2.85)."
            )
        else:
            # No CLEVER H₂ CCGT for this area: add a fresh, properly-coupled one.
            h2pp_kwargs = {
                "name": "hydrogen_power_plant",
                "factor": {"electricity": 1.0, "hydrogen": -2.85},
                "availability": 1.,
                "must_run": 0.,
                "variable_cost": variable_costs["Hydrogen_power_plant"],
                "fixed_cost": fixed_costs["Hydrogen_power_plant"],
                "invest_cost": investment_costs["Hydrogen_power_plant"],
                "life_span": lifetimes["Hydrogen_power_plant"],
                "early_decommissioning": True,
            }
            if not investable:
                h2pp_kwargs["power_capacity_max"] = hydrogen_capacity
                h2pp_kwargs["power_capacity_min"] = hydrogen_capacity
            elif h2pp_invest_max_mw is not None:
                # MUST set power_capacity_max too — without it POMMES defaults
                # total capacity to 0, making the investment bounds useless.
                h2pp_kwargs["power_capacity_max"] = float(h2pp_invest_max_mw)
                h2pp_kwargs["power_capacity_investment_max"] = float(h2pp_invest_max_mw)
                h2pp_kwargs["power_capacity_investment_min"] = 0.
            tech = ConversionTechnology(**h2pp_kwargs)
            area.add_component(tech)
            logger.info(
                f"Added fresh hydrogen-coupled H₂ CCGT to {area.name} "
                f"(CLEVER did not declare proelchyd for this country)."
            )


def add_h2_interconnections(
    energy_model,
    countries: list[str],
    pipeline_type: str = "new",
    pipeline_costs: dict | None = None,
):
    """Add bidirectional H₂ pipeline links between adjacent countries.

    Only connects country pairs that appear in ``H2_ADJACENCY``.
    Pipeline capacity is investable — the optimiser decides the optimal
    sizing.

    Args:
        energy_model: The POMMES EnergyModel.
        countries: List of country codes present in the model.
        pipeline_type: ``"new"`` for dedicated H₂ pipeline or
            ``"repurposed"`` for converted natural gas pipeline.
        pipeline_costs: Override for ``H2_PIPELINE_COSTS``.
    """
    from itertools import combinations

    costs = pipeline_costs or H2_PIPELINE_COSTS
    if pipeline_type == "repurposed":
        invest = costs["repurposed_pipeline_invest"]
    else:
        invest = costs["new_pipeline_invest"]

    hurdle = costs["hurdle_cost"]
    fixed = costs["fixed_cost"]
    lifetime = float(costs["lifetime"])

    # Resolve available areas (only connect countries present in the model)
    model_areas = energy_model.areas
    if isinstance(model_areas, list):
        model_areas = {getattr(a, "name", str(a)): a for a in model_areas}
    available = set(model_areas.keys())

    links_added = 0
    for c1, c2 in combinations(countries, 2):
        if c1 not in available or c2 not in available:
            continue
        if (c1, c2) not in H2_ADJACENCY and (c2, c1) not in H2_ADJACENCY:
            continue

        with energy_model.context():
            # Forward direction: c1 → c2
            transport_fwd = TransportTechnology(
                name="h2_pipeline",
                resource="hydrogen",
                life_span=lifetime,
                invest_cost=invest,
                fixed_cost=fixed,
                hurdle_costs=hurdle,
                finance_rate=0.,
            )
            link_fwd = Link(
                name=f"h2_link_{c1}_{c2}",
                area_from=model_areas[c1],
                area_to=model_areas[c2],
            )
            link_fwd.add_transport_technology(transport_fwd)

            # Reverse direction: c2 → c1
            transport_rev = TransportTechnology(
                name="h2_pipeline",
                resource="hydrogen",
                life_span=lifetime,
                invest_cost=invest,
                fixed_cost=fixed,
                hurdle_costs=hurdle,
                finance_rate=0.,
            )
            link_rev = Link(
                name=f"h2_link_{c2}_{c1}",
                area_from=model_areas[c2],
                area_to=model_areas[c1],
            )
            link_rev.add_transport_technology(transport_rev)

        links_added += 1
        logger.info(f"Added H₂ pipeline link: {c1} ↔ {c2}")

    logger.info(f"Total H₂ interconnections added: {links_added}")


def create_multi_country_renewable_model(
        renewable_installed_capacities: dict,
        biogas_capacities: dict,
        hydrogen_capacities: dict,
        hydrogen_demand: dict,
        interconnections: dict,
        reference_year:int,
        eraa_capacity_factor_year:int,
        ws: str,
        model_year:int,
        dispatchable_tech_costs: dict = None,
        dispatchable_ramp_rates: dict = None,
        fixed_costs: dict = None,
        investment_costs: dict = None,
        lifetimes: dict = None,
        lifetime_step: int = 5,
        demandforge_bundle: str = None,
        # ── New H₂ coupling parameters ──
        h2_investable: bool = False,
        h2_storage_type: str = "tank",
        h2_transport: bool = False,
        h2_pipeline_type: str = "new",
        h2_pipeline_costs: dict = None,
) -> None:
    """Create a multi-country renewable energy model.

    This function initializes and configures a renewable energy model for multiple countries
    using the provided energy capacities, demand data, economic factors, and technical parameters.
    The model incorporates renewable technologies, interconnections, and dispatchable resources.

    Args:
        renewable_installed_capacities (dict): Installed capacities of renewable technologies (e.g., solar, wind)
            for each country by technology.
        biogas_capacities (dict): Installed capacities of biogas plants for each country.
        hydrogen_capacities (dict): Installed capacities of hydrogen production for each country.
        hydrogen_demand (dict): Annual hydrogen demand for each country.
        interconnections (dict): Interconnection capacities between countries.
        reference_year (int): The reference year for the model's input data.
        eraa_capacity_factor_year (int): The year at which the capacity factor of the ERAA is taken.
        ws (str): The weather scenario used for eraa capacity factor data.
        model_year (int): The model's operation year for which the scenario is prepared.
        dispatchable_tech_costs (dict, optional): Costs for dispatchable technologies (e.g., biogas).
            Defaults to a pre-defined constant set of costs.
        dispatchable_ramp_rates (dict, optional): Ramp rates for dispatchable technologies.
            Defaults to a pre-defined constant set of ramp rates.
        fixed_costs (dict, optional): Fixed costs for all technologies. Defaults to pre-defined fixed cost values.
        investment_costs (dict, optional): Investment costs for all technologies. Defaults to pre-defined investment costs.
        lifetimes (dict, optional): Lifetimes of technologies in the model.
            Defaults to pre-defined lifetime values.
        lifetime_step (int, optional): The step size for lifetime grouping. Defaults to 5.

    Raises:
        ValueError: If the lifetime of any technology is not a multiple of the lifetime step.

    """
    logger.info(f"Creating pommes_craft model for {', '.join(list(renewable_installed_capacities.keys()))} "
                f"for the year {model_year}...")

    if dispatchable_tech_costs is None:
        dispatchable_tech_costs = DISPATCHABLE_DEFAULT_COSTS
    else:
        dispatchable_tech_costs = DISPATCHABLE_DEFAULT_COSTS | dispatchable_tech_costs

    if dispatchable_ramp_rates is None:
        dispatchable_ramp_rates = DISPATCHABLE_RAMP_RATES
    else:
        dispatchable_ramp_rates = DISPATCHABLE_RAMP_RATES | dispatchable_ramp_rates

    if fixed_costs is None:
        fixed_costs = FIXED_COSTS
    else:
        fixed_costs = FIXED_COSTS | fixed_costs

    if investment_costs is None:
        investment_costs = INVEST_COSTS
    else:
        investment_costs = INVEST_COSTS | investment_costs

    if lifetimes is None:
        lifetimes = LIFETIMES
    else:
        lifetimes = LIFETIMES | lifetimes
    
    # ── DemandForge H₂ integration ─────────────────────────────
    # When a DemandForge bundle is specified, fetch sector-decomposed
    # demand.  This replaces the old scalar-sum approach.
    _h2_sectoral = {}  # {country: {sector: MWh/yr}} — filled if DemandForge active
    if demandforge_bundle is not None:
        countries = list(renewable_installed_capacities.keys())
        _h2_sectoral = fetch_h2_demand_from_demandforge(
            bundle_name=demandforge_bundle,
            countries=countries,
            model_year=model_year,
            sectoral=True,
        )
        # Also populate the scalar hydrogen_demand dict for backward compat
        for cc in countries:
            sector_total = sum(_h2_sectoral.get(cc, {}).values())
            if cc not in hydrogen_demand or hydrogen_demand[cc] == 0:
                hydrogen_demand[cc] = sector_total
        logger.info(
            f"H₂ demand populated from DemandForge bundle '{demandforge_bundle}' "
            f"for year {model_year} (sectoral mode)"
        )

    for name, lt in lifetimes.items():
        if lt % lifetime_step != 0:
            raise ValueError(f"Life time must be multiple of lifetime step {lifetime_step}: {lt} for technology {name}"
                             "You can either adjust the lifetimes by technology or change lifetime_step")

    max_lifetime = max(lifetimes.values())
    countries = list(renewable_installed_capacities.keys())
    # --- Initialize EnergyModel ---
    hours = list(range(8760))
    energy_model = EnergyModel(
        name=f"model_{'_'.join(countries)}_{model_year}",
        hours=hours,
        year_ops=[model_year],
        year_invs=[model_year],
        year_decs=list(range(model_year, model_year + max_lifetime + lifetime_step, lifetime_step)),
        modes=["base"],
        resources=["electricity", "reservoir_water", "hydrogen"],
    )

    # --- Economic, time steps and area components ---
    areas = {}
    with energy_model.context():
        EconomicHypothesis("eco", discount_rate=0.0, year_ref=model_year, planning_step=25)
        TimeStepManager("ts", time_step_duration=1.0, operation_year_duration=8760)
        for country_code in countries:
            areas[country_code] = Area(country_code)


    for country_code, r_capa in renewable_installed_capacities.items():
        for tech_name, capacity in r_capa.items():
            plant_type = INTERMITTENT_TECH_DICT[tech_name]
            add_intermittent_tech(
                area=areas[country_code],
                tech_name=tech_name,
                plant_type=plant_type,
                capacity=capacity,
                reference_year=eraa_capacity_factor_year,
                fixed_cost=fixed_costs[tech_name],
                investment_cost=investment_costs[tech_name],
                lifetime=lifetimes[tech_name],
                ws=ws
            )

    for country_code in countries:
        # --- Load pre-processed data ---
        capacities = _get_input_data_file(country_code, reference_year, "installed_capacities")
        hydro_inflows = _get_input_data_file(country_code, reference_year, "inflow")

        # Reserver Hydro
        add_reservoir_hydro(
            area=areas[country_code],
            installed_capacities=capacities,
            inflows=hydro_inflows,
            fixed_cost=fixed_costs['Reservoir_Hydro'],
            investment_cost=investment_costs['Reservoir_Hydro'],
            lifetime=lifetimes['Reservoir_Hydro'],
            country=country_code
        )

        # Pumped hydro storage
        add_pumped_hydro(
            areas[country_code],
            capacities,
            country_code,
            fixed_costs['Pumped_Hydro'],
            investment_costs['Pumped_Hydro']
        )

        # add biogas plant
        with energy_model.context():
            tech = ConversionTechnology(
                name="Biogas",
                factor={"electricity": 1.0},
                availability=1.,
                must_run=0.,
                variable_cost=dispatchable_tech_costs["Biogas"],
                fixed_cost=fixed_costs["Biogas"],
                invest_cost=investment_costs["Biogas"],
                life_span=lifetimes["Biogas"],
                power_capacity_max=biogas_capacities[country_code],
                power_capacity_min=biogas_capacities[country_code],
                ramp_down=1.,
                ramp_up=1.,
                early_decommissioning=True,
            )
            areas[country_code].add_component(tech)

    for country_code, hydrogen_d in hydrogen_demand.items():
        # Use sectoral demand if available from DemandForge
        if country_code in _h2_sectoral and _h2_sectoral[country_code]:
            h2_demand_input = _h2_sectoral[country_code]
        else:
            h2_demand_input = hydrogen_d  # scalar (legacy)

        add_hydrogen(
            areas[country_code],
            h2_demand_input,
            hydrogen_capacities[country_code],
            fixed_costs,
            investment_costs,
            dispatchable_tech_costs,
            lifetimes,
            investable=h2_investable,
            storage_type=h2_storage_type,
            allow_h2_trade=h2_transport,
        )

    # ── Electricity interconnections ─────────────────────────────
    for countries, interconnection in interconnections.items():
        add_interconnections(
            energy_model,
            countries,
            interconnection['capacity'],
            interconnection['investment_cost'],
            reference_year
        )

    # ── H₂ interconnections (Phase 5) ───────────────────────────
    if h2_transport:
        add_h2_interconnections(
            energy_model,
            list(areas.keys()),
            pipeline_type=h2_pipeline_type,
            pipeline_costs=h2_pipeline_costs,
        )

    # --- Balancing and feasibility components ---

    with energy_model.context():
        for area in areas.values():
            spil = Spillage(name="electricity_spillage", resource="electricity", max_capacity=0.)
            area.add_component(spil)
            ls = LoadShedding(name="electricity_load_shedding", resource="electricity", cost=30_000.)
            area.add_component(ls)

            spil = Spillage(name="reservoir_water_spillage", resource="reservoir_water", max_capacity=50.0e3)
            area.add_component(spil)
            ls = LoadShedding(name="reservoir_water_load_shedding", resource="reservoir_water", max_capacity=0.0)
            area.add_component(ls)
            net_import = NetImport(
                name="electricity_import",
                resource="electricity",
                max_yearly_energy_import=0.0,
                max_yearly_energy_export=0.0)
            area.add_component(net_import)
    return energy_model

def create_multi_country_current_model(
        country_codes: list[str],
        interconnections: dict,
        reference_year:int,
        model_year:int,
        dispatchable_tech_costs: dict = None,
        dispatchable_ramp_rates: dict = None,
        fixed_costs: dict = None,
        investment_costs: dict = None,
        lifetimes: dict = None,
        lifetime_step: int = 5,
        # --- NEW hydrogen params (all optional, backward-compatible) ---
        hydrogen_capacities: dict | None = None,
        hydrogen_demand: dict | None = None,
        demandforge_bundle: str | None = None,
) -> None:
    """Create a multi-country renewable energy model.

    This function initializes and configures a renewable energy model for multiple countries
    using the provided energy capacities, demand data, economic factors, and technical parameters.
    The model incorporates renewable technologies, interconnections, and dispatchable resources.

    Args:
        country_codes:
        renewable_installed_capacities (dict): Installed capacities of renewable technologies (e.g., solar, wind)
            for each country by technology.
        interconnections (dict): Interconnection capacities between countries.
        reference_year (int): The reference year for the model's input data.
        model_year (int): The model's operation year for which the scenario is prepared.
        dispatchable_tech_costs (dict, optional): Costs for dispatchable technologies (e.g., biogas).
            Defaults to a pre-defined constant set of costs.
        dispatchable_ramp_rates (dict, optional): Ramp rates for dispatchable technologies.
            Defaults to a pre-defined constant set of ramp rates.
        fixed_costs (dict, optional): Fixed costs for all technologies. Defaults to pre-defined fixed cost values.
        investment_costs (dict, optional): Investment costs for all technologies. Defaults to pre-defined investment costs.
        lifetimes (dict, optional): Lifetimes of technologies in the model.
            Defaults to pre-defined lifetime values.
        lifetime_step (int, optional): The step size for lifetime grouping. Defaults to 5.

    Raises:
        ValueError: If the lifetime of any technology is not a multiple of the lifetime step.

    """
    logger.info(f"Creating pommes_craft model for {', '.join(list(country_codes))} "
                f"for the year {model_year}...")

    if dispatchable_tech_costs is None:
        dispatchable_tech_costs = DISPATCHABLE_DEFAULT_COSTS
    else:
        dispatchable_tech_costs = DISPATCHABLE_DEFAULT_COSTS | dispatchable_tech_costs

    if dispatchable_ramp_rates is None:
        dispatchable_ramp_rates = DISPATCHABLE_RAMP_RATES
    else:
        dispatchable_ramp_rates = DISPATCHABLE_RAMP_RATES | dispatchable_ramp_rates

    if fixed_costs is None:
        fixed_costs = FIXED_COSTS
    else:
        fixed_costs = FIXED_COSTS | fixed_costs

    if investment_costs is None:
        investment_costs = INVEST_COSTS
    else:
        investment_costs = INVEST_COSTS | investment_costs

    if lifetimes is None:
        lifetimes = LIFETIMES
    else:
        lifetimes = LIFETIMES | lifetimes

    # --- Optional hydrogen support ---
    if hydrogen_demand is None:
        hydrogen_demand = {}
    if hydrogen_capacities is None:
        hydrogen_capacities = {}

    if demandforge_bundle is not None:
        countries = list(current_installed_capacities.keys())
        df_h2 = fetch_h2_demand_from_demandforge(
            bundle_name=demandforge_bundle,
            countries=countries,
            model_year=model_year,
        )
        for cc in countries:
            if cc not in hydrogen_demand or hydrogen_demand[cc] == 0:
                hydrogen_demand[cc] = df_h2.get(cc, 0.0)
        logger.info(
            f"H2 demand populated from DemandForge bundle '{demandforge_bundle}' "
            f"for year {model_year}"
        )

    for name, lt in lifetimes.items():
        if lt % lifetime_step != 0:
            raise ValueError(f"Life time must be multiple of lifetime step {lifetime_step}: {lt} for technology {name}"
                             "You can either adjust the lifetimes by technology or change lifetime_step")

    max_lifetime = max(lifetimes.values())
    # --- Initialize EnergyModel ---
    hours = list(range(8760))
    energy_model = EnergyModel(
        name=f"model_{'_'.join(country_codes)}_{model_year}",
        hours=hours,
        year_ops=[model_year],
        year_invs=[model_year],
        year_decs=list(range(model_year, model_year + max_lifetime + lifetime_step, lifetime_step)),
        modes=["base"],
        resources=["electricity", "reservoir_water", "hydrogen"],
    )

    # --- Economic, time steps and area components ---
    areas = {}
    with energy_model.context():
        EconomicHypothesis("eco", discount_rate=0.0, year_ref=model_year, planning_step=25)
        TimeStepManager("ts", time_step_duration=1.0, operation_year_duration=8760)
        for country_code in country_codes:
            areas[country_code] = Area(country_code)


    for country_code in country_codes:

        capacities = _get_input_data_file(country_code, reference_year, "installed_capacities")
        hydro_inflows = _get_input_data_file(country_code, reference_year, "inflow")
        # --- Generation Technologies ---
        # Dispatchable
        for tech_name, plant_type in DISPATCHABLE_TECH_DICT.items():
            add_dispatchable_tech(
                area=areas[country_code],
                tech_name=tech_name,
                plant_type=plant_type,
                installed_capacities=capacities,
                reference_year=reference_year,
                variable_cost=dispatchable_tech_costs[tech_name],
                ramp_rate=dispatchable_ramp_rates[tech_name],
                fixed_cost=fixed_costs[tech_name],
                investment_cost=investment_costs[tech_name],
                lifetime=lifetimes[tech_name]
            )

        # Intermittent
        for tech_name, plant_type in INTERMITTENT_TECH_DICT.items():
            if plant_type not in capacities.columns:
                logger.info(f"Skipping {tech_name}: no installed capacity found for plant type '{plant_type}'.")
                return

            capacity = capacities[plant_type][0]
            add_intermittent_tech(
                area=areas[country_code],
                tech_name=tech_name,
                plant_type=plant_type,
                capacity=capacity,
                reference_year=reference_year,
                fixed_cost=fixed_costs[tech_name],
                investment_cost=investment_costs[tech_name],
                lifetime=lifetimes[tech_name]
            )

        # Hydrogen submodule:
        for country_code in current_installed_capacities:
            # ... existing area setup, add_load, add_renewables, etc. ...

            # --- NEW: add hydrogen if demand is provided ---
            h2_d = hydrogen_demand.get(country_code, 0.0)
            h2_c = hydrogen_capacities.get(country_code, 0.0)
            if h2_d > 0 and h2_c > 0:
                add_hydrogen(
                    areas[country_code],
                    h2_d,
                    h2_c,
                    fixed_costs,
                    investment_costs,
                    dispatchable_tech_costs,
                    lifetimes,
                )

        # Reserver Hydro
        add_reservoir_hydro(
            area=areas[country_code],
            installed_capacities=capacities,
            inflows=hydro_inflows,
            fixed_cost=fixed_costs['Reservoir_Hydro'],
            investment_cost=investment_costs['Reservoir_Hydro'],
            lifetime=lifetimes['Reservoir_Hydro'],
            country=country_code
        )

        # Pumped hydro storage
        add_pumped_hydro(
            areas[country_code],
            capacities,
            country_code,
            fixed_costs['Pumped_Hydro'],
            investment_costs['Pumped_Hydro']
        )

        # --- Balancing and feasibility components ---

        with energy_model.context():
            spil = Spillage(name="electricity_spillage", resource="electricity", max_capacity=0.)
            areas[country_code].add_component(spil)
            ls = LoadShedding(name="electricity_load_shedding", resource="electricity", cost=30_000.)
            areas[country_code].add_component(ls)

            spil = Spillage(name="reservoir_water_spillage", resource="reservoir_water", max_capacity=50.0e3)
            areas[country_code].add_component(spil)
            ls = LoadShedding(name="reservoir_water_load_shedding", resource="reservoir_water", max_capacity=0.0)
            areas[country_code].add_component(ls)
            net_import = NetImport(
                name="electricity_import",
                resource="electricity",
                max_yearly_energy_import=0.0,
                max_yearly_energy_export=0.0)
            areas[country_code].add_component(net_import)


    for countries, interconnection in interconnections.items():
        add_interconnections(
            energy_model,
            countries,
            interconnection['capacity'],
            interconnection['investment_cost'],
            reference_year
        )

    return energy_model


if __name__ == "__main__":

    logger.error("This script is intended to be run via Snakemake.")
    logger.info("Running in standalone mode for debugging with placeholder data.")
    # Example for standalone execution (requires dummy files)
    country, year = "FR", 2022
    model_year = 2025

    create_model(country, year, model_year)