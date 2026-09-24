# Deploy del processing-service con Docker + GPU

Este documento cubre la Fase 7: dockerizar solo `processing-service`.
El frontend Next.js sigue corriendo localmente por ahora.

## Requisitos del host

- Linux con GPU NVIDIA.
- Driver NVIDIA funcionando:

```bash
nvidia-smi
```

- Docker instalado.
- NVIDIA Container Toolkit instalado y configurado para Docker.
- Espacio en disco suficiente. La imagen base CUDA y los wheels de PyTorch
  CUDA pesan varios GB; antes de buildear conviene confirmar al menos 8 GB
  libres. En esta maquina el build exitoso dejo la imagen ocupando mucho
  espacio, asi que conviene tener bastante mas margen si se va a reconstruir:

```bash
df -h .
```

### NVIDIA Container Toolkit en Manjaro/Arch

En Manjaro/Arch, si `docker run --gpus all ...` falla con:

```text
failed to discover GPU vendor from CDI: no known GPU vendor found
```

instalar y configurar el runtime NVIDIA:

```bash
sudo pacman -S --needed nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Despues de reiniciar Docker, validar:

```bash
docker run --rm --gpus all colorwind-processing:phase7 nvidia-smi
```

## Buildear la imagen

Desde la carpeta del servicio:

```bash
cd /home/ro/Desktop/proyects/ColorWind/processing-service
docker build -t colorwind-processing:phase7 .
```

El build usa `nvidia/cuda:12.8.1-runtime-ubuntu24.04`, compatible con
PyTorch `2.11.0+cu128`. Los wheels CUDA de PyTorch incluyen las librerias
necesarias para inferencia, por eso no hace falta usar la variante `cudnn`
como base. Las dependencias se instalan desde
`processing-service/requirements.txt`.

## Verificar GPU dentro del container

```bash
docker run --rm --gpus all colorwind-processing:phase7 nvidia-smi
```

Si este comando falla, revisar que el host tenga NVIDIA Container Toolkit
instalado y que Docker pueda exponer GPUs.

## Correr el servicio

```bash
docker run --rm --gpus all \
  --name colorwind-processing \
  -p 8000:8000 \
  colorwind-processing:phase7
```

El container levanta uvicorn en `0.0.0.0:8000`. El modelo `models/FastSAM-s.pt`
queda copiado dentro de la imagen.

## Probar /segment contra Docker

Con el container corriendo, desde la raiz del repo:

```bash
cd /home/ro/Desktop/proyects/ColorWind

processing-service/venv/bin/python <<'PYCODE'
import base64
import pathlib

import httpx

image = base64.b64encode(
    pathlib.Path("processing-service/test-images/flat-illustration.png").read_bytes()
).decode("ascii")

response = httpx.post(
    "http://127.0.0.1:8000/segment",
    json={
        "image_base64": image,
        "mode": "color",
        "num_zones": 12,
        "min_zone_area_px": 500,
    },
    timeout=120,
)
response.raise_for_status()
data = response.json()
print("project_id=", data["project_id"])
print("zones=", len(data["zones_geojson"]["features"]))
print("palettes=", [palette["name"] for palette in data["suggested_palettes"]])
PYCODE
```

Los logs del container deben mostrar `device=cuda:0` y el nombre de la GPU.

## Variables y configuracion

El servicio no depende de paths locales del host dentro del container. Usa:

- `YOLO_CONFIG_DIR=/tmp/ultralytics`
- `MPLCONFIGDIR=/tmp/matplotlib`
- runtime local del container en `/app/runtime/uploads`

Para correr el frontend contra un processing-service remoto, ajustar la URL
del proxy de Next.js con:

```bash
PROCESSING_SERVICE_URL=http://HOST_O_IP_DEL_SERVIDOR:8000
```

En nube, exponer el puerto `8000` directamente solo en redes de confianza o
detras de un reverse proxy.
