import os, sys, bisect, json, subprocess
S = os.environ.get("WORK", os.getcwd())
TC = os.environ.get("TC", "arm-none-eabi-")
B=open(S+"/fork.bin","rb").read(); FB=0x2005c3c0
targets=[int(x,16) for x in sys.argv[1:]]
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fwdiff.py")).read().split("# ---------------------------------------------------------------- symbols")[0].replace("ref_bin, fork_bin, elf, tc, out_json = sys.argv[1:6]", f"ref_bin, fork_bin, elf, tc, out_json = '{S}/ref.bin','{S}/fork.bin','{S}/ref.elf','{TC}','/dev/null'"))
# inverse map fork offset -> ref offset using anchor chain
fk=[b for a,b in chain]
def fork_to_ref(fo):
    k=bisect.bisect_right(fk,fo)-1
    a,b=chain[max(k,0)]
    return a+(fo-b), fo-b-(k>=0 and 0)
syms=[]
for ln in subprocess.run([TC+"readelf","-sW","-C",elf],capture_output=True,text=True).stdout.splitlines():
    p=ln.split(None,7)
    if len(p)==8 and p[0].endswith(":") and p[3]=="FUNC":
        try: a,sz=int(p[1],16)&~1,int(p[2])
        except ValueError: continue
        off,s=vma_to_off(a)
        if off is not None and sz: syms.append((off,sz,p[7]))
syms.sort(); st=[x[0] for x in syms]
def near(off):
    k=bisect.bisect_right(st,off)-1
    if k>=0 and syms[k][0]<=off<syms[k][0]+syms[k][1]: return "in "+syms[k][2]
    nxt=syms[k+1][2] if k+1<len(syms) else "?"
    return f"between {syms[k][2] if k>=0 else '?'} and {nxt} (new code?)"
for i in range(0,len(B)-4,2):
    h1=int.from_bytes(B[i:i+2],"little"); h2=int.from_bytes(B[i+2:i+4],"little")
    if (h1&0xF800)!=0xF000 or (h2&0xD000) not in (0xD000,0xC000,0x9000): continue
    S_=(h1>>10)&1; imm10=h1&0x3FF; J1=(h2>>13)&1; J2=(h2>>11)&1; imm11=h2&0x7FF
    I1=1-(J1^S_); I2=1-(J2^S_)
    imm=(S_<<24)|(I1<<23)|(I2<<22)|(imm10<<12)|(imm11<<1)
    if S_: imm-=1<<25
    pc=FB+i; t=pc+4+imm
    if (h2&0xD000)==0xC000: t&=~3
    if t in targets:
        ro,_=fork_to_ref(i)
        print(f"fork 0x{pc:08x} -> 0x{t:08x}   ~ref 0x{BASE+ro:08x}  {near(ro)[:140]}")
