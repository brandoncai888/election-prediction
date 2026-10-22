"""Export the dashboard as static files for GitHub Pages or any static host.

Every API response the dashboard reads is written to JSON next to a copy of
web/. The exported index.html carries a marker meta tag that switches app.js to
read those files instead of /api/. Race files are compacted: the shared news
list is written once, and cohort rows duplicated across keys are rebuilt in the
browser. The manual scenario endpoint has no static equivalent.
"""
import csv
import io
import json
import re
import shutil
from pathlib import Path

from .forecasts import forecast_overview, forecast_table, TABLE_COLUMNS
from .kalshi import strategy
from .server import WEB, AS_OF, summary, groups_payload, races_payload, race_detail, race_news

OFFICES = ('house', 'senate', 'governor')
MANIFEST = 'manifest.json'
SAFE_ID = re.compile(r'^[A-Za-z0-9_.-]+$')


def _round(value):
    """Six decimal places: shares stay finer than displayed, counts stay exact."""
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {k: _round(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_round(v) for v in value]
    return value


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_round(payload), allow_nan=False, separators=(',', ':')), encoding='utf-8')
    return path.stat().st_size


def _compact_detail(detail, shared_ids):
    """Drop content that app.js reconstructs (see expandDetail in web/app.js)."""
    detail = dict(detail)
    projection = detail['projection'] = dict(detail['projection'])
    groups = projection.get('groups') or []
    if detail.get('forecasts') == groups:
        detail.pop('forecasts')
    demographics = detail.get('demographics') or []
    if groups and len(demographics) == len(groups):
        extra = {k: v for k, v in demographics[0].items() if k not in groups[0]}
        if all(row == {**g, **extra} for row, g in zip(demographics, groups)):
            detail.pop('demographics')
            detail['demographics_from_groups'] = extra
    if [item['news_id'] for item in detail.get('news', [])] == shared_ids:
        detail.pop('news')
        detail['news_shared'] = True
    feature = projection.get('news_feature')
    if isinstance(feature, dict) and 'article_ids' in feature:
        # Only the count is displayed; the full ID list is ~40 KB per race.
        projection['news_feature'] = {k: v for k, v in feature.items() if k != 'article_ids'}
    return detail


def export_site(conn, out, as_of=AS_OF):
    out = Path(out)
    data = out / 'data'
    if data.exists():
        # Only clear a folder this command wrote, never an unrelated directory.
        if not (data / MANIFEST).exists():
            raise ValueError(f'{data} exists but was not written by export-site; choose another --out')
        shutil.rmtree(data)
    if not conn.execute('SELECT 1 FROM race_forecasts WHERE as_of=? LIMIT 1', (as_of,)).fetchone():
        raise ValueError(f'No forecasts saved for {as_of}. Run python main.py predict --as-of {as_of}')
    out.mkdir(parents=True, exist_ok=True)
    sizes = {}
    for name in ('app.js', 'styles.css', 'maps/us-states.json'):
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(WEB / name, target)
    page = (WEB / 'index.html').read_text(encoding='utf-8')
    page = page.replace('<head>', '<head>\n  <meta name="election-static" content="true">', 1)
    page = page.replace('href="/api/export/forecasts"', 'href="data/forecast_table_2026.csv"')
    (out / 'index.html').write_text(page, encoding='utf-8')
    # GitHub Pages runs Jekyll by default; static files need no processing.
    (out / '.nojekyll').write_text('', encoding='utf-8')

    sizes['summary'] = _write_json(data / 'summary.json', summary(conn, as_of))
    sizes['groups'] = _write_json(data / 'groups.json', groups_payload())
    races = races_payload(conn)['races']
    sizes['races'] = _write_json(data / 'races.json', {'races': races})
    for office in OFFICES:
        sizes['predictions-' + office] = _write_json(data / f'predictions-{office}.json', forecast_overview(conn, as_of, office))
    sizes['strategy'] = _write_json(data / 'strategy.json', strategy(conn, as_of))
    # Races without race-specific articles all show this list; store it once.
    shared = race_news(conn, None, as_of)
    shared_ids = [item['news_id'] for item in shared]
    sizes['news'] = _write_json(data / 'news.json', {'news': shared})
    race_bytes = 0
    for race in races:
        if not SAFE_ID.match(race['race_id']):
            raise ValueError(f"Race id {race['race_id']!r} is not a safe file name")
        race_bytes += _write_json(data / 'race' / f"{race['race_id']}.json",
                                  _compact_detail(race_detail(conn, race['race_id'], as_of), shared_ids))
    sizes['race_files'] = race_bytes
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=TABLE_COLUMNS)
    writer.writeheader()
    for row in forecast_table(conn, as_of):
        writer.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()})
    (data / 'forecast_table_2026.csv').write_text(stream.getvalue(), encoding='utf-8', newline='')
    _write_json(data / MANIFEST, {'as_of': as_of, 'races': len(races), 'offices': list(OFFICES)})
    total = sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
    return {'out': str(out.resolve()), 'as_of': as_of, 'races': len(races), 'shared_news_articles': len(shared),
            'total_mb': round(total / 1e6, 1), 'race_files_mb': round(race_bytes / 1e6, 1)}
