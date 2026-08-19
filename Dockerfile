# NetLab: laboratorio de automatizacion de redes.
# La API usa solo la libreria estandar de Python: no hay pip install.
FROM python:3.12-slim

WORKDIR /app
COPY app/netlab.py app/ui.html ./

ENV PORT=8080 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8080

# Usuario sin privilegios: el proceso no necesita escribir en disco.
USER 10001:10001

# La imagen slim no trae curl; la verificacion de salud se hace con Python.
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s \
  CMD ["python3", "-c", "import urllib.request,os;urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT','8080')}/api/v1/health\", timeout=2)"]

CMD ["python3", "netlab.py"]
