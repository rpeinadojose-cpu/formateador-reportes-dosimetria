# -*- coding: utf-8 -*-
"""
Verificacion automatica de lo extraido, contra el propio PDF.

Se ejecuta siempre, despues de leer cada informe y ANTES de fusionar los
dosimetros de un usuario, porque en ese momento cada registro corresponde
uno a uno con una fila del informe.

Dos comprobaciones:

  1. RESPALDO EN EL TEXTO (todos los laboratorios)
     Cada dosis numerica extraida tiene que aparecer literalmente en el texto
     del PDF.

  2. SEGUNDA LECTURA (todos los laboratorios)
     El informe se relee por un camino ajeno al que uso el parser y se
     comparan las dosis DEL PERIODO fila por fila:

       - DOSICONTROL reconstruye su tabla por coordenadas, porque no tiene
         bordes; la segunda lectura usa pypdf, otra libreria y otro algoritmo,
         que devuelve las filas como texto plano.
       - IESS y DOSISRAD ubican las columnas por el texto del encabezado; la
         segunda lectura las toma por POSICION.

     Lo que se protege es de que la dosis salga de la columna equivocada: los
     tres formatos ponen la dosis del periodo al lado de las acumuladas anual
     y total, que NO deben usarse.

La comparacion es por FILA COMPLETA, es decir la terna (Hp(10), Hp(3),
Hp(0.07)), agrupando por (cedula, desde, hasta). Se compara como multiconjunto
porque una persona puede llevar varios dosimetros en el mismo periodo y el
orden de las filas no tiene por que coincidir entre las dos lecturas.

Un parser ofrece la segunda lectura definiendo:

    celdas_independientes(ruta_pdf, cfg)
        -> {(cedula, desde, hasta): [ (hp10, hp3, hp007), ... ]}

donde cada terna son los valores CRUDOS de una fila; lo que no sea un numero
se ignora al comparar.
"""
from __future__ import annotations

import re

import pdfplumber

from .base import limpiar

RE_NUM = re.compile(r"^\d+(?:[.,]\d+)?$")


def _numero(crudo):
    """Deja solo el numero de una celda; '--', notas y vacios dan ''."""
    for tok in limpiar(crudo or "").split():
        if RE_NUM.match(tok):
            return tok.replace(",", ".")
    return ""


def _texto(ruta_pdf):
    with pdfplumber.open(ruta_pdf) as pdf:
        return " ".join(" ".join((p.extract_text() or "").split())
                        for p in pdf.pages)


def comprobar(parser, ruta_pdf, registros, cfg, etiqueta):
    """Devuelve (resumen_para_el_log, incidencias)."""
    problemas = []
    if not registros:
        return "", problemas

    # ---------- 1) respaldo en el texto ----------
    texto = _texto(ruta_pdf)
    numericas = sin_respaldo = 0
    for r in registros:
        for mag in ("hp10", "hp007", "hp3"):
            valor = _numero(getattr(r, mag + "_crudo", ""))
            if not valor:
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

    # ---------- 2) segunda lectura ----------
    segunda = getattr(parser, "celdas_independientes", None)
    if segunda is None:
        partes.append("segunda lectura: no disponible para este formato")
        return "; ".join(partes), problemas

    try:
        esperado = segunda(ruta_pdf, cfg)
    except Exception as exc:
        problemas.append("%s: la segunda lectura fallo -> %s" % (etiqueta, exc))
        return "; ".join(partes), problemas

    # lo extraido, agrupado igual que la segunda lectura
    real = {}
    for r in registros:
        clave = (r.cedula, r.fecha_inicio, r.fecha_fin)
        terna = (_numero(r.hp10_crudo), _numero(r.hp3_crudo),
                 _numero(r.hp007_crudo))
        real.setdefault(clave, []).append(terna)

    filas = discrepantes = 0
    for clave, ternas in real.items():
        if clave not in esperado:
            continue
        filas += len(ternas)
        esp = sorted(tuple(_numero(v) for v in t) for t in esperado[clave])
        obt = sorted(ternas)
        if esp != obt:
            discrepantes += 1
            if len(problemas) < 12:
                problemas.append(
                    "%s: cedula %s (%s a %s) -> el informe dice %s y se "
                    "extrajo %s, en Hp(10)/Hp(3)/Hp(0.07)"
                    % (etiqueta, clave[0], clave[1], clave[2], esp, obt))

    sin_contraparte = len(real) - sum(
        1 for c in real if c in esperado)
    if sin_contraparte:
        problemas.append(
            "%s: %d usuario(s) que se extrajeron no aparecen en la segunda "
            "lectura" % (etiqueta, sin_contraparte))

    if filas:
        partes.append("%d fila(s) contrastadas con una segunda lectura%s"
                      % (filas, "" if not discrepantes
                         else ", %d NO coinciden" % discrepantes))
    else:
        partes.append("la segunda lectura no devolvio filas")
        problemas.append("%s: la segunda lectura no pudo leer ninguna fila"
                         % etiqueta)

    return "; ".join(partes), problemas
