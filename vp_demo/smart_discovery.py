"""Request-scoped discovery over catalog tags and public text, without per-problem LLM calls."""
import json
from smart_retrieval import BM25
from smart_workflow import bundle
from smart_retrieval_policy import source_weights, queries_for, rank_documents, fuse_rankings


def matches_tags(tags, intent):
    tags=set(tags)
    return (all(tags.intersection(group) for group in intent.get('tagGroups',[]))
            and not tags.intersection(intent.get('excludeTags',[])))


def admission_order(bundles, limit=20):
    """Every new batch is >=80% complete; old complete cards cannot subsidize it."""
    good=[];partial=[];empty=[]
    for pid,b in bundles.items():
        kinds={e['kind'] for e in b['evidence'] if e.get('text','').strip()}
        if b.get('complete_material',b['reference_code_available'] and sum(len(e['text'].strip()) for e in b['evidence'] if e['kind']=='editorial')>=80):
            good.append(pid)
        elif kinds & {'statement','editorial','code'}:partial.append(pid)
        else:empty.append(pid)
    good=good[:limit]
    return good+(partial+empty)[:min(len(good)//4,limit-len(good))]


class Discovery:
    LIMIT=64

    def __init__(self, pool):
        self.pool=pool

    def prepare(self, uid, check):
        check();scope=self.pool.discovery_scope(uid);check()
        with self.pool.connect() as c:
            docs={}
            for row in c.execute('SELECT problem_id,kind,payload FROM training_public_materials'):
                docs.setdefault(row[0],{})[row[1]]=json.loads(row[2])
        scope['docs']=docs
        scope['validTags']=sorted({t for ts in scope['catalog'].get('tags',{}).values() for t in ts})
        return scope

    @staticmethod
    def tags(scope, pid):
        e=scope['entries'][pid]
        return scope['catalog'].get('tags',{}).get(e['materialKey'],[])

    def filter(self, scope, intent, ids):
        return [p for p in ids if p in scope['entries'] and matches_tags(self.tags(scope,p),intent)]

    def search(self, uid, scope, intent, cached, check):
        ids=self.filter(scope,intent,scope['entries'])
        docs=[];weights=source_weights(intent)
        for pid in ids:
            e=scope['entries'][pid];key=e['materialKey']
            saved=scope['docs'].get(key,{})
            statement=saved.get('statement') or scope['catalog']['materials'].get(key,{}).get('payload',{})
            st=statement.get('statement') or {}
            editorial=saved.get('editorial') or {}
            fields={'metadata':e['problem']['title']+' '+' '.join(self.tags(scope,pid)),
                'statement':'\n'.join(str(st.get(k) or '') for k in ('body','input_format','output_format','note')),
                'editorial':str(editorial.get('text') or ''),
                'code':'\n'.join(b.get('text','') for b in editorial.get('codeBlocks',[]) if isinstance(b,dict))}
            for kind,value in fields.items():
                if kind in weights and value.strip():docs.append({'pid':pid,'id':pid+':'+kind,'kind':kind,'text':value})
        check()
        bm=BM25([d['text'] for d in docs]);rankings=[];matched=set()
        for q in queries_for(intent):
            ranked,_=rank_documents(docs,bm.scores(q),weights)
            matched.update(ranked);rankings.append(ranked)
        scores=fuse_rankings(rankings,limit=100)
        available={d['pid'] for d in docs}
        ranked=sorted(available,key=lambda p:(-scores.get(p,0),p))
        # Keep lexical hits plus a spread across the eligible direction. Semantic reranking
        # then reads the original text, not a fabricated summary of missing materials.
        selected=[p for p in ranked if scores.get(p,0)>0][:48]
        remaining=[p for p in sorted(available) if p not in selected]
        if remaining:
            n=min(self.LIMIT-len(selected),len(remaining))
            selected += [remaining[i*len(remaining)//n] for i in range(n)]
        selected=list(dict.fromkeys(selected))[:self.LIMIT]
        self.pool.register_discovered(uid,scope,selected)
        check()
        result={p:bundle(self.pool,uid,scope['entries'][p]) for p in selected}
        trace={'eligibleCount':len(scope['entries']),'directionCount':len(ids),'lexicalHits':len(matched),
               'materialCandidateCount':len(available),'target':intent.get('retrievalTarget','mixed'),
               'shortlist':selected,'outsideCache':sum(p not in cached for p in selected),
               'method':'tag_filter + source_routed_BM25 + bounded_semantic_shortlist',
               'shortlistLimit':self.LIMIT}
        return result,trace

    def with_metadata(self, scope, b):
        pid=b['problem_id']
        info={'title':b['problem']['title'],'rating':b['problem']['rating'],'tags':self.tags(scope,pid)}
        return b|{'evidence':b['evidence']+[{'evidence_id':pid+':metadata:0','kind':'metadata',
            'text':json.dumps(info,ensure_ascii=False)}]}
