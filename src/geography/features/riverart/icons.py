import numpy as np
from matplotlib.patches import Polygon as _Polygon

INK = "#5f4a35"
OUTLINE = [(0.00, 0.00), (0.14, 0.36), (0.24, 0.30), (0.34, 0.62), (0.46, 0.96), (0.55, 0.70), (0.62, 0.78), (0.74, 0.42), (0.84, 0.50), (1.00, 0.00)]
ICE_FACE, ICE_SHADE, ICE_INK = (0.93, 0.96, 0.98), (0.76, 0.86, 0.93), "#3f6280"
GREEN, GREEN_DARK, BARK = "#6f9a4f", "#3f5f30", (0.55, 0.40, 0.27)
WALL, ROOF, STONE = (0.94, 0.89, 0.78), "#b5654a", (0.80, 0.77, 0.70)


def _line(ax, pts, **kw):
    ax.plot([p[0] for p in pts], [p[1] for p in pts], solid_capstyle="round", solid_joinstyle="round", **kw)


def _foam(ax, pt, centres, zorder, face="white", edge="#9ccbe8"):
    for mx, my, r in centres:
        ax.add_patch(_Polygon([pt((mx + r * np.cos(a_), my + r * 0.6 * np.sin(a_))) for a_ in np.linspace(0, 2 * np.pi, 28)], closed=True, facecolor=face, edgecolor=edge, linewidth=0.45, zorder=zorder))


def _ellipse(ax, pt, cx, cy, rx, ry, zorder, **kw):
    ax.add_patch(_Polygon([pt((cx + rx * np.cos(a_), cy + ry * np.sin(a_))) for a_ in np.linspace(0, 2 * np.pi, 32)], closed=True, zorder=zorder, **kw))


def _jet(ax, pt, points, zorder, width=2.0):
    _line(ax, [pt(p) for p in points], color="#2f6a99", linewidth=width + 0.9, zorder=zorder)
    _line(ax, [pt(p) for p in points], color="#cfe8f6", linewidth=width * 0.45, zorder=zorder + 0.001)


def _rock(ax, pt, outline, zorder, face=(0.88, 0.80, 0.64), shade=(0.72, 0.60, 0.44)):
    ax.add_patch(_Polygon([pt(p) for p in outline], closed=True, facecolor=face, edgecolor=INK, linewidth=0.8, joinstyle="round", zorder=zorder))
    mid = max(p[0] for p in outline) * 0.62 + min(p[0] for p in outline) * 0.38
    ax.add_patch(_Polygon([pt(p) for p in outline if p[0] >= mid] + [pt((mid, min(p[1] for p in outline)))], closed=True, facecolor=shade, edgecolor="none", zorder=zorder + 0.0004))
    ax.add_patch(_Polygon([pt(p) for p in outline], closed=True, facecolor="none", edgecolor=INK, linewidth=0.8, joinstyle="round", zorder=zorder + 0.0012))


def _box(ax, pt, x0, y0, x1, y1, face, zorder, edge=INK, width=0.7):
    ax.add_patch(_Polygon([pt((x0, y0)), pt((x1, y0)), pt((x1, y1)), pt((x0, y1))], closed=True, facecolor=face, edgecolor=edge, linewidth=width, joinstyle="round", zorder=zorder))


def _house(ax, pt, x0, x1, base, height, roof_rise, zorder, roof=ROOF, wall=WALL):
    _box(ax, pt, x0, base, x1, base + height, wall, zorder)
    ax.add_patch(_Polygon([pt((x0 - 0.02, base + height)), pt(((x0 + x1) / 2, base + height + roof_rise)), pt((x1 + 0.02, base + height))], closed=True, facecolor=roof, edgecolor=INK, linewidth=0.7, joinstyle="round", zorder=zorder + 0.001))
    _box(ax, pt, (x0 + x1) / 2 - 0.02, base, (x0 + x1) / 2 + 0.02, base + height * 0.45, "#6b5a48", zorder + 0.002, width=0.4)


def _blade(ax, pt, x0, y0, x1, y1, zorder, colour=GREEN_DARK, width=0.9):
    _line(ax, [pt((x0, y0)), pt(((x0 + x1) / 2 + (x1 - x0) * 0.2, (y0 + y1) / 2)), pt((x1, y1))], color=colour, linewidth=width, zorder=zorder)


def ink_mountain(ax, x, y, size, tone=1.0, zorder=12):
    def pt(p):
        return (x + (p[0] - 0.5) * size, y + p[1] * size)
    ax.add_patch(_Polygon([pt(p) for p in OUTLINE], closed=True, facecolor=(0.88, 0.80, 0.64), edgecolor=INK, linewidth=0.9 * tone, joinstyle="round", zorder=zorder))
    shade = [(0.46, 0.96), (0.55, 0.70), (0.62, 0.78), (0.74, 0.42), (0.84, 0.50), (1.00, 0.0), (0.50, 0.0), (0.47, 0.52)]
    ax.add_patch(_Polygon([pt(p) for p in shade], closed=True, facecolor=(0.72, 0.60, 0.44), edgecolor="none", zorder=zorder + 0.0004))
    for k in range(7):
        t = 0.10 + k * 0.12
        ax.plot([pt((0.50 + t * 0.45, 0.02))[0], pt((0.47 + t * 0.36, 0.55 - t * 0.35))[0]], [pt((0.5, 0.02))[1], pt((0.5, 0.55 - t * 0.35))[1]], color=INK, linewidth=0.55 * tone, zorder=zorder + 0.0008, solid_capstyle="round")
    ax.add_patch(_Polygon([pt(p) for p in OUTLINE], closed=True, facecolor="none", edgecolor=INK, linewidth=0.9 * tone, joinstyle="round", zorder=zorder + 0.0012))


def ink_glacier(ax, x, y, size, tongue=True, zorder=5):
    def pt(p): return (x + (p[0] - 0.5) * size, y + p[1] * size)
    ax.add_patch(_Polygon([pt(p) for p in OUTLINE], closed=True, facecolor=ICE_FACE, edgecolor=ICE_INK, linewidth=0.9, joinstyle="round", zorder=zorder))
    shade = [(0.46, 0.96), (0.55, 0.70), (0.62, 0.78), (0.74, 0.42), (0.84, 0.50), (1.00, 0.0), (0.50, 0.0), (0.47, 0.52)]
    ax.add_patch(_Polygon([pt(p) for p in shade], closed=True, facecolor=ICE_SHADE, edgecolor="none", zorder=zorder + 0.0004))
    for k in range(7):
        t = 0.10 + k * 0.12
        ax.plot([pt((0.50 + t * 0.45, 0.02))[0], pt((0.47 + t * 0.36, 0.55 - t * 0.35))[0]], [pt((0.5, 0.02))[1], pt((0.5, 0.55 - t * 0.35))[1]], color=ICE_INK, linewidth=0.5, zorder=zorder + 0.0008, solid_capstyle="round")
    if tongue:
        flow = [(0.30, 0.02), (0.70, 0.02), (0.74, -0.14), (0.66, -0.30), (0.70, -0.46), (0.54, -0.58), (0.40, -0.50), (0.34, -0.34), (0.38, -0.18)]
        ax.add_patch(_Polygon([pt(p) for p in flow], closed=True, facecolor="#e3f1fa", edgecolor=ICE_INK, linewidth=0.9, joinstyle="round", zorder=zorder + 0.002))
        for cy, spread in ((-0.12, 0.13), (-0.26, 0.11), (-0.40, 0.09)):
            _line(ax, [pt((0.52 - spread, cy + 0.03)), pt((0.52, cy - 0.02)), pt((0.52 + spread, cy + 0.03))], color="#7fb4dc", linewidth=0.6, zorder=zorder + 0.003)
    ax.add_patch(_Polygon([pt(p) for p in OUTLINE], closed=True, facecolor="none", edgecolor=ICE_INK, linewidth=0.9, joinstyle="round", zorder=zorder + 0.0012))


def waterfall(ax, x, y, size, zorder=13):
    def pt(p): return (x + (p[0] - 0.5) * size, y + p[1] * size)
    cliff = [(0.10, 0.62), (0.90, 0.62), (0.92, 0.28), (0.82, 0.22), (0.64, 0.26), (0.50, 0.20), (0.36, 0.26), (0.18, 0.22), (0.08, 0.28)]
    ax.add_patch(_Polygon([pt(p) for p in cliff], closed=True, facecolor=(0.78, 0.66, 0.48), edgecolor=INK, linewidth=0.9, joinstyle="round", zorder=zorder))
    for fx in (0.14, 0.22, 0.78, 0.86):
        _line(ax, [pt((fx, 0.58)), pt((fx - 0.015, 0.32))], color=INK, linewidth=0.5, zorder=zorder + 0.02)
    sheet = [(0.32, 0.62), (0.68, 0.62), (0.70, 0.30), (0.62, 0.20), (0.50, 0.24), (0.38, 0.20), (0.30, 0.30)]
    ax.add_patch(_Polygon([pt(p) for p in sheet], closed=True, facecolor="#e8f4fb", edgecolor="#2f6a99", linewidth=1.0, joinstyle="round", zorder=zorder + 0.1))
    for fx in (0.40, 0.50, 0.60):
        _line(ax, [pt((fx, 0.58)), pt((fx, 0.28))], color="#7fb4dc", linewidth=0.7, zorder=zorder + 0.15)
    _foam(ax, pt, [(0.34, 0.08, 0.11), (0.50, 0.05, 0.15), (0.66, 0.08, 0.11)], zorder + 0.2)


def spring(ax, x, y, size, zorder=5):
    def pt(p): return (x + (p[0] - 0.5) * size, y + p[1] * size)
    _rock(ax, pt, ((0.06, 0.12), (0.04, 0.30), (0.14, 0.44), (0.28, 0.46), (0.40, 0.34), (0.38, 0.14)), zorder)
    _rock(ax, pt, ((0.26, 0.14), (0.28, 0.40), (0.40, 0.56), (0.58, 0.60), (0.72, 0.46), (0.74, 0.14)), zorder + 0.01)
    _rock(ax, pt, ((0.62, 0.12), (0.66, 0.32), (0.78, 0.42), (0.92, 0.38), (0.97, 0.22), (0.92, 0.12)), zorder + 0.02)
    ax.add_patch(_Polygon([pt(p) for p in ((0.44, 0.14), (0.47, 0.24), (0.53, 0.24), (0.56, 0.14))], closed=True, facecolor=INK, edgecolor=INK, linewidth=0.5, zorder=zorder + 0.03))
    left = [(0.48 + 0.10 * t, 0.22 - 0.16 * t - 0.03 * np.sin(t * np.pi)) for t in np.linspace(0, 1, 10)]
    right = [(0.52 + 0.20 * t, 0.22 - 0.16 * t - 0.03 * np.sin(t * np.pi)) for t in np.linspace(0, 1, 10)]
    ax.add_patch(_Polygon([pt(p) for p in left + right[::-1]], closed=True, facecolor="#8cc3e8", edgecolor="#2f6a99", linewidth=0.8, joinstyle="round", zorder=zorder + 0.04))
    _line(ax, [pt((0.50, 0.21)), pt((0.58, 0.12)), pt((0.62, 0.07))], color="#e8f4fb", linewidth=0.7, zorder=zorder + 0.05)
    _ellipse(ax, pt, 0.62, 0.05, 0.28, 0.065, zorder + 0.06, facecolor="#8cc3e8", edgecolor="#2f6a99", linewidth=0.9)
    _foam(ax, pt, [(0.56, 0.07, 0.04), (0.68, 0.05, 0.035)], zorder + 0.07)
    for gx, gh in ((0.02, 0.14), (0.10, 0.18), (0.26, 0.10), (0.84, 0.12), (0.94, 0.16)):
        for lean, height in ((-0.04, gh), (0.0, gh * 1.2), (0.04, gh * 0.9)):
            _line(ax, [pt((gx, 0.03)), pt((gx + lean, 0.03 + height))], color="#5aa83a", linewidth=1.2, zorder=zorder + 0.08)


def wet_swamp(ax, x, y, size, zorder=5):
    def pt(p): return (x + (p[0] - 0.5) * size, y + p[1] * size)
    _ellipse(ax, pt, 0.5, 0.10, 0.48, 0.10, zorder, facecolor="#7fb0b0", edgecolor="#2f6a99", linewidth=0.9)
    for ox, trunk_top, radius in ((0.24, 0.50, 0.17), (0.74, 0.46, 0.15), (0.50, 0.62, 0.21)):
        ax.add_patch(_Polygon([pt(p) for p in ((ox - 0.035, 0.10), (ox - 0.018, trunk_top), (ox + 0.018, trunk_top), (ox + 0.035, 0.10))], closed=True, facecolor=BARK, edgecolor=INK, linewidth=0.8, joinstyle="round", zorder=zorder + 0.01 + ox * 0.01))
        _ellipse(ax, pt, ox, trunk_top + radius * 0.85, radius, radius * 0.95, zorder + 0.02 + ox * 0.01, facecolor=GREEN, edgecolor=GREEN_DARK, linewidth=0.9)
        _ellipse(ax, pt, ox - radius * 0.28, trunk_top + radius * 1.1, radius * 0.45, radius * 0.38, zorder + 0.025 + ox * 0.01, facecolor="#8fb86a", edgecolor="none")
    for k in (0.10, 0.50, 0.90):
        _line(ax, [pt((k - 0.07, 0.09)), pt((k, 0.08)), pt((k + 0.07, 0.09))], color="#e8f4fb", linewidth=0.7, zorder=zorder + 0.04)


def wet_marsh(ax, x, y, size, zorder=5):
    def pt(p): return (x + (p[0] - 0.5) * size, y + p[1] * size)
    _ellipse(ax, pt, 0.30, 0.10, 0.28, 0.08, zorder, facecolor="#8cc3e8", edgecolor="#2f6a99", linewidth=0.8)
    _ellipse(ax, pt, 0.74, 0.07, 0.24, 0.07, zorder, facecolor="#8cc3e8", edgecolor="#2f6a99", linewidth=0.8)
    for sx, height, lean in ((0.14, 0.78, -0.03), (0.30, 0.62, 0.02), (0.50, 0.86, 0.0), (0.66, 0.66, -0.02), (0.84, 0.74, 0.03)):
        _blade(ax, pt, sx, 0.10, sx + lean, height, zorder + 0.01, width=1.0)
        _ellipse(ax, pt, sx + lean, height + 0.06, 0.028, 0.10, zorder + 0.02, facecolor=BARK, edgecolor=INK, linewidth=0.6)
    for sx, h in ((0.06, 0.34), (0.22, 0.28), (0.40, 0.36), (0.58, 0.30), (0.78, 0.38), (0.94, 0.30)):
        _blade(ax, pt, sx, 0.08, sx + 0.03, h, zorder + 0.015, colour=GREEN, width=0.8)


def place_village(ax, x, y, size, zorder=5):
    def pt(p): return (x + (p[0] - 0.5) * size, y + p[1] * size)
    _ellipse(ax, pt, 0.5, 0.10, 0.48, 0.07, zorder, facecolor="#cfd9a8", edgecolor="none")
    for ox, oy, w, h in ((0.10, 0.10, 0.26, 0.20), (0.44, 0.14, 0.24, 0.18), (0.70, 0.08, 0.22, 0.18)):
        _house(ax, pt, ox, ox + w, oy, h, 0.14, zorder + 0.01)
    _line(ax, [pt((0.40, 0.10)), pt((0.40, 0.30))], color=BARK, linewidth=1.2, zorder=zorder + 0.005)
    _ellipse(ax, pt, 0.40, 0.38, 0.09, 0.10, zorder + 0.006, facecolor=GREEN, edgecolor=GREEN_DARK, linewidth=0.7)


def place_commune(ax, x, y, size, zorder=5):
    def pt(p): return (x + (p[0] - 0.5) * size, y + p[1] * size)
    _ellipse(ax, pt, 0.5, 0.08, 0.48, 0.06, zorder, facecolor="#cfd9a8", edgecolor="none")
    _house(ax, pt, 0.04, 0.30, 0.08, 0.18, 0.13, zorder + 0.01)
    _house(ax, pt, 0.70, 0.96, 0.08, 0.18, 0.13, zorder + 0.01)
    _box(ax, pt, 0.34, 0.08, 0.66, 0.34, WALL, zorder + 0.02)
    _box(ax, pt, 0.43, 0.34, 0.57, 0.62, WALL, zorder + 0.021)
    ax.add_patch(_Polygon([pt((0.40, 0.62)), pt((0.50, 0.92)), pt((0.60, 0.62))], closed=True, facecolor=ROOF, edgecolor=INK, linewidth=0.7, joinstyle="round", zorder=zorder + 0.022))
    _line(ax, [pt((0.50, 0.92)), pt((0.50, 1.00))], color=INK, linewidth=0.8, zorder=zorder + 0.023)
    _line(ax, [pt((0.47, 0.97)), pt((0.53, 0.97))], color=INK, linewidth=0.8, zorder=zorder + 0.023)
    ax.add_patch(_Polygon([pt((0.34, 0.34)), pt((0.50, 0.46)), pt((0.66, 0.34))], closed=True, facecolor=ROOF, edgecolor=INK, linewidth=0.7, joinstyle="round", zorder=zorder + 0.021))
    _box(ax, pt, 0.47, 0.08, 0.53, 0.20, "#6b5a48", zorder + 0.025, width=0.4)


def place_town(ax, x, y, size, zorder=5):
    def pt(p): return (x + (p[0] - 0.5) * size, y + p[1] * size)
    _ellipse(ax, pt, 0.5, 0.07, 0.48, 0.055, zorder, facecolor="#cfd9a8", edgecolor="none")
    for ox, w, h, rise in ((0.04, 0.22, 0.22, 0.12), (0.28, 0.20, 0.30, 0.12), (0.62, 0.22, 0.26, 0.14), (0.78, 0.20, 0.18, 0.11)):
        _house(ax, pt, ox, ox + w, 0.08, h, rise, zorder + 0.01 + ox * 0.01)
    _box(ax, pt, 0.50, 0.08, 0.60, 0.52, WALL, zorder + 0.03)
    ax.add_patch(_Polygon([pt((0.48, 0.52)), pt((0.55, 0.72)), pt((0.62, 0.52))], closed=True, facecolor=ROOF, edgecolor=INK, linewidth=0.7, joinstyle="round", zorder=zorder + 0.031))
    _ellipse(ax, pt, 0.55, 0.42, 0.028, 0.035, zorder + 0.032, facecolor="white", edgecolor=INK, linewidth=0.4)


def place_city(ax, x, y, size, zorder=5):
    def pt(p): return (x + (p[0] - 0.5) * size, y + p[1] * size)
    _ellipse(ax, pt, 0.5, 0.07, 0.48, 0.05, zorder, facecolor="#c9c5b8", edgecolor="none")
    for k, (ox, w, h) in enumerate(((0.04, 0.16, 0.42), (0.20, 0.14, 0.62), (0.36, 0.18, 0.84), (0.56, 0.14, 0.56), (0.72, 0.16, 0.70), (0.86, 0.12, 0.38))):
        _box(ax, pt, ox, 0.08, ox + w, 0.08 + h, STONE if k % 2 else (0.74, 0.72, 0.68), zorder + 0.01 + k * 0.001)
        for wy in np.arange(0.18, 0.08 + h - 0.06, 0.12):
            for wx in (ox + w * 0.25, ox + w * 0.60):
                _box(ax, pt, wx, wy, wx + 0.025, wy + 0.04, "#f3e9b8", zorder + 0.02 + k * 0.001, edge="none", width=0)
    _line(ax, [pt((0.45, 0.92)), pt((0.45, 1.02))], color=INK, linewidth=0.9, zorder=zorder + 0.03)


def dam(ax, x, y, size, zorder=5):
    def pt(p): return (x + (p[0] - 0.5) * size, y + p[1] * size)
    _rock(ax, pt, ((0.00, 0.06), (0.02, 0.50), (0.08, 0.80), (0.22, 0.92), (0.26, 0.06)), zorder)
    _rock(ax, pt, ((1.00, 0.06), (0.98, 0.50), (0.92, 0.80), (0.78, 0.92), (0.74, 0.06)), zorder)
    ax.add_patch(_Polygon([pt(p) for p in ((0.20, 0.70), (0.80, 0.70), (0.80, 0.84), (0.60, 0.80), (0.50, 0.84), (0.40, 0.80), (0.20, 0.84))], closed=True, facecolor="#8cc3e8", edgecolor="#2f6a99", linewidth=0.8, joinstyle="round", zorder=zorder + 0.005))
    ax.add_patch(_Polygon([pt(p) for p in ((0.20, 0.10), (0.80, 0.10), (0.80, 0.66), (0.20, 0.66))], closed=True, facecolor=STONE, edgecolor=INK, linewidth=0.9, joinstyle="round", zorder=zorder + 0.01))
    ax.add_patch(_Polygon([pt(p) for p in ((0.18, 0.66), (0.82, 0.66), (0.82, 0.72), (0.18, 0.72))], closed=True, facecolor=(0.68, 0.65, 0.58), edgecolor=INK, linewidth=0.8, joinstyle="round", zorder=zorder + 0.011))
    for px_ in np.linspace(0.20, 0.80, 7):
        _box(ax, pt, px_ - 0.015, 0.72, px_ + 0.015, 0.78, (0.62, 0.60, 0.55), zorder + 0.012, width=0.5)
    for hy in (0.24, 0.38, 0.52):
        _line(ax, [pt((0.20, hy)), pt((0.80, hy))], color=INK, linewidth=0.4, zorder=zorder + 0.013)
    for gx in (0.38, 0.50, 0.62):
        _box(ax, pt, gx - 0.045, 0.40, gx + 0.045, 0.64, (0.50, 0.48, 0.44), zorder + 0.014, width=0.6)
        _jet(ax, pt, [(gx, 0.40), (gx, 0.26), (gx + (gx - 0.5) * 0.4, 0.12)], zorder + 0.02, width=1.5)
    _ellipse(ax, pt, 0.50, 0.06, 0.34, 0.07, zorder + 0.021, facecolor="#8cc3e8", edgecolor="#2f6a99", linewidth=0.9)
    _foam(ax, pt, [(0.40, 0.09, 0.05), (0.52, 0.08, 0.06), (0.62, 0.09, 0.05)], zorder + 0.022)


def canal(ax, x, y, size, zorder=5):
    def pt(p): return (x + (p[0] - 0.5) * size, y + p[1] * size)
    ax.add_patch(_Polygon([pt(p) for p in ((0.02, 0.06), (0.98, 0.06), (0.86, 0.70), (0.14, 0.70))], closed=True, facecolor=(0.80, 0.86, 0.62), edgecolor=INK, linewidth=0.9, joinstyle="round", zorder=zorder))
    ax.add_patch(_Polygon([pt(p) for p in ((0.20, 0.06), (0.80, 0.06), (0.64, 0.70), (0.36, 0.70))], closed=True, facecolor="#8cc3e8", edgecolor="#2f6a99", linewidth=0.8, joinstyle="round", zorder=zorder + 0.005))
    for start, end in (((0.20, 0.06), (0.36, 0.70)), ((0.80, 0.06), (0.64, 0.70))):
        _line(ax, [pt(start), pt(end)], color=STONE, linewidth=2.4, zorder=zorder + 0.01)
        _line(ax, [pt(start), pt(end)], color=INK, linewidth=0.6, zorder=zorder + 0.011)
    for side in (0.27, 0.73):
        _line(ax, [pt((side, 0.34)), pt((0.50, 0.42))], color=BARK, linewidth=2.2, zorder=zorder + 0.02)
        _line(ax, [pt((side, 0.34)), pt((0.50, 0.42))], color=INK, linewidth=0.5, zorder=zorder + 0.021)
    for ripple, half in ((0.18, 0.13), (0.58, 0.07)):
        _line(ax, [pt((0.50 - half, ripple)), pt((0.50 + half, ripple))], color="white", linewidth=0.9, zorder=zorder + 0.015)


def _dune_profile(left: float, width: float, height: float, crest: float = 0.62, samples: int = 24) -> list[tuple[float, float]]:
    rise = [(left + crest * width * t, 0.04 + height * np.sin(t * np.pi / 2) ** 1.6) for t in np.linspace(0, 1, samples)]
    fall = [(left + width * (crest + (1 - crest) * t), 0.04 + height * np.cos(t * np.pi / 2) ** 0.8) for t in np.linspace(0, 1, samples // 2)[1:]]
    return rise + fall


def desert(ax, x, y, size, zorder=5):
    def pt(p): return (x + (p[0] - 0.5) * size, y + p[1] * size)
    sand, shade = (0.93, 0.80, 0.55), (0.80, 0.64, 0.38)
    for left, width, height, z in ((0.00, 0.62, 0.30, 0.00), (0.36, 0.64, 0.40, 0.01), (0.14, 0.56, 0.20, 0.02)):
        profile = _dune_profile(left, width, height)
        ax.add_patch(_Polygon([pt(p) for p in profile], closed=True, facecolor=sand, edgecolor=INK, linewidth=0.9, joinstyle="round", zorder=zorder + z))
        top = max(range(len(profile)), key=lambda i: profile[i][1])
        ax.add_patch(_Polygon([pt(p) for p in profile[top:]] + [pt((profile[top][0] + width * 0.04, 0.04))], closed=True, facecolor=shade, edgecolor="none", zorder=zorder + z + 0.001))
        _line(ax, [pt(p) for p in profile], color=INK, linewidth=0.9, zorder=zorder + z + 0.002)
        for share in (0.35, 0.6):
            ripple = profile[int(top * share)]
            _line(ax, [pt((ripple[0] - width * 0.06, ripple[1] - height * 0.18)), pt((ripple[0] + width * 0.04, ripple[1] - height * 0.12))], color=shade, linewidth=0.7, zorder=zorder + z + 0.003)
    _line(ax, [pt((0.0, 0.04)), pt((1.0, 0.04))], color=INK, linewidth=0.9, zorder=zorder + 0.03)


def forest(ax, x, y, size, zorder=5):
    def pt(p): return (x + (p[0] - 0.5) * size, y + p[1] * size)
    for cx, base, crown, z in ((0.24, 0.04, 0.22, 0.0), (0.76, 0.04, 0.22, 0.01), (0.50, 0.00, 0.28, 0.02)):
        _box(ax, pt, cx - 0.035, base, cx + 0.035, base + 0.30, BARK, zorder + z, width=0.6)
        _ellipse(ax, pt, cx, base + 0.30 + crown * 0.8, crown, crown * 0.95, zorder + z + 0.001, facecolor=GREEN, edgecolor=GREEN_DARK, linewidth=0.9)
        _ellipse(ax, pt, cx - crown * 0.3, base + 0.30 + crown * 1.0, crown * 0.35, crown * 0.3, zorder + z + 0.002, facecolor="#8fb86a", edgecolor="none")


def cave(ax, x, y, size, zorder=5):
    def pt(p): return (x + (p[0] - 0.5) * size, y + p[1] * size)
    _rock(ax, pt, ((0.02, 0.10), (0.06, 0.40), (0.20, 0.70), (0.42, 0.88), (0.66, 0.84), (0.86, 0.62), (0.96, 0.34), (0.98, 0.10)), zorder)
    arch = [(0.30 + 0.40 * (1 - np.cos(t)) / 2 + 0.0, 0.10 + 0.42 * np.sin(t)) for t in np.linspace(0, np.pi, 18)]
    ax.add_patch(_Polygon([pt(p) for p in arch], closed=True, facecolor="#3b2f26", edgecolor=INK, linewidth=0.8, zorder=zorder + 0.01))
    for sx, sh in ((0.42, 0.10), (0.50, 0.14), (0.58, 0.09)):
        ax.add_patch(_Polygon([pt((sx - 0.025, 0.50)), pt((sx, 0.50 - sh)), pt((sx + 0.025, 0.50))], closed=True, facecolor=(0.88, 0.84, 0.74), edgecolor="none", zorder=zorder + 0.012))
    ribbon_l = [(0.44 + 0.04 * t, 0.12 - 0.10 * t) for t in np.linspace(0, 1, 6)]
    ribbon_r = [(0.58 + 0.20 * t, 0.12 - 0.10 * t) for t in np.linspace(0, 1, 6)]
    ax.add_patch(_Polygon([pt(p) for p in ribbon_l + ribbon_r[::-1]], closed=True, facecolor="#8cc3e8", edgecolor="#2f6a99", linewidth=0.8, zorder=zorder + 0.02))
    _ellipse(ax, pt, 0.60, 0.00, 0.26, 0.05, zorder + 0.021, facecolor="#8cc3e8", edgecolor="#2f6a99", linewidth=0.9)
    for gx, gh in ((0.00, 0.12), (0.10, 0.10), (0.92, 0.12), (0.98, 0.10)):
        for lean, height in ((-0.03, gh), (0.0, gh * 1.2), (0.03, gh * 0.9)):
            _line(ax, [pt((gx, 0.02)), pt((gx + lean, 0.02 + height))], color="#5aa83a", linewidth=1.1, zorder=zorder + 0.03)


def delta_fan(ax, x, y, size, angle, zorder=3.5):
    c, s = np.cos(angle), np.sin(angle)

    def pt(u, v):
        return (x + (u * c - v * s) * size, y + (u * s + v * c) * size)

    spread = np.radians(52)
    arc = [pt(np.cos(a) * 1.0, np.sin(a) * 1.0) for a in np.linspace(-spread, spread, 28)]
    ax.add_patch(_Polygon([pt(0, 0)] + arc, closed=True, facecolor="#cfd9a8", edgecolor="#7d7a4f", linewidth=1.0, linestyle=(0, (4, 3)), zorder=zorder))
    for k, a in enumerate(np.linspace(-spread * 0.9, spread * 0.9, 6)):
        bend = 0.10 * (1 if a > 0 else -1) * (1 - abs(a) / spread * 0.3)
        mid = pt(np.cos(a) * 0.5, np.sin(a) * 0.5 + bend); end = pt(np.cos(a) * 0.98, np.sin(a) * 0.98)
        xs, ys = [pt(0, 0)[0], mid[0], end[0]], [pt(0, 0)[1], mid[1], end[1]]
        width = 2.2 if abs(a) < spread * 0.5 else 1.5
        ax.plot(xs, ys, color="#2f6a99", linewidth=width + 0.9, solid_capstyle="round", zorder=zorder + 0.1)
        ax.plot(xs, ys, color="#4f93cf", linewidth=width, solid_capstyle="round", zorder=zorder + 0.11)


def gorge(ax, x, y, size, zorder=7.5):
    def pt(p): return (x + (p[0] - 0.5) * size, y + p[1] * size)
    wall, shade = (0.80, 0.52, 0.36), (0.62, 0.38, 0.26)
    left = ((0.00, 0.06), (0.00, 0.62), (0.10, 0.62), (0.10, 0.80), (0.24, 0.80), (0.24, 0.92), (0.38, 0.92), (0.38, 0.60), (0.34, 0.44), (0.40, 0.30), (0.42, 0.06))
    right = ((1.00, 0.06), (1.00, 0.66), (0.90, 0.66), (0.90, 0.84), (0.76, 0.84), (0.76, 0.94), (0.62, 0.94), (0.62, 0.62), (0.66, 0.46), (0.60, 0.30), (0.58, 0.06))
    for outline, edge_side in ((left, 1), (right, -1)):
        ax.add_patch(_Polygon([pt(p) for p in outline], closed=True, facecolor=wall, edgecolor=INK, linewidth=0.9, joinstyle="round", zorder=zorder))
        for sy in (0.20, 0.34, 0.48, 0.62, 0.76):
            xs = [p[0] for p in outline if abs(p[1] - sy) < 0.18]
            if xs: _line(ax, [pt((min(xs) if edge_side == 1 else max(xs), sy)), pt((0.0 if edge_side == 1 else 1.0, sy))], color=shade, linewidth=0.6, zorder=zorder + 0.001)
    ax.add_patch(_Polygon([pt(p) for p in ((0.40, 0.06), (0.60, 0.06), (0.58, 0.30), (0.50, 0.26), (0.42, 0.30))], closed=True, facecolor="#4f93cf", edgecolor="#2f6a99", linewidth=0.8, zorder=zorder + 0.002))


def ink_lake(ax, x, y, size, aspect=1.4, theta=0.0, zorder=4.6, dried=False):
    c, s = np.cos(theta), np.sin(theta)

    def pt(u, v):
        return (x + (u * c - v * s) * size * aspect / 2, y + (u * s + v * c) * size / 2)

    ang = np.linspace(0, 2 * np.pi, 72)
    radius = 1 + 0.07 * np.sin(3 * ang + 0.6) + 0.05 * np.sin(5 * ang + 1.3)
    ax.add_patch(_Polygon([pt(r * np.cos(a), r * np.sin(a)) for r, a in zip(radius, ang)], closed=True, facecolor="#cfcfcf" if dried else "#8cc3e8", edgecolor="#6f6f6f" if dried else "#2f6a99", linewidth=1.0, joinstyle="round", zorder=zorder))
    ax.add_patch(_Polygon([pt(0.80 * r * np.cos(a) - 0.06, 0.78 * r * np.sin(a) + 0.08) for r, a in zip(radius, ang)], closed=True, facecolor="#e6e6e6" if dried else "#b4dbf2", edgecolor="none", zorder=zorder + 0.001))
    for cu, cv, half in ((-0.38, 0.22, 0.22), (0.12, -0.08, 0.26), (0.42, 0.30, 0.18), (-0.18, -0.42, 0.20)):
        wave = [pt(cu + half * (t - 0.5) * 2, cv + 0.07 * np.sin(t * 2 * np.pi)) for t in np.linspace(0, 1, 10)]
        ax.plot([p[0] for p in wave], [p[1] for p in wave], color="#f2f9fd", linewidth=0.9, solid_capstyle="round", zorder=zorder + 0.002)


SPRING_OUTLET = (0.62, 0.05)
