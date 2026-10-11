

from tkinter import Tk
import tkinter.font as font




from pdf2image import convert_from_path


class Wido(Tk):
    def __init__(self):
        super().__init__()
        self.title("PDF Compare")

        self.font = font.Font(family='arial', size=10, weight='bold')

        # self.geometry('500x400')
