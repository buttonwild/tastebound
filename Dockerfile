# Container for Nebius Serverless Endpoints (or any container host).
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV PORT=8501
EXPOSE 8501
# Keys are injected at runtime as environment variables, never baked into the image.
CMD streamlit run app.py --server.port=${PORT} --server.address=0.0.0.0
