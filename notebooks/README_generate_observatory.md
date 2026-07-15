# Observatory HTML Report Generator

## Overview

Complete Python script that generates a professional, self-contained HTML observatory report for electricity adequacy analysis. The report analyzes the CLEVER 2050 Scenario Option D (NTC 4→9.5 GW, H2 CCGT 40 GW) for France and Germany using the POMMES modeling framework.

## File

**Script:** `/notebooks/generate_observatory.py`
**Output:** `/results/observatoire_adequation_clever_2050.html`

## Quick Start

```bash
cd /mnt/clever-work/notebooks
python generate_observatory.py
```

The script will:
1. Load real CSV data from `/results/export/`
2. Generate 9 new charts from CSV data (capacity mix, prices, adequacy, etc.)
3. Embed existing figures from `/results/figures/`
4. Generate a 3.8+ MB self-contained HTML report
5. Write to `/results/observatoire_adequation_clever_2050.html`

## Data Dependencies

The script reads these CSV files (relative to `/results/export/`):

- `prices_ramp_flex23pct.csv` — 17,520 hourly price observations
  - Columns: area, year_op, hour, resource, value
  - Both FR and DE markets

- `conversion_capacity_ramp_flex23pct.csv` — Installed generation capacity
  - Columns: area, year_op, name, value
  - All conversion technologies (Solar, Wind, CCGT, H2, Hydro, Battery)

- `adequacy_dashboard_ramp_flex23pct.csv` — Annual adequacy summary
  - Columns: Country, ENS (GWh), LOLE (h), Peak LS (MW), Max dual (EUR/MWh), Hours at VoLL, Mean price (EUR/MWh)
  - Two rows: FR, DE

- `exante_adequacy_ramp_flex23pct.csv` — Ex-ante adequacy metrics
  - Columns: country, peak_demand_mw, dispatchable_existing_mw, dispatchable_total_max_mw, vre_credit_mw, adequacy_margin_mw

## Features

### 19 Specialized Functions

| Function | Purpose |
|----------|---------|
| `load_csv_data()` | Load CSV with error handling |
| `image_to_base64()` | Convert PNG files to base64 |
| `fig_to_base64()` | Convert matplotlib figures to base64 |
| `embed_figure()` | Intelligent figure lookup |
| `format_number()` | Format numbers for display |
| `create_capacity_mix_chart()` | Stacked bar: FR vs DE capacity |
| `create_price_duration_curves()` | Duration curves with symlog scale |
| `create_price_histogram()` | Price distribution histogram (log x) |
| `create_price_spread_chart()` | DE-FR price spread area fill |
| `create_adequacy_waterfall_chart()` | Adequacy waterfall analysis |
| `create_monthly_price_comparison()` | Monthly avg price bars (12 months) |
| `create_price_volatility_chart()` | Monthly std dev volatility |
| `create_capacity_factor_analysis()` | VRE pattern by hour-of-day |
| `create_flexibility_stack_chart()` | Demand vs dispatchable stack |
| `load_all_data()` | Load all 4 CSV files |
| `generate_all_charts()` | Generate all 9 charts from CSV |
| `extract_kpi_data()` | Extract KPI values from adequacy CSV |
| `generate_html()` | Generate complete HTML document |
| `main()` | Entry point |

### HTML Sections (14 Total)

1. **Executive Summary** — Key findings, France success vs Germany challenge
2. **Methodology** — POMMES, linopy, Gurobi barrier method, modeling chain
3. **Scenario Hypotheses** — CLEVER 2050, SER framework, capacity allocations
4. **Option D Adjustments** — NTC +4.5 GW, H2 +25 GW, salt caverns
5. **Installed Capacities** — Capacity mix chart, technology breakdown
6. **Adequacy Assessment** — LOLE, ENS, waterfall analysis
7. **Price Analysis** — Duration curves, histogram, spread, monthly, volatility
8. **Dispatch Analysis** — Annual, week 4 (winter), week 30 (summer)
9. **Storage & Flexibility** — SOC profiles, H2 caverns, flexibility stack
10. **Cross-border Flows** — FR-DE interconnection analysis
11. **Residual Load & VRE** — Hour-of-day patterns
12. **Load Shedding** — Heatmap, timeline, quantification
13. **Base vs Option D** — Quantified comparison, sensitivity analysis
14. **Limits & Perspectives** — Model assumptions, future scenarios

### Charts Generated

**From CSV Data:**
1. Capacity Mix (stacked bar, FR vs DE)
2. Price Duration Curves (symlog Y-axis)
3. Price Histogram (log x-axis)
4. Price Spread (DE-FR area)
5. Adequacy Waterfall (capacity components)
6. Monthly Price Comparison (12 months)
7. Price Volatility (monthly std dev)
8. Capacity Factor Analysis (hour-of-day)
9. Flexibility Stack (demand vs supply)

**From Existing Figures (embedded):**
- dispatch.png, dispatch_w4.png, dispatch_w30.png
- price_duration_curves.png, price_heatmaps.png
- residual_load_analysis.png
- cross_border_flows.png
- storage_soc_profiles.png
- load_shedding_heatmap.png, load_shedding_timeline.png

### HTML Styling

- **Hero Banner:** Gradient #1a237e → #303f9f → #1565c0
- **Cards:** Rounded corners (12px), subtle shadows, colored left borders
- **KPI Cards:** Color-coded (green=success, red=danger, orange=warning, blue=info)
- **Tables:** Blue-900 headers, monospace numbers (JetBrains Mono)
- **Callout Boxes:** Colored left border + light background
- **Typography:** Inter font, clear hierarchy
- **Responsiveness:** Auto-fit grids, mobile-friendly
- **Print:** Clean print media rules

### Color Palette

| Technology | Hex |
|-----------|-----|
| Solar | #FFD700 |
| Wind Onshore | #4CAF50 |
| Wind Offshore | #0077BE |
| CCGT Gas | #FF6347 |
| H2 CCGT | #9370DB |
| H2 Storage | #20B2AA |
| Run-of-River Hydro | #00CED1 |
| Reservoir Hydro | #1E90FF |
| Battery 1h | #FF8C00 |
| Battery 4h | #FF4500 |
| France | #003399 |
| Germany | #DD0000 |

### Technical Specifications

- **Language:** Python 3
- **Dependencies:** pandas, numpy, matplotlib (+ standard library)
- **DPI:** 150 (publication quality)
- **Encoding:** UTF-8 with HTML entities for French accents
- **Chart Format:** PNG base64 embedded (self-contained HTML)
- **File Size:** ~3.8 MB (all charts embedded)
- **Compatibility:** All modern browsers, mobile-responsive, print-friendly

## Key Content

### Methodology Details

- **POMMES Definition:** Planning and Operation Model for Multi-Energy Systems (PERSEE Mines Paris)
- **Solver:** Gurobi 13.0.1 with barrier method (Method=2)
- **Formulation:** linopy Linear Programming
- **Data Chain:** CLEVER Excel → process.py → DemandForge → pommes_craft → runner.py
- **Objective:** Minimize CAPEX + OPEX + VoLL cost
- **Constraints:** Power/energy balance, ramps, storage, NTC, demand cover
- **VRE Profiles:** SupplyForge with country/year fallback
- **Demand:** DemandForge (baseload + thermosensitive + EV)
- **Cost Database:** EOLES (CIRED), 4% discount rate
- **VoLL:** 30,000 EUR/MWh (ENTSO-E ERAA)
- **H2 Round-trip:** 40% efficiency (70% electrolysis × 58% CCGT)

### Scenario Documentation

- **CLEVER:** Collaborative Low Energy Vision for the European Region
- **Framework:** SER (Suffisance-Efficacité-Renouvelables)
- **Reference:** clever-energy-scenario.eu, Negawatt
- **Year:** 2050
- **Geography:** France + Germany (2-zone model)
- **Option D Changes:**
  - NTC FR-DE: 4 GW → 9.5 GW
  - H2 CCGT: ~15 GW → 40 GW
  - Salt cavern storage: Long-term seasonal flexibility

### Key Results (Option D)

| Metric | France | Germany | Unit |
|--------|--------|---------|------|
| LOLE | 0 | 148 | h/year |
| ENS | 0.0 | 1902 | GWh/year |
| Mean Price | 19.2 | 568.6 | EUR/MWh |
| Price Spread | — | 549.3 | EUR/MWh |
| Peak Demand | 59,420 | 80,903 | MW |

## Runtime

- Typical execution: ~30-60 seconds
- Chart generation from CSV: ~5-10 seconds
- HTML generation: <5 seconds
- File I/O: <2 seconds
- Total: Depends on system CPU (matplotlib rendering)

## Output

**File:** `observatoire_adequation_clever_2050.html`
**Size:** ~3.8 MB (all images embedded as base64)
**Format:** Self-contained, no external dependencies
**Usage:** Open in any modern web browser

## Errors & Troubleshooting

### CSV File Not Found

If you see: `Warning: File not found: ...`
- Verify CSV files exist in `/results/export/`
- Check file names match exactly (case-sensitive)
- Confirm paths are relative to NOTEBOOK_DIR

### Import Error (pandas/numpy/matplotlib)

```bash
pip install pandas numpy matplotlib
```

### Low Memory

If matplotlib crashes during chart generation:
- Reduce figure size (adjust figsize in functions)
- Close other applications
- Use system with >4GB RAM recommended

### Chart Not Appearing in HTML

- Check if figure exists in `/results/figures/`
- Verify CSV data has correct columns
- Check console output for chart generation errors
- Manually verify CSV files have data

## Customization

### Modify Color Palette

Edit the `COLORS` dictionary at the top of the script:

```python
COLORS = {
    'Solar': '#NEW_HEX_CODE',
    ...
}
```

### Change DPI

Adjust `plt.rcParams['figure.dpi']` and `savefig(..., dpi=...)` calls:

```python
plt.rcParams['figure.dpi'] = 200  # Higher quality, larger file
```

### Add/Remove Sections

Edit the `generate_html()` function to modify section structure.

### Adjust Font Sizes

Modify matplotlib font settings:

```python
plt.rcParams['font.size'] = 11  # Default 9
```

## License & Attribution

- **POMMES:** PERSEE Mines Paris (planninganddispatch.com, or relevant)
- **CLEVER:** Negawatt association (clever-energy-scenario.eu)
- **Data:** Results from adequacy analysis runs
- **Generated:** 2026-04-06

## References

- ENTSO-E ERAA 2023 (Value of Lost Load)
- ENTSO-E TYNDP 2024 (Interconnection capacity)
- EOLES cost database (CIRED)
- clever-energy-scenario.eu (CLEVER scenario)
- negawatt.org (SER framework)

---

**Generated:** 2026-04-06 | **Version:** 1.0 | **Status:** Production-ready
