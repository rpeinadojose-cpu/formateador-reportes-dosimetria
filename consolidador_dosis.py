# -*- coding: utf-8 -*-
"""
Lector de informes de dosis (POE) -> CSV
========================================

Lee informes PDF de dosimetria personal, identifica el laboratorio que los
emitio y genera UN CSV POR CENTRO, SEDE Y PERIODO con las columnas:

    Lectura Desde;<fecha>;Lectura Hasta;<fecha>      <- fila de titulo
    cedula;nombre;fecha_inicio;fecha_fin;hp10;hp007;hp3;
    hospital;sede;practica;observacion

El archivo de salida se llama:

    <NOMBRE_DEL_CENTRO>_<SEDE>_<DESDE>_<HASTA>.csv

Reglas aplicadas a las dosis del periodo (Acuerdo Ministerial 245):
  * Lectura por debajo del nivel de registro -> "NR"
    Hp(10) < 0.1 mSv, Hp(3) < 0.6 mSv, Hp(0.07) < 2 mSv
  * Nota NC (dosimetro no canjeado) -> "NE"
  * Un usuario con varios dosimetros (cuerpo entero + cristalino/extremidades)
    se resume en UNA fila: cada magnitud se toma del dosimetro que la mide,
    aunque vengan en archivos distintos.

Laboratorios reconocidos: DOSICONTROL S.A.S. (informe DC-008), el laboratorio
del IESS (Informe Dosimetria Personal Termoluminiscente) y DOSISRAD S.A.

Uso:
    consolidador_dosis.exe                        -> carpeta del ejecutable
    consolidador_dosis.exe "C:\\ruta\\reportes"     -> todos los PDF de la carpeta
    consolidador_dosis.exe "C:\\ruta\\informe.pdf"  -> un solo informe
    consolidador_dosis.exe "C:\\ruta" -s "C:\\salida"

La configuracion se lee de config.json (junto al ejecutable). Si no existe se
crea con los valores por defecto de abajo.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import traceback
from datetime import datetime

import pdfplumber

import laboratorios
from laboratorios import logo, verificacion
from laboratorios.base import (
    CAMPOS_SALIDA, nombre_archivo_seguro, limpiar, normalizar_clave,
)

VERSION = "3.4.0"

# --------------------------------------------------------------------------
# Configuracion por defecto
# --------------------------------------------------------------------------
CONFIG_DEFECTO = {
    "_comentario": (
        "Configuracion del lector de informes de dosis. "
        "Los niveles de registro estan fijados por el Acuerdo Ministerial 245 "
        "y no se editan aqui."
    ),
    "carpeta_reportes": "",
    "carpeta_salida": "",
    # Logotipo de FISIQA en color al abrir el programa. Solo se dibuja en la
    # consola: el archivo lector_dosis.log guarda el encabezado corto.
    # "auto" pinta en color solo si la consola entiende ANSI; tambien
    # "siempre" y "nunca".
    "color_en_consola": "auto",
    # Buscar tambien dentro de las subcarpetas de la carpeta de reportes.
    # Desactivado por defecto a proposito: asi el programa solo mira la carpeta
    # que se le indica y nunca toca informes guardados en subcarpetas. Los
    # informes del IESS vienen repartidos en una carpeta por practica; para
    # esos, actívelo aqui o pase --subcarpetas en la linea de comandos.
    "buscar_en_subcarpetas": False,
    # Verificar cada informe releyendolo por otro camino y comparando con lo
    # extraido. Suma unos segundos por archivo. Desactivarlo solo si hace
    # falta ganar tiempo en una carpeta muy grande.
    "verificar": True,
    "separador_csv": ";",
    "codificacion_csv": "utf-8-sig",

    # Primera fila del CSV con el periodo de lectura del informe, antes de la
    # fila de encabezados. Ojo: deja el archivo no rectangular, hay que saltar
    # esa fila al importarlo.
    "fila_titulo_periodo": True,

    # Nombre del CSV: <centro>_<sede>_<desde>_<hasta>.csv
    # La sede evita que dos sedes del mismo centro se pisen el archivo.
    "incluir_sede_en_nombre": True,
    # Marcadores: {centro} {hospital} {sede} {desde} {hasta} {ciclo} {ciclo_num}
    "patron_nombre_salida": "{centro}_{desde}_{hasta}",

    # No todos los laboratorios manejan ciclos. Cuando el informe trae uno, se
    # anexa a la columna "observacion" como informacion complementaria.
    # Dejar en "" para no anexarlo.
    "texto_ciclo_en_observacion": "Ciclo {ciclo}",
    # Etiqueta del ciclo. Los ciclos bimensuales cruzan a veces el cambio de
    # anio; "anio_del_ciclo" dice de que fecha se toma: "fin" o "inicio".
    "formato_ciclo": "{anio}-C{numero}",
    "anio_del_ciclo": "fin",

    # Una fila por usuario: fusiona los dosimetros del mismo usuario (cuerpo
    # entero + anillo/cristalino) en una sola fila con Hp(10), Hp(0.07), Hp(3).
    # En false, se emite una fila por dosimetro.
    "una_fila_por_usuario": True,

    # Notas del informe que deben mostrarse como "NE" en la columna de dosis.
    "notas_a_ne": ["NC"],
    # Propagar la nota a las tres magnitudes de la fila. Solo tiene efecto con
    # "una_fila_por_usuario": false; al agrupar, cada magnitud toma su valor
    # del dosimetro que realmente la mide.
    "nota_aplica_a_toda_la_fila": False,
    # Que poner cuando NINGUN dosimetro del usuario mide esa magnitud, es decir
    # cuando no tiene asignado un dosimetro de ese tipo. Por defecto se deja la
    # celda en blanco: no hubo medicion, que es distinto de medir por debajo
    # del nivel de registro (eso es "NR"). Otras opciones: "NR", "NE".
    "tratar_guiones_como": "",
    "texto_sin_dato": "",

    # Sede a usar cuando el informe NO trae sede entre parentesis.
    "sede_por_defecto": {
        "HOSPITAL DE LOS VALLES": "CUMBAYA"
    },
    "sede_por_defecto_general": "MATRIZ",

    # Ajustes propios del laboratorio del IESS. Sus informes no nombran el
    # hospital: la linea "DATOS DE LA INSTITUCION USUARIA" trae la practica y,
    # tras un guion, la sede. Cuando no trae sede se usa la de por defecto.
    "iess": {
        "hospital": "HECAM",
        "sede_por_defecto": "HECAM",
        # Sedes ya conocidas. Sirven para resolver el orden de la linea
        # "DATOS DE LA INSTITUCION USUARIA": si una de las dos partes esta en
        # esta lista, esa es la sede y la otra la practica, sin importar cual
        # venga primero. Agregue aqui las sedes nuevas que vayan apareciendo.
        "sedes_conocidas": ["HECAM", "GUAYAQUIL", "QUITO", "CUENCA", "AMBATO",
                            "PORTOVIEJO", "MANTA", "MACHALA", "LOJA",
                            "IBARRA", "RIOBAMBA", "SANTO DOMINGO", "ESMERALDAS"]
    },

    # Ajustes propios de DOSISRAD. Su encabezado separa la sede del centro
    # con un guion ("... - NUCLEO DE PORTOVIEJO"); si no lo trae, la sede es
    # la ciudad. Los dosimetros ambientales (AMB) no son personal expuesto.
    "dosisrad": {
        "sede_por_defecto": "MATRIZ",
        "omitir_ambiental": True,
        # El encabezado recorta el nombre del cliente al ancho de su casilla,
        # asi que la sede se toma de "Ciudad". Aqui se puede fijar centro y
        # sede por "Codigo Nro", p.ej.:
        #   "<codigo>": {"hospital": "...", "sede": "..."}
        "centros": {
            "13S001": {"hospital": "SOLCA MANABÍ", "sede": "PORTOVIEJO"}
        }
    },

    # Normalizacion de la practica por palabras clave, sin tildes ni
    # mayusculas. Cada clave se busca como prefijo de palabra ("gastro"
    # reconoce "gastroenterologia"). Manda el orden: la primera regla que
    # coincide gana, asi que las mas especificas van primero. Se aplica cuando
    # "mapa_practica" (coincidencia exacta) no tiene una entrada para el texto.
    "practica_por_palabra_clave": [
        {"practica": "Hemodinamia e intervencionismo",
         "contiene": ["hemodinamia", "intervencionis", "angiograf", "cateteris",
                      "electrofisiolog", "arritmi", "marcapaso", "traumatolog",
                      "ortoped", "neurocirug", "gastro", "endoscop", "cpre",
                      "colangio", "urolog", "litotric", "vascular",
                      "quirofano hibrido", "arco en c"]},
        {"practica": "Ciclotrón",
         "contiene": ["ciclotron"]},
        {"practica": "Medicina Nuclear",
         "contiene": ["medicina nuclear", "pet", "spect", "endocrinolog",
                      "gammagraf", "gamma camara", "radiofarmac", "yodo"]},
        {"practica": "Radioterapia",
         "contiene": ["radioterap", "teleterap", "braquiterap", "acelerador"]},
        {"practica": "Radiodiagnóstico",
         "contiene": ["radiodiagnostico", "radiolog", "imagenolog", "imagen",
                      "tomograf", "mamograf", "densitometr", "rayos x",
                      "fluoroscop", "odontolog", "dental"]}
    ],

    # Normalizacion del area/practica que aparece como titulo de cada tabla.
    # La clave se compara sin tildes ni mayusculas; el valor se escribe tal cual.
    # Un area que no este aqui se copia literal desde el informe.
    "mapa_practica": {
        "Radiodiagnostico Medico": "Radiodiagnóstico",
        "Radiologia Intervencionista": "Hemodinamia e intervencionismo"
    }
}


# --------------------------------------------------------------------------
def carpeta_base():
    """Carpeta donde vive el .exe (o el .py si se corre con Python)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def cargar_config(ruta):
    cfg = json.loads(json.dumps(CONFIG_DEFECTO))
    if os.path.isfile(ruta):
        try:
            with open(ruta, "r", encoding="utf-8-sig") as fh:
                guardada = json.load(fh)
            for k, v in guardada.items():
                # las secciones por laboratorio se mezclan, no se reemplazan:
                # asi no se pierden claves al editar solo una
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except Exception as exc:
            print("  ! config.json no se pudo leer (%s). Se usan valores por defecto." % exc)
    else:
        try:
            with open(ruta, "w", encoding="utf-8") as fh:
                json.dump(CONFIG_DEFECTO, fh, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return cfg


def texto_primera_pagina(ruta_pdf):
    with pdfplumber.open(ruta_pdf) as pdf:
        if not pdf.pages:
            return ""
        return pdf.pages[0].extract_text() or ""


def listar_pdfs(destino, recursivo=False):
    """
    Acepta una carpeta o la ruta de un PDF suelto.

    Por defecto NO entra en las subcarpetas, para no leer ni sobrescribir nada
    fuera de la carpeta indicada. Con recursivo=True si entra: los informes del
    IESS vienen organizados en una carpeta por practica. Se ignoran los
    archivos de bloqueo que dejan Office/LibreOffice cuando el PDF esta
    abierto.
    """
    if os.path.isfile(destino):
        return [destino] if destino.lower().endswith(".pdf") else []

    def util(nombre):
        return (nombre.lower().endswith(".pdf")
                and not nombre.startswith((".~", "~$")))

    if not recursivo:
        return sorted(os.path.join(destino, f) for f in os.listdir(destino)
                      if util(f))

    encontrados = []
    for raiz, _, archivos in os.walk(destino):
        encontrados.extend(os.path.join(raiz, f) for f in archivos if util(f))
    return sorted(encontrados)


# --------------------------------------------------------------------------
def periodo(registros):
    """(desde, hasta) del informe; si el encabezado no los trae, de las filas."""
    r0 = registros[0]
    desde = r0.ciclo_lectura_desde
    hasta = r0.ciclo_lectura_hasta
    if not desde:
        f = [r.fecha_inicio for r in registros if r.fecha_inicio]
        desde = min(f) if f else ""
    if not hasta:
        f = [r.fecha_fin for r in registros if r.fecha_fin]
        hasta = max(f) if f else ""
    return desde, hasta


def clave_informe(reg):
    """Identifica el periodo de un informe, use o no ciclos el laboratorio."""
    return (reg.hospital, reg.sede,
            reg.ciclo_lectura_desde or reg.fecha_inicio,
            reg.ciclo_lectura_hasta or reg.fecha_fin)


def nombre_salida(registros, cfg):
    """<centro>[_<sede>]_<desde>_<hasta>"""
    reg = registros[0]
    centro = reg.hospital
    # La sede se omite si repite el nombre del centro: evita "HECAM_HECAM_...".
    if (cfg.get("incluir_sede_en_nombre", True) and reg.sede
            and normalizar_clave(reg.sede) != normalizar_clave(reg.hospital)):
        centro = "%s %s" % (centro, reg.sede)
    desde, hasta = periodo(registros)
    desde = desde.replace("/", "-")
    hasta = hasta.replace("/", "-")
    patron = cfg.get("patron_nombre_salida", "{centro}_{desde}_{hasta}")
    return nombre_archivo_seguro(
        patron.format(centro=centro, desde=desde or "SD", hasta=hasta or "SD",
                      ciclo=reg.ciclo or "SD", ciclo_num=reg.ciclo_num or "SD",
                      sede=reg.sede, hospital=reg.hospital))


def anexar_ciclo(registros, cfg):
    """Anexa el ciclo a 'observacion' como informacion complementaria."""
    plantilla = cfg.get("texto_ciclo_en_observacion", "Ciclo {ciclo}")
    if not plantilla:
        return
    for r in registros:
        if not r.ciclo:
            continue
        texto = plantilla.format(ciclo=r.ciclo, numero=r.ciclo_num)
        # separador " | ": no colisiona con el ";" del CSV, asi la celda no
        # necesita ir entrecomillada
        r.observacion = ("%s | %s" % (r.observacion, texto)
                         if r.observacion else texto)


MAGNITUDES = (("hp10", "hp10_crudo"), ("hp007", "hp007_crudo"), ("hp3", "hp3_crudo"))


def _num(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def agrupar_por_usuario(registros, cfg, incidencias, origen):
    """
    Une en una sola fila los dosimetros de un mismo usuario dentro del informe.

    En el informe, "--" significa dos cosas segun el caso:
      * el usuario aparece una sola vez -> no tiene dosimetro de ese tipo;
      * el usuario aparece varias veces -> es solo relleno de la columna, la
        lectura esta en la fila del dosimetro que si mide esa magnitud.
    Por eso cada magnitud se toma del dosimetro cuya celda NO es "--", y solo
    si ninguno la reporta se aplica 'tratar_guiones_como'.

    Las fechas del usuario abarcan desde la mas temprana hasta la mas tardia de
    sus dosimetros (el de cuerpo entero y el de anillo no siempre coinciden).
    """
    if not cfg.get("una_fila_por_usuario", True):
        return registros

    orden, grupos = [], {}
    for r in registros:
        k = clave_informe(r) + (r.practica, r.cedula)
        if k not in grupos:
            grupos[k] = []
            orden.append(k)
        grupos[k].append(r)

    salida = []
    for k in orden:
        filas = grupos[k]
        if len(filas) == 1:
            salida.append(filas[0])
            continue

        base = filas[0]
        for campo, crudo in MAGNITUDES:
            candidatos = [r for r in filas if getattr(r, crudo) not in ("--", "")]
            if not candidatos:
                # ningun dosimetro del usuario mide esa magnitud
                valor = cfg.get("tratar_guiones_como", "")
            elif len(candidatos) == 1:
                valor = getattr(candidatos[0], campo)
            else:
                # no deberia ocurrir; se toma la lectura mayor y se avisa
                con_num = [(_num(getattr(r, campo)), r) for r in candidatos]
                con_num = [(n, r) for n, r in con_num if n is not None]
                elegido = (max(con_num, key=lambda t: t[0])[1]
                           if con_num else candidatos[0])
                valor = getattr(elegido, campo)
                incidencias.append(
                    "%s: cedula %s tiene %d dosimetros que reportan %s (%s); "
                    "se usa %s"
                    % (origen, base.cedula, len(candidatos), campo.upper(),
                       ", ".join("%s=%s" % (r.serie_dosimetro, getattr(r, crudo))
                                 for r in candidatos), valor))
            setattr(base, campo, valor)

        inicios = [r.fecha_inicio for r in filas if r.fecha_inicio]
        fines = [r.fecha_fin for r in filas if r.fecha_fin]
        base.fecha_inicio = min(inicios) if inicios else ""
        base.fecha_fin = max(fines) if fines else ""
        base.serie_dosimetro = " + ".join(r.serie_dosimetro for r in filas)
        base.observacion = " ".join(dict.fromkeys(
            n for r in filas for n in r.observacion.split() if n))
        salida.append(base)

    return salida


def sin_repetidas(registros, incidencias, origen):
    """
    Descarta unicamente filas identicas en TODOS sus campos (mismo informe
    cargado dos veces). Un usuario con varios dosimetros conserva una fila por
    dosimetro, porque la serie los diferencia.
    """
    vistas = {}
    unicas = []
    repetidas = {}          # (archivo que la trajo primero, archivo repetido) -> n
    for r in registros:
        d = r.como_dict()
        d.pop("archivo_origen", None)
        k = tuple(d.items())
        if k in vistas:
            par = (vistas[k], r.archivo_origen)
            repetidas[par] = repetidas.get(par, 0) + 1
            continue
        vistas[k] = r.archivo_origen
        unicas.append(r)

    # Un aviso por par de archivos, no uno por fila: cuando el mismo informe
    # esta en dos carpetas se repetirian cientos de lineas iguales.
    for (antes, ahora), n in repetidas.items():
        if antes == ahora:
            incidencias.append(
                "%s: %d fila(s) repetidas dentro del mismo informe, se "
                "descartaron" % (ahora, n))
        else:
            incidencias.append(
                "%d fila(s) identicas descartadas: '%s' y '%s' traen los mismos "
                "datos (el mismo informe en dos carpetas)" % (n, antes, ahora))
    return unicas


def escribir_csv(registros, carpeta_salida, cfg, log, parser=None):
    archivo = os.path.join(carpeta_salida, nombre_salida(registros, cfg) + ".csv")
    with open(archivo, "w", encoding=cfg["codificacion_csv"], newline="") as fh:
        # 1a fila: periodo de lectura del informe. El texto lo arma cada
        # laboratorio, porque cada formato rotula el periodo a su manera.
        if cfg.get("fila_titulo_periodo", True):
            armar = getattr(parser, "fila_titulo", None)
            if armar is not None:
                desde, hasta = periodo(registros)
                csv.writer(fh, delimiter=cfg["separador_csv"]).writerow(
                    armar(desde, hasta, cfg))

        w = csv.DictWriter(fh, fieldnames=CAMPOS_SALIDA,
                           delimiter=cfg["separador_csv"])
        w.writeheader()
        for r in registros:
            w.writerow(r.fila_csv())
    log("    -> %-52s %4d usuarios" % (os.path.basename(archivo), len(registros)))
    return archivo


# --------------------------------------------------------------------------
def procesar(destino, salida, cfg, log):
    recursivo = bool(cfg.get("buscar_en_subcarpetas", False))
    pdfs = listar_pdfs(destino, recursivo)
    log("PDFs encontrados: %d   (%s)"
        % (len(pdfs), "incluyendo subcarpetas" if recursivo
           else "solo esta carpeta, sin subcarpetas"))
    log("")

    incidencias = []
    generados = []
    total = 0            # filas escritas: una por usuario y periodo
    personas = set()     # (centro, cedula): cuenta a cada persona una sola vez
    # Un CSV por (centro, sede, periodo). Varios PDF pueden alimentar el mismo:
    # el IESS emite un archivo por magnitud (cuerpo entero, cristalino,
    # extremidades) para el mismo periodo, y los tres van al mismo CSV.
    acumulado = {}

    for ruta in pdfs:
        # Ruta relativa a la carpeta de origen: al buscar en subcarpetas puede
        # haber dos informes con el mismo nombre y hay que poder distinguirlos.
        try:
            nombre = os.path.relpath(ruta, destino) if os.path.isdir(destino)                 else os.path.basename(ruta)
        except ValueError:
            nombre = os.path.basename(ruta)
        try:
            parser = laboratorios.identificar(texto_primera_pagina(ruta))
            if parser is None:
                incidencias.append(
                    "%s: no coincide con ningun laboratorio conocido, el informe "
                    "se omitio por completo" % nombre)
                log("  %-38s OMITIDO (laboratorio no reconocido)" % nombre)
                continue

            res = parser.parsear(ruta, cfg)
            registros, avisos = res[0], res[1]
            resumen = res[2] if len(res) > 2 else {}
            incidencias.extend(avisos)
            for r in registros:
                r.archivo_origen = nombre

            log("  %-38s %s" % (nombre, parser.NOMBRE))
            if resumen:
                fpp = resumen.get("filas_por_pagina") or []
                log("      %d pagina(s), %d dosimetro(s) leido(s)%s"
                    % (resumen.get("paginas", 0), len(registros),
                       "   [por pagina: %s]" % ", ".join(str(n) for n in fpp)
                       if fpp else ""))
                for nom, cant in resumen.get("secciones", []):
                    log("      seccion %-46s %4d" % (nom or "(sin titulo)", cant))

            if not registros:
                incidencias.append(
                    "%s: no se extrajo ninguna fila del informe" % nombre)
                log("      SIN FILAS")
                continue

            # Verificacion: se hace ANTES de fusionar los dosimetros, cuando
            # cada registro todavia equivale a una fila del PDF.
            if cfg.get("verificar", True):
                try:
                    resumen_v, problemas = verificacion.comprobar(
                        parser, ruta, registros, cfg, nombre)
                    if resumen_v:
                        log("      verificacion: %s" % resumen_v)
                    incidencias.extend(problemas)
                except Exception as exc:
                    incidencias.append(
                        "%s: la verificacion no se pudo completar -> %s"
                        % (nombre, exc))
                    log("      verificacion: fallo (%s)" % exc)

            for r in registros:
                grupo = acumulado.setdefault(
                    clave_informe(r),
                    {"registros": [], "parser": parser, "archivos": []})
                grupo["registros"].append(r)
                if nombre not in grupo["archivos"]:
                    grupo["archivos"].append(nombre)

        except Exception as exc:
            incidencias.append("%s: ERROR al procesar -> %s" % (nombre, exc))
            log("  %-38s ERROR: %s" % (nombre, exc))
            log(traceback.format_exc())

    # ---------- un CSV por centro, sede y periodo ----------
    # Se escribe al final, cuando ya se leyeron todos los PDF: el IESS emite un
    # archivo por magnitud (cuerpo entero, cristalino, extremidades) del mismo
    # periodo, y los tres tienen que caer en el mismo CSV.
    if acumulado:
        log("")
        log("-" * 74)
        log("CSV GENERADOS")
    for clave in sorted(acumulado):
        grupo = acumulado[clave]
        origen = ", ".join(grupo["archivos"])
        regs = sin_repetidas(grupo["registros"], incidencias, origen)
        regs = agrupar_por_usuario(regs, cfg, incidencias, origen)
        anexar_ciclo(regs, cfg)
        archivo = escribir_csv(regs, salida, cfg, log, grupo["parser"])
        personas.update((r.hospital, r.cedula) for r in regs if r.cedula)
        if len(grupo["archivos"]) > 1:
            log("       a partir de %d informes: %s"
                % (len(grupo["archivos"]), origen))
        generados.append(archivo)
        total += len(regs)

    return generados, total, incidencias, len(personas)


# --------------------------------------------------------------------------
def main(argv=None):
    base = carpeta_base()
    ap = argparse.ArgumentParser(
        description="Convierte informes de dosis (PDF) en un CSV por informe.")
    ap.add_argument("destino", nargs="?", default=None,
                    help="carpeta con los PDF, o la ruta de un PDF suelto "
                         "(por defecto: la carpeta del ejecutable)")
    ap.add_argument("-s", "--salida", default=None,
                    help="carpeta donde escribir los CSV")
    ap.add_argument("-c", "--config", default=os.path.join(base, "config.json"))
    ap.add_argument("-r", "--subcarpetas", action="store_true",
                    help="buscar tambien dentro de las subcarpetas")
    ap.add_argument("--sin-pausa", action="store_true",
                    help="no esperar ENTER al terminar")
    args = ap.parse_args(argv)

    cfg = cargar_config(args.config)
    if args.subcarpetas:
        cfg["buscar_en_subcarpetas"] = True

    destino = args.destino or cfg.get("carpeta_reportes") or base
    destino = os.path.abspath(os.path.expandvars(os.path.expanduser(destino)))
    salida = (args.salida or cfg.get("carpeta_salida")
              or (os.path.dirname(destino) if os.path.isfile(destino) else destino))
    salida = os.path.abspath(os.path.expandvars(os.path.expanduser(salida)))

    lineas_log = []

    def log(msg=""):
        try:
            print(msg)
        except UnicodeEncodeError:
            # consola Windows con codepage heredada: el .log siempre va en UTF-8
            enc = getattr(sys.stdout, "encoding", None) or "ascii"
            print(msg.encode(enc, "replace").decode(enc, "replace"))
        lineas_log.append(msg)

    # El logotipo va solo a la pantalla, nunca al archivo de log.
    for linea in logo.banner(logo.hay_color(cfg.get("color_en_consola", "auto"))):
        try:
            print(linea)
        except UnicodeEncodeError:
            pass

    log("=" * 74)
    log(" LECTOR DE INFORMES DE DOSIS -> CSV   v%s" % VERSION)
    log(" %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log("=" * 74)
    log("Origen : %s" % destino)
    log("Salida : %s" % salida)
    log("")

    if not os.path.exists(destino):
        log("ERROR: la ruta de origen no existe.")
        return _fin(1, lineas_log, salida, args.sin_pausa)

    os.makedirs(salida, exist_ok=True)
    generados, total, incidencias, personas = procesar(destino, salida, cfg, log)

    log("")
    log("=" * 74)
    # "filas" cuenta cada usuario una vez POR PERIODO; "personas" lo cuenta
    # una sola vez aunque aparezca en varios informes.
    log("CSV generados: %d    Filas escritas: %d    Personas distintas: %d"
        % (len(set(generados)), total, personas))

    if incidencias:
        log("")
        log("!" * 74)
        log("INCIDENCIAS: %d" % len(incidencias))
        log("!" * 74)
        log("Cada linea indica un dato que NO se pudo leer del PDF. Revise el")
        log("informe original en la pagina indicada antes de usar el CSV.")
        log("")
        for n, i in enumerate(incidencias, 1):
            log("  %2d. %s" % (n, limpiar(i)))
        log("")
        log("!" * 74)
    else:
        log("Sin incidencias: todas las filas numeradas del informe se leyeron.")
    log("")
    log("Listo.")
    return _fin(0 if generados else 1, lineas_log, salida, args.sin_pausa)


def _fin(codigo, lineas_log, salida, sin_pausa):
    try:
        os.makedirs(salida, exist_ok=True)
        with open(os.path.join(salida, "lector_dosis.log"),
                  "w", encoding="utf-8") as fh:
            fh.write("\n".join(lineas_log))
    except Exception:
        pass
    if not sin_pausa and sys.stdin is not None and sys.stdin.isatty():
        try:
            input("\nPresione ENTER para cerrar...")
        except Exception:
            pass
    return codigo


if __name__ == "__main__":
    sys.exit(main())
