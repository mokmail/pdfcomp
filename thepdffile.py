

from tkinter import filedialog as fd
from tkinter import simpledialog as sm

import os
from pdf2image import convert_from_path




class Thefile:

    def __init__(self, fos): 

        self.fos = fos #fisrt or second
        self.filepath = self.makeFileName(self.fos)

        self.filename = '{}_{}_IMG'.format(self.fos,
                                           self.filepath.split('/')[-1].split('.')[0])
        self.imaged_pages_from_pdf_file = self.convertPdf()
        self.pagenums = len(self.imaged_pages_from_pdf_file)
        self.singelpage = True if self.pagenums < 2 else False
        self.temp = os.getcwd() + '/temp'
        isExist = os.path.exists(self.temp)
        print(self.singelpage)
        if not isExist:

            os.makedirs(f'{self.temp}')

        self.info = []
        print(self.filename)

    def makeFileName(self, first_or_second):
        filepath = fd.askopenfilename(
            filetypes=[(" PDF", "*.pdf"), (" PDF", "*.PDF")])

        return(filepath)

    def getLocation(self):
        return(fd.askdirectory())

    def __str__(self) -> str:
        return(self.filename[-1])

    def convertPdf(self):

        convertedPdf = convert_from_path(self.filepath)

        print(convertedPdf[0].height)

        return(convertedPdf)

    def save_pdf_pages(self):
        if not self.singelpage:
            asa = sm.askinteger("Multiple pages", f"the file you selected contains {self.pagenums} pages,  \n which page do you need to analyse",
                               
                                minvalue=0, maxvalue=self.pagenums)

            self.imaged_pages_from_pdf_file[asa-1].save(
                '{}/_{}_{}.png'.format(self.temp, self.filename, asa-1))
        else:
            self.imaged_pages_from_pdf_file[0].save(
                '{}/_{}_{}.png'.format(self.temp, self.filename, 0))
