"""Short-lived, per-user browser capture jobs. No OJ cookies or credentials."""
import hmac
import secrets
import threading
import time
from urllib.parse import urlparse
from problem_statement import StatementParser, problem_url
from review_service import ReviewError


class BrowserBridge:
    def __init__(self, statements):
        self.statements = statements
        self.jobs = {}
        self.lock = threading.RLock()

    def expire(self):
        now = time.time()
        for job in self.jobs.values():
            if job['status'] == 'waiting' and job['expiresAt'] <= now:
                job.update(status='expired', message='等待题面超时，请重新点击浏览器获取。')
        self.jobs = {k: v for k, v in self.jobs.items() if v['expiresAt'] > now-3600}

    @staticmethod
    def public(job):
        return {k: job[k] for k in ('id', 'problemKey', 'url', 'status', 'message', 'expiresAt')}

    def create(self, uid, record):
        with self.lock:
            self.expire()
            for job in self.jobs.values():
                if job['uid'] == uid and job['problemKey'] == record['problemKey'] and job['status'] == 'waiting':
                    job.update(status='cancelled', message='已由新的获取任务替代。')
            if sum(j['status'] == 'waiting' for j in self.jobs.values()) >= 20 or len(self.jobs) >= 300:
                raise ReviewError('浏览器获取任务较多，请稍后重试。', 429)
            jid, token = secrets.token_urlsafe(18), secrets.token_urlsafe(32)
            job = dict(id=jid, token=token, uid=uid, record=record, problemKey=record['problemKey'],
                       url=problem_url(record), status='waiting', expiresAt=int(time.time())+600,
                       message='等待 CF 页面加载；如出现验证，请在原题页面手动完成。')
            self.jobs[jid] = job
            return self.public(job) | {'token': token}

    def status(self, uid, jid):
        with self.lock:
            self.expire()
            job = self.jobs.get(jid)
            if not job or job['uid'] != uid:
                raise ReviewError('浏览器任务不存在或服务已重启，请重新获取。', 404)
            return self.public(job)

    def cancel_user(self, uid):
        with self.lock:
            for job in self.jobs.values():
                if job['uid'] == uid:
                    job.update(status='cancelled', token='', message='登录已退出，任务取消。')

    def deliver(self, token, data):
        with self.lock:
            self.expire()
            job = self.jobs.get(data.get('id'))
            if not job or not token or not hmac.compare_digest(job['token'], token):
                raise ReviewError('浏览器任务凭证无效。', 401)
            if job['status'] == 'succeeded':
                return self.public(job)
            if job['status'] != 'waiting':
                raise ReviewError(job['message'], 409)
            if data.get('failure'):
                # Never display arbitrary messages supplied by an external page.
                job.update(status='failed', message='浏览器采集未完成；请确认原题正常显示后重新获取。')
                return self.public(job)
            actual, expected = urlparse(str(data.get('url', ''))), urlparse(job['url'])
            if actual.scheme != 'https' or actual.netloc != 'codeforces.com' or actual.path.rstrip('/') != expected.path:
                raise ReviewError('回传页面与所选原题不一致。', 422)
            html = data.get('html')
            if not isinstance(html, str) or len(html.encode('utf-8')) > 1_000_000:
                raise ReviewError('题面内容格式错误或超过大小限制。', 422)
            parser = StatementParser()
            parser.feed(html)
            try:
                result = parser.result()
            except ValueError as exc:
                raise ReviewError(str(exc), 422) from None
            now = int(time.time())
            result.update(url=job['url'], textOrigin='codeforces_browser', fetchedAt=now,
                          checkedAt=now, status='ready', message='')
            if data.get('formulaWarning') is True:
                result['warnings'].append('部分公式没有找到原始 LaTeX，保留可见文字，请对照原题核对。')
            self.statements.save_browser(job['uid'], job['record'], result)
            job.update(status='succeeded', message='题面已从浏览器保存，可以返回 VP Lab 查看和分析。')
            return self.public(job)
