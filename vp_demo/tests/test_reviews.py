import io
import hashlib
import http.client
import json
import threading
import time
import unittest
import urllib.error
from unittest.mock import patch

import server
import test_demo
from ai_provider import DeepSeekProvider, ProviderError
from review_service import validate_output, dump


def fake_output(snapshot, bad=False):
    def ref(r):
        return {"submissionId": r["externalId"], "lineStart": 1, "lineEnd": 1,
                "quote": "invented" if bad else r["source"].splitlines()[0]}
    lookup = {r["externalId"]: r for r in snapshot["records"]}
    return {"summary": "观察到修改，需要结合题面验证。", "steps": [{"before": s["before"], "after": s["after"],
        "observation": "表达式发生变化", "hypothesis": "可能修复边界", "test": "测试最小输入，尚未运行",
        "evidence": [ref(lookup[s["after"]])]} for s in snapshot["steps"]],
        "candidates": [{"title": "先检查边界", "category": "错误复盘", "scenario": "处理循环边界时", "rule": "检查首尾元素",
                        "checklist": ["测试最小输入"], "tags": ["边界"], "incident": "修改了代码", "evidence": [ref(snapshot["records"][0])]}],
        "limitations": ["尚未执行测试"]}


class ReviewHttpTests(unittest.TestCase):
    client = test_demo.HttpTests.client
    request = test_demo.HttpTests.request

    @classmethod
    def setUpClass(cls):
        test_demo.HttpTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        server.REVIEWS.keys.clear()
        test_demo.HttpTests.tearDownClass.__func__(cls)

    def prepare(self, name, rows=None):
        c = self.client(name)
        rows = rows or [test_demo.record(10, 'WRONG_ANSWER', source='int x = 1;\n'), test_demo.record(11, source='int x = 2;\n')]
        self.assertEqual(self.request(c, '/api/import', {'text': json.dumps(rows)})[0], 200)
        self.request(c, '/api/ai/config', {'apiKey': 'fake-model-key'})
        return c

    def start(self, c, **extra):
        data = {'analysisScope': 'pair', 'beforeSubmissionId':'10', 'afterSubmissionId':'11', **extra}
        status, preview = self.request(c, '/api/analyses/preview', data)
        self.assertEqual(status, 200, preview)
        data['fingerprint'] = preview['preview']['fingerprint']
        return self.request(c, '/api/analyses/create', data)

    def finish(self, c, aid):
        deadline = time.monotonic()+5
        while time.monotonic()<deadline:
            a = self.request(c, '/api/analysis?id='+str(aid))[1]['analysis']
            if a['status'] != 'running':
                # The worker releases its concurrency slot immediately after the write.
                threading.Event().wait(.01)
                return a
            threading.Event().wait(.01)
        self.fail('worker did not finish')

    def mark_history(self, c):
        uid = self.request(c, '/api/me')[1]['user']['id']
        with server.connect() as db:
            settings = json.loads(db.execute('SELECT settings FROM users WHERE id=?', (uid,)).fetchone()[0])
            settings['historyCoverage'] = {'platform':'codeforces','handle':'tester','completeThrough':1800000000}
            db.execute('UPDATE users SET settings=? WHERE id=?', (json.dumps(settings), uid))

    def test_journey_stops_at_first_ac_and_preserves_all_attempts(self):
        c = self.prepare('journey_review', [test_demo.record(9,'COMPILATION_ERROR'),test_demo.record(10,'WRONG_ANSWER'),test_demo.record(11,'TIME_LIMIT_EXCEEDED'),test_demo.record(12),test_demo.record(13)])
        data={'analysisScope':'first_ac','problemKey':'1900A'}
        p = self.request(c,'/api/analyses/preview',data)[1]['preview']
        self.assertEqual([r['externalId'] for r in p['timeline']], ['9','10','11','12'])
        self.assertEqual(p['coverageState'],'partial')
        self.assertEqual(self.request(c,'/api/analyses/create', data|{'fingerprint':p['fingerprint']})[0],422)
        self.mark_history(c)
        p = self.request(c,'/api/analyses/preview',data)[1]['preview']
        self.assertEqual(p['coverageState'],'complete')
        self.assertEqual(len(p['steps']),3)
        with patch.object(server.REVIEWS.provider,'analyze',side_effect=lambda s,k,**kw:(fake_output(s),{'total_tokens':50},'mock-model')):
            status, r = self.request(c,'/api/analyses/create',data|{'fingerprint':p['fingerprint']})
            self.assertEqual(status,202)
            a=self.finish(c,r['analysis']['id'])
        self.assertEqual(a['status'],'succeeded')
        self.assertEqual(len(a['result']['steps']),3)

    def test_missing_source_no_ac_and_snapshot_fingerprint(self):
        c=self.prepare('partial_review',[test_demo.record(10,'WRONG_ANSWER'),test_demo.record(11,'WRONG_ANSWER',source=''),test_demo.record(12,'TIME_LIMIT_EXCEEDED')])
        p=self.request(c,'/api/analyses/preview',{'analysisScope':'first_ac','problemKey':'1900A'})[1]['preview']
        self.assertEqual(p['coverageState'],'no_ac')
        self.assertEqual(p['missingSourceIds'],['11'])
        self.assertFalse(p['steps'][0]['adjacent'])
        pair=self.request(c,'/api/analyses/preview',{'beforeSubmissionId':'10','afterSubmissionId':'11'})[1]['preview']
        self.assertTrue(pair['blockers'])
        self.assertEqual(self.request(c,'/api/analyses/create',{'beforeSubmissionId':'10','afterSubmissionId':'12','fingerprint':'stale'})[0],409)
        self.assertEqual(self.request(c,'/api/analyses/preview',{'beforeSubmissionId':'12','afterSubmissionId':'10'})[0],400)

    def test_real_result_candidate_confirmation_versions_and_snapshot(self):
        c, other=self.prepare('review_save'),self.client('review_other')
        with patch.object(server.REVIEWS.provider,'analyze',side_effect=lambda s,k,**kw:(fake_output(s),None,'mock')) as model:
            status,r=self.start(c);self.assertEqual(status,202)
            aid=r['analysis']['id'];a=self.finish(c,aid)
            self.assertEqual(a['status'],'succeeded')
            status,reused=self.start(c);self.assertEqual(status,200);self.assertTrue(reused['reused'])
            self.assertEqual(model.call_count,1)
        self.assertEqual(self.request(other,'/api/analysis?id='+str(aid))[0],404)
        self.assertEqual(self.request(other,f'/api/analysis/source?id={aid}&submissionId=10')[0],404)
        status,r=self.request(c,'/api/experiences/from-analysis',{'analysisId':aid,'candidateKey':'0'})
        self.assertEqual(status,200);card=r['card'];self.assertEqual(card['reviewState'],'pending')
        duplicate=self.request(c,'/api/experiences/from-analysis',{'analysisId':aid,'candidateKey':'0'})[1]
        self.assertEqual(card['id'],duplicate['card']['id']);self.assertTrue(duplicate['existing'])
        self.assertEqual(self.request(other,'/api/experience?id='+str(card['id']))[0],404)
        self.assertEqual(self.request(other,'/api/experiences/confirm',{'id':card['id'],'expectedVersion':1})[0],404)
        r=self.request(c,'/api/experiences/confirm',{'id':card['id'],'expectedVersion':1})[1]
        self.assertEqual(r['card']['reviewState'],'confirmed')
        self.assertEqual(self.request(c,'/api/experiences/update',{'id':card['id'],'expectedVersion':1,'title':'stale'})[0],409)
        self.request(c,'/api/experiences/delete',{'id':card['id'],'expectedVersion':2})
        self.assertEqual(self.request(c,'/api/experiences')[1]['total'],0)
        self.assertEqual(self.request(c,'/api/experiences?trash=1')[1]['total'],1)
        restored=self.request(c,'/api/experiences/restore',{'id':card['id'],'expectedVersion':3})[1]['card']
        self.assertEqual(restored['sources'][0]['submissionIds'],['10','11'])
        # A later import must not rewrite the source used by the saved analysis.
        self.request(c,'/api/import',{'text':json.dumps([test_demo.record(10,source='changed')])})
        snap=self.request(c,f'/api/analysis/source?id={aid}&submissionId=10')[1]['record']
        self.assertEqual(snap['source'],'int x = 1;\n')
        status, diff = self.request(c,f'/api/analysis/diff?id={aid}&before=10&after=11')
        self.assertEqual(status, 200)
        self.assertNotIn('changed', json.dumps(diff))
        self.assertEqual(diff['before']['verdict'], 'WRONG_ANSWER')
        self.assertEqual(self.request(other,f'/api/analysis/diff?id={aid}&before=10&after=11')[0],404)
        self.assertEqual(self.request(c,f'/api/analysis/diff?id={aid}&before=10&after=999')[0],404)

    def test_invalid_evidence_cannot_be_collected(self):
        c=self.prepare('review_badref')
        with patch.object(server.REVIEWS.provider,'analyze',side_effect=lambda s,k,**kw:(fake_output(s,True),None,'mock')):
            _,r=self.start(c);a=self.finish(c,r['analysis']['id'])
        self.assertEqual(a['status'],'succeeded')
        self.assertFalse(a['result']['candidates'][0]['evidenceValid'])
        self.assertEqual(self.request(c,'/api/experiences/from-analysis',{'analysisId':a['id'],'candidateKey':'0'})[0],422)

    def test_generation_configuration_and_legacy_cache_isolation(self):
        c=self.prepare('thinking_cache')
        with patch.object(server.REVIEWS.provider,'analyze',side_effect=lambda s,k,**kw:(fake_output(s),None,'mock')):
            _,r=self.start(c);a=self.finish(c,r['analysis']['id'])
        self.assertEqual(a['context']['generation']['thinking'],{'type':'disabled'})
        request={'analysisScope':'pair','beforeSubmissionId':'10','afterSubmissionId':'11'}
        generation={'thinking':{'type':'enabled'},'reasoning_effort':'high','max_tokens':32768}
        with patch.object(server.REVIEWS.provider,'generation_config',return_value=generation):
            preview=self.request(c,'/api/analyses/preview',request)[1]['preview']
        self.assertNotEqual(preview['fingerprint'],a['fingerprint'])
        self.assertIsNone(preview['existingAnalysisId'])
        # Simulate an older stored result, which predates generation metadata.
        with server.connect() as db:
            snapshot=json.loads(db.execute('SELECT snapshot FROM analyses WHERE id=?',(a['id'],)).fetchone()[0])
            snapshot.pop('generation')
            old=dump(snapshot)
            db.execute('UPDATE analyses SET snapshot=?,fingerprint=? WHERE id=?',(old,hashlib.sha256(old.encode()).hexdigest(),a['id']))
        legacy=self.request(c,'/api/analysis?id='+str(a['id']))[1]['analysis']
        self.assertIsNone(legacy['context']['generation'])
        self.assertEqual(legacy['status'],'succeeded')
        preview=self.request(c,'/api/analyses/preview',request)[1]['preview']
        self.assertIsNone(preview['existingAnalysisId'])

    def test_cancel_isolated_job_and_credentials_not_persisted(self):
        c=self.prepare('review_cancel')
        entered,release=threading.Event(),threading.Event()
        def slow(snapshot,key,**kwargs):
            entered.set();release.wait(4);return fake_output(snapshot),None,'mock'
        with patch.object(server.REVIEWS.provider,'analyze',side_effect=slow):
            try:
                _,r=self.start(c);aid=r['analysis']['id'];self.assertTrue(entered.wait(2))
                cancelled=self.request(c,'/api/analyses/cancel',{'id':aid})[1]['analysis']
                self.assertEqual(cancelled['status'],'cancelled')
            finally:release.set()
            deadline=time.monotonic()+4
            while server.REVIEWS.active and time.monotonic()<deadline:threading.Event().wait(.01)
        self.assertEqual(self.request(c,'/api/analysis?id='+str(aid))[1]['analysis']['status'],'cancelled')
        config=self.request(c,'/api/ai/config')[1]
        self.assertTrue(config['configured']);self.assertNotIn('fake-model-key',json.dumps(config))
        with server.connect() as db:
            stored=''.join(str(tuple(r)) for r in db.execute('SELECT snapshot,result,error FROM analyses'))
        self.assertNotIn('fake-model-key',stored)
        self.request(c,'/api/logout',{})
        self.request(c,'/api/login',{'username':'review_cancel','password':'test-password-42'})
        self.assertFalse(self.request(c,'/api/ai/config')[1]['configured'])

    def test_manual_card_validation_and_provenance_not_forgeable(self):
        c=self.client('manual_lesson')
        self.assertEqual(self.request(c,'/api/experiences/create',{'title':'Incomplete','reviewState':'confirmed'})[0],400)
        status,r=self.request(c,'/api/experiences/create',{'title':'Manual','origin':'ai','analysisId':666,'analysisScope':'first_ac'})
        self.assertEqual(status,200);self.assertEqual(r['card']['origin'],'manual');self.assertIsNone(r['card']['analysisId'])
        self.assertEqual(self.request(c,'/api/experiences?reviewState=pending')[1]['total'],1)


class ProviderTests(unittest.TestCase):
    def test_json_request_and_error_redaction(self):
        snapshot={'records':[],'analysisScope':'pair','coverageState':'pair','warnings':[],'mode':'review','problem':{},'question':'','steps':[],'relatedExperiences':[]}
        body={'choices':[{'finish_reason':'stop','message':{'content':'{"summary":"ok"}','reasoning_content':'internal reasoning'}}],'model':'mock','usage':{'total_tokens':123}}
        with patch('ai_provider.urllib.request.urlopen') as call:
            call.return_value.__enter__.return_value.read.return_value=json.dumps(body).encode()
            result,usage,model=DeepSeekProvider().analyze(snapshot,'fake-key')
        payload=json.loads(call.call_args.args[0].data)
        self.assertEqual(payload['response_format'],{'type':'json_object'})
        self.assertEqual(payload['thinking'],{'type':'disabled'})
        self.assertNotIn('reasoning_effort',payload)
        self.assertEqual(payload['max_tokens'],6000)
        self.assertEqual(payload['temperature'],0.2)
        self.assertEqual(call.call_args.kwargs['timeout'],90)
        self.assertNotIn('tools',payload)
        self.assertEqual(call.call_count,2)
        self.assertEqual(usage['total_tokens'],246)
        self.assertEqual(usage['callCount'],2)
        first=json.loads(call.call_args_list[0].args[0].data)
        self.assertEqual(first['max_tokens'],3000)
        self.assertNotIn('response_format',first)
        self.assertEqual(result,{'summary':'ok'})
        error=urllib.error.HTTPError('https://api.deepseek.com/chat/completions',401,'fake-key',{},io.BytesIO(b'fake-key'))
        with patch('ai_provider.urllib.request.urlopen',side_effect=error):
            with self.assertRaises(ProviderError) as caught:DeepSeekProvider().analyze(snapshot,'fake-key')
        self.assertNotIn('fake-key',str(caught.exception))

    def test_exhausted_thinking_budget_does_not_accept_partial_result(self):
        snapshot={'records':[],'analysisScope':'pair','coverageState':'pair','warnings':[],'mode':'review','problem':{},'question':'','steps':[],'relatedExperiences':[]}
        body={'choices':[{'finish_reason':'length','message':{'reasoning_content':'unfinished','content':'{"summary":"partial"}'}}]}
        with patch('ai_provider.urllib.request.urlopen') as call:
            call.return_value.__enter__.return_value.read.return_value=json.dumps(body).encode()
            with self.assertRaises(ProviderError) as caught:
                DeepSeekProvider().analyze(snapshot,'fake-key')
            self.assertIn('length',str(caught.exception))

    def test_transport_errors_preserve_kind_without_sensitive_details(self):
        snapshot={'records':[],'analysisScope':'pair','coverageState':'pair','warnings':[],'mode':'review','problem':{},'question':'','steps':[],'relatedExperiences':[]}
        cases=[(TimeoutError('fake-key'),'TimeoutError'),
               (urllib.error.URLError(OSError('fake-key')),'OSError'),
               (http.client.IncompleteRead(b'fake-key',20),'IncompleteRead'),
               (json.JSONDecodeError('fake-key','fake-key',0),'JSON'),
               (RuntimeError('fake-key'),'RuntimeError')]
        for error,kind in cases:
            with self.subTest(kind=kind),patch('ai_provider.urllib.request.urlopen',side_effect=error):
                with self.assertRaises(ProviderError) as caught:
                    DeepSeekProvider().analyze(snapshot,'fake-key')
                self.assertIn(kind,str(caught.exception))
                self.assertNotIn('fake-key',str(caught.exception))

    def test_incomplete_steps_are_rejected(self):
        with self.assertRaises(ProviderError):
            validate_output({'summary':'bad','steps':[]}, {'records':[],'steps':[{'before':'1','after':'2'}]})
