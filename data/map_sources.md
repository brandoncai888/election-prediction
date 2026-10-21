# Map provenance

The Senate map uses 2024 US Census Bureau cartographic state boundaries at
1:20,000,000 scale. The source archive is 187,671 bytes:

https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_state_20m.zip

`python tools/fetch_maps.py` regenerates `web/maps/us-states.json` using Python's
standard library. Coordinates are rounded to four decimal places. All fifty
states are retained. Alaska and Hawaii are displayed in separate insets and are
not shown at the same scale as the contiguous states.

The House map is a state-grouped seat cartogram, with one selectable tile per
district. Tiles are arranged approximately geographically by state. Their area
and position do not represent district boundaries. This avoids labeling 119th
Congress boundaries as 2026 boundaries after subsequent redistricting.

Map colors represent experimental two-party vote-share projections. They do
not represent win probabilities. No-election Senate states are gray.
