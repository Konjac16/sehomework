"""Independent, synthetic retrieval regressions; no user holdout examples or model API calls."""
import tempfile
import unittest
from pathlib import Path

from smart_retrieval import Retriever, BM25, terms
from smart_retrieval_policy import queries_for, rank_documents


class TokenizerStub:
    def no_truncation(self):pass
    def no_padding(self):pass
    def enable_truncation(self,**kw):pass
    def enable_padding(self,**kw):pass


class DeterministicRetriever(Retriever):
    """Exercise production routing/fusion with a deliberately synthetic encoder."""
    def _load(self):
        self.tokenizer=TokenizerStub();self.model_hash='synthetic-test-encoder'
        self.cache_dir.mkdir(parents=True,exist_ok=True)
    def _chunks(self,text):return [text]
    def _encode(self,texts,prefix,check):
        import numpy as np
        rows=[]
        for text in texts:
            check();ts=set(terms(text))
            v=np.array([.01]+[float(w in ts) for w in ['violin','music','networks','sorting','offline','tickets']])
            rows.append(v/np.linalg.norm(v))
        return np.array(rows,dtype=np.float32)
    def _cached_passages(self,texts,check):return self._encode(texts,'',check)


def make_bundle(pid,**fields):
    return {'problem_id':pid,'evidence':[{'evidence_id':pid+':'+kind+':0','kind':kind,'text':text}
        for kind,text in fields.items()]}


def intent(target='mixed',query='violin music'):
    return {'retrievalTarget':target,'skill_queries':[{'zh':query,'en':query}], 'implementation_query':None}


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.retriever=DeterministicRetriever(cache_dir=Path(folder.name))

    def test_new_raw_problem_beats_many_irrelevant_cached_cards(self):
        bundles={f'old{i}':make_bundle(f'old{i}',statement='Graph networks and reachability.',editorial='Networks and paths.') for i in range(12)}
        bundles['new']=make_bundle('new',statement='A violin music concert sells tickets.')
        fake_cards={p:{'task':'violin music '*100,'values':[{'mechanism':'violin music'}]*10} for p in bundles if p!='new'}
        plain=self.retriever.retrieve(bundles,{},intent())
        with_cards=self.retriever.retrieve(bundles,fake_cards,intent())
        self.assertEqual(plain,with_cards)
        self.assertEqual(plain['top8'][0],'new')
        self.assertFalse(plain['generatedCardsAffectRanking'])

    def test_statement_request_cannot_match_only_editorial_or_metadata(self):
        bundles={'old':make_bundle('old',statement='Graph networks and reachability.',
            editorial='A violin music concert.',metadata='violin music'),
            'new':make_bundle('new',statement='A violin music concert sells tickets.'),
            'no_statement':make_bundle('no_statement',editorial='violin music')}
        result=self.retriever.retrieve(bundles,{},intent('statement'))
        self.assertEqual(result['top8'][0],'new')
        self.assertNotIn('no_statement',result['top8'])
        for trace in result['traces']:
            self.assertTrue(all(e['kind']=='statement' for e in trace['evidence'].values()))

    def test_solution_query_can_find_actual_editorial_or_code(self):
        bundles={'story':make_bundle('story',statement='A violin music concert sells tickets.'),
            'method':make_bundle('method',statement='Answer queries on networks.',editorial='Use offline sorting to process queries.'),
            'code':make_bundle('code',code='// offline sorting of queries')}
        result=self.retriever.retrieve(bundles,{},intent('solution','offline sorting'))
        self.assertEqual(set(result['top8'][:2]),{'method','code'})

    def test_no_requested_source_does_not_silently_search_other_sources(self):
        result=self.retriever.retrieve({'a':make_bundle('a',editorial='violin music')},{},intent('statement'))
        self.assertEqual(result['top8'],[])
        self.assertEqual(result['reason'],'missing_target_material')

    def test_basic_info_only_remains_searchable_for_metadata_request(self):
        result=self.retriever.retrieve({'a':make_bundle('a',metadata='violin music')},{},intent('metadata'))
        self.assertEqual(result['top8'],['a'])

    def test_duplicate_sources_do_not_add_votes(self):
        docs=[{'pid':'old','id':'o1','kind':'editorial'}, {'pid':'new','id':'n1','kind':'statement'}]
        expected=rank_documents(docs,[.4,.9],{'editorial':1,'statement':1})
        duplicated=docs+[{'pid':'old','id':f'o{i+2}','kind':'editorial'} for i in range(100)]
        actual=rank_documents(duplicated,[.4,.9]+[.4]*100,{'editorial':1,'statement':1})
        self.assertEqual(expected,actual)

    def test_source_boilerplate_is_not_a_keyword_hit(self):
        self.assertEqual(queries_for(intent('statement','violin themed problem statement')),['violin'])
        scores=BM25(['Problem statement and editorial.','Violin music concert.']).scores('violin themed problem statement')
        self.assertEqual(scores[0],0)
        self.assertGreater(scores[1],0)

    def test_cancellation_during_retrieval_is_honored(self):
        def stop():raise RuntimeError('cancelled-test')
        with self.assertRaisesRegex(RuntimeError,'cancelled-test'):
            self.retriever.retrieve({'p':make_bundle('p',statement='violin')},{},intent(),stop)


if __name__=='__main__':unittest.main()
