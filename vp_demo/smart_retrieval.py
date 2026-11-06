"""Local multilingual E5 + BM25 retrieval. No experiment imports or remote inference."""
from collections import Counter
import hashlib
import math
import os
from pathlib import Path
import re
import threading
import uuid

from recommendation_pool import PoolError
from training_archive import signature
from smart_retrieval_policy import source_weights, queries_for, rank_documents, fuse_rankings, SOURCE_WORDS, CHINESE_SOURCE_WORDS

ROOT = Path(__file__).resolve().parent
STOP = set('the and for that with from this have has are was were into not can will let use using need solution problem test case cases consider notice'.split()) | SOURCE_WORDS | CHINESE_SOURCE_WORDS
MIN_STATEMENT_CHARS = 80
CHUNK_TOKENS = 400
CHUNK_OVERLAP = 50
MIN_TAIL_TOKENS = 64
MAX_MERGED_TOKENS = 480  # Leave room for the embedding prefix and special tokens.


def meaningful_statement_chunk(text):
    return sum(not c.isspace() for c in text)>=MIN_STATEMENT_CHARS and any(c.isalnum() for c in text)


def terms(text):
    words = re.findall(r'[a-z][a-z0-9_]{2,}', text.lower())
    words += [s[i:i+2] for s in re.findall(r'[\u4e00-\u9fff]+',text) for i in range(len(s)-1)]
    return [w for w in words if w not in STOP]


class BM25:
    def __init__(self, texts):
        self.counts = [Counter(terms(t)) for t in texts]
        self.df = Counter(w for c in self.counts for w in c)
        self.lengths = [sum(c.values()) for c in self.counts]
        self.avg = sum(self.lengths)/max(1,len(texts)) or 1

    def scores(self, query):
        return [sum(math.log(1+(len(self.counts)-self.df[t]+.5)/(self.df[t]+.5))*c[t]*2.2 /
                    (c[t]+1.2*(.25+.75*length/self.avg)) for t in set(terms(query)) if c[t])
                for c,length in zip(self.counts,self.lengths)]


class Retriever:
    def __init__(self, model_dir=None, cache_dir=None):
        self.model_dir = Path(model_dir or os.environ.get('VPLAB_EMBEDDING_MODEL', ROOT/'.local/models/multilingual-e5-small'))
        self.cache_dir = Path(cache_dir or ROOT/'.local/smart-vectors')
        self.lock = threading.RLock()
        self.encoder = None

    def ready(self):
        if not all((self.model_dir/f).is_file() for f in ('model.onnx','tokenizer.json')):
            raise PoolError('本地检索模型尚未安装，请按 README 的智能推荐说明准备 multilingual-e5-small。',409)
        try:
            import numpy
            import onnxruntime
            import tokenizers
        except ImportError:
            raise PoolError('请先安装 requirements-recommendation.txt 中的智能推荐依赖。',409) from None

    def _load(self):
        if self.encoder is not None:
            return
        self.ready()
        import onnxruntime as ort
        from tokenizers import Tokenizer
        options=ort.SessionOptions();options.intra_op_num_threads=4
        self.encoder=ort.InferenceSession(str(self.model_dir/'model.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
        self.tokenizer=Tokenizer.from_file(str(self.model_dir/'tokenizer.json'))
        self.tokenizer.enable_truncation(max_length=512)
        self.tokenizer.enable_padding(pad_id=1,pad_token='<pad>')
        self.model_hash=signature({f:hashlib.sha256((self.model_dir/f).read_bytes()).hexdigest()
                                   for f in ('model.onnx','tokenizer.json')})
        self.cache_dir.mkdir(parents=True,exist_ok=True)

    def _encode(self, texts, prefix, check):
        import numpy as np
        vectors=[]
        for start in range(0,len(texts),12):
            check()
            enc=self.tokenizer.encode_batch([prefix+t for t in texts[start:start+12]])
            inputs={'input_ids':np.array([x.ids for x in enc],dtype=np.int64),
                    'attention_mask':np.array([x.attention_mask for x in enc],dtype=np.int64),
                    'token_type_ids':np.array([x.type_ids for x in enc],dtype=np.int64)}
            inputs={k:v for k,v in inputs.items() if k in {i.name for i in self.encoder.get_inputs()}}
            hidden=self.encoder.run(None,inputs)[0]
            if hidden.ndim==3:
                mask=inputs['attention_mask'][...,None];pooled=(hidden*mask).sum(1)/mask.sum(1)
            else:
                pooled=hidden
            pooled=pooled/np.maximum(np.linalg.norm(pooled,axis=1,keepdims=True),1e-12)
            vectors.extend(pooled)
        return np.array(vectors,dtype=np.float32)

    def _chunks(self, text):
        # E5 sees every part of long paragraphs, rather than truncating whole editorials.
        ids=self.tokenizer.encode(text,add_special_tokens=False).ids
        return [self.tokenizer.decode(ids[i:i+400],skip_special_tokens=True) for i in range(0,len(ids),350)]

    def statement_spans(self, text):
        """Keep useful original spans; merge short tails and never emit covered suffixes."""
        self.tokenizer.no_truncation();self.tokenizer.no_padding()
        try:
            offsets=self.tokenizer.encode(text,add_special_tokens=False).offsets
            ranges=[];start=0;n=len(offsets)
            while start<n:
                end=min(start+CHUNK_TOKENS,n)
                if end<n and n-(end-CHUNK_OVERLAP)<MIN_TAIL_TOKENS:end=n
                ranges.append((start,end))
                if end==n:break  # The previous loop emitted even already-covered trailing punctuation.
                start=end-CHUNK_OVERLAP
            if len(ranges)>1:
                start,end=ranges[-1]
                tail=text[offsets[start][0]:offsets[end-1][1]]
                if not meaningful_statement_chunk(tail):
                    previous=ranges[-2][0]
                    if end-previous<=MAX_MERGED_TOKENS:
                        ranges[-2:]=[(previous,end)]
                    else:
                        # Extend backwards to include context, without exceeding model capacity.
                        ranges[-1]=(max(previous,end-MAX_MERGED_TOKENS),end)
            spans=[(offsets[start][0],offsets[end-1][1]) for start,end in ranges]
            return [(start,end) for start,end in spans if meaningful_statement_chunk(text[start:end])]
        finally:
            self.tokenizer.enable_truncation(max_length=512)
            self.tokenizer.enable_padding(pad_id=1,pad_token='<pad>')

    def _cached_passages(self, texts, check, progress=None):
        import numpy as np
        folder=self.cache_dir/'passages';folder.mkdir(exist_ok=True)
        rows=[None]*len(texts);missing=[];paths=[]
        for i,text in enumerate(texts):
            if i%100==0:check()
            path=folder/(signature({'model':self.model_hash,'text':text,'version':1})+'.npy');paths.append(path)
            try:
                v=np.load(path,allow_pickle=False)
                if v.shape!=(self.encoder.get_outputs()[0].shape[-1],) or not np.isfinite(v).all():raise ValueError()
                rows[i]=v
            except (OSError,ValueError):missing.append(i)
        ready=len(texts)-len(missing)
        if progress:progress(ready,len(texts))
        # Commit incremental work so cancellation does not discard minutes of local encoding.
        for start in range(0,len(missing),96):
            group=missing[start:start+96]
            encoded=self._encode([texts[i] for i in group],'passage: ',check)
            for i,v in zip(group,encoded):
                rows[i]=v;tmp=paths[i].with_suffix('.'+uuid.uuid4().hex+'.tmp')
                with tmp.open('wb') as f:np.save(f,v)
                tmp.replace(paths[i])
            ready+=len(group)
            if progress:progress(ready,len(texts))
        return np.array(rows,dtype=np.float32)

    def retrieve(self, bundles, cards, intent, check=lambda:None):
        import numpy as np
        with self.lock:
            self._load()
            weights=source_weights(intent);docs=[]
            # All candidates have the same evidence scoring rules. Generated cards
            # remain useful to the audit, but never create extra retrieval votes.
            for pid,b in sorted(bundles.items()):
                check()
                self.tokenizer.no_truncation()
                self.tokenizer.no_padding()
                try:
                    for evidence in b['evidence']:
                        if evidence['kind'] not in weights or not evidence['text'].strip():continue
                        for i,text in enumerate(self._chunks(evidence['text'])):
                            docs.append({'pid':pid,'id':f"{evidence['evidence_id']}:{i}",
                                'kind':evidence['kind'],'text':text})
                finally:
                    self.tokenizer.enable_truncation(max_length=512)
                    self.tokenizer.enable_padding(pad_id=1,pad_token='<pad>')
            queries=queries_for(intent)
            if not docs or not queries:
                return {'top8':[],'scores':{},'traces':[],'target':intent.get('retrievalTarget','mixed'),
                    'reason':'missing_target_material' if not docs else 'empty_query'}
            check()
            path=self.cache_dir/(signature({'model':self.model_hash,'docs':docs,'version':2})+'.npy')
            try:
                vectors=np.load(path,allow_pickle=False)
                if vectors.ndim!=2 or len(vectors)!=len(docs) or not np.isfinite(vectors).all():raise ValueError()
            except (OSError,ValueError):
                vectors=self._cached_passages([d['text'] for d in docs],check)
                temp=path.with_suffix('.tmp')
                with temp.open('wb') as f:np.save(f,vectors)
                temp.replace(path)
            bm=BM25([d['text'] for d in docs]);traces=[];rankings=[]
            for query in queries:
                check();vector=self._encode([query],'query: ',check)[0]
                for method,values in [('dense',vectors@vector),('bm25',bm.scores(query))]:
                    ranked,best=rank_documents(docs,values,weights)
                    rankings.append(ranked)
                    traces.append({'query':query,'method':method,'top8':ranked[:8],
                        'evidence':{p:best[p] for p in ranked[:8]}})
            scores=fuse_rankings(rankings)
            top=sorted(scores,key=lambda p:(-scores[p],p))[:8]
            return {'top8':top,'scores':{p:scores[p] for p in top},'traces':traces,
                'target':intent.get('retrievalTarget','mixed'),'sourceWeights':weights,
                'scoring':'one_best_passage_per_problem_per_query_method',
                'generatedCardsAffectRanking':False}
