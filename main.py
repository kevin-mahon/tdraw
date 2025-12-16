from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, Horizontal
from textual.widgets import Static, Header, Footer, Button
from textual_canvas.canvas import Canvas
from textual.color import Color


class Draw(App):
    CSS = """
    Screen {
        layout: horizontal;
    }
    #left {
        width: 5%;
        border: solid black;
        padding: 1;
    }
    #right {
        width: 95%;
        border: solid white;
        padding: 1;
    }
    Canvas {
        width: 100%;
        height: 100%;
    }
    """

    current_screen: Canvas 

    def __init__(self):
        super().__init__()
        #get terminal size
        self.drawing = False
        self.past_pixel = None
        self.past_mouse = None
        self.brush_size = 1

    def screen_to_canvas_coords(self, event):
        """
        Translates mouse event coordinates (screen cells) to 
        canvas coordinates (virtual pixels).
        """
        return event.x, event.y * 2

    def compose(self) -> ComposeResult:
        yield Header()
        with Container():
            with Horizontal(id="main"):
                with Vertical(id="left"):
                    yield Static("Control Panel goes here", id="title")
                with Vertical(id="right"):
                    yield Canvas(120, 120)
        yield Footer()

    async def on_mount(self):
        with (canvas := self.query_one(Canvas)).batch_refresh():
            for x_pixel in range(0, canvas.width):
                for y_pixel in range(0, canvas.height):
                    canvas.set_pixel(
                        x_pixel,
                        y_pixel,
                        Color(0, 0, 0),
                    )

    async def on_button_pressed(self, event: Button.Pressed):
        pass

    def _draw(self, event):
        if event.x is None or event.y is None:
            return

        x,y = self.screen_to_canvas_coords(event)
        # extra_x, extra_y = self.predictive_brush(event)
        if x is not None and y is not None:
            with (canvas := self.query_one(Canvas)).batch_refresh():
                canvas.set_pixel(
                    x,
                    y,
                    Color(255, 0, 0)
                )
                # canvas.set_pixel(
                #     extra_x,
                #     extra_y,
                #     Color(255, 0, 0)
                # )
        self.past_mouse = (event.x, event.y)

    async def on_mouse_move(self, event):
        if self.drawing:
            self._draw(event)

    async def on_mouse_up(self):
        self.drawing = False

    async def on_mouse_down(self, event):
        self.drawing = True
        self._draw(event)


def main():
    Draw().run()

if __name__ == "__main__":
    main()
