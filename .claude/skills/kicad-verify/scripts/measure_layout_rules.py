#!/usr/bin/env python3
"""Measure the board against the layout rules its datasheets actually state.

Vendors write layout rules as prose -- "place the input capacitor as close to
the device as possible", "the VFB trace should be as small as possible" -- which
is easy to nod at and never check.  This turns each rule into a distance from a
component to the pin it serves, computed from absolute pad coordinates with
footprint rotation applied, so the rule either holds or it does not.

The target column is conventional practice, not a vendor number: the datasheets
say "as close as possible" without one.  Use the figures to rank work, not as a
pass/fail line.

Sources: Diodes DS41326 Rev 3-2 (AP63203/AP63205) PCB Layout;
TI SLVSE71 (TPS565201) section 10.1; NXP PN7160_PN7161 Rev 3.2.

Run:  python3 measure_layout_rules.py     (from the project root)
"""
import re, math, sys, pathlib, collections
sys.path.insert(0,'tools')
from design_data import parts
t=pathlib.Path('kicad/HomeKey-Lock-RevA-PN7161.kicad_pcb').read_text()

fp={}
for m in re.finditer(r'\n  \(footprint "([^"]+)".*?\n  \)\n', t, re.S):
    b=m.group(0); r=re.search(r'\(fp_text reference "([^"]+)"',b)
    a=re.search(r'\n    \(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)',b)
    if not r or not a: continue
    x,y,rot=float(a.group(1)),float(a.group(2)),float(a.group(3) or 0)
    pads={}
    for pm in re.finditer(r'\(pad "?([^"\s)]+)"?\s+\S+\s+\S+\s+\(at ([-\d.]+) ([-\d.]+)',b):
        px,py=float(pm.group(2)),float(pm.group(3)); ang=math.radians(rot)
        ax=x+px*math.cos(ang)+py*math.sin(ang); ay=y-px*math.sin(ang)+py*math.cos(ang)
        pads.setdefault(pm.group(1),(ax,ay))
    fp[r.group(1)]=(x,y,rot,pads)

def pad(ref,p):
    d=fp.get(ref)
    return d[3].get(p) if d else None
def dist(r1,p1,r2,p2):
    a,b=pad(r1,p1),pad(r2,p2)
    return math.hypot(a[0]-b[0],a[1]-b[1]) if a and b else None

def report(title, rows):
    print(f"\n### {title}")
    for label, d, budget, why in rows:
        if d is None: print(f"   {label:<44} n/a"); continue
        flag = "OK" if d<=budget else ("tight" if d<=budget*1.6 else "FAR")
        print(f"   {label:<44} {d:6.2f} mm   (target <{budget:.0f})  {flag:<5} {why}")

# --- B: U1 AP63205 5V buck -------------------------------------------------
report("B  5V 逻辑降压 U1 (AP63205)  Diodes: VIN 电容尽量近、FB 元件尽量近", [
  ("C3 10uF 输入电容 -> U1 pin3 VIN", dist("C3","1","U1","3"), 3, "输入回路面积"),
  ("C2 100nF -> U1 pin3 VIN",          dist("C2","1","U1","3"), 5, "高频旁路"),
  ("U1 pin1 FB <- 5V_BAT 取样点 L1",   dist("U1","1","L1","2"), 8, "固定输出，FB 直连输出"),
  ("C4 自举 -> U1 pin6 BST",           dist("C4","1","U1","6"), 3, "自举回路"),
  ("U1 pin5 SW -> L1",                 dist("U1","5","L1","1"), 5, "SW 节点辐射"),
  ("C5 输出电容 -> L1",                 dist("C5","1","L1","2"), 6, "输出纹波"),
])
# --- E: U3 AP63203 3V3 buck ------------------------------------------------
report("E  3V3 降压 U3 (AP63203)", [
  ("C7 10uF 输入电容 -> U3 pin3 VIN", dist("C7","1","U3","3"), 3, "输入回路面积"),
  ("C8 自举 -> U3 pin6 BST",           dist("C8","1","U3","6"), 3, "自举回路"),
  ("U3 pin5 SW -> L2",                 dist("U3","5","L2","1"), 5, "SW 节点辐射"),
  ("C9 输出电容 -> L2",                 dist("C9","1","L2","2"), 6, "输出纹波"),
])
# --- I: U6 TPS565201 servo buck 5A -----------------------------------------
report("I  舵机降压 U6 (TPS565201, 5A)  TI: 输入/输出电容尽量近、VFB 走线尽量短", [
  ("C39 10uF 输入电容 -> U6 pin3 VIN", dist("C39","1","U6","3"), 3, "5A 输入回路，最关键"),
  ("C40 100nF -> U6 pin3 VIN",         dist("C40","1","U6","3"), 4, "高频旁路"),
  ("C41 自举 -> U6 pin6 VBST",         dist("C41","1","U6","6"), 3, "自举回路"),
  ("U6 pin2 SW -> L5",                 dist("U6","2","L5","1"), 5, "TI 规则 4：SW 短而宽"),
  ("R29 下分压 -> U6 pin4 VFB",        dist("R29","1","U6","4"), 5, "TI 规则 9：VFB 尽量短"),
  ("R28 上分压 -> U6 pin4 VFB",        dist("R28","2","U6","4"), 6, "TI 规则 6/9"),
  ("C42 输出电容 -> L5",                dist("C42","1","L5","2"), 6, "输出纹波"),
  ("C44 1000uF -> L5",                 dist("C44","1","L5","2"), 25, "堵转能量缓冲"),
])
# --- G: NFC controller ------------------------------------------------------
report("G  NFC 控制器 U5 去耦与时钟", [
  ("C17 100nF -> U5 pin12/13 VBAT2/VDD(UP)", dist("C17","1","U5","13"), 3, "供电去耦"),
  ("C23 100nF -> U5 pin27 VDD",              dist("C23","1","U5","27"), 3, "核心去耦"),
  ("C26 100nF -> U5 pin17 VMID",             dist("C26","1","U5","17"), 3, "接收基准"),
  ("C21 2.2uF -> U5 pin14 VDD(TX)",          dist("C21","1","U5","14"), 5, "发射供电储能"),
  ("X1 晶振 -> U5 pin30 XTAL1",              dist("X1","1","U5","30"), 8, "27.12MHz，越短越好"),
  ("C15 负载电容 -> X1 pin1",                 dist("C15","1","X1","1"), 4, "晶振负载"),
])
# --- H: RF path symmetry ----------------------------------------------------
print("\n### H  RF 通道对称性（差分必须等长，否则共模泄漏、调谐不对称）")
for a,b,lab in [(("U5","21"),("L3","1"),"U5 TX1 -> L3"),
                (("U5","19"),("L4","1"),"U5 TX2 -> L4"),
                (("L3","2"),("C29","1"),"L3 -> C29"),
                (("L4","2"),("C30","1"),"L4 -> C30"),
                (("C29","2"),("R18","1"),"C29 -> R18"),
                (("C30","2"),("R19","1"),"C30 -> R19"),
                (("R22","2"),("AE1","2"),"R22 -> 天线 ANT_P"),
                (("R23","2"),("AE1","1"),"R23 -> 天线 ANT_N")]:
    d=dist(a[0],a[1],b[0],b[1])
    print(f"   {lab:<24} {d:6.2f} mm" if d is not None else f"   {lab:<24} n/a")
