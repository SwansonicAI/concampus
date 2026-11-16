FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY . .
RUN SECRET_KEY=build python manage.py collectstatic --noinput \
    && useradd --create-home campus && mkdir -p media && chown campus media
USER campus
CMD ["gunicorn", "concampus.wsgi", "--bind", "0.0.0.0:8000", "--workers", "3", "--worker-class", "gthread", "--threads", "4"]
