"""Validated bulk imports. A bad row aborts an entire file, never half an import."""
import csv
import math
import re
from pathlib import Path
from urllib.parse import urlparse
from .db import RACE_GROUPS, SEXES, EDUCATION, COARSE_EDUCATION, MODEL_RACE_GROUPS, utc_now

OFFICES = {'house','senate','governor','president'}
EXTRA_RACE_GROUPS = {'hispanic','middle_eastern','non_hispanic_white','non_hispanic_black','non_hispanic_asian','non_hispanic_other'}

def read_rows(path):
    with open(path, newline='', encoding='utf-8-sig') as stream:
        return list(csv.DictReader(stream))

def number(value, name, lower=0, upper=None, integer=False, optional=False):
    if optional and (value is None or str(value).strip()==''):
        return None
    n = float(value)
    if not math.isfinite(n) or n < lower or (upper is not None and n > upper):
        raise ValueError(f'{name} must be finite and between {lower} and {upper}')
    if integer and n != int(n):
        raise ValueError(f'{name} must be an integer')
    return int(n) if integer else n

def validate_geo(geo):
    if not re.fullmatch(r'us|state:\d{2}|cd:\d{4}', geo):
        raise ValueError(f'Invalid geography_id: {geo}')
    return geo

def validate_group(row):
    validate_geo(row['geography_id'])
    if row['race_group'] not in set(RACE_GROUPS)|EXTRA_RACE_GROUPS|set(MODEL_RACE_GROUPS):
        raise ValueError(f"Unknown race_group: {row['race_group']}")
    if row['sex'] not in SEXES or row['education'] not in set(EDUCATION)|set(COARSE_EDUCATION):
        raise ValueError('Use documented sex and education codes')

def source_row(source_id, source_name, source_url, kind, notes=''):
    if not source_id or not source_name or urlparse(source_url).scheme not in {'https','http'}:
        raise ValueError('A source ID, name, and public HTTP(S) source URL are required')
    return (source_id,source_name,source_url,utc_now(),kind,notes)

def import_history(conn,path,*,source_id,source_name,source_url):
    source = source_row(source_id,source_name,source_url,'survey',
                        'Imported aggregate weighted two-party voting estimates. Samples below 30 are suppressed in the API.')
    values=[]
    for i,row in enumerate(read_rows(path),2):
        try:
            validate_group(row)
            if row['office'] not in OFFICES:
                raise ValueError('Invalid office')
            n=number(row['sample_size'],'sample_size',integer=True)
            p=number(row.get('dem_share'),'dem_share',upper=1,optional=True)
            if n == 0 and p is not None:
                raise ValueError('A zero-sample cell cannot have a vote share')
            neff=number(row.get('effective_n'),'effective_n',optional=True)
            if neff is not None and neff > n+0.00001:
                raise ValueError('effective_n cannot exceed the unweighted sample size')
            scheme=row.get('race_scheme') or 'census_race_any_ethnicity'
            if row['race_group'] in EXTRA_RACE_GROUPS and scheme=='census_race_any_ethnicity':
                raise ValueError('Hispanic-exclusive race groups require a distinct race_scheme')
            if row.get('universe') not in {'survey_voters_age25_plus','survey_voters_age18_plus'}:
                raise ValueError('Specify universe: survey_voters_age25_plus or survey_voters_age18_plus')
            values.append((row['geography_id'],number(row['election_year'],'election_year',1900,2024,True),
                row['office'],row['race_group'],row['sex'],row['education'],p,n,neff,
                number(row.get('weighted_total'),'weighted_total',optional=True),
                row.get('estimate_kind') or 'weighted_survey',scheme,row['universe'],
                row.get('boundary_vintage') or 'unspecified',source_id))
        except (ValueError,KeyError,TypeError) as exc:
            raise ValueError(f'{path}, row {i}: {exc}') from exc
    keys=[v[:6] for v in values]
    if len(set(keys))!=len(keys):
        raise ValueError('Duplicate historical cells in one source file')
    with conn:
        conn.execute('INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?,?)',source)
        conn.executemany('INSERT OR REPLACE INTO historical_cells VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',values)
    return {'imported':len(values),'source_id':source_id}

def import_turnout(conn,path,*,source_id,source_name,source_url):
    source=source_row(source_id,source_name,source_url,'electorate',
                       'Explicit eligible-population and turnout inputs. Not inferred from ACS age-25+ residents.')
    values=[]
    for i,row in enumerate(read_rows(path),2):
        try:
            validate_group(row)
            if row.get('universe')!='eligible_voters_age18_plus':
                raise ValueError('Universe must be eligible_voters_age18_plus; ACS residents are not eligible voters')
            if not row.get('boundary_vintage'):
                raise ValueError('boundary_vintage is required')
            values.append((row['geography_id'],number(row['cycle'],'cycle',2026,2026,True),
                           row['race_group'],row['sex'],row['education'],
                           number(row['eligible_population'],'eligible_population'),
                           number(row['turnout'],'turnout',upper=1),row['universe'],row['boundary_vintage'],source_id))
        except (ValueError,KeyError,TypeError) as exc:
            raise ValueError(f'{path}, row {i}: {exc}') from exc
    if len({v[:5] for v in values})!=len(values):
        raise ValueError('Duplicate electorate cells')
    with conn:
        conn.execute('INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?,?)',source)
        conn.executemany('INSERT OR REPLACE INTO turnout_assumptions VALUES (?,?,?,?,?,?,?,?,?,?)',values)
    return {'imported':len(values),'source_id':source_id}

def import_results(conn,path,*,source_id,source_name,source_url):
    source=source_row(source_id,source_name,source_url,'official_returns')
    values=[]
    for i,row in enumerate(read_rows(path),2):
        try:
            validate_geo(row['geography_id'])
            if row['office'] not in OFFICES:
                raise ValueError('Invalid office')
            values.append((row['geography_id'],number(row['election_year'],'election_year',1900,2024,True),row['office'],
                number(row['dem_votes'],'dem_votes',integer=True),number(row['rep_votes'],'rep_votes',integer=True),
                number(row.get('other_votes') or 0,'other_votes',integer=True),row['boundary_vintage'],source_id))
        except (ValueError,KeyError,TypeError) as exc:
            raise ValueError(f'{path}, row {i}: {exc}') from exc
    if len({v[:3] for v in values})!=len(values):
        raise ValueError('Duplicate election result rows')
    with conn:
        conn.execute('INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?,?)',source)
        conn.executemany('INSERT OR REPLACE INTO election_results VALUES (?,?,?,?,?,?,?,?)',values)
    return {'imported':len(values),'source_id':source_id}
