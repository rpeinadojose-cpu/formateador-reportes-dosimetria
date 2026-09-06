# -*- coding: utf-8 -*-
"""
Encabezado de pantalla: logotipo de FISIQA en ASCII.

Se dibuja SOLO en la consola. El archivo lector_dosis.log conserva el
encabezado corto de texto, para no llenarlo de arte en cada corrida.

Todo el arte es ASCII de 7 bits a proposito: la consola de Windows con la
codificacion heredada convierte cualquier caracter fuera de esa tabla en "?".
El color se manda con secuencias ANSI y solo si la consola las entiende.
"""
from __future__ import annotations

import os
import sys

ANCHO = 74

# Logotipo reescalado desde el banner original de 300 columnas.
LOGO = [
    " ###########..:##.   .:#::::#::  .####.     .::::::::.          .::.",
    ".###########..#### .:##########  .####.  .::::::::::::::       :####.",
    ".####:::::::. :##. .####. .::#:  .####. .#:::########::::     ..####:",
    ".###: ...... .#### .#####::...   :####. ::::###:..####:.#.   :##.####:",
    ".###:.#####.  ####  .#########:  :####..#.:###.    ###::::  :###:.####:",
    ":###:.#:###.  ####  . . .::##### :####. #:.:##:....###..#. .#####:.####.",
    ":###:         #### .##:....:#### :####. .#:::#:###::#:::: .:###::. :####.",
    ".###:        .#### .##########:. :####.  .:::.:####..::. :####:     :####.",
    " ....        ....:  ..:##:#:..   ......      .#####:     .::::      ......",
    "                                           .::######:.",
]

# Columnas que ocupa la Q (el anillo del gantry): se pintan en turquesa.
Q_INI, Q_FIN = 39, 56

LEMA = "Cuidamos con calidad, protegemos con ciencia"

# Colores del logotipo, en la paleta de 256
AZUL = "\033[38;5;25m"      # azul del logotipo
TURQUESA = "\033[38;5;37m"  # turquesa del anillo
TENUE = "\033[38;5;245m"
FIN = "\033[0m"


# ---------------------------------------------------------------- color
def _habilitar_vt():
    """En Windows hay que pedirle a la consola que interprete ANSI."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        manejador = k32.GetStdHandle(-11)          # STD_OUTPUT_HANDLE
        modo = ctypes.c_uint32()
        if not k32.GetConsoleMode(manejador, ctypes.byref(modo)):
            return False
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return bool(k32.SetConsoleMode(manejador, modo.value | 0x0004))
    except Exception:
        return False


def hay_color(preferencia="auto"):
    """
    Decide si se usa color. 'auto' (por defecto) lo activa solo si la salida
    es una consola de verdad que entiende ANSI; 'siempre' y 'nunca' lo fuerzan.

    Se respeta la convencion NO_COLOR y TERM=dumb.
    """
    pref = str(preferencia).strip().lower()
    if pref in ("nunca", "no", "false", "0"):
        return False
    if pref in ("siempre", "si", "true", "1"):
        _habilitar_vt()            # se intenta, pero manda la preferencia
        return True

    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    try:
        if not sys.stdout.isatty():
            return False           # redirigido a un archivo o a una tuberia
    except Exception:
        return False
    return _habilitar_vt()


def _pintar(linea):
    """Azul el logotipo, turquesa la Q."""
    l = linea.ljust(ANCHO)
    izq, anillo, der = l[:Q_INI], l[Q_INI:Q_FIN + 1], l[Q_FIN + 1:]
    partes = []
    for texto, tono in ((izq, AZUL), (anillo, TURQUESA), (der, AZUL)):
        partes.append(tono + texto + FIN if texto.strip() else texto)
    return "".join(partes).rstrip()


# ---------------------------------------------------------------- banner
def banner(color=False):
    """Lineas del encabezado de pantalla. No va al archivo de log."""
    lineas = ["=" * ANCHO]
    lineas.extend(_pintar(l) if color else l for l in LOGO)
    lema = LEMA.center(ANCHO).rstrip()
    lineas.append(TENUE + lema + FIN if color else lema)
    return lineas          # el "=" de cierre lo pone el encabezado corto
