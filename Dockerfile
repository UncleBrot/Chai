FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (fonts for PIL caption rendering)
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Run the bot
CMD ["python", "bot.py"]
