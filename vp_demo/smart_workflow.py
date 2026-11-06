"""Validated model contracts and public-material inputs for recommendation."""
import json
from pathlib import Path

from ai_provider import ProviderError
from material_quality import is_reference_code_block, complete
from recommendation_pool import PoolError
from training_archive import signature
from smart_retrieval_policy import SOURCE_WEIGHTS

PROMPTS = {name:(Path(__file__).parent/'prompts'/f'smart-{name}.txt').read_text(encoding='utf-8')
           for name in ('card','intent','audit','rank')}
VERSION = signature({'prompts':PROMPTS,'workflow':6})
REASONS = {
    'implementation':'适合练习代码组织与实现配合。',
    'detail':'适合把细节处理作为本次训练重点。',
    'insight':'适合练习从题意中发现有用的转化。',
    'structure_insight':'适合练习让数据结构与思考相互配合。',
    'proof':'适合练习解释思路为什么成立。',
    'construction':'适合练习方案设计与条件核对。',
    'balanced':'适合作为一次综合训练。',
    'preference':'符合本次指定的题目方向或内容。',
}


def fail(message='AI 返回格式或资料引用不完整，本轮未发布新推荐。'):
    raise ProviderError(message)


def text(value, limit=1000):
    return isinstance(value,str) and bool(value.strip()) and len(value)<=limit


def refs_valid(obj, allowed):
    if isinstance(obj,dict):
        return all((k!='evidence_ids' or isinstance(v,list) and all(isinstance(x,str) and x in allowed for x in v))
                   and refs_valid(v,allowed) for k,v in obj.items())
    if isinstance(obj,list):return all(refs_valid(x,allowed) for x in obj)
    return True


def canonicalize(raw, field, candidates):
    """Accept only exact IDs or their unique, supplied CF display aliases."""
    if not isinstance(raw,dict) or not isinstance(raw.get(field),dict):fail()
    aliases={p:p for p in candidates}
    for pid,b in candidates.items():
        alias=b['problem']['problemKey']
        if alias in aliases and aliases[alias]!=pid:fail()
        aliases[alias]=pid
    values={}
    for key,value in raw[field].items():
        if key not in aliases or aliases[key] in values:fail()
        values[aliases[key]]=value
    if set(values)!=set(candidates):fail()
    result=raw | {field:values}
    if 'first' in raw:
        if not isinstance(raw['first'],list) or any(not isinstance(p,str) or p not in aliases for p in raw['first']):fail()
        result['first']=[aliases[p] for p in raw['first']]
    return result


def bundle(pool, uid, entry):
    try:
        material=pool.material(uid,entry['id'])
    except PoolError as exc:
        if exc.status!=404:raise
        material={}
    statement=(material.get('material') or {}).get('statement') or {}
    editorial=material.get('editorial') or {}
    evidence=[];limits=[]
    def add(kind, content, budget):
        if not content or not content.strip():return
        if len(content)>budget:limits.append(kind+' 内容有截断；不能对未提供部分下结论。')
        content=content[:budget]
        for i in range(0,len(content),1800):
            evidence.append({'evidence_id':f"{entry['id']}:{kind}:{i//1800}",'kind':kind,'text':content[i:i+1800]})
    add('statement','\n'.join(str(statement.get(k) or '') for k in ('body','input_format','output_format','interaction_format','note')),
        12000)
    add('editorial',editorial.get('text') or '',18000)
    codes=[x for x in editorial.get('codeBlocks',[]) if is_reference_code_block(x)]
    if codes:
        add('code',codes[0]['text'],14000)
        limits.append('附带代码仅为公开参考，未在本地评测；只提供第一份参考实现。')
    else:limits.append('没有参考代码；不可声称核对过实际实现。')
    if not statement:limits.append('缺少题面正文。')
    if not editorial.get('text'):limits.append('缺少题解正文。')
    limits.extend(str(x)[:400] for x in editorial.get('warnings',[])[:6])
    if editorial.get('pdfPages'):limits.append('PDF 提取文本的公式、布局未逐一人工校验。')
    result={'problem_id':entry['id'],'problem':entry['problem'],'evidence':evidence,
            'input_limits':limits,'reference_code_available':bool(codes),
            'sourceHashes':[material.get('contentHash'),material.get('editorialHash')]}
    return result | {'hash':signature(result),'complete_material':complete(editorial)}


def validate_intent(raw, valid_tags=None):
    if not isinstance(raw,dict):fail()
    if not {'tagGroups','excludeTags'}<=set(raw):
        fail('AI 检索计划缺少方向字段，本轮未执行检索，请重试。')
    if not isinstance(raw.get('retrievalTarget'),str) or raw['retrievalTarget'] not in SOURCE_WEIGHTS:
        fail('AI 检索计划缺少有效的资料类型，本轮未执行检索，请重试。')
    qs=raw.get('skill_queries');criteria=raw.get('criteria');soft=raw.get('soft');reasons=raw.get('reasonCodes')
    def query(q):return isinstance(q,dict) and set(q)=={'zh','en'} and all(text(v,400) for v in q.values())
    if not isinstance(qs,list) or not 1<=len(qs)<=(6 if raw['retrievalTarget']=='statement' else 3) or not all(query(q) for q in qs):fail()
    if raw.get('implementation_query') is not None and not query(raw['implementation_query']):fail()
    if not isinstance(criteria,list) or not 1<=len(criteria)<=3:fail()
    if any(not isinstance(c,dict) or c.get('name')!=f'c{i+1}' or not text(c.get('description'),400) for i,c in enumerate(criteria)):fail()
    if not isinstance(soft,list) or len(soft)>3 or not all(text(x,400) for x in soft):fail()
    if not isinstance(reasons,list) or not 1<=len(reasons)<=2 or not all(isinstance(x,str) and x in REASONS for x in reasons):fail()
    groups=raw.get('tagGroups',[]);excluded=raw.get('excludeTags',[])
    if not isinstance(groups,list) or len(groups)>3 or any(not isinstance(g,list) or not 1<=len(g)<=8 for g in groups):fail()
    if not isinstance(excluded,list) or len(excluded)>8:fail()
    tags=[t for group in groups for t in group]+excluded
    if any(not isinstance(t,str) or (valid_tags is not None and t not in valid_tags) for t in tags):fail()
    if any(set(g)<=set(excluded) for g in groups):fail()
    return {k:raw[k] for k in ('skill_queries','implementation_query','criteria','soft','reasonCodes')} | {
        'tagGroups':groups,'excludeTags':excluded,'retrievalTarget':raw['retrievalTarget']}


def validate_card(c, b):
    allowed={e['evidence_id'] for e in b['evidence']}
    if not isinstance(c,dict) or not refs_valid(c,allowed) or not text(c.get('task')):fail()
    try:
        answer,values,proof,imp=c['answer'],c['values'],c['proof'],c['implementation']
        if answer['kind'] not in ('construction','decision','number','interactive','mixed','unknown'):fail()
        if not text(answer['description']) or not isinstance(answer['evidence_ids'],list):fail()
        # Models sometimes supply a third valid value. Validate all of them before
        # retaining the first two, rather than throwing away the whole grounded card.
        if not isinstance(values,list) or len(values)>5:fail()
        for v in values:
            if not text(v['mechanism']) or not text(v['conditions']) or v['role'] not in ('central','supporting') or not isinstance(v['evidence_ids'],list):fail()
        if proof['support'] not in ('explained','partial','assertion_only','missing') or not isinstance(proof['evidence_ids'],list):fail()
        if not isinstance(proof['techniques'],list) or not all(text(x) for x in proof['techniques']) or not isinstance(proof['argument'],str):fail()
        if imp['burden'] not in ('compact','moderate','heavy','unknown') or imp['basis'] not in ('code','editorial','unknown'):fail()
        if not isinstance(imp['structures'],list) or not all(text(x) for x in imp['structures']) or not isinstance(imp['coordination'],str) or not isinstance(imp['evidence_ids'],list):fail()
        if imp['basis']=='code' and (not b['reference_code_available'] or not any(':code:' in x for x in imp['evidence_ids'])):fail()
        if not isinstance(c['limits'],list) or not all(text(x) for x in c['limits']):fail()
        if len(json.dumps(c))>20000:fail()
    except (KeyError,TypeError):fail()
    return c | {'values':values[:2]}


def unavailable_card():
    """No invented facets: raw material retrieval/auditing remains available."""
    return {'task':'学习卡未通过结构或引用检查',
            'answer':{'kind':'unknown','description':'未知','evidence_ids':[]},'values':[],
            'proof':{'techniques':[],'argument':'','support':'missing','evidence_ids':[]},
            'implementation':{'burden':'unknown','structures':[],'coordination':'','basis':'unknown','evidence_ids':[]},
            'limits':['此题学习卡不可用；必须以原始资料判断，不使用生成卡中的结论。']}


def validate_audit(raw, candidates, intent):
    assessments=raw.get('assessments') if isinstance(raw,dict) else None
    if not isinstance(assessments,dict) or set(assessments)!=set(candidates):fail()
    eligible=[]
    expected={c['name'] for c in intent['criteria']}
    aliases={c['name']:c['name'] for c in intent['criteria']}
    descriptions=[c.get('description') for c in intent['criteria']]
    for c in intent['criteria']:
        if c.get('description') and descriptions.count(c['description'])==1 and c['description'] not in aliases:
            aliases[c['description']]=c['name']
    for pid,a in assessments.items():
        try:
            allowed={e['evidence_id'] for e in candidates[pid]['evidence']}
            if not refs_valid(a,allowed) or a['match'] not in ('strong','moderate','weak','unknown'):fail()
            cs=a['criteria']
            if isinstance(cs,list):
                cs=[c|{'name':aliases.get(c.get('name'),c.get('name'))} if isinstance(c,dict) else c for c in cs]
                a['criteria']=cs
            if not isinstance(cs,list) or len(cs)!=len(expected) or {c['name'] for c in cs}!=expected:fail()
            if any(c['state'] not in ('yes','partial','no','unknown') or not text(c['reason']) or not isinstance(c['evidence_ids'],list) for c in cs):fail()
            if not text(a['reason']) or not isinstance(a['tradeoff'],str):fail()
            if a['match']=='strong' and all(c['state']=='yes' and c['evidence_ids'] for c in cs):eligible.append(pid)
        except (KeyError,TypeError):fail()
    return assessments,eligible


def validate_rank(raw, candidates):
    assessments=raw.get('assessments') if isinstance(raw,dict) else None
    first=raw.get('first') if isinstance(raw,dict) else None
    if not isinstance(assessments,dict) or set(assessments)!=set(candidates):fail()
    if not isinstance(first,list) or not all(isinstance(x,str) for x in first) or len(set(first))!=len(first):fail()
    try:
        for pid,a in assessments.items():
            if a['tier'] not in ('first','alternative','later') or not text(a['reason']):fail()
            if not refs_valid(a,{e['evidence_id'] for e in candidates[pid]['evidence']}):fail()
            if a['tier']=='first' and not a.get('evidence_ids'):fail()
        if set(first)!={p for p,a in assessments.items() if a['tier']=='first'}:fail()
    except (KeyError,TypeError):fail()
    return first
