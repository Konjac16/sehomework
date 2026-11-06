"""Shared, non-AI material availability rules for candidate selection and retrieval."""
import re

REQUIRED_PERCENT = 80


def has_editorial(material):
    text = (material or {}).get('text')
    return (isinstance(text,str) and len(text.strip()) >= 80
            and (material or {}).get('textStatus') != 'missing'
            and not re.search(r'Tutorial is (?:loading|not available)|Unable to parse markup',text,re.I))


def is_reference_code_block(block):
    if not isinstance(block,dict) or not isinstance(block.get('text'),str):
        return False
    text=block['text'].strip()
    if not text:
        return False
    # CF blogs also use <pre> for sample input/grids. Recognize this narrow data
    # shape without assuming every reference implementation uses C++ syntax.
    first=text.splitlines()[0].strip()
    numeric_header=re.fullmatch(r'[+-]?\d+(?:\.\d+)?(?:[ \t]+[+-]?\d+(?:\.\d+)?)*',first)
    if numeric_header and re.fullmatch(r'[\w\s.+#-]+',text):
        return False
    return True


def has_code(material):
    blocks = (material or {}).get('codeBlocks')
    return isinstance(blocks,list) and any(is_reference_code_block(b) for b in blocks)


def complete(material):
    return bool(has_editorial(material) and has_code(material))


def required_count(total):
    return (total * REQUIRED_PERCENT + 99) // 100


def coverage(total, count, stale=False, target=None):
    target=total if target is None else target
    required = required_count(target)
    ready = target > 0 and total==target and count >= required and not stale
    return {'minimumPercent':REQUIRED_PERCENT,'total':total,'targetCount':target,'completeCount':count,
            'requiredCount':required,'shortfall':max(0,required-count),
            'candidateShortfall':max(0,target-total),
            'ratio':count/total if total else 0,'ready':ready,
            'status':'stale' if stale else 'empty' if not target else 'ready' if ready else 'insufficient'}
