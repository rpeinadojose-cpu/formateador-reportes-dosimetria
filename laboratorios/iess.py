# -*- coding: utf-8 -*-
"""
Parser para los informes del laboratorio del IESS
("Informe Dosimetría Personal Termoluminiscente").

Diferencias con DOSICONTROL que condicionan el diseño:

  * La tabla tiene bordes reales, así que se lee con extract_tables() en vez
    de reconstruirla por coordenadas.
  * **Cada archivo trae una sola magnitud** (cuerpo entero -> Hp(10),
    cristalino -> Hp(3), extremidades -> Hp(0,07)). Los tres PDF de un mismo
    período y sede se juntan después en un solo CSV, agrupando por usuario.
  * El período es del informe completo: no se repite por usuario, así que
    todas las filas heredan las mismas fechas.
  * La práctica y la sede salen de "DATOS DE LA INSTITUCIÓN USUARIA".
  * Las notas significan otra cosa que en DOSICONTROL: aquí NR es "dosímetro
    no retornado" (-> NE), no "no registrable".

La única marca fiable del formato es el encabezado de la tabla, que solo
cambia en la magnitud: N.- | NOMBRES Y APELLIDOS | CEDULA | CODIGO DE
DOSIMETRO | DOSIS (mSv) Hp(x) | DOSIS ANUAL ACUMULADA (mSv) Hp(x) | FIRMA
RECIBIDO.
"""
from __future__ import annotations

import os
import re

import pdfplumber

from .base import (
    Registro, UMBRALES, limpiar, normalizar_clave, practica_por_palabras,
)

NOMBRE = "IESS - Dosimetría Termoluminiscente"
CLAVE = "iess"

# Notas del informe (leyenda al pie):
#   NR = Dosímetro no Retornado, NU = Dosímetro no Usado, DD = Dosímetro Dañado
#        -> no hay lectura válida: NE
#   <LD = valor menor que el límite detectable -> se considera cero: NR
NOTAS_NE = ("NR", "NU", "DD", "DP")
NOTAS_NR = ("LD", "<LD")

RE_HP = re.compile(r"HP\s*\(\s*(10|3|0\s*[.,]\s*07)\s*\)")
RE_NUMERO = re.compile(r"^([0-9]+(?:[.,][0-9]+)?)$")

# Palabras del encabezado de tabla que identifican el formato
FIRMA_ENCABEZADO = ("NOMBRES Y APELLIDOS", "CEDULA", "DOSIMETRO", "RECIBIDO")

TIPOS = {
    "CUERPO ENTERO": "hp10",
    "CRISTALINO": "hp3",
    "EXTREMIDADES": "hp007",
}


# ------------------------------------------------------- fila de titulo
# Mismas etiquetas que los demas laboratorios: el CSV debe poder consolidarse
# sin importar de que laboratorio venga.
def fila_titulo(desde, hasta, cfg):
    return ["Lectura Desde", desde, "Lectura Hasta", hasta]


# ---------------------------------------------------------------- deteccion
def detectar(texto_pagina1: str) -> bool:
    """El encabezado de la tabla es lo unico que identifica a este formato."""
    t = normalizar_clave(texto_pagina1)
    return all(k in t for k in FIRMA_ENCABEZADO)


# ---------------------------------------------------------------- auxiliares
def _celda(x):
    return limpiar((x or "").replace("\n", " "))


def _magnitud(texto):
    """'DOSIS (mSv) Hp(0,07)' -> 'hp007'."""
    m = RE_HP.search(normalizar_clave(texto).replace(" ", ""))
    if not m:
        return None
    clave = m.group(1).replace(" ", "").replace(",", ".")
    return {"10": "hp10", "3": "hp3", "0.07": "hp007"}.get(clave)


def _magnitud_por_tipo(texto):
    """Respaldo: la linea 'Tipo: TLD-CUERPO ENTERO' del encabezado."""
    m = re.search(r"Tipo\s*:\s*(.+)", texto, re.IGNORECASE)
    if not m:
        return None
    t = normalizar_clave(m.group(1))
    for k, v in TIPOS.items():
        if k in t:
            return v
    return None


def _a_fecha(txt):
    """'01/01/25' o '01/01/2025' -> '2025/01/01'."""
    m = re.match(r"\s*(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\s*$", txt or "")
    if not m:
        return ""
    d, mes, a = m.groups()
    a = int(a)
    if a < 100:
        a += 2000
    return "%04d/%02d/%02d" % (a, int(mes), int(d))


def _periodo(texto):
    """'Lectura: Bimensual Período: 01/01/25 - 28/02/25' -> ('2025/01/01', ...)"""
    m = re.search(r"Per[ií]odo\s*:\s*([0-9/\-]+)\s*[-–—]\s*([0-9/\-]+)",
                  texto, re.IGNORECASE)
    if not m:
        return "", ""
    return _a_fecha(m.group(1)), _a_fecha(m.group(2))


def _institucion(texto, ajustes, cfg):
    """
    Lee 'DATOS DE LA INSTITUCIÓN USUARIA' y devuelve (practica, sede, avisos).

    La linea puede venir en cualquiera de los dos ordenes:
        RADIODIAGNÓSTICO - GUAYAQUIL      (practica - sede)
        GUAYAQUIL - RADIODIAGNÓSTICO      (sede - practica)

    Para no depender del orden se mira CUAL de las dos partes es una practica
    conocida; la otra es la sede. Si la duda persiste se asume "practica -
    sede", que es lo que usan los informes vistos, y queda un aviso en el log.

    Cuando la linea trae una sola parte, es la practica y la sede es la de por
    defecto.
    """
    avisos = []
    sede_defecto = ajustes.get("sede_por_defecto", "HECAM")

    m = re.search(r"INSTITUCI[OÓ]N\s+USUARIA\s*:?\s*\n\s*(.+)", texto,
                  re.IGNORECASE)
    if not m:
        return "", sede_defecto, avisos

    linea = limpiar(m.group(1))
    partes = [x.strip() for x in re.split(r"\s+[-–—]\s+", linea) if x.strip()]
    if not partes:
        return "", sede_defecto, avisos
    if len(partes) == 1:
        return practica_por_palabras(partes[0], cfg) or partes[0], sede_defecto, avisos

    conocidas = {normalizar_clave(x)
                 for x in (ajustes.get("sedes_conocidas") or [])}
    conocidas.add(normalizar_clave(sede_defecto))

    # 1) una parte es una sede que ya conocemos
    idx_sede = next((i for i, x in enumerate(partes)
                     if normalizar_clave(x) in conocidas), None)
    if idx_sede is not None:
        i_prac = 0 if idx_sede else 1
        bruta, sede = partes[i_prac], partes[idx_sede]
    else:
        # 2) una sola parte se reconoce como practica: la otra es la sede
        son_practica = [i for i, x in enumerate(partes)
                        if practica_por_palabras(x, cfg)]
        if len(son_practica) == 1:
            i_prac = son_practica[0]
            bruta = partes[i_prac]
            sede = limpiar(" ".join(x for i, x in enumerate(partes)
                                    if i != i_prac))
        else:
            # 3) ambiguo: se asume "practica - sede" y se avisa
            bruta = partes[0]
            sede = limpiar(" ".join(partes[1:]))
            avisos.append(
                "institucion usuaria %r: no se pudo decidir cual parte es la "
                "practica y cual la sede; se tomo %r como practica y %r como "
                "sede. Agregue la sede a iess.sedes_conocidas o la practica a "
                "practica_por_palabra_clave para quitar la duda"
                % (linea, bruta, sede))

    return practica_por_palabras(bruta, cfg) or bruta, sede, avisos


def _indices(encabezado):
    """Ubica las columnas por su texto, no por posicion fija."""
    idx = {}
    for i, celda in enumerate(encabezado):
        t = normalizar_clave(_celda(celda))
        if not t:
            continue
        if t.startswith("N.-") or t == "N":
            idx.setdefault("n", i)
        elif "NOMBRES" in t:
            idx.setdefault("nombre", i)
        elif "CEDULA" in t:
            idx.setdefault("cedula", i)
        elif "DOSIMETRO" in t and "CODIGO" in t:
            idx.setdefault("codigo", i)
        elif "ACUMULADA" in t:
            idx.setdefault("anual", i)
        elif "DOSIS" in t:
            idx.setdefault("dosis", i)
    return idx


def _tabla_de_datos(tablas):
    """Devuelve (tabla, fila_encabezado, indices, magnitud) o None."""
    for tabla in tablas:
        for fila in tabla:
            t = normalizar_clave(" ".join(_celda(c) for c in fila))
            if all(k in t for k in FIRMA_ENCABEZADO):
                idx = _indices(fila)
                if "dosis" in idx and "cedula" in idx:
                    return tabla, fila, idx, _magnitud(t)
    return None


def _convertir(bruto, magnitud):
    """
    Devuelve (valor_para_el_csv, observacion).

    Ojo: el 'NR' del informe del IESS significa "no retornado" y se traduce a
    NE. El 'NR' del CSV significa "por debajo del nivel de registro".
    """
    v = limpiar(bruto or "")
    if not v:
        return "", ""
    u = normalizar_clave(v)
    if u in NOTAS_NE:
        return "NE", u
    if u.lstrip("<") in NOTAS_NR:
        return "NR", ""

    m = RE_NUMERO.match(v.replace(" ", ""))
    if not m:
        return v, u          # algo inesperado: se conserva y queda anotado
    num = float(m.group(1).replace(",", "."))
    if num < UMBRALES[magnitud]:
        return "NR", ""
    return m.group(1).replace(",", "."), ""


# ------------------------------------------------------------ verificacion
def celdas_por_posicion(ruta_pdf, cfg):
    """
    Relectura independiente para verificar: toma las columnas por POSICION
    (2=cedula, 3=codigo del dosimetro, 4=dosis del periodo) en vez de ubicarlas
    por el texto del encabezado, y saca la magnitud de la linea "Tipo:" en vez
    del "Hp(x)" del encabezado de tabla.

    Devuelve {(cedula, serie): (magnitud, valor_crudo)}.
    """
    salida = {}
    with pdfplumber.open(ruta_pdf) as pdf:
        magnitud = _magnitud_por_tipo(pdf.pages[0].extract_text() or "")
        if magnitud is None:
            return salida
        for pagina in pdf.pages:
            for tabla in pagina.extract_tables():
                for fila in tabla:
                    if len(fila) < 5:
                        continue
                    cedula = _celda(fila[2])
                    serie = _celda(fila[3])
                    if not re.fullmatch(r"[0-9A-Za-z\-]{6,15}", cedula or ""):
                        continue
                    if not any(c.isdigit() for c in cedula) or not serie:
                        continue
                    salida[(cedula, serie)] = (magnitud, _celda(fila[4]))
    return salida


# ---------------------------------------------------------------- parser
def parsear(ruta_pdf: str, cfg: dict):
    registros = []
    avisos = []
    archivo = os.path.basename(ruta_pdf)

    ajustes = cfg.get("iess") or {}
    hospital = ajustes.get("hospital", "HECAM")

    with pdfplumber.open(ruta_pdf) as pdf:
        texto = "\n".join(p.extract_text() or "" for p in pdf.pages)
        practica, sede, avisos_inst = _institucion(texto, ajustes, cfg)
        avisos.extend("%s: %s" % (archivo, a) for a in avisos_inst)
        desde, hasta = _periodo(texto)
        magnitud_respaldo = _magnitud_por_tipo(texto)

        if not desde or not hasta:
            avisos.append("%s: no se pudo leer el periodo de lectura del "
                          "encabezado; las fechas quedan vacias" % archivo)
        if not practica:
            avisos.append("%s: no se pudo leer la institucion usuaria; la "
                          "practica queda vacia" % archivo)

        filas_por_pagina = []
        for n_pag, pagina in enumerate(pdf.pages, start=1):
            filas_pag = 0
            hallazgo = _tabla_de_datos(pagina.extract_tables())
            if hallazgo is None:
                filas_por_pagina.append(0)
                continue

            tabla, encabezado, idx, magnitud = hallazgo
            magnitud = magnitud or magnitud_respaldo
            if magnitud is None:
                avisos.append(
                    "%s p.%d: no se pudo saber que magnitud reporta la tabla "
                    "(ni por el encabezado ni por la linea 'Tipo:'); la pagina "
                    "se omitio" % (archivo, n_pag))
                filas_por_pagina.append(0)
                continue

            visto_encabezado = False
            n_esperado = 1
            for fila in tabla:
                if fila is encabezado:
                    visto_encabezado = True
                    continue
                if not visto_encabezado:
                    continue

                num = _celda(fila[idx["n"]]) if "n" in idx else ""
                cedula = _celda(fila[idx["cedula"]])
                if not num.isdigit() or not cedula:
                    continue

                n_fila = int(num)
                if n_fila > n_esperado:
                    avisos.append(
                        "%s p.%d: la numeracion del informe salta de la fila %d "
                        "a la %d -> faltan %d fila(s) por leer"
                        % (archivo, n_pag, n_esperado - 1, n_fila,
                           n_fila - n_esperado))
                n_esperado = n_fila + 1

                valor, obs = _convertir(_celda(fila[idx["dosis"]]), magnitud)
                reg = Registro(
                    cedula=cedula,
                    nombre=_celda(fila[idx["nombre"]]) if "nombre" in idx else "",
                    fecha_inicio=desde,
                    fecha_fin=hasta,
                    hospital=hospital,
                    sede=sede,
                    practica=practica,
                    observacion=obs,
                    laboratorio=NOMBRE,
                    serie_dosimetro=(_celda(fila[idx["codigo"]])
                                     if "codigo" in idx else ""),
                    ciclo_lectura_desde=desde,
                    ciclo_lectura_hasta=hasta,
                )
                setattr(reg, magnitud, valor)
                setattr(reg, magnitud + "_crudo", _celda(fila[idx["dosis"]]))
                registros.append(reg)
                filas_pag += 1

            filas_por_pagina.append(filas_pag)
            if filas_pag == 0:
                avisos.append("%s p.%d: se encontro la tabla pero no se leyo "
                              "ninguna fila de datos" % (archivo, n_pag))

    resumen = {
        "paginas": len(filas_por_pagina),
        "filas_por_pagina": filas_por_pagina,
        "secciones": [(practica, len(registros))] if registros else [],
    }
    return registros, avisos, resumen
