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
    hp007_izq: str = ""        # extremidad izquierda, si el informe la separa
    hp007_der: str = ""        # extremidad derecha
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
    lateralidad: str = ""      # "izq" / "der" / "" segun el subtitulo
    hp10_crudo: str = ""
    hp3_crudo: str = ""
    hp007_crudo: str = ""
    ciclo_lectura_desde: str = ""
    ciclo_lectura_hasta: str = ""
    fecha_reporte: str = ""
    archivo_origen: str = ""

    def como_dict(self):
        return asdict(self)

    def fila_csv(self, campos=None):
        d = asdict(self)
        return {k: d.get(k, "") for k in (campos or CAMPOS_SALIDA)}


CAMPOS_SALIDA = ["cedula", "nombre", "fecha_inicio", "fecha_fin",
                 "hp10", "hp007", "hp007_izq", "hp007_der", "hp3",
                 "hospital", "sede", "practica", "observacion"]
# Todo lo que se puede pedir en config.json -> "columnas".
CAMPOS_DISPONIBLES = ["cedula", "nombre", "fecha_inicio", "fecha_fin",
                      "hp10", "hp007", "hp007_izq", "hp007_der", "hp3",
                      "hospital", "sede", "practica", "observacion"]
CAMPOS_CSV = [f.name for f in fields(Registro)]


# --------------------------------------------------------------------------
# Umbrales de registro (Acuerdo Ministerial 245, 28-feb-2015)
# Lecturas por debajo de estos valores se consideran cero -> se marcan "NR"
# --------------------------------------------------------------------------
UMBRALES = {
    "hp10": 0.1,
    "hp3": 0.6,
    "hp007": 2.0,
    # la extremidad separada por lados es la misma magnitud, mismo umbral
    "hp007_izq": 2.0,
    "hp007_der": 2.0,
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


# --------------------------------------------------------------------------
# Lateralidad de la extremidad
# --------------------------------------------------------------------------
# Algunos centros piden la dosis de extremidades separada por lado, y el
# laboratorio la entrega en tablas distintas rotuladas con un subtitulo. No hay
# una forma unica de escribirlo, asi que la etiqueta se normaliza (sin tildes
# ni signos) y se busca la palabra del lado. Todas estas caen en la misma
# columna:
#
#     hp007_izq | Hp(0,07) izq | Hp(0,07) extremidad izquierda
#     Extremidad izq | Anillo izquierdo | Mano izquierda
#
LADOS = (
    ("izq", ("IZQ", "IZQD", "IZQDA", "IZQUIERDA", "IZQUIERDO",
             "IZQUIERDAS", "IZQUIERDOS", "LEFT")),
    ("der", ("DER", "DCHA", "DCHO", "DRA", "DRCHA", "DERECHA", "DERECHO",
             "DERECHAS", "DERECHOS", "RIGHT")),
)


def _palabras(texto):
    """
    Palabras del texto normalizado, separando letras de cifras.

    El laboratorio pega el contador de subseccion al rotulo ("8Mano derecha"),
    y "Hp(0,07)" debe dar HP y 007 por separado.
    """
    return set(re.findall(r"[A-Z]+|\d+", normalizar_clave(texto)))


def lateralidad_de(texto):
    """
    Devuelve "izq", "der" o "" segun el rotulo de una tabla.

    Compara palabra por palabra, no por subcadena: de lo contrario cualquier
    palabra que contenga "der" activaria el lado por accidente. Si el rotulo
    nombra los dos lados a la vez no se decide nada.
    """
    hallados = [lado for lado, claves in LADOS
                if _palabras(texto).intersection(claves)]
    return hallados[0] if len(hallados) == 1 else ""


# Partes del cuerpo (o la propia magnitud) con que se rotula la tabla. Se
# exige una de estas ADEMAS del lado: un apellido como "Izquierdo" nombra un
# lado pero no una extremidad, y no debe confundirse con un subtitulo.
PARTES_CUERPO = ("MANO", "MANOS", "ANILLO", "ANILLOS", "DEDO", "DEDOS",
                 "MUNECA", "MUNECAS", "EXTREMIDAD", "EXTREMIDADES",
                 "BRAZO", "BRAZOS", "PIE", "PIES", "PULSERA",
                 "HP", "HP007", "HP0", "007")


def rotulo_de_extremidad(texto):
    """Lado de una tabla de Hp(0.07), o "" si el texto no es ese rotulo."""
    lado = lateralidad_de(texto)
    if not lado:
        return ""
    return lado if _palabras(texto).intersection(PARTES_CUERPO) else ""


def practica_por_palabras(texto, cfg):
    """
    Normaliza la practica buscando palabras clave, sin tildes ni mayusculas.

    Cada palabra clave se busca como PREFIJO DE PALABRA, de modo que "gastro"
    reconoce "GASTROENTEROLOGIA" y "urolog" reconoce "UROLOGOS", pero "pet" no
    se dispara dentro de "COMPETENCIA". El orden de las reglas manda: las mas
    especificas van primero (p.ej. "radiologia intervencionista" cae en
    Intervencionismo, no en Radiodiagnostico).

    Devuelve None si ninguna regla aplica, para que el llamador conserve el
    texto original del informe.
    """
    t = normalizar_clave(texto)
    if not t:
        return None
    for regla in cfg.get("practica_por_palabra_clave") or []:
        for clave in regla.get("contiene") or []:
            if re.search(r"\b" + re.escape(normalizar_clave(clave)), t):
                return regla.get("practica") or texto
    return None


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
