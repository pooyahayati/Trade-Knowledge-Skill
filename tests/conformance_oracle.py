"""Independent rational-arithmetic oracle; does not import production calculations."""
from decimal import Decimal, localcontext
from fractions import Fraction as F
from datetime import datetime, timezone


def instant(s):return datetime.fromisoformat(s.replace('Z','+00:00')).astimezone(timezone.utc)
def stamp(t):return t.isoformat().replace('+00:00','Z')
def text(x):
    if x is None:return None
    with localcontext() as c:
        c.prec=70
        return format(Decimal(x.numerator)/Decimal(x.denominator),'f')


def oracle(payload):
    s=payload['series'];n=s['atr_period'];cutoff=instant(payload['as_of'])
    bars=[]
    for b in s['bars']:
        if not b['is_closed'] or instant(b['close_time'])>cutoff or instant(b['available_at'])>cutoff:break
        bars.append(b)
    prior=None;atr=None;weight=None;trs=[];rows=[];known=None
    def snap(x,points):
        return next((p for p in points if abs(x-p)<=F(1,10**9)),x)
    for i,b in enumerate(bars):
        o,h,l,c=(F(b[k]) for k in ['open','high','low','close']);size=h-l;body=abs(c-o)
        up=h-max(o,c);down=min(o,c)-l;baseline=atr;bw=weight
        tr=None if prior is None else max(size,abs(h-prior),abs(l-prior))
        if tr is not None:
            trs.append(tr)
            if len(trs)==n:atr=sum(trs)/n;weight=F(1)
            elif len(trs)>n:atr=(atr*(n-1)+tr)/n;weight*=F(n-1,n)
        bf=body/size if size else None;cl=(c-l)/size if size else None
        ratio=comp=None;label='not_computable';dominant=pin=None
        if not size:
            label='flat_bar';dominant=pin=False
            if baseline is not None and baseline>0:ratio=comp=F(0)
        elif baseline is not None and baseline>0:
            ratio=size/baseline;comp=snap(ratio,[F(4,5),F(6,5),F(5,2)])
            label='short' if comp<F(4,5) else 'standard' if comp<=F(6,5) else 'long_range' if comp<=F(5,2) else 'very_long_range'
            dominant=label=='standard' and snap(max(body,up,down)/size,[F(4,5)])>=F(4,5)
            top=snap((min(o,c)-l)/size,[F(4,5)])>=F(4,5) and snap(up/size,[F(1,10)])<=F(1,10)
            bottom=snap((max(o,c)-l)/size,[F(1,5)])<=F(1,5) and snap(down/size,[F(1,10)])<=F(1,10)
            pin=comp>=F(6,5) and (top or bottom)
        known=max(known,instant(b['available_at'])) if known else instant(b['available_at'])
        numeric=dict(true_range=tr,atr_after_bar=atr,baseline_atr=baseline,range=size,body=body,upper_wick=up,lower_wick=down,
            baseline_seed_weight=bw,range_ratio=ratio,comparison_ratio=comp,body_fraction=bf,close_location=cl)
        rows.append(dict(id=b['id'],open_time=stamp(instant(b['open_time'])),close_time=stamp(instant(b['close_time'])),
            available_at=stamp(known),is_closed=True,baseline_bar_id=bars[i-1]['id'] if baseline is not None else None,
            length_class=label,standard_dominant_candidate=dominant,pinbar_candidate=pin,**{k:text(v) for k,v in numeric.items()}))
        prior=c
    return rows
