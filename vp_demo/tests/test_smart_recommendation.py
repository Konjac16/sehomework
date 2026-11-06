"""No network or paid model calls. Synthetic model output is explicitly a fixture."""
import json
import threading
import time
import unittest
from unittest.mock import patch

import test_intelligent as pool_tests
from smart_recommendation import SmartRecommendationService
from smart_workflow import PROMPTS, validate_audit, validate_rank, validate_intent, canonicalize
from ai_provider import ProviderError
from recommendation_pool import PoolError


class FakeRetriever:
    def ready(self):pass
    def retrieve(self,bundles,cards,intent,check):
        check()
        return {'top8':sorted(bundles)[:8],'traces':[]}


class FakeProvider:
    model='test-only-synthetic'
    def __init__(self):
        self.calls=[];self.entered=threading.Event();self.release=None

    def _request(self,prompt,inputs,key,generation,structured,model=None):
        stage=next(k for k,v in PROMPTS.items() if v==prompt)
        self.calls.append((stage,inputs));self.entered.set()
        if self.release is not None:self.release.wait(10)
        if stage=='card':
            result={'cards':{}}
            for b in inputs['problems']:
                refs=[b['evidence'][0]['evidence_id']]
                result['cards'][b['problem_id']]={
                    'task':'Synthetic task','answer':{'kind':'number','description':'Synthetic answer','evidence_ids':refs},
                    'values':[{'mechanism':'Synthetic hidden solution','conditions':'Fixture only','role':'central','evidence_ids':refs}],
                    'proof':{'techniques':[],'argument':'Fixture proof','support':'explained','evidence_ids':refs},
                    'implementation':{'burden':'moderate','structures':[],'coordination':'Fixture coordination','basis':'editorial','evidence_ids':refs},
                    'limits':['Synthetic fixture, not a recommendation quality test']}
        elif stage=='intent':
            result={'skill_queries':[{'zh':'细节','en':'detail'}],'implementation_query':None,
                    'criteria':[{'name':'c1','description':'本次偏好'}],'soft':[],'reasonCodes':['detail'],
                    'tagGroups':[],'excludeTags':[],'retrievalTarget':'mixed'}
        elif stage=='audit':
            result={'assessments':{b['problem_id']:{'match':'strong','criteria':[
                {'name':'c1','state':'yes','reason':'Synthetic hidden solution','evidence_ids':[b['evidence'][0]['evidence_id']]}],
                'reason':'Synthetic hidden solution','tradeoff':'Fixture only','evidence_ids':[b['evidence'][0]['evidence_id']]}
                for b in inputs['candidates']}}
        else:
            result={'first':[b['problem_id'] for b in inputs['candidates']],
                'assessments':{b['problem_id']:{'tier':'first','reason':'Synthetic hidden solution','evidence_ids':[b['evidence'][0]['evidence_id']]}
                               for b in inputs['candidates']},'tradeoff':'Fixture only'}
        return json.dumps(result,ensure_ascii=False),{'total_tokens':100},self.model


class SmartTests(unittest.TestCase):
    setUpBase=pool_tests.PoolPreparationTests.setUp
    connect=pool_tests.PoolPreparationTests.connect
    record=pool_tests.PoolPreparationTests.record
    def setUp(self):
        self.setUpBase();pool_tests.write_archive(self.pack,count=3)
        with self.connect() as c:SmartRecommendationService.init_tables(c)
        self.pool.refresh(1)
        self.provider=FakeProvider()
        self.service=SmartRecommendationService(self.connect,threading.RLock(),self.pool,lambda uid:'fixture-key',
                                                self.provider,FakeRetriever())

    def finish(self):
        limit=time.monotonic()+12
        while self.service.active and time.monotonic()<limit:time.sleep(.02)
        self.assertFalse(self.service.active)
        return self.service.state(1)

    def test_pipeline_snapshot_refresh_and_account_isolation(self):
        self.service.start(1,{'preference':'练习细节'})
        result=self.finish()
        self.assertEqual(result['job']['status'],'completed',result['job'])
        self.assertEqual(result['recommendation']['problemId'],'cf:1:A1')
        self.assertEqual(len(self.provider.calls),4)
        public=json.dumps(result)
        self.assertNotIn('Synthetic hidden solution',public)
        self.assertNotIn('fixture-key',public)
        self.assertNotIn('tags',public)
        self.service.start(1,{'preference':'练习细节'})
        self.assertEqual(len(self.provider.calls),4)
        self.assertEqual(result['recommendation']['id'],self.service.state(1)['recommendation']['id'])
        self.assertIsNone(self.service.state(2)['recommendation'])
        self.assertEqual(self.service.state(2)['job']['status'],'idle')

    def test_new_preference_reuses_cards_and_old_preference_restores_old_snapshot(self):
        self.service.start(1,{'preference':'练习细节'});first=self.finish()['recommendation']
        self.service.start(1,{'preference':'练习实现'});second=self.finish()['recommendation']
        self.assertNotEqual(first['id'],second['id'])
        self.assertEqual([s for s,_ in self.provider.calls].count('card'),1)
        self.service.start(1,{'preference':'练习细节'})
        self.assertEqual(self.service.state(1)['recommendation']['id'],first['id'])

    def test_cancel_during_paid_call_prevents_next_call_and_snapshot(self):
        self.provider.release=threading.Event()
        self.service.start(1,{'preference':'练习细节'});self.provider.entered.wait(3)
        self.service.cancel(1);self.provider.release.set()
        result=self.finish()
        self.assertEqual(result['job']['status'],'cancelled')
        self.assertEqual(len(self.provider.calls),1)
        self.assertIsNone(result['recommendation'])

    def test_concurrent_start_does_not_double_bill(self):
        self.provider.release=threading.Event()
        self.service.start(1,{'preference':'练习细节'});self.provider.entered.wait(3)
        self.service.start(1,{'preference':'练习实现'})
        self.assertEqual(len(self.provider.calls),1)
        self.provider.release.set();self.finish()

    def test_input_change_during_call_does_not_publish(self):
        self.provider.release=threading.Event()
        self.service.start(1,{'preference':'练习细节'});self.provider.entered.wait(3)
        self.record(verdict='OK');self.provider.release.set()
        result=self.finish()
        self.assertEqual(result['job']['status'],'failed')
        self.assertIsNone(result['recommendation'])
        self.assertEqual(len(self.provider.calls),1)

    def test_settings_and_ac_mark_saved_result_stale_without_new_call(self):
        self.service.start(1,{'preference':'练习细节'});self.finish()
        self.pool.settings(1,{'difficultyMode':'manual','minRating':1700,'maxRating':2100})
        self.assertTrue(self.service.state(1)['recommendation']['stale'])
        self.assertEqual(self.pool.settings(2)['preferences']['difficultyMode'],'default')
        self.assertEqual(len(self.provider.calls),4)
        self.service.start(1,{'preference':'练习细节'})
        self.assertEqual(self.finish()['job']['status'],'completed')

    def test_incomplete_materials_can_recommend_without_admitting_cards(self):
        with self.connect() as c:c.execute("DELETE FROM training_public_materials WHERE kind='editorial'")
        self.service.start(1,{'preference':'练习细节'})
        result=self.finish()
        self.assertEqual(result['job']['status'],'completed',result['job'])
        self.assertIsNotNone(result['recommendation']['problem'])
        self.assertEqual(result['recommendation']['admittedCards'],0)
        self.assertNotIn('card',[s for s,_ in self.provider.calls])

    def test_restart_preserves_snapshot_and_marks_interrupted_job(self):
        self.service.start(1,{'preference':'练习细节'});saved=self.finish()['recommendation']
        with self.connect() as c:c.execute("UPDATE smart_jobs SET status='running'")
        new=SmartRecommendationService(self.connect,threading.RLock(),self.pool,lambda uid:'',FakeProvider(),FakeRetriever())
        new.recover();state=new.state(1)
        self.assertEqual(state['job']['status'],'interrupted')
        self.assertEqual(state['recommendation']['id'],saved['id'])

    def test_schema_cannot_add_candidate_or_mix_criteria(self):
        candidate={'p':{'evidence':[{'evidence_id':'p:one'}]}}
        with self.assertRaises(ProviderError):validate_rank({'assessments':{'alien':{}},'first':['alien']},candidate)
        with self.assertRaises(ProviderError):validate_audit({'assessments':{'p':{'match':'strong','criteria':[
            {'name':'not-current-preference','state':'yes','reason':'bad','evidence_ids':['p:one']}],
            'reason':'bad','tradeoff':''}}},candidate,{'criteria':[{'name':'c1'}]})

    def test_no_strong_candidate_is_a_saved_no_match(self):
        with patch('smart_recommendation.validate_audit',return_value=({},[])):
            self.service.start(1,{'preference':'练习细节'});result=self.finish()
        self.assertEqual(result['job']['status'],'completed')
        self.assertIsNone(result['recommendation']['problem'])
        self.assertNotIn('rank',[s for s,_ in self.provider.calls])

    def test_exact_display_ids_are_normalized_but_invented_ids_rejected(self):
        candidates={'cf:1336:C':{'problem':{'problemKey':'1336C'}}}
        self.assertEqual(canonicalize({'cards':{'1336C':{'task':'fixture'}}},'cards',candidates),
                         {'cards':{'cf:1336:C':{'task':'fixture'}}})
        with self.assertRaises(ProviderError):canonicalize({'cards':{'1336C':{},'cf:1336:C':{}}},'cards',candidates)
        with self.assertRaises(ProviderError):canonicalize({'cards':{'1336D':{}}},'cards',candidates)

    def test_invalid_card_is_not_used_as_evidence_or_block_other_cards(self):
        original=self.provider._request
        def malformed(*args,**kwargs):
            content,usage,model=original(*args,**kwargs)
            raw=json.loads(content)
            if 'cards' in raw:
                raw['cards'][sorted(raw['cards'])[0]]['proof']['evidence_ids']=['alien source']
            return json.dumps(raw),usage,model
        self.provider._request=malformed
        self.service.start(1,{'preference':'练习细节'});result=self.finish()
        self.assertEqual(result['job']['status'],'completed',result['job'])
        self.assertEqual(result['recommendation']['cardWarnings'],1)
        audit_input=next(inputs for stage,inputs in self.provider.calls if stage=='audit')
        self.assertEqual(audit_input['candidates'][0]['card']['values'],[])
        self.assertTrue(audit_input['candidates'][0]['evidence'])
        self.service.start(1,{'preference':'练习实现'});result=self.finish()
        self.assertEqual(result['job']['cardWarnings'],1)
        self.assertEqual([s for s,_ in self.provider.calls].count('card'),1)

    def test_extra_grounded_value_is_bounded_without_discarding_card(self):
        original=self.provider._request
        def extra(*args,**kwargs):
            content,usage,model=original(*args,**kwargs);raw=json.loads(content)
            for card in raw.get('cards',{}).values():card['values']*=3
            return json.dumps(raw),usage,model
        self.provider._request=extra
        self.service.start(1,{'preference':'练习细节'});result=self.finish()
        self.assertEqual(result['job']['status'],'completed')
        self.assertEqual(result['recommendation']['cardWarnings'],0)
        with self.connect() as c:
            cached=[json.loads(r[0]) for r in c.execute('SELECT payload FROM smart_public_cards')]
        self.assertTrue(cached)
        self.assertTrue(all(len(card['values'])==2 for card in cached))


if __name__=='__main__':unittest.main()
