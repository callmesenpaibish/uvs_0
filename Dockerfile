FROM python:3.11-slim

RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /uvs_0
COPY . .
RUN pip install -r requirements.txt

CMD ["python", "main.py"]
