FROM python:3.14-slim
WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home watttally && mkdir /app/data && chown watttally:watttally /app/data
USER watttally
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
