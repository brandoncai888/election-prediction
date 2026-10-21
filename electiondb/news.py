"""RSS affect and content features, separate from assumed electoral reactions.

VADER measures title/summary affect. Local outcome rules assign party targets;
neither measures public opinion. Zero and missing lexical coverage are distinct.
"""
from __future__ import annotations
import csv
import hashlib
import html
from html.parser import HTMLParser
import importlib.util
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse, urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from .db import utc_now

DEFAULT_FEED = 'https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml'
DEFAULT_FEEDS = [('BBC US & Canada RSS', DEFAULT_FEED),
    ('NPR Politics RSS', 'https://feeds.npr.org/1014/rss.xml'),
    ('The Guardian US Politics RSS', 'https://www.theguardian.com/us-news/us-politics/rss')]
METHOD = 'vader_rss_summary_v2; local_party_outcome_rules_v2'
TOKEN = re.compile(r"[a-z]+(?:'[a-z]+)?", re.I)
DEMOCRATIC = re.compile(r'\b(democrats?|democratic|harris|biden|schumer|jeffries|newsom|pelosi|talarico|ossoff)\b', re.I)
REPUBLICAN = re.compile(r'\b(republicans?|gop|trump|vance|mcconnell|mike johnson|rubio)\b', re.I)
PARTIES = {'democratic': DEMOCRATIC, 'republican': REPUBLICAN}
NEGATIONS = frozenset("no not never neither nor hardly without isn't aren't wasn't weren't don't doesn't didn't won't wouldn't can't cannot couldn't hasn't haven't hadn't".split())

# Explicit analyst rules, not coefficients fitted to election outcomes. We do
# not assign the negative tone of war/crime/inflation to a nearby quoted party.
OUTCOME_RULES = [
 (r"\b(?:wins?|winning|won(?!['’])|victor(?:y|ies|ious)|success(?:ful)?|surges?|gains?|gaining|boosts?|grows?|recover(?:s|y|ing)?)\b", 1.6),
 (r'\b(?:leads?|leading|popular|improves?|improved|improving|endors(?:es?|ed|ement)|backs?|backing|supports?|supporting)\b', 1.2),
 (r'\bahead in (?:the )?(?:polls?|surveys?|race)\b', 1.2),
 (r'\b(?:los(?:e|es|ing|t)|loss(?:es)?|fails?|failed|failure|defeats?|defeated|setbacks?|craters?|plunges?|collapses?|sued|indicted|convicted|scandals?|corruption)\b', -1.8),
 (r'\b(?:struggl(?:e|es|ing)|frustrated|unpopular|disapprov(?:al|es?)|declin(?:e|es|ing)|backlash|legal (?:challenge|dispute)|blocks?|blocked|rejected|unreliable|false|misleading)\b', -1.3),
 (r'\b(?:losing faith|los(?:e|es|ing|t) support|approval (?:rating )?(?:falls|craters|drops)|opposition grows|upholds? pause on)\b', -2.0),
 # Voter-movement language, common in coverage of demographic groups. Longer
 # phrases are matched first, so "gains ... slip" is one negative cue.
 (r"\b(?:gains?|support|edge|lead|advantage|inroads)\b(?:\s+\w+){0,5}?\s+(?:slips?|slipping|slipped|erod(?:e|es|ing|ed)|fad(?:e|es|ing|ed)|shrink(?:s|ing)?|shrank|evaporat(?:e|es|ing|ed)|vanish(?:es|ing|ed)?|collaps(?:e|es|ing|ed)|reverse[sd]?)\b", -2.0),
 (r"\b(?:swing(?:s|ing)?|swung|shift(?:s|ing|ed)?|turn(?:s|ing|ed)?|mov(?:e|es|ing|ed)|break(?:s|ing)?|broke|drift(?:s|ing|ed)?) away from\b|\bsour(?:s|ing|ed)? on\b|\babandon(?:s|ing|ed)?\b|\bdesert(?:s|ing|ed)?\b|\bdefect(?:s|ing|ed)? from\b|\bturn(?:s|ing|ed)? on\b|\blos(?:e|es|ing|t) ground\b|\bdisillusioned with\b|\bfrustrat(?:ed|ion) with\b|\bbacklash against\b", -2.0),
 (r"\b(?:swing(?:s|ing)?|swung|shift(?:s|ing|ed)?|mov(?:e|es|ing|ed)|break(?:s|ing)?|broke|drift(?:s|ing|ed)?|trend(?:s|ing|ed)?) (?:toward|towards|to)\b|\bflock(?:s|ing|ed)? to\b|\breturn(?:s|ing|ed)? to\b|\brall(?:y|ies|ying|ied) (?:behind|to|around)\b|\bwarm(?:s|ing|ed)? (?:up )?to\b|\bgain(?:s|ing|ed)? ground\b|\bmake inroads\b|\bmaking inroads\b", 1.8)]
OUTCOMES = [(re.compile(pattern, re.I), score) for pattern, score in OUTCOME_RULES]
TOPIC_TERMS = {
 'economy': ('economy','economic','inflation','prices','cost of living','jobs','unemployment','wages','tariff','tariffs','tax','taxes','gas','grocery','groceries','recession','housing','rent','affordability','trade','strike'),
 'immigration': ('immigration','immigrant','immigrants','migrant','migrants','deportation','deportations','border','asylum','noncitizen','noncitizens','citizenship','ice'),
 'abortion': ('abortion','abortions','reproductive','roe','planned parenthood','contraception','ivf'),
 'healthcare': ('healthcare','health care','medicare','medicaid','insurance','hospital','hospitals','doctors','nurses','affordable care','drug prices','gender care','gender-affirming care'),
 'democracy': ('democracy','democratic institutions','voting rights','voting system','voting crimes','voter','voters','election','elections','midterm','midterms','authoritarian','no kings','constitution','supreme court','ballot','gerrymander','political violence'),
 'crime': ('crime','crimes','criminal','police','shooting','shootings','violence','murder','fentanyl','drugs','prison','prisons','prisoners','public safety'),
 'foreign_policy': ('ukraine','russia','russian','putin','china','chinese','israel','israeli','gaza','iran','war','military','defense','sanctions','foreign policy','nato','diplomacy','palestinian','palestinians'),
 'education': ('education','school','schools','teachers','college','colleges','university','universities','tuition','student','students','student loan','student loans','curriculum'),
 'climate': ('climate','pollution','extreme weather','hurricane','hurricanes','wildfire','wildfires','renewable','emissions','clean energy','warming','environmental')}
TOPIC_PATTERNS = {key: [(term, re.compile(r'\b'+re.escape(term)+r'\b', re.I)) for term in terms] for key, terms in TOPIC_TERMS.items()}

# Racial-group coverage. An explicit group mention makes an article fully
# relevant to that group; issue terms that disproportionately affect a group
# add partial relevance (0.25 per distinct term, capped at 0.5).
GROUP_NAMES = {
 'hispanic': r"\b(?:latin[oax]s?|latine|hispanics?|mexican[- ]americans?|puerto ricans?|cuban[- ]americans?|tejan[oa]s?)\b",
 'black': r"\b(?:black (?:voters?|americans?|communit(?:y|ies)|men|women|residents|families|churches|lawmakers|caucus|turnout|support)|african[- ]americans?|hbcus?|naacp)\b",
 'asian': r"\b(?:asian[- ]americans?|asian (?:voters?|communit(?:y|ies)|residents)|aapi|(?:chinese|indian|vietnamese|filipino|korean)[- ]americans?)\b",
 'white': r"\b(?:white (?:voters?|americans?|working[- ]class|evangelicals?|men|women|suburban\w*|rural\w*|non[- ]college\w*|college[- ]educated)|working[- ]class whites?)\b",
 'native': r"\b(?:native americans?|american indians?|alaska natives?|tribal|tribes?|indigenous|navajo)\b",
}
GROUP_ISSUES = {
 'hispanic': ('immigration', 'immigrant', 'immigrants', 'deportation', 'deportations', 'deport', 'deported', 'raid', 'raids',
              'border', 'asylum', 'daca', 'dreamers', 'tps', 'migrant', 'migrants', 'sanctuary', 'ICE'),
 'black': ('police', 'policing', 'civil rights', 'voting rights act', 'voting rights', 'racial gerrymander', 'racial gerrymandering',
           'dei', 'diversity, equity', 'affirmative action', 'racism', 'racist', 'reparations', 'juneteenth', 'callais', 'majority-black'),
 'asian': ('anti-asian', 'h-1b', 'chinatown', 'affirmative action', 'student visas'),
 'native': ('reservation', 'reservations', 'indian health service'),
 'white': (),
}
GROUP_NAME_PATTERNS = {g: re.compile(p, re.I) for g, p in GROUP_NAMES.items()}
# "ICE" is only the agency when capitalized.
GROUP_ISSUE_PATTERNS = {g: [(t, re.compile(r'\b'+re.escape(t)+r'\b', 0 if t.isupper() else re.I)) for t in terms]
                        for g, terms in GROUP_ISSUES.items()}
REFERENDUM_WEIGHT = 0.5
INCUMBENT_PARTY = 'republican'  # holds the White House and both chambers in 2026
SENTENCE = re.compile(r'(?<=[.!?;])\s+|\n+')


@lru_cache(maxsize=None)
def group_features(text):
    """Per-group relevance and party direction (+ favors D, - favors R).

    Direction uses the local party-outcome rules on sentences about the group.
    When no party is attributed but the passage covers a group-affecting policy
    issue (raids, deportations, voting rights...), the sign comes from VADER tone
    under an incumbent-party referendum assumption: negative policy coverage
    counts against the governing party, at half weight. A bare group mention
    with no party cue stays neutral.
    """
    sentences = [s for s in SENTENCE.split(text or '') if s.strip()]
    result = {}
    for group, pattern in GROUP_NAME_PATTERNS.items():
        named = [s for s in sentences if pattern.search(s)]
        issues = sorted({t for t, p in GROUP_ISSUE_PATTERNS[group] for s in sentences if p.search(s)})
        if not named and not issues:
            continue
        relevance = 1.0 if named else min(.5, .25 * len(issues))
        about = [s for s in sentences if pattern.search(s) or any(p.search(s) for _, p in GROUP_ISSUE_PATTERNS[group])]
        passage = ' '.join(about)
        tones, evidence = _party_tone(passage)
        direction, kind = None, 'neutral'
        if tones['democratic'] is not None or tones['republican'] is not None:
            direction = max(-1.0, min(1.0, (tones['democratic'] or 0) - (tones['republican'] or 0)))
            kind = 'party_attributed'
        elif issues:
            # Only policy coverage (raids, deportations, voting rights...) is
            # attributed to the governing party; a mere group mention is not.
            policy = ' '.join(s for s in about if any(p.search(s) for _, p in GROUP_ISSUE_PATTERNS[group]))
            compound = _analyzer().polarity_scores(policy)['compound']
            if abs(compound) >= .05:
                sign = 1 if INCUMBENT_PARTY == 'republican' else -1
                direction, kind = -sign * REFERENDUM_WEIGHT * compound, 'incumbent_referendum'
        result[group] = {'relevance': relevance, 'direction': direction, 'kind': kind, 'named': bool(named),
                         'issue_terms': issues, 'cues': [c['cue'] for p in evidence.values() for c in p][:6]}
    return result


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
    def handle_data(self, data):
        self.parts.append(data)
    def handle_starttag(self, tag, attrs):
        if tag in {'p','br','li','div'}:
            self.parts.append('. ')


def plain_text(value):
    parser = _TextExtractor()
    parser.feed(html.unescape(value or ''))
    value = re.sub(r'\bContinue reading\.{0,3}', '', ' '.join(parser.parts), flags=re.I)
    return re.sub(r'\s+', ' ', value).strip(' .')[:12000]


@lru_cache(maxsize=1)
def _analyzer():
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    except ImportError:
        module_path = Path(__file__).resolve().parents[1]/'.dependencies'/'vaderSentiment'/'vaderSentiment.py'
        if not module_path.exists():
            raise RuntimeError('News scoring requires vaderSentiment. Run python -m pip install -r requirements.txt')
        spec = importlib.util.spec_from_file_location('_election_vader', module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        SentimentIntensityAnalyzer = module.SentimentIntensityAnalyzer
    return SentimentIntensityAnalyzer()


def _normalized(value):
    return value/math.sqrt(value*value+15) if value else 0.0


def _party_tone(text):
    """Nearest named entity per outcome cue, in sentence/contrast clauses.

    A reporting verb has no valence. Mixed-party clauses without an attributable
    cue abstain. Distances are capped at ten tokens; preceding negation reverses
    valence at 0.74 strength. This transparent heuristic has not been trained.
    """
    evidence = {'democratic': [], 'republican': []}
    for clause in re.split(r'[.!?;\n]+|\b(?:but|while|whereas|although|as)\b', text, flags=re.I):
        tokens = list(TOKEN.finditer(clause.lower()))
        if not tokens:
            continue
        entities = []
        for party, pattern in PARTIES.items():
            for match in pattern.finditer(clause):
                index = next((i for i,t in enumerate(tokens) if t.start() >= match.start()), 0)
                entities.append((index,party,match.group()))
        if not entities:
            continue
        occupied = set()
        candidates = [(match,valence) for pattern,valence in OUTCOMES for match in pattern.finditer(clause)]
        # Long phrases consume their tokens before individual words; "losing
        # support" is one negative cue, not a loss plus positive support.
        for match,valence in sorted(candidates,key=lambda item:-(item[0].end()-item[0].start())):
                index = next((i for i,t in enumerate(tokens) if t.start() >= match.start()), 0)
                span = {i for i,t in enumerate(tokens) if t.start()<match.end() and t.end()>match.start()}
                if occupied.intersection(span):
                    continue
                occupied.update(span)
                distance,_,party,entity_index,name = min((abs(index-i),i>index,p,i,n) for i,p,n in entities)
                if distance > 10:
                    continue
                parties = {p for _,p,_ in entities}
                if len(parties)>1 and index>max(i for i,_,_ in entities) and re.search(r'\band\b',clause[:match.start()],re.I):
                    continue
                context = [t.group().lower() for t in tokens[max(0,index-3):index]]
                negated = any(t in NEGATIONS or t.endswith("n't") for t in context)
                score = valence*(-0.74 if negated else 1)
                cue = match.group().lower()
                # Bare support is often a noun ("complicated his support").
                # Require a verb context or an immediately following recipient.
                if cue=='support' and not ('to' in context or entity_index>index and distance<=2):
                    continue
                if cue in {'back','backs','backing','endorse','endorses','endorsed','endorsement'} and entity_index>index and distance>3:
                    continue
                # Adverbial "back" ("back in the White House") is not support.
                if cue=='back' and re.match(r"\s+(?:in|into|to|on|from|home|then)\b",clause[match.end():],re.I):
                    continue
                # Retrospective election victories are background, not current
                # electoral news. A date cutoff alone cannot remove backstory.
                if cue=='won' and re.search(r'\b(?:two|four|six|\d+) years? after\b|\b202[024]\b',clause,re.I):
                    continue
                # "Democrats defeat Republicans": positive actor, negative object.
                if cue in {'defeat','defeats','block','blocks','rejects'} and len(parties)>1:
                    before = [(index-i,p,i,n) for i,p,n in entities if i<index]
                    after = [(i-index,p,i,n) for i,p,n in entities if i>index]
                    if before and after:
                        left,right = min(before),min(after)
                        evidence[left[1]].append({'cue':cue,'valence':-score,'entity':left[3],'negated':negated,'clause':clause.strip()[:300]})
                        evidence[right[1]].append({'cue':cue,'valence':score,'entity':right[3],'negated':negated,'clause':clause.strip()[:300]})
                        continue
                evidence[party].append({'cue':cue,'valence':score,'entity':name,'negated':negated,'clause':clause.strip()[:300]})
    tones = {}
    for party,cues in evidence.items():
        unique = {(c['cue'],c['valence'],c['entity'].lower()):c for c in cues}
        evidence[party] = list(unique.values())
        tones[party] = round(_normalized(sum(c['valence'] for c in unique.values())),6) if unique else None
    return tones,evidence


def analyze_content(text, title=''):
    title,content = plain_text(title),plain_text(text)
    combined = title+('. '+content if content and content!=title else '')
    analyzer = _analyzer()
    scores = analyzer.polarity_scores(combined)
    matched = sorted({w.lower() for w in TOKEN.findall(combined) if w.lower() in analyzer.lexicon})
    tones,evidence = _party_tone(combined)
    topic_hits = {k:[term for term,pattern in patterns if pattern.search(combined)] for k,patterns in TOPIC_PATTERNS.items()}
    topics = {k:min(1.0,len(terms)/3) for k,terms in topic_hits.items() if terms}
    covered = bool(matched)
    status = 'positive' if scores['compound']>=.05 else 'negative' if scores['compound']<=-.05 else 'neutral_or_mixed' if covered else 'no_lexicon_matches'
    count = sum(value is not None for value in tones.values())
    explanation = (f"Title and RSS summary: {scores['compound']:+.4f} ({status}); {len(matched)} sentiment terms. "
        f"{count} party target(s) with local outcome evidence. Missing target tone means attribution abstained.")
    return {'sentiment':float(scores['compound']),'compound':float(scores['compound']),
        'dem_tone':tones['democratic'],'rep_tone':tones['republican'],'topics':topics,
        'topic_terms':{k:v for k,v in topic_hits.items() if v},'coverage':covered,'coverage_status':status,
        'method':METHOD,'matched_terms':matched,'target_evidence':evidence,'explanation':explanation,
        'content':content,'positive':scores['pos'],'negative':scores['neg'],'neutral':scores['neu']}


def score_headline(title):
    result = analyze_content('',title=title)
    d,r = bool(DEMOCRATIC.search(title)),bool(REPUBLICAN.search(title))
    return result['sentiment'],'democratic' if d and not r else 'republican' if r and not d else 'unassigned'


def ensure_news_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS news_analysis (
        news_id TEXT PRIMARY KEY REFERENCES news_items(news_id) ON DELETE CASCADE,
        content TEXT NOT NULL,compound REAL NOT NULL CHECK(compound BETWEEN -1 AND 1),
        dem_tone REAL,rep_tone REAL,topics TEXT NOT NULL,coverage INTEGER NOT NULL,
        coverage_status TEXT NOT NULL,matched_terms TEXT NOT NULL,explanation TEXT NOT NULL,
        details TEXT NOT NULL,method TEXT NOT NULL,analyzed_at TEXT NOT NULL)''')


def _save_analysis(conn, news_id, result):
    conn.execute('''INSERT INTO news_analysis VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(news_id) DO UPDATE SET content=excluded.content,compound=excluded.compound,
        dem_tone=excluded.dem_tone,rep_tone=excluded.rep_tone,topics=excluded.topics,
        coverage=excluded.coverage,coverage_status=excluded.coverage_status,
        matched_terms=excluded.matched_terms,explanation=excluded.explanation,
        details=excluded.details,method=excluded.method,analyzed_at=excluded.analyzed_at''',
        (news_id,result['content'],result['compound'],result['dem_tone'],result['rep_tone'],
        json.dumps(result['topics'],sort_keys=True),int(result['coverage']),result['coverage_status'],
        json.dumps(result['matched_terms']),result['explanation'],json.dumps(result,sort_keys=True),result['method'],utc_now()))


def normalize_date(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:
        dt = parsedate_to_datetime(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec='seconds')


def _description(item):
    return plain_text(item.findtext('{http://purl.org/rss/1.0/modules/content/}encoded') or item.findtext('description') or '')


def _url_key(url):
    parsed = urlsplit(url)
    query = [(k,v) for k,v in parse_qsl(parsed.query) if not k.startswith('utm_') and k not in {'at_medium','at_campaign'}]
    return urlunsplit((parsed.scheme,parsed.netloc.lower(),parsed.path.rstrip('/'),urlencode(query),''))


def fetch_news(conn,feed_url=DEFAULT_FEED,cache_dir=Path('data/cache'),race_id=None,source_name='BBC US & Canada RSS',per_item_source=False):
    if urlparse(feed_url).scheme not in {'https','http'}:
        raise ValueError('RSS URL must use HTTP(S)')
    if race_id and not conn.execute('SELECT 1 FROM races WHERE race_id=?',(race_id,)).fetchone():
        raise ValueError('Unknown race_id')
    request = Request(feed_url,headers={'User-Agent':'ElectionResearch/0.2 (local aggregate research)'})
    with urlopen(request,timeout=40) as response:
        raw = response.read(5_000_001)
    if len(raw)>5_000_000:
        raise ValueError('RSS response exceeds 5 MB limit')
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True,exist_ok=True)
    digest = hashlib.sha256(raw).hexdigest()
    (cache_dir/f'news_{digest[:16]}.xml').write_bytes(raw)
    (cache_dir/f'news_{digest[:16]}.metadata.json').write_text(json.dumps({'url':feed_url,'retrieved_at':utc_now(),'sha256':digest},indent=2),encoding='utf-8')
    parsed,skipped,inserted,updated = 0,0,0,0
    ensure_news_schema(conn)
    with conn:
        conn.execute('''INSERT INTO sources VALUES (?,?,?,?,?,?) ON CONFLICT(source_id)
            DO UPDATE SET name=excluded.name,url=excluded.url,retrieved_at=excluded.retrieved_at,notes=excluded.notes''',
            ('news-feed:'+hashlib.sha256(feed_url.encode()).hexdigest()[:16],source_name,feed_url,utc_now(),'news_feed',
            'RSS title and summary snapshots. VADER affect, separate local party-outcome rules and issue dictionaries; not voter sentiment. Raw XML and checksum metadata cached.'))
        for item in ET.fromstring(raw).findall('.//item'):
            title,url,published = plain_text(item.findtext('title') or ''),item.findtext('link') or '',item.findtext('pubDate') or ''
            publisher = source_name
            if per_item_source and (item.findtext('source') or '').strip():
                # Aggregators carry the original outlet; balance by outlet, not by query.
                publisher = plain_text(item.findtext('source'))
                title = re.sub(r'\s+-\s+'+re.escape(publisher)+r'\s*$','',title)
            if not title or urlparse(url).scheme not in {'https','http'} or not published:
                skipped += 1
                continue
            try:
                published = normalize_date(published)
            except (ValueError,TypeError,OverflowError):
                skipped += 1
                continue
            description = '' if per_item_source else _description(item)
            result = analyze_content(description,title=title)
            d,r = bool(DEMOCRATIC.search(title)),bool(REPUBLICAN.search(title))
            party = 'democratic' if d and not r else 'republican' if r and not d else 'unassigned'
            existing = conn.execute('SELECT news_id FROM news_items WHERE url=? AND race_id IS ? LIMIT 1',(url,race_id)).fetchone()
            news_id = existing[0] if existing else hashlib.sha256((url+'|'+str(race_id)).encode()).hexdigest()
            if existing:
                conn.execute('UPDATE news_items SET published_at=?,fetched_at=?,title=?,source_name=?,sentiment=?,method=? WHERE news_id=?',
                    (published,utc_now(),title,publisher,result['sentiment'],METHOD,news_id))
                updated += 1
            else:
                conn.execute('INSERT INTO news_items VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (news_id,race_id,published,utc_now(),title,url,publisher,party,result['sentiment'],METHOD))
                inserted += 1
            _save_analysis(conn,news_id,result)
            parsed += 1
    return {'inserted':inserted,'updated':updated,'parsed':parsed,'skipped':skipped,'source_url':feed_url,'method':METHOD}


GROUP_QUERIES = ('"Latino voters"', '"Hispanic voters"', 'Latino immigration deportations midterms',
    '"Black voters"', 'Black voters "voting rights"', '"Asian American voters"', '"Native American voters"',
    '"white voters" midterms', '"working-class" white voters', 'ICE raids midterm election', '"Voting Rights Act" redistricting 2026',
    'DEI civil rights midterms')


def fetch_group_news(conn,cache_dir=Path('data/cache'),days=30,pause=2.0):
    """Google News searches for coverage of racial groups and racial issues."""
    import time
    from urllib.parse import quote_plus
    report = {'queries':[],'inserted':0,'errors':[]}
    for index,query in enumerate(GROUP_QUERIES):
        url = f'https://news.google.com/rss/search?q={quote_plus(query+f" when:{days}d")}&hl=en-US&gl=US&ceid=US:en'
        try:
            item = fetch_news(conn,feed_url=url,cache_dir=cache_dir,source_name='Google News: '+query,per_item_source=True)
            report['queries'].append({'query':query,'inserted':item['inserted'],'parsed':item['parsed']})
            report['inserted'] += item['inserted']
        except (ValueError,OSError) as exc:
            report['errors'].append({'query':query,'error':str(exc)})
        if index < len(GROUP_QUERIES)-1:
            time.sleep(pause)
    return report


def rescore_news(conn,cache_dir=Path('data/cache')):
    """Backfill existing database summaries from cached snapshots, offline."""
    ensure_news_schema(conn)
    summaries = {}
    for path in sorted(Path(cache_dir).glob('news_*.xml')):
        try:
            root = ET.fromstring(path.read_bytes())
        except (ET.ParseError,OSError):
            continue
        for item in root.findall('.//item'):
            content,key = _description(item),_url_key(item.findtext('link') or '')
            if content and len(content)>len(summaries.get(key,'')):
                summaries[key] = content
    rows = conn.execute('''SELECT n.news_id,n.title,n.url,a.content,n.sentiment,n.party,n.method,a.method
        FROM news_items n LEFT JOIN news_analysis a USING(news_id)''').fetchall()
    counts = {'rescored':0,'with_summary':0,'nonzero_count':0,'covered_count':0,'attributed_count':0}
    with conn:
        for news_id,title,url,existing,manual_score,party,original_method,analysis_method in rows:
            content = summaries.get(_url_key(url),existing or '')
            result = analyze_content(content,title=title)
            # A refresh must not silently replace an explicitly imported score.
            imported = analysis_method and analysis_method.endswith('; imported_score_with_topic_features')
            if imported and manual_score is not None:
                result['compound'] = result['sentiment'] = manual_score
                result['dem_tone'] = manual_score if party=='democratic' else None
                result['rep_tone'] = manual_score if party=='republican' else None
                result['method'] = analysis_method
                result['explanation'] = 'Explicit imported score retained during refresh; RSS content features updated. '+result['explanation']
            conn.execute('UPDATE news_items SET sentiment=?,method=? WHERE news_id=?',
                (result['sentiment'],original_method if imported else METHOD,news_id))
            _save_analysis(conn,news_id,result)
            counts['rescored'] += 1
            counts['with_summary'] += bool(content)
            counts['nonzero_count'] += bool(result['sentiment'])
            counts['covered_count'] += result['coverage']
            counts['attributed_count'] += result['dem_tone'] is not None or result['rep_tone'] is not None
    return counts | {'method':METHOD}


def news_signals(conn,race,as_of,days=30):
    """Bounded party/content signals with UTC publication-date cutoffs."""
    if not 1<=days<=366:
        raise ValueError('days must be between 1 and 366')
    cutoff = date.fromisoformat(as_of[:10])
    start = cutoff-timedelta(days=days-1)
    race_id = race if isinstance(race,str) else race['race_id']
    state = None if isinstance(race,str) else race['state']
    ensure_news_schema(conn)
    cursor = conn.execute('''SELECT n.*,a.content,a.compound,a.dem_tone,a.rep_tone,a.topics,a.coverage,
        a.coverage_status,a.matched_terms,a.explanation,a.details FROM news_items n
        LEFT JOIN news_analysis a USING(news_id) WHERE (n.race_id=? OR n.race_id IS NULL)
        AND date(n.published_at) BETWEEN ? AND ? ORDER BY n.published_at DESC,n.news_id''',
        (race_id,start.isoformat(),cutoff.isoformat()))
    columns = [item[0] for item in cursor.description]
    rows = [dict(zip(columns,row)) for row in cursor.fetchall()]
    from .races import STATES
    by_publisher,articles,seen = {},[],set()
    for row in rows:
        key = _url_key(row['url'])
        if key in seen:
            continue
        seen.add(key)
        if row['compound'] is None:
            result = analyze_content('',title=row['title'])
            if row['method'] not in {METHOD,'headline_lexicon_v1; entity mention is not target sentiment'} and row['sentiment'] is not None:
                result['compound'] = row['sentiment']
                if row['party'] in PARTIES:
                    result['dem_tone' if row['party']=='democratic' else 'rep_tone'] = row['sentiment']
            row.update(compound=result['compound'],dem_tone=result['dem_tone'],rep_tone=result['rep_tone'],
                topics=json.dumps(result['topics']),coverage=int(result['coverage']),coverage_status=result['coverage_status'],
                matched_terms=json.dumps(result['matched_terms']),explanation=result['explanation'],content=result['content'])
        topics = json.loads(row['topics'])
        article_text = row['title']+' '+(row['content'] or '')
        states = {postal for postal,data in STATES.items() if re.search(r'\b'+re.escape(data['name'])+r'\b',article_text,re.I)}
        relevance = 1.0 if not state or row['race_id'] or not states or state in states else .25
        age = (cutoff-date.fromisoformat(row['published_at'][:10])).days
        weight = 2**(-age/14)*relevance
        d,r = row['dem_tone'],row['rep_tone']
        direction = max(-1.0,min(1.0,(d or 0)-(r or 0))) if d is not None or r is not None else None
        row.update(topics=topics,matched_terms=json.loads(row['matched_terms']),direction=direction,
            relevance=relevance,weight=weight,state_mentions=sorted(states),sentiment=row['compound'],
            groups=group_features(row['title']+'. '+(row['content'] or '')))
        articles.append(row)
        by_publisher.setdefault(row['source_name'],[]).append(row)

    def mean_publishers(value,include=lambda row:True):
        means = []
        for publisher_rows in by_publisher.values():
            selected = [row for row in publisher_rows if include(row)]
            denominator = sum(row['weight'] for row in selected)
            if denominator:
                means.append(sum(row['weight']*value(row) for row in selected)/denominator)
        return sum(means)/len(means) if means else None

    difference = mean_publishers(lambda row:row['direction'],lambda row:row['direction'] is not None)
    groups = {}
    for group in GROUP_NAMES:
        scored = [row for row in articles if row['groups'].get(group,{}).get('direction') is not None]
        relevant = [row for row in articles if group in row['groups']]
        # Weight each article by recency x geography x group relevance, then
        # average publishers equally, as for the national party signal.
        by_pub = {}
        for row in scored:
            by_pub.setdefault(row['source_name'],[]).append(row)
        means = []
        for rows_ in by_pub.values():
            w = [row['weight']*row['groups'][group]['relevance'] for row in rows_]
            if sum(w):
                means.append(sum(wi*row['groups'][group]['direction'] for wi,row in zip(w,rows_))/sum(w))
        groups[group] = {'signal': sum(means)/len(means) if means else None, 'articles': len(scored),
            'relevant_articles': len(relevant), 'publishers': len(means),
            'party_attributed': sum(row['groups'][group]['kind']=='party_attributed' for row in scored),
            'incumbent_referendum': sum(row['groups'][group]['kind']=='incumbent_referendum' for row in scored),
            'salience': len(relevant)/len(articles) if articles else 0.0,
            'top_articles': [{'title':row['title'],'source':row['source_name'],'direction':row['groups'][group]['direction'],
                              'kind':row['groups'][group]['kind']} for row in sorted(scored,key=lambda r:-r['weight']*abs(r['groups'][group]['direction']))[:5]]}
    topics = {topic:mean_publishers(lambda row,topic=topic:(row['direction'] or 0)*row['topics'].get(topic,0)) or 0.0 for topic in TOPIC_TERMS}
    salience = {topic:mean_publishers(lambda row,topic=topic:row['topics'].get(topic,0)) or 0.0 for topic in TOPIC_TERMS}
    return {'start':start.isoformat(),'as_of':as_of,'difference':difference,
        'democratic_mean':mean_publishers(lambda row:row['dem_tone'],lambda row:row['dem_tone'] is not None),
        'republican_mean':mean_publishers(lambda row:row['rep_tone'],lambda row:row['rep_tone'] is not None),
        'topics':topics,'topic_salience':salience,'groups':groups,'article_count':len(articles),
        'nonzero_count':sum(bool(row['compound']) for row in articles),'covered_count':sum(bool(row['coverage']) for row in articles),
        'attributed_count':sum(row['direction'] is not None for row in articles),'attributed_article_count':sum(row['direction'] is not None for row in articles),
        'publisher_count':len(by_publisher),'party_article_counts':{p:sum(row['dem_tone' if p=='democratic' else 'rep_tone'] is not None for row in articles) for p in PARTIES},
        'articles':articles,'method':METHOD,
        'weighting':'Equal publisher mean; recency half-life 14 days; other-state-only articles receive 0.25 relevance. Party direction clips D tone minus R tone to [-1,1]; a missing counterpart contributes zero, not an imputed observation.',
        'limitation':'RSS affect and local outcome rules are observational news features. They do not measure voter opinion or establish demographic response.'}


def import_news(conn,path):
    values = []
    with open(path,encoding='utf-8-sig',newline='') as stream:
        for index,row in enumerate(csv.DictReader(stream),2):
            try:
                party = row['party']
                if party not in {'democratic','republican','unassigned'}:
                    raise ValueError('Invalid party')
                sentiment = float(row['sentiment']) if row.get('sentiment','')!='' else None
                if sentiment is not None and (not math.isfinite(sentiment) or not -1<=sentiment<=1):
                    raise ValueError('sentiment must be between -1 and 1')
                if urlparse(row['url']).scheme not in {'https','http'}:
                    raise ValueError('Invalid article URL')
                if not row['title'] or not row['source_name'] or not row['method']:
                    raise ValueError('title, source_name and method are required')
                race_id = row.get('race_id') or None
                if race_id and not conn.execute('SELECT 1 FROM races WHERE race_id=?',(race_id,)).fetchone():
                    raise ValueError('Unknown race_id')
                values.append((hashlib.sha256((row['url']+'|'+party).encode()).hexdigest(),race_id,
                    normalize_date(row['published_at']),utc_now(),row['title'],row['url'],row['source_name'],party,sentiment,row['method']))
            except (ValueError,KeyError,TypeError) as exc:
                raise ValueError(f'{path}, row {index}: {exc}') from exc
    ensure_news_schema(conn)
    with conn:
        conn.executemany('''INSERT INTO news_items VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(news_id)
            DO UPDATE SET race_id=excluded.race_id,published_at=excluded.published_at,fetched_at=excluded.fetched_at,
            title=excluded.title,url=excluded.url,source_name=excluded.source_name,party=excluded.party,
            sentiment=excluded.sentiment,method=excluded.method''',values)
        for row in values:
            result = analyze_content('',title=row[4])
            if row[8] is not None:
                result['compound'] = result['sentiment'] = row[8]
                result['dem_tone'] = row[8] if row[7]=='democratic' else None
                result['rep_tone'] = row[8] if row[7]=='republican' else None
                result['method'] = row[9]+'; imported_score_with_topic_features'
                result['explanation'] = 'Explicit imported score; title issue features. '+result['explanation']
            _save_analysis(conn,row[0],result)
    return {'imported':len(values)}
