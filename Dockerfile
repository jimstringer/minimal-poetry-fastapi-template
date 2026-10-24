FROM python:3.15-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-deps --no-cache-dir -r requirements.txt

COPY src/app/ ./app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
