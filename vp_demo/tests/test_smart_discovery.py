"""Generic development fixtures. User holdout requests are deliberately absent."""
import json
import threading
import unittest
from zipfile import ZipFile
from unittest.mock import patch

import test_intelligent as pool_tests
import test_smart_recommendation as job_tests
from smart_discovery import Discovery, admission_order, matches_tags
from smart_recommendation import SmartRecommendationService
from smart_workflow import validate_intent, bundle
from ai_provider import ProviderError


class DiscoveryTests(unittest.TestCase):
    setUp=pool_tests.PoolPreparationTests.setUp
    connect=pool_tests.PoolPreparationTests.connect
    record=pool_tests.PoolPreparationTests.record

    def test_tag_logic_or_within_and_across_groups_and_exclusions(self):
        intent={'tagGroups':[['graphs','trees'],['dp']], 'excludeTags':['interactive']}
        self.assertTrue(matches_tags(['trees','dp'],intent))
        self.assertFalse(matches_tags(['trees'],intent))
        self.assertFalse(matches_tags(['graphs','dp','interactive'],intent))
        self.assertTrue(matches_tags([],{}))

    def test_quota_counts_only_new_batch_partial_before_empty(self):
        def b(kinds,code=False):return {'reference_code_available':code,'evidence':[{'kind':k,'text':'x'*90} for k in kinds]}
        items={'empty':b([]),'partial':b(['statement'])}
        items.update({f'full{i}':b(['statement','editorial','code'],True) for i in range(7)})
        result=admission_order(items)
        self.assertEqual(len(result),8)
        self.assertEqual(result[-1],'partial')
        self.assertNotIn('empty',result)
        self.assertEqual(admission_order({'empty':b([]),'partial':b(['statement'])}),[])
        self.assertEqual(len(admission_order(items,5)),5)

    def test_discovery_reuses_archive_beyond_pool_and_keeps_public_tags_private(self):
        # A specific word exists only in a reserve statement, not a card.
        with ZipFile(self.pack) as z:files={name:z.read(name) for name in z.namelist()}
        statements=json.loads(files['statements/1.json'])
        statements['A99']['body']='A harbor schedules vessels using a timetable. '*8
        files['statements/1.json']=json.dumps(statements).encode()
        with ZipFile(self.pack,'w') as z:
            for name,raw in files.items():z.writestr(name,raw)
        self.pool.refresh(1);d=Discovery(self.pool);scope=d.prepare(1,lambda:None)
        intent={'skill_queries':[{'zh':'港口船舶时间表','en':'harbor vessels timetable'}], 'tagGroups':[]}
        result,trace=d.search(1,scope,intent,set(),lambda:None)
        self.assertEqual(trace['shortlist'][0],'cf:1:A99')
        self.assertIn('harbor',str(result['cf:1:A99']['evidence']))
        self.assertNotIn('tags',result['cf:1:A99']['problem'])
        with self.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM smart_card_memberships').fetchone()[0],0)
        self.assertNotIn('cf:1:A99',{e['id'] for e in self.pool.recommendation_candidates(1)})

    def test_ac_attempts_and_range_filter_before_discovery(self):
        self.record(verdict='OK',index='A1')
        self.record(sid='2',index='A2')
        scope=Discovery(self.pool).prepare(1,lambda:None)
        self.assertNotIn('cf:1:A1',scope['entries'])
        self.assertNotIn('cf:1:A2',scope['entries'])
        self.pool.settings(1,{'includeAttempted':True})
        self.assertIn('cf:1:A2',Discovery(self.pool).prepare(1,lambda:None)['entries'])
        self.pool.settings(1,{'difficultyMode':'manual','minRating':2000,'maxRating':2400})
        self.assertEqual(Discovery(self.pool).prepare(1,lambda:None)['entries'],{})

    def test_statement_discovery_does_not_use_editorial_mentions(self):
        self.pool.refresh(1);d=Discovery(self.pool);scope=d.prepare(1,lambda:None)
        for key in scope['docs']:
            scope['docs'][key]['editorial']={'text':'Violin concerts and music.'}
        _,trace=d.search(1,scope,{'skill_queries':[{'zh':'音乐','en':'violin music'}],
            'retrievalTarget':'statement'},set(),lambda:None)
        self.assertEqual(trace['lexicalHits'],0)
        self.assertEqual(trace['target'],'statement')

    def test_memberships_survive_refresh_but_ac_removes_eligibility(self):
        self.pool.refresh(1)
        with self.connect() as c:c.execute('INSERT INTO smart_card_memberships VALUES(1,?,1000)',('cf:1:A130',))
        self.pool.refresh(1)
        self.assertIn('cf:1:A130',{e['id'] for e in self.pool.recommendation_candidates(1)})
        self.record(index='A130',verdict='OK');self.pool.refresh(1)
        self.assertNotIn('cf:1:A130',{e['id'] for e in self.pool.recommendation_candidates(1)})
        with self.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM smart_card_memberships').fetchone()[0],1)
        self.assertEqual(self.pool.recommendation_candidates(2),[])


class DynamicJobTests(unittest.TestCase):
    setUp=job_tests.SmartTests.setUp
    setUpBase=job_tests.SmartTests.setUpBase
    connect=job_tests.SmartTests.connect
    record=job_tests.SmartTests.record
    finish=job_tests.SmartTests.finish

    def test_intent_precedes_any_card_and_cold_start_works(self):
        with self.connect() as c:
            c.execute('DELETE FROM smart_preparations');c.execute('DELETE FROM smart_pool_entries')
        self.service.start(1,{'preference':'通用测试请求'});result=self.finish()
        self.assertEqual(result['job']['status'],'completed',result['job'])
        self.assertEqual(self.provider.calls[0][0],'intent')
        stages=[s for s,_ in self.provider.calls]
        self.assertLess(stages.index('audit'),stages.index('card'))
        self.assertEqual(result['recommendation']['admittedCards'],3)
        self.assertFalse(result['recommendation']['stale'])

    def test_unknown_tag_is_rejected_not_silently_ignored(self):
        original=self.provider._request
        def request(*a,**kw):
            content,usage,model=original(*a,**kw);r=json.loads(content)
            if 'skill_queries' in r:r['tagGroups']=[['made_up_tag']]
            return json.dumps(r),usage,model
        self.provider._request=request
        self.service.start(1,{'preference':'通用测试请求'});result=self.finish()
        self.assertEqual(result['job']['status'],'failed')
        self.assertEqual(len(self.provider.calls),1)

    def test_missing_direction_fields_never_silently_disables_filter(self):
        original=self.provider._request
        def request(*a,**kw):
            content,usage,model=original(*a,**kw);r=json.loads(content)
            if 'skill_queries' in r:r.pop('tagGroups')
            return json.dumps(r),usage,model
        self.provider._request=request
        self.service.start(1,{'preference':'通用测试请求'});result=self.finish()
        self.assertEqual(result['job']['status'],'failed')
        self.assertEqual(len(self.provider.calls),1)
        self.assertIsNone(result['recommendation'])

    def test_missing_source_route_does_not_default_to_mixed(self):
        original=self.provider._request
        def request(*a,**kw):
            content,usage,model=original(*a,**kw);r=json.loads(content)
            if 'skill_queries' in r:r.pop('retrievalTarget')
            return json.dumps(r),usage,model
        self.provider._request=request
        self.service.start(1,{'preference':'通用测试请求'});result=self.finish()
        self.assertEqual(result['job']['status'],'failed')
        self.assertEqual(len(self.provider.calls),1)

    def test_no_source_match_is_completed_without_fabricated_audit(self):
        self.service.retriever.retrieve=lambda *args:{'top8':[],'traces':[],'reason':'missing_target_material'}
        self.service.start(1,{'preference':'通用测试请求'});result=self.finish()
        self.assertEqual(result['job']['status'],'completed',result['job'])
        self.assertIsNone(result['recommendation']['problem'])
        self.assertEqual([s for s,_ in self.provider.calls],['intent'])

    def test_no_tag_match_saves_no_match_without_relaxing(self):
        # Valid catalog tag, none of the eligible problems carry it.
        original=self.service.discovery.prepare
        def scope(*a):
            s=original(*a);s['validTags'].append('graphs');return s
        self.service.discovery.prepare=scope
        original_request=self.provider._request
        def request(*a,**kw):
            content,usage,model=original_request(*a,**kw);r=json.loads(content)
            if 'skill_queries' in r:r['tagGroups']=[['graphs']]
            return json.dumps(r),usage,model
        self.provider._request=request
        self.service.start(1,{'preference':'通用测试请求'});result=self.finish()
        self.assertEqual(result['job']['status'],'completed',result['job'])
        self.assertIsNone(result['recommendation']['problem'])
        self.assertEqual(len(self.provider.calls),1)

    def test_metadata_only_can_recommend_but_not_admit(self):
        with self.connect() as c:c.execute('DELETE FROM training_public_materials')
        with patch('training_archive.TrainingArchive.load',return_value=self.pool.archive.load()|{'materials':{}}):
            self.pool.refresh(1)
            self.service.start(1,{'preference':'通用测试请求'});result=self.finish()
        self.assertEqual(result['job']['status'],'completed',result['job'])
        self.assertIsNotNone(result['recommendation']['problem'])
        self.assertEqual(result['recommendation']['admittedCards'],0)
        self.assertEqual(result['recommendation']['materialKinds'],[])
        self.assertNotIn('card',[s for s,_ in self.provider.calls])

    def test_unadmitted_result_reuses_snapshot_and_material_change_invalidates(self):
        with self.connect() as c:c.execute("DELETE FROM training_public_materials WHERE kind='editorial'")
        self.pool.refresh(1)
        self.service.start(1,{'preference':'通用测试请求'});result=self.finish()
        self.assertEqual(result['job']['status'],'completed',result['job'])
        self.assertEqual(result['recommendation']['admittedCards'],0)
        count=len(self.provider.calls)
        self.service.start(1,{'preference':'通用测试请求'})
        self.assertEqual(len(self.provider.calls),count)
        self.assertEqual(self.service.state(1)['recommendation']['id'],result['recommendation']['id'])
        with self.connect() as c:
            c.execute("UPDATE training_public_materials SET content_hash='changed' WHERE kind='statement' AND problem_id=?",
                (result['recommendation']['problemId'],))
        self.assertTrue(self.service.state(1)['recommendation']['stale'])
        self.assertEqual(len(self.provider.calls),count)


if __name__=='__main__':unittest.main()
