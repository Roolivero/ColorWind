# Instalacion local privada

Esta guia es para correr ColorWind en una PC local, sin publicar la app y sin
usar Docker. Es el modo recomendado mientras el proyecto siga en uso privado o
compartido con alguien de confianza para pruebas locales.

Docker queda reservado para deploy futuro o migracion a un servidor con GPU.
Para eso ver `docs/DEPLOY.md`.

## Requisitos

- Linux.
- GPU NVIDIA recomendada.
- Driver NVIDIA funcionando:

```bash
nvidia-smi
```

- Python con `venv`. El entorno actual fue probado con Python 3.12.
- `pnpm` para el frontend. El proyecto declara `pnpm@11.3.0`.
- Espacio en disco suficiente. El entorno Python con PyTorch CUDA puede pesar
  varios GB; conviene tener al menos 10 GB libres antes de instalar.

```bash
df -h .
```

## Clonar o copiar el proyecto

En la maquina principal de desarrollo, la ruta actual es:

```bash
cd /home/ro/Desktop/proyects/ColorWind
```

En otra PC, entrar a la carpeta donde se haya clonado o copiado el repo.

## Backend: processing-service

Crear el entorno virtual si no existe:

```bash
python3 -m venv processing-service/venv
```

Instalar dependencias:

```bash
processing-service/venv/bin/python -m pip install --upgrade pip
processing-service/venv/bin/python -m pip install -r processing-service/requirements.txt
```

El `requirements.txt` ya incluye el indice extra de PyTorch CUDA `cu128`.

Verificar que PyTorch ve la GPU:

```bash
processing-service/venv/bin/python -c "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'SIN GPU')"
```

La salida esperada debe incluir `True` y el nombre de la GPU NVIDIA.

Levantar la API:

```bash
cd processing-service
venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

`--reload` es para uso local/desarrollo: aplica cambios del backend sin
reiniciar a mano. Cada reload reinicia el proceso, asi que se pierden los
`project_id` guardados en memoria por el servicio.

En los logs de arranque deberia aparecer:

```text
device=cuda:0
```

Dejar esta terminal abierta.

## Frontend: web

En otra terminal:

```bash
cd web
pnpm install
pnpm dev
```

Abrir:

```text
http://127.0.0.1:3000
```

El frontend llama a los route handlers de Next.js (`/api/...`) y estos
reenvian al processing-service local en:

```text
http://127.0.0.1:8000
```

## Variable opcional

Si el processing-service corre en otra maquina de la red, levantar el frontend
con:

```bash
PROCESSING_SERVICE_URL=http://IP_DE_LA_MAQUINA:8000 pnpm dev
```

Para uso local en la misma PC no hace falta configurar nada.

## Prueba rapida

1. Confirmar que la API esta corriendo en `http://127.0.0.1:8000`.
2. Confirmar que el frontend esta corriendo en `http://127.0.0.1:3000`.
3. Subir una imagen JPG o PNG.
4. Elegir modo color o blanco y negro.
5. Tocar `Generar preview`.
6. Cambiar colores o descargar PDF para confirmar el flujo completo.

## Archivos locales que no hace falta compartir

- `processing-service/venv/`
- `processing-service/runtime/`
- `processing-service/outputs/`
- `web/node_modules/`
- `web/db/app.db`
- `.next/`

Si se comparte el proyecto con otra persona, lo ideal es compartir el codigo y
que esa persona instale dependencias en su propia maquina.

## Problemas frecuentes

### El backend no usa GPU

Revisar:

```bash
nvidia-smi
processing-service/venv/bin/python -c "import torch; print(torch.cuda.is_available())"
```

Si `torch.cuda.is_available()` da `False`, el problema esta en drivers CUDA,
PyTorch CUDA o compatibilidad de la GPU.

### El frontend muestra error al generar preview

Confirmar que `processing-service` esta corriendo:

```bash
curl http://127.0.0.1:8000/docs
```

Si no responde, volver a levantar uvicorn.

### Falta espacio en disco

Revisar:

```bash
df -h .
du -sh processing-service/venv web/node_modules web/.next 2>/dev/null
```

El entorno Python con PyTorch CUDA y las dependencias del frontend pueden ser
pesados. Para desarrollo local no hace falta conservar imagenes Docker.
