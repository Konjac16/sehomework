"""Public problem text: bounded HTML parsing and persistent cache, without OJ credentials."""
from html.parser import HTMLParser
import json
import re
import threading
import time
import urllib.error
import urllib.request


def problem_url(record):
    cid, index = str(record.get('contestId', '')), str(record.get('index', ''))
    if not re.fullmatch(r'[1-9][0-9]{0,8}', cid) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,9}', index):
        raise ValueError('题目标识无效，不能获取原题。')
    return f'https://codeforces.com/{"gym" if int(cid) >= 100000 else "contest"}/{cid}/problem/{index}'


class StatementParser(HTMLParser):
    blocks = {'div', 'p', 'pre', 'li', 'ol', 'ul', 'table', 'tr', 'h1', 'h2', 'h3'}
    voids = {'br', 'img', 'hr', 'input', 'meta', 'link', 'wbr', 'source', 'area', 'embed', 'param', 'col', 'base'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.parts = [], []
        self.active = self.done = self.has_images = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if not self.active:
            if not self.done and 'problem-statement' in attrs.get('class', '').split():
                self.active = True
            else:
                return
        if tag in self.blocks or tag == 'br':
            self.parts.append('\n')
        if tag == 'img':
            self.has_images = True
            self.parts.append('[题面图片：' + attrs.get('alt', '请查看原题') + ']')
        if tag == 'sup':
            self.parts.append('^(')
        if tag == 'sub':
            self.parts.append('_(')
        if tag not in self.voids:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.voids:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if not self.active or tag not in self.stack:
            return
        if tag in ('sub', 'sup'):
            self.parts.append(')')
        if tag in self.blocks:
            self.parts.append('\n')
        pos = len(self.stack)-1-self.stack[::-1].index(tag)
        del self.stack[pos:]
        if not self.stack:
            self.active, self.done = False, True

    def handle_data(self, data):
        if self.active and not any(t in self.stack for t in ('script', 'style', 'button')):
            self.parts.append(data)

    def result(self):
        text = ''.join(self.parts).replace('\r\n', '\n').replace('\xa0', ' ')
        text = re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+', '\n\n', text).strip()
        if not self.done or len(text) < 40:
            raise ValueError('未找到完整题面，页面可能要求登录或验证。请打开原题查看。')
        if len(text) > 40000:
            raise ValueError('题面超过 40000 字符，未截断保存；请打开原题并手动补充必要内容。')
        return {'text': text, 'warnings': ['题面包含图片，当前只提取文字；图片含义请查看原题，AI 未读取图片。'] if self.has_images else []}


def fetch_cf_statement(record):
    url = problem_url(record)
    request = urllib.request.Request(url+'?locale=en', headers={'User-Agent': 'VPLab/0.3 (local study tool)', 'Accept-Language': 'en'})
    try:
        with urllib.request.urlopen(request, timeout=18) as response:
            if not response.geturl().startswith('https://codeforces.com/'):
                raise ValueError('原题跳转到未支持的站点，未读取题面。')
            data = response.read(2_000_001)
            if len(data) > 2_000_000:
                raise ValueError('原题页面过大，未读取题面。')
            parser = StatementParser()
            parser.feed(data.decode('utf-8'))
            return parser.result() | {'url': url, 'fetchedAt': int(time.time()), 'textOrigin': 'codeforces'}
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise ValueError(f'CF 暂时拒绝自动读取题面（HTTP {exc.code}）。请打开原题；也可以稍后重试或手动补充题面。') from None
        raise ValueError(f'原题获取失败（HTTP {exc.code}），请打开原题确认是否可访问。') from None
    except ValueError:
        raise
    except Exception:
        raise ValueError('无法连接 CF 题面，请检查网络或稍后重试。') from None


class StatementService:
    def __init__(self, connect, adapters):
        self.connect, self.adapters = connect, adapters
        self.lock = threading.Lock()
        self.last_call = 0.0

    @staticmethod
    def init_tables(c):
        c.execute('''CREATE TABLE IF NOT EXISTS problem_statements(
          platform TEXT NOT NULL, problem_key TEXT NOT NULL, payload TEXT NOT NULL,
          PRIMARY KEY(platform,problem_key))''')

    def cached(self, record):
        with self.connect() as c:
            row = c.execute('SELECT payload FROM problem_statements WHERE platform=? AND problem_key=?',
                            (record['platform'], record['problemKey'])).fetchone()
        return json.loads(row[0]) if row else None

    def get(self, record, refresh=False):
        cached = self.cached(record)
        now = int(time.time())
        ttl = 86400*7 if cached and cached.get('status') == 'ready' else 60
        if cached and now-cached['checkedAt'] < ttl and (not refresh or now-cached['checkedAt'] < 60):
            return cached | {'cached': True}
        base = cached or {'text': '', 'url': problem_url(record), 'warnings': [], 'textOrigin': 'missing'}
        if not self.lock.acquire(blocking=False):
            return base | {'status': 'stale' if base['text'] else 'unavailable', 'message': '另一个题面正在获取，请稍后重试。', 'cached': bool(cached)}
        try:
            delay = 2.1-(time.monotonic()-self.last_call)
            if delay > 0:
                time.sleep(delay)
            self.last_call = time.monotonic()
            try:
                adapter = self.adapters[record['platform']]
                result = adapter.fetch_statement(record) | {'status': 'ready', 'message': ''}
            except (ValueError, KeyError, AttributeError) as exc:
                result = base | {'status': 'stale' if base['text'] else 'unavailable',
                                 'message': str(exc) if isinstance(exc, ValueError) else '此平台暂不支持题面获取。'}
            result.update(checkedAt=int(time.time()))
            with self.connect() as c:
                c.execute('INSERT OR REPLACE INTO problem_statements(platform,problem_key,payload) VALUES(?,?,?)',
                          (record['platform'], record['problemKey'], json.dumps(result, ensure_ascii=False)))
            return result | {'cached': False}
        finally:
            self.lock.release()
