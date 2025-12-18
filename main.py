import time
import threading
from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, Horizontal
from textual.widgets import Static, Header, Footer, Button
from textual_canvas.canvas import Canvas
from textual.color import Color
from pyautogui import position


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
        self.canvas_size = (120, 80)  #width, height in virtual pixels
        self.past_pixel = None
        self.past_mouse = None
        self.brush_size = 1
        self.poll_mouse_thread = threading.Thread(target=self.poll_mouse_position)
        self.poll_mouse_thread.daemon = True
        self.poll_mouse_thread.start()

    def poll_mouse_position(self):
        """
        Polls the mouse position every 0.1 seconds and draws if the mouse is down.
        """

        while True:
            if self.drawing:
                mouse_pos = position()
                #find relative position to self.first_pixel
                rel_x = mouse_pos.x - self.first_pixel["actual_coords"].x
                rel_y = mouse_pos.y - self.first_pixel["actual_coords"].y
                x,y = self.first_pixel["pixel_coords"]
                x += rel_x#self.canvas_size[0]
                y += rel_y#/self.canvas_size[1]
                print(f"Mouse pos: {mouse_pos}, \
                      First pixel coords: {self.first_pixel['pixel_coords']}, \
                      Rel pos: {rel_x}, {rel_y} \
                      Drawing at: {x}, {y}")
                #this relative change in position needs to be translated to terminal character cells
                self._draw(int(x),int(y))
            time.sleep(0.1)

    def screen_to_canvas_coords(self, event):
        """
        Translates mouse event coordinates (screen cells) to 
        canvas coordinates (virtual pixels).
        """
        #check the event.screen_x and event.screen_y to see if the mouse is halfway down a character cell
        #and adjust event.y*2 - 1 if so
        print(f"Screen coords: {event.screen_x}, {event.screen_y}")
        return event.x, event.y * 2

    def compose(self) -> ComposeResult:
        yield Header()
        with Container():
            with Horizontal(id="main"):
                with Vertical(id="left"):
                    yield Static("Control Panel goes here", id="title")
                with Vertical(id="right"):
                    yield Canvas(self.canvas_size[0], self.canvas_size[1])
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

    def _draw(self, x, y):
        if x is not None and y is not None:
            with (canvas := self.query_one(Canvas)).batch_refresh():
                canvas.set_pixel(
                    x,
                    y,
                    Color(255, 0, 0)
                )

    async def on_mouse_move(self, event):
        pass
        # print(f"Mouse position : {position()}")
        # if self.drawing:
            # self._draw(event)

    async def on_mouse_up(self):
        self.drawing = False

    async def on_mouse_down(self, event):
        self.first_pixel = {
                "pixel_coords" : (event.x, event.y),
                "actual_coords" : position(),
        }
        self.drawing = True
        # self._draw(event)


def main():
    Draw().run()

if __name__ == "__main__":
    main()
