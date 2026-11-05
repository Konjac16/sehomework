import json
import os
import sqlite3
import time
import unittest
import urllib.error
from unittest.mock import Mock, patch
import server
import test_demo
from problem_statement import StatementParser, StatementService, fetch_cf_statement, problem_url


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


class ArchiveFallbackTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        StatementService.init_tables(self.db)
        self.adapter = Mock()
        self.service = StatementService(lambda: self.db, {'codeforces': self.adapter})
        self.record = {'platform':'codeforces', 'problemKey':'105588G', 'contestId':105588, 'index':'G'}
        self.payload = {'text':'Previously fetched full statement', 'status':'ready', 'message':'',
                        'textOrigin':'codeforces', 'checkedAt':int(time.time()),
                        'url':problem_url(self.record), 'warnings':['Keep this warning']}
        env = patch.dict(os.environ, {'VPLAB_CF_ARCHIVE':'fixture-archive'})
        env.start()
        self.addCleanup(env.stop)

    def save_public(self, payload):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO problem_statements VALUES(?,?,?)',
                            ('codeforces', '105588G', json.dumps(payload)))

    def test_archive_miss_keeps_cache_for_view_refresh_and_analysis(self):
        self.save_public(self.payload)
        with patch('problem_statement.archived_statement', side_effect=ValueError('Archive missing problem')):
            self.assertEqual(self.service.cached_for(1, self.record), self.payload)
            for refresh in (False, True):
                result = self.service.get(self.record, refresh=refresh)
                self.assertEqual(result, self.payload | {'cached':True})
        self.adapter.fetch_statement.assert_not_called()

    def test_failed_refresh_keeps_old_text_origin_and_warnings(self):
        self.save_public(self.payload | {'checkedAt':0})
        self.adapter.fetch_statement.side_effect = ValueError('Archive missing problem')
        with patch('problem_statement.archived_statement', side_effect=ValueError('Archive missing problem')):
            result = self.service.get(self.record, refresh=True)
            self.assertEqual(result['status'], 'stale')
            for field in ('text', 'textOrigin', 'warnings', 'url'):
                self.assertEqual(result[field], self.payload[field])
            self.assertEqual(self.service.cached_for(1, self.record)['text'], self.payload['text'])
        self.adapter.fetch_statement.assert_called_once_with(self.record)

    def test_archive_precedes_public_cache_and_browser_cache_stays_private(self):
        self.save_public(self.payload)
        archived = self.payload | {'text':'Offline statement', 'textOrigin':'cf_archive'}
        browser = self.payload | {'text':'Browser statement', 'textOrigin':'codeforces_browser'}
        self.service.save_browser(1, self.record, browser)
        with patch('problem_statement.archived_statement', return_value=archived):
            self.assertEqual(self.service.cached_for(1, self.record), browser)
            self.assertEqual(self.service.cached_for(2, self.record)['text'], 'Offline statement')
        with patch('problem_statement.archived_statement', side_effect=ValueError('Archive missing problem')):
            self.assertEqual(self.service.cached_for(1, self.record), browser)
            self.assertEqual(self.service.cached_for(2, self.record), self.payload)

    def test_missing_both_sources_reports_missing_without_network(self):
        for saved in (None, self.payload | {'text':'', 'status':'unavailable'}):
            if saved is not None:
                self.save_public(saved)
            with patch('problem_statement.archived_statement', side_effect=ValueError('Archive missing problem')):
                result = self.service.get(self.record)
                self.assertEqual((result['status'], result['text']), ('unavailable', ''))
                self.assertEqual(result['message'], 'Archive missing problem')
        self.adapter.fetch_statement.assert_not_called()


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
        with patch.dict(os.environ, {'VPLAB_CF_ARCHIVE':'fixture-archive'}), \
                patch('problem_statement.archived_statement', side_effect=ValueError('Archive missing problem')):
            fallback = self.request(c,'/api/analyses/preview',data)[1]['preview']
            self.assertEqual(fallback['problem']['text'], preview['problem']['text'])
            self.assertEqual(fallback['fingerprint'], preview['fingerprint'])
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
