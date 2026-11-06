import json
import time
import unittest
from unittest.mock import Mock
import test_intelligent as fixtures
from test_editorials import material
from recommendation_pool import PoolError
from editorial_service import EditorialService
from cf_editorials import CFEditorialProvider
from material_quality import coverage, complete


class PoolQualityTests(unittest.TestCase):
    seed_materials=False
    setUp=fixtures.PoolPreparationTests.setUp
    connect=fixtures.PoolPreparationTests.connect
    record=fixtures.PoolPreparationTests.record

    def keys(self):
        self.pool.refresh(1)
        with self.connect() as c:return [r[0] for r in c.execute('SELECT problem_id FROM smart_pool_entries WHERE user_id=1 ORDER BY position')]

    def put(self,key,data):
        data=data|{'codeDiscoveryVersion':CFEditorialProvider.CODE_VERSION}
        with self.connect() as c:
            c.execute('INSERT OR REPLACE INTO training_public_materials VALUES(?,?,?,?,?)',(key,'editorial','fixture',json.dumps(data),self.now))

    def wait(self,service):
        deadline=time.monotonic()+20
        while service.status(1)['status']=='running' and time.monotonic()<deadline:time.sleep(.01)
        self.assertNotEqual(service.status(1)['status'],'running',service.status(1))

    def test_unchecked_stay_outside_pool_and_at_most_twenty_incomplete(self):
        keys=self.keys();self.assertEqual(self.pool.state(1)['candidateCount'],0)
        for k in keys[:25]:self.put(k,material()|{'codeBlocks':[]})
        state=self.pool.refresh(1)
        self.assertEqual(state['candidateCount'],20)
        self.assertEqual(state['materialCoverage']['completeCount'],0)
        self.assertFalse(state['retrievalReady'])
        self.assertEqual(state['reserveCount'],110)

    def test_intersection_and_full_pool_gate(self):
        keys=self.keys()
        for k in keys[:79]:self.put(k,material())
        self.put(keys[79],material()|{'codeBlocks':[],'codeLinks':[{'url':'https://codeforces.com/contest/1/submission/1'}]})
        self.put(keys[80],material()|{'text':''})
        for k in keys[81:100]:self.put(k,material()|{'codeBlocks':[]})
        state=self.pool.refresh(1)
        self.assertEqual(state['materialCoverage']['completeCount'],79)
        self.assertEqual(state['candidateCount'],99)
        self.assertFalse(state['retrievalReady'])
        with self.assertRaises(PoolError):self.pool.retrieval_candidates(1)
        self.put(keys[79],material());state=self.pool.refresh(1)
        self.assertEqual(state['candidateCount'],100)
        self.assertTrue(state['retrievalReady'])
        self.assertEqual(len(self.pool.retrieval_candidates(1)),100)
        self.record(verdict='OK',index=state['candidates'][0]['problem']['index'])
        self.assertFalse(self.pool.state(1)['retrievalReady'])

    def test_rejected_partial_material_is_preserved_and_can_later_enter(self):
        keys=self.keys()
        for k in keys[:25]:self.put(k,material()|{'codeBlocks':[]})
        state=self.pool.refresh(1);outside=next(k for k in keys[:25] if k not in {e['id'] for e in state['candidates']})
        self.assertTrue(self.pool.material(1,outside)['editorial']['text'])
        self.put(outside,material());state=self.pool.refresh(1)
        self.assertIn(outside,[p['id'] for p in state['candidates']])

    def test_no_shrinking_denominator(self):
        keys=self.keys()
        for k in keys[:37]:self.put(k,material())
        for k in keys[37:]:self.put(k,material()|{'codeBlocks':[]})
        state=self.pool.refresh(1)
        self.assertEqual(state['candidateCount'],57)
        self.assertEqual(state['materialCoverage']['requiredCount'],80)
        self.assertEqual(state['materialCoverage']['shortfall'],43)
        self.assertFalse(state['retrievalReady'])
        provider=Mock();service=EditorialService(self.connect,self.pool.lock,provider,lambda:self.now,pool=self.pool)
        service.start(1);self.wait(service)
        self.assertEqual(service.status(1)['status'],'insufficient')
        provider.fetch.assert_not_called()

    def test_rounding_empty_and_blank_payload(self):
        self.assertFalse(coverage(13,10)['ready']);self.assertTrue(coverage(13,11)['ready'])
        self.assertFalse(coverage(0,0)['ready'])
        self.assertFalse(complete(material()|{'codeBlocks':[{'text':'  '}]}))
        self.assertFalse(complete(material()|{'text':'Tutorial is loading... '*20}))

    def test_sample_grid_is_not_code_but_non_cpp_code_is_retained(self):
        self.assertFalse(complete(material()|{'codeBlocks':[{'text':'13 5\nAAAAA\nBBBBB\nAAACA'}]}))
        self.assertFalse(complete(material()|{'codeBlocks':[{'text':'3\n1 2 3\n4 5 6'}]}))
        self.assertTrue(complete(material()|{'codeBlocks':[{'text':'v\n> 543** > :#v_ $&> :#v_ 1 > :0g >'}]}))
        self.assertTrue(complete(material()|{'codeBlocks':[{'text':'dp[i + 1][j] += dp[i][j] * p'}]}))

    def test_stream_skips_excess_incomplete_until_full(self):
        keys=self.keys();count=0
        def fetch(*args,**kwargs):
            nonlocal count
            count+=1
            return material()|({'codeBlocks':[]} if count<=25 else {})
        provider=Mock();provider.fetch=Mock(side_effect=fetch)
        service=EditorialService(self.connect,self.pool.lock,provider,lambda:self.now,pool=self.pool)
        service.start(1);self.wait(service)
        self.assertEqual(service.status(1)['status'],'completed',service.status(1))
        state=self.pool.state(1)
        self.assertTrue(state['retrievalReady'])
        self.assertEqual((state['candidateCount'],state['materialCoverage']['completeCount']),(100,80))
        self.assertEqual(provider.fetch.call_count,105)
        self.assertEqual(len(set(keys[:25]) & {p['id'] for p in state['candidates']}),20)


if __name__=='__main__':unittest.main()
