import polars as pl
import pandas as pd
import matplotlib.pyplot as plt
from pommes_craft.components import *
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd


gen_palette = {
    'Nuclear': '#A0A0A0',
    'RoR_Pondage': '#1E90FF',
    'Solar': '#FDB813',
    'Wind_Onshore': '#66B2FF',
    'Wind_Offshore': '#0077BE',
    'lake_hydro_plant': '#0000CD',
    'Pumped_Hydro_Discharging': '#9370DB',
    'Battery_Discharging': '#9B59B6',
    'Coal': '#4B4B4B',
    'Gas': '#E97451',
    'Oil': '#8B4513',
    'Biomass': '#2E8B57',
    'Waste': '#6B8E23',
    'Other': '#CCCCCC',
    'Imports': '#15616d',
    "Load_Shedding": '#d90429',
    'Biogas': '#2E8B57',
    "hydrogen_power_plant": "#1BA3C6"
}
dem_palette = {
    'Demand': '#2a9d8f',
    'Pumped_Hydro_Charging': '#CBA0F5',
    'Battery_Charging': '#D7BDE2',
    'Exports': '#F08080',
    "Flexible_Demand": "#0f4c5c",
    "Electrolysis_Demand": "#1BA3C6"
}

def extract_power_results(energy_model, country_code):

    area_fr = energy_model.areas[country_code]
    # retrieving power produced by generators
    powers = (
        energy_model.get_results("operation", "power", [ConversionTechnology])
        .pivot(on='name', values='value')
        .filter(pl.col('area') == country_code)
        .drop(['area', 'year_op', 'hour', "lake_hydro_inflow", "component_class"])
        .to_pandas()
        .set_index('datetime')
    )

    # retrieving power stored and released in pumped hydro storages
    hydro_storage_net_generation = (
        area_fr.components['pumped_hydro_storage']
        .results['operation']['net_generation']
        .filter(pl.col('resource') == 'electricity')
    )
    hydro_storage_power_out = hydro_storage_net_generation.with_columns(
        pl.when(pl.col('value') > 0)
        .then(pl.col('value'))
        .otherwise(0)
        .alias('value')
    )
    hydro_storage_power_in = hydro_storage_net_generation.with_columns(
        pl.when(pl.col('value') < 0)
        .then(-pl.col('value'))
        .otherwise(0)
        .alias('value')
    )

    # retrieving imported and exported powers
    interco_powers = energy_model.get_results("operation", "power", [TransportTechnology])
    imports_to_fr = (
        interco_powers.filter(pl.col('link').str.ends_with(country_code))
        .group_by(pl.col('datetime'), maintain_order=True)
        .agg(pl.col('value').sum())
    )
    exports_from_fr = (
        interco_powers.filter(
            (pl.col('link').str.ends_with(country_code) == False) & 
            (pl.col('link').str.contains(country_code, literal=True))
        )
        .group_by(pl.col('datetime'), maintain_order=True)
        .agg(pl.col('value').sum())
    )
    demand = area_fr.components['electricity_demand'].demand['demand'].to_numpy()
    load_shedding = (
        area_fr
        .components['electricity_load_shedding']
        .results['operation']['power']['value'].to_numpy()
    )
    powers['Pumped_Hydro_Discharging'] = hydro_storage_power_out['value'].to_numpy()
    powers['Pumped_Hydro_Charging'] = hydro_storage_power_in['value'].to_numpy()
    powers['Imports'] = imports_to_fr['value'].to_numpy()
    powers['Exports'] = exports_from_fr['value'].to_numpy()
    powers['Demand'] = demand
    powers['Load_Shedding'] = load_shedding

    if "Battery_Storage" in area_fr.components.keys():
        # retrieving power stored and released in batteries
        battery_net_generation = (
            area_fr.components['Battery_Storage']
            .results['operation']['net_generation']
            .filter((pl.col('resource') == 'electricity'))
        )
        battery_power_in = battery_net_generation.with_columns(
            pl.when(pl.col('value') > 0)
            .then(pl.col('value'))
            .otherwise(0)
            .alias('value')
        )
        battery_power_out = battery_net_generation.with_columns(
            pl.when(pl.col('value') < 0)
            .then(-pl.col('value'))
            .otherwise(0)
            .alias('value')
        )
        powers['Battery_Charging'] = battery_power_in['value'].to_numpy()
        powers['Battery_Discharging'] = battery_power_out['value'].to_numpy()
    else:
        powers['Battery_Charging'] = 0.
        powers['Battery_Discharging'] = 0.

    if "electrolysis" in area_fr.components.keys():
        electrolysis = area_fr.components['electrolysis'].results['operation']['net_generation']
        electrolysis = - electrolysis.filter(pl.col('resource') == "electricity")['value'].to_numpy()
        powers['Electrolysis_Demand'] = electrolysis
    else:
        powers['Electrolysis_Demand'] = 0.


    flex = energy_model.get_results('operation', 'demand', [FlexibleDemand])

    if flex.is_empty():
        powers['Flexible_Demand'] = 0.
    else:
        powers['Flexible_Demand'] = flex['value'].to_numpy()

    return powers

def plot_power_balance(power_df, resample_step="1h", week=None, day=None, max_y=None):


    fig, ax = plt.subplots(1, 2,figsize=(15, 6))

    if week is not None:
        plot_mask = power_df.index.isocalendar().week == week
    elif day is not None:
        plot_mask = power_df.index.day_of_year == day
    else:
        plot_mask = power_df.index

    powers_to_p = power_df.loc[plot_mask].resample(resample_step).mean()

    gen_cols = list(gen_palette.keys())
    powers_to_p[gen_cols].plot.area(ax=ax[0], color=gen_palette, linewidth=0)

    dem_cols = list(dem_palette.keys())
    powers_to_p[dem_cols].plot.area(ax=ax[1], color=dem_palette, linewidth=0)
    ax[0].legend(
        loc='upper center',
        bbox_to_anchor=(0.5, -0.15),
        ncol=3,
        frameon=False,
        fontsize=9
    )
    ax[1].legend(
        loc='upper center',
        bbox_to_anchor=(0.5, -0.15),
        ncol=3,
        frameon=False,
        fontsize=9
    )

    ax[0].set_ylabel('Power in MW')
    ax[0].set_xlabel('')
    ax[1].set_xlabel('')
    if max_y is not None:
        ax[0].set_ylim(0, max_y)
        ax[1].set_ylim(0, max_y)
    plt.show()



def plot_power_balance_plotly(power_df, resample_step="1h", month=None, week=None, day=None, max_y=None):

    if month is not None:
        plot_mask = power_df.index.month == month
    elif week is not None:
        plot_mask = power_df.index.isocalendar().week == week
    elif day is not None:
        plot_mask = power_df.index.day_of_year == day
    else:
        plot_mask = power_df.index

    powers_to_p = power_df.loc[plot_mask].resample(resample_step).mean()

    # Two-row subplot with shared x-axis
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.2,
        subplot_titles=("Generation", "Demand")
    )
    gen_palette_selection = {k: v for k, v in gen_palette.items() if k in powers_to_p.columns}
    dem_palette_selection = {k: v for k, v in dem_palette.items() if k in powers_to_p.columns}
    # --- Generation traces (hide legend) ---
    for gen in gen_palette_selection.keys():
        fig.add_trace(
            go.Scatter(
                x=powers_to_p.index,
                y=powers_to_p[gen],
                stackgroup='gen',
                name=gen,
                line=dict(width=0),
                fillcolor=gen_palette[gen],
                showlegend=False
            ),
            row=1, col=1
        )

    # --- Demand traces (hide legend) ---
    for dem in dem_palette_selection.keys():
        fig.add_trace(
            go.Scatter(
                x=powers_to_p.index,
                y=powers_to_p[dem],
                stackgroup='dem',
                name=dem,
                line=dict(width=0),
                fillcolor=dem_palette[dem],
                showlegend=False
            ),
            row=2, col=1
        )

    # Axis labels
    fig.update_yaxes(title_text="Power in MW", row=1, col=1, range=[0, max_y] if max_y else None)
    fig.update_yaxes(title_text="Power in MW", row=2, col=1, range=[0, max_y] if max_y else None)
    fig.update_xaxes(title_text="Time", row=2, col=1)

    # Range slider and selector
    fig.update_layout(
        height=800,
        width=1000,
        margin=dict(r=200),  # <-- increase right margin for legends
        xaxis=dict(
            rangeslider=dict(visible=True, thickness=0.1),
        )
    )

    # --- Custom vertical legends using annotations ---
    def add_vertical_legend(y_base, items, colors):
        for i, (name, color) in enumerate(zip(items, colors)):
            fig.add_shape(
                type="rect",
                xref="paper", yref="paper",
                x0=1.02, x1=1.04,  # move inside the right margin
                y0=y_base + i*0.03, y1=y_base + i*0.03 + 0.02,
                fillcolor=color,
                line=dict(width=0)
            )
            fig.add_annotation(
                x=1.055, y=y_base + i*0.03 + 0.01,
                xref="paper", yref="paper",
                text=name,
                showarrow=False,
                xanchor="left",
                yanchor="middle",
                font=dict(size=10)
            )

    # Generation legend (right of first subplot)
    add_vertical_legend(y_base=0.5,
                        items=list(gen_palette_selection.keys()),
                        colors=list(gen_palette_selection.values()))
    # Demand legend (right of second subplot)
    add_vertical_legend(y_base=0.1,
                        items=list(dem_palette_selection.keys()),
                        colors=list(dem_palette_selection.values()))

    fig.show()






def get_summary_df(model_dict, country_code, cmap='Greys'):

    if isinstance(country_code, str):
        country_codes = [country_code]
    else:
        country_codes = country_code
    dfs = []
    for cc in country_codes:
        for name, energy_model in model_dict.items():
            powers_df = extract_power_results(energy_model, cc)
            gen_palette_selection = {k: v for k, v in gen_palette.items() if k in powers_df.columns}
            dem_palette_selection = {k: v for k, v in dem_palette.items() if k in powers_df.columns}
            cols = list(gen_palette_selection.keys()) + list(dem_palette_selection.keys())
            index_colors = (
                    {f'{k} in TWh': f"{v}80" for k,v in gen_palette_selection.items()}
                    | {f'{k} in TWh': f"{v}80" for k,v in dem_palette_selection.items()}
                    | {
                        "Costs in B€": "#fca31180",
                        'Peak Demand in GW': "#2a9d8f80",
                        'Peak Load Shedding in GW': "#FF000080"
                    }
            )
            comp_df = powers_df[cols].sum().to_frame() * 1.e-6
            if isinstance(country_code, str):
                col_name = name
            else:
                col_name = f"{name} {cc}"
            comp_df.columns = [col_name]
            comp_df.index = [f"{c} in TWh" for c in comp_df.index]
            peak_demand = powers_df[['Demand', "Flexible_Demand", "Electrolysis_Demand"]].sum(axis=1).max() * 1.e-3
            comp_df.loc['Peak Demand in GW', col_name] = peak_demand
            peak_load_shedding = powers_df["Load_Shedding"].max() * 1.e-3
            comp_df.loc['Peak Load Shedding in GW', col_name] = peak_load_shedding
            costs = energy_model.get_results('operation', "costs").filter(pl.col("area") == cc)['value'].sum() * 1.e-9
            comp_df.loc['Operation Costs in B€', col_name] = costs
            dfs.append(comp_df)


    df = pd.concat(dfs, axis=1).round(2)

    separators = [f"{list(gen_palette)[-1]} in TWh",
                  f"{list(dem_palette)[-1]} in TWh",
                  'Peak Load Shedding in GW',
                  ]

    def bold_row_bottom_border(styler, indices_to_style):
        index_map = {label: i + 1 for i, label in enumerate(styler.data.index)}
        styles = []

        # Corrected Property Format: (property_name, property_value)
        property_tuple = ('border-bottom', '3px solid #14213d !important;')

        for index_label in indices_to_style:
            if index_label in index_map:
                row_number = index_map[index_label]

                # Selector targets ALL cells (*, i.e., both <th> and <td>)
                # within the Nth table row (tr:nth-child(N)).
                selector = f'tr:nth-child({row_number}) *'

                styles.append({
                    'selector': selector,
                    # Pass the property tuple inside a list
                    'props': [property_tuple]
                })

        return styles


    df_styled = (
        df.style.apply_index(
            lambda idx: [f"background-color: {index_colors.get(i, '')}" for i in idx],
            axis=0
        ).format(precision=2)
        .set_table_styles(
            bold_row_bottom_border(df.style, separators)
        )
    )

    if len(dfs) > 1:
        df_styled = df_styled.background_gradient(cmap=cmap, axis=1)

    return df, df_styled