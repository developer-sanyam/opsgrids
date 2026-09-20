"""Hero art for the six trade themes, plus a wide plate for Atelier.

These are a *third* visual language, on purpose. QuickCart is small packaged goods on a marigold
shelf; Atelier is one large piece of furniture in a dark room. These are **landscape stacks of
material** — pallets, bags, coils, rail — shot low and wide, each in the palette of its own
theme's tokens so the gallery card and the picture agree with each other.

**The safe band.** A hero renders about 1240x420, which is 2.95:1. `object-fit: cover` on a 4:3
source keeps only y 82..217 of the 300. Everything that has to be recognised therefore lives in
y 80..220; the rest of the frame is atmosphere that only the 4:3 crops ever show.
"""

from kit import backdrop, cast, ell, f, lin, path, rad, rect, svg

SAFE_TOP, SAFE_BOT = 80, 220     # what survives a 3:1 crop
GROUND = 214                     # the floor line, just inside the safe band


def room(gid, top, bottom, glow, glow_at=(0.34, 0.34), glow_r=0.66,
         floor_near=None, floor_far=None, floor_y=GROUND):
    """A wide bay: graded back wall, one pool of light, a floor that recedes."""
    d, b = backdrop(gid, top, bottom)
    d.append(rad(gid + "g", ((0, glow, "0.5"), (0.6, glow, "0.12"), (1, glow, "0")),
                 cx=glow_at[0], cy=glow_at[1], r=glow_r))
    b.append(rect(0, 0, 400, 300, "url(#%sg)" % gid))
    if floor_near:
        d.append(lin(gid + "f", ((0, floor_far, None), (1, floor_near, None)), 0, 0, 0, 1))
        b.append(rect(0, floor_y, 400, 300 - floor_y, "url(#%sf)" % gid))
    d.append(lin("gl", ((0, "#FFFFFF", "0.4"), (1, "#FFFFFF", "0")), 0, 0, 1, 0))
    d.append(lin("sd", ((0, "#000000", "0.34"), (1, "#000000", "0")), 1, 0, 0, 0))
    return d, b


def shadow(gid, cx, cy, rx, ry, op=0.38, colour="#000"):
    return cast(gid, cx, cy, rx, ry, colour, op)


def slab(gid, x, y, w, h, dep, light, mid, dark, top_c=None):
    """A box seen from slightly above and to the left: front, top and one receding side."""
    dy = dep * 0.5
    d = [lin(gid, ((0, light, None), (0.5, mid, None), (1, dark, None)), 0, 0, 1, 0.2)]
    b = [
        path("M%s %s l%s -%s h%s l-%s %s Z" % (f(x), f(y), f(dep), f(dy), f(w), f(dep), f(dy)),
             top_c or light),
        path("M%s %s l%s -%s v%s l-%s %s Z" % (f(x + w), f(y), f(dep), f(dy), f(h), f(dep),
                                               f(dy)), dark),
        rect(x, y, w, h, "url(#%s)" % gid),
    ]
    return d, b


def bag(gid, cx, base, w, h, light, mid, dark, band, seam):
    """A filled paper sack lying flat.

    The first attempt was an ellipse and read as a bean. A sack has *corners*: near-vertical ends,
    a bulged top and bottom, a fold ear at each end and one printed panel across the middle.
    """
    hw = w / 2
    l, r = cx - hw, cx + hw
    top = base - h
    d = [lin(gid, ((0, light, None), (0.45, mid, None), (1, dark, None)), 0, 0, 0.3, 1)]
    b = [
        path("M%s %s Q%s %s %s %s Q%s %s %s %s Q%s %s %s %s Q%s %s %s %s Z" % (
            f(l + 10), f(base - 4),
            f(l - 5), f(base - h * 0.52), f(l + 13), f(top + 5),
            f(cx), f(top - 7), f(r - 13), f(top + 5),
            f(r + 5), f(base - h * 0.52), f(r - 10), f(base - 4),
            f(cx), f(base + 8), f(l + 10), f(base - 4)), "url(#%s)" % gid),
        # the ear each corner of a filled sack folds into
        path("M%s %s q-15 -3 -20 -14 q14 1 22 7 Z" % (f(l + 12), f(top + h * 0.34)), mid),
        path("M%s %s q15 -3 20 -14 q-14 1 -22 7 Z" % (f(r - 12), f(top + h * 0.34)), dark),
        # the stitched seam across the mouth
        rect(l + 14, top + h * 0.12, w - 28, 5, dark, rx=2.5, op="0.45"),
        rect(cx - w * 0.28, base - h * 0.62, w * 0.56, h * 0.34, band, rx=5),
        rect(cx - w * 0.19, base - h * 0.54, w * 0.38, 8, seam, rx=4),
        rect(cx - w * 0.19, base - h * 0.4, w * 0.22, 6, seam, rx=3, op="0.72"),
        path("M%s %s Q%s %s %s %s V%s Q%s %s %s %s Z" % (
            f(l + 12), f(base - h * 0.2), f(l - 1), f(base - h * 0.56), f(l + 16), f(top + 9),
            f(top + 16), f(l + 12), f(base - h * 0.5), f(l + 26), f(base - h * 0.2)),
             "url(#gl)"),
        path("M%s %s Q%s %s %s %s V%s Q%s %s %s %s Z" % (
            f(r - 12), f(base - h * 0.2), f(r + 1), f(base - h * 0.56), f(r - 16), f(top + 9),
            f(top + 16), f(r - 12), f(base - h * 0.5), f(r - 26), f(base - h * 0.2)),
             "url(#sd)"),
    ]
    return d, b


def coil(gid, cx, cy, r, light, mid, dark, core, turns=4):
    """A coil of cable, lateral or strapping, seen end-on."""
    d = [lin(gid, ((0, light, None), (0.5, mid, None), (1, dark, None)), 0, 0, 1, 0.3)]
    b = [ell(cx, cy, r, r * 0.92, "url(#%s)" % gid)]
    for i in range(turns):
        rr = r - (i + 1) * (r * 0.62 / turns)
        b.append(ell(cx, cy, rr, rr * 0.92, dark if i % 2 else mid, op="0.55"))
    b += [
        ell(cx, cy, r * 0.34, r * 0.31, core),
        path("M%s %s A%s %s 0 0 1 %s %s" % (f(cx - r), f(cy), f(r), f(r * 0.92),
                                            f(cx), f(cy - r * 0.92)), "none"),
        ell(cx - r * 0.3, cy - r * 0.34, r * 0.3, r * 0.2, "#FFF", op="0.2"),
    ]
    return d, b


def ring(gid, cx, cy, r, light, mid, dark, bore):
    """A bearing race, three-quarter on. Concentric ellipses with a lit upper-left arc."""
    d = [lin(gid, ((0, light, None), (0.45, mid, None), (1, dark, None)), 0, 0, 1, 0.4)]
    b = [
        ell(cx, cy + r * 0.13, r, r * 0.9, dark),
        ell(cx, cy, r, r * 0.9, "url(#%s)" % gid),
        ell(cx, cy, r * 0.78, r * 0.7, mid),
        ell(cx, cy, r * 0.62, r * 0.55, dark),
        ell(cx, cy, r * 0.44, r * 0.39, bore),
        ell(cx - r * 0.34, cy - r * 0.42, r * 0.34, r * 0.13, "#FFF", op="0.34"),
    ]
    for i in range(7):
        a = -2.7 + i * 0.44
        from math import cos, sin
        b.append(ell(cx + cos(a) * r * 0.7, cy + sin(a) * r * 0.62, r * 0.09, r * 0.08,
                     light, op="0.8"))
    return d, b


def bolt(gid, x, y, ln, th, light, mid, dark, head):
    """A hex-head bolt lying down.

    The head was drawn as a five-point path and came out a diamond. It is an explicit hexagon
    now: six points on a circle, flat sides top and bottom, which is how a spanner sees it.
    """
    R = th * 0.95
    hx = x + R
    hy = R * 0.866
    d = [lin(gid, ((0, light, None), (0.4, mid, None), (1, dark, None)), 0, 0, 0, 1)]
    b = [
        rect(hx, y - th / 2, ln, th, "url(#%s)" % gid, rx=th * 0.16),
        path("M%s %s L%s %s L%s %s L%s %s L%s %s L%s %s Z" % (
            f(hx + R), f(y), f(hx + R / 2), f(y + hy), f(hx - R / 2), f(y + hy),
            f(hx - R), f(y), f(hx - R / 2), f(y - hy), f(hx + R / 2), f(y - hy)), head),
        path("M%s %s L%s %s L%s %s L%s %s Z" % (
            f(hx - R), f(y), f(hx - R / 2), f(y - hy), f(hx + R / 2), f(y - hy),
            f(hx + R), f(y)), "#FFF", "0.2"),
        path("M%s %s L%s %s L%s %s L%s %s Z" % (
            f(hx - R), f(y), f(hx - R / 2), f(y + hy), f(hx + R / 2), f(y + hy),
            f(hx + R), f(y)), "#000", "0.16"),
    ]
    n = int(ln * 0.6 / (th * 0.52))
    for i in range(n):
        b.append(rect(hx + ln * 0.36 + i * th * 0.52, y - th / 2, th * 0.17, th, "#000",
                      op="0.2"))
    b.append(rect(hx, y - th / 2, ln, th * 0.26, "#FFF", rx=th * 0.13, op="0.26"))
    return d, b


def breaker(gid, x, y, w, h, light, mid, dark, lever, face):
    """A DIN-rail circuit breaker: body, toggle, a printed rating face."""
    d = [lin(gid, ((0, light, None), (0.5, mid, None), (1, dark, None)), 0, 0, 1, 0.2)]
    b = [
        rect(x, y, w, h, "url(#%s)" % gid, rx=2),
        rect(x, y + h * 0.3, w, 4, "#000", op="0.14"),
        rect(x + w * 0.24, y + h * 0.08, w * 0.52, h * 0.2, lever, rx=2),
        rect(x + w * 0.16, y + h * 0.42, w * 0.68, h * 0.24, face, rx=1.5),
        rect(x + 2, y + 3, 4, h - 6, "url(#gl)", rx=2),
        rect(x + w - 6, y + 3, 4, h - 6, "url(#sd)", rx=2),
    ]
    return d, b
