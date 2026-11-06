FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY run.py .
ENV COMAXIANS_DATA_DIR=/data PORT=8080
VOLUME ["/data"]
EXPOSE 8080
CMD ["python", "run.py"]
