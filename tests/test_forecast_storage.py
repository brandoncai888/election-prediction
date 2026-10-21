import json
import math
import sqlite3
import unittest

from electiondb.db import initialize
from electiondb.forecasts import save_predictions,get_prediction,forecast_overview,coarse_demographics,sensitivity


class ForecastStorageTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:')
        self.conn.row_factory=sqlite3.Row
        initialize(self.conn)
        self.conn.execute("INSERT INTO sources VALUES ('test','Isolated test fixture','https://example.org/test','2026-01-01','test','Synthetic, never production')")

    def tearDown(self):
        self.conn.close()

    def payload(self):
        groups=[]
        for edu,pop,p0,p1 in [('high_school_or_less',25,.4,.45),('some_college_or_more',75,.6,.62)]:
            groups.append({'race_group':'white_non_hispanic','sex':'female','education':edu,'population':pop,
                           'baseline_dem_share':p0,'projected_dem_share':p1,'swing_pp':100*(p1-p0)})
        return {'as_of':'2026-10-21','model':{'version':'test-only'},'validation':{'scope':'synthetic fixture'},
            'races':[{'race_id':'2026-senate-NC-regular','office':'senate','state':'NC','status':'experimental_forecast',
            'dem_share':.5775,'rep_share':.4225,'baseline_dem_share':.55,'margin_pp':15.5,'swing_pp':2.75,
            'winner':'D','rating':'likely_d','groups':groups,'news_feature':{'difference':.5}}]}

    def test_persistence_is_repeatable_and_exposes_normalized_cohort_weights(self):
        payload=self.payload()
        save_predictions(self.conn,payload)
        save_predictions(self.conn,payload)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM race_forecasts').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM demographic_forecasts').fetchone()[0],2)
        row=self.conn.execute('SELECT SUM(weight),SUM(weight*projected_dem_share) FROM demographic_forecasts').fetchone()
        self.assertAlmostEqual(row[0],1)
        self.assertAlmostEqual(row[1],payload['races'][0]['dem_share'])
        overview=forecast_overview(self.conn,'2026-10-21','senate')
        self.assertEqual(overview['summary']['total'],1)
        self.assertNotIn('groups',overview['races'][0])
        self.assertEqual(get_prediction(self.conn,'2026-senate-NC-regular','2026-10-21')['groups'],payload['races'][0]['groups'])

    def test_education_merge_preserves_counts_without_summing_overlapping_ethnicity_tables(self):
        for race in ('white','hispanic','white_non_hispanic'):
            for edu,n,moe in [('less_than_high_school',10,3),('high_school',20,4),('some_college',30,6),('bachelors_plus',40,8)]:
                self.conn.execute('INSERT INTO demographics VALUES (?,?,?,?,?,?,?,?,?,?)',
                    ('state:37',2024,race,'female',edu,n,moe,'Residents25+','2024','test'))
        rows=coarse_demographics(self.conn,'state:37')
        self.assertEqual(len(rows),2)
        self.assertEqual(sum(r['population'] for r in rows),100)
        self.assertEqual({r['education']:r['moe'] for r in rows},{'high_school_or_less':5,'some_college_or_more':10})

    def test_manual_sensitivity_is_separate_from_the_main_prediction(self):
        save_predictions(self.conn,self.payload())
        race=dict(self.conn.execute("SELECT * FROM races WHERE race_id='2026-senate-NC-regular'").fetchone())
        result=sensitivity(self.conn,race,'2026-10-21',coefficient_pp=2,assumed_shift_pp=1)
        self.assertEqual(result['status'],'manual_scenario')
        self.assertAlmostEqual(result['groups'][0]['projected_dem_share'],.42)
        self.assertAlmostEqual(get_prediction(self.conn,race['race_id'],'2026-10-21')['dem_share'],.5775)
        with self.assertRaises(ValueError):
            sensitivity(self.conn,race,'2026-10-21',coefficient_pp=math.inf)


if __name__=='__main__':
    unittest.main()
