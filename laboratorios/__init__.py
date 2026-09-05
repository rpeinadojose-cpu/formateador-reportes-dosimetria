# -*- coding: utf-8 -*-
"""
Registro de parsers por laboratorio.

Para agregar un laboratorio nuevo:
  1. crear un modulo aqui (p.ej. laboratorios/otro_lab.py) copiando la
     estructura de dosicontrol.py: NOMBRE, detectar(texto), parsear(ruta, cfg)
  2. importarlo abajo y agregarlo a la lista PARSERS.
"""
from . import dosicontrol

PARSERS = [
    dosicontrol,
]


def identificar(texto_pagina1: str):
    """Devuelve el modulo parser que reconoce el reporte, o None."""
    for p in PARSERS:
        try:
            if p.detectar(texto_pagina1):
                return p
        except Exception:
            continue
    return None
