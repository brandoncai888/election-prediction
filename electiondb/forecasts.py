"""Persist inspectable race and demographic predictions for the dashboard and SQL."""
import json
import math
from .db import utc_now, EDUCATION_BRIDGE, RACE_GROUPS


def save_predictions(conn, payload):
    as_of=payload['as_of']
    model=payload.get('model',{})
    model.setdefault('status','experimental_forecast')
    version=str(model.get('forecast_version') or model.get('version') or model.get('model_version') or 'demographic-news-v2')
    timestamp=utc_now()
    race_rows=[]
    group_rows=[]
    for row in payload['races']:
        race_id=row['race_id']
        row.setdefault('population',sum(float(g.get('population') or 0) for g in row.get('groups',[])))
        encoded=json.dumps(row,allow_nan=False)
        race_rows.append((race_id,as_of,version,timestamp,row.get('dem_share'),row.get('rep_share'),
                          row.get('winner'),row.get('margin_pp'),row.get('rating'),row['status'],encoded))
        groups=row.get('groups',[])
        total=sum(float(g.get('population') or 0) for g in groups)
        for g in groups:
            population=float(g.get('population') or 0)
            # Forecasts carry expected-voter weights; older payloads fall back to population.
            weight=g['weight'] if 'weight' in g else population/total if total else 0
            group_rows.append((race_id,as_of,g['race_group'],g['sex'],g['education'],population,weight,
                g['baseline_dem_share'],g['projected_dem_share'],g['swing_pp'],version,json.dumps(g,allow_nan=False)))
    with conn:
        conn.executemany('INSERT OR REPLACE INTO race_forecasts VALUES (?,?,?,?,?,?,?,?,?,?,?)',race_rows)
        conn.execute('DELETE FROM demographic_forecasts WHERE as_of=?',(as_of,))
        conn.executemany('INSERT INTO demographic_forecasts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',group_rows)
        conn.execute('INSERT OR REPLACE INTO forecast_metadata VALUES (?,?,?,?)',
            (as_of,timestamp,json.dumps(model,allow_nan=False),json.dumps(payload.get('validation',{}),allow_nan=False)))
    validation={k:v for k,v in payload.get('validation',{}).items() if k not in {'calibration_bins','offices'}}
    return {'as_of':as_of,'races':len(race_rows),'demographic_predictions':len(group_rows),'model_version':version,
            'status':'experimental_forecast','validation':validation,'counts':payload.get('counts',{})}


def get_prediction(conn,race_id,as_of):
    row=conn.execute('SELECT result FROM race_forecasts WHERE race_id=? AND as_of=?',(race_id,as_of)).fetchone()
    return json.loads(row['result']) if row else None


def forecast_overview(conn,as_of,office=None):
    values=[as_of]
    sql='''SELECT f.result,r.state,r.office FROM race_forecasts f JOIN races r USING(race_id) WHERE f.as_of=?'''
    if office:
        sql+=' AND r.office=?'
        values.append(office)
    rows=[]
    for stored in conn.execute(sql+' ORDER BY r.state,r.district,r.office',values):
        result=json.loads(stored['result'])
        # Maps need compact race summaries. Full inputs remain on race selection.
        fields={'race_id','label','state','state_fips','district','office','geography_id',
                'election_type','baseline_dem_share','dem_share','rep_share','winner','margin_pp',
                'swing_pp','rating','status','population','news_article_count','boundary_status',
                'baseline_margin_pp','news_margin_shift_pp','win_probability_d','win_probability_r',
                'win_probability_d_without_news','sigma_margin_pp','environment_shift_pp','turnout_shift_pp'}
        anchor=result.get('anchor') or {}
        result={key:value for key,value in result.items() if key in fields}
        result['district_redrawn_for_2026']=bool(anchor.get('district_redrawn_for_2026'))
        result['anchor_source']=anchor.get('source_id')
        rows.append(result)
    metadata=conn.execute('SELECT * FROM forecast_metadata WHERE as_of=?',(as_of,)).fetchone()
    model=json.loads(metadata['model']) if metadata else {'status':'not_built','message':'Run python main.py predict'}
    from .senate import holdover_counts
    chambers=model.get('chambers',{})
    return {'as_of':as_of,'races':rows,'chamber':chambers.get(office) if office else chambers,
        'holdovers':holdover_counts() if office in (None,'senate') else None,
        'model':model,
        'validation':json.loads(metadata['validation']) if metadata else {},
        'summary':{'total':len(rows),'democratic':sum(r.get('winner')=='D' for r in rows),
                   'republican':sum(r.get('winner')=='R' for r in rows),
                   'tossups':sum(r.get('rating')=='tossup' for r in rows),
                   'scope':'Seats contested in 2026. Senate chamber totals add the 65 holdover seats (see holdovers).'}}


def coarse_demographics(conn,geography_id):
    rows=conn.execute('''SELECT * FROM demographics WHERE geography_id=?
        AND acs_year=(SELECT MAX(acs_year) FROM demographics WHERE geography_id=?)''',
        (geography_id,geography_id)).fetchall()
    groups={}
    for raw in rows:
        row=dict(raw)
        if row['race_group'] not in RACE_GROUPS:
            continue
        key=(row['race_group'],row['sex'],EDUCATION_BRIDGE.get(row['education'],row['education']))
        if key not in groups:
            groups[key]={**row,'education':key[2],'population':0,'moe':None,'_moe2':0,'_moe_complete':True,
                         'moe_method':'Root-sum-of-squares approximation for combined education categories'}
        item=groups[key]
        item['population']+=row['population'] or 0
        if row['moe'] is None:
            item['_moe_complete']=False
        else:
            item['_moe2']+=row['moe']**2
    for item in groups.values():
        item['moe']=item.pop('_moe2')**.5 if item.pop('_moe_complete') else None
        item.pop('_moe_complete',None)
        item.pop('_moe2',None)
    return list(groups.values())


def sensitivity(conn,race,as_of,coefficient_pp=0.0,assumed_shift_pp=0.0):
    """Optional uniform sensitivity around fitted 2024 group baselines."""
    if not all(math.isfinite(x) and abs(x)<=50 for x in (coefficient_pp,assumed_shift_pp)):
        raise ValueError('Scenario assumptions must be finite and within 50 percentage points')
    result=get_prediction(conn,race['race_id'],as_of)
    if not result:
        from .analysis import scenario
        return scenario(conn,race,as_of=as_of,coefficient_pp=coefficient_pp,assumed_shift_pp=assumed_shift_pp)
    feature=result.get('news_feature',{})
    difference=feature.get('difference')
    if coefficient_pp and difference is None:
        return {'status':'missing_sentiment','message':'No attributable party-tone difference is available in the cutoff window.',
                'groups':[],'news_feature':feature,'as_of':as_of,'baseline_year':2024}
    change=(assumed_shift_pp+coefficient_pp*(difference or 0))/100
    for group in result['groups']:
        baseline=group['baseline_dem_share']
        group['projected_dem_share']=min(1,max(0,baseline+change))
        group['swing_pp']=100*(group['projected_dem_share']-baseline)
        group['model_status']='manual_scenario'
        group['exposure_note']='Uniform manual sensitivity around the fitted 2024 baseline; separate from the news-content forecast.'
    total=sum(g['population'] for g in result['groups'])
    result['dem_share']=sum(g['population']*g['projected_dem_share'] for g in result['groups'])/total if total else None
    result.update(status='manual_scenario',message='Manual sensitivity; the main forecast uses demographic-specific issue responses.',
                  baseline_year=2024,coefficient_pp=coefficient_pp,assumed_shift_pp=assumed_shift_pp)
    return result


TABLE_COLUMNS=('race_id','office','state','label','rating','win_probability_d','win_probability_r','margin_pp',
               'dem_share','rep_share','baseline_margin_pp','environment_shift_pp','turnout_shift_pp',
               'news_margin_shift_pp','win_probability_d_without_news','district_redrawn_for_2026')


def forecast_table(conn,as_of,office=None):
    """One row per race: win probabilities, margins, and the news contribution."""
    order={'senate':0,'governor':1,'house':2}
    rows=forecast_overview(conn,as_of,office)['races']
    rows.sort(key=lambda r:(order.get(r['office'],3),r['state'],str(r.get('district') or '')))
    return [{k:r.get(k) for k in TABLE_COLUMNS} for r in rows]


BJC_COLUMNS=('state','district','chamber','predicted_winner','party','model_probability')
PARTY_NAMES={'D':'Democratic','R':'Republican'}


def _bjc_key(race):
    """(state, district, chamber): district is 01-style or AL for House, blank for Senate."""
    if race['office']=='senate':
        return race['state'],'','Senate'
    district=str(race.get('district') or '00').zfill(2)
    return race['state'],'AL' if district=='00' else district,'House'


def _bjc_row_key(row):
    # Sheets written before the column change identify races as AK-SEN / AL-01.
    if 'race_id' in row:
        state,_,seat=row['race_id'].partition('-')
        return (state,'','Senate') if seat=='SEN' else (state,seat,'House')
    return row['state'],row['district'],row['chamber']


def write_bjc_prediction(conn,as_of,path):
    """Fill the House/Senate submission sheet with the points-maximizing pick.

    The pick follows electiondb.kalshi.choose_pick; model_probability is the
    model's win probability for the picked party, so underdog picks show < 0.5.
    predicted_winner is the Wikipedia nominee for that party (checked against
    Kalshi; see electiondb.kalshi.pick_name), otherwise any name already typed
    in. Rows keep the file's order.
    """
    import csv
    from pathlib import Path
    from .kalshi import strategy
    path=Path(path)
    existing=[]
    if path.exists():
        with open(path,encoding='utf-8-sig',newline='') as stream:
            existing=list(csv.DictReader(stream))
    races={}
    for race in strategy(conn,as_of)['races']:
        races[_bjc_key(race)]=race
    order=[_bjc_row_key(row) for row in existing] or sorted(races,key=lambda k:(k[2]!='Senate',k))
    names={_bjc_row_key(row):row.get('predicted_winner',row.get('candidate_name','')) for row in existing}
    missing=[key for key in order if key not in races]
    if missing:
        raise ValueError(f'{path.name} lists races with no saved forecast: {", ".join("-".join(filter(None,k[:2])) for k in missing[:5])}')
    with open(path,'w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=BJC_COLUMNS)
        writer.writeheader()
        for key in order:
            race=races[key]
            writer.writerow({'state':key[0],'district':key[1],'chamber':key[2],
                'predicted_winner':race['candidate'] or names.get(key,''),'party':PARTY_NAMES[race['pick']],
                'model_probability':round(race['model_probability'],4)})
    return {'path':str(path),'rows':len(order),'underdog_picks':sum(r['reason']=='underdog_value' for r in races.values())}


def write_forecast_table(conn,as_of,path):
    import csv
    rows=forecast_table(conn,as_of)
    with open(path,'w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=TABLE_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k:(round(v,4) if isinstance(v,float) else v) for k,v in row.items()})
    return {'path':str(path),'rows':len(rows)}
