FROM python:3.12.8-slim

# Avoid writing .pyc files
ENV PYTHONDONTWRITEBYTECODE=1
# Force stdout/stderr output unbuffered
ENV PYTHONUNBUFFERED=1

# Install required system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy the requirements file first to leverage Docker cache
COPY requirements.txt .

# Upgrade pip and install
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . /app/

# Expose the dashboard port
EXPOSE 5000

# By default, we let docker-compose override the command
CMD ["gunicorn", "-k", "eventlet", "-w", "1", "dashboard:application", "--bind", "0.0.0.0:5000", "--log-level", "info", "--timeout", "120", "--capture-output", "--enable-stdio-inheritance"]
