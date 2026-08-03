#!/bin/sh
open -a Docker
sleep 2

docker rm -f avance-ai
docker build --no-cache -t avance-ai-image .
docker run -p 8000:80 --name avance-ai avance-ai-image
