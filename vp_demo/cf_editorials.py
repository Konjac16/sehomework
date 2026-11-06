"""Bounded public CF editorial acquisition; no account credentials or code execution."""
from html.parser import HTMLParser
from http.cookiejar import CookieJar
import io
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from material_quality import is_reference_code_block

MAX_BYTES = 4_000_000
MAX_TEXT = 80_000
VOID = {'br','img','hr','input','meta','link','wbr','source','area','embed','param','col','base'}
BLOCK = {'p','div','li','ul','ol','table','tr','h1','h2','h3','h4','h5','h6'}


class EditorialError(ValueError):
    def __init__(self, message, code='unresolved', sources=None):
        super().__init__(message)
        self.code, self.sources = code, sources or []


class AccessRestricted(EditorialError):
    pass


class Cancelled(EditorialError):
    pass


class Node:
    def __init__(self, tag='', attrs=None):
        self.tag, self.attrs, self.children = tag, dict(attrs or []), []

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def text(self, code=False):
        if self.tag in ('script','style','button'):
            return ''
        if self.tag == 'br':
            return '\n'
        if self.tag == 'img':
            return '[图示：' + self.attrs.get('alt', '请查看来源') + ']'
        value = ''.join(c.text(code) if isinstance(c, Node) else c for c in self.children)
        if not code and self.tag in ('sub','sup'):
            return ('_(' if self.tag == 'sub' else '^(') + value + ')'
        return ('\n' + value + '\n') if self.tag in BLOCK else value


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def normalize(text):
    return re.sub(r'\s+', ' ', text).strip()


def classes(node):
    return node.attrs.get('class','').split()


def extract(nodes, allow_partial=False):
    """Keep original code text apart from editorial prose; never return raw HTML."""
    codes, text, images, links = [], [], False, []
    def visit(node):
        nonlocal images
        if isinstance(node, str):
            text.append(node)
            return
        if node.tag == 'script' and 'math/tex' in node.attrs.get('type',''):
            text.append(' $$$'+''.join(c for c in node.children if isinstance(c,str))+'$$$ ')
            return
        if node.tag in ('script','style','button') or 'spoiler-title' in classes(node):
            return
        if 'spoiler' in classes(node):
            title = next((n for n in node.walk() if 'spoiler-title' in classes(n)), None)
            if title and re.search(r'bonus|challenge|poll|rate the problem', title.text(), re.I):
                return
        if node.tag == 'a':
            href = urllib.parse.urljoin('https://codeforces.com',node.attrs.get('href',''))
            parsed = urllib.parse.urlparse(href)
            if parsed.scheme=='https' and parsed.netloc=='codeforces.com' and re.fullmatch(r'/(?:contest/[0-9]+|gym/[0-9]+|problemset)/submission/[0-9]+',parsed.path):
                if href not in links: links.append(href)
        if node.tag == 'pre':
            code = node.text(code=True).replace('\r\n','\n').strip('\n')
            block={'text':code, 'verification':'editorial_attached_not_judge_verified'}
            if is_reference_code_block(block):
                codes.append(block)
            elif code.strip():
                text.append('\n'+code+'\n')  # Keep examples as editorial evidence.
            return
        if node.tag == 'img':
            if 'tex' in node.attrs.get('class','').lower():
                text.append(' '+node.attrs.get('alt','[公式缺失]')+' ')
                return
            images = True
            text.append(node.text())
            return
        if node.tag in ('sub','sup','br'):
            text.append(node.text())
            return
        if node.tag in BLOCK:
            text.append('\n')
        for child in node.children:
            visit(child)
        if node.tag in BLOCK:
            text.append('\n')
    for node in nodes:
        visit(node)
    body = re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+', '\n\n', ''.join(text)).strip()
    text_ready = len(body)>=80 and not re.search(r'Unable to parse markup|Tutorial is (?:loading|not available)',body,re.I)
    if not text_ready:
        if not allow_partial or not (codes or links):
            raise EditorialError('未取得足够的题解正文。')
        body = ''
    if len(body) + sum(len(c['text']) for c in codes) > MAX_TEXT:
        raise EditorialError('题解资料超过本次大小限制，未截断保存。')
    warnings = ['题解包含图示，当前只读取了文字。'] if images else []
    return {'text':body, 'textStatus':'ready' if text_ready else 'missing',
            'codeBlocks':codes, 'codeLinks':[{'url':u,'status':'found'} for u in links], 'warnings':warnings}


def problem_links(node):
    keys = set()
    for n in node.walk():
        if n.tag != 'a':
            continue
        path = urllib.parse.urlparse(n.attrs.get('href','')).path
        match = re.fullmatch(r'/(?:contest|gym)/(\d+)/problem/([A-Za-z0-9]+)', path)
        if not match:
            match = re.fullmatch(r'/problemset/problem/(\d+)/([A-Za-z0-9]+)', path)
        if match:
            keys.add(f'{int(match[1])}{match[2]}')
    return keys


def section_from_blog(html, problem, allow_partial=False):
    doc = Document(html)
    root = next((n for n in doc.root.walk() if 'ttypography' in classes(n)), doc.root)
    # Blog API content is a fragment; unwrap only a single enclosing container.
    while True:
        children = [n for n in root.children if isinstance(n, Node) or str(n).strip()]
        if len(children)==1 and isinstance(children[0],Node) and children[0].tag in ('div','article','section'):
            root=children[0]
        else:
            break
    nodes = [n for n in root.children if isinstance(n,Node)]
    target = f"{problem['contestId']}{problem['index']}"
    headings = []
    for i,node in enumerate(nodes):
        label = normalize(node.text())
        keys = problem_links(node)
        full = re.match(r'^(\d{1,6}[A-Z][0-9]*)\s*(?:[-—.:]|\s)',label)
        if node.tag in ('h1','h2','h3','h4','h5','h6') or (node.tag=='p' and (full or keys) and len(label)<250):
            exact = target in keys or (full is not None and full[1]==target)
            # Letter-only sections need the actual problem title as corroboration.
            letter = re.match(r'^([A-Z][0-9]*)\s*[-—.:]\s*(.+)$',label)
            titled = bool(letter and letter[1]==problem['index'] and
                          normalize(letter[2]).casefold()==normalize(problem['title']).casefold())
            headings.append((i,node.tag,label,exact or titled))
    matched = [h for h in headings if h[3]]
    if len(matched)!=1:
        raise EditorialError('无法唯一定位这道题的题解段落，未使用整场题解。')
    start,tag,label,_ = matched[0]
    rank = int(tag[1]) if re.fullmatch('h[1-6]',tag) else 7
    end = len(nodes)
    for i,next_tag,next_label,_ in headings:
        if i<=start:
            continue
        # A nested Solution/Proof heading belongs to this problem; the next task does not.
        boundary = problem_links(nodes[i]) or re.match(r'^(?:\d{1,6})?[A-Z][0-9]*(?:/[A-Z][0-9]*)?\s*[-—.:]',next_label)
        next_rank = int(next_tag[1]) if re.fullmatch('h[1-6]',next_tag) else 7
        if boundary or (next_rank <= rank and tag!='p'):
            end=i
            break
    result = extract(nodes[start+1:end], allow_partial=True) if allow_partial else extract(nodes[start+1:end])
    return result | {'sectionHeading':label}


def cf_url(value, path_pattern):
    parsed = urllib.parse.urlparse(urllib.parse.urljoin('https://codeforces.com',value))
    if parsed.scheme!='https' or parsed.netloc!='codeforces.com' or not re.fullmatch(path_pattern,parsed.path):
        raise EditorialError('资料地址不在支持的公开 CF 路径内。')
    return 'https://codeforces.com'+parsed.path


# External sources are accepted only when linked by the CF contest materials.
PDF_HOSTS = {'codeforces.com', 'competition.binus.ac.id', 'fileserv.icpc.jp'}


def public_url(value):
    url = urllib.parse.urljoin('https://codeforces.com', value)
    p = urllib.parse.urlparse(url)
    if p.scheme != 'https' or p.netloc not in PDF_HOSTS:
        raise EditorialError('资料来源尚未支持自动读取。', 'unsupported_source', [url])
    return url


class RestrictedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def section_from_pdf(raw, problem):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise EditorialError('已找到 PDF；安装可选依赖 pypdf 后可自动提取。', 'pdf_dependency_missing') from None
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted or len(reader.pages) > 120:
            raise EditorialError('PDF 加密或页数超过限制。', 'pdf_unresolved')
        pages = [p.extract_text() or '' for p in reader.pages]
    except EditorialError:
        raise
    except Exception:
        raise EditorialError('PDF 无法提取文字，请查看来源。', 'pdf_unresolved') from None
    lines = [(i+1, line.strip()) for i, page in enumerate(pages) for line in page.splitlines()]
    norm = lambda s: re.sub(r'[^\w]+', '', s).casefold()
    pattern = re.compile(r'^([A-Z][0-9]*)[.\s]+(.+)$')
    hits = [j for j, (_, line) in enumerate(lines) if (m := pattern.match(line))
            and m[1] == problem['index'] and norm(m[2]) == norm(problem['title'])]
    if len(hits) != 1:
        raise EditorialError('PDF 无法唯一匹配题号和题名；未使用整份文件。', 'pdf_section_unresolved')
    start, end = hits[0], len(lines)
    next_index = chr(ord(problem['index'][0])+1)
    for j in range(start+1, len(lines)):
        boundary = pattern.match(lines[j][1])
        if boundary and boundary[1] == next_index and boundary[2][0].isupper():
            end = j
            break
    section = lines[start:end]
    text = '\n'.join(line for _, line in section if not re.search(r'Problem Analysis\s+\d+$', line))
    if len(text) < 100 or len(text) > MAX_TEXT:
        raise EditorialError('PDF 本题章节文字不足或过长。', 'pdf_section_unresolved')
    return {'text':text, 'codeBlocks':[], 'sectionHeading':lines[start][1],
            'pdfPages':sorted({page for page, _ in section}),
            'warnings':['PDF 自动提取可能丢失公式排版或图示，请对照原文件。']}


class CFEditorialProvider:
    VERSION = 'public-editorial-3'
    CODE_VERSION = 'editorial-code-2'

    def __init__(self):
        self.lock = threading.Lock()
        self.last_request = 0.0
        self.blog_cache, self.pdf_cache = {}, {}
        self.page_cache = {}
        self.source_blocked_until = 0
        self.opener = urllib.request.build_opener(RestrictedRedirect(), urllib.request.HTTPCookieProcessor(CookieJar()))
        self.csrf = None
        self.referer = 'https://codeforces.com/contest/1/problem/A?locale=en'
        self.request_count = 0

    def _request(self, url, cancel, data=None, headers=None):
        public_url(url)
        with self.lock:
            if cancel.wait(max(0, 2.2-(time.monotonic()-self.last_request))):
                raise Cancelled('已停止准备。', 'cancelled')
            self.last_request = time.monotonic()
            self.request_count += 1
            req = urllib.request.Request(url, data=data, headers={
                'User-Agent':'VPLab/0.3 (local study tool)', 'Accept-Language':'en', **(headers or {})})
            try:
                with self.opener.open(req, timeout=18) as response:
                    raw = response.read(MAX_BYTES+1)
                    if len(raw) > MAX_BYTES:
                        raise EditorialError('资料过大，未截断保存。', 'too_large')
                    return raw
            except urllib.error.HTTPError as exc:
                cls = AccessRestricted if exc.code in (401,403,429) else EditorialError
                raise cls(f'资料读取返回 HTTP {exc.code}。', f'http_{exc.code}') from None
            except OSError:
                raise EditorialError('资料连接超时或网络不可用。', 'network_error') from None

    def _fetch_text(self, problem, cancel):
        cid, index = problem['contestId'], problem['index']
        url = cf_url(f'/contest/{cid}/problem/{index}', r'/contest/[1-9][0-9]*/problem/[A-Za-z0-9]+')
        attempts, links = [], []
        if not self.csrf:
            page = self._request(self.referer, cancel).decode('utf-8')
            doc = Document(page)
            self.csrf = next((n.attrs.get('content') for n in doc.root.walk()
                              if n.tag == 'meta' and n.attrs.get('name') == 'X-Csrf-Token'), None)
            if not self.csrf or not any('problem-statement' in classes(n) for n in doc.root.walk()):
                raise AccessRestricted('CF 页面未正常加载，请在浏览器查看。', 'verification_required')
        try:
            body = urllib.parse.urlencode({'problemCode':f'{cid}{index}','csrf_token':self.csrf}).encode()
            data = json.loads(self._request('https://codeforces.com/data/problemTutorial', cancel, data=body,
                headers={'Referer':self.referer,'X-Csrf-Token':self.csrf,'X-Requested-With':'XMLHttpRequest'}))
            if isinstance(data,dict) and data.get('success') in (True,'true') and isinstance(data.get('html'),str):
                result = extract(Document(data['html']).root.children)
                return result | {'sourceUrl':url, 'retrievalUrl':'https://codeforces.com/data/problemTutorial',
                    'method':'problem_tutorial', 'sectionHeading':f'{cid}{index}', 'providerVersion':self.VERSION}
            attempts.append('direct_no_text')
        except (Cancelled, AccessRestricted):
            raise
        except (EditorialError, ValueError) as exc:
            attempts.append(getattr(exc,'code','invalid_response'))
        raw = self._request(url+'?locale=en', cancel)
        self.page_cache[url] = raw
        if len(self.page_cache)>100:self.page_cache.pop(next(iter(self.page_cache)))
        if raw.startswith(b'%PDF'):
            raise EditorialError('原题页返回题面 PDF，未发现题解入口。', 'statement_pdf', [url])
        doc = Document(raw.decode('utf-8'))
        if not any('problem-statement' in classes(n) for n in doc.root.walk()):
            raise AccessRestricted('原题页未正常加载。', 'verification_required', [url])
        for n in doc.root.walk():
            if n.tag != 'a' or not re.search(r'tutorial|editorial|solution', n.text(), re.I):
                continue
            link = urllib.parse.urljoin(url,n.attrs.get('href',''))
            parsed = urllib.parse.urlparse(link)
            if (parsed.netloc == 'codeforces.com' and re.fullmatch(r'/blog/entry/[0-9]+',parsed.path)) or parsed.path.lower().endswith('.pdf'):
                if link not in links: links.append(link)
        for link in links[:4]:
            try:
                if urllib.parse.urlparse(link).path.lower().endswith('.pdf'):
                    public_url(link)
                    if link not in self.pdf_cache:
                        self.pdf_cache[link] = self._request(link,cancel)
                        if len(self.pdf_cache)>8:self.pdf_cache.pop(next(iter(self.pdf_cache)))
                    result = section_from_pdf(self.pdf_cache[link],problem)
                    method = 'official_pdf'
                else:
                    bid = link.rsplit('/',1)[1]
                    if bid not in self.blog_cache:
                        data = json.loads(self._request('https://codeforces.com/api/blogEntry.view?blogEntryId='+bid,cancel))
                        if data.get('status')!='OK' or not isinstance(data.get('result',{}).get('content'),str):
                            raise EditorialError('博客未返回正文。','blog_no_text')
                        self.blog_cache[bid] = data['result']['content']
                        if len(self.blog_cache)>64:self.blog_cache.pop(next(iter(self.blog_cache)))
                    result = section_from_blog(self.blog_cache[bid],problem)
                    method = 'blog_section'
                return result | {'sourceUrl':link,'retrievalUrl':link,'method':method,
                                 'providerVersion':self.VERSION,'attempts':attempts}
            except (Cancelled, AccessRestricted):
                raise
            except (EditorialError, ValueError, TypeError) as exc:
                attempts.append(getattr(exc,'code','invalid_response'))
        raise EditorialError('未自动取得可明确对应本题的题解；可查看来源。',
                             attempts[-1] if links and attempts else 'no_link',links)

    def fetch(self, problem, cancel, existing=None):
        """Text and source discovery have independent success states; retain partial evidence."""
        import copy
        result = copy.deepcopy(existing) if existing else None
        text_error = None
        if not result or not result.get('text'):
            try:
                fresh = self._fetch_text(problem,cancel)
                if result:
                    fresh['codeBlocks'] = result.get('codeBlocks',[]) + fresh.get('codeBlocks',[])
                    fresh['codeLinks'] = result.get('codeLinks',[]) + fresh.get('codeLinks',[])
                result = fresh
            except (Cancelled,AccessRestricted):
                raise
            except EditorialError as exc:
                text_error = exc
        url=cf_url(f"/contest/{problem['contestId']}/problem/{problem['index']}",r'/contest/[1-9][0-9]*/problem/[A-Za-z0-9]+')
        if result is None:
            result={'text':'','codeBlocks':[],'codeLinks':[],'warnings':[],
                    'sourceUrl':url,'method':'partial_editorial'}
        result.setdefault('codeLinks',[])
        result.setdefault('warnings',[])
        rejected=[b for b in result.get('codeBlocks',[]) if not is_reference_code_block(b)]
        if rejected:
            result.setdefault('exampleBlocks',[]).extend(rejected)
            result['codeBlocks']=[b for b in result['codeBlocks'] if is_reference_code_block(b)]
        result.pop('stopReason',None)
        result['codeDiscoveryErrors']=[]
        try:
            self._enrich_code(result,problem,url,cancel)
        except Cancelled:
            raise
        except AccessRestricted as exc:
            if not (result.get('text') or result.get('codeBlocks') or result.get('codeLinks')):raise
            # Persist acquired evidence before the service stops the batch.
            result['stopReason']={'code':exc.code,'message':str(exc)}
            result['codeDiscoveryErrors'].append({'code':exc.code,'message':str(exc)})
        except (EditorialError,ValueError,TypeError) as exc:
            result['codeDiscoveryErrors'].append({'code':getattr(exc,'code','invalid_response'),'message':str(exc)[:200]})
        if not (result.get('text') or result.get('codeBlocks') or result.get('codeLinks')):
            raise text_error or EditorialError('未取得本题题解或代码入口。','unresolved')
        result['textStatus']='ready' if result.get('text') else 'missing'
        result['codeStatus']='ready' if result.get('codeBlocks') else 'links_only' if result['codeLinks'] else 'missing'
        result['codeDiscoveryVersion']=self.CODE_VERSION
        result['providerVersion']=self.VERSION
        if text_error:result['textError']={'code':text_error.code,'message':str(text_error)}
        return result

    def _enrich_code(self, result, problem, url, cancel):
        # Once actual code is present, avoid unnecessary requests to alternate implementations.
        if not result.get('codeBlocks'):
            links=[]
            if re.fullmatch(r'https://codeforces.com/blog/entry/[0-9]+',result.get('sourceUrl','')):
                links.append(result['sourceUrl'])
            else:
                raw=self.page_cache.get(url)
                if raw is None:raw=self._request(url+'?locale=en',cancel)
                if not raw.startswith(b'%PDF'):
                    doc=Document(raw.decode('utf-8'))
                    if not any('problem-statement' in classes(n) for n in doc.root.walk()):
                        raise AccessRestricted('原题页未正常加载。','verification_required')
                    for n in doc.root.walk():
                        if n.tag=='a' and re.search(r'tutorial|editorial|solution',n.text(),re.I):
                            link=urllib.parse.urljoin(url,n.attrs.get('href',''))
                            if re.fullmatch(r'https://codeforces.com/blog/entry/[0-9]+',link) and link not in links:links.append(link)
            for link in links[:4]:
                try:
                    bid=link.rsplit('/',1)[1]
                    if bid not in self.blog_cache:
                        data=json.loads(self._request('https://codeforces.com/api/blogEntry.view?blogEntryId='+bid,cancel))
                        if data.get('status')!='OK':raise EditorialError('博客未返回正文。','blog_no_text')
                        self.blog_cache[bid]=data['result']['content']
                        if len(self.blog_cache)>64:self.blog_cache.pop(next(iter(self.blog_cache)))
                    section=section_from_blog(self.blog_cache[bid],problem,allow_partial=True)
                    if not result.get('text') and section.get('text'):
                        result.update(text=section['text'],sourceUrl=link,method='blog_section')
                    for code in section['codeBlocks']:
                        if not any(c['text']==code['text'] for c in result['codeBlocks']):
                            result['codeBlocks'].append(code|{'sourceUrl':link,'origin':'editorial_inline'})
                    for ref in section['codeLinks']:
                        if not any(r['url']==ref['url'] for r in result['codeLinks']):
                            result['codeLinks'].append(ref|{'editorialUrl':link})
                    for warning in section['warnings']:
                        if warning not in result['warnings']:result['warnings'].append(warning)
                    if result['codeBlocks'] or result['codeLinks']:break
                except (Cancelled,AccessRestricted):raise
                except (EditorialError,ValueError,KeyError,TypeError) as exc:
                    result['codeDiscoveryErrors'].append({'code':getattr(exc,'code','invalid_response'),'sourceUrl':link})
        for ref in result['codeLinks'][:2]:
            if result['codeBlocks']:break
            if time.monotonic()<self.source_blocked_until:
                ref.update(status='access_restricted',message='提交源码访问受限，暂缓读取；可打开原链接。')
                continue
            try:
                raw=self._request(ref['url'],cancel).decode('utf-8')
                code=source_from_submission(raw,problem,ref['url'].rsplit('/',1)[1])
                result['codeBlocks'].append(code|{'sourceUrl':ref['url'],'origin':'editorial_submission'})
                ref['status']='ready'
            except Cancelled:raise
            except AccessRestricted as exc:
                self.source_blocked_until=time.monotonic()+900
                ref.update(status='access_restricted',code=exc.code,message='提交源码访问受限，可打开原链接。')
            except (EditorialError,ValueError) as exc:
                ref.update(status='unavailable',code=getattr(exc,'code','invalid_response'),message=str(exc)[:200])


def source_from_submission(html, problem, submission_id=None):
    doc=Document(html)
    # Match the judged submission row, not unrelated sidebar/problem links.
    rows=[n for n in doc.root.walk() if n.tag=='tr' and n.attrs.get('data-submission-id')]
    expected=f"{problem['contestId']}{problem['index']}"
    matching=[r for r in rows if expected in problem_links(r) and (submission_id is None or r.attrs['data-submission-id']==str(submission_id))]
    if len(matching)!=1:
        raise EditorialError('提交页无法确认属于本题，未保存源码。','submission_problem_mismatch')
    source=[n for n in doc.root.walk() if n.tag=='pre' and n.attrs.get('id')=='program-source-text']
    if len(source)!=1:
        raise EditorialError('提交页未公开返回源码，可打开原链接。','source_unavailable')
    text=source[0].text(code=True).replace('\r\n','\n').strip('\n')
    if not text.strip() or len(text)>MAX_TEXT:
        raise EditorialError('提交源码为空或超过大小限制。','source_unavailable')
    verdict=next((n.text() for n in matching[0].walk() if 'submissionVerdict' in classes(n)),None)
    return {'text':text,'verification':'editorial_linked_submission_not_locally_tested','verdict':normalize(verdict or '')}
