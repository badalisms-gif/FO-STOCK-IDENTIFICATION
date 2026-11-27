"""Read-only Upstox quote adapter; never uses last trade time as depth timestamp."""
from research_v3.forward_paper_execution import Quote, valid
from live_market_engine import fetch_quotes
from stock_intelligence_engine import parse_timestamp, now

def parse_depth(item, observed):
    try:
        stamp=parse_timestamp(item.get("timestamp"))
        if stamp is None: return None
        depth=item["depth"]
        bid=max(depth["buy"],key=lambda x:float(x["price"]))
        ask=min((x for x in depth["sell"] if float(x["price"])>0),key=lambda x:float(x["price"]))
        q=Quote(stamp,float(bid["price"]),float(ask["price"]),int(bid["quantity"]),int(ask["quantity"]))
        return q if valid(q,observed) else None
    except (KeyError,TypeError,ValueError,OverflowError):
        return None

def read_depth(token,keys):
    raw,errors=fetch_quotes(token,keys)
    observed=now()
    quotes={}
    for key in keys:
        q=parse_depth(raw.get(key,{}),observed)
        if q is not None: quotes[key]=q
    return observed,quotes,{"requested":len(keys),"accepted":len(quotes),"api_errors":len(errors)}
