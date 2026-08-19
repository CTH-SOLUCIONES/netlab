# NetLab: laboratorio de automatizacion de redes.
# La API usa solo la libreria estandar de Python: no hay pip install.
FROM python:3.12-slim

# Vincula el paquete de ghcr.io con el repositorio y su documentacion.
LABEL org.opencontainers.image.source="https://github.com/CTH-SOLUCIONES/netlab" \
      org.opencontainers.image.description="Laboratorio de automatizacion de redes: dispositivos Cisco simulados por API REST" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app
COPY app/netlab.py app/ui.html ./

# El estado del laboratorio se persiste en SQLite dentro de /data; montar un
# volumen ahi hace que sobreviva a recrear el contenedor.
RUN mkdir /data && chown 10001:10001 /data
VOLUME /data

ENV PORT=8080 \
    NETLAB_DB=/data/netlab.db \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8080

# Usuario sin privilegios: solo escribe la base de datos en /data.
USER 10001:10001

# La imagen slim no trae curl; la verificacion de salud se hace con Python.
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s \
  CMD ["python3", "-c", "import urllib.request,os;urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT','8080')}/api/v1/health\", timeout=2)"]

CMD ["python3", "netlab.py"]
