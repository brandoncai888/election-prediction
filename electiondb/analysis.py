"""Historical comparisons and explicitly unvalidated sentiment scenarios."""
import json
import math
from datetime import date, datetime, timedelta, timezone
from .db import RACE_GROUPS, SEXES, EDUCATION, utc_now

MIN_SAMPLE=30

def usable(row):
    return (row is not None and row['dem_share'] is not None and row['sample_size']>=MIN_SAMPLE
            and (row['effective_n'] is None or row['effective_n']>=MIN_SAMPLE))

def historical_comparison(conn,geography_id,office):
    rows=conn.execute('''SELECT h.*, s.name AS source_name, s.url AS source_url
        FROM historical_cells h JOIN sources s USING(source_id)
        WHERE h.geography_id=? AND h.office=? ORDER BY h.election_year,h.race_group,h.sex,h.education,h.source_id''',
        (geography_id,office)).fetchall()
    result=[]
    for raw in rows:
        row=dict(raw)
        national=conn.execute('''SELECT * FROM historical_cells WHERE geography_id='us'
            AND election_year=? AND office=? AND race_group=? AND sex=? AND education=? AND source_id=?
            AND race_scheme=? AND universe=?''',
            (row['election_year'],office,row['race_group'],row['sex'],row['education'],row['source_id'],row['race_scheme'],row['universe'])).fetchone()
        row['suppressed']=not usable(row)
        row['dem_share']=row['dem_share'] if usable(row) else None
        row['national_dem_share']=national['dem_share'] if usable(national) else None
        row['national_sample_size']=national['sample_size'] if national else None
        row['gap_pp']=100*(row['dem_share']-row['national_dem_share']) if row['dem_share'] is not None and row['national_dem_share'] is not None else None
        row['benchmark_note']='Weighted national survey estimate for the identical group, year, office, source, race definition, and age universe. It includes local respondents.'
        result.append(row)
    # Even where the local sample is empty, the national reference is useful.
    keys={(r['election_year'],r['race_group'],r['sex'],r['education'],r['source_id']) for r in result}
    national_rows=conn.execute('''SELECT h.*,s.name AS source_name,s.url AS source_url
        FROM historical_cells h JOIN sources s USING(source_id) WHERE geography_id='us' AND office=?''',(office,)).fetchall()
    for raw in national_rows:
        row=dict(raw)
        if (row['election_year'],row['race_group'],row['sex'],row['education'],row['source_id']) in keys:
            continue
        row.update(national_dem_share=row['dem_share'] if usable(row) else None,
                   national_sample_size=row['sample_size'],dem_share=None,sample_size=0,effective_n=None,
                   weighted_total=None,boundary_vintage=None,
                   geography_id=geography_id,gap_pp=None,suppressed=True,
                   benchmark_note='National reference only; no local cell is available. National data is never presented as a local observation.')
        result.append(row)
    return result

def news_feature(conn,race_id,as_of,days=30):
    cutoff=date.fromisoformat(as_of)
    start=cutoff-timedelta(days=days-1)
    rows=conn.execute('''SELECT * FROM news_items WHERE (race_id=? OR race_id IS NULL)
        AND date(published_at) BETWEEN ? AND ? AND sentiment IS NOT NULL ORDER BY published_at''',
        (race_id,start.isoformat(),cutoff.isoformat())).fetchall()
    # Equal publisher weighting prevents a single prolific outlet dominating the feature.
    publisher={}
    for r in rows:
        if r['party']=='unassigned':
            continue
        key=(r['party'],r['source_name'])
        publisher.setdefault(key,[]).append(r['sentiment'])
    party_means={}
    for party in ('democratic','republican'):
        means=[sum(v)/len(v) for (p,_),v in publisher.items() if p==party]
        party_means[party]=sum(means)/len(means) if means else None
    d,r=party_means['democratic'],party_means['republican']
    return {'start':start.isoformat(),'as_of':as_of,'article_count':len(rows),
            'publisher_count':len({r['source_name'] for r in rows}),
            'attributed_article_count':sum(r['party']!='unassigned' for r in rows),
            'party_article_counts':{p:sum(r['party']==p for r in rows) for p in ('democratic','republican')},
            'democratic_mean':d,'republican_mean':r,'difference':d-r if d is not None and r is not None else None,
            'method':'30-day window by UTC publication date; equal publisher weight within party; D tone minus R tone. Articles without target attribution are excluded.',
            'limitation':'Entity mentions and headline tone do not establish sentiment toward a party, voter sentiment, or demographic exposure.'}

def scenario(conn,race,*,as_of='2026-10-21',baseline_year=None,source_id=None,coefficient_pp=0.0,assumed_shift_pp=0.0,persist=False):
    if not math.isfinite(coefficient_pp) or not math.isfinite(assumed_shift_pp):
        raise ValueError('Scenario parameters must be finite')
    if abs(coefficient_pp)>50 or abs(assumed_shift_pp)>50:
        raise ValueError('Scenario parameters must be between -50 and 50 percentage points')
    cutoff=date.fromisoformat(as_of)
    if cutoff>=date.fromisoformat(race['election_date']):
        raise ValueError('A pre-election scenario cutoff must precede the election date')
    feature=news_feature(conn,race['race_id'],as_of)
    baseline_year=baseline_year or conn.execute('SELECT MAX(election_year) FROM historical_cells WHERE office=? AND election_year<?',(race['office'],cutoff.year)).fetchone()[0]
    response={'status':'missing_history','message':'Import weighted historical survey cells for this office to create group scenarios.',
        'dem_share':None,'lower':None,'upper':None,'groups':[],'news_feature':feature,
        'as_of':as_of,'baseline_year':baseline_year,'coefficient_pp':coefficient_pp,'assumed_shift_pp':assumed_shift_pp,
        'validation':'No sentiment coefficient or election uncertainty has been calibrated. These are sensitivity scenarios, not validated election forecasts.'}
    if baseline_year is None:
        return response
    if baseline_year>=cutoff.year:
        raise ValueError('Baseline election must be earlier than the forecast cycle')
    if coefficient_pp and feature['difference'] is None:
        response.update(status='missing_sentiment',message='A news-based shift needs attributed news for both parties within the cutoff window.')
        return response
    shift=assumed_shift_pp+coefficient_pp*(feature['difference'] or 0)
    history=conn.execute('''SELECT * FROM historical_cells WHERE election_year=? AND office=?
        AND geography_id IN (?, 'us') ORDER BY geography_id, sample_size DESC, source_id''',
        (baseline_year,race['office'],race['geography_id'])).fetchall()
    # Select one documented study within a run; never silently pool sources.
    available={r['source_id'] for r in history if usable(r)}
    if source_id and source_id not in available:
        response['message']='The requested source has no usable historical cells for this year and office.'
        return response
    def source_rank(sid):
        candidates=[r for r in history if r['source_id']==sid and usable(r)]
        compatible=sum(r['race_scheme']=='census_race_any_ethnicity' and r['universe']=='survey_voters_age18_plus' for r in candidates)
        local=sum(r['geography_id']==race['geography_id'] for r in candidates)
        return (-compatible,-local,-len(candidates),sid)
    if not source_id and available:
        source_id=sorted(available,key=source_rank)[0]
    response['source_id']=source_id
    history=[r for r in history if r['source_id']==source_id]
    by_group={}
    for row in history:
        if not usable(row):
            continue
        key=(row['race_group'],row['sex'],row['education'],row['race_scheme'],row['universe'])
        existing=by_group.get(key)
        if existing is None or (existing['geography_id']=='us' and row['geography_id']==race['geography_id']):
            by_group[key]=row
    # A single race scheme/universe must be used within a run. Prefer compatible,
    # eligible-age survey cells, then 25+ descriptive cells, then alternative race schemes.
    choices={key[3:] for key in by_group}
    if not choices:
        response['message']='No historical cells meet the minimum raw and effective sample size of 30.'
        return response
    scheme,universe=sorted(choices,key=lambda x:(x[0]!='census_race_any_ethnicity',x[1]!='survey_voters_age18_plus',x))[0]
    groups=[]
    for key,row in sorted(by_group.items()):
        if key[3:]!=(scheme,universe):
            continue
        baseline=row['dem_share']
        projection=max(0.0,min(1.0,baseline+shift/100))
        groups.append({'race_group':row['race_group'],'sex':row['sex'],'education':row['education'],
            'baseline_dem_share':baseline,'projected_dem_share':projection,'swing_pp':100*(projection-baseline),
            'lower':None,'upper':None,'model_status':'unvalidated_scenario','baseline_geography':row['geography_id'],
            'sample_size':row['sample_size'],'source_id':row['source_id'],'race_scheme':scheme,'universe':universe,
            'baseline_boundary_vintage':row['boundary_vintage'],
            'exposure_note':'Uniform assumed shift; no demographic-specific news response has been measured.'})
    response.update(status='scenario_only',message='Group sensitivity scenarios are available. Race totals require a complete, compatible eligible-electorate and turnout table.',groups=groups)
    electorate=conn.execute('SELECT * FROM turnout_assumptions WHERE geography_id=? AND cycle=2026',(race['geography_id'],)).fetchall()
    required={(r,s,e) for r in RACE_GROUPS for s in SEXES for e in EDUCATION}
    weights={(r['race_group'],r['sex'],r['education']):r for r in electorate}
    estimates={(g['race_group'],g['sex'],g['education']):g for g in groups}
    valid_boundaries=all(r['boundary_vintage']=='2026' for r in electorate)
    valid_universe=all(r['universe']=='eligible_voters_age18_plus' for r in electorate)
    valid_history_boundaries=(race['office']!='house' or all(g['baseline_geography']=='us' or g['baseline_boundary_vintage']=='2026' for g in groups))
    if scheme=='census_race_any_ethnicity' and universe=='survey_voters_age18_plus' and set(weights)==required and required<=set(estimates) and valid_boundaries and valid_universe and valid_history_boundaries:
        total=sum(r['eligible_population']*r['turnout'] for r in electorate)
        if total>0:
            p=sum(r['eligible_population']*r['turnout']*estimates[k]['projected_dem_share'] for k,r in weights.items())/total
            response.update(status='unvalidated_aggregate_scenario',dem_share=p,
                message='Two-party aggregate sensitivity scenario using explicit eligible-population and turnout assumptions. No calibrated win probability is available.')
    response['coverage']={'available_group_scenarios':len(groups),'expected_acs_groups':len(required),
        'electorate_cells':len(electorate),'race_scheme':scheme,'historical_universe':universe,
        'compatible_2026_electorate':bool(electorate) and valid_boundaries and valid_universe and set(weights)==required,
        'compatible_history_boundaries':valid_history_boundaries}
    if persist:
        with conn:
            conn.execute('INSERT INTO model_runs(race_id,created_at,as_of,baseline_year,coefficient_pp,assumed_shift_pp,status,parameters,result) VALUES (?,?,?,?,?,?,?,?,?)',
                (race['race_id'],utc_now(),as_of,baseline_year,coefficient_pp,assumed_shift_pp,response['status'],
                 json.dumps({'minimum_sample':MIN_SAMPLE,'news_days':30,'shift_pp':shift,'source_id':source_id}),json.dumps(response,allow_nan=False)))
    return response
