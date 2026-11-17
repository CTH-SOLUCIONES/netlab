"""Guiones de Playwright para la interfaz (B1, B2 e I6).

Necesitan navegador y no corren en CI: si playwright no esta instalado, o si
falta su Chromium, se omiten solos. Para correrlos en local:

    pip install playwright && python3 -m playwright install chromium
    python3 -m unittest discover -s tests -v
"""
