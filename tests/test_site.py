import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from electiondb.db import initialize
from electiondb.forecasts import save_predictions
from electiondb.server import race_detail
from electiondb.site import export_site, _round

RACE='2026-senate-NC-regular'


class StaticSiteTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:')
        self.conn.row_factory=sqlite3.Row
        initialize(self.conn)
        groups=[{'race_group':'white_non_hispanic','sex':'female','education':edu,'population':pop,
                 'baseline_dem_share':.4,'projected_dem_share':.45,'swing_pp':5.0}
                for edu,pop in [('high_school_or_less',25),('some_college_or_more',75)]]
        save_predictions(self.conn,{'as_of':'2026-10-21','model':{'version':'test-only'},'validation':{},
            'races':[{'race_id':RACE,'office':'senate','state':'NC','status':'experimental_forecast',
                      'dem_share':.4375,'rep_share':.5625,'winner':'R','rating':'lean_r','groups':groups,
                      'news_feature':{'difference':.1,'article_ids':['a','b']}}]})
        self.tmp=tempfile.TemporaryDirectory()
        self.out=Path(self.tmp.name)/'docs'

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_export_writes_static_page_and_every_endpoint(self):
        result=export_site(self.conn,self.out)
        page=(self.out/'index.html').read_text(encoding='utf-8')
        self.assertIn('name="election-static"',page)
        self.assertIn('href="data/forecast_table_2026.csv"',page)
        self.assertTrue((self.out/'.nojekyll').exists())
        for name in ('summary','groups','races','news','predictions-house','predictions-senate','predictions-governor'):
            self.assertTrue((self.out/'data'/f'{name}.json').exists(),name)
        self.assertEqual(len(list((self.out/'data'/'race').glob('*.json'))),result['races'])

    def test_compacted_race_expands_to_the_api_response(self):
        export_site(self.conn,self.out)
        stored=json.loads((self.out/'data'/'race'/f'{RACE}.json').read_text(encoding='utf-8'))
        news=json.loads((self.out/'data'/'news.json').read_text(encoding='utf-8'))['news']
        # Mirrors expandDetail in web/app.js.
        groups=stored['projection']['groups']
        stored.setdefault('forecasts',groups)
        extra=stored.pop('demographics_from_groups',None)
        if extra is not None:
            stored['demographics']=[{**g,**extra} for g in groups]
        if stored.pop('news_shared',False):
            stored['news']=news
        expected=_round(json.loads(json.dumps(race_detail(self.conn,RACE))))
        expected['projection']['news_feature'].pop('article_ids')
        self.assertEqual(stored,expected)

    def test_refuses_to_clear_a_folder_it_did_not_write(self):
        (self.out/'data').mkdir(parents=True)
        (self.out/'data'/'keep.txt').write_text('not ours',encoding='utf-8')
        with self.assertRaises(ValueError):
            export_site(self.conn,self.out)
        self.assertTrue((self.out/'data'/'keep.txt').exists())

    def test_requires_saved_forecasts(self):
        with self.assertRaises(ValueError):
            export_site(self.conn,self.out,as_of='2026-10-01')


if __name__=='__main__':
    unittest.main()
