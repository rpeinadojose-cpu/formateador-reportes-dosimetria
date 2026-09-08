# -*- coding: utf-8 -*-
"""
Parser para los informes de DOSISRAD S.A.
("LABORATORIO DE DOSIMETRIA PERSONAL - Dosimetria Termoluminiscente TLD").

Rasgos del formato:

  * La tabla tiene bordes reales, asi que se lee con extract_tables(). En
    texto plano las dosis salen desalineadas de su fila y no sirve.
  * Un solo informe por departamento, con las TRES magnitudes en columnas
    separadas: Hp(10), Hp(007) y Hp(3), mas una columna de Notas.
  * Cada fila es un dosimetro. El tipo dice que magnitud mide:
    CE cuerpo entero y AMB ambiental -> Hp(10); AL anillo y BR brazalete ->
    Hp(0.07); CR cristalino -> Hp(3). Un usuario con varios dosimetros
    aparece en varias filas y se fusiona despues.
  * Las fechas del periodo vienen POR FILA (Desde / Hasta). El periodo del
    informe se toma como el par de fechas mas repetido: quien se incorpora
    tarde, o a quien se le leen dos periodos juntos, trae fechas propias.
  * El encabezado no numera el informe, pero la columna "Periodo" trae el
    numero de medida dentro del anio, que se anexa a observacion.

Nomenclatura de notas del propio informe:
    DD   Dosimetro danado          DP    Dosimetro perdido por usuario
    DNU  Dosimetro no utilizado    DNE   Dosimetro no entregado por usuario
    UNL  Usuario dejo de laborar   PEM   Persona embarazada
    DPI  Dosis para investigacion  DMS   Dosis modificada SCAN
    DE2P Se leen dos o mas periodos
    <LD  Valor menor al limite de deteccion
"""
from __future__ import annotations

import os
import re

import pdfplumber

from .base import (
    Registro, UMBRALES, limpiar, normalizar_clave, practica_por_palabras,
)

NOMBRE = "DOSISRAD S.A."
CLAVE = "dosisrad"

# Notas que significan que NO hubo lectura valida -> NE
NOTAS_NE = ("DD", "DP", "DNU", "DNE", "UNL")
# Notas que solo informan: la lectura sigue siendo valida
NOTAS_INFORMATIVAS = ("DE2P", "DPI", "DMS", "PEM", "CA")
# Menor al limite de deteccion -> se considera cero -> NR
NOTAS_NR = ("LD", "<LD")

# Que magnitud mide cada tipo de dosimetro
MAGNITUD_POR_TIPO = {
    "CE": "hp10",     # cuerpo entero
    "AMB": "hp10",    # ambiental
    "CR": "hp3",      # cristalino
    "AL": "hp007",    # anillo
    "BR": "hp007",    # brazalete
}

RE_SERIE = re.compile(r"^[A-Z]{2}\d{6,8}$")
RE_NUMERO = re.compile(r"^([0-9]+(?:[.,][0-9]+)?)$")
RE_FECHA_DMA = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$")

# Palabras del encabezado de la tabla de datos
FIRMA_TABLA = ("SERIE DEL", "NOMBRE DEL USUARIO", "CEDULA")


# ------------------------------------------------------- fila de titulo
def fila_titulo(desde, hasta, cfg):
    return ["Lectura Desde", desde, "Lectura Hasta", hasta]


# ---------------------------------------------------------------- deteccion
def detectar(texto_pagina1: str) -> bool:
    t = normalizar_clave(texto_pagina1)
    return "DOSISRAD" in t


# ---------------------------------------------------------------- auxiliares
def _celda(x):
    return limpiar((x or "").replace("\n", " "))


def _fecha(txt):
    """'12/04/2026' -> '2026/04/12'."""
    m = RE_FECHA_DMA.match(txt or "")
    if not m:
        return ""
    d, mes, a = m.groups()
    return "%s/%02d/%02d" % (a, int(mes), int(d))


def _cabecera(texto):
    def buscar(patron, defecto=""):
        m = re.search(patron, texto, re.IGNORECASE)
        return limpiar(m.group(1)) if m else defecto

    return {
        "codigo": buscar(r"C[oó]digo\s*Nro\s*:?\s*(\S+)"),
        "nombre": buscar(r"\bNombre\s*:\s*(.+)"),
        "ciudad": buscar(r"\bCiudad\s*:\s*(.+)"),
    }


def _centro_y_sede(cab, ajustes):
    """
    Devuelve (centro, sede).

    OJO: el encabezado RECORTA el nombre al ancho de su casilla. En el informe
    de SOLCA Manabi el texto termina literalmente en "... DE MANABI - NUC": la
    sede queda cortada y no hay forma de recuperarla del PDF. Por eso el centro
    se toma hasta el guion y la sede se toma de "Ciudad", que si viene entera.

    Para nombres mas cortos o mas claros, "dosisrad.centros" permite fijar
    centro y sede por "Codigo Nro", que es estable para cada institucion.
    """
    fijo = (ajustes.get("centros") or {}).get(cab.get("codigo", ""))
    if fijo:
        return (limpiar(fijo.get("hospital", "")),
                limpiar(fijo.get("sede", "")))

    nombre = limpiar(cab.get("nombre", ""))
    ciudad = limpiar(cab.get("ciudad", ""))
    centro = limpiar(re.split(r"\s+[-–—]\s+", nombre)[0]) if nombre else ""
    return centro, (ciudad or ajustes.get("sede_por_defecto", "MATRIZ"))


def _departamento(tablas):
    """Texto de 'NOMBRE DEL DEPARTAMENTO' en la tabla del encabezado."""
    for tabla in tablas:
        for i, fila in enumerate(tabla):
            texto = normalizar_clave(" ".join(_celda(c) for c in fila))
            if "NOMBRE DEL DEPARTAMENTO" in texto and i + 1 < len(tabla):
                fila_valor = tabla[i + 1]
                if len(fila_valor) > 1:
                    return _celda(fila_valor[1])
    return ""


def _indices(encabezado, subencabezado):
    """
    Ubica las columnas por su texto. La fila con Hp(10)/Hp(007)/Hp(3) va
    debajo; las tres primeras corresponden a DOSIS DEL PERIODO, y las
    siguientes a las acumuladas anual y total, que no se usan.
    """
    idx = {}
    for i, celda in enumerate(encabezado):
        t = normalizar_clave(_celda(celda))
        if "SERIE DEL" in t:
            idx.setdefault("serie", i)
        elif "NOMBRE DEL USUARIO" in t:
            idx.setdefault("nombre", i)
        elif "CEDULA" in t:
            idx.setdefault("cedula", i)
        elif t == "DESDE":
            idx.setdefault("desde", i)
        elif t == "HASTA":
            idx.setdefault("hasta", i)
        elif t == "DIAS":
            idx.setdefault("dias", i)

    vistos = []
    for i, celda in enumerate(subencabezado):
        t = normalizar_clave(_celda(celda)).replace(" ", "")
        if t in ("HP(10)", "HP(007)", "HP(0.07)", "HP(3)"):
            vistos.append((t, i))
        elif t == "NOTAS":
            idx.setdefault("notas", i)
    # solo el primer bloque de tres es DOSIS DEL PERIODO
    for t, i in vistos[:3]:
        idx.setdefault({"HP(10)": "hp10", "HP(007)": "hp007",
                        "HP(0.07)": "hp007", "HP(3)": "hp3"}[t], i)
    return idx


def _tabla_de_datos(tablas):
    """Devuelve (tabla, indice_primera_fila, indices) o None."""
    for tabla in tablas:
        for i, fila in enumerate(tabla):
            t = normalizar_clave(" ".join(_celda(c) for c in fila))
            if all(k in t for k in FIRMA_TABLA):
                sub = tabla[i + 1] if i + 1 < len(tabla) else []
                idx = _indices(fila, sub)
                if "serie" in idx and "cedula" in idx and "hp10" in idx:
                    return tabla, i + 2, idx
    return None


def _convertir(bruto, magnitud, nota):
    """
    Devuelve (valor_para_el_csv, observacion).

    La nota manda cuando dice que no hubo lectura: DNE, DD, DP, DNU y UNL
    dejan la celda de dosis vacia en el informe.
    """
    n = normalizar_clave(nota or "")
    v = limpiar(bruto or "")
    u = normalizar_clave(v)

    if n in NOTAS_NE:
        return "NE", n
    if u.lstrip("<") in NOTAS_NR or n.lstrip("<") in NOTAS_NR:
        return "NR", n if n in NOTAS_INFORMATIVAS else ""
    if not v:
        return "", n

    m = RE_NUMERO.match(v.replace(" ", ""))
    if not m:
        return v, n
    num = float(m.group(1).replace(",", "."))
    if num < UMBRALES[magnitud]:
        return "NR", n
    return m.group(1).replace(",", "."), n


# ------------------------------------------------------------ verificacion
def celdas_independientes(ruta_pdf, cfg):
    """
    Relectura independiente para verificar: toma las columnas por POSICION
    (0=serie, 2=cedula, 8=tipo, 9/10/11=Hp del periodo) en vez de ubicarlas por
    el texto del encabezado.

    Importa sobre todo distinguir el primer bloque de tres columnas, que es la
    DOSIS DEL PERIODO, de las acumuladas anual y total que vienen despues.

    Devuelve {(cedula, desde, hasta): [(hp10, hp3, hp007), ...]}, una terna
    por fila del informe.
    """
    salida = {}
    with pdfplumber.open(ruta_pdf) as pdf:
        for pagina in pdf.pages:
            for tabla in pagina.extract_tables():
                for fila in tabla:
                    if len(fila) < 13:
                        continue
                    serie = _celda(fila[0]).upper()
                    cedula = _celda(fila[2])
                    if not RE_SERIE.match(serie) or not cedula:
                        continue
                    desde = _fecha(_celda(fila[5]))
                    hasta = _fecha(_celda(fila[6]))
                    if not desde or not hasta:
                        continue
                    # el informe pone Hp(10), Hp(007) y Hp(3) en 9, 10 y 11;
                    # la terna va en el orden Hp(10), Hp(3), Hp(0.07)
                    terna = (_celda(fila[9]), _celda(fila[11]), _celda(fila[10]))
                    salida.setdefault((cedula, desde, hasta), []).append(terna)
    return salida


# ---------------------------------------------------------------- parser
def parsear(ruta_pdf: str, cfg: dict):
    registros = []
    avisos = []
    archivo = os.path.basename(ruta_pdf)
    ajustes = cfg.get("dosisrad") or {}
    omitir_amb = ajustes.get("omitir_ambiental", True)

    with pdfplumber.open(ruta_pdf) as pdf:
        texto1 = pdf.pages[0].extract_text() or ""
        cab = _cabecera(texto1)
        hospital, sede = _centro_y_sede(cab, ajustes)
        bruto_depto = _departamento(pdf.pages[0].extract_tables())
        practica = practica_por_palabras(bruto_depto, cfg) or bruto_depto
        mapa = {normalizar_clave(k): v
                for k, v in cfg.get("mapa_practica", {}).items()}
        practica = mapa.get(normalizar_clave(bruto_depto), practica)

        if not hospital:
            avisos.append("%s: no se pudo leer el nombre de la institucion "
                          "del encabezado" % archivo)
        if not bruto_depto:
            avisos.append("%s: no se pudo leer el departamento; la practica "
                          "queda vacia" % archivo)

        filas_por_pagina = []
        omitidos_amb = 0
        periodos = {}
        fechas = {}

        for n_pag, pagina in enumerate(pdf.pages, start=1):
            hallazgo = _tabla_de_datos(pagina.extract_tables())
            if hallazgo is None:
                filas_por_pagina.append(0)
                # ¿habia filas de datos que no se pudieron leer?
                sueltas = len(RE_SERIE.findall(
                    " ".join((pagina.extract_text() or "").split())))
                if sueltas:
                    avisos.append(
                        "%s p.%d: no se reconocio la tabla pero la pagina "
                        "menciona %d serie(s) de dosimetro"
                        % (archivo, n_pag, sueltas))
                continue

            tabla, primera, idx = hallazgo
            leidas = 0
            for fila in tabla[primera:]:
                if len(fila) <= max(idx.values()):
                    continue
                serie = _celda(fila[idx["serie"]])
                cedula = _celda(fila[idx["cedula"]])
                if not RE_SERIE.match(serie.upper()) or not cedula:
                    continue

                tipo = ""
                for i, celda in enumerate(fila):
                    t = normalizar_clave(_celda(celda))
                    if t in MAGNITUD_POR_TIPO:
                        tipo = t
                        break
                if omitir_amb and tipo == "AMB":
                    omitidos_amb += 1
                    continue

                nota = _celda(fila[idx["notas"]]) if "notas" in idx else ""
                # la magnitud la dice la columna con valor; si viene vacia
                # (nota DNE y similares), la dice el tipo de dosimetro
                magnitud = None
                crudo = ""
                for m in ("hp10", "hp007", "hp3"):
                    if m in idx and _celda(fila[idx[m]]):
                        magnitud = m
                        crudo = _celda(fila[idx[m]])
                        break
                if magnitud is None:
                    magnitud = MAGNITUD_POR_TIPO.get(tipo, "hp10")
                    if not tipo:
                        avisos.append(
                            "%s p.%d: dosimetro %s (cedula %s) sin tipo ni "
                            "lectura; se asume cuerpo entero"
                            % (archivo, n_pag, serie, cedula))

                valor, obs = _convertir(crudo, magnitud, nota)
                desde = _fecha(_celda(fila[idx["desde"]])) if "desde" in idx else ""
                hasta = _fecha(_celda(fila[idx["hasta"]])) if "hasta" in idx else ""
                if desde and hasta:
                    fechas[(desde, hasta)] = fechas.get((desde, hasta), 0) + 1

                # numero de medida dentro del anio: entre la fecha de ingreso
                # y la de inicio del periodo
                for i in range(idx.get("cedula", 0) + 1, idx.get("desde", 0)):
                    t = _celda(fila[i])
                    if re.fullmatch(r"\d{1,2}", t):
                        periodos[t] = periodos.get(t, 0) + 1
                        break

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
                    serie_dosimetro=serie,
                    dias=_celda(fila[idx["dias"]]) if "dias" in idx else "",
                )
                setattr(reg, magnitud, valor)
                setattr(reg, magnitud + "_crudo", crudo or nota or "x")
                registros.append(reg)
                leidas += 1

            filas_por_pagina.append(leidas)

            # control de completitud: el parser debe leer tantas filas como
            # series de dosimetro menciona la pagina
            texto_pag = " ".join((pagina.extract_text() or "").split())
            esperadas = len(RE_SERIE.findall(texto_pag))
            if esperadas and leidas < esperadas:
                avisos.append(
                    "%s p.%d: la pagina menciona %d serie(s) de dosimetro "
                    "pero solo se leyeron %d fila(s)"
                    % (archivo, n_pag, esperadas, leidas))

        # periodo del informe: el par de fechas mas repetido
        if fechas:
            desde_inf, hasta_inf = max(fechas.items(), key=lambda kv: kv[1])[0]
        else:
            desde_inf = hasta_inf = ""
            avisos.append("%s: no se pudo leer ninguna fecha de periodo" % archivo)

        numero = max(periodos.items(), key=lambda kv: kv[1])[0] if periodos else ""
        etiqueta = ""
        if numero and hasta_inf:
            etiqueta = cfg.get("formato_ciclo", "{anio}-C{numero}").format(
                anio=hasta_inf[:4], numero=numero)

        for r in registros:
            r.ciclo_lectura_desde = desde_inf
            r.ciclo_lectura_hasta = hasta_inf
            r.ciclo = etiqueta
            r.ciclo_num = numero

        if omitidos_amb:
            avisos.append("%s: %d dosimetro(s) ambientales omitidos (no son "
                          "personal expuesto)" % (archivo, omitidos_amb))

    resumen = {
        "paginas": len(filas_por_pagina),
        "filas_por_pagina": filas_por_pagina,
        "secciones": [(practica, len(registros))] if registros else [],
    }
    return registros, avisos, resumen
