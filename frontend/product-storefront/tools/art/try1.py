import os
import sys

sys.path.insert(0, "/tmp/art")
from quickcart import BASE, bottle, box3d, ground, pouch, sack, scene, tub
from kit import svg

OUT = "/tmp/art/try1"
os.makedirs(OUT, exist_ok=True)


def emit(name, label, parts):
    d, b = [], []
    for pd, pb in parts:
        d += pd
        b += pb
    open(os.path.join(OUT, name + ".svg"), "w").write(svg(label, d, b))


emit("p-milk", "Pouch of toned milk", [
    scene("dairy"),
    ground("sh", 196, 92),
    pouch("g1", 188, 56, 138, 176, "#FFFFFF", "#EEF4FB", "#BDD3E8", "#1F5FA8", "#1F5FA8",
          "#FFFFFF"),
])

emit("p-atta", "Pack of chakki atta", [
    scene("staples"),
    ground("sh", 202, 96),
    pouch("g1", 206, 52, 146, 180, "#FFF6E2", "#F2DCA8", "#BE9B58", "#8A5A1E", "#8A5A1E",
          "#FFE9B8"),
])

emit("p-oil", "Bottle of sunflower oil", [
    scene("staples"),
    ground("sh", 200, 76),
    bottle("g1", 194, 44, 108, 188, "#FFEAA6", "#F3CB4E", "#BC8F10", "#2B2118", "#FFFFFF",
           "#8A5A1E"),
])

emit("p-curd", "Tub of set curd", [
    scene("dairy"),
    ground("sh", 198, 90),
    tub("g1", 204, 92, 156, 140, "#FFFFFF", "#F0F5FB", "#BFD3E7", "#1F5FA8", "#1F5FA8"),
])

emit("p-biscuits", "Pack of biscuits", [
    scene("snacks"),
    ground("sh", 196, 96),
    box3d("g1", 116, 86, 142, 146, 38, "#FFDCC3", "#F4B487", "#BE753C", "#FFE9D7", "#FFFFFF",
          "#9C4A1A"),
])

emit("p-rice", "Sack of basmati rice", [
    scene("staples"),
    ground("sh", 200, 98),
    sack("g1", 196, 50, 162, 182, "#FFF8E8", "#EFDDB8", "#B99B5E", "#8A5A1E", "#FFFFFF",
         "#8A5A1E"),
])
print("ok")
