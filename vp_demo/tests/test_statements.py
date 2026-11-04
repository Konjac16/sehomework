import json
import unittest
import urllib.error
from unittest.mock import patch
import server
import test_demo
from problem_statement import StatementParser, fetch_cf_statement, problem_url


HTML = '''<html><div>Unrelated navigation</div><div class="problem-statement">
<div class="header"><div class="title">A. Example</div><div>1 second</div></div>
<p>Find a sum for $$$1 \\le n \\le 10^5$$$ and a &lt; b.</p>
<div class="input-specification">Input<p>An integer n.</p></div>
<div class="output-specification">Output<p>Print n<sup>2</sup>.</p></div>
<div class="sample-test"><div class="input"><pre><div>2</div><div>3 4</div></pre></div></div>
<p>Note: <img src="/diagram.png" alt="diagram"></p><script>do_not_include()</script>
</div><footer>Unrelated footer</footer></html>'''


class ParserTests(unittest.TestCase):
    def test_structured_text_math_samples_and_images(self):
        p = StatementParser(); p.feed(HTML); r = p.result()
        self.assertIn('$$$1 \\le n \\le 10^5$$$', r['text'])
        self.assertIn('a < b', r['text'])
        self.assertIn('n^(2)', r['text'])
        self.assertIn('2\n\n3 4', r['text'])
        self.assertNotIn('Unrelated', r['text'])
        self.assertNotIn('do_not_include', r['text'])
        self.assertTrue(r['warnings'])

    def test_challenge_and_truncated_pages_rejected(self):
        for html in ['<html>Please verify you are human</html>', '<div class="problem-statement">'+'x'*100]:
            p = StatementParser(); p.feed(html)
            with self.assertRaises(ValueError): p.result()

    def test_fixed_url_and_fetch_failure(self):
        r = {'contestId':1900,'index':'A'}
        self.assertEqual(problem_url(r), 'https://codeforces.com/contest/1900/problem/A')
        with self.assertRaises(ValueError): problem_url({'contestId':1900,'index':'../../secret'})
        with patch('problem_statement.urllib.request.urlopen') as call:
            response = call.return_value.__enter__.return_value
            response.geturl.return_value = problem_url(r)
            response.read.return_value = HTML.encode()
            self.assertEqual(fetch_cf_statement(r)['textOrigin'], 'codeforces')
        with patch('problem_statement.urllib.request.urlopen', side_effect=urllib.error.HTTPError(problem_url(r),403,'Forbidden',{},None)):
            with self.assertRaisesRegex(ValueError,'403'): fetch_cf_statement(r)


class StatementHttpTests(unittest.TestCase):
    client = test_demo.HttpTests.client
    request = test_demo.HttpTests.request

    @classmethod
    def setUpClass(cls):
        test_demo.HttpTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        test_demo.HttpTests.tearDownClass.__func__(cls)

    def test_cache_authorization_and_automatic_ai_context(self):
        c, other = self.client('statement_owner'), self.client('statement_other')
        self.request(c,'/api/import',{'text':json.dumps([test_demo.record(10,'WRONG_ANSWER'),test_demo.record(11)])})
        p = StatementParser(); p.feed(HTML)
        fetched = p.result() | {'url':problem_url({'contestId':1900,'index':'A'}),'fetchedAt':123,'textOrigin':'codeforces'}
        with patch.object(server.ADAPTERS['codeforces'],'fetch_statement',return_value=fetched) as fetch:
            self.assertEqual(self.request(other,'/api/problem/statement',{'problemKey':'1900A'})[0],404)
            self.assertEqual(fetch.call_count,0)
            for _ in range(2):
                status, r = self.request(c,'/api/problem/statement',{'problemKey':'1900A'})
                self.assertEqual(status,200); self.assertEqual(r['statement']['status'],'ready')
            self.assertEqual(fetch.call_count,1)
        data={'beforeSubmissionId':'10','afterSubmissionId':'11'}
        preview=self.request(c,'/api/analyses/preview',data)[1]['preview']
        self.assertEqual(preview['problem']['textOrigin'],'codeforces')
        self.assertIn('An integer n.',preview['problem']['text'])
        self.assertTrue(any('图片' in w for w in preview['warnings']))
        custom=self.request(c,'/api/analyses/preview',data|{'problemText':'My corrected statement'})[1]['preview']
        self.assertEqual(custom['problem']['textOrigin'],'user')
        self.assertNotEqual(preview['fingerprint'],custom['fingerprint'])
        # Refresh failure preserves usable cached text, but records the failure.
        with server.connect() as db:
            payload = r['statement'] | {'checkedAt':0}
            db.execute('UPDATE problem_statements SET payload=?',(json.dumps(payload),))
        with patch.object(server.ADAPTERS['codeforces'],'fetch_statement',side_effect=ValueError('HTTP 403')):
            result=self.request(c,'/api/problem/statement',{'problemKey':'1900A','refresh':True})[1]['statement']
        self.assertEqual(result['status'],'stale')
        self.assertEqual(result['text'],fetched['text'])
