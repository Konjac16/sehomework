import json
import sqlite3
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_intelligent as fixtures
from cf_editorials import (CFEditorialProvider, EditorialError, AccessRestricted,
                           Cancelled, Document, extract, section_from_blog,
                           section_from_pdf, public_url, source_from_submission)
from editorial_service import EditorialService
from recommendation_pool import PoolError


def material():
    return {'text':'Editorial fixture: use a carefully defined invariant. '*4,
            'codeBlocks':[{'text':'<script>not HTML</script>\nint main() {}',
                           'verification':'editorial_attached_not_judge_verified'}],
            'sourceUrl':'https://codeforces.com/blog/entry/1','method':'blog_section','warnings':[]}


def seed_public_editorials(db, count=13, variants=False):
    """Browser fixture: all public texts explicitly synthetic; no network acquisition."""
    with sqlite3.connect(db) as c:
        for i in range(1,count+1):
            data=material()
            if variants and i==2:
                data.update(codeBlocks=[],codeDiscoveryVersion=CFEditorialProvider.CODE_VERSION,
                            codeLinks=[{'url':'https://codeforces.com/contest/1/submission/123','status':'access_restricted','message':'Fixture: source unavailable'}])
            if variants and i==3:data['text']=''
            c.execute('INSERT OR REPLACE INTO training_public_materials VALUES(?,?,?,?,?)',
                      (f'cf:1:A{i}','editorial','fixture',json.dumps(data),int(time.time())))


class ExtractionTests(unittest.TestCase):
    def test_preformatted_sample_is_retained_as_text_not_reference_code(self):
        doc=Document('<p>'+('Detailed editorial explanation. '*5)+'</p><pre>13 5\nAAAAA\nBBBBB</pre><pre>int main() { return 0; }</pre>')
        result=extract(doc.root.children)
        self.assertIn('13 5\nAAAAA\nBBBBB',result['text'])
        self.assertEqual([b['text'] for b in result['codeBlocks']],['int main() { return 0; }'])

    def test_code_survives_dynamic_tutorial_placeholder_without_adjacent_problem(self):
        provider=CFEditorialProvider();provider.csrf='fixture'
        blog='<p><a href="/contest/12/problem/C">12C - Title</a></p><div>Tutorial is loading...</div><div class="spoiler"><b class="spoiler-title">Code</b><pre>int main() { return 0; }</pre></div><p><a href="/contest/12/problem/D">12D - Other</a></p><pre>WRONG PROBLEM</pre>'
        direct=json.dumps({'success':True,'html':'<p>'+'Actual proof. '*20+'</p>'}).encode()
        page=b'<div class="problem-statement">public</div><a href="/blog/entry/2">Tutorial</a>'
        with patch.object(provider,'_request',side_effect=[direct,page,json.dumps({'status':'OK','result':{'content':blog}}).encode()]) as request:
            result=provider.fetch({'contestId':12,'index':'C','title':'Title'},threading.Event())
        self.assertEqual(request.call_count,3)
        self.assertIn('Actual proof',result['text'])
        self.assertEqual(result['codeBlocks'][0]['text'],'int main() { return 0; }')
        self.assertEqual(result['codeStatus'],'ready')

    def test_submission_restriction_preserves_text_and_links_and_suppresses_retries(self):
        provider=CFEditorialProvider()
        blog='<h2>12C - Title</h2><p>'+'Proof '*30+'</p><a href="/contest/12/submission/123">Code</a>'
        provider.blog_cache['2']=blog
        previous={'text':'Good original proof '*20,'codeBlocks':[],'sourceUrl':'https://codeforces.com/blog/entry/2','method':'blog_section'}
        with patch.object(provider,'_request',side_effect=AccessRestricted('HTTP 403','http_403')) as request:
            a=provider.fetch({'contestId':12,'index':'C','title':'Title'},threading.Event(),existing=previous)
            b=provider.fetch({'contestId':12,'index':'C','title':'Title'},threading.Event(),existing=a)
        request.assert_called_once()
        self.assertEqual(a['text'],previous['text'])
        self.assertEqual(b['codeStatus'],'links_only')
        self.assertEqual(b['codeLinks'][0]['status'],'access_restricted')
        self.assertNotIn('stopReason',b)

    def test_submission_source_requires_correct_problem_and_submission(self):
        html='<tr data-submission-id="123"><td><a href="/contest/12/problem/C">C</a></td><td class="submissionVerdict">Accepted</td></tr><pre id="program-source-text">int main() { if (1 &lt; 2) return 0; }</pre>'
        p={'contestId':12,'index':'C'}
        self.assertIn('1 < 2',source_from_submission(html,p,'123')['text'])
        for problem,sid in [(p,'999'),({'contestId':12,'index':'D'},'123')]:
            with self.assertRaises(EditorialError):source_from_submission(html,problem,sid)

    def test_code_links_alone_do_not_count_as_editorial_text(self):
        html='<h2>12C - Title</h2><div>Tutorial is loading...</div><a href="/contest/12/submission/123">Code</a>'
        result=section_from_blog(html,{'contestId':12,'index':'C','title':'Title'},allow_partial=True)
        self.assertEqual(result['text'],'')
        self.assertEqual(result['codeBlocks'],[])
        self.assertEqual(result['codeLinks'][0]['status'],'found')

    def test_placeholder_is_not_success_and_math_script_is_preserved(self):
        for body in ['Tutorial is loading... '*20,'Unable to parse markup'+'x'*100]:
            with self.assertRaises(EditorialError):extract(Document('<p>'+body+'</p>').root.children)
        result=extract(Document('<p>'+'Proof context '*20+'<script type="math/tex">x^2</script></p><pre>if (a &lt; b)\n  f();</pre>').root.children)
        self.assertIn('$$$x^2$$$',result['text'])
        self.assertEqual(result['codeBlocks'][0]['text'],'if (a < b)\n  f();')

    def test_blog_is_exact_section_and_rejects_wrong_version(self):
        html='<h2><a href="/contest/12/problem/C2">12C2 - Title</a></h2><p>'+'Hard version proof. '*20+'</p><h2>12D - Other</h2><p>UNRELATED</p>'
        with self.assertRaises(EditorialError):section_from_blog(html,{'contestId':12,'index':'C1','title':'Title'})
        self.assertNotIn('UNRELATED',section_from_blog(html,{'contestId':12,'index':'C2','title':'Title'})['text'])

    def test_pdf_title_and_section_bounds_without_cutting_english_article(self):
        pages=['E Duplicates\n'+'Proof '*30+'\nA similar argument handles the other case.\nF Next Problem\nUNRELATED']
        reader=SimpleNamespace(is_encrypted=False,pages=[SimpleNamespace(extract_text=lambda:t) for t in pages])
        with patch.dict('sys.modules',{'pypdf':SimpleNamespace(PdfReader=lambda _:reader)}):
            result=section_from_pdf(b'fixture',{'index':'E','title':'Duplicates'})
            self.assertIn('A similar argument',result['text'])
            self.assertNotIn('UNRELATED',result['text'])
            self.assertEqual(result['pdfPages'],[1])
            with self.assertRaises(EditorialError):section_from_pdf(b'fixture',{'index':'E','title':'Wrong title'})

    def test_external_addresses_are_bounded(self):
        for url in ['http://codeforces.com/a','https://127.0.0.1/a.pdf','https://codeforces.com.evil/a.pdf','https://codeforces.com:443/a.pdf']:
            with self.assertRaises(EditorialError):public_url(url)

    def test_direct_failure_uses_blog_with_same_public_provider(self):
        provider=CFEditorialProvider();provider.csrf='fixture'
        page='<div class="problem-statement">public</div><a href="/blog/entry/2">Tutorial</a>'
        blog='<h2>12C - Title</h2><p>'+'Meaningful proof. '*20+'</p>'
        responses=[b'{"success":false}',page.encode(),json.dumps({'status':'OK','result':{'content':blog}}).encode()]
        with patch.object(provider,'_request',side_effect=responses) as request:
            result=provider.fetch({'contestId':12,'index':'C','title':'Title'},threading.Event())
        self.assertEqual(result['method'],'blog_section');self.assertEqual(request.call_count,3)


class ServiceTests(unittest.TestCase):
    seed_materials=False
    setUp=fixtures.PoolPreparationTests.setUp
    connect=fixtures.PoolPreparationTests.connect

    def service(self, fetch=None):
        provider=Mock();provider.fetch=Mock(side_effect=fetch) if fetch else Mock(return_value=material())
        self.pool.refresh(1)
        # Individual acquisition tests focus on ownership/caching, independent of pool admission.
        with self.connect() as c:c.execute("UPDATE smart_pool_entries SET status='candidate' WHERE user_id=1 AND problem_id='cf:1:A1'")
        return EditorialService(self.connect,self.pool.lock,provider,lambda:self.now)

    def wait_job(self, service):
        deadline=time.monotonic()+5
        while service.status(1)['status']=='running' and time.monotonic()<deadline:time.sleep(.01)
        self.assertNotEqual(service.status(1)['status'],'running')
        return service.status(1)

    def test_cache_persists_refresh_and_user_scope_and_no_spoilers(self):
        s=self.service()
        with self.assertRaises(PoolError):s.prepare_one(2,'cf:1:A1')
        self.assertEqual(s.prepare_one(1,'cf:1:A1')['status'],'ready')
        self.assertEqual(s.prepare_one(1,'cf:1:A1')['status'],'cached')
        s.provider.fetch.assert_called_once()
        state=self.pool.refresh(1)
        self.assertEqual(state['editorialReady'],1)
        self.assertEqual(state['referenceCodeReady'],1)
        self.assertNotIn('invariant',json.dumps(state))
        self.assertIn('invariant',self.pool.material(1,'cf:1:A1')['editorial']['text'])
        with self.connect() as c:c.execute("DELETE FROM training_public_materials WHERE kind='statement'")
        self.assertIn('Describe an independent public problem',self.pool.material(1,'cf:1:A1')['material']['statement']['body'])

    def test_failure_is_recorded_and_not_repeated_immediately(self):
        s=self.service(lambda *a:(_ for _ in ()).throw(EditorialError('No exact section','unresolved')))
        self.assertEqual(s.prepare_one(1,'cf:1:A1')['status'],'failed')
        self.assertEqual(s.prepare_one(1,'cf:1:A1')['status'],'deferred')
        s.provider.fetch.assert_called_once()
        state=self.pool.state(1)
        entry=next(e for e in state['candidates'] if e['id']=='cf:1:A1')
        self.assertEqual(entry['materials']['editorial'],'failed')
        self.assertEqual(state['editorialReady'],0)

    def test_old_text_cache_is_upgraded_without_losing_text(self):
        s=self.service()
        old=material();old['codeBlocks']=[]
        with self.connect() as c:
            c.execute('INSERT INTO training_public_materials VALUES(?,?,?,?,?)',('cf:1:A1','editorial','old',json.dumps(old),self.now))
        result=s.prepare_one(1,'cf:1:A1')
        self.assertEqual(result['status'],'ready')
        self.assertEqual(s.provider.fetch.call_args.kwargs['existing']['text'],old['text'])
        self.assertEqual(self.pool.state(1)['referenceCodeReady'],1)

    def test_failed_upgrade_preserves_old_text_and_restricts_repeated_attempts(self):
        s=self.service(lambda *a,**kw:(_ for _ in ()).throw(EditorialError('network','network_error')))
        old=material();old['codeBlocks']=[]
        with self.connect() as c:
            c.execute('INSERT INTO training_public_materials VALUES(?,?,?,?,?)',('cf:1:A1','editorial','old',json.dumps(old),self.now))
        self.assertEqual(s.prepare_one(1,'cf:1:A1')['status'],'failed')
        self.assertEqual(self.pool.material(1,'cf:1:A1')['editorial']['text'],old['text'])
        self.assertEqual(s.prepare_one(1,'cf:1:A1')['status'],'deferred')
        s.provider.fetch.assert_called_once()

    def test_links_only_are_separate_from_successfully_acquired_code(self):
        s=self.service()
        s.provider.fetch.return_value={'text':'','codeBlocks':[], 'codeLinks':[{'url':'https://codeforces.com/contest/1/submission/123','status':'access_restricted'}],
                                     'method':'partial_editorial','sourceUrl':'https://codeforces.com/blog/entry/1'}
        self.assertEqual(s.prepare_one(1,'cf:1:A1')['status'],'partial')
        state=self.pool.state(1)
        self.assertEqual((state['editorialReady'],state['referenceCodeReady'],state['referenceCodeLinksOnly']),(0,0,1))

    def test_access_restriction_stops_batch(self):
        s=self.service(lambda *a:(_ for _ in ()).throw(AccessRestricted('HTTP 429','http_429')))
        s.start(1);job=self.wait_job(s)
        self.assertEqual(job['status'],'blocked');self.assertEqual(job['code'],'http_429')
        s.provider.fetch.assert_called_once()

    def test_cancel_and_duplicate_start(self):
        entered=threading.Event()
        def fetch(p,cancel):
            entered.set();cancel.wait(3);raise Cancelled('cancelled','cancelled')
        s=self.service(fetch);s.start(1);self.assertTrue(entered.wait(2))
        s.start(1);s.cancel(1);self.assertEqual(self.wait_job(s)['status'],'cancelled')
        s.provider.fetch.assert_called_once()


class EditorialHttpTests(fixtures.PoolHttpTests):
    def test_editorial_routes(self):
        import urllib.request
        import server
        anonymous=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.assertEqual(self.request(anonymous,'/api/intelligent/editorials',{})[0],401)
        owner,other=self.client('editorial_owner'),self.client('editorial_other')
        self.request(owner,'/api/intelligent/prepare',{})
        self.assertEqual(self.request(owner,'/api/intelligent/editorials',{'url':'https://example.com'})[0],400)
        self.assertEqual(self.request(other,'/api/intelligent/editorials',{})[0],400)
        with patch.object(server.EDITORIALS.provider,'fetch',return_value=material()):
            self.assertEqual(self.request(owner,'/api/intelligent/editorials',{})[0],202)
            for _ in range(100):
                job=self.request(owner,'/api/intelligent/editorials')[1]['job']
                if job['status']!='running':break
                time.sleep(.01)
            self.assertEqual(job['status'],'completed')
        self.assertEqual(self.request(other,'/api/intelligent/editorials')[1]['job']['status'],'idle')
        self.assertGreater(self.request(owner,'/api/intelligent/pool')[1]['editorialReady'],0)


if __name__=='__main__':unittest.main()
