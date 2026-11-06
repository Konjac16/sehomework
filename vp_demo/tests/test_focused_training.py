"""Recovered focused-training contract; isolated database, no external calls."""
import json
import sqlite3
import threading
import unittest
from unittest.mock import Mock

import server
import test_training as daily_tests
from training_service import TrainingService, DEFAULTS, profile_table, recommendation_table

problem=daily_tests.problem


class FocusedTests(unittest.TestCase):
    setUp=daily_tests.TrainingTests.setUp
    catalog=daily_tests.TrainingTests.catalog
    records=daily_tests.TrainingTests.records

    def setup_directions(self, selected=None):
        self.catalog([problem('1900A',tags=['dp']),problem('1900B',tags=['graphs']),
                      problem('1900C',tags=['math']),problem('1900D',tags=['strings'])])
        return self.s.preferences(1,{'mode':'focused','directions':selected or ['dp','graphs']})

    def finish(self, key, mode, completion='independent'):
        self.catalog([problem(key,tags=['dp'])])
        if mode=='focused':self.s.preferences(1,{'mode':mode,'directions':['dp']})
        self.s.recommend(1,{'mode':mode})
        run=self.s.act(1,'start',{'mode':mode,'problemKey':key})['active']
        self.now+=5;self.records(problem(key,tags=['dp']))
        # Feedback from the other tab must still go to the origin rating.
        return self.s.act(1,'feedback',{'mode':'focused' if mode=='daily' else 'daily',
                         'id':run['id'],'completionMode':completion})

    def test_direction_selection_limits_and_or_semantics(self):
        self.setup_directions()
        for dirs in ([],['dp']*2,['dp','graphs','math','strings'],['invented'],['dp',True],'dp'):
            with self.assertRaises(ValueError):self.s.preferences(1,{'mode':'focused','directions':dirs})
        with self.assertRaises(ValueError):self.s.preferences(1,{'mode':'daily','directions':['dp']})
        result=self.s.recommend(1,{'mode':'focused'})
        self.assertIn(result['recommendation']['problemKey'],['1900A','1900B'])
        self.assertNotIn('tags',result['recommendation'])
        self.assertEqual(result['history'],[])

    def test_empty_selection_does_not_guess_a_direction(self):
        self.setup_directions()
        state=self.s.recommend(2,{'mode':'focused'})
        self.assertIsNone(state['recommendation'])
        self.assertEqual(state['preferences']['directions'],[])
        self.assertIn('dp',state['directionOptions'])

    def test_equal_direction_chance_not_weighted_by_problem_counts(self):
        self.catalog([problem(str(i)+'A',tags=['dp']) for i in range(20)]+[problem('2000A',tags=['graphs'])])
        self.s.preferences(1,{'mode':'focused','directions':['dp','graphs']})
        rng=Mock()
        def choice(items):
            if isinstance(items[0],str):
                self.assertEqual(items,['dp','graphs']);return 'graphs'
            self.assertEqual(len(items),1);return items[0]
        rng.choice.side_effect=choice;self.s.rng=rng
        self.assertEqual(self.s.recommend(1,{'mode':'focused'})['recommendation']['problemKey'],'2000A')
        rng.choices.assert_not_called()

    def test_unavailable_directions_do_not_expand_scope(self):
        self.setup_directions(['dp','graphs'])
        self.catalog([problem('1900A',2200,['dp']),problem('1900B',1800,['graphs']),problem('1900C',1800,['math'])])
        self.assertEqual(self.s.recommend(1,{'mode':'focused'})['recommendation']['problemKey'],'1900B')
        self.s.act(1,'dismiss',{'mode':'focused','problemKey':'1900B'})
        self.assertIsNone(self.s.recommend(1,{'mode':'focused'})['recommendation'])

    def test_settings_mode_selection_and_recommendations_are_independent(self):
        self.setup_directions(['dp'])
        daily=self.s.recommend(1)['recommendation']
        self.s.preferences(1,{'mode':'focused','difficultyMode':'manual','minRating':1700,'maxRating':1900})
        focused=self.s.recommend(1,{'mode':'focused'})['recommendation']
        self.s.select_mode(1,{'mode':'focused'})
        self.assertEqual(self.s.state(1)['mode'],'focused')
        self.assertEqual(self.s.state(1)['range'],[1700,1900])
        self.assertEqual(self.s.state(1,'daily')['range'],[1600,2000])
        self.assertEqual(self.s.state(1,'daily')['recommendation'],daily)
        self.assertEqual(self.s.state(1,'focused')['recommendation'],focused)
        self.assertEqual(self.s.state(2)['mode'],'daily')
        with self.assertRaises(ValueError):self.s.select_mode(1,{'mode':'intelligent'})

    def test_switch_retains_active_and_prevents_two_starts(self):
        self.setup_directions(['dp']);self.s.recommend(1)
        p=self.s.recommend(1,{'mode':'focused'})['recommendation']
        run=self.s.act(1,'start',{'mode':'focused','problemKey':p['problemKey']})['active']
        result=self.s.select_mode(1,{'mode':'daily'})
        self.assertEqual(result['active']['id'],run['id'])
        self.assertEqual(result['active']['mode'],'focused')
        with self.assertRaises(ValueError):self.s.act(1,'start',{'mode':'daily','problemKey':'1900B'})
        self.assertEqual(len(self.s.state(1)['history']),1)

    def test_ratings_and_three_feedback_windows_are_independent(self):
        self.finish('1900A','daily');self.finish('1900B','daily');self.finish('1900C','focused')
        self.assertEqual(self.s.state(1,'daily')['feedbackWindow'],2)
        self.assertEqual(self.s.state(1,'focused')['feedbackWindow'],1)
        self.assertEqual(self.s.state(1)['ratings'],{'daily':1800,'focused':1800})
        self.finish('1900D','focused');self.finish('1900E','focused')
        self.assertEqual(self.s.state(1)['ratings'],{'daily':1800,'focused':1900})
        self.assertEqual(self.s.state(1,'daily')['feedbackWindow'],2)
        self.finish('1900F','daily')
        self.assertEqual(self.s.state(1)['ratings'],{'daily':1900,'focused':1900})

    def test_official_rating_resets_both_and_excludes_old_epochs(self):
        self.finish('1900A','focused');self.finish('1900B','daily')
        self.s.preferences(1,{'mode':'focused','difficultyMode':'manual','minRating':1500,'maxRating':1900})
        self.records(problem('1950Z'),profile={'rating':2100})
        for mode in ('daily','focused'):
            s=self.s.state(1,mode)
            self.assertEqual(s['referenceRating'],2100)
            self.assertEqual(s['feedbackWindow'],0)
        self.assertEqual(self.s.state(1,'focused')['range'],[1500,1900])
        with server.connect() as c:c.execute('UPDATE training_focused_profiles SET reference=2200 WHERE user_id=1')
        self.records(problem('1950Y'),profile={'rating':2100})
        self.assertEqual(self.s.state(1)['ratings'],{'daily':2100,'focused':2200})

    def test_shared_cooldown_ac_and_attempted_records(self):
        self.catalog([problem(tags=['dp'])]);self.s.preferences(1,{'mode':'focused','directions':['dp']})
        self.s.recommend(1);self.s.recommend(1,{'mode':'focused'})
        self.s.act(1,'dismiss',{'mode':'daily','problemKey':'1900A'})
        self.assertIsNone(self.s.state(1,'focused')['recommendation'])
        self.assertIsNone(self.s.recommend(1,{'mode':'focused'})['recommendation'])
        self.now+=86400
        self.s.recommend(1,{'mode':'focused'})
        run=self.s.act(1,'start',{'mode':'focused','problemKey':'1900A'})['active']
        self.s.act(1,'skip',{'id':run['id']});self.now+=86400
        self.assertIsNone(self.s.recommend(1)['recommendation'])
        self.s.preferences(1,{'includeAttempted':True})
        self.assertIsNotNone(self.s.recommend(1)['recommendation'])
        self.records(problem())
        self.assertIsNone(self.s.state(1)['recommendation'])
        self.assertEqual(self.s.state(1)['pending'][0]['mode'],'focused')

    def test_cross_mode_resume_retains_first_training_owner_and_single_feedback(self):
        self.catalog([problem(tags=['dp'])]);self.s.preferences(1,{'mode':'focused','directions':['dp']})
        self.s.recommend(1,{'mode':'focused'})
        run=self.s.act(1,'start',{'mode':'focused','problemKey':'1900A'})['active']
        self.s.act(1,'skip',{'id':run['id']});self.now+=86400
        self.s.preferences(1,{'mode':'daily','includeAttempted':True})
        self.s.recommend(1)
        resumed=self.s.act(1,'start',{'mode':'daily','problemKey':'1900A'})['active']
        self.assertEqual((resumed['id'],resumed['mode']),(run['id'],'focused'))
        self.records(problem())
        self.s.act(1,'feedback',{'id':run['id'],'mode':'daily','completionMode':'hint'})
        self.assertEqual(self.s.state(1,'focused')['feedbackWindow'],1)
        self.assertEqual(self.s.state(1,'daily')['feedbackWindow'],0)
        with self.assertRaises(ValueError):self.s.act(1,'feedback',{'id':run['id'],'completionMode':'hint'})

    def test_concurrent_cross_mode_start_keeps_one_active(self):
        self.setup_directions(['dp']);self.s.recommend(1);self.s.recommend(1,{'mode':'focused'})
        choices={m:self.s.state(1,m)['recommendation']['problemKey'] for m in ('daily','focused')}
        barrier=threading.Barrier(2);results=[]
        def start(mode):
            barrier.wait()
            try:self.s.act(1,'start',{'mode':mode,'problemKey':choices[mode]});results.append('ok')
            except ValueError:results.append('blocked')
        threads=[threading.Thread(target=start,args=(m,)) for m in choices]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(sorted(results),['blocked','ok'])

    def test_migration_preserves_daily_data_and_is_repeatable(self):
        self.s.state(1)
        with server.connect() as c:
            c.execute('UPDATE training_profiles SET reference=1950,epoch=7 WHERE user_id=1')
            before=dict(c.execute('SELECT * FROM training_profiles WHERE user_id=1').fetchone())
            TrainingService.init_tables(c);TrainingService.init_tables(c)
            self.assertEqual(before,dict(c.execute('SELECT * FROM training_profiles WHERE user_id=1').fetchone()))
        # Exercise the actual pre-mode schema migration, not just a newly created DB.
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
        c.executescript("CREATE TABLE users(id INTEGER PRIMARY KEY); CREATE TABLE training_runs(id INTEGER PRIMARY KEY,user_id INTEGER,status TEXT); INSERT INTO training_runs VALUES(42,1,'skipped');")
        TrainingService.init_tables(c);TrainingService.init_tables(c)
        self.assertEqual(tuple(c.execute('SELECT id,status,mode FROM training_runs').fetchone()),(42,'skipped','daily'))
        c.close()


if __name__=='__main__':unittest.main()
