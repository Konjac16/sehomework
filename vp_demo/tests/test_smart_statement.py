"""Synthetic statement-search regressions, independent of user validation requests."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from statement_search import StatementSearch, with_statement_hits
from smart_retrieval import BM25, Retriever, meaningful_statement_chunk, MAX_MERGED_TOKENS
from smart_workflow import validate_intent
from ai_provider import ProviderError
from test_smart_retrieval import DeterministicRetriever, intent
import test_smart_recommendation as workflow_tests
from test_intelligent import write_archive


class SemanticRetriever(DeterministicRetriever):
    def statement_spans(self,text):
        return [(i,min(i+1000,len(text))) for i in range(0,len(text),900)]

    def _encode(self,texts,prefix,check):
        # A synthetic semantic synonym has no lexical overlap with the query.
        return super()._encode([t.replace('fiddle','violin') for t in texts],prefix,check)

    def _cached_passages(self,texts,check,progress=None):
        self.seen=list(texts)
        if progress:progress(len(texts),len(texts))
        return self._encode(texts,'',check)


class StatementRetrievalTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.r=SemanticRetriever(cache_dir=Path(folder.name))
        self.search=StatementSearch(None,self.r)

    def test_dense_sees_all_statements_without_lexical_gate(self):
        corpus={f'p{i:03}':'Networks and sorting.' for i in range(130)}
        corpus['z-last']='A fiddle concert.'
        self.assertEqual(BM25([corpus['z-last']]).scores('violin'),[0])
        result=self.search.search(corpus,intent('statement','violin'),lambda:None)
        self.assertEqual(result['problemCount'],131)
        self.assertEqual(len(self.r.seen),131)
        self.assertEqual(result['ranked'][0],'z-last')
        self.assertEqual(result['perProblemLLMCalls'],0)
        self.assertEqual({t['method'] for t in result['traces']},{'bm25','dense'})

    def test_match_after_truncation_limit_keeps_original_evidence(self):
        raw='Networks. '*1500+'乐曲 A violin music concert sells tickets.'
        result=self.search.search({'p':raw},intent('statement'),lambda:None)
        hits=result['hits']['p']
        self.assertTrue(any(h['start']>12000 and 'violin' in h['text'] for h in hits))
        for h in hits:self.assertEqual(h['text'],raw[h['start']:h['end']])
        b={'hash':'stable-public-hash','evidence':[]}
        audited=with_statement_hits(b,hits)
        self.assertEqual(audited['hash'],b['hash'])
        self.assertEqual(b['evidence'],[])
        self.assertTrue(audited['matchedStatementEvidence'])

    def test_corpus_uses_only_eligible_raw_statements(self):
        scope={'entries':{p:{'materialKey':p} for p in ('a','b','c')},
               'docs':{'a':{'editorial':{'text':'violin'}},'b':{'statement':{'statement':{'body':'saved'}}}},
               'catalog':{'materials':{'b':{'payload':{'statement':{'body':'old'}}},
                                         'c':{'payload':{'statement':{'body':'excluded'}}}}}}
        self.assertEqual(self.search.corpus(scope,['a','b']),{'b':'saved'})
        self.assertEqual(self.search.search({},intent('statement'),lambda:None)['ranked'],[])

    def test_statement_query_expansion_does_not_expand_other_targets(self):
        raw=intent('statement')|{'criteria':[{'name':'c1','description':'music'}],
            'soft':[],'reasonCodes':['preference'],'tagGroups':[],'excludeTags':[]}
        raw['skill_queries']*=6
        self.assertEqual(len(validate_intent(raw)['skill_queries']),6)
        with self.assertRaises(ProviderError):validate_intent(raw|{'retrievalTarget':'solution'})

    def test_original_offsets_and_overlap_with_unicode(self):
        from tokenizers import Tokenizer, models, pre_tokenizers
        tokenizer=Tokenizer(models.WordLevel({'[UNK]':0,'<pad>':1},unk_token='[UNK]'))
        tokenizer.pre_tokenizer=pre_tokenizers.Whitespace()
        self.r.tokenizer=tokenizer
        raw=('乐曲 violin 🎵\n'*400).strip()
        spans=Retriever.statement_spans(self.r,raw)
        self.assertGreater(len(spans),2)
        self.assertEqual(spans[0][0],0)
        self.assertEqual(spans[-1][1],len(raw))
        for (start,end),(next_start,next_end) in zip(spans,spans[1:]):
            self.assertLess(next_start,end)
            self.assertIn(raw[next_start:end],raw[start:end])

    def test_vector_checkpoint_survives_cancel_and_is_reused(self):
        self.r._load()
        self.r.encoder=SimpleNamespace(get_outputs=lambda:[SimpleNamespace(shape=[None,None,7])])
        texts=[f'violin sample {i}' for i in range(110)]
        original=self.r._encode;calls=[]
        def encode(values,prefix,check):
            calls.append(len(values))
            if len(calls)==2:raise RuntimeError('cancelled')
            return original(values,prefix,check)
        self.r._encode=encode
        with self.assertRaisesRegex(RuntimeError,'cancelled'):
            Retriever._cached_passages(self.r,texts,lambda:None)
        self.assertEqual(len(list((self.r.cache_dir/'passages').glob('*.npy'))),96)
        remaining=[]
        def resume(values,prefix,check):
            remaining.append(len(values));return original(values,prefix,check)
        self.r._encode=resume
        values=Retriever._cached_passages(self.r,texts,lambda:None)
        self.assertEqual(remaining,[14])
        self.assertEqual(values.shape,(110,7))

    def test_chunk_minimum_tail_merging_and_complete_coverage(self):
        from tokenizers import Tokenizer, models, pre_tokenizers
        tokenizer=Tokenizer(models.WordLevel({'[UNK]':0,'<pad>':1},unk_token='[UNK]'))
        tokenizer.pre_tokenizer=pre_tokenizers.Whitespace()
        self.r.tokenizer=tokenizer
        for size in (351,400,401,410,414,415,700,701,750,751,1200):
            with self.subTest(size=size):
                raw=' '.join(['word']*(size-1)+['.'])
                spans=Retriever.statement_spans(self.r,raw)
                self.assertTrue(spans)
                self.assertEqual(spans[0][0],0)
                self.assertEqual(spans[-1][1],len(raw))
                self.assertTrue(all(meaningful_statement_chunk(raw[a:b]) for a,b in spans))
                for a,b in spans:
                    self.assertLessEqual(len(tokenizer.encode(raw[a:b],add_special_tokens=False).ids),MAX_MERGED_TOKENS)
                for (_,end),(start,_) in zip(spans,spans[1:]):self.assertLessEqual(start,end)
                if size<=413:self.assertEqual(len(spans),1)

    def test_short_and_punctuation_only_statements_are_not_indexed(self):
        from tokenizers import Tokenizer, models, pre_tokenizers
        self.r.tokenizer=Tokenizer(models.WordLevel({'[UNK]':0,'<pad>':1},unk_token='[UNK]'))
        self.r.tokenizer.pre_tokenizer=pre_tokenizers.Whitespace()
        for raw in ('', '.', ' . '*800, 'tiny text', 'a'+' '*100):
            self.assertEqual(Retriever.statement_spans(self.r,raw),[])

    def test_character_short_tail_keeps_context_instead_of_symbol_chunk(self):
        from tokenizers import Tokenizer, models, pre_tokenizers
        self.r.tokenizer=Tokenizer(models.WordLevel({'[UNK]':0,'<pad>':1},unk_token='[UNK]'))
        self.r.tokenizer.pre_tokenizer=pre_tokenizers.Whitespace()
        raw='word '*350+'x '*70
        spans=Retriever.statement_spans(self.r,raw)
        self.assertEqual(spans,[(0,len(raw.rstrip()))])


class StatementWorkflowTests(unittest.TestCase):
    setUp=workflow_tests.SmartTests.setUp
    setUpBase=workflow_tests.SmartTests.setUpBase
    connect=workflow_tests.SmartTests.connect
    record=workflow_tests.SmartTests.record
    finish=workflow_tests.SmartTests.finish

    def prepare_statement(self):
        original=self.provider._request
        def statement_request(*args,**kwargs):
            content,usage,model=original(*args,**kwargs);raw=json.loads(content)
            if 'retrievalTarget' in raw:raw['retrievalTarget']='statement'
            return json.dumps(raw),usage,model
        self.provider._request=statement_request
        def search(corpus,intent,check,progress):
            self.searched=corpus
            selected=sorted(corpus)[-16:]
            return {'ranked':selected,'hits':{p:[{'evidence_id':p+':statement_hit:0:10',
                'kind':'statement','text':corpus[p][:10],'start':0,'end':10}] for p in selected},
                'problemCount':len(corpus),'method':'full_eligible_statements_independent_BM25_and_dense'}
        self.service.statements.search=search

    def test_full_scope_audit_and_unreviewed_changes_invalidate_snapshot(self):
        write_archive(self.pack,count=130);self.pool.refresh(1)
        self.prepare_statement()
        with patch.object(self.service.discovery,'search',side_effect=AssertionError('64 prefilter forbidden')),\
             patch.object(self.service.retriever,'retrieve',side_effect=AssertionError('second retrieval forbidden')):
            self.service.start(1,{'preference':'寻找音乐背景'});state=self.finish()
        self.assertEqual(state['job']['status'],'completed',state['job'])
        self.assertEqual(len(self.searched),130)
        self.assertEqual(state['recommendation']['searchedProblemCount'],130)
        audit=next(i for s,i in self.provider.calls if s=='audit')
        self.assertTrue(all({e['kind'] for e in b['evidence']}=={'statement'} for b in audit['candidates']))
        self.assertTrue(all(b['matchedStatementEvidence'] for b in audit['candidates']))
        self.assertFalse(state['recommendation']['stale'])
        reviewed={b['problem_id'] for b in audit['candidates']}
        unseen=next(p for p in self.searched if p not in reviewed)
        with self.connect() as c:
            c.execute("INSERT OR REPLACE INTO training_public_materials VALUES(?,?,?,?,?)",
                (unseen,'statement','changed',json.dumps({'statement':{'body':'new music evidence'}}),1001))
        self.assertTrue(self.service.state(1)['recommendation']['stale'])

    def test_no_match_audits_two_batches_without_editorial_fetch(self):
        write_archive(self.pack,count=30);self.pool.refresh(1)
        self.prepare_statement()
        self.provider.audit_match='weak';self.provider.criterion_state='no'
        with patch.object(self.service,'editorials') as editorials:
            self.service.start(1,{'preference':'寻找音乐背景'});state=self.finish()
            editorials.prepare_one.assert_not_called()
        self.assertEqual(state['job']['status'],'completed',state['job'])
        self.assertEqual(state['recommendation']['reviewedCount'],16)
        self.assertIsNotNone(state['recommendation']['problem'])
        self.assertTrue(state['recommendation']['approximate'])
        self.assertEqual(sum(s=='audit' for s,_ in self.provider.calls),2)


if __name__=='__main__':unittest.main()
