"""Shared source routing. Cache membership is deliberately not a ranking feature."""
import re

SOURCE_WEIGHTS = {
    'statement': {'statement':1.0},
    'solution': {'editorial':1.0, 'statement':.85, 'code':.85, 'metadata':.35},
    'metadata': {'metadata':1.0, 'statement':.8, 'editorial':.8},
    'mixed': {'statement':1.0, 'editorial':1.0, 'code':.85, 'metadata':.5},
}
SOURCE_WORDS = {'problem','problems','statement','statements','editorial','editorials',
                'question','questions','themed','theme','related','training','practice'}
CHINESE_SOURCE_WORDS = {'题目','题面','题解','相关','练习','训练'}


def source_weights(intent):
    # Direct library callers may omit routing; model responses are validated strictly.
    return SOURCE_WEIGHTS[intent.get('retrievalTarget','mixed')]


def queries_for(intent):
    queries=[q[lang] for q in intent['skill_queries'] for lang in ('zh','en')]
    if intent.get('implementation_query'):queries+=list(intent['implementation_query'].values())
    result=[]
    for q in queries:
        q=re.sub(r'\b(?:'+ '|'.join(sorted(SOURCE_WORDS)) +r')\b',' ',q,flags=re.I)
        # Remove standalone source labels, without damaging substrings in names.
        q=' '.join(w for w in q.split() if w not in CHINESE_SOURCE_WORDS).strip()
        if q and q.casefold() not in {s.casefold() for s in result}:result.append(q)
    return result


def rank_documents(docs, values, weights):
    """One strongest source passage per problem. More passages never add votes."""
    best={}
    for doc,value in zip(docs,values):
        value=float(value)*weights.get(doc['kind'],0)
        if value<=0:continue
        pid=doc['pid'];current=best.get(pid)
        if current is None or value>current['value'] or (value==current['value'] and doc['id']<current['evidenceId']):
            best[pid]={'value':value,'evidenceId':doc['id'],'kind':doc['kind']}
    order=sorted(best,key=lambda p:(-best[p]['value'],p))
    return order,best


def fuse_rankings(rankings, limit=40):
    """Equal per-query/method budgets; source count and card count cannot multiply them."""
    scores={}
    for ranked in rankings:
        for rank,pid in enumerate(ranked[:limit]):scores[pid]=scores.get(pid,0)+1/(60+rank+1)
    return scores
