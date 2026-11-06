"""Read public training materials independently of the VP room implementation."""
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import threading
from zipfile import BadZipFile, ZipFile


def signature(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def problem_id(cid, index):
    if type(cid) is not int or cid <= 0 or not re.fullmatch(r'[A-Za-z0-9]{1,10}', str(index)):
        raise ValueError('题目标识无效')
    return f'cf:{cid}:{index}'


class TrainingArchive:
    """The ZIP is public material, never a store for private code or experience cards."""
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self._stamp = None
        self._data = None

    def load(self):
        with self.lock:
            try:
                stat = self.path.stat()
                stamp = (stat.st_mtime_ns, stat.st_size)
                if stamp == self._stamp:
                    return self._data
                with ZipFile(self.path) as pack:
                    doc = json.loads(pack.read('catalog.json'))
                    if doc.get('schemaVersion') != '1.0' or doc.get('mode') != 'real_snapshot':
                        raise ValueError('题库格式不受支持')
                    problems, contests, materials, tags = {}, {}, {}, {}
                    for p in doc['problems']:
                        key = problem_id(p['contestId'], p['index'])
                        if p['problemKey'] != key or key in problems:
                            raise ValueError('题库包含重复或不一致的题号')
                        problems[key] = {'id': key, 'platform':'codeforces',
                            'contestId':p['contestId'], 'index':p['index'],
                            'problemKey':f"{p['contestId']}{p['index']}", 'title':p['name'],
                            'rating':p.get('rating'),
                            'url':f"https://codeforces.com/contest/{p['contestId']}/problem/{p['index']}"}
                        tags[key] = p.get('tags') or []
                    for c in doc['contests']:
                        if c.get('phase') != 'FINISHED' or c.get('problemSetComplete') is not True:
                            continue
                        cid, keys = c['contestId'], c['problemKeys']
                        if cid in contests or not keys or len(set(keys)) != len(keys):
                            raise ValueError('比赛题单不完整')
                        if any(k not in problems or problems[k]['contestId'] != cid for k in keys):
                            raise ValueError('比赛题单与目录不一致')
                        if type(c.get('durationSeconds')) is not int or c['durationSeconds'] <= 0:
                            raise ValueError('比赛时长无效')
                        contests[cid] = deepcopy(c)
                        try:
                            statements = json.loads(pack.read(f'statements/{cid}.json'))
                        except KeyError:
                            statements = {}
                        for key in keys:
                            p = problems[key]
                            row = statements.get(p['index'])
                            if not row or not isinstance(row.get('body'), str) or not row['body'].strip():
                                continue
                            # No user material enters this reusable public cache.
                            content = {k:row.get(k) for k in ('body','input_format','output_format',
                                'interaction_format','note','time_limit','memory_limit','examples')}
                            warnings = []
                            if '[image]' in json.dumps(content).lower():
                                warnings.append('原题含图示，当前资料未包含原图。')
                            if row.get('example_source') == 'ronin':
                                warnings.append('此来源部分样例可能缺少换行，请对照原题。')
                            payload = {'problemId':key, 'statement':content,
                                'sourceUrl':p['url'], 'bodyProvider':row.get('body_source'),
                                'exampleProvider':row.get('example_source'), 'warnings':warnings,
                                'editorial':None, 'referenceCode':None}
                            materials[key] = {'contentHash':signature(payload), 'payload':payload}
                # Exact full-content matches only. Names/tags alone never imply an alias.
                groups = defaultdict(list)
                for key, m in materials.items():
                    content = m['payload']['statement']
                    if len(content['body'].strip()) >= 100 and content.get('examples'):
                        groups[signature(content)].append(key)
                canonical = {key:key for key in problems}
                aliases = {key:[key] for key in problems}
                for keys in groups.values():
                    keys.sort(key=lambda k:(problems[k]['contestId'], problems[k]['index']))
                    for key in keys:
                        canonical[key], aliases[key] = keys[0], keys
                result = {'contests':contests, 'problems':problems, 'materials':materials, 'tags':tags,
                          'canonical':canonical, 'aliases':aliases, 'snapshot':doc.get('snapshot'),
                          'revision':signature({'catalog':doc, 'materials':{
                              k:v['contentHash'] for k,v in materials.items()}})}
            except (OSError, KeyError, TypeError, ValueError, BadZipFile) as exc:
                raise ValueError('离线题库无法读取，请检查题库文件；已准备的候选和资料会保留。') from exc
            self._data, self._stamp = result, stamp
            return result
