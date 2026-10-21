import csv
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from xml.sax.saxutils import escape

from electiondb.db import initialize
from electiondb.news import (analyze_content, ensure_news_schema, fetch_news,
                             import_news, news_signals, rescore_news)


class NewsFeatureTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        initialize(self.conn)
        self.race = dict(self.conn.execute('SELECT * FROM races WHERE race_id=?',
                         ('2026-senate-NC-regular',)).fetchone())

    def tearDown(self):
        self.conn.close()

    def add(self, news_id='fixture', title='Election briefing', party='unassigned',
            score=0, day='2026-10-20', source='Fixture publisher', race_id=None, url=None):
        self.conn.execute('INSERT INTO news_items VALUES (?,?,?,?,?,?,?,?,?,?)',
            (news_id, race_id, day+'T12:00:00+00:00', '2026-10-21', title,
             url or 'https://example.org/'+news_id, source, party, score, 'manual_fixture'))

    @staticmethod
    def rss(title='Democrats gain support', description='A strong recovery in jobs and wages.'):
        return ('<rss><channel><item><title>'+escape(title)+'</title>'
                '<link>https://example.org/article</link><pubDate>Tue, 20 Oct 2026 12:00:00 GMT</pubDate>'
                '<description>'+escape(description)+'</description></item></channel></rss>').encode()

    def test_numeric_neutral_is_distinct_from_missing_lexical_coverage(self):
        result = analyze_content('The committee meets on Tuesday.')
        self.assertEqual(result['compound'], 0.0)
        self.assertFalse(result['coverage'])
        self.assertEqual(result['coverage_status'], 'no_lexicon_matches')
        self.assertIsNone(result['dem_tone'])
        self.assertIsNone(result['rep_tone'])

    def test_summary_content_is_scored_and_topics_are_extracted(self):
        result = analyze_content('<p>Terrible failures in healthcare and immigration. College tuition prices surge.</p>',
                                 title='Committee briefing')
        self.assertLess(result['compound'], 0)
        self.assertTrue(result['coverage'])
        self.assertIn('healthcare', result['topics'])
        self.assertIn('immigration', result['topics'])
        self.assertIn('education', result['topics'])
        self.assertIn('economy', result['topics'])
        self.assertNotIn('<p>', result['content'])
        # Emotional issue reporting alone does not assign a party target.
        self.assertIsNone(result['dem_tone'])
        self.assertIsNone(result['rep_tone'])

    def test_party_outcomes_respect_negation_and_separate_mixed_targets(self):
        result = analyze_content('Democrats gain support while Republicans lose support.')
        self.assertGreater(result['dem_tone'], 0)
        self.assertLess(result['rep_tone'], 0)
        self.assertLess(analyze_content('Democrats fail.')['dem_tone'], 0)
        self.assertGreater(analyze_content('Democrats did not fail.')['dem_tone'], 0)
        ambiguous = analyze_content('Democrats and Republicans gain in the survey.')
        self.assertIsNone(ambiguous['dem_tone'])
        self.assertIsNone(ambiguous['rep_tone'])

    def test_schedule_contractions_and_old_victories_are_not_current_wins(self):
        for title in ["Trump won't change our vote", 'Trump campaigns ahead of the midterms',
                      'Carney backing to take on Trump', 'Two years after Trump won Kansas, voters meet']:
            with self.subTest(title=title):
                self.assertIsNone(analyze_content('', title=title)['rep_tone'])

    def test_backfill_recovers_summaries_and_preserves_ids_and_original_table_shape(self):
        self.add(title='Committee briefing', url='https://example.org/article')
        ensure_news_schema(self.conn)
        ensure_news_schema(self.conn)
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, 'news_fixture.xml').write_bytes(self.rss(title='Committee briefing',
                description='Republicans lose support after terrible healthcare failures.'))
            result = rescore_news(self.conn, Path(temp))
        self.assertEqual(result['rescored'], 1)
        self.assertEqual(result['with_summary'], 1)
        self.assertEqual(result['nonzero_count'], 1)
        self.assertEqual(result['attributed_count'], 1)
        self.assertEqual(len(self.conn.execute('PRAGMA table_info(news_items)').fetchall()), 10)
        row = self.conn.execute('SELECT n.news_id,n.sentiment,a.content,a.rep_tone FROM news_items n JOIN news_analysis a USING(news_id)').fetchone()
        self.assertEqual(row['news_id'], 'fixture')
        self.assertLess(row['sentiment'], 0)
        self.assertLess(row['rep_tone'], 0)

    def test_refetch_updates_existing_scores_instead_of_ignoring_existing_urls(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch('electiondb.news.urlopen', side_effect=[BytesIO(self.rss()),
                    BytesIO(self.rss(title='Democrats lose support', description='Terrible healthcare failure.'))]):
                first = fetch_news(self.conn, 'https://example.org/feed', Path(temp))
                original_id = self.conn.execute('SELECT news_id FROM news_items').fetchone()[0]
                second = fetch_news(self.conn, 'https://example.org/feed', Path(temp))
            self.assertEqual(len(list(Path(temp).glob('news_*.metadata.json'))), 2)
        self.assertEqual(first['inserted'], 1)
        self.assertEqual(second['updated'], 1)
        self.assertEqual(second['inserted'], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM news_items').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT news_id FROM news_items').fetchone()[0], original_id)
        self.assertLess(self.conn.execute('SELECT dem_tone FROM news_analysis').fetchone()[0], 0)

    def test_cutoffs_race_scope_and_single_party_direction(self):
        self.add('included', party='republican', score=-.5)
        self.add('future', party='democratic', score=-1, day='2026-10-22')
        self.add('stale', party='democratic', score=-1, day='2026-08-01')
        self.add('other-race', party='democratic', score=-1, race_id='2026-house-CA-01')
        result = news_signals(self.conn, self.race, '2026-10-21')
        self.assertEqual(result['article_count'], 1)
        self.assertEqual(result['attributed_article_count'], 1)
        self.assertAlmostEqual(result['difference'], .5)
        self.assertIsNone(result['democratic_mean'])

    def test_publishers_are_balanced_and_tracking_urls_are_deduplicated(self):
        self.add('a', party='democratic', score=1, source='A')
        self.add('b', party='democratic', score=1, source='A')
        self.add('c', party='democratic', score=-1, source='B')
        self.add('duplicate', party='democratic', score=1, source='A', url='https://example.org/a?utm_source=rss')
        result = news_signals(self.conn, self.race, '2026-10-21')
        self.assertEqual(result['article_count'], 3)
        self.assertEqual(result['publisher_count'], 2)
        self.assertAlmostEqual(result['difference'], 0)

    def test_other_state_articles_have_reduced_geographic_relevance(self):
        self.add('texas', title='Texas Republicans fail', party='republican', score=-1)
        self.add('nc', title='North Carolina Republicans win', party='republican', score=1)
        result = news_signals(self.conn, self.race, '2026-10-21')
        self.assertAlmostEqual(result['difference'], -.6)
        self.assertEqual({a['news_id']:a['relevance'] for a in result['articles']}, {'texas':.25,'nc':1.0})

    def test_manual_import_preserves_explicit_score_and_adds_content_features(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, 'scores.csv')
            with path.open('w', newline='', encoding='utf-8') as stream:
                fields = ['title','url','published_at','source_name','party','sentiment','method']
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerow(dict(zip(fields, ['Democrats address inflation','https://example.org/manual',
                    '2026-10-20','Research fixture','democratic',-.7,'human_test_score'])))
            import_news(self.conn, path)
            rescore_news(self.conn, Path(temp))
        result = news_signals(self.conn, self.race, '2026-10-21')
        self.assertAlmostEqual(result['difference'], -.7)
        self.assertGreater(result['topic_salience']['economy'], 0)


if __name__ == '__main__':
    unittest.main()
