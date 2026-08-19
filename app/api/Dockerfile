FROM python:3.12-slim

WORKDIR /app

# Install dependencies first so this layer is cached across source changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source after deps.
COPY main.py .

# Run as a non-root user.
RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
