# Especificación funcional

## Flujo completo (paso a paso)

### 1. Subida de imagen

- Input: JPG o PNG.
- El usuario elige el modo: "Imagen a color" o "Imagen en blanco y negro /
  línea".
  - Modo color → pipeline de segmentación + cuantización de color.
  - Modo B/N → pipeline de detección de regiones cerradas (sin información
    de color de origen).
- Sugerido: detectar automáticamente el modo probable, pero dejar el toggle
  manual siempre visible por si el usuario quiere corregirlo.

### 2. Configuración inicial (antes de procesar)

- Slider "Cantidad de colores/números" (rango sugerido: 5–30). Esta cantidad
  es exacta: son los números que aparecen en el dibujo y las entradas de la
  paleta, siempre que existan suficientes zonas geométricas para repartirlos.
- Slider "Nivel de detalle" (bajo/medio/alto, con puntos intermedios). Este
  control afecta cuántas zonas geométricas intenta conservar/generar el
  algoritmo, pero no promete una cantidad exacta de zonas.
- Botón "Generar preview".

### 3. Preview y ajuste

- Vista del resultado en blanco y negro con números superpuestos.
- Toggle para ver la versión a color de referencia (con la paleta aplicada).
- Los sliders del paso 2 se pueden volver a mover y regenerar sin re-subir
  la imagen.

### 4. Edición manual de zonas

- Herramienta seleccionable con dos modos:
  - **Fusionar**: click en una zona + click en una zona vecina → se unen en
    una sola. Si las zonas tienen distinto número/color, se conserva por
    defecto el de la zona más grande (mostrar confirmación si la diferencia
    de tamaño es chica). Si ambas zonas ya comparten número/color, no hace
    falta confirmar.
  - **Dividir**: el usuario traza una línea dentro de una zona → se genera
    una nueva zona geométrica. Ambas piezas conservan el mismo número/color
    de pintura de la zona original.
- Después de cada fusión/división: renumerar automáticamente todas las
  zonas geométricas para no dejar huecos en `zone_id`, sin cambiar la
  cantidad de números/colores elegida por el usuario.

### 5. Editor de paleta

- Lista de números de pintura, cada uno con su color asignado (swatch). Un
  mismo número puede aparecer en varias zonas geométricas.
- El usuario puede:
  - Elegir entre 2–3 paletas sugeridas automáticas, generadas a partir de
    los colores dominantes reales de la imagen (relaciones de color:
    complementaria, análoga, triádica).
  - Elegir entre paletas curadas predefinidas (ej. "acuarela", "pastel",
    "vibrante").
  - Editar manualmente el color de cualquier número con un color picker.
  - Guardar la paleta editada como "paleta custom" para reusar en otro
    proyecto.
- El preview a color (del paso 3) se actualiza en vivo al cambiar colores.

### 6. Export a PDF

- Botón "Descargar PDF".
- Contenido del PDF:
  - Página con el dibujo en líneas + números.
  - Página/sección de leyenda: número + swatch + código hex del color.
- Opciones: tamaño de papel (A4/Carta), orientación (vertical/horizontal).
- Calidad: mínimo 300dpi.

## Reglas de negocio importantes

- El resultado final (para colorear) siempre es en blanco y negro, sin
  importar el modo de entrada.
- La numeración nunca debe tener huecos (ej. no puede faltar el número 4 si
  hay un 3 y un 5). Esto aplica a los números/colores elegidos por el
  usuario; las zonas geométricas tienen sus propios ids internos.
- Toda edición manual de zonas debe ser reversible (undo) al menos un paso
  atrás.
