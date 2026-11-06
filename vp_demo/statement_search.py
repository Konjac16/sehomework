"""Full eligible-statement search: independent lexical and vector recall, no generated cards."""
from training_archive import signature
from smart_retrieval import BM25
from smart_retrieval_policy import queries_for, rank_documents, fuse_rankings


def statement_text(statement):
    return '\n\n'.join(str(statement.get(k) or '') for k in
        ('body','input_format','output_format','interaction_format','note')).strip()


class StatementSearch:
    LIMIT=16

    def __init__(self, pool, retriever):
        self.pool,self.retriever=pool,retriever

    def revision(self):
        archive=self.pool.archive.load()
        hashes={k:v['contentHash'] for k,v in archive['materials'].items()}
        with self.pool.connect() as c:
            hashes.update(dict(c.execute("SELECT problem_id,content_hash FROM training_public_materials WHERE kind='statement'")))
            catalog=c.execute("SELECT payload FROM training_catalog WHERE platform='codeforces'").fetchone()
        return signature({'statements':hashes,'archive':archive.get('revision'),
                          'catalog':catalog[0] if catalog else None})

    def corpus(self, scope, ids):
        result={}
        for pid in ids:
            e=scope['entries'][pid];key=e['materialKey']
            saved=scope['docs'].get(key,{})
            m=saved.get('statement') or scope['catalog']['materials'].get(key,{}).get('payload',{})
            text=statement_text(m.get('statement') or {})
            if text:result[pid]=text
        return result

    def search(self, corpus, intent, check, progress=lambda *a:None):
        r=self.retriever
        with r.lock:
            r._load();docs=[]
            for pid,text in sorted(corpus.items()):
                check()
                for start,end in r.statement_spans(text):
                    docs.append({'pid':pid,'id':f'{pid}:statement_hit:{start}:{end}',
                        'kind':'statement','text':text[start:end],'start':start,'end':end})
            queries=queries_for(intent)
            if not docs or not queries:
                return {'ranked':[],'hits':{},'problemCount':len(corpus),'chunkCount':len(docs),'traces':[]}
            progress('statement_index',f'正在准备 {len(corpus)} 份题面的 {len(docs)} 个文本块；已有向量会复用。')
            vectors=r._cached_passages([d['text'] for d in docs],check,
                progress=lambda ready,total:progress('statement_index',f'题面向量已就绪 {ready}/{total} 个文本块。'))
            bm=BM25([d['text'] for d in docs]);rankings=[];traces=[];hit_scores={}
            progress('statement_search',f'关键词与向量分别检索全部 {len(corpus)} 份合格题面。')
            for query in queries:
                check();vector=r._encode([query],'query: ',check)[0]
                # Both methods see the same entire corpus. BM25 is never a gate for vectors.
                for method,values in [('bm25',bm.scores(query)),('dense',vectors@vector)]:
                    ranked,best=rank_documents(docs,values,{'statement':1})
                    rankings.append(ranked)
                    traces.append({'query':query,'method':method,'top16':ranked[:16]})
                    for rank,pid in enumerate(ranked[:40]):
                        eid=best[pid]['evidenceId']
                        hit_scores.setdefault(pid,{})[eid]=hit_scores.get(pid,{}).get(eid,0)+1/(61+rank)
            scores=fuse_rankings(rankings)
            selected=sorted(scores,key=lambda p:(-scores[p],p))[:self.LIMIT]
            by_id={d['id']:d for d in docs};hits={}
            for pid in selected:
                chosen=sorted(hit_scores[pid],key=lambda eid:(-hit_scores[pid][eid],eid))[:3]
                hits[pid]=[{'evidence_id':eid,'kind':'statement','text':by_id[eid]['text'],
                    'start':by_id[eid]['start'],'end':by_id[eid]['end']} for eid in chosen]
            check()
            return {'ranked':selected,'scores':{p:scores[p] for p in selected},'hits':hits,
                'problemCount':len(corpus),'chunkCount':len(docs),'queries':queries,'traces':traces,
                'method':'full_eligible_statements_independent_BM25_and_dense','perProblemLLMCalls':0}


def with_statement_hits(b, hits):
    """Query-specific hits are audit evidence, not part of reusable public card hashes."""
    existing={e['evidence_id'] for e in b['evidence']}
    extra=[h for h in hits if h['evidence_id'] not in existing]
    return b|{'evidence':b['evidence']+extra,'matchedStatementEvidence':[h['evidence_id'] for h in hits]}
