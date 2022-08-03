import tkinter as tk
from PIL import Image, ImageChops, ImageTk
from tkinter import Canvas
from datetime import datetime

from tkinter import messagebox
import os
import sys
import shutil
from tkinter import ttk

from thepdffile import Thefile

from wido import Wido


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


root = Wido()

frm = ttk.Frame(root, padding=5)
frm2 = ttk.Frame(root, padding=5)
frm3 = ttk.Frame(root, padding=5)
frm4 = ttk.Frame(root, padding=5)

frm2.grid(column=0, row=0)
ttk.Separator(root, orient='horizontal').grid(
    column=0, row=1, ipadx=200, ipady=0)
ttk.Separator(root, orient='horizontal').grid(
    column=0, row=4, ipadx=200, ipady=0)
frm.grid(column=0, row=3,)
frm3.grid(column=0, row=5)
frm4.grid(column=0, row=6)
#root.title('PDF COMPARE')
pr = ttk.Progressbar(
    frm2,
    orient='horizontal',
    mode='indeterminate',
    length=280
)


canvas = Canvas(
    frm2,
    width=327,
    height=143
)
canvas.grid()
#img = ImageTk.PhotoImage(Image.open(resource_path('dp2.png')))
canvas.create_image(
    163,
    70,

    #   image=img
)


class Comparer:
    def __init__(self) -> None:
        self.wert = 0
        self.temp_delete()

    def deco(function, *args):

        def wrapper(self, *args):
            #pr.grid(column=0, row=3)
            #pr.start()

            start = datetime.now()

            #messagebox.showinfo('Started', start )

            dos = function(self, *args)
            end = datetime.now()
            duration = end-start
            messagebox.showinfo('Done', duration)

            return(dos)

        return wrapper

    @deco
    def openfile(self, fos):
        # pr.start()
        self.thepdf = Thefile(fos)

        self.thepdf.save_pdf_pages()
        self.wert += 1
        self.next_but()

    def compare(self):

        self.temp = self.thepdf.temp
        try:

            image1 = Image.open(
                '{}/{}'.format(self.thepdf.temp, os.listdir(self.thepdf.temp)[0]))
            #image1 = Image.open('ersult.png').show()
            image2 = Image.open(
                '{}/{}'.format(self.thepdf.temp, os.listdir(self.thepdf.temp)[1]))
            diffs = ImageChops.difference(image1=image1, image2=image2)

            res = Image.blend(image1, diffs, .9)
            res.save('result.png')
            res.show()
            self.temp_delete()

        except Exception as e:
            messagebox.showerror("Error", e)
            print(e)

    def next_but(self):
        if self.wert == 1:

            tk.Button(frm, text="Open 2nd PDF",
                      command=lambda: self.openfile('x2')).grid(column=0, row=2, sticky=tk.W+tk.E)
        elif self.wert == 2:

            tk.Button(frm, text="START", font=root.font, fg="green",
                      command=lambda: self.compare()).grid(column=0, row=3, sticky=tk.W+tk.E)
        # pr.stop()

    def temp_delete(self):
        if os.path.exists(os.getcwd()+'/temp'):

            shutil.rmtree(os.getcwd()+'/temp')

    def destroyer(self):
        root.destroy()
        self.temp_delete()


co = Comparer()

tk.Label(frm, text="Please select PDF Files to analyse!".center(
    50)).grid(column=0, row=0, ipady=20)
tk.Button(frm, text="Open 1st PDF",
          command=lambda: co.openfile('x1')).grid(column=0, row=1, sticky=tk.W+tk.E)

#ttk.Button(frm, text="Load second PDF",command=lambda: co.openfile('x2')).grid(column=2, row=1)


cl = tk.Button(frm3, text="Clean up & close", fg="red",
               command=co.destroyer)

cl.grid(column=1, row=4, sticky=tk.W+tk.E)

tk.Label(frm4, text="copyright © 2022 Mohammed kmail",
         font=("helvetica", 10), fg="white").grid(column=0, row=0, ipady=2)


root.mainloop()
