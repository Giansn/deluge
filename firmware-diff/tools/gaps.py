import os, bisect, json, subprocess, sys, re
S = os.environ.get("WORK", os.getcwd())
TC = os.environ.get("TC", "arm-none-eabi-")
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fwdiff.py")).read().split("# ---------------------------------------------------------------- symbols")[0].replace("ref_bin, fork_bin, elf, tc, out_json = sys.argv[1:6]", f"ref_bin, fork_bin, elf, tc, out_json = '{S}/ref.bin','{S}/fork.bin','{S}/ref.elf','{TC}','/dev/null'"))
# symbols (all, by ref offset)
syms=[]
for ln in subprocess.run([TC+"readelf","-sW","-C",elf],capture_output=True,text=True).stdout.splitlines():
    p=ln.split(None,7)
    if len(p)==8 and p[0].endswith(":") and p[3] in("FUNC","OBJECT"):
        try: a,sz=int(p[1],16),int(p[2])
        except ValueError: continue
        off,s=vma_to_off(a&~1)
        if off is not None and sz: syms.append((off,sz,p[7]))
syms.sort(); st=[x[0] for x in syms]
def symat(off):
    k=bisect.bisect_right(st,off)-1
    while k>=0 and not(syms[k][0]<=off<syms[k][0]+syms[k][1]) and off-syms[k][0]<4096: k-=1
    return syms[k][2] if k>=0 and syms[k][0]<=off<syms[k][0]+syms[k][1] else "?"
def secname(off):
    for s in secs:
        if s["lma"]-BASE<=off<s["lma"]-BASE+s["size"]: return s["name"]
    return "?"
rows=[]
for k in range(1,len(chain)):
    (r1,f1),(r2,f2)=chain[k-1],chain[k]
    rg=r2-(r1+W); fg=f2-(f1+W)
    if fg-rg>=24 or rg-fg>=24:
        rows.append((fg-rg,r1+W,r2,f1+W,f2))
rows.sort(key=lambda x:-abs(x[0]))
for extra,ra,rb,fa,fb in rows[:25]:
    txt=re.findall(rb"[ -~]{4,}",B[fa:fb])
    print(f"{extra:+6d} B  ref 0x{BASE+ra:08x}-0x{BASE+rb:08x} ({rb-ra}B) -> fork 0x{FORK_BASE+fa:08x}-0x{FORK_BASE+fb:08x} ({fb-fa}B) [{secname(ra)}] in/after: {symat(ra)[:70]} .. {symat(rb-1)[:50]}")
    if txt: print("        strings:", [t.decode() for t in txt][:12])
