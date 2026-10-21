"""Read-only localhost dashboard. Network fetching and imports are explicit CLI jobs."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import csv
import io
import json
import gzip
import mimetypes
import os
import socket
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlparse
from datetime import date
from .db import connect, RACE_GROUPS, MODEL_RACE_GROUPS, SEXES, COARSE_EDUCATION, LIMITATIONS
from .analysis import historical_comparison, scenario
from .forecasts import get_prediction,forecast_overview,coarse_demographics,sensitivity

WEB=Path(__file__).resolve().parents[1]/'web'
AS_OF='2026-10-21'

def summary(conn,as_of=AS_OF):
    tables={'races':'races','demographic_cells':'demographics','historical_cells':'historical_cells','news_items':'news_items','scenario_runs':'model_runs'}
    counts={name:conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for name,table in tables.items()}
    counts['forecasts']=conn.execute('SELECT COUNT(*) FROM race_forecasts WHERE as_of=?',(as_of,)).fetchone()[0]
    counts['demographic_predictions']=conn.execute('SELECT COUNT(*) FROM demographic_forecasts WHERE as_of=?',(as_of,)).fetchone()[0]
    counts['published_historical_cells']=conn.execute('SELECT COUNT(*) FROM historical_cells WHERE dem_share IS NOT NULL AND sample_size>=30 AND (effective_n IS NULL OR effective_n>=30)').fetchone()[0]
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='news_analysis'").fetchone():
        counts['news_scored']=conn.execute('SELECT COUNT(*) FROM news_analysis WHERE compound IS NOT NULL').fetchone()[0]
        counts['news_nonzero']=conn.execute('SELECT COUNT(*) FROM news_analysis WHERE ABS(compound)>0.00001').fetchone()[0]
        counts['news_attributed']=conn.execute('SELECT COUNT(*) FROM news_analysis WHERE dem_tone IS NOT NULL OR rep_tone IS NOT NULL').fetchone()[0]
    sources=[dict(r) for r in conn.execute('SELECT * FROM sources ORDER BY kind,name')]
    # Race roster citations are carried on each row; include them in provenance UI.
    for row in conn.execute('SELECT DISTINCT source_url,office FROM races'):
        sources.append({'name':f"2026 {row['office'].title()} roster",'url':row['source_url'],'kind':'race_roster','notes':'Verified seat-level roster; candidates are not yet loaded.','retrieved_at':AS_OF})
    metadata=conn.execute('SELECT model,validation FROM forecast_metadata WHERE as_of=?',(as_of,)).fetchone()
    return {'cycle':2026,'as_of':as_of,'counts':counts,'offices':[dict(r) for r in conn.execute('SELECT office,COUNT(*) AS count FROM races GROUP BY office')],
            'sources':sources,'limitations':LIMITATIONS,'model':json.loads(metadata['model']) if metadata else {'status':'not_built'},
            'validation':json.loads(metadata['validation']) if metadata else {}}

def race_detail(conn,race_id,as_of=AS_OF):
    raw=conn.execute('SELECT * FROM races WHERE race_id=?',(race_id,)).fetchone()
    if raw is None:
        raise LookupError('Race not found')
    race=dict(raw)
    raw_demographics=coarse_demographics(conn,race['geography_id'])
    projection=get_prediction(conn,race_id,as_of)
    if projection is None:
        projection={'status':'not_built','message':'Run python main.py predict to fit and generate every race forecast.',
                    'groups':[],'dem_share':None,'news_feature':{}}
    demographics=[]
    for g in projection.get('groups',[]):
        demographics.append({**g,'acs_year':2024,'universe':'Resident population age 25+, population weighting proxy',
                             'boundary_vintage':'119th Congress (2024 ACS)',
                             'moe':g.get('moe')})
    if not demographics:
        demographics=raw_demographics
    from .news import ensure_news_schema
    ensure_news_schema(conn)
    news=[]
    for raw in conn.execute('''SELECT n.*,a.compound,a.topics,a.coverage,a.explanation,
        a.content,a.dem_tone,a.rep_tone,a.coverage_status,a.method AS analysis_method
        FROM news_items n LEFT JOIN news_analysis a USING(news_id)
        WHERE (n.race_id=? OR n.race_id IS NULL) AND date(n.published_at)<=?
        ORDER BY n.published_at DESC LIMIT 80''',(race_id,as_of)):
        item=dict(raw)
        if isinstance(item.get('topics'),str):
            item['topics']=json.loads(item['topics'])
        news.append(item)
    history=historical_comparison(conn,race['geography_id'],race['office'])
    aliases={k:k+'_non_hispanic' for k in RACE_GROUPS}
    for h in history:
        if h.get('race_scheme')=='ces_hispanic_exclusive':
            h['race_group']=aliases.get(h['race_group'],h['race_group'])
    return {'race':race,'demographics':demographics,'raw_demographics':raw_demographics,'history':history,'forecasts':projection['groups'],'news':news,'projection':projection,
        'coverage':{'demographic_cells':len(demographics),'expected_demographic_cells':32,
                    'history_cells':len(history),'actual_local_history_cells':sum(r['sample_size']>0 for r in history),
                    'acs_year':demographics[0]['acs_year'] if demographics else None,
                    'population_universe':'Residents age 25+; not eligible voters',
                    'district_boundaries_verified':race['office']!='house'}}

def serve(db_path,host='127.0.0.1',port=8000,as_of=AS_OF):
    if host not in {'127.0.0.1','localhost','::1'}:
        raise ValueError('This research server is designed for localhost. Use 127.0.0.1 or localhost.')
    if not date(2026,1,1)<=date.fromisoformat(as_of)<date(2026,11,3):
        raise ValueError('Dashboard cutoff must be a date in 2026 before November 3')
    class Handler(BaseHTTPRequestHandler):
        def respond(self,data,status=200,content_type='application/json; charset=utf-8'):
            if isinstance(data,(dict,list)):
                data=json.dumps(data,allow_nan=False).encode('utf-8')
            if isinstance(data,str):
                data=data.encode('utf-8')
            compressed=len(data)>1024 and 'gzip' in self.headers.get('Accept-Encoding','').lower()
            if compressed:
                data=gzip.compress(data,compresslevel=5)
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(data)))
            if compressed:
                self.send_header('Content-Encoding','gzip')
            self.send_header('Vary','Accept-Encoding')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed=urlparse(self.path)
            params=parse_qs(parsed.query)
            def arg(k,default=''):
                return params.get(k,[default])[0]
            try:
                if parsed.path.startswith('/api/'):
                    conn=connect(db_path)
                    try:
                        if parsed.path=='/api/summary':
                            payload=summary(conn,as_of)
                        elif parsed.path=='/api/groups':
                            races=MODEL_RACE_GROUPS
                            payload={key:[{'value':k,'label':v} for k,v in values.items()]
                                     for key,values in [('race_groups',races),('sexes',SEXES),('education',COARSE_EDUCATION)]}
                        elif parsed.path=='/api/races':
                            where=[]
                            values=[]
                            for field in ('office','state'):
                                if arg(field):
                                    where.append(f'{field}=?')
                                    values.append(arg(field))
                            if arg('q'):
                                where.append('(label LIKE ? OR race_id LIKE ?)')
                                values.extend(['%'+arg('q')+'%']*2)
                            query='SELECT * FROM races'+(' WHERE '+' AND '.join(where) if where else '')+' ORDER BY state,office,district,race_id'
                            payload={'races':[dict(r) for r in conn.execute(query,values)]}
                        elif parsed.path=='/api/race':
                            payload=race_detail(conn,arg('id'),arg('as_of',as_of))
                        elif parsed.path=='/api/predictions':
                            payload=forecast_overview(conn,arg('as_of',as_of),arg('office') or None)
                        elif parsed.path=='/api/scenario':
                            race=conn.execute('SELECT * FROM races WHERE race_id=?',(arg('id'),)).fetchone()
                            if race is None:
                                raise LookupError('Race not found')
                            payload=sensitivity(conn,dict(race),as_of=arg('as_of',as_of),
                                coefficient_pp=float(arg('coefficient_pp','0')),assumed_shift_pp=float(arg('assumed_shift_pp','0')))
                        elif parsed.path=='/api/export/forecasts':
                            from .forecasts import forecast_table,TABLE_COLUMNS
                            stream=io.StringIO()
                            writer=csv.DictWriter(stream,fieldnames=TABLE_COLUMNS)
                            writer.writeheader()
                            writer.writerows(forecast_table(conn,arg('as_of',as_of),arg('office') or None))
                            self.respond(stream.getvalue(),content_type='text/csv; charset=utf-8')
                            return
                        elif parsed.path=='/api/export/history':
                            # Export only aggregate comparisons; no respondent-level rows.
                            race=conn.execute('SELECT * FROM races WHERE race_id=?',(arg('id'),)).fetchone()
                            if race is None:
                                raise LookupError('Race not found')
                            rows=historical_comparison(conn,race['geography_id'],race['office'])
                            stream=io.StringIO()
                            if rows:
                                writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
                                writer.writeheader()
                                writer.writerows(rows)
                            self.respond(stream.getvalue(),content_type='text/csv; charset=utf-8')
                            return
                        else:
                            raise LookupError('API endpoint not found')
                    finally:
                        conn.close()
                    self.respond(payload)
                    return
                files={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/styles.css':'styles.css',
                       '/maps/us-states.json':'maps/us-states.json'}
                if parsed.path not in files:
                    raise LookupError('File not found')
                path=WEB/files[parsed.path]
                self.respond(path.read_bytes(),content_type=mimetypes.guess_type(path.name)[0]+'; charset=utf-8')
            except LookupError as exc:
                self.respond({'error':str(exc)},404)
            except (ValueError,TypeError) as exc:
                self.respond({'error':str(exc)},400)
            except (sqlite3.Error,OSError) as exc:
                self.log_error('%s',exc)
                self.respond({'error':'Database or file unavailable. Run python main.py init and inspect the server log.'},500)

    class Server(ThreadingHTTPServer):
        # On Windows, SO_REUSEADDR lets a second server bind an occupied port,
        # so a stale process with old code can silently answer requests.
        allow_reuse_address=os.name!='nt'
        def server_bind(self):
            if os.name=='nt' and hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
                self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            super().server_bind()
    try:
        server=Server((host,port),Handler)
    except OSError as exc:
        raise OSError(f'Port {port} is already in use. Stop the other dashboard server or pass --port.') from exc
    print(f'Election research dashboard: http://{host}:{port}',flush=True)
    print('Press Ctrl+C to stop. Data updates run through the CLI.',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
