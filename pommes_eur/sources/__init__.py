"""pommes_eur.sources — external data acquisition (fetch → process → inputs).

- ``fetch``          : download & cache the CLEVER workbook + ENSPRESO biomass.
- ``process``        : parse the CLEVER Excel workbook into structured CSVs.
- ``demand``         : build hourly electricity demand (CLEVER CSVs + DemandForge).
- ``data_fetchers``  : generic live commodity/weather/price fetchers with caching.
"""
