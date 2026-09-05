# Lector de informes de dosis (POE) → CSV

Convierte los informes PDF de dosimetría personal en un CSV por informe.

---

## Uso

Copie `dist\consolidador_dosis.exe` a la carpeta donde el administrador deja
los PDF y haga **doble clic**. Por cada PDF se genera un CSV en esa misma
carpeta.

También desde consola:

```bat
consolidador_dosis.exe "C:\ruta\reportes"          REM todos los PDF de la carpeta
consolidador_dosis.exe "C:\ruta\informe.pdf"       REM un solo informe
consolidador_dosis.exe "C:\ruta" -s "C:\salida"    REM salida en otra carpeta
```

Cada corrida vuelve a leer todos los PDF y reescribe los CSV, así que basta con
agregar informes nuevos a la carpeta y volver a ejecutar. Se deja además un
`lector_dosis.log` con el detalle de la corrida.

## Salida

Un archivo por informe:

```
<CENTRO>_<SEDE>_<DESDE>_<HASTA>.csv
```

Ejemplo: `HOSPITAL_DE_LOS_VALLES_CUMBAYA_2024-03-18_2024-05-17.csv`

Las fechas son las del período del informe (`Lectura Desde` / `Lectura Hasta`).
Identifican el informe sin depender de que el laboratorio numere ciclos.

> La sede va en el nombre porque, sin ella, `Bosque_ciclo_1.pdf` y
> `Cumbaya_ciclo_1.pdf` producirían el mismo archivo y uno sobrescribiría al
> otro. Para quitarla, ponga `"incluir_sede_en_nombre": false` en `config.json`.

Separador `;`, codificación UTF-8 con BOM para que Excel abra bien las tildes.
La **primera fila** lleva el período de lectura del informe y la segunda los
encabezados de columna:

```
Lectura Desde;2025/01/12;Lectura Hasta;2025/03/11
cedula;nombre;fecha_inicio;fecha_fin;hp10;hp007;hp3;hospital;sede;practica;observacion
```

> La fila de título deja el archivo no rectangular: al importarlo hay que
> saltar la primera fila (`Table.Skip(T, 1)` en Power Query) o promover la
> segunda como encabezado. El texto lo arma cada laboratorio — DOSICONTROL usa
> sus propias etiquetas `Lectura Desde` / `Lectura Hasta` — así que otro formato
> puede rotularlo distinto. Se desactiva con `"fila_titulo_periodo": false`.

* **Una fila por usuario.** Si en el informe un usuario aparece varias veces es
  porque tiene varios dosímetros; sus lecturas se fusionan en una sola fila.
* `hospital` y `sede` salen del encabezado del informe. Cuando el centro tiene
  varias sedes, el informe la indica entre paréntesis tras el nombre del
  cliente; si no la trae, se toma de `sede_por_defecto`.
* `practica` sale del título de cada tabla del informe (`Área: ...`):
  *Radiodiagnóstico Médico* → `Radiodiagnóstico`, *Radiología Intervencionista*
  → `Hemodinamia e intervencionismo`. Se ajusta en `mapa_practica`; un área que
  no esté en el mapa se copia literal.
* `observacion` trae los códigos de nota del informe, tal cual: `DD` dosímetro
  dañado, `NU` no usado, `NC` no canjeado, `DP` perdido, `DA` dosis asignada por
  la Autoridad Reguladora, `SLI` superior al límite de investigación, `DNV`
  dosis no válida, `DMS` dosis modificada/autorizada SCAN, `DOP` lectura de
  doble periodo. Al fusionar dosímetros se acumulan los códigos de todos ellos.

  No todos los laboratorios manejan ciclos, así que el ciclo dejó de ser una
  columna propia: cuando el informe lo trae, se anexa aquí como información
  complementaria — `DOP | Ciclo 2024-C7`, o sólo `Ciclo 2024-C7` si no hay
  notas. La etiqueta es `<año>-C<número>`; los ciclos bimensuales a veces cruzan
  el cambio de año y el año se toma de la fecha de fin (`anio_del_ciclo`). Con
  `"texto_ciclo_en_observacion": ""` no se anexa nada.
* `fecha_inicio` / `fecha_fin` son las columnas **DESDE** y **HASTA** de cada
  usuario, no las del encabezado del informe. No siempre coinciden: quien se
  incorpora a mitad de ciclo tiene su propia fecha de inicio. Al fusionar
  dosímetros con períodos distintos se toma el más temprano y el más tardío.
* Las dosis son las de **DOSIS DEL PERIODO**, no las acumuladas anuales.

## Reglas aplicadas a las dosis

| Situación en el informe | Valor en el CSV |
|---|---|
| Lectura bajo el nivel de registro: Hp(10) < 0.1, Hp(3) < 0.6, Hp(0.07) < 2 mSv | `NR` |
| Nota `NC` (dosímetro no canjeado) | `NE` |
| El usuario no tiene dosímetro que mida esa magnitud | *celda en blanco* |
| Cualquier otro caso | el valor en mSv |

### Fusión de dosímetros

Un mismo usuario puede tener el dosímetro de cuerpo entero (serie de 9 dígitos,
reporta Hp(10)) y uno o dos de anillo o cristalino (serie de 4–5 dígitos,
reportan Hp(3) o Hp(0.07)). El informe le dedica una fila a cada dosímetro y
rellena con `--` las columnas que ese dosímetro no mide.

El programa toma cada magnitud del dosímetro que realmente la mide y las reúne
en una sola fila. Sólo cuando **ningún** dosímetro del usuario mide esa
magnitud —es decir, no tiene asignado un dosímetro de ese tipo— la celda queda
**en blanco**: no hubo medición, que es distinto de haber medido por debajo del
nivel de registro (eso es `NR`). Se cambia con `tratar_guiones_como`.

Ejemplo (período 2024/05/18–2024/11/17): tres filas del informe, con las series
del dosímetro de cuerpo entero y de los dos de extremidad, se convierten en

```
1700000000;Nombre Apellido Ejemplo;2024/05/18;2024/11/17;0.358;5.370;0.668;HOSPITAL EJEMPLO;CUMBAYA;Hemodinamia e intervencionismo;DOP | Ciclo 2024-C7
```

Con `"una_fila_por_usuario": false` se emite una fila por dosímetro.

## Configuración (`config.json`)

Se crea solo la primera vez que se ejecuta, junto al `.exe`. Claves útiles:

| Clave | Qué hace |
|---|---|
| `una_fila_por_usuario` | `true`: fusiona los dosímetros de un usuario en una fila. `false`: una fila por dosímetro. |
| `tratar_guiones_como` | Qué poner cuando el usuario no tiene dosímetro que mida esa magnitud. `""` (actual: celda en blanco), `"NR"` o `"NE"`. |
| `notas_a_ne` | Notas que se convierten en `NE`. Hoy `["NC"]`; puede agregar `"DP"`, `"NU"`, `"DOP"`. |
| `nota_aplica_a_toda_la_fila` | Sólo aplica con `una_fila_por_usuario: false`. Al fusionar, cada magnitud toma su valor del dosímetro que la mide. |
| `sede_por_defecto` | Sede a usar cuando el informe no la trae entre paréntesis. |
| `mapa_practica` | Normaliza el título de tabla (`Área: ...`) que va en la columna `practica`. |
| `texto_ciclo_en_observacion` | Texto del ciclo anexado a `observacion`. `"Ciclo {ciclo}"` por defecto; `""` para no anexarlo. |
| `formato_ciclo` | Plantilla de la etiqueta del ciclo. `"{anio}-C{numero}"` por defecto. |
| `anio_del_ciclo` | De qué fecha sale el año: `"fin"` (actual) o `"inicio"`. |
| `fila_titulo_periodo` | Escribir la primera fila con el período de lectura. `true` por defecto. |
| `separador_csv` | `";"` por defecto. |
| `incluir_sede_en_nombre` | Incluir la sede en el nombre del archivo. |
| `patron_nombre_salida` | Marcadores: `{centro}` `{hospital}` `{sede}` `{desde}` `{hasta}` `{ciclo}` `{ciclo_num}`. |

Los niveles de registro (0.1 / 0.6 / 2 mSv) están fijados por el Acuerdo
Ministerial 245 y viven en `laboratorios/base.py`; no se editan por
configuración.

## Agregar otro laboratorio

Hoy está implementado **DOSICONTROL S.A.S.** (informe `DC-008`). Para los otros
dos laboratorios del país:

1. Copie `laboratorios/dosicontrol.py` a `laboratorios/<nuevo_lab>.py` y adapte
   `NOMBRE`, `detectar()` y `parsear()`.
2. Regístrelo en `laboratorios/__init__.py` (import + lista `PARSERS`).
3. Reconstruya con `construir_exe.bat`.

`detectar()` recibe el texto de la primera página y devuelve `True` si el
informe es de ese laboratorio. Los PDF que ningún parser reconoce se omiten y
quedan anotados en el log.

## Cómo lee el PDF (por si hay que depurar)

No usa posiciones fijas de texto. Toma las coordenadas de cada palabra y:

* localiza el encabezado de tabla para fijar la **rejilla de las 9 columnas de
  dosis**, y asigna cada valor a su columna por posición horizontal — por eso no
  se descoloca cuando el PDF deja celdas vacías;
* reconstruye los nombres partidos en varias líneas, incluso cuando la fila
  queda cortada entre dos páginas;
* verifica que la numeración de filas de cada sección sea correlativa (1..N) y
  reporta en el log cualquier salto, que indicaría una fila no leída.

## El log (`lector_dosis.log`)

Por cada informe queda el detalle de lo que se leyó, para poder contrastarlo
con el PDF:

```
  Hospital Metropolitanociclo 30.pdf     DOSICONTROL S.A.S.
      10 pagina(s), 167 dosimetro(s) leido(s)   [por pagina: 11, 21, 19, ...]
      seccion Radioterapia - Teleterapia                       10
      seccion Medicina Nuclear                                 30
      seccion Radiodiagnóstico Médico. - Radiología Intervencionista  127
    -> Hospital_Metropolitano_MATRIZ_2025-01-12_2025-03-11.csv  112 usuarios
```

Un `0` en «por pagina» sobre una página con tabla, o un total de sección que no
cuadre con el PDF, señalan que algo no se leyó.

Al final se listan las **incidencias**, cada una con archivo, página y sección.
El programa las detecta comparando su lectura contra la numeración del propio
informe:

| Incidencia | Qué significa |
|---|---|
| `la numeración del informe salta de la fila N a la M` | Se perdieron `M-N-1` filas en esa página. |
| `el informe numera hasta la fila N pero solo se leyeron M` | Faltan filas en esa sección, en cualquier punto. |
| `la lectura de la página se cortó al tomar esta línea como pie` | Una línea se confundió con el pie de página y el resto de la hoja quedó sin leer. |
| `la página trae encabezado de tabla pero no se leyó ninguna fila` | La tabla existe pero ninguna fila se reconoció. |
| `solo trae N valor(es) de dosis, se esperaban 3` | Fila con columnas de dosis incompletas. |
| `no coincide con ningún laboratorio conocido` | Falta implementar ese parser; el informe se omitió entero. |

Si no hay nada que reportar, el log lo dice explícitamente: *«Sin incidencias:
todas las filas numeradas del informe se leyeron»*.

## Reconstruir el ejecutable

```bat
construir_exe.bat
```

Requiere Python 3.9+ e instala `pdfplumber` y `pyinstaller`. El resultado queda
en `dist\consolidador_dosis.exe` y no necesita Python en la máquina destino.
