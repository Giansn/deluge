"""annot.py <fork_start_hex> <fork_end_hex> [arm] : disassemble a fork region, annotate targets/literals with ref symbols"""
import sys, bisect, subprocess, re, tempfile, os, struct
S = os.environ.get("WORK", os.getcwd())
TC = os.environ.get("TC", "arm-none-eabi-")
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fwdiff.py")).read().split("# ---------------------------------------------------------------- symbols")[0].replace("ref_bin, fork_bin, elf, tc, out_json = sys.argv[1:6]", f"ref_bin, fork_bin, elf, tc, out_json = '{S}/ref.bin','{S}/fork.bin','{S}/ref.elf','{TC}','/dev/null'"))
fk=[b for a,b in chain]
def fork_addr_to_ref(fa):
    fo=fa-FORK_BASE
    if not (0<=fo<len(B)): return None
    k=bisect.bisect_right(fk,fo)-1; a,b=chain[max(k,0)]
    return BASE+a+(fo-b)
syms=[]
for ln in subprocess.run([TC+"readelf","-sW","-C",elf],capture_output=True,text=True).stdout.splitlines():
    p=ln.split(None,7)
    if len(p)==8 and p[0].endswith(":") and p[3] in ("FUNC","OBJECT"):
        try: a,sz=int(p[1],16)&~1,int(p[2])
        except ValueError: continue
        if sz: syms.append((a,sz,p[7]))
syms.sort(); st=[x[0] for x in syms]
def name_ref(a):
    k=bisect.bisect_right(st,a)-1
    while k>=0 and not(syms[k][0]<=a<syms[k][0]+syms[k][1]) and a-syms[k][0]<0x10000: k-=1
    if k>=0 and syms[k][0]<=a<syms[k][0]+syms[k][1]:
        o=a-syms[k][0]; return syms[k][2]+(f"+{o:#x}" if o else "")
    return None
def name_fork(fa):
    if 0x0c000000<=fa<0x10000000:  # SDRAM VMA: same origin
        sd=next(s for s in secs if s["vma"]<0x20000000)
        # approximate: shift by in-sdram growth
        for s in secs:
            if s["vma"]<=fa<s["vma"]+s["size"]+0x1000:
                return (name_ref(fa) or name_ref(fa-88) or "?")+" (sdram≈)"
    r=fork_addr_to_ref(fa)
    return name_ref(r) if r else None
a0,a1=int(sys.argv[1],16),int(sys.argv[2],16)
arm=len(sys.argv)>3 and sys.argv[3]=="arm"
data=B[a0-FORK_BASE:a1-FORK_BASE]
with tempfile.NamedTemporaryFile(delete=False) as t: t.write(data)
args=[TC+"objdump","-D","-b","binary","-marm",f"--adjust-vma={a0:#x}",t.name]
if not arm: args.insert(5,"-Mforce-thumb")
out=subprocess.run(args,capture_output=True,text=True).stdout; os.unlink(t.name)
for l in out.splitlines():
    m=re.match(r"\s*([0-9a-f]+):\t([0-9a-f ]+)\t(.*)$",l)
    if not m: continue
    addr=int(m.group(1),16); ins=m.group(3); note=""
    t2=re.search(r"\b(bl|blx|b\.w|b|b\.n|beq\.w|bne\.w)\s+0x([0-9a-f]+)",ins)
    if t2 and ins.split()[0] in ("bl","blx","b.w"):
        n=name_fork(int(t2.group(2),16)); note=f"  -> {n}"
    lit=re.search(r"@ \(0x([0-9a-f]+)\)",ins)
    if lit:
        la=int(lit.group(1),16); w=struct.unpack("<I",B[la-FORK_BASE:la-FORK_BASE+4])[0]
        n=name_fork(w)
        s=""
        if 0x20000000<=w<0x20300000 or 0x0c000000<=w<0x10000000:
            fo=w-FORK_BASE
            if 0<=fo<len(B):
                mm=re.match(rb"[ -~]{3,}",B[fo:fo+60]); s=f' "{mm.group().decode()}"' if mm else ""
        f=struct.unpack("<f",struct.pack("<I",w))[0]
        note=f"  =0x{w:08x} {n or ''}{s}"+(f"  (float {f:g})" if 1e-6<abs(f)<1e7 else "")
    print(f"{addr:08x}: {ins}{note}")
