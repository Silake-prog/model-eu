"""OpenStreetMap power-infrastructure + industry fetchers (ODbL — attribute OpenStreetMap contributors).

All modules share :mod:`.overpass`, a cache-first, rate-limit-friendly Overpass client, and aggregate
features to NUTS regions by point-in-polygon:

- :mod:`.generation`        ``power=plant``      -> installed capacity by technology (the *sources*).
- :mod:`.electricity`       ``power=substation`` (transmission backbone) + ``power=converter`` (HVDC).
- :mod:`.electricity_lines` ``power=line``/``cable`` -> circuit-km by voltage class (the grid *edges*).
- :mod:`.industry`          ``landuse=industrial`` + ``man_made=works`` -> sites/area (a *demand* proxy).

Together they give an OSM-derived picture of electricity supply, transmission, and industrial demand,
feeding ``pecd.zone_capacities`` weighting and the regional grid model's per-region fleet.

Note: country-scale ``out geom`` queries (full line/industrial-polygon geometry) can time out / 504 on
the busy public mirrors -- use a Geofabrik extract for those, or the light count modes for smaller areas.
"""
