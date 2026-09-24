# Proyecto: Pintar por Números para Adultos

## Objetivo

App que convierte una imagen (a color o ya en blanco y negro) en un dibujo de
"pintar por números" para adultos, con:

- Zonas numeradas, con tamaño y cantidad ajustables.
- Edición manual de zonas (fusionar / dividir).
- Paletas de colores sugeridas y editables, asociadas a cada número.
- Export final en PDF listo para imprimir.

## Cómo usar esta documentación

Estos documentos son la especificación completa del proyecto, pensados para
que un agente de código los use como guía de implementación:

- `ARCHITECTURE.md` — decisiones de stack y por qué, estructura de componentes.
- `FUNCTIONAL_SPEC.md` — flujo funcional completo, paso a paso, con el detalle
  de cada control de UI.
- `API_CONTRACT.md` — contrato de la API entre el frontend y el servicio de
  procesamiento.
- `ROADMAP.md` — orden de trabajo sugerido, en fases, con criterio de "hecho"
  para cada una.
- `SETUP.md` — instrucciones para crear el entorno, probar la GPU y levantar
  el servicio de procesamiento local y el frontend.
- `DEPLOY.md` — instrucciones para buildear y correr `processing-service` con
  Docker y GPU.

## Instrucciones generales para el agente

- Avanzar fase por fase según `ROADMAP.md`, sin saltar pasos.
- Ante cualquier ambigüedad en estos documentos, preguntar antes de asumir.
- No cambiar las decisiones de stack de `ARCHITECTURE.md` sin señalarlo
  explícitamente y explicar el motivo.
- Priorizar que cada fase quede funcional y testeable antes de avanzar a la
  siguiente.
- El resultado final para colorear siempre es en blanco y negro, sin importar
  si la imagen de entrada era a color o ya en blanco y negro.
