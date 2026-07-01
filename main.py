"""tdraw -- a tiny MS-Paint-style drawing app for the terminal.

Requirements:
    pip install textual textual-canvas pyautogui
    (pyautogui is optional -- it enables sub-cell mouse precision)

Controls:
    Tools:   p pen   e eraser   l line   r rect   R handled via buttons
             o ellipse   f fill (bucket)   t text (click, then type + Enter)
    Mouse:   left-drag draw   right-drag erase   middle-drag pan
             scroll wheel = zoom at cursor
    Keys:    [ / ]  brush size      + / -  zoom      arrows  pan
             u undo    c clear
"""

import math
import struct
import sys
import time
from collections import deque

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, Static
from textual.color import Color
from textual_canvas.canvas import Canvas

try:
    import fcntl
    import termios
except ImportError:  # non-POSIX platform
    fcntl = None

try:
    from pyautogui import position as _mouse_px
except Exception:  # not installed, or no display server access (e.g. Wayland)
    _mouse_px = None


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

VIEW_W, VIEW_H = 120, 80      # viewport size in virtual (half-block) pixels
DOC_W, DOC_H = 240, 160       # document size in pixels
PAPER = Color(0, 0, 0)        # background/eraser colour
GUTTER = Color(40, 40, 40)    # shown where the view hangs off the document

PALETTE = [
    ("White",  Color(255, 255, 255)),
    ("Red",    Color(230, 60, 60)),
    ("Orange", Color(245, 150, 40)),
    ("Yellow", Color(240, 220, 60)),
    ("Green",  Color(70, 200, 90)),
    ("Cyan",   Color(70, 200, 220)),
    ("Blue",   Color(80, 110, 240)),
    ("Purple", Color(180, 90, 220)),
]

TOOLS = [
    ("Pen", "pen"),
    ("Eraser", "eraser"),
    ("Line", "line"),
    ("Rect", "rect"),
    ("Rect fill", "rectf"),
    ("Ellipse", "ellipse"),
    ("Ell. fill", "ellipsef"),
    ("Fill", "fill"),
    ("Text", "text"),
]


def get_cell_pixel_size():
    """Ask the terminal how big a character cell is in device pixels.

    Uses the TIOCGWINSZ ioctl; most modern terminal emulators (kitty,
    wezterm, alacritty, foot, iTerm2, ...) fill in ws_xpixel/ws_ypixel.
    Returns (cell_w, cell_h) in pixels, or None if unavailable.
    """
    if fcntl is None:
        return None
    try:
        buf = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
        rows, cols, xpix, ypix = struct.unpack("HHHH", buf)
        if rows and cols and xpix and ypix:
            return xpix / cols, ypix / rows
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------
# Rasterisation helpers (all in document-pixel coordinates)
# --------------------------------------------------------------------------

_BRUSH_CACHE = {}


def brush_offsets(size):
    """Round-ish brush footprint as (dx, dy) offsets."""
    if size not in _BRUSH_CACHE:
        r2 = (size * size) / 4
        rr = size // 2 + 1
        pts = [
            (ox, oy)
            for oy in range(-rr, rr + 1)
            for ox in range(-rr, rr + 1)
            if ox * ox + oy * oy <= r2
        ]
        _BRUSH_CACHE[size] = pts or [(0, 0)]
    return _BRUSH_CACHE[size]


def line_points(x0, y0, x1, y1):
    """Bresenham line."""
    pts = []
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        pts.append((x0, y0))
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy
    return pts


def rect_points(x0, y0, x1, y1):
    xa, xb = min(x0, x1), max(x0, x1)
    ya, yb = min(y0, y1), max(y0, y1)
    pts = []
    for x in range(xa, xb + 1):
        pts.append((x, ya))
        pts.append((x, yb))
    for y in range(ya + 1, yb):
        pts.append((xa, y))
        pts.append((xb, y))
    return pts


def rect_fill_points(x0, y0, x1, y1):
    xa, xb = min(x0, x1), max(x0, x1)
    ya, yb = min(y0, y1), max(y0, y1)
    return [(x, y) for y in range(ya, yb + 1) for x in range(xa, xb + 1)]


def ellipse_points(x0, y0, x1, y1):
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    a, b = abs(x1 - x0) / 2, abs(y1 - y0) / 2
    if a < 0.5 or b < 0.5:
        return line_points(x0, y0, x1, y1)
    steps = max(24, int(2 * math.pi * max(a, b) * 2))
    pts = set()
    for i in range(steps):
        t = 2 * math.pi * i / steps
        pts.add((round(cx + a * math.cos(t)), round(cy + b * math.sin(t))))
    return list(pts)


def ellipse_fill_points(x0, y0, x1, y1):
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    a, b = abs(x1 - x0) / 2, abs(y1 - y0) / 2
    if a < 0.5 or b < 0.5:
        return line_points(x0, y0, x1, y1)
    pts = []
    for y in range(min(y0, y1), max(y0, y1) + 1):
        dy = (y - cy) / b
        if abs(dy) <= 1:
            w = a * math.sqrt(1 - dy * dy)
            for x in range(math.ceil(cx - w), int(cx + w) + 1):
                pts.append((x, y))
    return pts


SHAPES = {
    "line": line_points,
    "rect": rect_points,
    "rectf": rect_fill_points,
    "ellipse": ellipse_points,
    "ellipsef": ellipse_fill_points,
}


# --------------------------------------------------------------------------
# 3x5 bitmap font for the text tool
# --------------------------------------------------------------------------

FONT = {
    "A": ["010", "101", "111", "101", "101"],
    "B": ["110", "101", "110", "101", "110"],
    "C": ["011", "100", "100", "100", "011"],
    "D": ["110", "101", "101", "101", "110"],
    "E": ["111", "100", "110", "100", "111"],
    "F": ["111", "100", "110", "100", "100"],
    "G": ["011", "100", "101", "101", "011"],
    "H": ["101", "101", "111", "101", "101"],
    "I": ["111", "010", "010", "010", "111"],
    "J": ["011", "001", "001", "101", "010"],
    "K": ["101", "110", "100", "110", "101"],
    "L": ["100", "100", "100", "100", "111"],
    "M": ["101", "111", "111", "101", "101"],
    "N": ["110", "101", "101", "101", "101"],
    "O": ["010", "101", "101", "101", "010"],
    "P": ["110", "101", "110", "100", "100"],
    "Q": ["010", "101", "101", "010", "001"],
    "R": ["110", "101", "110", "101", "101"],
    "S": ["011", "100", "010", "001", "110"],
    "T": ["111", "010", "010", "010", "010"],
    "U": ["101", "101", "101", "101", "111"],
    "V": ["101", "101", "101", "101", "010"],
    "W": ["101", "101", "111", "111", "101"],
    "X": ["101", "101", "010", "101", "101"],
    "Y": ["101", "101", "010", "010", "010"],
    "Z": ["111", "001", "010", "100", "111"],
    "0": ["111", "101", "101", "101", "111"],
    "1": ["010", "110", "010", "010", "111"],
    "2": ["110", "001", "010", "100", "111"],
    "3": ["110", "001", "010", "001", "110"],
    "4": ["101", "101", "111", "001", "001"],
    "5": ["111", "100", "110", "001", "110"],
    "6": ["011", "100", "110", "101", "010"],
    "7": ["111", "001", "010", "010", "010"],
    "8": ["010", "101", "010", "101", "010"],
    "9": ["010", "101", "011", "001", "110"],
    " ": ["000", "000", "000", "000", "000"],
    ".": ["000", "000", "000", "000", "010"],
    ",": ["000", "000", "000", "010", "100"],
    "!": ["010", "010", "010", "000", "010"],
    "?": ["110", "001", "010", "000", "010"],
    "-": ["000", "000", "111", "000", "000"],
    ":": ["000", "010", "000", "010", "000"],
    "'": ["010", "010", "000", "000", "000"],
}


# --------------------------------------------------------------------------
# Canvas widget: forwards mouse events to the app
# --------------------------------------------------------------------------

class PaintCanvas(Canvas):
    can_focus = True

    def _adjust(self, event):
        """Widget-relative coords, compensating for any internal scrolling."""
        off = self.scroll_offset
        return event.x + int(off.x), event.y + int(off.y)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self.capture_mouse()
        x, y = self._adjust(event)
        self.app.pointer_down(x, y, event.button)
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        x, y = self._adjust(event)
        self.app.pointer_move(x, y)
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self.release_mouse()
        self.app.pointer_up()
        event.stop()

    def on_mouse_scroll_up(self, event) -> None:
        x, y = self._adjust(event)
        self.app.zoom_at(x, y, +1)
        event.stop()

    def on_mouse_scroll_down(self, event) -> None:
        x, y = self._adjust(event)
        self.app.zoom_at(x, y, -1)
        event.stop()


# --------------------------------------------------------------------------
# The application
# --------------------------------------------------------------------------

class DrawApp(App):
    CSS = """
    #main { height: 1fr; }
    #left {
        width: 26;
        border: solid $accent;
        padding: 0 1;
    }
    #right { width: 1fr; }
    .heading { color: $text-muted; margin-top: 1; }
    Button.tool {
        width: 100%;
        height: 1;
        min-width: 6;
        border: none;
        margin: 0;
    }
    .swatchrow { height: 1; }
    Button.swatch {
        width: 5;
        height: 1;
        min-width: 5;
        border: none;
        margin: 0 1 0 0;
    }
    #textinput { margin-top: 1; }
    #status { margin-top: 1; color: $text-muted; }
    """

    BINDINGS = [
        Binding("p", "set_tool('pen')", "Pen"),
        Binding("e", "set_tool('eraser')", "Eraser"),
        Binding("l", "set_tool('line')", "Line"),
        Binding("r", "set_tool('rect')", "Rect"),
        Binding("o", "set_tool('ellipse')", "Ellipse"),
        Binding("f", "set_tool('fill')", "Fill"),
        Binding("t", "set_tool('text')", "Text"),
        Binding("left_square_bracket", "brush(-1)", "Brush -"),
        Binding("right_square_bracket", "brush(1)", "Brush +"),
        Binding("plus,equals_sign", "zoom(1)", "Zoom +"),
        Binding("minus", "zoom(-1)", "Zoom -"),
        Binding("up", "pan(0,-1)", show=False),
        Binding("down", "pan(0,1)", show=False),
        Binding("left", "pan(-1,0)", show=False),
        Binding("right", "pan(1,0)", show=False),
        Binding("u", "undo", "Undo"),
        Binding("c", "clear", "Clear"),
    ]

    def __init__(self):
        super().__init__()
        # Document: a plain 2D list of Colors -- the source of truth.
        self.doc = [[PAPER for _ in range(DOC_W)] for _ in range(DOC_H)]
        self.tool = "pen"
        self.color_index = 0
        self.brush = 1
        self.zoom = 1
        self.pan_x = (DOC_W - VIEW_W) // 2
        self.pan_y = (DOC_H - VIEW_H) // 2

        # Sub-cell mouse precision: terminal cell size in device pixels.
        self.cell_px = get_cell_pixel_size()

        # Transient interaction state.
        self._drawing = False
        self._panning = False
        self._stroke_color = PALETTE[0][1]
        self._last_doc = None          # last pen point (doc coords, ints)
        self._shape_start = None       # shape anchor (doc coords, ints)
        self._shape_end = None
        self._anchor_view = None       # (fx, fy) view pixel where drag began
        self._anchor_px = None         # OS mouse position at drag start
        self._pan_anchor = None
        self._text_pos = None
        self._last_render = 0.0
        self._undo = deque(maxlen=30)

    # -- layout -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            with VerticalScroll(id="left"):
                yield Static("Tools", classes="heading")
                for label, key in TOOLS:
                    yield Button(label, id=f"tool-{key}", classes="tool")
                yield Static("Colors", classes="heading")
                for row in (PALETTE[:4], PALETTE[4:]):
                    with Horizontal(classes="swatchrow"):
                        for name, color in row:
                            i = next(
                                j for j, (n, _) in enumerate(PALETTE) if n == name
                            )
                            b = Button(" ", id=f"color-{i}", classes="swatch")
                            b.styles.background = color
                            yield b
                yield Input(placeholder="text + Enter", id="textinput")
                yield Static("", id="status")
            with Vertical(id="right"):
                yield PaintCanvas(VIEW_W, VIEW_H, PAPER)
        yield Footer()

    def on_mount(self) -> None:
        self.canvas = self.query_one(PaintCanvas)
        self.render_view()
        self.update_status()
        self.canvas.focus()
        if self.cell_px is None:
            self.notify(
                "Terminal didn't report pixel size; sub-cell precision off.",
                severity="warning",
            )

    # -- coordinate transforms ----------------------------------------------

    def view_to_doc(self, fx, fy):
        """Float viewport pixel -> int document pixel."""
        return (
            int(math.floor(self.pan_x + fx / self.zoom)),
            int(math.floor(self.pan_y + fy / self.zoom)),
        )

    def refined_view_pos(self, cell_x, cell_y):
        """Best-guess viewport pixel position (floats) for a mouse event.

        Terminal mouse reporting is per character cell, i.e. 1 pixel wide
        but only *half* the vertical resolution of the canvas.  During a
        drag we recover full sub-cell precision by measuring how far the
        OS mouse pointer has moved (in device pixels) since mouse-down and
        dividing by the terminal's cell size.  Without pyautogui/cell size
        we fall back to the centre of the reported cell.
        """
        if (
            self._anchor_view is not None
            and self._anchor_px is not None
            and self.cell_px is not None
            and _mouse_px is not None
        ):
            try:
                px = _mouse_px()
            except Exception:
                px = None
            if px is not None:
                ax, ay = self._anchor_view
                apx, apy = self._anchor_px
                cw, ch = self.cell_px
                return (
                    ax + (px.x - apx) / cw,           # 1 cell  == 1 pixel wide
                    ay + (px.y - apy) / (ch / 2.0),   # 1 cell  == 2 pixels tall
                )
        # Fallback: centre of the reported cell.
        return cell_x + 0.5, cell_y * 2 + 1.0

    # -- rendering ------------------------------------------------------------

    def render_view(self):
        """Blit the visible part of the document into the canvas widget."""
        canvas, doc, zoom = self.canvas, self.doc, self.zoom
        px, py = self.pan_x, self.pan_y
        with canvas.batch_refresh():
            for sy in range(VIEW_H):
                dy = py + sy // zoom
                row = doc[dy] if 0 <= dy < DOC_H else None
                for sx in range(VIEW_W):
                    dx = px + sx // zoom
                    color = row[dx] if row is not None and 0 <= dx < DOC_W else GUTTER
                    canvas.set_pixel(sx, sy, color)

    def _blit_doc_pixel(self, dx, dy, color):
        """Update the on-screen zoom block for one document pixel."""
        sx0 = (dx - self.pan_x) * self.zoom
        sy0 = (dy - self.pan_y) * self.zoom
        for sy in range(sy0, sy0 + self.zoom):
            if 0 <= sy < VIEW_H:
                for sx in range(sx0, sx0 + self.zoom):
                    if 0 <= sx < VIEW_W:
                        self.canvas.set_pixel(sx, sy, color)

    def plot_many(self, points, color):
        """Write points to the document and incrementally update the view."""
        with self.canvas.batch_refresh():
            for dx, dy in points:
                if 0 <= dx < DOC_W and 0 <= dy < DOC_H:
                    self.doc[dy][dx] = color
                    self._blit_doc_pixel(dx, dy, color)

    def overlay_points(self, points, color):
        """Draw points on the *view only* (used for shape previews)."""
        with self.canvas.batch_refresh():
            for dx, dy in points:
                self._blit_doc_pixel(dx, dy, color)

    # -- drawing primitives -----------------------------------------------

    def stamped(self, points):
        """Expand a point list by the current brush footprint."""
        offsets = brush_offsets(self.brush)
        out = set()
        for x, y in points:
            for ox, oy in offsets:
                out.add((x + ox, y + oy))
        return out

    def flood_fill(self, x, y, color):
        if not (0 <= x < DOC_W and 0 <= y < DOC_H):
            return
        target = self.doc[y][x]
        if target == color:
            return
        queue = deque([(x, y)])
        self.doc[y][x] = color
        count = 0
        while queue and count < DOC_W * DOC_H:
            cx, cy = queue.popleft()
            count += 1
            for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if 0 <= nx < DOC_W and 0 <= ny < DOC_H and self.doc[ny][nx] == target:
                    self.doc[ny][nx] = color
                    queue.append((nx, ny))

    def stamp_text(self, text, x, y, color, scale=1):
        points = []
        cx = x
        for ch in text.upper():
            glyph = FONT.get(ch, FONT["?"])
            for gy, row in enumerate(glyph):
                for gx, bit in enumerate(row):
                    if bit == "1":
                        for oy in range(scale):
                            for ox in range(scale):
                                points.append(
                                    (cx + gx * scale + ox, y + gy * scale + oy)
                                )
            cx += 4 * scale  # 3px glyph + 1px spacing
        self.plot_many(points, color)

    def snapshot(self):
        self._undo.append([row[:] for row in self.doc])

    # -- pointer handling (called by PaintCanvas) --------------------------

    def pointer_down(self, cell_x, cell_y, button):
        fx, fy = cell_x + 0.5, cell_y * 2 + 1.0
        self._anchor_view = (fx, fy)
        self._anchor_px = None
        if _mouse_px is not None:
            try:
                p = _mouse_px()
                self._anchor_px = (p.x, p.y)
            except Exception:
                pass

        if button == 2:  # middle button: pan
            self._panning = True
            self._pan_anchor = (self.pan_x, self.pan_y)
            return

        dx, dy = self.view_to_doc(fx, fy)
        erase = button == 3 or self.tool == "eraser"
        self._stroke_color = PAPER if erase else PALETTE[self.color_index][1]

        if self.tool == "fill" and not erase:
            self.snapshot()
            self.flood_fill(dx, dy, self._stroke_color)
            self.render_view()
            return

        if self.tool == "text" and not erase:
            self._text_pos = (dx, dy)
            self.query_one("#textinput", Input).focus()
            self.notify("Type your text and press Enter.")
            return

        self.snapshot()
        if erase or self.tool in ("pen", "eraser"):
            self._mode = "pen"
            self._last_doc = (dx, dy)
            self.plot_many(self.stamped([(dx, dy)]), self._stroke_color)
        elif self.tool in SHAPES:
            self._mode = "shape"
            self._shape_start = (dx, dy)
            self._shape_end = (dx, dy)
        self._drawing = True

    def pointer_move(self, cell_x, cell_y):
        if not (self._drawing or self._panning):
            return
        fx, fy = self.refined_view_pos(cell_x, cell_y)

        if self._panning:
            ax, ay = self._anchor_view
            bx, by = self._pan_anchor
            self.pan_x = int(round(bx - (fx - ax) / self.zoom))
            self.pan_y = int(round(by - (fy - ay) / self.zoom))
            self.throttled_render()
            return

        dx, dy = self.view_to_doc(fx, fy)
        if self._mode == "pen":
            if (dx, dy) != self._last_doc:
                lx, ly = self._last_doc
                pts = self.stamped(line_points(lx, ly, dx, dy))
                self.plot_many(pts, self._stroke_color)
                self._last_doc = (dx, dy)
        elif self._mode == "shape":
            self._shape_end = (dx, dy)
            if self.throttled_render():
                sx, sy = self._shape_start
                pts = self.stamped(SHAPES[self.tool](sx, sy, dx, dy))
                self.overlay_points(pts, self._stroke_color)

    def pointer_up(self):
        if self._panning:
            self._panning = False
            self.render_view()
        elif self._drawing and self._mode == "shape" and self._shape_start:
            sx, sy = self._shape_start
            ex, ey = self._shape_end
            pts = self.stamped(SHAPES[self.tool](sx, sy, ex, ey))
            self.render_view()
            self.plot_many(pts, self._stroke_color)
        self._drawing = False
        self._shape_start = None
        self._anchor_view = None
        self._anchor_px = None
        self.update_status()

    def throttled_render(self, fps=30):
        now = time.monotonic()
        if now - self._last_render >= 1 / fps:
            self._last_render = now
            self.render_view()
            return True
        return False

    # -- zoom / pan ---------------------------------------------------------

    def zoom_at(self, cell_x, cell_y, direction):
        fx, fy = cell_x + 0.5, cell_y * 2 + 1.0
        focus_x = self.pan_x + fx / self.zoom
        focus_y = self.pan_y + fy / self.zoom
        new = min(8, max(1, self.zoom * 2 if direction > 0 else self.zoom // 2))
        if new == self.zoom:
            return
        self.zoom = new
        self.pan_x = int(round(focus_x - fx / new))
        self.pan_y = int(round(focus_y - fy / new))
        self.render_view()
        self.update_status()

    def action_zoom(self, direction: int):
        self.zoom_at(VIEW_W // 2, VIEW_H // 4, direction)

    def action_pan(self, ox: int, oy: int):
        step = max(1, 8 // self.zoom) * 2
        self.pan_x += ox * step
        self.pan_y += oy * step
        self.render_view()
        self.update_status()

    # -- other actions --------------------------------------------------------

    def action_set_tool(self, tool: str):
        self.tool = tool
        self.update_status()

    def action_brush(self, delta: int):
        self.brush = min(9, max(1, self.brush + delta))
        self.update_status()

    def action_undo(self):
        if self._undo:
            self.doc = self._undo.pop()
            self.render_view()

    def action_clear(self):
        self.snapshot()
        self.doc = [[PAPER for _ in range(DOC_W)] for _ in range(DOC_H)]
        self.render_view()

    def update_status(self):
        name = PALETTE[self.color_index][0]
        precision = "sub-cell" if (self.cell_px and _mouse_px) else "cell"
        self.query_one("#status", Static).update(
            f"tool: {self.tool}\n"
            f"color: {name}\n"
            f"brush: {self.brush}  zoom: {self.zoom}x\n"
            f"pan: {self.pan_x},{self.pan_y}\n"
            f"mouse: {precision}"
        )

    # -- widget events ---------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed):
        bid = event.button.id or ""
        if bid.startswith("tool-"):
            self.tool = bid[5:]
        elif bid.startswith("color-"):
            self.color_index = int(bid[6:])
        self.update_status()
        self.canvas.focus()

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "textinput" and self._text_pos and event.value:
            self.snapshot()
            x, y = self._text_pos
            self.stamp_text(
                event.value, x, y, PALETTE[self.color_index][1], scale=self.brush
            )
            event.input.value = ""
            self._text_pos = None
            self.canvas.focus()


def main():
    DrawApp().run()


if __name__ == "__main__":
    main()
