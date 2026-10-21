import csv
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from electiondb.db import SCHEMA, RACE_GROUPS, SEXES, EDUCATION, initialize
from electiondb.analysis import historical_comparison, news_feature, scenario
from electiondb.importers import import_history
from electiondb.news import score_headline
from electiondb.races import race_rows
from electiondb.census import _integer


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:')
        self.conn.row_factory=sqlite3.Row
        initialize(self.conn)
        self.conn.execute("INSERT INTO sources VALUES ('test','Synthetic test fixture','https://example.org/test','2025-01-01','test','Never production data')")
        self.race=dict(self.conn.execute("SELECT * FROM races WHERE race_id='2026-senate-NC-regular'").fetchone())

    def tearDown(self):
        self.conn.close()

    def cell(self,geo='us',p=.6,n=100,neff=80,source='test',race='white',sex='female',education='bachelors_plus',scheme='census_race_any_ethnicity',universe='survey_voters_age18_plus'):
        self.conn.execute('INSERT OR REPLACE INTO historical_cells VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (geo,2024,'senate',race,sex,education,p,n,neff,float(n),'weighted_survey',scheme,universe,'2024',source))

    def test_roster_is_unique_complete_and_idempotent(self):
        rows=race_rows()
        self.assertEqual(len(rows),506)
        self.assertEqual(len({r['race_id'] for r in rows}),506)
        self.assertEqual(sum(r['office']=='house' for r in rows),435)
        self.assertEqual(sum(r['office']=='senate' for r in rows),35)
        self.assertEqual(sum(r['office']=='governor' for r in rows),36)
        self.assertIn('cd:0200',{r['geography_id'] for r in rows})
        self.assertEqual(initialize(self.conn),0)

    def test_history_matches_national_source_and_suppresses_small_cells(self):
        self.cell()
        self.cell(geo='state:37',p=.8,n=15,neff=12)
        result=historical_comparison(self.conn,'state:37','senate')
        self.assertEqual(len(result),1)
        self.assertIsNone(result[0]['dem_share'])
        self.assertIsNone(result[0]['gap_pp'])
        self.assertEqual(result[0]['national_dem_share'],.6)
        self.cell(geo='state:37',p=.7,n=70,neff=50)
        self.assertAlmostEqual(historical_comparison(self.conn,'state:37','senate')[0]['gap_pp'],10)

    def test_empty_local_history_is_not_invented_from_national(self):
        self.cell()
        result=historical_comparison(self.conn,'state:37','senate')
        self.assertIsNone(result[0]['dem_share'])
        self.assertEqual(result[0]['national_dem_share'],.6)
        self.assertEqual(result[0]['sample_size'],0)

    def test_effective_sample_size_matters(self):
        self.cell(n=200,neff=10)
        result=scenario(self.conn,self.race)
        self.assertEqual(result['status'],'missing_history')
        self.assertEqual(result['groups'],[])

    def test_news_cutoff_and_ambiguous_entity_attribution(self):
        for i,day,party,score in [(1,'2026-10-15','democratic',.5),(2,'2026-10-16','republican',-.5),
                                  (3,'2026-10-22','democratic',-1),(4,'2026-08-01','republican',1)]:
            self.conn.execute('INSERT INTO news_items VALUES (?,?,?,?,?,?,?,?,?,?)',
                (str(i),None,day+'T12:00:00+00:00',day,'Fixture','https://example.org/'+str(i),'Test fixture',party,score,'test'))
        result=news_feature(self.conn,self.race['race_id'],'2026-10-21')
        self.assertEqual(result['article_count'],2)
        self.assertEqual(result['difference'],1)
        self.assertEqual(score_headline('Democrats and Republicans gain in survey')[1],'unassigned')

    def test_scenario_uses_explicit_shift_without_fabricating_total_or_interval(self):
        self.cell()
        result=scenario(self.conn,self.race,assumed_shift_pp=5,persist=True)
        self.assertAlmostEqual(result['groups'][0]['projected_dem_share'],.65)
        self.assertEqual(result['groups'][0]['baseline_geography'],'us')
        self.assertIsNone(result['dem_share'])
        self.assertIsNone(result['lower'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM model_runs').fetchone()[0],1)
        with self.assertRaises(ValueError):
            scenario(self.conn,self.race,as_of='2026-11-03')

    def test_aggregate_requires_all_compatible_eligible_electorate_cells(self):
        for race in RACE_GROUPS:
            for sex in SEXES:
                for education in EDUCATION:
                    self.cell(race=race,sex=sex,education=education)
                    self.conn.execute('INSERT INTO turnout_assumptions VALUES (?,?,?,?,?,?,?,?,?,?)',
                        ('state:37',2026,race,sex,education,100,.5,'eligible_voters_age18_plus','2026','test'))
        result=scenario(self.conn,self.race)
        self.assertAlmostEqual(result['dem_share'],.6)
        self.assertEqual(result['status'],'unvalidated_aggregate_scenario')
        self.conn.execute("UPDATE turnout_assumptions SET boundary_vintage='2024' WHERE race_group='white'")
        self.assertIsNone(scenario(self.conn,self.race)['dem_share'])

    def test_alternative_race_definitions_never_weight_census_population(self):
        self.cell(scheme='ces_hispanic_exclusive')
        result=scenario(self.conn,self.race)
        self.assertIsNone(result['dem_share'])
        self.assertEqual(result['groups'][0]['race_scheme'],'ces_hispanic_exclusive')

    def test_scenario_uses_one_source_for_local_cells_and_national_fallbacks(self):
        self.conn.execute("INSERT INTO sources VALUES ('other','Second synthetic fixture','https://example.org/other','2025-01-01','test','Never production data')")
        self.cell(geo='us',p=.6,source='test')
        self.cell(geo='state:37',p=.7,source='test',race='black')
        self.cell(geo='us',p=.2,n=300,neff=200,source='other')
        self.cell(geo='state:37',p=.3,n=300,neff=200,source='other')

        automatic=scenario(self.conn,self.race)
        self.assertTrue(automatic['groups'])
        self.assertEqual({g['source_id'] for g in automatic['groups']},{automatic['source_id']})
        explicit=scenario(self.conn,self.race,source_id='test')
        self.assertEqual({g['source_id'] for g in explicit['groups']},{'test'})
        self.assertEqual({g['race_group'] for g in explicit['groups']},{'white','black'})
        self.assertEqual({g['race_group']:g['baseline_geography'] for g in explicit['groups']},
                         {'white':'us','black':'state:37'})
        unavailable=scenario(self.conn,self.race,source_id='missing')
        self.assertEqual(unavailable['status'],'missing_history')
        self.assertEqual(unavailable['groups'],[])

    def test_resident_age25_turnout_is_rejected_on_import_and_by_scenario(self):
        from electiondb.importers import import_turnout

        fields=['geography_id','cycle','race_group','sex','education','eligible_population',
                'turnout','universe','boundary_vintage']
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'residents.csv'
            with path.open('w',newline='',encoding='utf-8') as stream:
                writer=csv.DictWriter(stream,fieldnames=fields)
                writer.writeheader()
                writer.writerow(dict(zip(fields,['state:37',2026,'white','female','bachelors_plus',
                                                  100,.5,'residents_age25_plus','2026'])))
            with self.assertRaisesRegex(ValueError,'eligible_voters_age18_plus'):
                import_turnout(self.conn,path,source_id='residents',source_name='Resident fixture',
                               source_url='https://example.org/residents')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM turnout_assumptions').fetchone()[0],0)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM sources WHERE source_id='residents'").fetchone())

        # Other database writers must not be able to bypass the universe gate.
        for race in RACE_GROUPS:
            for sex in SEXES:
                for education in EDUCATION:
                    self.cell(race=race,sex=sex,education=education)
                    self.conn.execute('INSERT INTO turnout_assumptions VALUES (?,?,?,?,?,?,?,?,?,?)',
                        ('state:37',2026,race,sex,education,100,.5,'residents_age25_plus','2026','test'))
        result=scenario(self.conn,self.race)
        self.assertIsNone(result['dem_share'])
        self.assertEqual(result['status'],'scenario_only')
        self.assertFalse(result['coverage']['compatible_2026_electorate'])
        self.conn.execute("UPDATE turnout_assumptions SET universe='eligible_voters_age18_plus'")
        self.assertAlmostEqual(scenario(self.conn,self.race)['dem_share'],.6)

    def test_house_local_history_requires_2026_boundary_reconciliation_for_aggregation(self):
        house=dict(self.conn.execute("SELECT * FROM races WHERE race_id='2026-house-NC-01'").fetchone())
        for race in RACE_GROUPS:
            for sex in SEXES:
                for education in EDUCATION:
                    self.cell(geo='us',p=.4,race=race,sex=sex,education=education)
                    self.cell(geo=house['geography_id'],p=.6,race=race,sex=sex,education=education)
                    self.conn.execute('INSERT INTO turnout_assumptions VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (house['geography_id'],2026,race,sex,education,100,.5,
                         'eligible_voters_age18_plus','2026','test'))
        # The fixture helper creates Senate rows; this test requires House history.
        self.conn.execute("UPDATE historical_cells SET office='house',boundary_vintage='119th_congress'")
        result=scenario(self.conn,house)
        self.assertIsNone(result['dem_share'])
        self.assertEqual(result['status'],'scenario_only')
        self.assertTrue(result['coverage']['compatible_2026_electorate'])
        self.assertFalse(result['coverage']['compatible_history_boundaries'])
        self.assertEqual({g['baseline_geography'] for g in result['groups']},{house['geography_id']})
        self.assertEqual({g['baseline_boundary_vintage'] for g in result['groups']},{'119th_congress'})

        self.conn.execute("UPDATE historical_cells SET boundary_vintage='2026' WHERE geography_id=?",
                          (house['geography_id'],))
        reconciled=scenario(self.conn,house)
        self.assertEqual(reconciled['status'],'unvalidated_aggregate_scenario')
        self.assertTrue(reconciled['coverage']['compatible_history_boundaries'])
        self.assertAlmostEqual(reconciled['dem_share'],.6)

    def test_import_validates_all_rows_before_mutating(self):
        fields=['geography_id','election_year','office','race_group','sex','education','dem_share','sample_size','universe']
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'invalid.csv'
            with path.open('w',newline='',encoding='utf-8') as stream:
                writer=csv.DictWriter(stream,fieldnames=fields)
                writer.writeheader()
                for p in [.6,2]:
                    writer.writerow(dict(zip(fields,['state:37',2024,'senate','white','female','bachelors_plus',p,100,'survey_voters_age25_plus'])))
            with self.assertRaises(ValueError):
                import_history(self.conn,path,source_id='bad',source_name='Invalid fixture',source_url='https://example.org/bad')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM historical_cells').fetchone()[0],0)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM sources WHERE source_id='bad'").fetchone())

    def test_census_sentinel_is_missing_not_negative_population(self):
        self.assertIsNone(_integer('-666666666'))
        self.assertIsNone(_integer(None))
        self.assertEqual(_integer('0'),0)


if __name__=='__main__':
    unittest.main()
