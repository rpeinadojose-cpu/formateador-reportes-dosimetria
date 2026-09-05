# -*- coding: utf-8 -*-
"""
Estructuras y utilidades comunes a todos los parsers de laboratorio.

Cada laboratorio se implementa como un modulo dentro de este paquete que expone:
    NOMBRE      -> str, nombre del laboratorio
    detectar(texto_pagina1: str) -> bool
    parsear(ruta_pdf: str, cfg: dict) -> list[Registro]
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, asdict, fields

# --------------------------------------------------------------------------
# Esquema
# --------------------------------------------------------------------------
# El CSV de salida lleva SOLO las columnas de CAMPOS_SALIDA. El resto de
# campos del Registro se usan internamente (nombre del archivo, deteccion de
# duplicados, trazabilidad) y no se escriben.

@dataclass
class Registro:
    # --- columnas del CSV, en este orden ---
    cedula: str = ""
    nombre: str = ""
    fecha_inicio: str = ""
    fecha_fin: str = ""
    hp10: str = ""
    hp007: str = ""
    hp3: str = ""
    hospital: str = ""
    sede: str = ""
    practica: str = ""         # del titulo de la tabla ("Area: ...")
    observacion: str = ""      # codigos de nota del informe: NC, NU, DP, DOP...
    # --- uso interno (no se escriben en el CSV) ---
    ciclo: str = ""            # etiqueta del ciclo, si el laboratorio los usa
    ciclo_num: str = ""        # numero de ciclo tal como viene en el informe
    laboratorio: str = ""
    serie_dosimetro: str = ""
    dias: str = ""
    hp10_crudo: str = ""
    hp3_crudo: str = ""
    hp007_crudo: str = ""
    ciclo_lectura_desde: str = ""
    ciclo_lectura_hasta: str = ""
    fecha_reporte: str = ""
    archivo_origen: str = ""

    def como_dict(self):
        return asdict(self)

    def fila_csv(self):
        d = asdict(self)
        return {k: d[k] for k in CAMPOS_SALIDA}


CAMPOS_SALIDA = ["cedula", "nombre", "fecha_inicio", "fecha_fin",
                 "hp10", "hp007", "hp3", "hospital", "sede", "practica",
                 "observacion"]
CAMPOS_CSV = [f.name for f in fields(Registro)]


# --------------------------------------------------------------------------
# Umbrales de registro (Acuerdo Ministerial 245, 28-feb-2015)
# Lecturas por debajo de estos valores se consideran cero -> se marcan "NR"
# --------------------------------------------------------------------------
UMBRALES = {
    "hp10": 0.1,
    "hp3": 0.6,
    "hp007": 2.0,
}

RE_FECHA = re.compile(r"^\d{4}[/-]\d{2}[/-]\d{2}$")
RE_NUM = re.compile(r"^-?\d+(?:[.,]\d+)?$")
RE_GUION = re.compile(r"^-{1,3}$")
# Codigos de nota usados por los laboratorios (nomenclatura del pie de pagina)
RE_NOTA = re.compile(r"^(?:DD|NU|NC|DP|DA|SLI|DNV|DMS|DOP|<?LD)$", re.IGNORECASE)


# --------------------------------------------------------------------------
# Utilidades de texto
# --------------------------------------------------------------------------
def limpiar(texto: str) -> str:
    """Colapsa espacios/saltos y quita caracteres invisibles."""
    if not texto:
        return ""
    for c in "\u00a0\u2007\u202f\u200b\u200c\u200d\ufeff":
        texto = texto.replace(c, " " if c != "\u200b" else "")
    return re.sub(r"\s+", " ", texto).strip()


def sin_tildes(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


def normalizar_clave(texto: str) -> str:
    """Clave comparable: sin tildes, mayusculas, espacios colapsados."""
    return re.sub(r"\s+", " ", sin_tildes(limpiar(texto)).upper()).strip()


def nombre_archivo_seguro(texto: str) -> str:
    texto = limpiar(texto)
    texto = re.sub(r'[<>:"/\|?*]', "", texto)
    texto = re.sub(r"\s+", "_", texto)
    return texto.strip("._") or "SIN_NOMBRE"


def separar_sede(nombre_cliente: str):
    """
    'HOSPITAL DE LOS VALLES (EL BOSQUE)' -> ('HOSPITAL DE LOS VALLES', 'EL BOSQUE')
    'HOSPITAL DE LOS VALLES'             -> ('HOSPITAL DE LOS VALLES', '')
    """
    nombre_cliente = limpiar(nombre_cliente)
    sedes = re.findall(r"\(([^)]*)\)", nombre_cliente)
    base = limpiar(re.sub(r"\([^)]*\)", " ", nombre_cliente))
    sede = limpiar(sedes[-1]) if sedes else ""
    return base, sede


# --------------------------------------------------------------------------
# Agrupacion de palabras de pdfplumber en lineas
# --------------------------------------------------------------------------
def agrupar_en_lineas(palabras, tolerancia=2.5):
    """
    palabras: lista de dicts de pdfplumber.extract_words()
    devuelve: lista de lineas; cada linea es una lista de palabras ordenada por x0
    """
    if not palabras:
        return []
    ordenadas = sorted(palabras, key=lambda w: (round(w["top"], 1), w["x0"]))
    lineas = []
    actual = [ordenadas[0]]
    ref = ordenadas[0]["top"]
    for w in ordenadas[1:]:
        if abs(w["top"] - ref) <= tolerancia:
            actual.append(w)
        else:
            lineas.append(sorted(actual, key=lambda x: x["x0"]))
            actual = [w]
            ref = w["top"]
    lineas.append(sorted(actual, key=lambda x: x["x0"]))
    return lineas


def texto_de(linea) -> str:
    return limpiar(" ".join(w["text"] for w in linea))


# --------------------------------------------------------------------------
# Conversion de lecturas a valor final segun las reglas del usuario
# --------------------------------------------------------------------------
def convertir_dosis(valor_crudo: str, nota: str, magnitud: str, cfg: dict) -> str:
    """
    Reglas:
      * Lectura < umbral de registro  -> 'NR'   (se considera cero)
      * Nota NC (u otras configuradas) -> 'NE'
      * '--' -> configurable (por defecto 'NR')
    """
    notas_ne = {n.upper() for n in cfg.get("notas_a_ne", ["NC"])}
    nota_u = (nota or "").upper().lstrip("<")

    if nota_u in notas_ne:
        return "NE"

    v = limpiar(valor_crudo or "")
    if v == "":
        return cfg.get("texto_sin_dato", "")
    if RE_GUION.match(v):
        return cfg.get("tratar_guiones_como", "")
    if not RE_NUM.match(v):
        # p.ej. la celda trae solo el codigo de nota
        return v.upper()

    num = float(v.replace(",", "."))
    if num < UMBRALES[magnitud]:
        return "NR"
    return v.replace(",", ".")
