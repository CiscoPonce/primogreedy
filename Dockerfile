# Use a lightweight Python version
FROM python:3.11-slim

# Set up the working directory
WORKDIR /app

# Copy the dependency list
COPY requirements.txt .

# Install the dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy all your code into the container
COPY . .

# Tell the container to listen on port 7860 (Hugging Face default)
ENV PORT=7860
