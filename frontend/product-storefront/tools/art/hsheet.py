"""Hero contact sheet: the 3:1 band a hero actually crops to, beside the full 4:3 frame."""
import glob
import os
import sys

src, out, title = sys.argv[1], sys.argv[2], sys.argv[3]
rows = []
for p in sorted(glob.glob(os.path.join(src, "*.svg"))):
    n = os.path.basename(p)[:-4]
    u = "file://" + p
    rows.append(f"""<div class="row"><div class="nm">{n}</div>
      <div class="c" style="width:620px;height:210px"><img src="{u}"></div>
      <div class="c" style="width:186px;height:63px"><img src="{u}"></div>
      <div class="c" style="width:200px;height:150px"><img src="{u}"></div></div>""")

open(out, "w").write(f"""<!doctype html><meta charset="utf-8">
<style>body{{margin:0;padding:18px;background:#8a8f96;font:13px -apple-system,sans-serif}}
h1{{font-size:15px;margin:0 0 6px;color:#fff}}
p{{color:#232730;margin:0 0 14px;font-weight:600}}
.row{{display:flex;gap:14px;align-items:center;margin-bottom:14px}}
.nm{{width:118px;text-align:right;color:#1d2026;font-weight:600}}
.c{{overflow:hidden;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.35);flex:none}}
.c img{{width:100%;height:100%;object-fit:cover;display:block}}</style>
<h1>{title}</h1><p>hero band 3:1 &nbsp;·&nbsp; the same band in the gallery miniature &nbsp;·&nbsp; full 4:3</p>{''.join(rows)}""")
print(out)
