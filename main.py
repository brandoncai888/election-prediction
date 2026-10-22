"""2026 US midterm demographic research database and dashboard."""
import argparse
import json
from pathlib import Path
import sys
from urllib.error import URLError
from electiondb.db import DEFAULT_DB, ROOT, connect, initialize

def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',type=Path,default=DEFAULT_DB,help='SQLite database path')
    subs=p.add_subparsers(dest='command')
    subs.add_parser('init',help='Create database and source-verified 2026 race roster')
    subs.add_parser('status',help='Show data coverage and provenance')
    census=subs.add_parser('fetch-census',help='Fetch real Census ACS joint demographic cells')
    census.add_argument('--year',type=int,default=2024)
    census.add_argument('--scope',choices=['all','states','districts','national'],default='all')
    survey=subs.add_parser('fetch-survey',help='Download and aggregate documented historical voting survey')
    survey.add_argument('--year',type=int,default=2024,choices=[2022,2024])
    news=subs.add_parser('fetch-news',help='Fetch current RSS headlines and transparent sentiment features')
    news.add_argument('--url',help='Custom RSS feed; defaults to BBC, NPR Politics, and Guardian US Politics')
    news.add_argument('--race-id')
    news.add_argument('--source-name',default='Custom RSS feed')
    subs.add_parser('fetch-group-news',help='Fetch Google News coverage of racial groups and racial issues')
    elastic=subs.add_parser('build-elasticity',help='Build demographic response ratios from the CES midterm analysis summary')
    elastic.add_argument('--summary',type=Path)
    subs.add_parser('fetch-anchors',help='Download actual 2024 presidential results by district and state (baseline anchors)')
    subs.add_parser('fetch-polls',help='Fetch generic-ballot aggregator averages (Wikipedia 2026 House page)')
    kalshi=subs.add_parser('fetch-kalshi',help='Fetch Kalshi House/Senate prices and rewrite the submission picks')
    kalshi.add_argument('--as-of',default='2026-10-21')
    roster=subs.add_parser('fetch-candidates',help='Fetch 2026 general-election candidates from Wikipedia and rewrite the submission names')
    roster.add_argument('--as-of',default='2026-10-21')
    polls=subs.add_parser('import-polls',help='Import generic-ballot averages CSV; see templates/polls.csv')
    polls.add_argument('csv',type=Path)
    subs.add_parser('build-midterm-inputs',help='Freeze midterm out-party gains and CES validated turnout by demographic cell')
    subs.add_parser('build-news-effect',help='Fit the group news coefficient on 2010-2022 midterm coverage and CES group swings')
    table=subs.add_parser('forecast-table',help='Write the per-race win probability / margin table as CSV')
    table.add_argument('--as-of',default='2026-10-21')
    table.add_argument('--out',type=Path,default=ROOT/'data'/'forecast_table_2026.csv')
    subs.add_parser('rescore-news',help='Rescore saved news titles and summaries with the current analyzer')
    train=subs.add_parser('train-model',help='Fit and validate the 2024 demographic vote-choice baseline')
    train.add_argument('--force',action='store_true',help='Refit even if a compatible artifact exists')
    for name in ('predict','refresh'):
        item=subs.add_parser(name,help='Generate all race forecasts' if name=='predict' else 'Refresh news, rescore, and regenerate every race forecast')
        item.add_argument('--as-of',default='2026-10-21')
    for name in ('history','turnout','results'):
        item=subs.add_parser('import-'+name,help=f'Import validated {name} CSV; see templates/')
        item.add_argument('csv',type=Path)
        item.add_argument('--source-id',required=True)
        item.add_argument('--source-name',required=True)
        item.add_argument('--source-url',required=True)
    item=subs.add_parser('import-news',help='Import article-level attributed sentiment CSV')
    item.add_argument('csv',type=Path)
    projection=subs.add_parser('scenario',help='Run and save an explicitly unvalidated sentiment sensitivity scenario')
    projection.add_argument('--race-id',required=True)
    projection.add_argument('--as-of',default='2026-10-21')
    projection.add_argument('--baseline-year',type=int)
    projection.add_argument('--source-id',help='Use one explicit historical survey source')
    projection.add_argument('--coefficient-pp',type=float,default=0)
    projection.add_argument('--assumed-shift-pp',type=float,default=0)
    site=subs.add_parser('export-site',help='Write the dashboard as static files for GitHub Pages')
    site.add_argument('--out',type=Path,default=ROOT/'docs',help='Output folder (default: docs/)')
    site.add_argument('--as-of',default='2026-10-21')
    server=subs.add_parser('serve',help='Start the local interactive dashboard (default)')
    server.add_argument('--host',default='127.0.0.1')
    server.add_argument('--port',type=int,default=8000)
    server.add_argument('--as-of',default='2026-10-21',help='News cutoff date, before November 3, 2026')
    return p

def main(argv=None):
    args=parser().parse_args(argv)
    conn=connect(args.db)
    try:
        initialize(conn)
        cache=ROOT/'data'/'cache'
        if args.command=='init':
            result={'database':str(args.db),'races':conn.execute('SELECT COUNT(*) FROM races').fetchone()[0]}
        elif args.command=='status':
            from electiondb.server import summary
            result=summary(conn)
        elif args.command=='fetch-census':
            from electiondb.census import fetch_census
            result=fetch_census(conn,year=args.year,scope=args.scope,cache_dir=cache)
        elif args.command=='fetch-survey':
            from electiondb.surveys import fetch_survey
            result=fetch_survey(conn,year=args.year,cache_dir=cache)
        elif args.command=='fetch-news':
            from electiondb.news import fetch_news, DEFAULT_FEEDS
            if args.url:
                result=fetch_news(conn,feed_url=args.url,race_id=args.race_id,source_name=args.source_name,cache_dir=cache)
            else:
                result={'feeds':[],'inserted':0,'errors':[]}
                for name,url in DEFAULT_FEEDS:
                    try:
                        report=fetch_news(conn,feed_url=url,race_id=args.race_id,source_name=name,cache_dir=cache)
                        result['feeds'].append(report)
                        result['inserted']+=report['inserted']
                    except (ValueError,OSError,URLError) as exc:
                        result['errors'].append({'source':name,'error':str(exc)})
        elif args.command=='fetch-group-news':
            from electiondb.news import fetch_group_news
            result=fetch_group_news(conn,cache_dir=cache)
        elif args.command=='build-elasticity':
            from electiondb.elasticity import build_elasticity,DEFAULT_SUMMARY
            result=build_elasticity(args.summary or DEFAULT_SUMMARY)
        elif args.command=='fetch-anchors':
            from electiondb.anchors import fetch_anchors
            result=fetch_anchors(conn,cache_dir=cache)
        elif args.command=='fetch-polls':
            from electiondb.environment import fetch_polls
            result=fetch_polls(conn,cache_dir=cache)
        elif args.command=='fetch-kalshi':
            from electiondb.kalshi import fetch_kalshi
            from electiondb.forecasts import write_bjc_prediction
            result=fetch_kalshi(conn,cache_dir=cache)
            result['bjc_prediction']=write_bjc_prediction(conn,args.as_of,ROOT/'bjc_congress_prediction.csv')
        elif args.command=='fetch-candidates':
            from electiondb.candidates import fetch_candidates
            from electiondb.forecasts import write_bjc_prediction
            result=fetch_candidates(conn,cache_dir=cache)
            result['bjc_prediction']=write_bjc_prediction(conn,args.as_of,ROOT/'bjc_congress_prediction.csv')
        elif args.command=='import-polls':
            from electiondb.environment import import_polls
            result=import_polls(conn,args.csv)
        elif args.command=='build-midterm-inputs':
            from electiondb.environment import build_midterm_inputs
            result=build_midterm_inputs()
        elif args.command=='build-news-effect':
            from electiondb.newseffect import build_news_effect
            result=build_news_effect(cache_dir=cache)
        elif args.command=='forecast-table':
            from electiondb.forecasts import write_forecast_table
            result=write_forecast_table(conn,args.as_of,args.out)
        elif args.command=='rescore-news':
            from electiondb.news import rescore_news
            result=rescore_news(conn,cache_dir=cache)
        elif args.command=='train-model':
            from electiondb.predict import train_model,load_model
            model=train_model(conn,cache_dir=cache) if args.force else load_model(conn,cache_dir=cache)
            result={k:v for k,v in model.items() if k in {'model_version','trained_at','baseline_year','artifact_path','training','fit'}}
            result['validation']={k:v for k,v in model['validation'].items() if k not in {'calibration_bins','offices'}}
        elif args.command in {'predict','refresh'}:
            news_report=None
            if args.command=='refresh':
                from electiondb.news import fetch_news,DEFAULT_FEEDS,rescore_news
                news_report={'feeds':[],'errors':[]}
                for name,url in DEFAULT_FEEDS:
                    try:
                        news_report['feeds'].append(fetch_news(conn,feed_url=url,source_name=name,cache_dir=cache))
                    except (ValueError,OSError,URLError) as exc:
                        news_report['errors'].append({'source':name,'error':str(exc)})
                from electiondb.news import fetch_group_news
                news_report['group_news']=fetch_group_news(conn,cache_dir=cache)
                news_report['errors']+=news_report['group_news']['errors']
                news_report['scoring']=rescore_news(conn,cache_dir=cache)
                from electiondb.environment import fetch_polls
                try:
                    news_report['polls']={k:v for k,v in fetch_polls(conn,cache_dir=cache).items() if k!='rows'}
                except (ValueError,OSError,URLError,KeyError) as exc:
                    news_report['errors'].append({'source':'generic-ballot polls','error':str(exc)})
                from electiondb.kalshi import fetch_kalshi
                try:
                    news_report['kalshi']=fetch_kalshi(conn,cache_dir=cache)
                except (ValueError,OSError,URLError,KeyError) as exc:
                    news_report['errors'].append({'source':'Kalshi markets','error':str(exc)})
            from electiondb.predict import predict_all
            from electiondb.forecasts import save_predictions
            payload=predict_all(conn,as_of=args.as_of,cache_dir=cache)
            result=save_predictions(conn,payload)
            from electiondb.forecasts import write_forecast_table
            result['table']=write_forecast_table(conn,args.as_of,ROOT/'data'/'forecast_table_2026.csv')
            from electiondb.forecasts import write_bjc_prediction
            result['bjc_prediction']=write_bjc_prediction(conn,args.as_of,ROOT/'bjc_congress_prediction.csv')
            result['chambers']={k:{f:v[f] for f in v if f!='distribution'} for k,v in payload['model']['chambers'].items()}
            if news_report:
                result['news_refresh']=news_report
                result['errors']=news_report['errors']
        elif args.command in {'import-history','import-turnout','import-results'}:
            from electiondb import importers
            fn={'import-history':importers.import_history,'import-turnout':importers.import_turnout,'import-results':importers.import_results}[args.command]
            result=fn(conn,args.csv,source_id=args.source_id,source_name=args.source_name,source_url=args.source_url)
        elif args.command=='import-news':
            from electiondb.news import import_news
            result=import_news(conn,args.csv)
        elif args.command=='export-site':
            from electiondb.site import export_site
            result=export_site(conn,args.out,as_of=args.as_of)
        elif args.command=='scenario':
            from electiondb.analysis import scenario
            row=conn.execute('SELECT * FROM races WHERE race_id=?',(args.race_id,)).fetchone()
            if row is None:
                raise ValueError('Unknown race_id')
            result=scenario(conn,dict(row),as_of=args.as_of,baseline_year=args.baseline_year,
                source_id=args.source_id,
                coefficient_pp=args.coefficient_pp,assumed_shift_pp=args.assumed_shift_pp,persist=True)
        else:
            conn.close()
            from electiondb.server import serve
            serve(args.db,host=getattr(args,'host','127.0.0.1'),port=getattr(args,'port',8000),as_of=getattr(args,'as_of','2026-10-21'))
            return 0
        print(json.dumps(result,indent=2,ensure_ascii=True,allow_nan=False))
        return 1 if result.get('errors') else 0
    except (ValueError,OSError,URLError,RuntimeError,ImportError) as exc:
        print(f'Error: {exc}',file=sys.stderr)
        return 1
    finally:
        conn.close()

if __name__=='__main__':
    raise SystemExit(main())
