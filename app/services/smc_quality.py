"""Versioned, explanatory quality score; not a calibrated win probability."""
import math

VERSION = "confluence-1.1"

def cost_guard(row):
    entry, stop = row.get("entry"), row.get("stop")
    if not entry or stop is None or entry == stop:
        return
    cost_r = entry * .0014 / abs(entry-stop)
    row["estimated_roundtrip_cost_r"] = round(cost_r,3)
    if row.get("ready") and cost_r >= 1:
        row.update(ready=False, phase="MALIYET_BEKLE")

def score(row):
    steps = {s["key"]: bool(s["ok"]) for s in row.get("steps", [])}
    seq = row.get("sequence", {})
    entry = row.get("entry") or 0
    stop = row.get("stop") or entry
    risk = abs(entry-stop)
    ob = seq.get("order_block")
    ob_fit = 0.0
    if ob and risk > 0 and ob.get("mitigated_at") is None and not ob.get("is_breaker"):
        distance = max(ob["bottom"]-entry, entry-ob["top"], 0)
        ob_fit = max(0, 1-distance/risk)
    rr = max(0, min(1, (float(row.get("rr") or 0)-1)/3))
    age = max(0, float(row.get("confirmation_age_bars") or 0))
    recency = max(0, 1-age/12)
    funding = row.get("funding_bp")
    funding_fit = 0 if funding is None else max(0, 1-abs(float(funding))/5)
    factors = [
        ("Üst zaman yönü", 10, float(steps.get("htf", False))),
        ("Premium / discount", 4, float(row.get("htf",{}).get("location_ok",False))),
        ("Likidite süpürmesi", 16, float(bool(seq.get("sweep")))),
        ("Displacement", 12, min(1,0.5+float(seq["displacement"].get("body_atr",0))/4) if seq.get("displacement") else 0),
        ("Yapı kırılımı", 12, float(bool(seq.get("mss")))),
        ("CISD", 8, float(bool(seq.get("cisd")))),
        ("FVG / IFVG", 12, float(bool(seq.get("fvg") or seq.get("ifvg")))),
        ("Order block yakınlığı", 10, ob_fit),
        ("Risk / getiri", 8, rr),
        ("Teyit tazeliği", 5, recency),
        ("Funding dengesi", 3, funding_fit),
    ]
    components = [{"name":name,"weight":weight,"value":round(value,3),"points":round(weight*value,2)}
                  for name,weight,value in factors]
    base = round(sum(f["points"] for f in components),1)
    assert math.isfinite(base)
    return {"version":VERSION,"base":base,"score":base,"learning_delta":0,
            "samples":0,"components":components,"probability":False}
