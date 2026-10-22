"""2026 House and Senate general-election candidates from Wikipedia's election boxes.

Each state's House page and each Senate race page lists the general election as
an {{Election box begin}} block; primaries are skipped by their box titles. The
roster is written to data/candidates_2026.csv and names the submission picks.
"""
import csv
import json
import re
import time
from pathlib import Path
from urllib.parse import quote
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from .db import ROOT

WIKI_API='https://en.wikipedia.org/w/api.php'
ROSTER=ROOT/'data'/'candidates_2026.csv'
COLUMNS=('race_id','party','candidate','incumbent','source_url')
BOX=re.compile(r'\{\{\s*Election box begin[^|}]*\|\s*title\s*=\s*(.*?)\}\}(.*?)\{\{\s*Election box end',re.S|re.I)
CANDIDATE=re.compile(r'\{\{\s*Election box (?:candidate|winning candidate)[^|]*((?:\|[^{}]*|\{\{[^{}]*\}\})*)\}\}',re.I)
DISTRICT_HEADING=re.compile(r'^==\s*District\s+(\d+)\s*==\s*$',re.M)
ORDINAL=re.compile(r"'s\s+(\d+)(?:st|nd|rd|th)\s+congressional district",re.I)


def _wikitext(page,folder,max_age_hours=12):
    """Page wikitext, or None if the page does not exist. Recent pages are read from the cache."""
    cached=folder/(re.sub(r'[^A-Za-z0-9]+','_',page)+'.json')
    if cached.exists() and time.time()-cached.stat().st_mtime<max_age_hours*3600:
        raw=cached.read_bytes()
    else:
        url=f'{WIKI_API}?action=parse&format=json&formatversion=2&prop=wikitext&redirects=1&page={quote(page)}'
        for attempt in range(5):
            try:
                raw=urlopen(Request(url,headers={'User-Agent':'ElectionResearch/0.4 (aggregate research)'}),timeout=40).read()
                break
            except OSError as exc:
                if (isinstance(exc,HTTPError) and exc.code!=429) or attempt==4:
                    raise
                retry=exc.headers.get('Retry-After') if isinstance(exc,HTTPError) else None
                time.sleep(int(retry or 0) or 5*(attempt+1))
        time.sleep(1)
        cached.write_bytes(raw)
    payload=json.loads(raw.decode('utf-8'))
    return None if 'error' in payload else payload['parse']['wikitext']


def _field(body,name):
    match=re.search(r'\|\s*'+name+r'\s*=\s*((?:[^|{}\[\]]|\[\[[^\]]*\]\]|\{\{[^{}]*\}\})*)',body)
    return match.group(1).strip() if match else ''


def _plain(text):
    text=re.sub(r'<ref[^>]*/>|<ref[^>]*>.*?</ref>|<!--.*?-->','',text,flags=re.S)
    text=re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]*)\]\]',r'\1',text)
    text=re.sub(r'\{\{[^{}]*\}\}','',text)
    return re.sub(r'\s+',' ',text).strip()


def _party(text):
    text=_plain(text).lower()
    if text.startswith('democratic'):
        return 'D'
    if text.startswith('republican'):
        return 'R'
    return 'O'


def general_candidates(wikitext):
    """[(title, [(party, name, incumbent)])] for every non-primary election box."""
    boxes=[]
    for match in BOX.finditer(wikitext):
        title=_plain(match.group(1))
        if re.search(r'primary|runoff|convention|caucus|jungle|nonpartisan blanket',title,re.I):
            continue
        rows=[]
        for candidate in CANDIDATE.finditer(match.group(2)):
            raw=_field(candidate.group(1),'candidate')
            name=re.sub(r'\s*\((?:incumbent|write-in)\)\s*','',_plain(raw),flags=re.I).strip()
            if name and name.lower() not in ('none of these candidates','write-in','write-ins'):
                rows.append((_party(_field(candidate.group(1),'party')),name,'incumbent' in raw.lower()))
        if rows:
            boxes.append((match.start(),title,rows))
    return boxes


def _house_page(name,folder):
    for page in (f'2026 United States House of Representatives elections in {name}',
                 f'2026 United States House of Representatives election in {name}'):
        text=_wikitext(page,folder)
        if text:
            return page,text
    return None,None


def fetch_candidates(conn,cache_dir=ROOT/'data'/'cache',out=ROSTER):
    folder=Path(cache_dir)/'wikipedia_candidates'
    folder.mkdir(parents=True,exist_ok=True)
    roster,missing=[],[]
    house={}
    for race in conn.execute("SELECT race_id,state,district FROM races WHERE office='house' ORDER BY race_id"):
        house.setdefault(race['state'],[]).append(race)
    names={r['state']:r['label'].split(' - ')[0] for r in conn.execute("SELECT state,label FROM races WHERE office='senate'")}
    names.update({r['state']:r['label'].split(' at-large')[0].split(' District')[0].split(' - ')[0]
                  for r in conn.execute("SELECT state,label FROM races WHERE office='house'") if r['state'] not in names})
    for state,races in house.items():
        page,text=_house_page(names[state],folder)

        if not text:
            missing+=[r['race_id'] for r in races]
            continue
        source='https://en.wikipedia.org/wiki/'+quote(page.replace(' ','_'))
        boxes=general_candidates(text)
        headings=[(m.start(),int(m.group(1))) for m in DISTRICT_HEADING.finditer(text)]
        by_district={}
        for start,title,rows in boxes:
            ordinal=ORDINAL.search(title)
            if ordinal:
                district=int(ordinal.group(1))
            elif 'at-large' in title.lower() or len(races)==1:
                district=0
            else:
                before=[d for pos,d in headings if pos<start]
                district=before[-1] if before else None
            if district is not None:
                by_district[district]=rows  # the last general box in a section is the general election
        for race in races:
            rows=by_district.get(int(race['district']))
            if not rows:
                missing.append(race['race_id'])
                continue
            roster+=[(race['race_id'],party,name,incumbent,source) for party,name,incumbent in rows]
    for race in conn.execute("SELECT race_id,state,election_type FROM races WHERE office='senate' ORDER BY state").fetchall():
        page=f"2026 United States Senate {'special election' if race['election_type']=='special' else 'election'} in {names[race['state']]}"
        text=_wikitext(page,folder)

        boxes=general_candidates(text or '')
        if not boxes:
            missing.append(race['race_id'])
            continue
        source='https://en.wikipedia.org/wiki/'+quote(page.replace(' ','_'))
        roster+=[(race['race_id'],party,name,incumbent,source) for party,name,incumbent in boxes[-1][2]]
    with open(out,'w',newline='',encoding='utf-8') as stream:
        writer=csv.writer(stream)
        writer.writerow(COLUMNS)
        writer.writerows(roster)
    return {'path':str(out),'candidates':len(roster),'races':len({r[0] for r in roster}),'races_missing':missing}


def load_roster(path=ROSTER):
    """{race_id: {party: [names]}} from the saved roster; empty when none was fetched."""
    roster={}
    if Path(path).exists():
        with open(path,encoding='utf-8',newline='') as stream:
            for row in csv.DictReader(stream):
                roster.setdefault(row['race_id'],{}).setdefault(row['party'],[]).append(row['candidate'])
    return roster
