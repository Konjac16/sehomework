"""Production two-stage integration, using mock transport; no paid API calls."""
import copy
import json
import unittest
from unittest.mock import patch

from ai_provider import DeepSeekProvider, ProviderError
from review_service import validate_output
from test_review_inputs import snapshot
from test_reviews import fake_output


def response(content, usage=None, finish='stop'):
    return json.dumps({'choices':[{'finish_reason':finish,'message':{'content':content,'reasoning_content':'not retained'}}],
                       'model':'mock-flash','usage':usage}).encode()


class TwoStageTests(unittest.TestCase):
    def test_pair_and_journey_pass_originals_and_draft_and_preserve_schema(self):
        for verdicts in (['OK'],['WRONG_ANSWER','OK'],['TIME_LIMIT_EXCEEDED','WRONG_ANSWER','OK']):
            with self.subTest(verdicts=verdicts):
                snap=snapshot(verdicts,'Provided statement')
                snap['workflow']=DeepSeekProvider().workflow_config()
                # Distinct sources with no final newline exercise actual diff construction.
                snap['records'][-1]['source']='int main() {\n    return 1;\n}'
                original=copy.deepcopy(snap)
                raw=fake_output(snap)
                for step in raw['steps']:step['test']=''
                stages=[]
                with patch('ai_provider.urllib.request.urlopen') as http:
                    http.return_value.__enter__.return_value.read.side_effect=[
                        response('First-round notes',{'prompt_tokens':40,'completion_tokens':10,'total_tokens':50}),
                        response(json.dumps(raw),{'prompt_tokens':70,'completion_tokens':30,'total_tokens':100})]
                    result,usage,model=DeepSeekProvider().analyze(snap,'fake-key',on_stage=lambda s:stages.append(s))
                self.assertEqual(stages,['analyze','distill'])
                self.assertEqual(usage['total_tokens'],150)
                self.assertEqual(usage['prompt_tokens'],110)
                self.assertTrue(usage['complete'])
                self.assertEqual(model,'mock-flash')
                first,last=[json.loads(c.args[0].data) for c in http.call_args_list]
                a,b=[json.loads(c['messages'][1]['content']) for c in (first,last)]
                self.assertEqual(set(a),{'source'})
                self.assertEqual(set(b),{'source','analysisDraft'})
                self.assertEqual(b['source'],a['source'])
                self.assertEqual(b['analysisDraft'],'First-round notes')
                self.assertNotIn('not retained',json.dumps(b))
                self.assertNotIn('fake-key',json.dumps(b))
                self.assertEqual(first['thinking'],{'type':'disabled'})
                self.assertEqual(last['response_format'],{'type':'json_object'})
                validated=validate_output(result,snap)
                self.assertEqual(len(validated['steps']),len(snap['steps']))
                self.assertTrue(validated['candidates'][0]['evidenceValid'])
                if len(verdicts)>1:
                    diff=b['source']['diffs'][-1]['diff']
                    self.assertIn('-    return 0;\n+    return 1;',diff)
                self.assertEqual(snap,original)

    def test_cancel_between_rounds_sends_only_one_request(self):
        stages=[]
        def checkpoint(stage):
            stages.append(stage)
            return stage!='distill'
        with patch('ai_provider.urllib.request.urlopen') as http:
            http.return_value.__enter__.return_value.read.return_value=response('Notes')
            with self.assertRaisesRegex(ProviderError,'取消'):
                DeepSeekProvider().analyze(snapshot(['OK']),'fake-key',on_stage=checkpoint)
        self.assertEqual(http.call_count,1)
        self.assertEqual(stages,['analyze','distill'])

    def test_invalid_first_round_stops_without_second_call(self):
        for content,finish in [('', 'stop'),('unfinished','length'),(None,'stop'),('x'*20001,'stop')]:
            with self.subTest(finish=finish,length=len(content or '')),patch('ai_provider.urllib.request.urlopen') as http:
                http.return_value.__enter__.return_value.read.return_value=response(content,finish=finish)
                with self.assertRaises(ProviderError):DeepSeekProvider().analyze(snapshot(['OK']),'fake-key')
                self.assertEqual(http.call_count,1)

    def test_second_round_failure_returns_no_partial_result_or_retry(self):
        for second in [response('not-json'),response('[]'),response('{}',finish='length')]:
            with self.subTest(second=second),patch('ai_provider.urllib.request.urlopen') as http:
                http.return_value.__enter__.return_value.read.side_effect=[response('Notes'),second]
                with self.assertRaises(ProviderError):DeepSeekProvider().analyze(snapshot(['OK']),'fake-key')
                self.assertEqual(http.call_count,2)

    def test_incomplete_usage_is_not_presented_as_total(self):
        with patch('ai_provider.urllib.request.urlopen') as http:
            http.return_value.__enter__.return_value.read.side_effect=[response('Notes'),response('{}',{'total_tokens':100})]
            _,usage,_=DeepSeekProvider().analyze(snapshot(['OK']),'fake-key')
        self.assertFalse(usage['complete'])
        self.assertNotIn('total_tokens',usage)
        self.assertEqual(usage['stages'][1]['usage']['total_tokens'],100)

    def test_new_workflow_accepts_at_most_one_card_without_breaking_legacy(self):
        snap=snapshot(['WRONG_ANSWER','OK'])
        raw=fake_output(snap);raw['candidates']*=2
        self.assertEqual(len(validate_output(raw,snap)['candidates']),2)
        snap['workflow']=DeepSeekProvider().workflow_config()
        with self.assertRaises(ProviderError):validate_output(raw,snap)
