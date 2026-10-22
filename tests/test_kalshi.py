import unittest

from electiondb.candidates import general_candidates
from electiondb.kalshi import choose_pick, market_race, market_rows, pick_name, points


class PickTests(unittest.TestCase):
    def test_points_are_log_half_of_price(self):
        self.assertAlmostEqual(points(.5),1)
        self.assertAlmostEqual(points(.25),2)

    def test_clear_favorite_is_automatic_even_when_underdog_is_cheap(self):
        pick=choose_pick(.95,{'D':(.5,'Dem'),'R':(.02,'Rep')})
        self.assertEqual((pick['pick'],pick['reason']),('D','model_auto'))

    def test_cheap_underdog_is_picked_when_expected_points_clear_the_ratio(self):
        # 0.30 x log2(1/0.03) = 1.52 against 0.70 x log2(1/0.97) = 0.03
        pick=choose_pick(.7,{'D':(.97,'Dem'),'R':(.03,'Rep')})
        self.assertEqual((pick['pick'],pick['reason'],pick['candidate']),('R','underdog_value','Rep'))
        self.assertAlmostEqual(pick['model_probability'],.3)

    def test_favorite_is_kept_when_underdog_is_fairly_priced(self):
        pick=choose_pick(.7,{'D':(.7,'Dem'),'R':(.3,'Rep')})
        self.assertEqual((pick['pick'],pick['reason']),('D','favorite_value'))

    def test_strong_third_candidate_and_missing_market_keep_the_favorite(self):
        self.assertEqual(choose_pick(.31,{'D':(.01,'Dem'),'R':(.84,'Rep'),'O':.16})['reason'],'third_candidate')
        self.assertEqual(choose_pick(.4,{})['reason'],'no_market')


class MarketMappingTests(unittest.TestCase):
    SENATE={'Kentucky':'2026-senate-KY-regular','Minnesota':'2026-senate-MN-regular'}

    def market(self,ticker,rules):
        return {'ticker':ticker,'status':'active','yes_sub_title':'Name','yes_bid_dollars':'0.40','yes_ask_dollars':'0.42','rules_primary':rules}

    def test_race_and_party_come_from_rules_not_ticker(self):
        rows=market_rows([self.market('SENATELA-26-R','If a representative of the Republican party is sworn in as a Senator of Kentucky for the term beginning in 2027, then the market resolves to Yes.'),
                          self.market('SENATEMN-26-D','If a representative of the Democratic (DFL) party is sworn in as a Senator of Minnesota for the term beginning in 2027, then the market resolves to Yes.'),
                          self.market('KXHOUSENC11-26-DEM','If the House member sworn in for NC-11 for the term beginning in 2027 is a member of the Democratic Party, then the market resolves to Yes.'),
                          self.market('HOUSEAKAL-26-BHIL','If Bill Hill is sworn in for AK-AL for the term beginning in 2027, then the market resolves to Yes.')],
                         self.SENATE,'now')
        self.assertEqual([(r[0],r[2]) for r in rows],[('2026-senate-KY-regular','R'),('2026-senate-MN-regular','D'),
                                                     ('2026-house-NC-11','D'),('2026-house-AK-00','O')])
        self.assertAlmostEqual(rows[0][7],.41)

    def test_unrecognized_rules_are_skipped(self):
        self.assertIsNone(market_race({'rules_primary':'Will turnout exceed 50%?'},self.SENATE))


class NameTests(unittest.TestCase):
    def test_wikipedia_spelling_confirmed_by_kalshi(self):
        self.assertEqual(pick_name('Nick Begich',['Nick Begich III']),('Nick Begich III','confirmed'))

    def test_disagreement_and_gaps_are_labeled(self):
        self.assertEqual(pick_name('Darline Graham',['Lindsey Graham']),('Lindsey Graham','wikipedia_differs'))
        self.assertEqual(pick_name('Republican party',['Dan Sullivan']),('Dan Sullivan','wikipedia'))
        self.assertEqual(pick_name('Jane Doe',[]),('Jane Doe','kalshi_only'))
        self.assertEqual(pick_name('Democratic party',[]),('','missing'))

    def test_kalshi_picks_between_same_party_candidates(self):
        self.assertEqual(pick_name('Ro Khanna',['Someone Else','Ro Khanna']),('Ro Khanna','confirmed'))

    def test_general_election_box_parsed_and_primary_skipped(self):
        text='''{{Election box begin no change|title=Republican primary results}}
{{Election box candidate with party link no change|party=Republican Party (United States)|candidate=Loser|votes=1}}
{{Election box end}}
{{Election box begin|title=2026 Iowa's 1st congressional district election}}
{{Election box candidate with party link|party=Republican Party (United States)|candidate=[[Mariannette Miller-Meeks]] (incumbent)|votes=}}
{{Election box candidate with party link|party=Democratic Party (United States)|candidate=[[Christina Bohannan]]<ref>x</ref>|votes=}}
{{Election box candidate with party link|party=Independent|candidate=Michael Bridgford|votes=}}
{{Election box end}}'''
        (_,title,rows),=general_candidates(text)
        self.assertIn('1st congressional district',title)
        self.assertEqual(rows,[('R','Mariannette Miller-Meeks',True),('D','Christina Bohannan',False),('O','Michael Bridgford',False)])


if __name__=='__main__':
    unittest.main()
