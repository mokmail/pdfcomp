

from tkinter import Tk
import tkinter.font as font




from pdf2image import convert_from_path


class Wido(Tk):
    def __init__(self):
        super().__init__()
        self.title("PDF Compare")

        self.font = font.Font(family='arial', size=10, weight='bold')
    def __str__(self) -> str:
        return( 'this class inherits the tkinter class, just to take over controll of the object ')

p = Wido()
print(p)

        # self.geometry('500x400')
