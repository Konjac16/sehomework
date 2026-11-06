import base64
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
import http.cookiejar
import zipfile
import hashlib
import time
from unittest.mock import patch

import server
from adapters import parse_import, CodeforcesAdapter, normalize_api_credential, network_error_message
from code_diff import compare_sources
from domain import summarize, predict


def record(sid=10, verdict="OK", index="A", source="int main(){return 0;}"):
    return {"handle": "tester", "submission": {"id": sid, "contestId": 1900,
        "creationTimeSeconds": 1700000000 + sid, "verdict": verdict,
        "problem": {"contestId": 1900, "index": index, "name": "Test <safe>", "rating": 1600, "tags": ["dp", "math"]},
        "author": {"members": [{"handle": "tester"}], "participantType": "PRACTICE"},
        "programmingLanguage": "C++17", "sourceBase64": base64.b64encode(source.encode()).decode()}}


class DomainTests(unittest.TestCase):
    def test_credential_copy_formatting_and_private_errors(self):
        self.assertEqual(normalize_api_credential('**fake-key&#xA0;**', 'API Key'), 'fake-key')
        self.assertEqual(normalize_api_credential(' fake-secret\u00a0 ', 'API Secret'), 'fake-secret')
        self.assertEqual(normalize_api_credential('`fake-key`', 'API Key'), 'fake-key')
        with self.assertRaises(ValueError) as caught:
            normalize_api_credential('secret: fake-sensitive-value', 'API Secret')
        self.assertNotIn('fake-sensitive-value', str(caught.exception))
        with patch('adapters.urllib.request.getproxies', return_value={'https':'http://fake-proxy'}):
            message = network_error_message(urllib.error.URLError(ConnectionRefusedError('fake-secret')))
        self.assertIn('代理', message)
        self.assertNotIn('fake-secret', message)
        self.assertIn('超时', network_error_message(TimeoutError('fake-secret')))

    def test_running_server_must_identify_as_compatible_vplab(self):
        with patch('server.urllib.request.build_opener') as build:
            response = build.return_value.open.return_value.__enter__.return_value
            for payload, expected in (({'app':'vplab','apiVersion':server.API_VERSION}, True),
                                      ({'app':'vplab','apiVersion':server.API_VERSION-1}, False),
                                      ({'app':'other','apiVersion':7}, False),
                                      ({'app':'vplab','apiVersion':1}, False), ([], False)):
                response.read.return_value = json.dumps(payload).encode()
                self.assertEqual(server.running_vplab(8765), expected)
            response.read.return_value = b'not json'
            self.assertFalse(server.running_vplab(8765))

    def test_signature_and_source_page(self):
        with patch('adapters.time.time', return_value=1700000000), patch('adapters.secrets.token_hex', return_value='abcdef'):
            signed = CodeforcesAdapter.signed_params('user.status', {'handle': 'tester', 'from': 1, 'includeSources': 'true'}, 'test-key', 'test-secret')
        canonical = 'abcdef/user.status?apiKey=test-key&from=1&handle=tester&includeSources=true&time=1700000000#test-secret'
        self.assertEqual(signed['apiSig'], 'abcdef' + hashlib.sha512(canonical.encode()).hexdigest())
        self.assertNotIn('test-secret', json.dumps(signed))
        adapter = CodeforcesAdapter()
        with patch.object(adapter, 'call', return_value=[record()['submission']]) as call:
            rows = adapter.source_page('tester', 'test-key', 'test-secret', start=201)
        self.assertEqual(rows[0]['source'], 'int main(){return 0;}')
        self.assertEqual(call.call_args.kwargs['includeSources'], 'true')
        self.assertEqual(call.call_args.kwargs['from'], 201)

    def test_diff_highlights_small_edits_and_line_numbers(self):
        diff = compare_sources('int n;\nif (i <= n) {\n  run();\n}\n', 'int n;\nif (i < n) {\n  run();\n  done();\n}\n')
        self.assertEqual((diff['removed'], diff['added']), (1, 2))
        rows = [r for h in diff['hunks'] for r in h]
        modified = next(r for r in rows if r['oldLine'] == 2)
        self.assertIn(['=', True], modified['old'])
        added = next(r for r in rows if r['newLine'] == 4)
        self.assertIsNone(added['oldLine'])
        self.assertEqual(''.join(s for s, _ in added['new']), '  done();')
        self.assertTrue(compare_sources('x\n', 'x\n')['identical'])
        self.assertFalse(compare_sources('x', 'x\n')['identical'])
        self.assertTrue(compare_sources('x\r\n', 'x\n')['lineEndingsDiffer'])
        self.assertEqual(len(compare_sources('x\n', 'x\n', full=True)['hunks'][0]), 1)

    def test_http_api_errors_are_read_without_exposing_credentials(self):
        error = urllib.error.HTTPError('https://codeforces.com/api/user.status?apiKey=fake-key', 400, 'Bad Request', {},
            io.BytesIO(json.dumps({'status':'FAILED', 'comment':'Bad fake-secret / fake-key'}).encode()))
        with patch('adapters.urllib.request.urlopen', side_effect=error), patch('adapters.time.sleep'):
            with self.assertRaises(ValueError) as caught:
                CodeforcesAdapter().call('user.status', api_key='fake-key', api_secret='fake-secret', handle='tester')
        self.assertIn('Bad', str(caught.exception))
        self.assertNotIn('fake-secret', str(caught.exception))
        self.assertNotIn('fake-key', str(caught.exception))

    def test_diff_context_limits_and_escaping_boundary(self):
        before = ''.join(f'line{i}\n' for i in range(50))
        diff = compare_sources(before, before.replace('line25', '<script>alert(1)</script>'))
        self.assertEqual(len(diff['hunks'][0]), 7)
        self.assertEqual(len(compare_sources(before, before, full=True)['hunks'][0]), 50)
        with self.assertRaises(ValueError):
            compare_sources('x' * 200001, 'y')

    def test_real_export_counts_when_available(self):
        sample = Path("E:/codeforces_submissions.jsonl")
        if not sample.exists():
            self.skipTest("real export not on this machine")
        rows = parse_import(sample.read_text(encoding="utf-8-sig"))
        summary = summarize(rows)
        self.assertEqual(summary["stats"]["submissions"], 309)
        self.assertEqual(summary["stats"]["solved"], 279)
        self.assertEqual(summary["stats"]["sources"], 309)
        self.assertEqual(summary["stats"]["rated"], 222)
        self.assertTrue(summary["onlyAccepted"])

    def test_unique_problems_and_multiple_attempts(self):
        rows = parse_import(json.dumps([record(10, "WRONG_ANSWER"), record(11), record(12, "TIME_LIMIT_EXCEEDED", "B")]))
        s = summarize(rows)
        self.assertEqual(s["stats"]["problems"], 2)
        self.assertEqual(s["stats"]["solved"], 1)
        self.assertFalse(s["onlyAccepted"])
        self.assertEqual(next(p for p in s["problems"] if p["problemKey"] == "1900A")["attempts"], 2)
        self.assertEqual(s["tags"][0]["solved"], 1)

    def test_probability_boundaries_and_already_solved(self):
        summary = summarize(parse_import(json.dumps([record()])))
        self.assertIsNone(predict(summary, 1800, 1600, "1900A")["probability"])
        self.assertIsNone(predict(summary, None, 1600)["probability"])
        self.assertIsNone(predict(summary, 1800, 1600, platform="qoj")["probability"])
        easy = predict(summary, 1800, 1200)
        hard = predict(summary, 1800, 2400)
        self.assertGreater(easy["probability"], hard["probability"])
        self.assertFalse(easy["calibrated"])
        self.assertEqual(easy["nearbyObserved"], 0)

    def test_invalid_import_rejected(self):
        with self.assertRaises(ValueError):
            parse_import('{"oops": 1}')
        bad = record()
        bad["submission"]["sourceBase64"] = "!!!"
        with self.assertRaises(ValueError):
            parse_import(json.dumps([bad]))


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.original_db = server.DB
        server.DB = Path(cls.tmp.name) / "test.sqlite3"
        server.init_db()
        cls.http = server.LocalServer(("127.0.0.1", 0), server.Handler)
        cls.base = f"http://127.0.0.1:{cls.http.server_port}"
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()
        server.DB = cls.original_db
        # SQLite connections are context-managed transactions; force object collection
        # so Windows can release temporary database handles before directory cleanup.
        import gc
        gc.collect()
        cls.tmp.cleanup()

    def client(self, name):
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.request(client, "/api/register", {"username": name, "password": "test-password-42"})
        return client

    def request(self, client, path, data=None, origin=None):
        req = urllib.request.Request(self.base + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Content-Type": "application/json", **({"Origin": origin} if origin else {})})
        try:
            with client.open(req, timeout=10) as response:
                blob = response.read()
                return response.status, json.loads(blob) if "json" in response.headers.get("Content-Type", "") else blob
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_import_dedup_source_preservation_and_export(self):
        c = self.client("import_test")
        payload = {"text": json.dumps([record()])}
        self.assertEqual(self.request(c, "/api/import", payload)[1]["added"], 1)
        self.assertEqual(self.request(c, "/api/import", payload)[1]["added"], 0)
        public = record(source="")
        self.request(c, "/api/import", {"text": json.dumps([public])})
        d = self.request(c, "/api/dashboard")[1]
        self.assertEqual(d["stats"]["sources"], 1)
        detail = self.request(c, "/api/problem?key=1900A")[1]
        self.assertEqual(detail["records"][0]["source"], "int main(){return 0;}")
        z = zipfile.ZipFile(io.BytesIO(self.request(c, "/api/export")[1]))
        self.assertIn("sources/10.txt", z.namelist())
        self.assertEqual(len(json.loads(z.read("records.normalized.json"))), 1)

    def test_boot_assets_and_exclusive_port(self):
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.assertEqual(self.request(client, '/api/health'), (200, {'app':'vplab','apiVersion':server.API_VERSION}))
        for path in ('/', '/source.js', '/app.js', '/review.js', '/review.css', '/statement.js', '/helper.js', '/training.js', '/training.css', '/duo', '/duo.js', '/duo.css'):
            status, body = self.request(client, path)
            self.assertEqual(status, 200)
            self.assertGreater(len(body), 100)
        with self.assertRaises(OSError):
            duplicate = server.LocalServer(('127.0.0.1', self.http.server_port), server.Handler)
            duplicate.server_close()

    def test_duplicate_launcher_opens_existing_server(self):
        with patch('sys.argv', ['server.py', '--port', str(self.http.server_port), '--browser']), \
             patch.object(server.webbrowser, 'open', return_value=True) as browser, \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            server.main()
        browser.assert_called_once_with(self.base)
        self.assertIn('already running', output.getvalue())
        self.assertTrue(server.running_vplab(self.http.server_port))

    def test_training_routes_require_ac_and_isolate_users(self):
        alice, bob = self.client('training_alice'), self.client('training_bob')
        anonymous = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.assertEqual(self.request(anonymous, '/api/training')[0], 401)
        self.assertEqual(self.request(anonymous, '/api/recommendations', {})[0], 401)
        self.request(alice, '/api/import', {'text': json.dumps([record(index='Z')])})
        p = {'platform':'codeforces','externalId':'1900A','problemKey':'1900A','title':'Example',
             'rating':1800,'tags':['hidden-tag'],'url':'https://codeforces.com/contest/1900/problem/A'}
        with patch.object(server.TRAINING, 'catalog', return_value=[p]):
            status, state = self.request(alice, '/api/recommendations', {'mode':'daily'})
            self.assertEqual(status, 200)
            self.assertNotIn('hidden-tag', json.dumps(state))
            self.assertEqual(state['referenceRating'], 1800)
            state = self.request(alice, '/api/training/start', {'problemKey':'1900A'})[1]
            run = state['active']
            self.assertEqual(self.request(bob, '/api/training/skip', {'id':run['id']})[0], 400)
            self.assertEqual(self.request(alice, '/api/training/feedback', {'id':run['id'],'completionMode':'independent'})[0], 400)
            row = record(sid=999)
            row['submission']['creationTimeSeconds'] = run['startedAt'] + 1
            self.request(alice, '/api/import', {'text':json.dumps([row])})
            self.assertEqual(self.request(alice, '/api/training/skip', {'id':run['id']})[0], 400)
            self.assertEqual(self.request(alice, '/api/training/feedback', {'id':run['id']})[0], 400)
            state = self.request(alice, '/api/training/feedback', {'id':run['id'],'completionMode':'hint'})[1]
            self.assertEqual(state['history'][0]['status'], 'completed')
            self.assertEqual(self.request(bob, '/api/training')[1]['history'], [])
            self.assertIsNone(self.request(alice, '/api/recommendations', {})[1]['recommendation'])
            self.assertEqual(self.request(alice, '/api/recommendations', {'mode':'focused'})[0], 400)

    def test_accounts_and_task_permissions(self):
        alice, bob = self.client("alice_test"), self.client("bob_test")
        self.request(alice, "/api/import", {"text": json.dumps([record()])})
        self.assertEqual(self.request(bob, "/api/dashboard")[1]["stats"]["submissions"], 0)
        self.assertEqual(self.request(bob, "/api/problem?key=1900A")[0], 404)
        self.request(alice, "/api/tasks/create", {"title": "Try DP", "rating": 1800, "tags": ["dp"]})
        tasks = self.request(alice, "/api/dashboard")[1]["tasks"]
        tid = tasks[0]["id"]
        self.assertEqual(self.request(bob, "/api/tasks/update", {"id": tid, "pinned": True})[0], 404)
        self.assertEqual(self.request(alice, "/api/tasks/update", {"id": tid, "done": True})[0], 400)
        self.request(alice, "/api/tasks/update", {"id": tid, "done": True, "completionMode": "editorial", "note": "Learned transitions"})
        d = self.request(alice, "/api/dashboard")[1]
        self.assertEqual(d["tasks"][0]["level"], "已完成")
        self.assertEqual(d["stats"]["solved"], 1)  # manual completion creates no AC

    def test_login_logout_and_csrf(self):
        c = self.client("login_test")
        self.assertEqual(self.request(c, "/api/settings", {}, "https://evil.example")[0], 403)
        self.request(c, "/api/logout", {})
        self.assertEqual(self.request(c, "/api/dashboard")[0], 401)
        self.assertEqual(self.request(c, "/api/login", {"username": "login_test", "password": "wrong-password"})[0], 400)
        self.assertEqual(self.request(c, "/api/login", {"username": "login_test", "password": "test-password-42"})[0], 200)
        self.assertEqual(self.request(c, "/api/me")[1]["user"]["username"], "login_test")

    def test_malformed_import_is_atomic_and_handle_cannot_mix(self):
        c = self.client("atomic_test")
        self.assertEqual(self.request(c, "/api/import", {"text": json.dumps([record(), {"bad": True}])})[0], 400)
        self.assertEqual(self.request(c, "/api/dashboard")[1]["stats"]["submissions"], 0)
        self.request(c, "/api/import", {"text": json.dumps([record()])})
        other = record(11)
        other["handle"] = "someone_else"
        self.assertEqual(self.request(c, "/api/import", {"text": json.dumps([other])})[0], 400)
        self.assertEqual(self.request(c, "/api/dashboard")[1]["stats"]["submissions"], 1)

    def test_settings_priority_and_prediction(self):
        c = self.client("settings_test")
        self.request(c, "/api/settings", {"referenceRating": 1800, "focusTags": ["dp"]})
        self.request(c, "/api/tasks/create", {"title": "Hard", "rating": 2600, "tags": []})
        self.request(c, "/api/tasks/create", {"title": "Near", "rating": 1800, "tags": ["dp"]})
        d = self.request(c, "/api/dashboard")[1]
        self.assertEqual(d["referenceRating"], 1800)
        self.assertEqual(d["tasks"][0]["title"], "Near")
        self.assertEqual(d["tasks"][1]["level"], "暂缓")
        r = self.request(c, "/api/predict", {"referenceRating": 1800, "problemRating": 1800})[1]
        self.assertEqual(r["status"], "estimated")
        self.assertFalse(r["calibrated"])
        self.assertEqual(self.request(c, "/api/tasks/create", {"title": "Bad link", "url": "javascript:alert(1)"})[0], 400)

    def test_sync_error_retains_records(self):
        c = self.client("network_test")
        self.request(c, "/api/import", {"text": json.dumps([record()])})
        with patch.object(server.ADAPTERS["codeforces"], "sync", side_effect=ValueError("Network unavailable")):
            self.assertEqual(self.request(c, "/api/sync", {"handle": "tester"})[0], 400)
        self.assertEqual(self.request(c, "/api/dashboard")[1]["stats"]["submissions"], 1)

    def wait_job(self, client):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = self.request(client, '/api/sync/source/status')[1]['job']
            if job and job['status'] != 'running':
                return job
            threading.Event().wait(.01)
        self.fail('source worker did not finish')

    def test_history_and_diff_permissions(self):
        c, other = self.client('history_test'), self.client('history_other')
        rows = [record(i, 'WRONG_ANSWER' if i < 30 else 'OK', source=f'if (x < {i}) return 0;\n') for i in range(1, 31)]
        rows.append(record(31, index='B', source=''))
        self.request(c, '/api/import', {'text': json.dumps(rows)})
        history = self.request(c, '/api/submissions')[1]
        self.assertEqual((history['total'], len(history['rows'])), (31, 25))
        self.assertEqual(history['rows'][0]['externalId'], '31')
        self.assertNotIn('source', history['rows'][0])
        self.assertEqual(len(self.request(c, '/api/submissions?page=2')[1]['rows']), 6)
        self.assertEqual(self.request(c, '/api/submissions?verdict=OK&source=yes')[1]['total'], 1)
        self.assertEqual(self.request(c, '/api/submissions?q=1900B')[1]['total'], 1)
        self.assertEqual(self.request(c, '/api/submissions?source=no')[1]['total'], 1)
        self.assertEqual(self.request(c, '/api/diff?before=29&after=30')[1]['added'], 1)
        self.assertEqual(self.request(c, '/api/diff?before=29&after=31')[0], 400)
        self.assertEqual(self.request(other, '/api/submission?id=30')[0], 404)
        self.assertEqual(self.request(other, '/api/diff?before=29&after=30')[0], 404)
        self.assertEqual(self.request(other, '/api/submissions')[1]['total'], 0)
        self.request(c, '/api/import', {'text': json.dumps([record(32, source='')])})
        self.assertEqual(self.request(c, '/api/diff?before=30&after=32')[0], 400)

    def test_source_sync_all_pages_and_no_credentials_saved(self):
        c = self.client('source_pages')
        adapter = server.ADAPTERS['codeforces']
        pages = [parse_import(json.dumps([record(i) for i in range(1, 201)])), parse_import(json.dumps([record(201, 'WRONG_ANSWER')]))]
        with patch.object(adapter, 'call', return_value=[{'handle': 'tester', 'rating': 1800}]), patch.object(adapter, 'source_page', side_effect=pages) as fetch:
            status, result = self.request(c, '/api/sync/source', {'handle':'tester', 'apiKey':'test-key-unique', 'apiSecret':'test-secret-unique'})
            self.assertEqual(status, 202)
            job = self.wait_job(c)
        self.assertEqual((job['status'], job['received'], job['sources']), ('completed', 201, 201))
        self.assertTrue(job['exhausted'])
        self.assertEqual([call.kwargs['start'] for call in fetch.call_args_list], [1, 201])
        self.assertEqual(self.request(c, '/api/dashboard')[1]['stats']['submissions'], 201)
        with server.connect() as db:
            settings = str([dict(r) for r in db.execute('SELECT settings FROM users')])
        self.assertNotIn('test-secret-unique', settings + json.dumps(result) + json.dumps(job))
        self.assertNotIn('test-key-unique', settings + json.dumps(result) + json.dumps(job))

    def test_source_partial_failure_is_retained_and_redacted(self):
        c = self.client('source_failure')
        adapter = server.ADAPTERS['codeforces']
        page = parse_import(json.dumps([record(i) for i in range(1, 201)]))
        with patch.object(adapter, 'call', return_value=[{'handle':'tester'}]), patch.object(adapter, 'source_page', side_effect=[page, ValueError('test-secret-sensitive network failed')]):
            self.request(c, '/api/sync/source', {'handle':'tester','apiKey':'test-key-sensitive','apiSecret':'test-secret-sensitive'})
            job = self.wait_job(c)
        self.assertEqual(job['status'], 'failed')
        self.assertNotIn('test-secret-sensitive', json.dumps(job))
        self.assertEqual(self.request(c, '/api/dashboard')[1]['stats']['submissions'], 200)

    def test_source_cancel_and_duplicate_job(self):
        c, other = self.client('source_cancel'), self.client('source_no_job')
        entered, release = threading.Event(), threading.Event()
        def blocked_page(*args, **kwargs):
            entered.set()
            release.wait(4)
            return parse_import(json.dumps([record()]))
        adapter = server.ADAPTERS['codeforces']
        payload = {'handle':'tester','apiKey':'fake-key','apiSecret':'fake-secret'}
        with patch.object(adapter, 'call', return_value=[{'handle':'tester'}]), patch.object(adapter, 'source_page', side_effect=blocked_page):
            try:
                self.assertEqual(self.request(c, '/api/sync/source', payload)[0], 202)
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.request(c, '/api/sync/source', payload)[0], 400)
                self.assertIsNone(self.request(other, '/api/sync/source/status')[1]['job'])
                self.assertIsNone(self.request(other, '/api/sync/source/cancel', {})[1]['job'])
                self.assertTrue(self.request(c, '/api/sync/source/cancel', {})[1]['job']['cancelRequested'])
            finally:
                release.set()
            self.assertEqual(self.wait_job(c)['status'], 'cancelled')
        self.assertEqual(self.request(c, '/api/dashboard')[1]['stats']['submissions'], 0)


if __name__ == "__main__":
    unittest.main()
