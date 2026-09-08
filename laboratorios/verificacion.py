# -*- coding: utf-8 -*-
"""
Verificacion automatica de lo extraido, contra el propio PDF.

Se ejecuta siempre, despues de leer cada informe y ANTES de fusionar los
dosimetros de un usuario, porque en ese momento cada registro corresponde
uno a uno con una fila del PDF.

Dos comprobaciones:

  1. RESPALDO EN EL TEXTO (todos los laboratorios)
     Cada dosis numerica extraida tiene que aparecer literalmente en el texto
     del PDF. Detecta valores inventados o mal formateados.

  2. CELDA A CELDA (solo los formatos con tabla con bordes)
     Se relee la tabla tomando las columnas POR POSICION, mientras que los
     parsers las ubican por el texto del encabezado. Son dos caminos
     distintos: si coinciden, la columna de la que se tomo cada dosis es la
     correcta. Es el riesgo real de estos formatos, donde una tabla trae
     Hp(10), Hp(0.07) y Hp(3) del periodo junto a las acumuladas anual y
     total, que NO deben usarse.

Lo que NO se puede comprobar asi es el formato de DOSICONTROL: su tabla no
tiene bordes y se reconstruye por coordenadas, sin un segundo camino
independiente. Ahi queda el respaldo en texto mas el control de numeracion
de filas que hace el propio parser.

Un parser ofrece la segunda comprobacion definiendo:

    celdas_por_posicion(ruta_pdf, cfg) -> {(cedula, serie): (magnitud, crudo)}
"""
from __future__ import annotations

import re

import pdfplumber

from .base import limpiar, normalizar_clave

RE_NUM = re.compile(r"^\d+(?:[.,]\d+)?$")


def _texto(ruta_pdf):
    with pdfplumber.open(ruta_pdf) as pdf:
        return " ".join(" ".join((p.extract_text() or "").split())
                        for p in pdf.pages)


def comprobar(parser, ruta_pdf, registros, cfg, etiqueta):
    """
    Devuelve (resumen, problemas).

    'resumen' es una linea para el log; 'problemas' son incidencias.
    """
    problemas = []
    if not registros:
        return "", problemas

    # ---------- 1) respaldo en el texto ----------
    texto = _texto(ruta_pdf)
    numericas = sin_respaldo = 0
    for r in registros:
        for mag in ("hp10", "hp007", "hp3"):
            crudo = limpiar(getattr(r, mag + "_crudo", "") or "")
            valor = crudo.split()[0] if crudo else ""
            if not RE_NUM.match(valor):
                continue
            numericas += 1
            if valor not in texto:
                sin_respaldo += 1
                if len(problemas) < 12:
                    problemas.append(
                        "%s: la dosis %s de la cedula %s (%s) no aparece en el "
                        "texto del PDF" % (etiqueta, valor, r.cedula, mag))

    partes = ["%d dosis con respaldo en el texto" % (numericas - sin_respaldo)]
    if sin_respaldo:
        partes[0] += " de %d" % numericas

    # ---------- 2) celda a celda ----------
    por_posicion = getattr(parser, "celdas_por_posicion", None)
    if por_posicion is None:
        partes.append("relectura por posicion: no aplica a este formato")
        return "; ".join(partes), problemas

    try:
        esperado = por_posicion(ruta_pdf, cfg)
    except Exception as exc:
        problemas.append("%s: la relectura de verificacion fallo -> %s"
                         % (etiqueta, exc))
        return "; ".join(partes), problemas

    comprobadas = discrepantes = 0
    for r in registros:
        clave = (r.cedula, r.serie_dosimetro)
        if clave not in esperado:
            continue
        mag_esp, crudo_esp = esperado[clave]
        comprobadas += 1

        # Se compara el NUMERO de la celda. Cuando la fila no trae lectura
        # (nota DNE, NC y similares) el parser deja el codigo de la nota como
        # marca interna, asi que en ese caso basta con que haya asignado la
        # misma magnitud, que es lo que se quiere comprobar.
        real_crudo = limpiar(getattr(r, mag_esp + "_crudo", "") or "")
        real = next((t for t in real_crudo.split() if RE_NUM.match(t)), "")
        esp = next((t for t in limpiar(crudo_esp).split() if RE_NUM.match(t)), "")

        if esp:
            malo = real.replace(",", ".") != esp.replace(",", ".")
            detalle = "el informe dice %s y se extrajo %s" % (esp, real or "nada")
        else:
            malo = not real_crudo          # la magnitud no quedo asignada
            detalle = ("la fila no trae lectura y la magnitud %s quedo sin "
                       "asignar" % mag_esp.upper())

        if malo:
            discrepantes += 1
            if len(problemas) < 12:
                problemas.append(
                    "%s: cedula %s dosimetro %s, columna %s -> %s"
                    % (etiqueta, r.cedula, r.serie_dosimetro,
                       mag_esp.upper(), detalle))

    if comprobadas:
        partes.append("%d celda(s) releidas por posicion%s"
                      % (comprobadas,
                         "" if not discrepantes else ", %d NO coinciden" % discrepantes))
        faltan = len(registros) - comprobadas
        if faltan > 0:
            partes.append("%d fila(s) sin contraparte en la relectura" % faltan)
            problemas.append(
                "%s: %d fila(s) extraidas no se encontraron al releer la tabla "
                "por posicion" % (etiqueta, faltan))
    else:
        partes.append("la relectura por posicion no devolvio filas")
        problemas.append("%s: la verificacion por posicion no pudo releer "
                         "ninguna fila" % etiqueta)

    return "; ".join(partes), problemas
