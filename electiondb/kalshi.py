"""Kalshi market prices and the points-maximizing submission pick for each race.

A correct pick scores log_(1/2)(p) = -log2(p) points, where p is Kalshi's price
for the picked candidate; a wrong pick scores 0. Expected points for a side are
therefore model probability x -log2(Kalshi price).
"""
import hashlib
import json
import math
import re
import time
from pathlib import Path
from urllib.request import Request, urlopen
from .db import ROOT, utc_now

API='https://api.elections.kalshi.com/trade-api/v2'
SOURCE_ID='kalshi-election-markets'
AUTO_PICK=0.80          # model probability above which the favorite is always picked
UNDERDOG_RATIO=2.0     # underdog expected points must reach this multiple of the favorite's
THIRD_CANDIDATE=0.05   # a non-major candidate priced at least this high breaks the two-party mapping
PRICE_FLOOR=0.01       # keeps -log2(p) finite; a correct pick is worth at most log2(100) = 6.6 points
# Tickers are not reliable (SENATELA-26 is the Kentucky race), so each market is
# placed by the seat its rules name and takes its party from the same text.
HOUSE_SEAT=re.compile(r'\b([A-Z]{2})-(AL|\d{1,2})\b')
SENATE_STATE=re.compile(r'Senator (?:of|from) ([A-Z][A-Za-z ]+?) (?:for|pursuant)\b')
RULE_PARTY=re.compile(r'\b(Democratic|Republican)(?: \(DFL\)|-Farmer-Labor)? [Pp]arty\b')

SCHEMA='''CREATE TABLE IF NOT EXISTS kalshi_markets (
 race_id TEXT NOT NULL REFERENCES races(race_id), ticker TEXT NOT NULL, party TEXT NOT NULL CHECK(party IN ('D','R','O')),
 candidate TEXT NOT NULL, yes_bid REAL, yes_ask REAL, last_price REAL, probability REAL CHECK(probability BETWEEN 0 AND 1),
 fetched_at TEXT NOT NULL, PRIMARY KEY(race_id,ticker)
);'''


def ensure_kalshi_schema(conn):
    conn.executescript(SCHEMA)


def _get(path,folder):
    raw=urlopen(Request(API+path,headers={'User-Agent':'ElectionResearch/0.4 (aggregate research)','Accept':'application/json'}),timeout=40).read()
    (folder/f'{hashlib.sha256(raw).hexdigest()[:16]}.json').write_bytes(raw)
    return json.loads(raw.decode('utf-8'))


def market_probability(market):
    """Midpoint of the quoted yes bid and ask; the last trade when one side is missing."""
    bid,ask,last=(float(market.get(k) or 0) for k in ('yes_bid_dollars','yes_ask_dollars','last_price_dollars'))
    if bid>0 and ask>=bid:
        return (bid+ask)/2
    return last or None


def market_race(market,senate_races):
    """race_id named by a market's rules; senate_races maps state name to race_id."""
    rules=market.get('rules_primary') or ''
    seat=HOUSE_SEAT.search(rules)
    if seat:
        state,district=seat.groups()
        return f"2026-house-{state}-{'00' if district=='AL' else district.zfill(2)}"
    state=SENATE_STATE.search(rules)
    return senate_races.get(state.group(1)) if state else None


def market_rows(markets,senate_races,fetched_at):
    rows=[]
    for m in markets:
        race_id=market_race(m,senate_races)
        if m.get('status') not in ('active','open') or race_id is None:
            continue
        party=RULE_PARTY.search(m.get('rules_primary') or '')
        rows.append((race_id,m['ticker'],party.group(1)[0] if party else 'O',m.get('yes_sub_title') or '',
                     float(m.get('yes_bid_dollars') or 0),float(m.get('yes_ask_dollars') or 0),
                     float(m.get('last_price_dollars') or 0),market_probability(m),fetched_at))
    return rows


def fetch_kalshi(conn,cache_dir=ROOT/'data'/'cache'):
    """Replace stored prices with current Kalshi markets for every House and Senate race."""
    ensure_kalshi_schema(conn)
    folder=Path(cache_dir)/'kalshi'
    folder.mkdir(parents=True,exist_ok=True)
    fetched_at=utc_now()
    races={r['race_id'] for r in conn.execute("SELECT race_id FROM races WHERE office IN ('house','senate')")}
    senate=conn.execute("SELECT race_id,state,label,election_type FROM races WHERE office='senate' ORDER BY state").fetchall()
    senate_races={r['label'].split(' - ')[0]:r['race_id'] for r in senate}
    by_race={}
    def add(markets):
        # The first event found for a race wins; a second listing of the same seat is ignored.
        found={}
        for row in market_rows(markets,senate_races,fetched_at):
            found.setdefault(row[0],[]).append(row)
        for race_id,rows in found.items():
            if race_id in races:
                by_race.setdefault(race_id,rows)
    def event(ticker):
        try:
            payload=_get(f'/events/{ticker}?with_nested_markets=true',folder)
        except OSError:
            return False
        time.sleep(0.1)
        add(payload['event'].get('markets') or payload.get('markets') or [])
        return True
    for race in senate:
        event(f"SENATE{race['state']}{'S' if race['election_type']=='special' else ''}-26")
    for race in senate:
        if race['race_id'] not in by_race:
            any(event(ticker) for ticker in (f"KXSENATE{race['state']}-26NOV",f"KXSENATE{race['state']}-26"))
    cursor=''
    while True:
        page=_get('/events?series_ticker=KXHOUSERACE&status=open&with_nested_markets=true&limit=200'+(f'&cursor={cursor}' if cursor else ''),folder)
        for item in page.get('events',[]):
            add(item.get('markets') or [])
        cursor=page.get('cursor') or ''
        if not cursor:
            break
    # Seats with markets listed before KXHOUSERACE keep their own series (HOUSEPA7, KXHOUSETX9, HOUSEAKAL).
    for race in conn.execute("SELECT race_id,state,district FROM races WHERE office='house' ORDER BY race_id").fetchall():
        if race['race_id'] not in by_race:
            seat='AL' if race['district']=='00' else str(int(race['district']))
            any(event(ticker) and race['race_id'] in by_race for ticker in
                (f"HOUSE{race['state']}{seat}-26",f"KXHOUSE{race['state']}{seat}-26",f"KXHOUSE{race['state']}{race['district']}-26"))
    rows=[row for race_rows in by_race.values() for row in race_rows]
    covered=set(by_race)
    missing=sorted(races-covered)
    with conn:
        conn.execute('''INSERT INTO sources VALUES (?,?,?,?,?,?) ON CONFLICT(source_id)
            DO UPDATE SET retrieved_at=excluded.retrieved_at,notes=excluded.notes''',
            (SOURCE_ID,'Kalshi election markets','https://kalshi.com/category/elections',fetched_at,'markets',
             'House and Senate winner market prices (bid/ask midpoint) used only to choose submission picks, not in the forecast. Raw responses cached.'))
        conn.execute('DELETE FROM kalshi_markets')
        conn.executemany('INSERT INTO kalshi_markets VALUES (?,?,?,?,?,?,?,?,?)',rows)
    return {'markets':len(rows),'races_covered':len(covered),'races_missing':missing,'fetched_at':fetched_at}


def points(price):
    """Points for a correct pick: log base 1/2 of the Kalshi price."""
    return -math.log2(max(price,PRICE_FLOOR))


def choose_pick(p_dem,prices):
    """Pick D or R for one race from the model's D win probability and Kalshi prices.

    prices maps 'D'/'R' to (probability, candidate) and 'O' to the highest other
    candidate's probability. Returns the pick with the expected points behind it.
    """
    favorite='D' if p_dem>=.5 else 'R'
    underdog='R' if favorite=='D' else 'D'
    model={'D':p_dem,'R':1-p_dem}
    kalshi={party:(prices.get(party) or (None,''))[0] for party in ('D','R')}
    names={party:(prices.get(party) or (None,''))[1] for party in ('D','R')}
    ev={party:model[party]*points(kalshi[party]) if kalshi[party] is not None else None for party in ('D','R')}
    if model[favorite]>AUTO_PICK:
        pick,reason=favorite,'model_auto'
    elif ev[favorite] is None or ev[underdog] is None:
        pick,reason=favorite,'no_market'
    elif (prices.get('O') or 0)>=THIRD_CANDIDATE:
        pick,reason=favorite,'third_candidate'
    elif ev[underdog]>=UNDERDOG_RATIO*ev[favorite]:
        pick,reason=underdog,'underdog_value'
    else:
        pick,reason=favorite,'favorite_value'
    ratio=ev[underdog]/ev[favorite] if ev[favorite] and ev[underdog] is not None else None
    return {'pick':pick,'reason':reason,'favorite':favorite,'model_probability':model[pick],
            'model_favorite_probability':model[favorite],'kalshi_d':kalshi['D'],'kalshi_r':kalshi['R'],
            'kalshi_other':prices.get('O') or 0,'candidate':names[pick],'candidate_d':names['D'],'candidate_r':names['R'],
            'ev_favorite':ev[favorite],'ev_underdog':ev[underdog],'underdog_ratio':ratio,
            'points_if_right':points(kalshi[pick]) if kalshi[pick] is not None else None,
            'expected_points':ev[pick]}


def stored_prices(conn):
    ensure_kalshi_schema(conn)
    prices={}
    for row in conn.execute('SELECT race_id,party,candidate,probability FROM kalshi_markets WHERE probability IS NOT NULL'):
        race=prices.setdefault(row['race_id'],{})
        if row['party']=='O':
            race['O']=max(race.get('O',0),row['probability'])
        else:
            race[row['party']]=(row['probability'],row['candidate'])
    fetched=conn.execute('SELECT MAX(fetched_at) FROM kalshi_markets').fetchone()[0]
    return prices,fetched


def _name_key(name):
    """(first initial, last name) with accents, punctuation and suffixes removed."""
    import unicodedata
    plain=unicodedata.normalize('NFKD',name).encode('ascii','ignore').decode().lower()
    words=[w for w in re.findall(r"[a-z]+(?:['-][a-z]+)*",plain) if w not in ('jr','sr','ii','iii','iv')]
    return (words[0][0],words[-1]) if words else None


def pick_name(kalshi_name,listed):
    """Candidate name for a pick and how it was checked.

    listed holds the Wikipedia general-election candidates of the picked party.
    Wikipedia's spelling wins; Kalshi's name decides between two same-party
    candidates (California top-two) and fills in when Wikipedia has none.
    """
    kalshi_name='' if kalshi_name.endswith(' party') else kalshi_name
    match=next((name for name in listed if kalshi_name and _name_key(name)==_name_key(kalshi_name)),None)
    if match:
        return match,'confirmed'
    if len(listed)==1:
        return listed[0],'wikipedia' if not kalshi_name else 'wikipedia_differs'
    if listed:
        return listed[0],'wikipedia_ambiguous'
    return kalshi_name,'kalshi_only' if kalshi_name else 'missing'


def strategy(conn,as_of):
    """Every House and Senate pick, plus the rules and totals the dashboard shows."""
    from .forecasts import forecast_overview
    from .candidates import load_roster
    prices,fetched_at=stored_prices(conn)
    roster=load_roster()
    races=[]
    for race in forecast_overview(conn,as_of)['races']:
        if race['office'] not in ('house','senate') or race.get('win_probability_d') is None:
            continue
        pick=choose_pick(race['win_probability_d'],prices.get(race['race_id'],{}))
        pick['kalshi_candidate']=pick['candidate']
        pick['candidate'],pick['name_check']=pick_name(pick['candidate'],roster.get(race['race_id'],{}).get(pick['pick'],[]))
        races.append({'race_id':race['race_id'],'label':race['label'],'office':race['office'],'state':race['state'],
                      'district':race.get('district'),**pick})
    reasons={}
    for race in races:
        reasons[race['reason']]=reasons.get(race['reason'],0)+1
    return {'as_of':as_of,'kalshi_fetched_at':fetched_at,
            'rules':{'auto_pick_above':AUTO_PICK,'underdog_ratio':UNDERDOG_RATIO,'third_candidate':THIRD_CANDIDATE,'price_floor':PRICE_FLOOR},
            'summary':{'races':len(races),'reasons':reasons,
                       'underdog_picks':reasons.get('underdog_value',0),
                       'name_checks':{check:sum(r['name_check']==check for r in races) for check in sorted({r['name_check'] for r in races})},
                       'expected_points':sum(r['expected_points'] or 0 for r in races),
                       'picks':{office:{party:sum(r['office']==office and r['pick']==party for r in races) for party in ('D','R')}
                                for office in ('house','senate')}},
            'races':races}
