FROM python:3.11-slim

WORKDIR /app

# Set PYTHONPATH to include the app directory
ENV PYTHONPATH=/app

# Copy the source code (setuptools needs the packages present to build)
COPY . /app/

# Install the project and its dependencies (PEP 621 / setuptools)
RUN pip install --no-cache-dir .

# Default command (will be overridden by Docker Compose)
CMD ["python", "src/main.py"]
