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
    "Electrolyser": 10, # future projection
    "Hydrogen_storage": 20,
    "Hydrogen_power_plant": 30,
    "Biogas": 30
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
    """Add a fixed-capacity dispatchable thermal technology to ``area``.

    The plant is sized from ``installed_capacities[plant_type]`` (min = max, so the capacity is
    fixed, not an investment decision) and priced by ``variable_cost`` (its short-run marginal cost),
    so HiGHS dispatches the fleet in merit order. Hourly de-rating comes from the ENTSO-E-derived
    ``availability`` series for that plant type (defaults to 100 % if absent), and ramping is bounded
    by ``ramp_rate`` (fraction of capacity per hour). No-op if ``plant_type`` is absent.

    Args:
        area: pommes :class:`Area` to add the technology to.
        tech_name: component name (e.g. ``"FR_Nuclear"``).
        plant_type: ENTSO-E plant-type label used to look up capacity + availability.
        installed_capacities: one-row frame with a column per plant type (MW).
        variable_cost: short-run marginal cost (EUR/MWh).
        reference_year: year whose availability series to read.
        ramp_rate: max up/down ramp per hour as a fraction of capacity (default 1.0 = unconstrained).
        fixed_cost, investment_cost, lifetime: O&M (EUR/MW/yr), overnight CAPEX (EUR/MW), life (yr).
    """
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
    variable_cost: float = 0.0,
) -> None:
    """Add a fixed-capacity intermittent renewable (solar / wind) to ``area``.

    Output is ``capacity × capacity_factor`` each hour: the hourly capacity-factor series for
    ``plant_type`` (or ``tech_name``) is loaded for ``reference_year`` and passed as the technology's
    ``availability`` (zero fuel cost). For future PECD/ERAA years a weather scenario ``ws`` selects
    the climate year. The series is leap-trimmed/padded to 8760 h; capacity is fixed (min = max).

    Args:
        area: pommes :class:`Area` to add the technology to.
        tech_name: component name (e.g. ``"FR_Solar"``).
        plant_type: capacity-factor plant-type label (falls back to ``tech_name``).
        capacity: installed capacity (MW).
        reference_year: year whose capacity-factor series to read.
        ws: weather-scenario / climate-year selector (required for future-year CF data).
        fixed_cost, investment_cost, lifetime: O&M (EUR/MW/yr), overnight CAPEX (EUR/MW), life (yr).
    """



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
        # CRITICAL: pommes treats a NaN availability as 1.0 (full nameplate). The
        # observed-CF bucket series carry NaNs (e.g. BE Wind Offshore ~705 h) and
        # artefact values >1; leaving them unclipped injects free zero-cost
        # generation and suppresses the price. Force them to a valid [0,1] CF.
        .with_columns(pl.col("availability").fill_nan(0.0).fill_null(0.0).clip(0.0, 1.0))
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
            invest_cost_power=10.,
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
    """Add a pumped-hydro storage (PHS) unit to ``area``.

    Power (turbine) capacity comes from ``installed_capacities["Hydro Pumped Storage"]`` and the
    energy (reservoir) capacity from ERAA storage volumes (closed- + open-loop pumping). Modelled as
    a :class:`StorageTechnology` with an 80 % round-trip efficiency (``factor_out = 0.8``) and no
    standing losses. Both capacities are fixed (investment min = max). No-op if PHS capacity is absent.

    Args:
        area: pommes :class:`Area` to add the storage to.
        installed_capacities: one-row frame with a ``"Hydro Pumped Storage"`` column (MW).
        country: country code, used to look up ERAA reservoir energy (MWh).
        fixed_cost: fixed O&M on power capacity (EUR/MW/yr).
        investment_cost: overnight CAPEX on power capacity (EUR/MW).
        lifetime: technical life (years).
        eraa_year: ERAA vintage for the storage-energy lookup.
    """
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
        ntc_scale: float = 1.0,
):
    """Add price-based electricity imports/exports to every neighbour of ``country``.

    For each connected country in the NTC dataset, a satellite :class:`Area` is created with a
    :class:`NetImport` priced by the neighbour's hourly day-ahead prices (import and export legs),
    and an ``electric_line`` :class:`TransportTechnology` capped at the inter-country NTC (MW).
    Borders without a usable price series are skipped (not priced at a flat default). This
    represents the rest of the system as a price-taking border rather than an explicit grid.

    Args:
        area: the modelled country's pommes :class:`Area`.
        country: that country's code (drives the NTC + price lookups).
        reference_year: year whose NTC + day-ahead prices to read.
        ntc_scale: multiplier on every border's transfer capacity (interconnection
            market-design lever; ``0`` isolates the zone, ``1.5`` adds 50 % grid).
    """
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

        grid_capacity = float(grid_capacity or 0.0) * ntc_scale
        if grid_capacity <= 0.0:          # interconnection lever set this border to 0
            continue

        bzn = COUNTRY_BIDDING_ZONE_MAPPING.get(connected_country, [connected_country])[0]

        try:
            # Price the border by the NEIGHBOUR's own real day-ahead series (read the
            # neighbour's price file), not the modelled country's — each
            # ``day_ahead_prices_<cc>`` file holds only that country's bidding zone, so
            # passing ``country`` here always missed and fell back to the flat default.
            day_ahead_import_prices = get_electricity_import_prices(
                connected_country,
                reference_year,
                year_op,
                hours,
                bzn
            )
        except Exception as exc:  # noqa: BLE001 — no staged price file (e.g. phantom NTC zone)
            logger.info("add_imports: skipping %s border — no day-ahead prices (%s)",
                        connected_country, exc)
            continue
        # Skip borders with no usable price signal (empty, or all-null like LU)
        # rather than pricing them at a flat default — a fabricated cheap/dear border
        # would distort the focus zone's imports and scarcity hours.
        if (day_ahead_import_prices.is_empty()
                or day_ahead_import_prices["import_price"].null_count()
                   == day_ahead_import_prices.height):
            logger.info("add_imports: skipping %s border — price series empty/all-null",
                        connected_country)
            continue
        day_ahead_export_prices = day_ahead_import_prices.rename({"import_price": "export_price"})

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
    """Existing interconnection capacity (MW) between two countries from the NTC dataset.

    Reads both directions' net-transfer capacities for ``reference_year`` and returns the larger
    (0.0 if neither direction is available). Used by :func:`add_interconnections` when capacity is
    set to ``"use_existing"``.

    Args:
        countries: a 2-element list ``[a, b]`` of country codes.
        reference_year: year whose NTC data to read.

    Returns:
        The interconnection capacity in MW (``max`` of the two directions).
    """
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


def add_hydrogen(area, hydrogen_demand, hydrogen_capacity, fixed_costs, investment_costs, variable_costs, lifetimes):
    """
    Adds a hydrogen system configuration to a specified area, including an
    electrolyzer, hydrogen demand component, storage, and a hydrogen-based power
    plant.

    This function configures a comprehensive hydrogen-based energy system in the
    provided area's energy model. It accounts for hydrogen production via
    electrolysis, integrates storage systems, and establishes hydrogen demand and
    generation capabilities. No hydrogen import or export is allowed in this
    setup.

    Args:
        area: The energy model area to which the hydrogen system is added.
        hydrogen_demand: The yearly hydrogen demand (in consistent energy units).
        hydrogen_capacity: Maximum power capacity for the hydrogen power plant.
        fixed_costs: Dictionary containing fixed cost values for each technology.
        investment_costs: Dictionary with investment costs for each technology,
            including storage power and energy costs.
        variable_costs: Dictionary with variable operation costs for
            relevant technologies.
        lifetimes: Dictionary specifying lifespans of the included technologies in
            years.
    """

    energy_model = area.model
    year_op = energy_model.year_ops[0]
    hours = energy_model.hours

    demand_df = pl.DataFrame(
        {
            "demand": [hydrogen_demand / 8760.] * 8760,
            "hour": hours,
            "year_op": [year_op] * 8760
        }
    )

    with energy_model.context():
        electrolyzer = ConversionTechnology(
            name="electrolysis",
            factor={
                "hydrogen": 1.,
                "electricity": -1.85
            },
            emission_factor=0.,
            fixed_cost=fixed_costs["Electrolyser"],
            variable_cost=0.,
            life_span=lifetimes["Electrolyser"],
            must_run=0.,
            availability=1.0,  # Always available
            invest_cost=investment_costs["Electrolyser"],
            early_decommissioning=True
        )
        area.add_component(electrolyzer)

        demand = Demand(
            name="hydrogen_demand",
            resource="hydrogen",
            demand=demand_df,
        )
        area.add_component(demand)

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
        hydrogen_import = NetImport(
            name="hydrogen_import",
            resource="hydrogen",
            max_yearly_energy_import=0.,  # No import allowed
            max_yearly_energy_export=0.  # No export allowed
        )
        area.add_component(hydrogen_import)
        hydrogen_storage = StorageTechnology(
            name="h2_tank",
            factor_in={"hydrogen": -1.02},
            factor_out={"hydrogen": 1.},
            factor_keep={"hydrogen": 0.},
            invest_cost_energy=investment_costs["Hydrogen_storage_power"],
            invest_cost_power=investment_costs["Hydrogen_storage_energy"],
            fixed_cost_power=fixed_costs["Hydrogen_storage_power"],
            dissipation=0.,
            life_span=lifetimes["Hydrogen_storage"],
        )
        area.add_component(hydrogen_storage)

        tech = ConversionTechnology(
            name="hydrogen_power_plant",
            factor={
                "electricity": 1.0,
                "hydrogen": -2.85
            },
            availability=1.,
            must_run=0.,
            variable_cost=variable_costs["Hydrogen_power_plant"],
            fixed_cost=fixed_costs["Hydrogen_power_plant"],
            invest_cost=investment_costs["Hydrogen_power_plant"],
            life_span=lifetimes["Hydrogen_power_plant"],
            power_capacity_max=hydrogen_capacity,
            power_capacity_min=hydrogen_capacity,
            early_decommissioning=True,
        )
        area.add_component(tech)


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
        add_hydrogen(
            areas[country_code],
            hydrogen_d,
            hydrogen_capacities[country_code],
            fixed_costs,
            investment_costs,
            dispatchable_tech_costs,
            lifetimes
        )

    for countries, interconnection in interconnections.items():
        add_interconnections(
            energy_model,
            countries,
            interconnection['capacity'],
            interconnection['investment_cost'],
            reference_year
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