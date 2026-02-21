FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create upload/output dirs at runtime
RUN mkdir -p uploads output

EXPOSE ${PORT:-5000}

# Shell form so $PORT env var (set by Railway) expands at runtime
CMD gunicorn app:app --bind 0.0.0.0:${PORT:-5000} --workers 2 --timeout 120
