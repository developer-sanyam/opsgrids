"""Build a contact sheet that shows art at the sizes it is really seen at."""
import glob
import os
import sys

src = sys.argv[1]
out = sys.argv[2]
title = sys.argv[3] if len(sys.argv) > 3 else src

files = sorted(glob.glob(os.path.join(src, "*.svg")))

rows = []
for p in files:
    name = os.path.basename(p)[:-4]
    u = "file://" + p
    rows.append(f"""
    <div class="row">
      <div class="nm">{name}</div>
      <div class="c" style="width:88px;height:88px"><img src="{u}"></div>
      <div class="c" style="width:132px;height:99px"><img src="{u}"></div>
      <div class="c" style="width:240px;height:180px"><img src="{u}"></div>
      <div class="c" style="width:380px;height:285px"><img src="{u}"></div>
    </div>""")

html = f"""<!doctype html><meta charset="utf-8"><title>{title}</title>
<style>
 body{{margin:0;padding:20px;background:#8a8f96;font:13px/1.3 -apple-system,sans-serif;color:#fff}}
 h1{{font-size:15px;margin:0 0 14px}}
 .hdr{{display:flex;gap:14px;align-items:flex-end;margin:0 0 8px 150px;color:#2a2d31;font-weight:600}}
 .hdr span:nth-child(1){{width:88px}} .hdr span:nth-child(2){{width:132px}}
 .hdr span:nth-child(3){{width:240px}} .hdr span:nth-child(4){{width:380px}}
 .row{{display:flex;gap:14px;align-items:flex-end;margin-bottom:14px}}
 .nm{{width:136px;text-align:right;color:#20232a;font-weight:600;padding-bottom:4px}}
 .c{{overflow:hidden;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.35);flex:none}}
 .c img{{width:100%;height:100%;object-fit:cover;display:block}}
</style>
<h1>{title}</h1>
<div class="hdr"><span>88 tile</span><span>132 card</span><span>240 card</span><span>380 hero</span></div>
{''.join(rows)}
"""
open(out, "w").write(html)
print(out, len(files), "pieces")
