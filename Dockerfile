FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt requirements.lock.txt ./
RUN pip install --no-cache-dir -c requirements.lock.txt -r requirements.txt
RUN useradd --uid 10001 --create-home scena
COPY --chown=scena:scena . .
RUN mkdir -p /app/storage /app/media && chown -R scena:scena /app
USER scena
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
