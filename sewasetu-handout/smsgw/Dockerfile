FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir Flask==3.1.3 Werkzeug==3.1.9
COPY app.py .
EXPOSE 8025
CMD ["python", "app.py"]
