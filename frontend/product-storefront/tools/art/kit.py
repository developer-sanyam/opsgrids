"""Shared SVG craft kit. Emits compact, dependency-free 400x300 scenes."""

W, H = 400, 300


def f(v):
    """Compact number: drop a trailing .0 so the files stay small."""
    if isinstance(v, float):
        s = ("%.1f" % v).rstrip("0").rstrip(".")
        return s if s else "0"
    return str(v)


def stops(*pairs):
    return "".join(
        '<stop offset="%s" stop-color="%s"%s/>'
        % (o, c, "" if a is None else ' stop-opacity="%s"' % a)
        for o, c, a in pairs
    )


def lin(gid, pairs, x1=0, y1=0, x2=0, y2=1):
    return '<linearGradient id="%s" x1="%s" y1="%s" x2="%s" y2="%s">%s</linearGradient>' % (
        gid, f(x1), f(y1), f(x2), f(y2), stops(*pairs))


def rad(gid, pairs, cx=0.5, cy=0.5, r=0.5):
    return '<radialGradient id="%s" cx="%s" cy="%s" r="%s">%s</radialGradient>' % (
        gid, f(cx), f(cy), f(r), stops(*pairs))


def svg(label, defs, body):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300" role="img" aria-label="%s">'
        "<defs>%s</defs>%s</svg>\n" % (label, "".join(defs), "".join(body))
    )


# ---------------------------------------------------------------- scene parts

def backdrop(gid, top, bottom, glow=None, glow_at=(0.36, 0.30), glow_r=0.62):
    """Graded ground for the whole frame, plus an optional pool of light."""
    d = [lin(gid, ((0, top, None), (1, bottom, None)))]
    b = ['<rect width="400" height="300" fill="url(#%s)"/>' % gid]
    if glow:
        d.append(rad(gid + "l", ((0, glow, "0.9"), (1, glow, "0")),
                     cx=glow_at[0], cy=glow_at[1], r=glow_r))
        b.append('<rect width="400" height="300" fill="url(#%s)"/>' % (gid + "l"))
    return d, b


def surface(gid, y, near, far):
    """A horizontal plane the subject stands on; it recedes towards the horizon."""
    d = [lin(gid, ((0, far, None), (1, near, None)))]
    b = ['<rect y="%s" width="400" height="%s" fill="url(#%s)"/>' % (f(y), f(H - y), gid)]
    return d, b


def cast(gid, cx, cy, rx, ry, colour="#000", op=0.30):
    """A soft contact shadow. A radial fade, not a filter: cheap, and crisp at any scale."""
    d = [rad(gid, ((0, colour, f(op)), (0.55, colour, f(round(op * 0.45, 3))), (1, colour, "0")))]
    b = ['<ellipse cx="%s" cy="%s" rx="%s" ry="%s" fill="url(#%s)"/>' % (
        f(cx), f(cy), f(rx), f(ry), gid)]
    return d, b


def body_grad(gid, light, mid, dark, angle="lr"):
    """Form shading: light catches the near-left, the far side falls away."""
    coords = {"lr": (0, 0, 1, 0.35), "tb": (0, 0, 0, 1), "diag": (0, 0, 0.9, 1),
              "rl": (1, 0, 0, 0.35)}[angle]
    return lin(gid, ((0, light, None), (0.45, mid, None), (1, dark, None)), *coords)


def gloss(d_attr, op="0.34"):
    """A specular strip. Always white, always low: it is light, not paint."""
    return '<path d="%s" fill="#FFF" opacity="%s"/>' % (d_attr, op)


def shade(d_attr, op="0.2"):
    return '<path d="%s" fill="#000" opacity="%s"/>' % (d_attr, op)


def rect(x, y, w, h, fill, rx=None, op=None):
    a = '<rect x="%s" y="%s" width="%s" height="%s"' % (f(x), f(y), f(w), f(h))
    if rx is not None:
        a += ' rx="%s"' % f(rx)
    a += ' fill="%s"' % fill
    if op is not None:
        a += ' opacity="%s"' % op
    return a + "/>"


def path(d_attr, fill, op=None):
    a = '<path d="%s" fill="%s"' % (d_attr, fill)
    if op is not None:
        a += ' opacity="%s"' % op
    return a + "/>"


def ell(cx, cy, rx, ry, fill, op=None):
    a = '<ellipse cx="%s" cy="%s" rx="%s" ry="%s" fill="%s"' % (
        f(cx), f(cy), f(rx), f(ry), fill)
    if op is not None:
        a += ' opacity="%s"' % op
    return a + "/>"
