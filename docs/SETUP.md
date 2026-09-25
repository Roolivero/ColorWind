# Guia para levantar el proyecto

Estado actual: estan implementadas las Fases 1 a 6 del roadmap:
`processing-service` con segmentacion por GPU, API `/segment`, endpoints de
merge/split, frontend Next.js con upload/preview, editor de paleta, edicion
manual de zonas, persistencia SQLite y export PDF.

Para una guia corta de instalacion local privada, ver
`docs/LOCAL_INSTALL.md`. Para correr `processing-service` dockerizado con GPU,
ver `docs/DEPLOY.md`.

## Requisitos

- Linux con GPU NVIDIA accesible.
- Driver NVIDIA funcionando. Verificar con:

```bash
nvidia-smi
```

- Python 3.14 en esta maquina de desarrollo.
- Espacio en disco suficiente. El `venv` con PyTorch CUDA pesa alrededor de
  `7.2G`, asi que conviene tener varios GB libres antes de instalar.

```bash
df -h .
```

## Crear o reusar el entorno Python

Desde la raiz del repo:

```bash
cd /home/ro/Desktop/proyects/ColorWind
```

Si el entorno ya existe, reusarlo:

```bash
processing-service/venv/bin/python --version
```

Si no existe, crearlo con `venv`:

```bash
python3 -m venv processing-service/venv
```

Instalar dependencias:

```bash
processing-service/venv/bin/python -m pip install -r processing-service/requirements.txt
```

El `requirements.txt` incluye el indice extra de PyTorch CUDA `cu128`, usado
por el servicio actual.

## Verificar CUDA en PyTorch

```bash
processing-service/venv/bin/python -c "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

La salida esperada en esta maquina incluye:

```text
True
NVIDIA GeForce RTX 2060
```

## Generar imagenes de prueba

Si no existen las imagenes placeholder de Fase 1:

```bash
processing-service/venv/bin/python processing-service/generate_test_images.py
```

Esto crea:

- `processing-service/test-images/realistic-placeholder.png`
- `processing-service/test-images/flat-illustration.png`
- `processing-service/test-images/line-art-bw.png`

## Probar el script de segmentacion

```bash
processing-service/venv/bin/python processing-service/segment_image.py \
  processing-service/test-images/realistic-placeholder.png \
  processing-service/test-images/flat-illustration.png \
  processing-service/test-images/line-art-bw.png
```

El script debe loggear `device=cuda:0` y la GPU detectada. Genera salidas en:

- `processing-service/outputs/*.zones.png`
- `processing-service/outputs/*.polygons.json`

## Levantar la API

Desde `processing-service`:

```bash
cd /home/ro/Desktop/proyects/ColorWind/processing-service
venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

En el arranque debe aparecer algo como:

```text
INFO device=cuda:0 gpu=NVIDIA GeForce RTX 2060
INFO FastAPI startup device=cuda:0 gpu=NVIDIA GeForce RTX 2060
```

## Levantar el frontend

En esta maquina `npm` no esta disponible en PATH, pero si `pnpm`. Desde la
raiz del repo:

```bash
cd /home/ro/Desktop/proyects/ColorWind/web
pnpm install --store-dir /tmp/pnpm-store
pnpm dev
```

La app queda disponible en:

```text
http://127.0.0.1:3000
```

El frontend no llama al servicio Python directamente desde el navegador. Llama
a `/api/segment` dentro de Next.js, y ese route handler reenvia el pedido a:

```text
http://127.0.0.1:8000/segment
```

Esto evita problemas de CORS entre puertos distintos y permite mostrar un error
claro si el servicio de procesamiento no esta corriendo.

## Probar POST /segment con curl

En otra terminal, desde la raiz del repo:

```bash
cd /home/ro/Desktop/proyects/ColorWind
IMAGE_B64="$(base64 -w 0 processing-service/test-images/flat-illustration.png)"

curl -s http://127.0.0.1:8000/segment \
  -H "Content-Type: application/json" \
  -d "{\"image_base64\":\"$IMAGE_B64\",\"mode\":\"color\",\"num_colors\":12,\"detail_level\":0.5}" \
  | processing-service/venv/bin/python -m json.tool
```

La respuesta debe tener exactamente estos campos de primer nivel:

```json
{
  "project_id": "...",
  "zones_svg": "...",
  "zones_geojson": {},
  "suggested_palettes": []
}
```

## Smoke test con las 3 imagenes

Con el servidor corriendo:

```bash
processing-service/venv/bin/python <<'PY'
import base64
import pathlib

import httpx

cases = [
    ("processing-service/test-images/realistic-placeholder.png", "color"),
    ("processing-service/test-images/flat-illustration.png", "color"),
    ("processing-service/test-images/line-art-bw.png", "bw"),
]
expected = {"project_id", "zones_svg", "zones_geojson", "suggested_palettes"}
client = httpx.Client(timeout=120)

for path, mode in cases:
    image = base64.b64encode(pathlib.Path(path).read_bytes()).decode("ascii")
    response = client.post(
        "http://127.0.0.1:8000/segment",
        json={
            "image_base64": image,
            "mode": mode,
            "num_colors": 12,
            "detail_level": 0.5,
        },
    )
    response.raise_for_status()
    data = response.json()
    assert set(data.keys()) == expected
    print(
        path,
        "project_id=",
        data["project_id"],
        "zones=",
        len(data["zones_geojson"]["features"]),
        "palettes=",
        [palette["name"] for palette in data["suggested_palettes"]],
    )
PY
```

## Notas utiles

- El servicio mantiene el modelo FastSAM cargado una sola vez por proceso.
- El estado transitorio generado por `/segment` queda en memoria del proceso
  FastAPI. Si se reinicia uvicorn, esos `project_id` se pierden.
- La web app persiste el proyecto actual y las paletas custom en SQLite, en
  `web/db/app.db`. Ese archivo es local y esta ignorado por git.
- En el entorno de Codex, el acceso a GPU y a `127.0.0.1` puede requerir
  permisos escalados por sandbox. En una terminal normal del usuario no deberia
  hacer falta.
