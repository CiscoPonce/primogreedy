# Use a lightweight Python version
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system tools (curl/git) just in case
RUN apt-get update && apt-get install -y git curl && rm -rf /var/lib/apt/lists/*

# Copy the clean requirements first
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your app code
COPY . .

# Expose the correct port for Hugging Face
EXPOSE 7860

# The Start Command (Using python -m is safer)
CMD ["python", "-m", "chainlit", "run", "app.py", "--host", "0.0.0.0", "--port", "7860", "--headless"]