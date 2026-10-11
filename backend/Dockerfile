# Hosted backend (Railway). Runs from the source tree so scenarios/*.json ship with it.
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir anthropic "pydantic>=2" fastapi uvicorn python-dotenv
COPY agnt911 ./agnt911
COPY serve.py .
ENV AGNT911_LLM=fake AGNT911_CLOCK=stepped
CMD uvicorn serve:app --host 0.0.0.0 --port ${PORT:-8000}
