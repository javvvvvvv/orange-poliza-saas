FROM python:3.11-slim

# Evitar que python escriba .pyc, forzar stdout sin buffer y definir PYTHONPATH completo
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app:/app/backend:/app/backend/app:/app/backend/app/core:/app/backend/app/importers:/app/backend/app/exporters:/app/backend/app/cfdi:/app/webapp

# Instalar LibreOffice (requerido para xls_writer.py) y dependencias de psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-calc \
    libreoffice-core \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Crear usuario sin privilegios de root
RUN groupadd -r orange && useradd -r -g orange -d /app -s /sbin/nologin orange

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

# Preparar directorios de escritura y asignar permisos al usuario no-root
RUN mkdir -p /app/webapp/uploads/_tmp /app/backend/app/core/ml_models && \
    chown -R orange:orange /app

USER orange

# Exponer el puerto
EXPOSE 8000

# Script de inicio (ejecuta Gunicorn desde /app/webapp)
WORKDIR /app/webapp
CMD ["sh", "-c", "python init_db.py && gunicorn --bind 0.0.0.0:8000 --workers 4 --timeout 900 app:app"]
