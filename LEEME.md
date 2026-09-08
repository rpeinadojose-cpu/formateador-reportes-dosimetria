# Lector de informes de dosis (POE) → CSV

Convierte los informes PDF de dosimetría personal en un CSV por centro, sede y
período. Reconoce los formatos de **DOSICONTROL S.A.S.**, del **laboratorio del
IESS** y de **DOSISRAD S.A.**

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

Por defecto lee **sólo los PDF de la carpeta indicada**, sin entrar en sus
subcarpetas: así lo que se procesa es exactamente lo que usted señaló. Los
informes del IESS vienen repartidos en una carpeta por práctica, y para esos
hay que pedirlo:

```bat
consolidador_dosis.exe "C:
uta\INFORMES_IESS" -r
```

o dejarlo fijo con `"buscar_en_subcarpetas": true` en `config.json`. El log dice
siempre cuántos PDF encontró y si miró o no las subcarpetas.

Los CSV se escriben **siempre en una sola carpeta de salida** (la indicada, o la
de origen si no se indica otra): aunque lea PDF de varias subcarpetas, nunca
escribe dentro de ellas.

Cada corrida vuelve a leer todos los PDF y reescribe los CSV, así que basta con
agregar informes nuevos a la carpeta y volver a ejecutar. Se deja además un
`lector_dosis.log` con el detalle de la corrida.

## Salida

Un archivo por centro, sede y período:

```
<CENTRO>_<SEDE>_<DESDE>_<HASTA>.csv
```

Ejemplo: `HOSPITAL_DE_LOS_VALLES_CUMBAYA_2024-03-18_2024-05-17.csv`

Las fechas son las del período del informe (`Lectura Desde` / `Lectura Hasta`).
Identifican el informe sin depender de que el laboratorio numere ciclos.

**Varios PDF pueden alimentar un mismo CSV**, porque se agrupa por centro, sede
y período, no por archivo:

* el **IESS** emite un archivo por magnitud —cuerpo entero (Hp(10)), cristalino
  (Hp(3)) y extremidades (Hp(0,07))— para el mismo período;
* **DOSISRAD** emite un archivo por departamento, todos del mismo bimestre.

En ambos casos los archivos del mismo período se juntan en un solo CSV, y las
lecturas de una misma persona quedan en su fila. El log dice de qué informes
salió cada archivo:

```
    -> SOLCA_MANABI_PORTOVIEJO_2026-04-12_2026-06-11.csv     105 usuarios
       a partir de 3 informes: dosis-4.pdf, dosis-5.pdf, dosis-6.pdf
```

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
| `buscar_en_subcarpetas` | Entrar en las subcarpetas de la carpeta de reportes. `true` por defecto. |
| `sede_por_defecto` | Sede a usar cuando el informe no la trae entre paréntesis. |
| `iess.hospital` / `iess.sede_por_defecto` | Centro y sede de los informes del IESS, que no los nombran. |
| `dosisrad.centros` | Fija centro y sede por `Código Nro`, porque el encabezado recorta el nombre. |
| `dosisrad.omitir_ambiental` | Omitir los dosímetros de área (`AMB`). `true` por defecto. |
| `practica_por_palabra_clave` | Normaliza la práctica por palabras clave (prefijo de palabra, sin tildes). |
| `mapa_practica` | Normaliza el título de tabla (`Área: ...`) que va en la columna `practica`. |
| `texto_ciclo_en_observacion` | Texto del ciclo anexado a `observacion`. `"Ciclo {ciclo}"` por defecto; `""` para no anexarlo. |
| `formato_ciclo` | Plantilla de la etiqueta del ciclo. `"{anio}-C{numero}"` por defecto. |
| `anio_del_ciclo` | De qué fecha sale el año: `"fin"` (actual) o `"inicio"`. |
| `fila_titulo_periodo` | Escribir la primera fila con el período de lectura. `true` por defecto. |
| `color_en_consola` | `"auto"` (actual), `"siempre"` o `"nunca"`. |
| `separador_csv` | `";"` por defecto. |
| `incluir_sede_en_nombre` | Incluir la sede en el nombre del archivo. |
| `patron_nombre_salida` | Marcadores: `{centro}` `{hospital}` `{sede}` `{desde}` `{hasta}` `{ciclo}` `{ciclo_num}`. |

Los niveles de registro (0.1 / 0.6 / 2 mSv) están fijados por el Acuerdo
Ministerial 245 y viven en `laboratorios/base.py`; no se editan por
configuración.

## Laboratorio del IESS

*Informe Dosimetría Personal Termoluminiscente*. Se reconoce por el encabezado
de la tabla —`N.- | NOMBRES Y APELLIDOS | CEDULA | CODIGO DE DOSIMETRO | DOSIS
(mSv) Hp(x) | DOSIS ANUAL ACUMULADA | FIRMA RECIBIDO`—, que es lo único que lo
identifica; de ahí sale también la magnitud, lo único que cambia entre
archivos.

| Dato | De dónde sale |
|---|---|
| Período | `Lectura: Bimensual Período: 01/01/25 - 28/02/25`. Es del informe completo: todas las filas heredan las mismas fechas, el formato no las repite por usuario. |
| Práctica y sede | La línea bajo `DATOS DE LA INSTITUCIÓN USUARIA`. Si sólo trae la práctica, la sede es la de `iess.sede_por_defecto` (**HECAM**). |
| Hospital | No aparece en el informe: se toma de `iess.hospital` (**HECAM**). |

**El orden de esa línea no importa.** `RADIODIAGNÓSTICO - GUAYAQUIL` y
`GUAYAQUIL - RADIODIAGNÓSTICO` dan el mismo resultado. Para decidirlo el
programa mira, en este orden:

1. si una de las partes está en `iess.sedes_conocidas`, esa es la sede;
2. si no, cuál de las dos se reconoce como práctica (por las palabras clave):
   la otra es la sede;
3. si ninguna de las dos reglas resuelve, asume *práctica - sede* y **lo anota
   como incidencia en el log**, indicando qué tomó como cada cosa.

Cuando aparezca una sede nueva conviene agregarla a `iess.sedes_conocidas`.

Las notas significan otra cosa que en DOSICONTROL:

| En el informe del IESS | En el CSV |
|---|---|
| `NR` (dosímetro **no retornado**) | `NE` |
| `NU` (no usado), `DD` (dañado) | `NE` |
| `<LD` (menor al límite detectable) | `NR` |

> Cuidado con el `NR`: en el informe del IESS significa *no retornado*, y en el
> CSV significa *por debajo del nivel de registro*. El código original queda en
> `observacion` para poder distinguirlos.

### Práctica por palabras clave

La práctica del IESS es texto libre, así que se normaliza buscando palabras
clave sin tildes ni mayúsculas (`practica_por_palabra_clave` en `config.json`).
Cada clave se busca como **prefijo de palabra**: `gastro` reconoce
*GASTROENTEROLOGÍA* y `urolog` reconoce *URÓLOGOS*. Manda el orden, así que las
reglas más específicas van primero.

| Práctica | Reconoce, entre otras |
|---|---|
| Intervencionismo | hemodinamia, intervencionismo, angiografía, cateterismo, electrofisiología, arritmias, marcapasos, traumatología, ortopedia, neurocirugía, gastroenterología, endoscopía, CPRE, urología, litotricia, vascular |
| Ciclotrón | ciclotrón |
| Medicina Nuclear | medicina nuclear, PET, SPECT, endocrinología, gammagrafía, radiofármacos |
| Radioterapia | radioterapia, teleterapia, braquiterapia, acelerador |
| Radiodiagnóstico | radiodiagnóstico, radiología, imagenología, tomografía, mamografía, densitometría, rayos X, fluoroscopía |

Se aplica sólo cuando `mapa_practica` (coincidencia exacta) no tiene entrada
para ese texto, así que las prácticas de DOSICONTROL siguen mandando sobre ella.

## Laboratorio DOSISRAD

*Laboratorio de Dosimetría Personal — Dosimetría Termoluminiscente TLD*. Se
reconoce por la palabra `DOSISRAD` del encabezado. Un informe por departamento,
con las tres magnitudes en columnas separadas más una de notas.

| Dato | De dónde sale |
|---|---|
| Período del informe | El **par de fechas Desde/Hasta más repetido** entre las filas. No el mínimo y el máximo: una sola lectura de dos períodos correría el inicio del informe entero. |
| Fechas del usuario | Las de su propia fila, sin tocar, así quien tiene una lectura de 121 días conserva su ventana real. |
| Centro y sede | `Nombre` hasta el guion, y `Ciudad` como sede. El encabezado **recorta el nombre** al ancho de su casilla (termina en `... DE MANABI - NUC`), así que la sede no se puede sacar de ahí. Con `dosisrad.centros` se fija por `Código Nro`. |
| Práctica | `NOMBRE DEL DEPARTAMENTO`. |
| Ciclo | La columna `Periodo` (número de medida del año), anexada a `observacion`. |

Cada fila es un dosímetro y su tipo dice qué magnitud mide: `CE` cuerpo entero y
`AMB` ambiental → Hp(10); `AL` anillo y `BR` brazalete → Hp(0,07); `CR`
cristalino → Hp(3). Los ambientales se omiten (`omitir_ambiental`) porque miden
un área, no a una persona.

Nomenclatura de notas, tomada del pie del propio informe:

| Nota | Significado | En el CSV |
|---|---|---|
| `DD` `DP` `DNU` `DNE` `UNL` | dañado, perdido, no utilizado, no entregado, dejó de laborar | `NE` |
| `<LD` | menor al límite de detección | `NR` |
| `DE2P` `DPI` `DMS` `PEM` | dos o más períodos, investigación, modificada SCAN, embarazo | conserva el valor, sólo se anota |

## Agregar otro laboratorio

Los tres laboratorios del país ya están implementados. Para uno nuevo:

1. Copie `laboratorios/iess.py` o `laboratorios/dosicontrol.py` a
   `laboratorios/<nuevo_lab>.py` y adapte `NOMBRE`, `detectar()`, `parsear()` y
   `fila_titulo()`.
2. Regístrelo en `laboratorios/__init__.py` (import + lista `PARSERS`).
3. Reconstruya con `construir_exe.bat`.

`detectar()` recibe el texto de la primera página y devuelve `True` si el
informe es de ese laboratorio. `parsear()` devuelve `(registros, avisos,
resumen)`. Los PDF que ningún parser reconoce se omiten y quedan anotados en el
log.

Los dos formatos ya implementados sirven de plantilla para casos distintos:
DOSICONTROL reconstruye la tabla por coordenadas (no tiene bordes) y el IESS la
lee con `extract_tables()` (sí los tiene).

## Cómo lee el PDF (por si hay que depurar)

No usa posiciones fijas de texto. Toma las coordenadas de cada palabra y:

* localiza el encabezado de tabla para fijar la **rejilla de las 9 columnas de
  dosis**, y asigna cada valor a su columna por posición horizontal — por eso no
  se descoloca cuando el PDF deja celdas vacías;
* reconstruye los nombres partidos en varias líneas, incluso cuando la fila
  queda cortada entre dos páginas;
* verifica que la numeración de filas de cada sección sea correlativa (1..N) y
  reporta en el log cualquier salto, que indicaría una fila no leída.

## Logotipo en pantalla

Al abrir, el programa dibuja el logotipo de **FISIQA** con el lema
*«Cuidamos con calidad, protegemos con ciencia»*. La **Q** —el anillo del
gantry— va en turquesa y el resto en azul, como el logotipo original.

Todo el arte es ASCII de 7 bits a propósito: la consola de Windows con
codificación heredada convierte en `?` cualquier carácter fuera de esa tabla.

El color se decide solo (`color_en_consola`):

| Valor | Qué hace |
|---|---|
| `"auto"` (por defecto) | Pinta en color sólo si la salida es una consola de verdad que entiende ANSI. Si se redirige a un archivo o a una tubería, sale en blanco y negro para no ensuciarlo. Respeta `NO_COLOR` y `TERM=dumb`. |
| `"siempre"` | Fuerza el color. |
| `"nunca"` | Nunca usa color. |

En Windows el programa le pide a la consola que interprete ANSI antes de
escribir; si no lo consigue, cae solo a blanco y negro.

**El logotipo se dibuja sólo en pantalla.** El archivo `lector_dosis.log`
conserva el encabezado corto de texto, para no llenarse de arte en cada
corrida.

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
