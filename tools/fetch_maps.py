"""Fetch compact, official Census state outlines without GIS dependencies.

The House view intentionally uses a seat cartogram: 119th-Congress polygons
are not an authoritative representation of 2026 districts after redistricting.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import struct
import urllib.request
import zipfile

SOURCE = "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_state_20m.zip"
ROOT = Path(__file__).resolve().parents[1]


def dbf_rows(data: bytes) -> list[dict]:
    count = struct.unpack_from("<I", data, 4)[0]
    header, record_length = struct.unpack_from("<HH", data, 8)
    fields = []
    offset = 32
    while data[offset] != 13:
        descriptor = data[offset:offset + 32]
        fields.append((descriptor[:11].split(b"\0")[0].decode("ascii"), descriptor[16]))
        offset += 32
    rows = []
    for index in range(count):
        record = data[header + index * record_length:header + (index + 1) * record_length]
        offset, row = 1, {}
        for name, length in fields:
            row[name] = record[offset:offset + length].decode("utf-8", errors="replace").strip()
            offset += length
        rows.append(row)
    return rows


def polygon_records(data: bytes) -> list[list]:
    offset, records = 100, []
    while offset < len(data):
        _, words = struct.unpack_from(">II", data, offset)
        record = data[offset + 8:offset + 8 + words * 2]
        offset += 8 + words * 2
        kind = struct.unpack_from("<I", record)[0]
        if kind == 0:
            records.append([])
            continue
        if kind != 5:
            raise ValueError(f"Expected Polygon, found shape type {kind}")
        part_count, point_count = struct.unpack_from("<II", record, 36)
        parts = list(struct.unpack_from(f"<{part_count}I", record, 44)) + [point_count]
        points_offset = 44 + part_count * 4
        points = [list(struct.unpack_from("<dd", record, points_offset + index * 16)) for index in range(point_count)]
        records.append([[[round(x, 4), round(y, 4)] for x, y in points[start:end]] for start, end in zip(parts, parts[1:])])
    return records


def geojson_polygons(rings: list) -> list:
    """Group clockwise shapefile shells and counterclockwise interior rings."""
    def signed_area(ring):
        return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:])) / 2

    def contains(ring, point):
        x, y = point
        inside = False
        for a, b in zip(ring, ring[1:]):
            if (a[1] > y) != (b[1] > y):
                crossing = a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
                if x < crossing:
                    inside = not inside
        return inside

    polygons = [[ring] for ring in rings if signed_area(ring) < 0]
    for ring in rings:
        if signed_area(ring) < 0:
            continue
        shell = next((polygon for polygon in polygons if contains(polygon[0], ring[0])), None)
        if shell is None:
            polygons.append([ring])
        else:
            shell.append(ring)
    return polygons


def main() -> None:
    with urllib.request.urlopen(SOURCE, timeout=40) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    basename = next(name[:-4] for name in archive.namelist() if name.endswith(".shp"))
    rows = dbf_rows(archive.read(basename + ".dbf"))
    shapes = polygon_records(archive.read(basename + ".shp"))
    features = []
    for properties, coordinates in zip(rows, shapes):
        if properties["STUSPS"] in {"DC", "AS", "GU", "MP", "PR", "VI"}:
            continue
        # Rings render with SVG's even-odd rule; retaining every ring preserves
        # state islands and lakes without topology-changing simplification.
        features.append({"type": "Feature", "properties": {"state": properties["STUSPS"], "name": properties["NAME"]}, "geometry": {"type": "MultiPolygon", "coordinates": geojson_polygons(coordinates)}})
    assert len(features) == 50, f"Expected 50 states, received {len(features)}"
    destination = ROOT / "web" / "maps" / "us-states.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({"type": "FeatureCollection", "source": SOURCE, "vintage": "2024", "features": features}, separators=(",", ":")), encoding="utf-8")
    print(f"Saved {len(features)} Census state outlines to {destination} ({destination.stat().st_size:,} bytes).")


if __name__ == "__main__":
    main()
