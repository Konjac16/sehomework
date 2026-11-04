import io
import json
import time
import unittest
import urllib.request
import urllib.error
import zipfile
from unittest.mock import patch
import server
import test_demo
from test_statements import HTML


class BrowserHttpTests(unittest.TestCase):
    client = test_demo.HttpTests.client
    request = test_demo.HttpTests.request

    @classmethod
    def setUpClass(cls):
        test_demo.HttpTests.setUpClass.__func__(cls)
        server.BRIDGE.jobs.clear()

    @classmethod
    def tearDownClass(cls):
        server.BRIDGE.jobs.clear()
        test_demo.HttpTests.tearDownClass.__func__(cls)

    def prepare(self, name):
        c = self.client(name)
        self.request(c, '/api/import', {'text': json.dumps([test_demo.record(10,'WRONG_ANSWER'),test_demo.record(11)])})
        status, body = self.request(c, '/api/browser/create', {'problemKey':'1900A'})
        self.assertEqual(status, 200)
        return c, body['job']

    def deliver(self, job, **changes):
        payload = {'id':job['id'], 'url':job['url']+'?locale=en', 'html':HTML} | changes
        request = urllib.request.Request(self.base+'/api/browser/result', data=json.dumps(payload).encode(),
                  headers={'Content-Type':'application/json','X-VPLab-Token':job['token'], 'Origin':'chrome-extension://'+'a'*32})
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with client.open(request,timeout=5) as response:return response.status, json.load(response)
        except urllib.error.HTTPError as exc:return exc.code,json.load(exc)

    def test_capture_and_ai_context_private_cache(self):
        owner,j = self.prepare('capture_owner')
        other,_ = self.prepare('capture_other')
        status,r = self.deliver(j)
        self.assertEqual(status,200);self.assertEqual(r['job']['status'],'succeeded')
        self.assertNotIn('token',r['job'])
        own=self.request(owner,'/api/problem/statement',{'problemKey':'1900A'})[1]['statement']
        self.assertEqual(own['textOrigin'],'codeforces_browser')
        uid=self.request(other,'/api/me')[1]['user']['id']
        self.assertIsNone(server.STATEMENTS.cached_for(uid,server.user_records(uid)[0]))
        preview=self.request(owner,'/api/analyses/preview',{'beforeSubmissionId':'10','afterSubmissionId':'11'})[1]['preview']
        self.assertEqual(preview['problem']['textOrigin'],'codeforces_browser')
        self.assertIn('An integer n.',preview['problem']['text'])
        # Replay is idempotent: a later payload cannot replace an accepted snapshot.
        self.assertEqual(self.deliver(j,html=HTML.replace('An integer n.','REPLACED'))[0],200)
        cached=self.request(owner,'/api/problem/statement',{'problemKey':'1900A'})[1]['statement']
        self.assertNotIn('REPLACED',cached['text'])

    def test_tokens_problem_matching_and_account_isolation(self):
        c,j=self.prepare('capture_auth')
        other=self.client('capture_intruder')
        self.assertEqual(self.request(other,'/api/browser/status?id='+j['id'])[0],404)
        self.assertEqual(self.request(other,'/api/browser/create',{'problemKey':'1900A'})[0],404)
        self.assertEqual(self.deliver(j|{'token':'wrong'})[0],401)
        self.assertEqual(self.deliver(j,url='https://codeforces.com/contest/1900/problem/B')[0],422)
        self.assertEqual(self.deliver(j,html='<html>Please verify</html>')[0],422)
        status=self.request(c,'/api/browser/status?id='+j['id'])[1]['job']
        self.assertEqual(status['status'],'waiting');self.assertNotIn('token',status)
        self.request(c,'/api/logout',{})
        self.assertEqual(self.deliver(j)[0],401)

    def test_expiry_and_superseded_tasks(self):
        c,j=self.prepare('capture_expiry')
        new=self.request(c,'/api/browser/create',{'problemKey':'1900A'})[1]['job']
        self.assertEqual(self.deliver(j)[0],409)
        with patch('browser_bridge.time.time',return_value=time.time()+700):
            self.assertEqual(self.deliver(new)[0],409)
            self.assertEqual(self.request(c,'/api/browser/status?id='+new['id'])[1]['job']['status'],'expired')

    def test_extension_zip_and_cors_scope(self):
        client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        status,blob=self.request(client,'/browser-helper.zip')
        self.assertEqual(status,200)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            manifest=json.loads(z.read('VP-Lab-Browser-Helper/manifest.json'))
            self.assertEqual(manifest['manifest_version'],3)
            self.assertNotIn('cookies',manifest['permissions'])
            self.assertIn('VP-Lab-Browser-Helper/extract.js',z.namelist())
        req=urllib.request.Request(self.base+'/api/browser/result',method='OPTIONS',headers={'Origin':'chrome-extension://'+'a'*32})
        with client.open(req) as r:self.assertEqual(r.headers['Access-Control-Allow-Origin'],'chrome-extension://'+'a'*32)
        self.assertEqual(self.request(client,'/api/browser/result',{},origin='https://attacker.example')[0],403)
