FROM python:3.11-slim

RUN apt-get update && apt-get install -y git curl && rm -rf /var/lib/apt/lists/*

# Required by Hugging Face
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

WORKDIR /app
COPY --chown=user requirements.txt .

# No cache prevents memory spikes during installation
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user . .
EXPOSE 7860

CMD ["python", "-m", "chainlit", "run", "app.py", "--host", "0.0.0.0", "--port", "7860", "--headless"]