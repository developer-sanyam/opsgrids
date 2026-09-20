"""Contact sheet for a named subset, so a fix can be judged without re-reading 29 rows."""
import os
import sys

src, out, title = sys.argv[1], sys.argv[2], sys.argv[3]
names = sys.argv[4].split(",")

rows = []
for n in names:
    u = "file://" + os.path.join(src, n + ".svg")
    rows.append(f"""<div class="row"><div class="nm">{n}</div>
      <div class="c" style="width:88px;height:88px"><img src="{u}"></div>
      <div class="c" style="width:132px;height:99px"><img src="{u}"></div>
      <div class="c" style="width:300px;height:225px"><img src="{u}"></div></div>""")

open(out, "w").write(f"""<!doctype html><meta charset="utf-8">
<style>body{{margin:0;padding:18px;background:#8a8f96;font:13px -apple-system,sans-serif}}
h1{{font-size:15px;margin:0 0 12px;color:#fff}}
.row{{display:flex;gap:14px;align-items:flex-end;margin-bottom:12px}}
.nm{{width:120px;text-align:right;color:#1d2026;font-weight:600;padding-bottom:4px}}
.c{{overflow:hidden;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.35);flex:none}}
.c img{{width:100%;height:100%;object-fit:cover;display:block}}</style>
<h1>{title}</h1>{''.join(rows)}""")
print(out)
