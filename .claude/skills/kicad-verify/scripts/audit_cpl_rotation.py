#!/usr/bin/env python3
"""Settle CPL rotation offline, without a JLCPCB preview.

The one thing a local audit normally cannot answer is whether a CPL angle is
measured against the same zero-degree orientation JLC's pick-and-place uses,
because that lives in JLC's own parts library.  It is reachable: LCSC serves
the EasyEDA package model for any part number, and that model IS what JLC
assembles from.

So for each part we fetch LCSC's model, centre both pad constellations, and
ask at which of 0/90/180/270 degrees the project's footprint lands pad-for-pad
on LCSC's *with the pad numbers agreeing*.  Numbers matter: a SOIC-8's pad
positions are 180-degree symmetric, so a position-only test calls it harmless
when rotating it would in fact swap pin 1 with pin 5.

Reading "correct as emitted" for a part means its CPL angle needs no JLC
rotation correction.  Three outcomes need a human:

  * NEEDS n deg      - the CPL angle for that part is wrong by n degrees.
  * numbering differ - the two libraries number the same pads differently.
    Benign for a symmetric part (an SPST switch, a two-terminal passive);
    read the geometry line before dismissing it.
  * could not fetch  - no LCSC model; fall back to the vendor drawing.

Network: reaches easyeda.com.  Responses are cached under /tmp/ezcache.

Run:  python3 audit_cpl_rotation.py     (from the project root)
"""
import json, math, re, subprocess, sys, time, pathlib
sys.path.insert(0,'tools')
from design_data import parts, footprint_for
CACHE=pathlib.Path('/tmp/ezcache'); EZ=0.254
UA=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36")
def fetch(l):
    f=CACHE/f"{l}.json"
    if not f.exists() or f.stat().st_size<40:
        subprocess.run(["curl","-sS","--max-time","45","-A",UA,
            "-H","Accept: application/json, text/plain, */*","-H","Referer: https://easyeda.com/",
            "-o",str(f),f"https://easyeda.com/api/products/{l}/components?version=6.4.19.5"],
            capture_output=True); time.sleep(0.8)
    try: return json.loads(f.read_text())
    except Exception: return None
def lcsc(l):
    d=fetch(l)
    if not d or not d.get("success"): return None
    pk=(d.get("result") or {}).get("packageDetail")
    if not pk: return None
    out=[]
    for s in pk["dataStr"]["shape"]:
        g=s.split("~")
        if g[0]=="PAD": out.append((g[8].strip(), float(g[2])*EZ, float(g[3])*EZ))
    return out or None
def proj(n):
    p=pathlib.Path('kicad/HomeKey_RevA.pretty')/f"{n}.kicad_mod"
    if not p.exists(): return None
    return [(m.group(1), float(m.group(2)), float(m.group(3))) for m in
            re.finditer(r'\(pad "?([^"\s)]+)"?\s+\S+\s+\S+\s+\(at ([-\d.]+) ([-\d.]+)', p.read_text())] or None
def ctr(ps):
    cx=sum(p[1] for p in ps)/len(ps); cy=sum(p[2] for p in ps)/len(ps)
    return [(n,x-cx,y-cy) for n,x,y in ps]
def mapping(a,b,deg,tol=0.4):
    if len(a)!=len(b): return None
    r=math.radians(deg); pool=list(b); out=[]
    for n,x,y in a:
        rx,ry=x*math.cos(r)-y*math.sin(r), x*math.sin(r)+y*math.cos(r)
        h=min(pool,key=lambda q: math.dist((rx,ry),(q[1],q[2])),default=None)
        if h is None or math.dist((rx,ry),(h[1],h[2]))>tol: return None
        pool.remove(h); out.append((n,h[0]))
    return out

seen={}
for p in parts:
    if not p.lcsc or p.dnp or p.fields.get("ExcludeFromBOM")=="yes": continue
    seen.setdefault((p.lcsc, footprint_for(p).split(':',1)[1]), []).append(p.ref)
cplrefs={l.split(',')[0] for l in open('production/assembly/CPL_JLCPCB_DRAFT.csv',encoding='utf-8-sig')}
POLARISED={'D1','D2','D3','D4','D5','C44','U1','U2','U3','U4','U5','U6','Q1','X1','J1','J2','J3','J4','SW1','SW2','SW3'}

rows=[]
for (l,name),refs in sorted(seen.items(), key=lambda kv: kv[1][0]):
    lp, pp = lcsc(l), proj(name)
    label=",".join(sorted(refs)); incpl=any(r in cplrefs for r in refs)
    pol = any(r in POLARISED for r in refs)
    if not lp or not pp: rows.append((label,l,name,incpl,pol,None,None)); continue
    a,b=ctr(pp),ctr(lp)
    idmatch=[d for d in (0,90,180,270) if (m:=mapping(a,b,d)) and all(x==y for x,y in m)]
    anymatch={d:mapping(a,b,d) for d in (0,90,180,270) if mapping(a,b,d)}
    rows.append((label,l,name,incpl,pol,idmatch,anymatch))

print(f"{'refs':<17} {'LCSC':<10} {'pol':<4} {'CPL':<4} {'numbering identical at':<24} verdict")
alarm=[]
for label,l,name,incpl,pol,idm,anym in rows:
    P='YES' if pol else '-'; C='yes' if incpl else 'THT'
    if idm is None:
        v="could not fetch LCSC model"; s='-'
    elif idm==[0] or (0 in idm and len(idm)==1):
        v="correct as emitted"; s=str(idm)
    elif 0 in idm:
        v="correct at 0 (and symmetric)"; s=str(idm)
    elif idm:
        v=f"NEEDS {idm[0]} deg"; s=str(idm); alarm.append((label,name,idm[0],pol,incpl))
    else:
        same=sorted(anym) if anym else []
        v=("numbering schemes differ (geometry fits at %s)" % same) if same else "PAD LAYOUT MISMATCH"
        s='none'
        if pol: alarm.append((label,name,None,pol,incpl))
    print(f"{label[:16]:<17} {l:<10} {P:<4} {C:<4} {s:<24} {v}")
print()
if alarm:
    print("=== needs a human decision ===")
    for label,name,d,pol,incpl in alarm:
        print(f"  {label:<18} {name:<40} {'rot '+str(d) if d is not None else 'numbering differs'}"
              f"{'  [POLARISED]' if pol else ''}{'' if incpl else '  [not in CPL]'}")
else:
    print("no rotation corrections needed")
