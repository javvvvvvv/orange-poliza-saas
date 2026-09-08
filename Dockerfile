FROM python:3.11-slim

# Evitar que python escriba .pyc y forzar stdout sin buffer
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Instalar LibreOffice (requerido para xls_writer.py) y dependencias de psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-calc \
    libreoffice-core \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Crear directorio de trabajo
WORKDIR /app

# Instalar requerimientos (primero copiamos solo txt para cachear capas)
COPY requirements.txt ./
COPY webapp/requirements.txt ./webapp/
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir -r webapp/requirements.txt

# Copiar el codigo del backend y webapp
COPY backend /app/backend
COPY database /app/database
COPY webapp /app/webapp

# Exponer el puerto
EXPOSE 8000

# Script de inicio (ejecuta Gunicorn desde /app/webapp)
WORKDIR /app/webapp
CMD ["sh", "-c", "python init_db.py && gunicorn --bind 0.0.0.0:8000 --workers 4 --timeout 300 app:app"]
