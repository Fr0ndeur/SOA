FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.115.12 uvicorn==0.34.2 httpx==0.28.1 prometheus-client==0.21.1
COPY *.py .
RUN mkdir -p /data && useradd -u 10001 app && chown app /data
USER app
CMD ["uvicorn","collector:app","--host","0.0.0.0","--port","8000"]
