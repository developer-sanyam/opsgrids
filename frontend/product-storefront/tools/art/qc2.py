"""More QuickCart object recipes. Same light, same shelf, same two soft ramps (`gl`, `sd`)."""

from kit import ell, f, lin, path, rad, rect
from quickcart import BASE


def tin(gid, cx, top, w, h, light, mid, dark, lid, band, ink):
    """A cylinder — ghee, instant coffee. The two ellipses are the whole illusion."""
    hw = w / 2
    d = [lin(gid, ((0, light, None), (0.38, mid, None), (1, dark, None)), 0, 0, 1, 0)]
    b = [
        path("M%s %s V%s A%s 9 0 0 0 %s %s V%s Z" % (
            f(cx - hw), f(top + 10), f(BASE - 6), f(hw), f(cx + hw), f(BASE - 6), f(top + 10)),
             "url(#%s)" % gid),
        rect(cx - hw - 4, top, w + 8, 16, lid, rx=3),
        ell(cx, top, hw + 4, 7, "#FFF", op="0.26"),
        path("M%s %s Q%s %s %s %s H%s Q%s %s %s %s Z" % (
            f(cx - hw + 11), f(top + 24), f(cx - hw + 5), f((top + BASE) / 2),
            f(cx - hw + 11), f(BASE - 16), f(cx - hw + 24),
            f(cx - hw + 18), f((top + BASE) / 2), f(cx - hw + 24), f(top + 24)), "url(#gl)"),
        path("M%s %s Q%s %s %s %s H%s Q%s %s %s %s Z" % (
            f(cx + hw - 10), f(top + 24), f(cx + hw - 4), f((top + BASE) / 2),
            f(cx + hw - 10), f(BASE - 16), f(cx + hw - 21),
            f(cx + hw - 15), f((top + BASE) / 2), f(cx + hw - 21), f(top + 24)), "url(#sd)"),
        rect(cx - hw + 2, BASE - h * 0.5, w - 4, h * 0.32, band, rx=2),
        rect(cx - hw + 12, BASE - h * 0.43, w - 24, 9, ink, rx=4.5),
        rect(cx - hw + 12, BASE - h * 0.31, (w - 24) * 0.55, 7, ink, rx=3.5, op="0.72"),
    ]
    return d, b


def puff(gid, cx, top, w, h, light, mid, dark, seal, band, ink):
    """A crisp packet: air-filled, so the waist is the widest part and the seals are pinched."""
    hw = w / 2
    my = (top + BASE) / 2
    d = [lin(gid, ((0, light, None), (0.4, mid, None), (1, dark, None)), 0, 0, 1, 0.25)]
    b = [
        path("M%s %s C%s %s %s %s %s %s C%s %s %s %s %s %s C%s %s %s %s %s %s "
             "C%s %s %s %s %s %s Z" % (
                 f(cx - w * 0.2), f(top + 8),
                 f(cx - hw), f(top + 24), f(cx - hw - 2), f(my - 10), f(cx - hw - 2), f(my),
                 f(cx - hw - 2), f(my + 18), f(cx - hw + 2), f(BASE - 20),
                 f(cx - w * 0.22), f(BASE - 2),
                 f(cx + w * 0.22), f(BASE - 2), f(cx + hw + 2), f(BASE - 20),
                 f(cx + hw + 2), f(my),
                 f(cx + hw + 2), f(my - 10), f(cx + hw), f(top + 24), f(cx + w * 0.2),
                 f(top + 8)), "url(#%s)" % gid),
        rect(cx - w * 0.22, top, w * 0.44, 12, seal, rx=3),
        rect(cx - w * 0.24, BASE - 7, w * 0.48, 11, seal, rx=3),
        path("M%s %s C%s %s %s %s %s %s H%s C%s %s %s %s %s %s Z" % (
            f(cx - w * 0.3), f(BASE - 26), f(cx - w * 0.36), f(my + 10),
            f(cx - w * 0.34), f(my - 24), f(cx - w * 0.24), f(top + 24),
            f(cx - w * 0.12),
            f(cx - w * 0.22), f(my - 24), f(cx - w * 0.24), f(my + 10),
            f(cx - w * 0.18), f(BASE - 26)), "url(#gl)"),
        path("M%s %s C%s %s %s %s %s %s H%s C%s %s %s %s %s %s Z" % (
            f(cx + w * 0.28), f(BASE - 26), f(cx + w * 0.34), f(my + 10),
            f(cx + w * 0.32), f(my - 24), f(cx + w * 0.22), f(top + 26),
            f(cx + w * 0.12),
            f(cx + w * 0.2), f(my - 24), f(cx + w * 0.22), f(my + 10),
            f(cx + w * 0.16), f(BASE - 26)), "url(#sd)"),
        rect(cx - w * 0.34, my - h * 0.1, w * 0.68, h * 0.3, band, rx=6),
        rect(cx - w * 0.25, my - h * 0.03, w * 0.5, 9, ink, rx=4.5),
        rect(cx - w * 0.25, my + h * 0.09, w * 0.3, 7, ink, rx=3.5, op="0.72"),
    ]
    return d, b


def gable(gid, cx, top, w, h, light, mid, dark, roof, band, ink):
    """A gable-top carton — juice, long-life milk. The folded ridge is the giveaway."""
    hw = w / 2
    ry = top + h * 0.3
    d = [lin(gid, ((0, light, None), (0.45, mid, None), (1, dark, None)), 0, 0, 1, 0.2)]
    b = [
        rect(cx - hw, ry, w, BASE - ry, "url(#%s)" % gid),
        path("M%s %s L%s %s H%s L%s %s Z" % (f(cx - hw), f(ry), f(cx - w * 0.14), f(top + 11),
                                             f(cx + w * 0.14), f(cx + hw), f(ry)), roof),
        path("M%s %s L%s %s H%s Z" % (f(cx + w * 0.02), f(ry), f(cx + w * 0.14), f(top + 11),
                                      f(cx + hw)), dark),
        rect(cx - w * 0.15, top, w * 0.3, 13, roof, rx=2),
        rect(cx - w * 0.15, top, w * 0.3, 4, "#FFF", op="0.32"),
        rect(cx - hw + 7, ry + 8, 17, BASE - ry - 16, "url(#gl)"),
        rect(cx + hw - 22, ry + 8, 15, BASE - ry - 16, "url(#sd)"),
        rect(cx - hw + 3, ry + h * 0.22, w - 6, h * 0.3, band, rx=3),
        rect(cx - hw + 14, ry + h * 0.29, w - 28, 9, ink, rx=4.5),
        rect(cx - hw + 14, ry + h * 0.41, (w - 28) * 0.56, 7, ink, rx=3.5, op="0.72"),
    ]
    return d, b


def orb(gid, cx, cy, r, light, mid, dark, stem=None, leaf=None, calyx=None, squash=0.96):
    """A round fruit. The offset radial is what turns a circle into a sphere.

    `calyx` draws a tomato's five-lobed green crown. Without it — with a leaf instead — the same
    shape reads as an apple, which is what the first pass of `p-tomato` looked like.
    """
    d = [rad(gid, ((0, light, None), (0.55, mid, None), (1, dark, None)),
             cx=0.36, cy=0.32, r=0.72)]
    b = [ell(cx, cy, r, r * squash, "url(#%s)" % gid),
         ell(cx - r * 0.32, cy - r * 0.38, r * 0.24, r * 0.17, "#FFF", op="0.45")]
    if calyx:
        ty = cy - r * squash + 3
        for ang in (-1.0, -0.5, 0.0, 0.5, 1.0):
            b.append(path("M%s %s q%s -4 %s -11 q%s 10 %s 13 Z" % (
                f(cx), f(ty), f(ang * 13), f(ang * 26), f(-ang * 9), f(-ang * 26)), calyx))
        b.append(ell(cx, ty, r * 0.16, r * 0.11, calyx))
    if stem:
        b.append(rect(cx - 4, cy - r * squash - 11, 8, 15, stem, rx=3))
    if leaf:
        b.append(path("M%s %s q17 -15 31 -3 q-17 14 -31 3 Z" % (f(cx - 2), f(cy - r - 5)), leaf))
    return d, b


def crescent(gid, cx, cy, w, h, light, mid, dark, tip):
    """A banana. One thick arc, one thin arc for the lit face, two dark tips."""
    d = [lin(gid, ((0, light, None), (0.45, mid, None), (1, dark, None)), 0, 0, 0.4, 1)]
    b = [
        path("M%s %s C%s %s %s %s %s %s C%s %s %s %s %s %s Z" % (
            f(cx - w / 2), f(cy - h * 0.1),
            f(cx - w * 0.3), f(cy + h * 0.66), f(cx + w * 0.3), f(cy + h * 0.66),
            f(cx + w / 2), f(cy - h * 0.1),
            f(cx + w * 0.34), f(cy + h * 0.26), f(cx - w * 0.34), f(cy + h * 0.26),
            f(cx - w / 2), f(cy - h * 0.1)), "url(#%s)" % gid),
        path("M%s %s C%s %s %s %s %s %s C%s %s %s %s %s %s Z" % (
            f(cx - w * 0.36), f(cy + h * 0.04),
            f(cx - w * 0.2), f(cy + h * 0.48), f(cx + w * 0.2), f(cy + h * 0.48),
            f(cx + w * 0.36), f(cy + h * 0.04),
            f(cx + w * 0.24), f(cy + h * 0.3), f(cx - w * 0.24), f(cy + h * 0.3),
            f(cx - w * 0.36), f(cy + h * 0.04)), "url(#gl)"),
        rect(cx - w / 2 - 5, cy - h * 0.2, 13, 15, tip, rx=5),
        rect(cx + w / 2 - 8, cy - h * 0.2, 13, 15, tip, rx=5),
    ]
    return d, b


def loaf(gid, cx, top, w, h, light, mid, dark, crust, cut):
    """A sandwich loaf: a domed crust, a squared body, one cut face showing crumb."""
    hw = w / 2
    d = [lin(gid, ((0, light, None), (0.45, mid, None), (1, dark, None)), 0, 0, 1, 0.3)]
    b = [
        path("M%s %s V%s C%s %s %s %s %s %s C%s %s %s %s %s %s V%s Z" % (
            f(cx - hw), f(BASE), f(top + h * 0.42),
            f(cx - hw), f(top), f(cx - w * 0.1), f(top - 7), f(cx), f(top + 2),
            f(cx + w * 0.16), f(top - 5), f(cx + hw), f(top + 4), f(cx + hw),
            f(top + h * 0.42), f(BASE)), "url(#%s)" % gid),
        path("M%s %s C%s %s %s %s %s %s C%s %s %s %s %s %s Z" % (
            f(cx - hw), f(top + h * 0.44), f(cx - hw), f(top), f(cx - w * 0.1), f(top - 7),
            f(cx), f(top + 2), f(cx + w * 0.16), f(top - 5), f(cx + hw), f(top + 4),
            f(cx + hw), f(top + h * 0.44)), crust),
        rect(cx - hw + 7, top + h * 0.52, w * 0.32, BASE - top - h * 0.54, cut, rx=5, op="0.92"),
        rect(cx - hw + 16, top + h * 0.62, 6, BASE - top - h * 0.76, dark, rx=3, op="0.42"),
        rect(cx - hw + 30, top + h * 0.62, 6, BASE - top - h * 0.76, dark, rx=3, op="0.42"),
        rect(cx + hw - 24, top + h * 0.46, 24, BASE - top - h * 0.46, "url(#sd)"),
    ]
    return d, b


def eggs(gid, x, top, w, h, light, mid, dark, shell, shell_dk):
    """An open egg tray. The eggs stand proud of the back wall — that is the depth cue."""
    d = [lin(gid, ((0, light, None), (0.5, mid, None), (1, dark, None)), 0, 0, 1, 0.3)]
    r = w * 0.115
    b = []
    for cx in (x + w * 0.22, x + w * 0.5, x + w * 0.78):
        b.append(ell(cx, top + r + 4, r, r * 1.16, shell))
        b.append(ell(cx + r * 0.34, top + r + 8, r * 0.48, r * 0.92, shell_dk, op="0.5"))
        b.append(ell(cx - r * 0.32, top + r - 3, r * 0.24, r * 0.32, "#FFF", op="0.55"))
    b.append(path("M%s %s H%s L%s %s H%s Z" % (f(x), f(top + r + 2), f(x + w),
                                               f(x + w - 11), f(BASE), f(x + 11)),
                  "url(#%s)" % gid))
    b.append(path("M%s %s H%s L%s %s H%s Z" % (f(x + w - 36), f(top + r + 2), f(x + w),
                                               f(x + w - 11), f(BASE), f(x + w - 32)),
                  "url(#sd)"))
    return d, b


def flatpack(gid, cx, top, w, h, light, mid, dark, rim, band, ink):
    """A vacuum pack — paneer, cheese. A sealed rim all round, one soft highlight."""
    hw = w / 2
    d = [lin(gid, ((0, light, None), (0.45, mid, None), (1, dark, None)), 0, 0, 1, 0.35)]
    b = [
        rect(cx - hw, top, w, BASE - top, rim, rx=11),
        rect(cx - hw + 9, top + 9, w - 18, BASE - top - 18, "url(#%s)" % gid, rx=6),
        rect(cx - hw + 9, top + 9, 22, BASE - top - 18, "url(#gl)", rx=6),
        rect(cx + hw - 29, top + 9, 20, BASE - top - 18, "url(#sd)", rx=6),
        rect(cx - w * 0.3, top + h * 0.3, w * 0.6, h * 0.34, band, rx=5),
        rect(cx - w * 0.22, top + h * 0.37, w * 0.44, 9, ink, rx=4.5),
        rect(cx - w * 0.22, top + h * 0.5, w * 0.26, 7, ink, rx=3.5, op="0.72"),
    ]
    return d, b


def bar(gid, cx, top, w, h, light, mid, dark, foil, band, ink):
    """A chocolate bar stood on end, the wrapper torn back to show foil and segments."""
    hw = w / 2
    d = [lin(gid, ((0, light, None), (0.42, mid, None), (1, dark, None)), 0, 0, 1, 0.15)]
    b = [
        rect(cx - hw, top, w, BASE - top, foil, rx=4),
    ]
    for i in range(3):
        b.append(rect(cx - hw + 8 + i * (w - 16) / 3, top + 6, 4, h * 0.13, "#000", op="0.14"))
    b += [
        rect(cx - hw, top + h * 0.22, w, BASE - top - h * 0.22, "url(#%s)" % gid, rx=4),
        rect(cx - hw, top + h * 0.22, w, 5, "#FFF", op="0.35"),
        rect(cx - hw + 5, top + h * 0.28, 17, BASE - top - h * 0.32, "url(#gl)", rx=3),
        rect(cx + hw - 21, top + h * 0.28, 16, BASE - top - h * 0.32, "url(#sd)", rx=3),
        rect(cx - w * 0.34, top + h * 0.38, w * 0.68, h * 0.26, band, rx=4),
        rect(cx - w * 0.26, top + h * 0.44, w * 0.52, 9, ink, rx=4.5),
        rect(cx - w * 0.26, top + h * 0.56, w * 0.3, 7, ink, rx=3.5, op="0.72"),
    ]
    return d, b


def cup(gid, cx, top, w, h, light, mid, dark, inner, saucer):
    """A cup on a saucer, with steam. The rim ellipse and the handle carry it at 88px."""
    hw = w / 2
    bw = w * 0.76 / 2
    d = [lin(gid, ((0, light, None), (0.42, mid, None), (1, dark, None)), 0, 0, 1, 0.2)]
    b = []
    for sx, sh in ((-22, 42), (2, 58), (24, 38)):
        b.append(rect(cx + sx - 3, top - 8 - sh, 7, sh, "#FFF", rx=3.5, op="0.44"))
    b += [
        path("M%s %s Q%s %s %s %s H%s Q%s %s %s %s Z" % (
            f(cx + hw + 2), f(top + 12), f(cx + hw + 32), f(top + 28),
            f(cx + hw + 13), f(top + h * 0.62), f(cx + hw + 2),
            f(cx + hw + 21), f(top + h * 0.48), f(cx + hw - 4), f(top + 24)), mid),
        path("M%s %s L%s %s Q%s %s %s %s L%s %s Z" % (
            f(cx - hw), f(top + 6), f(cx - bw), f(BASE - 15),
            f(cx), f(BASE - 6), f(cx + bw), f(BASE - 15), f(cx + hw), f(top + 6)),
             "url(#%s)" % gid),
        ell(cx, top + 6, hw, 10, inner),
        ell(cx, top + 8, hw - 7, 7, dark, op="0.35"),
        path("M%s %s L%s %s H%s L%s %s Z" % (f(cx - w * 0.3), f(top + 18), f(cx - w * 0.26),
                                             f(BASE - 22), f(cx - w * 0.14), f(cx - w * 0.18),
                                             f(top + 18)), "url(#gl)"),
        ell(cx, BASE - 3, hw + 32, 12, saucer),
        ell(cx, BASE - 7, hw + 32, 11, light),
    ]
    return d, b
