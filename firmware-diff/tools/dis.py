#!/usr/bin/env python3
"""dis.py <ref_addr_hex> <size> <delta> : disassemble ref function and fork counterpart side by side."""
import subprocess, sys, tempfile, os
TC = os.environ.get("TC", "arm-none-eabi-")
S = os.environ.get("WORK", os.getcwd())
BASE=0x2005c380
def dis(path, vma, n):
    data=open(path,"rb").read()[vma-BASE:vma-BASE+n]
    with tempfile.NamedTemporaryFile(delete=False) as t: t.write(data)
    out=subprocess.run([TC+"objdump","-D","-b","binary","-marm","-Mforce-thumb",f"--adjust-vma={vma:#x}",t.name],capture_output=True,text=True).stdout
    os.unlink(t.name)
    return [l.split(":",1)[1].strip() for l in out.splitlines() if l.strip()[:1].isalnum() and ":\t" in l]
a=int(sys.argv[1],16); n=int(sys.argv[2]); d=int(sys.argv[3])
r=dis(S+"/ref.bin",a,n); f=dis(S+"/fork.bin",a+d,n)
w=max((len(x) for x in r),default=10)
for i in range(max(len(r),len(f))):
    x=r[i] if i<len(r) else ""; y=f[i] if i<len(f) else ""
    mark = "  " if x.split("\t")[1:]==y.split("\t")[1:] else "!!"
    print(f"{mark} {x:<{min(w,60)}} | {y}")
