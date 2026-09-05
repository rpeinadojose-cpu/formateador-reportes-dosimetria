# De qué se trata este proyecto

**Ejecutable que convierte informes PDF de dosimetría personal en CSV**, uno por
informe, para llevar el control de dosis del personal ocupacionalmente expuesto
(POE) de un hospital.

El administrador deja los PDF que envía el laboratorio en una carpeta, hace
doble clic en `consolidador_dosis.exe`, y obtiene un CSV por informe listo para
Excel o Power BI. Antes esto se hacía a mano: importar tablas a Excel, pegarlas
como valores, renombrar, y un Power Query largo para limpiarlas.

---

## El problema que resuelve

En Ecuador hay **tres laboratorios** que prestan servicio de dosimetría, cada
uno con su propio formato de informe. Un mismo hospital puede tener varias sedes
y cada trabajador puede llevar varios dosímetros a la vez. Extraer esto a mano
es lento y se presta a errores silenciosos.

Hoy está implementado **DOSICONTROL S.A.S.** (informe `DC-008`). Faltan los
otros dos: la arquitectura ya está preparada para ellos.

## Qué produce

`<CENTRO>_<SEDE>_<DESDE>_<HASTA>.csv`, por ejemplo
`HOSPITAL_DE_LOS_VALLES_CUMBAYA_2024-09-18_2024-11-17.csv`:

```
Lectura Desde;2024/09/18;Lectura Hasta;2024/11/17
cedula;nombre;fecha_inicio;fecha_fin;hp10;hp007;hp3;hospital;sede;practica;observacion
1700000000;Nombre Apellido Ejemplo;2024/05/18;2024/11/17;0.358;5.370;0.668;HOSPITAL EJEMPLO;CUMBAYA;Hemodinamia e intervencionismo;DOP | Ciclo 2024-C7
```

La primera fila es el período de lectura del informe; la segunda, los
encabezados. Al importar hay que **saltar la primera fila**.

---

## Las reglas del dominio (lo que hay que recordar)

Vienen del **Acuerdo Ministerial 245** (28-feb-2015) y de cómo escribe los
informes el laboratorio.

| Situación en el informe | Valor en el CSV |
|---|---|
| Lectura bajo el nivel de registro: Hp(10) < 0.1, Hp(3) < 0.6, Hp(0.07) < 2 mSv | `NR` |
| Nota `NC` (dosímetro no canjeado) | `NE` |
| El usuario no tiene dosímetro que mida esa magnitud | *celda en blanco* |

La distinción entre **`NR` y celda vacía** es la que más cuesta recordar: `NR`
significa *se midió y dio por debajo del umbral*; vacío significa *nunca hubo
dosímetro de ese tipo*. No son lo mismo para el cálculo de dosis acumulada.

### Una fila por usuario

Un trabajador puede llevar tres dosímetros a la vez: el de cuerpo entero (serie
de 9 dígitos, reporta Hp(10)) y uno o dos de anillo o cristalino (serie de 4–5
dígitos, reportan Hp(3) o Hp(0.07)). El informe le dedica **una fila a cada
dosímetro** y rellena con `--` las columnas que ese dosímetro no mide.

El programa toma cada magnitud del dosímetro que realmente la mide y las junta
en una sola fila por persona. Sobre los datos reales no hubo un solo conflicto:
nunca dos dosímetros de un mismo usuario reportan la misma magnitud.

### Ciclos

DOSICONTROL numera ciclos bimensuales de corrido desde el inicio del contrato
(no reinicia cada año), así que algunos cruzan el cambio de año. Como **no todos
los laboratorios usan ciclos**, dejó de ser columna: cuando el informe lo trae,
se anexa a `observacion` como `Ciclo 2024-C7`. El año sale de la fecha de fin
del período.

---

## Decisiones que se tomaron (y por qué)

| Decisión | Razón |
|---|---|
| La **sede** va en el nombre del archivo | Sin ella, dos sedes del mismo hospital en el mismo período se sobrescriben. |
| El nombre usa el **período**, no el ciclo | Identifica el informe aunque el laboratorio no numere ciclos. |
| Separador `;` y UTF-8 con BOM | Excel en español abre bien las tildes sin pasos extra. |
| El separador interno de `observacion` es ` \| ` | Si fuera `;` chocaría con el del CSV y obligaría a entrecomillar la celda. |
| La fila de título la arma **cada laboratorio** | Cada formato rotula el período a su manera. |
| Lectura por **coordenadas**, no por posiciones de texto | Las columnas se asignan por posición horizontal contra la rejilla del encabezado, así no se descoloca cuando el PDF deja celdas vacías. |

Casi todo esto es configurable en `config.json` sin recompilar.

## El bug que enseñó a desconfiar

El parser reconocía el bloque de firmas del pie por el título profesional
(`Ing.`, `Fís.`…) y dejaba de leer el resto de la hoja. En un hospital la
persona de contacto tenía título en el encabezado de la página 1 — el parser lo
tomó por el pie y **perdió la página 1 completa de los 9 informes**: 117
usuarios menos.

Lo grave: sólo 4 de 9 informes reportaron algo, porque el chequeo dependía de un
salto visible en la numeración. Desde entonces el log verifica **por sección**
que la cantidad de filas leídas coincida con la numeración del informe, y avisa
cuando una página con tabla no aporta ninguna fila. Si el log dice *«Sin
incidencias»*, es que todas las filas numeradas se leyeron.

**Revisar siempre el log antes de usar un CSV.**

---

## Estructura

```
consolidador_dosis.py      programa principal: CSV, agrupación, log, config
laboratorios/
    base.py                Registro, umbrales, conversión NR/NE, utilidades
    dosicontrol.py         parser del formato DC-008
    __init__.py            registro de parsers y detección automática
LEEME.md                   manual de uso y configuración
construir_exe.bat          compila el .exe con PyInstaller
```

Para agregar un laboratorio: copiar `dosicontrol.py`, adaptar `NOMBRE`,
`detectar()`, `parsear()` y `fila_titulo()`, y registrarlo en `__init__.py`.
`detectar()` recibe el texto de la primera página y dice si el informe es suyo.

## Estado

- **DOSICONTROL** implementado y verificado sobre 32 informes reales de dos
  hospitales (1965 usuarios), sin incidencias.
- **Pendiente:** los otros dos laboratorios del país.

## Privacidad

Los PDF y los CSV llevan cédula y nombre de trabajadores reales. El
`.gitignore` los excluye del repositorio; los ejemplos de la documentación están
anonimizados. **Nunca subir informes ni resultados.**
