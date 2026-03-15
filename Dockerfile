FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY *.py ./

# Render provides PORT env var (default 10000)
ENV PORT=10000

CMD ["python", "main.py"]
