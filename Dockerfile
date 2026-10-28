FROM python:3.12-slim
WORKDIR /srv/atlas
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN mkdir -p /srv/atlas/data
ENV ATLAS_DB=/srv/atlas/data/atlas.db
EXPOSE 8000
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]
