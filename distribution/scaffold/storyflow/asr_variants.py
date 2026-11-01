"""Bounded, independently reviewed ASR spelling variants; never edits raw evidence."""
import re
from .core import WorkflowError

_DIGITS={c:i for i,c in enumerate('零一二三四五六七八九')}
_DIGITS.update({'〇':0,'两':2})
_UNITS={'十':10,'百':100,'千':1000,'万':10000,'亿':100000000}

def numeral(text):
    if not re.fullmatch('[0-9零〇一二三四五六七八九两十百千万亿]+',text):return None
    if not any(c in _UNITS for c in text):
        return int(''.join(str(_DIGITS[c]) if c in _DIGITS else c for c in text))
    # Deliberately reject unsupported composite/repeated large units.
    if text.count('亿')>1 or text.count('万')>1 or ('亿' in text and '万' in text.split('亿')[0]):return None
    total=section=number=0;previous_small=10000
    for token in re.findall('[0-9]+|.',text):
        if token.isascii() and token.isdigit():number=int(token)
        elif token in _DIGITS:number=number*10+_DIGITS[token]
        else:
            unit=_UNITS[token]
            if unit<10000:
                if unit>=previous_small:return None
                previous_small=unit;section+=(number or 1)*unit
            else:total+=(section+number or 1)*unit;section=0;previous_small=10000
            number=0
    return total+section+number

def adjudicate(value,actual,target,author,normalize):
    review=value.get('manual_adjudication',{})
    reviewer=value.get('reviewer')
    if value.get('status')!='pass' or not isinstance(reviewer,str) or not reviewer.strip() or reviewer.strip().casefold()==author.strip().casefold() or review.get('verdict')!='pass' or not isinstance(review.get('reason'),str) or not review['reason'].strip():
        raise WorkflowError('ASR spelling differences need independent bound adjudication')
    differences=review.get('allowed_differences')
    if not isinstance(differences,list) or not 1<=len(differences)<=8:raise WorkflowError('ASR adjudication needs bounded explicit spans')
    transformed=actual;changed=False
    for diff in differences:
        if not isinstance(diff,dict):raise WorkflowError('invalid ASR difference')
        old,new=diff.get('actual'),diff.get('expected')
        if any(not isinstance(s,str) or not s.strip() for s in (old,new,diff.get('scope'),diff.get('reason'))) or old==new or max(len(old),len(new))>20:raise WorkflowError('invalid ASR difference span')
        if old not in transformed:continue
        if transformed.count(old)!=1 or target.count(new)!=1:raise WorkflowError('ASR difference must identify one exact occurrence')
        a,b=numeral(old),numeral(new)
        numeric_span=bool(re.fullmatch('[0-9零〇一二三四五六七八九两十百千万亿]+',old) or re.fullmatch('[0-9零〇一二三四五六七八九两十百千万亿]+',new))
        if numeric_span:
            if a is None or b is None or a!=b:raise WorkflowError('ASR numeric value differs')
        elif not (2<=len(old)==len(new)<=6 and all('\u4e00'<=c<='\u9fff' for c in old+new) and value.get('owner_review',{}).get('status')=='pending_owner_review'):
            raise WorkflowError('ASR nonnumeric differences restricted to reviewed short proper-name spellings')
        transformed=transformed.replace(old,new,1);changed=True
    if not changed or normalize(transformed)!=normalize(target):raise WorkflowError('ASR differences do not fully account for transcript')
    return transformed
