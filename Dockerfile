FROM python3.9.12
RUN apt-get update -y

RUN apt-get upgrade -y
RUN apt-get install pip -y
RUN pip install tk
RUN pip install pdf2image
RUN pip install Pillow
COPY . .

CMD ["python",  "pdfcomp.py"]
