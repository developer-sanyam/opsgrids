"""Atelier's art world: a dark showroom, deliberately the opposite of QuickCart.

QuickCart is a lit white aisle with a marigold shelf and small packaged goods. Atelier is a *room*
— near-black warm walls taken straight off the theme's own `colorBg` (#17110C) and `colorBorder`,
one low brass light from the upper left, a long floor plane, and one large piece of furniture
occupying most of the frame with a long soft shadow raking away from the light.

Nothing here is pastel and nothing is centred at 60%: the two sets should not be mistakable for
each other in a gallery of eight cards.
"""

from kit import backdrop, cast, ell, f, lin, path, rad, rect, svg

BRASS = "#C9A227"          # the theme's own accent, used for light and hardware
BRASS_FOOT = "#8A6A26"     # the same brass in shadow, for feet: at full saturation a foot
                           # read as a bright yellow wedge and pulled the eye off the piece
BRASS_LT = "#F0D98A"
FLOOR = 236          # the horizon where wall meets floor
BASE = 268           # where furniture meets the floor (it stands in front of the wall)

# wall top, wall bottom, floor near, floor far
ROOM = {
    "seating":  ("#2A2018", "#140F0A", "#3A2C1F", "#221A12"),
    "dining":   ("#241C16", "#100C08", "#342A1E", "#1E1710"),
    "beds":     ("#2C241C", "#16100B", "#3E3226", "#241C14"),
    "lighting": ("#1E1710", "#0B0805", "#2E241A", "#18120C"),
}


def scene(room, glow=(0.34, 0.3), glow_r=0.62):
    """The room every Atelier frame shares: warm dark wall, a brass pool of light, a floor."""
    wt, wb, fn, ff = ROOM[room]
    d, b = backdrop("bg", wt, wb)
    d.append(rad("gw", ((0, BRASS, "0.3"), (0.55, BRASS, "0.09"), (1, BRASS, "0")),
                 cx=glow[0], cy=glow[1], r=glow_r))
    b.append(rect(0, 0, 400, 300, "url(#gw)"))
    d.append(lin("fl", ((0, ff, None), (1, fn, None)), 0, 0, 0, 1))
    b.append(rect(0, FLOOR, 400, 300 - FLOOR, "url(#fl)"))
    # the skirting line: a single brass hairline is the whole room's craft cue
    b.append(rect(0, FLOOR - 2, 400, 2, BRASS, op="0.34"))
    d.append(lin("gl", ((0, "#FFE9B8", "0.3"), (1, "#FFE9B8", "0")), 0, 0, 1, 0))
    d.append(lin("sd", ((0, "#000000", "0.42"), (1, "#000000", "0")), 1, 0, 0, 0))
    return d, b


def floorshadow(gid, cx, cy=None, rx=150, ry=16, op=0.55):
    """The long shadow raking away from the light. Darker and wider than QuickCart's."""
    return cast(gid, cx, BASE - 2 if cy is None else cy, rx, ry, "#000", op)


def wood(gid, light, mid, dark, angle="lr"):
    coords = {"lr": (0, 0, 1, 0.3), "tb": (0, 0, 0, 1)}[angle]
    return lin(gid, ((0, light, None), (0.45, mid, None), (1, dark, None)), *coords)


def leg(x, y, w, h, colour, taper=0.55):
    """A tapered foot. Straight ones read as a diagram; tapered ones read as made."""
    return path("M%s %s H%s L%s %s H%s Z" % (f(x), f(y), f(x + w), f(x + w - w * (1 - taper) / 2),
                                             f(y + h), f(x + w * (1 - taper) / 2)), colour)


# ------------------------------------------------------------------- pieces

def sofa(gid, cx, top, w, light, mid, dark, cushion, frame, brass=BRASS_FOOT):
    """A three-seater, seen square on. The back sits behind the arms; the arms catch the light."""
    hw = w / 2
    seat = top + (BASE - top) * 0.46
    d = [wood(gid, light, mid, dark)]
    b = [
        # back
        rect(cx - hw + 26, top, w - 52, seat - top + 26, "url(#%s)" % gid, rx=14),
        rect(cx - hw + 38, top + 10, w * 0.28, seat - top - 4, "url(#gl)", rx=10),
        # arms, nearer the eye and so a step lighter on the left, darker on the right
        # two back cushions, deep and soft — bigger than the seat pads, or the whole sofa reads
        # as a grid of equal tiles rather than as upholstery
        rect(cx - hw + 42, top + 12, (w - 96) / 2, seat - top + 4, cushion, rx=16),
        rect(cx + 6, top + 12, (w - 96) / 2, seat - top + 4, cushion, rx=16),
        rect(cx - hw + 52, top + 20, (w - 130) / 2, 10, "#FFF", rx=5, op="0.1"),
        # arms, in front of the back and squarer than a lozenge
        rect(cx - hw, seat - 46, 44, BASE - seat + 28, mid, rx=11),
        rect(cx + hw - 44, seat - 46, 44, BASE - seat + 28, dark, rx=11),
        rect(cx - hw + 5, seat - 38, 15, BASE - seat + 8, "url(#gl)", rx=7),
        # one seat, seamed into three pads
        rect(cx - hw + 36, seat + 6, w - 72, 42, cushion, rx=12),
        rect(cx - hw + 36, seat + 6, w - 72, 9, "#FFF", rx=4.5, op="0.13"),
        rect(cx - hw + 36 + (w - 72) / 3, seat + 10, 3, 34, "#000", rx=1.5, op="0.28"),
        rect(cx - hw + 36 + 2 * (w - 72) / 3, seat + 10, 3, 34, "#000", rx=1.5, op="0.28"),
        rect(cx + hw - 44, seat - 40, 30, BASE - seat + 20, "url(#sd)", rx=10),
        rect(cx - hw + 22, BASE - 14, w - 44, 14, frame, rx=4),
    ]
    for lx in (cx - hw + 16, cx + hw - 27):
        b.append(leg(lx, BASE - 3, 11, 15, brass))
    return d, b


def chair(gid, cx, top, w, light, mid, dark, cushion, frame, brass=BRASS_FOOT):
    """A single armchair, three-quarter on, so one arm reads in front of the other."""
    hw = w / 2
    seat = top + (BASE - top) * 0.52
    d = [wood(gid, light, mid, dark)]
    b = [
        rect(cx - hw + 20, top, w - 40, seat - top + 24, "url(#%s)" % gid, rx=w * 0.28),
        rect(cx - hw + 30, top + 12, w * 0.22, seat - top, "url(#gl)", rx=12),
        rect(cx - hw, seat - 26, 38, BASE - seat + 10, mid, rx=14),
        rect(cx + hw - 38, seat - 26, 38, BASE - seat + 10, dark, rx=14),
        rect(cx - hw + 4, seat - 18, 13, BASE - seat - 6, "url(#gl)", rx=6),
        rect(cx - hw + 32, seat, w - 64, 40, cushion, rx=10),
        rect(cx - hw + 32, seat, w - 64, 7, "#FFF", rx=3.5, op="0.14"),
        rect(cx - hw + 36, top + 16, w - 72, seat - top - 26, cushion, rx=10, op="0.85"),
        rect(cx + hw - 32, seat - 20, 24, BASE - seat, "url(#sd)", rx=9),
        rect(cx - hw + 20, BASE - 10, w - 40, 10, frame, rx=3),
    ]
    for lx in (cx - hw + 13, cx + hw - 24):
        b.append(leg(lx, BASE - 3, 11, 16, brass))
    return d, b


def table(gid, cx, top, w, light, mid, dark, edge, brass=BRASS, round_top=False, legs=2):
    """A table. The top is a shallow ellipse or slab seen slightly from above; the edge sells it."""
    hw = w / 2
    d = [wood(gid, light, mid, dark)]
    b = []
    if round_top:
        b += [
            ell(cx, top + 16, hw, 17, "url(#%s)" % gid),
            path("M%s %s A%s 17 0 0 0 %s %s V%s A%s 17 0 0 1 %s %s Z" % (
                f(cx - hw), f(top + 16), f(hw), f(cx + hw), f(top + 16), f(top + 27),
                f(hw), f(cx - hw), f(top + 27)), edge),
            ell(cx - hw * 0.34, top + 10, hw * 0.4, 7, "#FFF", op="0.12"),
        ]
        ty = top + 27
    else:
        b += [
            path("M%s %s L%s %s H%s L%s %s Z" % (f(cx - hw), f(top + 16), f(cx - hw + 26),
                                                 f(top), f(cx + hw), f(cx + hw - 26),
                                                 f(top + 16)), light),
            rect(cx - hw, top + 16, w, 14, "url(#%s)" % gid),
            rect(cx - hw, top + 16, w, 4, "#FFF", op="0.14"),
            rect(cx - hw, top + 26, w, 4, edge),
        ]
        ty = top + 30
    if legs == 2:
        for lx in (cx - hw + 22, cx + hw - 44):
            b.append(path("M%s %s H%s L%s %s H%s Z" % (f(lx), f(ty), f(lx + 22), f(lx + 17),
                                                       f(BASE), f(lx + 5)), mid))
        b.append(rect(cx - hw + 34, BASE - 36, w - 90, 8, dark, rx=3))
    else:
        for lx, col in ((cx - hw + 14, light), (cx + hw - 32, dark)):
            b.append(path("M%s %s H%s L%s %s H%s Z" % (f(lx), f(ty), f(lx + 18), f(lx + 14),
                                                       f(BASE), f(lx + 4)), col))
    b.append(rect(cx - hw, ty - 4, w, 5, brass, op="0.5"))
    return d, b


def carcase(gid, cx, top, w, light, mid, dark, front, brass=BRASS, doors=2, shelves=0):
    """A wardrobe or bookshelf: one box, one receding side, and the joinery that dates it."""
    hw = w / 2
    dep = 26
    d = [wood(gid, light, mid, dark)]
    b = [
        path("M%s %s l%s -%s h%s l-%s %s Z" % (f(cx - hw), f(top), f(dep), f(dep * 0.5), f(w),
                                               f(dep), f(dep * 0.5)), light),
        path("M%s %s l%s -%s v%s l-%s %s Z" % (f(cx + hw), f(top), f(dep), f(dep * 0.5),
                                               f(BASE - top - 14), f(dep), f(dep * 0.5)), dark),
        rect(cx - hw, top, w, BASE - top, "url(#%s)" % gid),
    ]
    if shelves:
        b.append(rect(cx - hw + 10, top + 10, w - 20, BASE - top - 26, front, rx=2))
        gap = (BASE - top - 36) / shelves
        for i in range(shelves):
            y = top + 16 + i * gap
            b.append(rect(cx - hw + 10, y + gap - 8, w - 20, 7, mid))
            for j in range(4):
                bw = (w - 34) / 4
                bx = cx - hw + 17 + j * bw
                bh = gap - 20 - (j % 3) * 5
                b.append(rect(bx, y + gap - 8 - bh, bw - 5, bh, dark if j % 2 else light,
                              rx=1, op="0.85"))
    else:
        for i in range(doors):
            dw = (w - 14) / doors
            b.append(rect(cx - hw + 7 + i * dw, top + 9, dw - 7, BASE - top - 24, front, rx=2))
        for i in range(doors):
            dw = (w - 14) / doors
            hx = cx - hw + 7 + i * dw + (dw - 20 if i == 0 else 8)
            b.append(rect(hx, top + (BASE - top) * 0.42, 7, 30, brass, rx=3.5))
    b += [
        rect(cx - hw + 6, top + 8, 20, BASE - top - 22, "url(#gl)"),
        rect(cx + hw - 24, top + 8, 20, BASE - top - 22, "url(#sd)"),
    ]
    for lx in (cx - hw + 9, cx + hw - 23):
        b.append(leg(lx, BASE - 12, 14, 13, BRASS_FOOT))
    return d, b


def bed(gid, cx, top, w, light, mid, dark, linen, linen_dk, brass=BRASS):
    """A bed seen from the foot: headboard tall behind, mattress and two pillows in front."""
    hw = w / 2
    mt = top + (BASE - top) * 0.52
    d = [wood(gid, light, mid, dark)]
    b = [
        rect(cx - hw + 20, top, w - 40, mt - top + 20, "url(#%s)" % gid, rx=10),
        rect(cx - hw + 32, top + 10, w * 0.26, mt - top, "url(#gl)", rx=8),
        rect(cx - hw + 34, top + 16, w - 68, mt - top - 26, mid, rx=6, op="0.6"),
        # pillows, sitting proud of the headboard
        rect(cx - hw + 46, mt - 40, (w - 110) / 2, 36, linen, rx=12),
        rect(cx + 8, mt - 40, (w - 110) / 2, 36, linen_dk, rx=12),
        rect(cx - hw + 52, mt - 36, (w - 130) / 2, 9, "#FFF", rx=4.5, op="0.2"),
        # mattress and turned-down sheet
        rect(cx - hw, mt, w, 34, linen, rx=8),
        rect(cx - hw, mt, w, 11, "#FFF", rx=5, op="0.16"),
        rect(cx - hw, mt + 30, w, 22, linen_dk, rx=6),
        rect(cx - hw, mt + 48, w, BASE - mt - 48, dark, rx=4),
        rect(cx + hw - 30, mt, 30, BASE - mt, "url(#sd)", rx=6),
    ]
    for lx in (cx - hw + 8, cx + hw - 22):
        b.append(leg(lx, BASE - 3, 13, 16, BRASS_FOOT))
    return d, b


def pendant(gid, cx, top, w, shade_lt, shade_dk, inner, drop=True):
    """A pendant lamp, and the only object in the set that is its own light source."""
    hw = w / 2
    sy = top + 86
    d = [lin(gid, ((0, shade_lt, None), (0.5, shade_lt, None), (1, shade_dk, None)), 0, 0, 1, 0),
         rad("lp", ((0, "#FFE9B8", "0.55"), (0.5, "#FFD98A", "0.14"), (1, "#FFD98A", "0")),
             cx=0.5, cy=0.5, r=0.5)]
    b = []
    if drop:
        b.append(rect(cx - 2, 0, 4, 90, shade_dk))
    b += [
        # the pool of light the shade throws, drawn under it
        ell(cx, sy + 84, hw + 58, 64, "url(#lp)"),
        path("M%s %s L%s %s H%s L%s %s Z" % (
            f(cx - hw), f(sy), f(cx - w * 0.13), f(top + 6), f(cx + w * 0.13),
            f(cx + hw), f(sy)), "url(#%s)" % gid),
        path("M%s %s L%s %s H%s L%s %s Z" % (
            f(cx - hw), f(sy), f(cx - w * 0.13), f(top + 6), f(cx - w * 0.02),
            f(cx - hw * 0.34), f(sy)), "#FFF", "0.14"),
        ell(cx, sy, hw, 11, inner),
        ell(cx, sy + 2, hw * 0.55, 6, "#FFE9B8"),
        ell(cx, sy + 26, 15, 15, "#FFF3D2"),
    ]
    return d, b


def floorlamp(gid, cx, top, w, shade_lt, shade_dk, inner, stem, brass=BRASS):
    """A floor lamp: a tall stem, a drum shade lit from within, a weighted base."""
    hw = w / 2
    sy = top + 64
    d = [lin(gid, ((0, shade_lt, None), (0.55, shade_lt, None), (1, shade_dk, None)), 0, 0, 1, 0),
         rad("lp", ((0, "#FFE9B8", "0.5"), (0.5, "#FFD98A", "0.12"), (1, "#FFD98A", "0")),
             cx=0.5, cy=0.5, r=0.5)]
    b = [
        ell(cx, sy + 58, hw + 64, 74, "url(#lp)"),
        rect(cx - 4, top + 40, 8, BASE - top - 44, stem),
        rect(cx - 4, top + 40, 3, BASE - top - 44, brass, op="0.45"),
        path("M%s %s L%s %s H%s L%s %s Z" % (
            f(cx - hw), f(sy), f(cx - hw * 0.72), f(top), f(cx + hw * 0.72),
            f(cx + hw), f(sy)), "url(#%s)" % gid),
        path("M%s %s L%s %s H%s L%s %s Z" % (
            f(cx - hw), f(sy), f(cx - hw * 0.72), f(top), f(cx - hw * 0.16),
            f(cx - hw * 0.3), f(sy)), "#FFF", "0.12"),
        ell(cx, sy, hw, 9, inner),
        ell(cx, top, hw * 0.72, 7, "#FFE9B8", op="0.6"),
        ell(cx, BASE - 2, 44, 10, stem),
        ell(cx, BASE - 5, 44, 9, brass),
    ]
    return d, b
