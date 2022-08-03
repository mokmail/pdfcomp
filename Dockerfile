FROM python

RUN apt-get update -y
ENV PYTHON_VERSION=3.9.12

RUN apt-get upgrade -y
RUN apt-get install pip -y
RUN apt-get install tk -y
RUN pip install pdf2image
RUN pip install Pillow
WORKDIR /
COPY . .


CMD ["python", "pdfcomp.py"]
