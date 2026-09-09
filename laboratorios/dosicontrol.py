# -*- coding: utf-8 -*-
"""
Parser para los informes de dosis del laboratorio DOSICONTROL S.A.S.
(formato "INFORME DE DOSIS", codigo de reporte DC-008).

Estructura del informe:
  * Encabezado: laboratorio (columna izq.), cliente/sede (columna centro),
    ciclo y fechas de lectura (columna der.).
  * Una o varias secciones "Area: <practica>", cada una con su tabla.
  * Cada fila: N, serie del dosimetro, nombre, cedula, dias, desde, hasta y
    tres bloques de tres columnas HP(10)/HP(3)/HP(0.07):
    DOSIS DEL PERIODO | ACUMULADA ANUAL | ACUMULADA ANUAL (niveles registro).
    Solo interesa el primer bloque (DOSIS DEL PERIODO).
  * Los nombres largos se parten en varias lineas y pueden continuar en la
    pagina siguiente, despues de repetirse el encabezado de la tabla.
"""
from __future__ import annotations

import os
import re

import pdfplumber

from .base import (
    Registro, RE_FECHA, RE_NUM, RE_GUION, RE_NOTA,
    agrupar_en_lineas, texto_de, limpiar, normalizar_clave,
    separar_sede, convertir_dosis, rotulo_de_extremidad,
    practica_por_palabras,
)

# Como se rotula cada lado en el log y en el resumen de secciones
ETIQUETA_LADO = {"izq": "extremidad izquierda", "der": "extremidad derecha"}

NOMBRE = "DOSICONTROL S.A.S."
CLAVE = "dosicontrol"

RE_HP = re.compile(r"^HP\(\s*(?:10|3|0[.,]07)\s*\)$", re.IGNORECASE)
# Titulos profesionales: marcan el bloque de firmas al pie del informe
RE_TITULO = re.compile(
    r"^(?:Ing|F[ií]s|M[eé]d|Dr|Dra|Lic|Mgs|Msc|Tlgo|Tglo|Arq|Abg|Econ|Blgo)\.?$",
    re.IGNORECASE)

# Lineas de encabezado (se ignoran, pero NO cierran la fila en curso:
# el encabezado de tabla se repite en cada pagina y puede partir un nombre)
RUIDO_ENCABEZADO = (
    "DATOS DEL USUARIO", "DOSIS DEL PERIODO", "DOSIS ACUMULADA",
    "SERIE DEL", "DOSIMETRO", "IDENTIFICACION", "ASIGNADOS",
    "CONSIDERANDO NIVELES", "TOTAL(MSV)", "INFORME DE DOSIS",
    "PERIODICIDAD", "VIGENCIA DE", "PERSONA DE", "CLIENTE DESDE",
    "LABORATORIO DE DOSIMETRIA", "DOSIMETRIA OPTICAMENTE",
    "CODIGO DEL REPORTE", "FECHA DEL REPORTE", "LECTURA DESDE",
    "LECTURA HASTA", "CODIGO NRO", "DIRECCION:", "RUC /", "CIUDAD:",
    "AVDA.", "EDIFICIO", "TELEFONO:", "WHASTAPP", "WWW.", "INFO@",
)

# Lineas de pie: cierran la fila en curso y terminan la pagina
RUIDO_PIE = (
    "REPRESENTANTE LEGAL", "RESPONSABLE TECNICO", "NORMA TECNICA",
    "NOMENCLATURA DE NOTAS", "LICENCIA DE OPERACION", "METODO DE ENSAYO",
    "INCERTIDUMBRE", "SE CONSIDERAN CERO", "LECTURAS SUPERIORES",
    "AUTORIDAD REGULADORA", "DETALLES DE LA DOSIS", "PLATAFORMA METRIX",
    "DOSIS ASIGNADA POR", "MEM N",
)


# ------------------------------------------------------- fila de titulo
# Etiquetas tal como las escribe DOSICONTROL en el encabezado del informe.
# Cada laboratorio define las suyas: otro formato solo tiene que exponer su
# propia fila_titulo().
ETIQUETA_DESDE = "Lectura Desde"
ETIQUETA_HASTA = "Lectura Hasta"


def fila_titulo(desde, hasta, cfg):
    """Primera fila del CSV: el periodo de lectura del informe."""
    return [ETIQUETA_DESDE, desde, ETIQUETA_HASTA, hasta]


# ---------------------------------------------------------------- deteccion
def detectar(texto_pagina1: str) -> bool:
    t = normalizar_clave(texto_pagina1)
    return "DOSICONTROL" in t or "DC-008" in t


# ---------------------------------------------------------------- auxiliares
def _es_fecha(w) -> bool:
    return bool(RE_FECHA.match(w["text"]))


def _centro(w) -> float:
    return (w["x0"] + w["x1"]) / 2.0


def _col_mas_cercana(x, rejilla):
    if not rejilla:
        return None
    return min(range(len(rejilla)), key=lambda k: abs(x - rejilla[k]))


def _es_pie(ln, txt_n, alto):
    """
    Detecta el pie de pagina (bloque de firmas y notas legales).

    El titulo profesional ("Ing.", "Fis.", ...) solo cuenta si ABRE la linea y
    esta en la parte baja de la hoja: en el encabezado puede aparecer un titulo
    dentro del texto -- p.ej. "Persona de contacto: Ing. <nombre>" -- y no
    debe cortar la lectura de la pagina.
    """
    if any(k in txt_n for k in RUIDO_PIE):
        return True
    if RE_TITULO.match(ln[0]["text"]):
        return alto <= 0 or ln[0]["top"] > 0.55 * alto
    return False


def _indice_fechas(ln):
    """Posicion de las dos fechas consecutivas DESDE / HASTA, o None."""
    for i in range(len(ln) - 1):
        if _es_fecha(ln[i]) and _es_fecha(ln[i + 1]):
            return i
    return None


def _idx_fila_datos(ln):
    """
    Devuelve el indice de la columna DESDE si la linea es una fila de datos.

    La serie puede tener 4-5 digitos (dosimetro de extremidad / cristalino) o
    9 (cuerpo entero): no se puede exigir una longitud fija.
    """
    idx = _indice_fechas(ln)
    if (idx is not None and idx >= 4
            and re.fullmatch(r"\d{1,3}", ln[0]["text"])
            and re.fullmatch(r"\d{1,4}", ln[idx - 1]["text"])
            and re.fullmatch(r"[0-9A-Za-z\-]{8,15}", ln[idx - 2]["text"])
            and re.fullmatch(r"[0-9A-Za-z\-]{3,12}", ln[1]["text"])
            and any(c.isdigit() for c in ln[1]["text"])):
        return idx
    return None


def _rejilla_dosis(linea):
    """Centros x de las columnas HP(...) del encabezado de tabla (9 columnas)."""
    hp = [w for w in linea if RE_HP.match(w["text"])]
    if len(hp) < 3:
        return None
    return [_centro(w) for w in sorted(hp, key=lambda w: w["x0"])]


def _grupos_valor(tokens):
    """
    Agrupa los tokens posteriores a HASTA en [valor, nota, centro_x].
    Una nota (NC, NU, DP...) se adhiere al valor inmediatamente anterior.
    """
    grupos = []
    for w in tokens:
        t = w["text"]
        if RE_NUM.match(t) or RE_GUION.match(t):
            grupos.append([t, "", _centro(w)])
        elif RE_NOTA.match(t):
            if grupos and grupos[-1][1] == "":
                grupos[-1][1] = t
            else:
                grupos.append(["", t, _centro(w)])
    return grupos


def _dosis_del_periodo(grupos, rejilla):
    """
    Asigna cada grupo a la columna mas cercana de la rejilla y devuelve las
    tres primeras (DOSIS DEL PERIODO: HP(10), HP(3), HP(0.07)).
    Sin rejilla utilizable, toma las tres primeras por posicion.
    """
    if not rejilla or len(rejilla) < 3:
        sel = list(grupos[:3])
    else:
        sel = [None, None, None]
        for g in grupos:
            i = _col_mas_cercana(g[2], rejilla)
            if i is not None and i < 3 and sel[i] is None:
                sel[i] = g
        if all(s is None for s in sel):
            sel = list(grupos[:3])
    sel = sel + [None] * (3 - len(sel))
    return [(g[0], g[1]) if g else ("", "") for g in sel[:3]]


def _cabecera(texto):
    def buscar(patron, defecto=""):
        m = re.search(patron, texto, re.IGNORECASE)
        return limpiar(m.group(1)) if m else defecto

    return {
        "ciclo": buscar(r"Ciclo\s*:\s*(\S+)"),
        "desde": buscar(r"Lectura\s*Desde\s*:\s*(\d{4}[/-]\d{2}[/-]\d{2})"),
        "hasta": buscar(r"Lectura\s*Hasta\s*:\s*(\d{4}[/-]\d{2}[/-]\d{2})"),
        "fecha_reporte": buscar(r"Fecha\s*del\s*reporte\s*:\s*(\d{4}[/-]\d{2}[/-]\d{2})"),
    }


def _etiqueta_ciclo(cab, cfg):
    """
    Etiqueta del ciclo, por defecto "<anio>-C<numero>" (p.ej. 2024-C2).

    Los ciclos son bimensuales y algunos cruzan el cambio de anio; el anio se
    toma de la fecha de fin del ciclo ('fin', por defecto) o la de inicio
    ('inicio'), segun la clave 'anio_del_ciclo'.
    """
    numero = cab.get("ciclo", "")
    fuente = "desde" if cfg.get("anio_del_ciclo", "fin") == "inicio" else "hasta"
    fecha = cab.get(fuente) or cab.get("hasta") or cab.get("desde") or ""
    anio = fecha[:4]
    if not numero:
        return anio
    if not anio:
        return numero
    return cfg.get("formato_ciclo", "{anio}-C{numero}").format(
        anio=anio, numero=numero)


def _cliente(lineas):
    """
    Reconstruye el bloque de cliente (columna central del encabezado) y
    devuelve (razon_social, nombre_comercial).
    """
    x_der = None
    for ln in lineas:
        for w in ln:
            if w["text"] in ("Lectura", "Ciclo:"):
                x_der = w["x0"] if x_der is None else min(x_der, w["x0"])
    if x_der is None:
        x_der = float("inf")

    i_cli = x_cli = None
    for i, ln in enumerate(lineas):
        for w in ln:
            if w["text"].rstrip(":").upper() == "CLIENTE":
                i_cli, x_cli = i, w["x0"]
                break
        if i_cli is not None:
            break
    if i_cli is None:
        return "", ""

    bloque = []
    for ln in lineas[i_cli:i_cli + 12]:
        pal = [w for w in ln if x_cli - 2 <= w["x0"] < x_der]
        if not pal:
            continue
        txt = limpiar(" ".join(w["text"] for w in pal))
        txt = re.sub(r"^Cliente\s*:\s*", "", txt, flags=re.IGNORECASE)
        if normalizar_clave(txt).startswith(("CODIGO NRO", "DIRECCION", "RUC", "CIUDAD")):
            break
        if txt:
            bloque.append(txt)

    if not bloque:
        return "", ""

    # unir fragmentos partidos por salto de linea dentro de un parentesis
    fus = []
    for t in bloque:
        if fus and fus[-1].count("(") > fus[-1].count(")"):
            fus[-1] = fus[-1] + " " + t
        else:
            fus.append(t)

    return fus[0], (fus[-1] if len(fus) > 1 else fus[0])


# ------------------------------------------------------------ verificacion
# La tabla de DOSICONTROL no tiene bordes, asi que el parser la reconstruye
# por coordenadas. Para verificar hace falta un camino ajeno a ese: se usa
# pypdf, que es otra libreria y otro algoritmo de extraccion, y saca las filas
# como texto plano ya ordenado.
RE_FILA_TEXTO = re.compile(
    r"(\d{8,13})\s+(\d{1,4})\s+(\d{4}/\d{2}/\d{2})\s+(\d{4}/\d{2}/\d{2})\s+(.*)$")


def celdas_independientes(ruta_pdf, cfg):
    """
    Relee el informe con pypdf y devuelve, por usuario y periodo, la terna de
    dosis DEL PERIODO de cada una de sus filas:

        {(cedula, desde, hasta): [(hp10, hp3, hp007), ...]}

    En el texto plano, tras las dos fechas vienen los nueve valores en orden:
    DOSIS DEL PERIODO, ACUMULADA ANUAL y ACUMULADA ANUAL con niveles de
    registro. Solo interesan los tres primeros, en el orden Hp(10), Hp(3),
    Hp(0.07) que usa el informe.
    """
    import pypdf

    salida = {}
    lector = pypdf.PdfReader(ruta_pdf)
    for pagina in lector.pages:
        for linea in (pagina.extract_text() or "").splitlines():
            m = RE_FILA_TEXTO.search(linea.strip())
            if not m:
                continue
            cedula, _dias, desde, hasta, resto = m.groups()
            valores = []
            for tok in resto.split():
                if RE_NUM.match(tok) or RE_GUION.match(tok):
                    valores.append(tok)
                elif RE_NOTA.match(tok):
                    continue          # las notas no ocupan columna
                else:
                    break             # texto inesperado: se corta la fila
            terna = tuple(valores[i] if i < len(valores) else ""
                          for i in range(3))
            salida.setdefault((cedula, desde, hasta), []).append(terna)
    return salida


def _abre_tabla(lineas, i, salto=3):
    """
    Una tabla arranca en la linea i o poco despues, con solo lineas cortas
    entremedio.

    Se miran varias lineas porque el subtitulo de la subseccion no siempre cabe
    en una: el nombre del servicio puede ocupar dos renglones y el codigo que a
    veces lo acompana ("1SQ") puede ir en cualquiera de ellos, o no estar.
    """
    for j in range(i, min(i + salto, len(lineas))):
        t = normalizar_clave(texto_de(lineas[j]))
        if t.startswith("DOSIS ACUMULADA") or t.startswith("DATOS DEL USUARIO"):
            return True
        if len(lineas[j]) > 8 or _idx_fila_datos(lineas[j]) is not None:
            return False
    return False


# ---------------------------------------------------------------- parser
def parsear(ruta_pdf: str, cfg: dict):
    registros = []
    avisos = []

    with pdfplumber.open(ruta_pdf) as pdf:
        # ---------- encabezado (pagina 1) ----------
        p1 = pdf.pages[0]
        cab = _cabecera(p1.extract_text() or "")
        razon, comercial = _cliente(agrupar_en_lineas(p1.extract_words()))
        hospital, sede = separar_sede(comercial or razon)

        if not sede:
            defectos = {normalizar_clave(k): v
                        for k, v in cfg.get("sede_por_defecto", {}).items()}
            sede = defectos.get(normalizar_clave(hospital),
                                cfg.get("sede_por_defecto_general", "MATRIZ"))

        mapa_practica = {normalizar_clave(k): v
                         for k, v in cfg.get("mapa_practica", {}).items()}
        etiqueta_ciclo = _etiqueta_ciclo(cab, cfg)
        toda_la_fila = cfg.get("nota_aplica_a_toda_la_fila", True)

        practica = ""
        lateralidad = ""     # lado vigente: "izq", "der" o "" (sin separar)
        sub_pendiente = []   # lineas del subtitulo leido, aun sin filas
        x_nombre = None      # donde empieza la columna del nombre
        rejilla = None
        pend = None          # fila en curso (puede continuar en otra pagina)
        n_esperado = 1       # numero de fila esperado dentro de la seccion
        secciones = []       # [{nombre, leidas, max}] para verificar completitud
        filas_por_pagina = []
        archivo = os.path.basename(ruta_pdf)

        def cerrar():
            """Cierra la fila en curso: arma el nombre, aplica notas y convierte."""
            nonlocal pend
            if pend is None:
                return
            reg = pend["reg"]
            reg.nombre = limpiar(" ".join(pend["partes"]))

            v10, n10 = pend["val"][0]
            v3, n3 = pend["val"][1]
            v007, n007 = pend["val"][2]

            # las columnas *_crudo guardan lo que dice literalmente el informe
            reg.hp10_crudo = limpiar(v10 + " " + n10)
            reg.hp3_crudo = limpiar(v3 + " " + n3)
            reg.hp007_crudo = limpiar(v007 + " " + n007)

            notas = list(dict.fromkeys(
                [n.upper() for n in (n10, n3, n007) if n] + pend["notas"]))
            reg.observacion = " ".join(notas)
            if toda_la_fila and notas:
                n10 = n3 = n007 = notas[0]

            reg.hp10 = convertir_dosis(v10, n10, "hp10", cfg)
            reg.hp3 = convertir_dosis(v3, n3, "hp3", cfg)
            # si el informe separo la extremidad por lados, la dosis va a la
            # columna de su lado y hp007 queda vacia. hp007_crudo conserva el
            # valor literal en los tres casos, porque es lo que se contrasta
            # contra la segunda lectura del PDF.
            columna = {"izq": "hp007_izq", "der": "hp007_der"}.get(
                reg.lateralidad, "hp007")
            setattr(reg, columna, convertir_dosis(v007, n007, columna, cfg))

            registros.append(reg)
            pend = None

        # ---------- filas ----------
        for n_pag, pagina in enumerate(pdf.pages, start=1):
            lineas = agrupar_en_lineas(pagina.extract_words())
            alto = pagina.height or 0
            filas_pagina = 0
            tabla_en_pagina = False

            for i_ln, ln in enumerate(lineas):
                txt_n = normalizar_clave(texto_de(ln))
                if not txt_n:
                    continue

                # -- pie de pagina: cierra la fila y termina la pagina
                if _es_pie(ln, txt_n, alto):
                    cerrar()
                    sin_leer = sum(1 for r in lineas[i_ln + 1:]
                                   if _idx_fila_datos(r) is not None)
                    if sin_leer:
                        avisos.append(
                            "%s p.%d: la lectura de la pagina se corto al tomar "
                            "esta linea como pie de pagina -> %r, y quedaron %d "
                            "fila(s) de datos sin leer"
                            % (archivo, n_pag, texto_de(ln)[:90], sin_leer))
                    break

                # -- encabezado de tabla: captura la rejilla de columnas de dosis
                rej = _rejilla_dosis(ln)
                if rej:
                    tabla_en_pagina = True
                    # una rejilla parcial (p.ej. una linea de leyenda con
                    # "Hp(10) ... Hp(3) ... Hp(0.07)") no debe sustituir a la
                    # rejilla completa de 9 columnas ya detectada
                    if not (rejilla and len(rejilla) >= 6 and len(rej) < 6):
                        rejilla = rej
                    continue

                # -- seccion de practica ("Area: ...")
                if txt_n.startswith("AREA:"):
                    cerrar()
                    corte = next((w["x0"] for w in ln
                                  if w["text"].upper().startswith("DOSIS")), None)
                    pal = [w for w in ln if corte is None or w["x0"] < corte]
                    bruto = texto_de(pal).split(":", 1)[-1].strip(" .")
                    practica = mapa_practica.get(normalizar_clave(bruto), bruto)
                    lateralidad = ""
                    sub_pendiente = []
                    n_esperado = 1
                    secciones.append({"nombre": practica, "leidas": 0, "max": 0})
                    continue

                # -- fila de datos: dos fechas consecutivas (DESDE / HASTA)
                idx = _idx_fila_datos(ln)

                if idx is not None:
                    cerrar()
                    grupos = _grupos_valor(ln[idx + 2:])
                    if len(grupos) < 3:
                        avisos.append(
                            "%s p.%d: fila %s (cedula %s) solo trae %d valor(es) "
                            "de dosis, se esperaban 3"
                            % (archivo, n_pag, ln[0]["text"],
                               ln[idx - 2]["text"], len(grupos)))

                    # El informe numera las filas 1..N dentro de cada seccion:
                    # un salto significa que alguna fila no se pudo leer.
                    n_fila = int(ln[0]["text"])
                    if n_fila == 1:
                        # volver a la fila 1 significa que empieza otra tabla
                        # dentro de la misma area, no que falte informacion.
                        # Aqui se resuelve el subtitulo acumulado: puede ocupar
                        # varias lineas, asi que se juzga el texto completo.
                        rotulo = limpiar(" ".join(sub_pendiente))
                        sub_pendiente = []
                        lateralidad = rotulo_de_extremidad(rotulo)
                        if not lateralidad and rotulo:
                            # el subtitulo nombra el servicio del cliente; si se
                            # reconoce como practica, precisa la del area, y si
                            # no (p.ej. "CENTRO QUIRURGICO") se respeta el area
                            nueva = (mapa_practica.get(normalizar_clave(rotulo))
                                     or practica_por_palabras(rotulo, cfg))
                            if nueva:
                                practica = nueva
                        if secciones and secciones[-1]["leidas"]:
                            secciones.append({"nombre": practica,
                                              "leidas": 0, "max": 0})
                        elif secciones:
                            secciones[-1]["nombre"] = practica
                    elif n_fila > n_esperado:
                        avisos.append(
                            "%s p.%d: seccion '%s': la numeracion del informe "
                            "salta de la fila %d a la %d -> faltan %d fila(s) "
                            "por leer" % (archivo, n_pag, practica or "(sin titulo)",
                                          n_esperado - 1, n_fila,
                                          n_fila - n_esperado))
                    elif n_fila < n_esperado:
                        avisos.append(
                            "%s p.%d: seccion '%s': la numeracion retrocede de la "
                            "fila %d a la %d (seccion sin titulo o fila repetida)"
                            % (archivo, n_pag, practica or "(sin titulo)",
                               n_esperado - 1, n_fila))
                    n_esperado = n_fila + 1

                    if not secciones:
                        secciones.append({"nombre": practica, "leidas": 0, "max": 0})
                    if lateralidad:
                        secciones[-1]["nombre"] = "%s (%s)" % (
                            practica, ETIQUETA_LADO[lateralidad])
                    secciones[-1]["leidas"] += 1
                    secciones[-1]["max"] = max(secciones[-1]["max"], n_fila)
                    filas_pagina += 1

                    reg = Registro(
                        hospital=hospital, sede=sede,
                        cedula=ln[idx - 2]["text"],
                        ciclo=etiqueta_ciclo,
                        ciclo_num=cab["ciclo"],
                        fecha_inicio=ln[idx]["text"],
                        fecha_fin=ln[idx + 1]["text"],
                        practica=practica,
                        lateralidad=lateralidad,
                        laboratorio=NOMBRE,
                        serie_dosimetro=ln[1]["text"],
                        dias=ln[idx - 1]["text"],
                        ciclo_lectura_desde=cab["desde"],
                        ciclo_lectura_hasta=cab["hasta"],
                        fecha_reporte=cab["fecha_reporte"],
                    )
                    if idx > 2:
                        x_nombre = ln[2]["x0"]
                    pend = {
                        "reg": reg,
                        "x_lim": ln[idx - 1]["x0"] - 2,
                        "partes": [w["text"] for w in ln[2:idx - 2]],
                        "val": _dosis_del_periodo(grupos, rejilla),
                        "notas": [],
                    }
                    continue

                # -- encabezado repetido: se ignora sin cerrar la fila en curso
                if any(k in txt_n for k in RUIDO_ENCABEZADO):
                    continue

                # -- subtitulo de subseccion ("Mano izquierda", "9SQ CICLOTRON")
                # El informe abre varias tablas dentro de un area y reinicia la
                # numeracion en cada una. Hay que reconocer el subtitulo aunque
                # no diga nada del lado: si no, la linea se pega al nombre de la
                # persona de la fila anterior. El lado, cuando lo hay, queda en
                # espera y lo adopta la primera fila de la tabla que sigue.
                # Se reconoce por dos caminos, y basta con uno:
                #   - por geometria: arranca a la izquierda de la columna del
                #     nombre (una continuacion de nombre nace dentro de ella) y
                #     la tabla empieza justo despues;
                #   - por texto: nombra una extremidad y un lado. Hace falta
                #     porque el subtitulo puede quedar al pie de la pagina, con
                #     su tabla ya en la hoja siguiente, y ahi no hay geometria
                #     que mirar.
                a_la_izquierda = (x_nombre is not None
                                  and ln[0]["x0"] < x_nombre - 10)
                if (rotulo_de_extremidad(txt_n)
                        or (a_la_izquierda and len(ln) <= 8
                            and _abre_tabla(lineas, i_ln + 1))):
                    cerrar()
                    sub_pendiente.append(texto_de(ln))
                    continue

                # -- continuacion del nombre (y notas sueltas) de la fila en curso
                if pend is not None:
                    x_lim = pend["x_lim"]
                    nombre = [w["text"] for w in ln
                              if w["x1"] <= x_lim
                              and re.search(r"[^\W\d_]", w["text"], re.UNICODE)]
                    notas = [w["text"].upper() for w in ln
                             if w["x0"] > x_lim and RE_NOTA.match(w["text"])
                             and (_col_mas_cercana(_centro(w), rejilla) or 0) < 3]
                    if nombre:
                        pend["partes"].extend(nombre)
                    pend["notas"].extend(notas)
                    if not nombre and not notas:
                        cerrar()

            cerrar()
            filas_por_pagina.append(filas_pagina)
            if tabla_en_pagina and filas_pagina == 0:
                avisos.append(
                    "%s p.%d: la pagina trae encabezado de tabla pero no se leyo "
                    "ninguna fila de datos" % (archivo, n_pag))

        # ---------- verificacion de completitud por seccion ----------
        for s_ in secciones:
            if s_["max"] and s_["leidas"] != s_["max"]:
                avisos.append(
                    "%s: seccion '%s': el informe numera hasta la fila %d pero "
                    "solo se leyeron %d -> faltan %d"
                    % (archivo, s_["nombre"] or "(sin titulo)", s_["max"],
                       s_["leidas"], s_["max"] - s_["leidas"]))

        resumen = {
            "paginas": len(pdf.pages),
            "filas_por_pagina": filas_por_pagina,
            "secciones": [(x["nombre"], x["leidas"]) for x in secciones],
        }

    return registros, avisos, resumen
