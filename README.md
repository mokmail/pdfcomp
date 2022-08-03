# pdfcomp

this tool helps to find out changes made to a PDF file by comparing old and last state  
### build docker image image 
sudo docker build . -t pdf:third
### to run the docker image
sudo docker run -u=$(id -u $USER):$(id -g $USER) -e DISPLAY=$DISPLAY -v /tmp/.X11-unix:/tmp/.X11-unix:rw -v $(pwd)/app:/app --rm pdfer:third
