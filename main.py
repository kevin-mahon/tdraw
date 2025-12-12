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

    def screen_to_canvas_coords(self, event):
        """
        Translates mouse event coordinates (screen cells) to 
        canvas coordinates (virtual pixels).
        """
        if self.past_pixel is None:
            return event.x, event.y * 2
        else:
            last_x, last_y = self.past_pixel
            #determine if we should draw in between the last pixel y and the current pixel_y
            pixel_y = event.y * 2
            pixel_x = event.x
            if abs(pixel_y - last_y) > 2:
                #draw in between
                with (canvas := self.query_one(Canvas)).batch_refresh():
                    canvas.set_pixel(
                        pixel_x,
                        pixel_y - 1 if pixel_y > last_y else pixel_y + 1,
                        Color(255, 0, 0)
                    )
            return pixel_x, pixel_y

         # Each cell is 2 pixels high

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

    def update_state(self):
        pass

    async def on_button_pressed(self, event: Button.Pressed):
        pass

    async def on_mouse_move(self, event):
        print(f"Mouse move at {event.x}, {event.y}")
        if self.drawing:
            x,y = self.screen_to_canvas_coords(event)
            if x is not None and y is not None:
                with (canvas := self.query_one(Canvas)).batch_refresh():
                    canvas.set_pixel(
                        x,
                        y,
                        Color(255, 0, 0)
                    )

    async def on_mouse_up(self, event):
        self.drawing = False

    async def on_mouse_down(self, event):
        self.drawing = True
        #convert the mouse coordinates to canvas coordinates
        x,y = self.screen_to_canvas_coords(event)
        self.past_pixel = (x,y)
        
        if x is None or y is None:
            with (canvas := self.query_one(Canvas)).batch_refresh():
                canvas.set_pixel(
                    x,
                    y,
                    Color(255, 0, 0)
                )

    def on_unmount(self):
        pass
        # self.hub_client.close()



def main():
    Draw().run()

if __name__ == "__main__":
    main()
