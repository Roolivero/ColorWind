"use client";

import { ChangeEvent, DragEvent, useMemo, useRef, useState } from "react";
import { ImagePlus, Loader2, RefreshCw, Upload } from "lucide-react";

type Mode = "color" | "bw";
type PreviewView = "zones" | "reference";

type ZoneFeature = {
  id?: string;
  properties?: {
    zone_id?: string;
  };
  geometry: {
    type: "Polygon";
    coordinates: number[][][];
  };
};

type SegmentResponse = {
  project_id: string;
  zones_svg: string;
  zones_geojson: {
    type: "FeatureCollection";
    properties?: {
      width?: number;
      height?: number;
    };
    features: ZoneFeature[];
  };
  suggested_palettes: Array<{
    name: string;
    colors: Record<string, string>;
  }>;
};

const minZoneAreaOptions = {
  min: 100,
  max: 3000,
  step: 50,
};

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result);
      resolve(result.includes(",") ? result.split(",", 2)[1] : result);
    };
    reader.onerror = () => reject(new Error("No se pudo leer la imagen."));
    reader.readAsDataURL(file);
  });
}

function polygonCentroid(points: number[][]) {
  const ring = points.length > 1 ? points.slice(0, -1) : points;
  if (!ring.length) {
    return { x: 0, y: 0 };
  }

  let twiceArea = 0;
  let x = 0;
  let y = 0;

  for (let index = 0; index < ring.length; index += 1) {
    const current = ring[index];
    const next = ring[(index + 1) % ring.length];
    const cross = current[0] * next[1] - next[0] * current[1];
    twiceArea += cross;
    x += (current[0] + next[0]) * cross;
    y += (current[1] + next[1]) * cross;
  }

  if (Math.abs(twiceArea) < 0.001) {
    const sums = ring.reduce(
      (accumulator, point) => ({
        x: accumulator.x + point[0],
        y: accumulator.y + point[1],
      }),
      { x: 0, y: 0 },
    );
    return { x: sums.x / ring.length, y: sums.y / ring.length };
  }

  return {
    x: x / (3 * twiceArea),
    y: y / (3 * twiceArea),
  };
}

export default function Home() {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [imageBase64, setImageBase64] = useState<string>("");
  const [referenceUrl, setReferenceUrl] = useState<string>("");
  const [mode, setMode] = useState<Mode>("color");
  const [numZones, setNumZones] = useState(12);
  const [minZoneAreaPx, setMinZoneAreaPx] = useState(500);
  const [result, setResult] = useState<SegmentResponse | null>(null);
  const [previewView, setPreviewView] = useState<PreviewView>("zones");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [isDragging, setIsDragging] = useState(false);

  const zoneCount = useMemo(
    () => result?.zones_geojson.features.length ?? 0,
    [result],
  );
  const previewSize = result?.zones_geojson.properties;
  const previewWidth = previewSize?.width ?? 0;
  const previewHeight = previewSize?.height ?? 0;
  const zoneLabels = useMemo(() => {
    if (!result) {
      return [];
    }

    return result.zones_geojson.features.map((feature, index) => {
      const ring = feature.geometry.coordinates[0] ?? [];
      const centroid = polygonCentroid(ring);
      return {
        id: feature.properties?.zone_id ?? feature.id ?? String(index + 1),
        x: centroid.x,
        y: centroid.y,
      };
    });
  }, [result]);

  async function acceptFile(nextFile: File) {
    if (!["image/jpeg", "image/png"].includes(nextFile.type)) {
      setError("Subi una imagen JPG o PNG.");
      return;
    }

    setError("");
    setResult(null);
    setFile(nextFile);
    setReferenceUrl(URL.createObjectURL(nextFile));

    try {
      setImageBase64(await fileToBase64(nextFile));
    } catch (readError) {
      setImageBase64("");
      setError(readError instanceof Error ? readError.message : "No se pudo leer la imagen.");
    }
  }

  function onFileInput(event: ChangeEvent<HTMLInputElement>) {
    const nextFile = event.target.files?.[0];
    if (nextFile) {
      void acceptFile(nextFile);
    }
  }

  function onDrop(event: DragEvent<HTMLButtonElement>) {
    event.preventDefault();
    setIsDragging(false);
    const nextFile = event.dataTransfer.files?.[0];
    if (nextFile) {
      void acceptFile(nextFile);
    }
  }

  async function generatePreview() {
    if (!imageBase64) {
      setError("Subi una imagen antes de generar el preview.");
      return;
    }

    setIsLoading(true);
    setError("");

    try {
      const response = await fetch("/api/segment", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          image_base64: imageBase64,
          mode,
          num_zones: numZones,
          min_zone_area_px: minZoneAreaPx,
        }),
      });

      const data = await response.json();
      if (!response.ok) {
        throw new Error(
          data?.error ??
            data?.detail ??
            "No se pudo generar el preview con el servicio de procesamiento.",
        );
      }

      setResult(data as SegmentResponse);
      setPreviewView("zones");
    } catch (requestError) {
      setResult(null);
      setError(
        requestError instanceof Error
          ? requestError.message
          : "No se pudo contactar el servicio de procesamiento.",
      );
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <main className="min-h-screen px-5 py-6 text-[var(--foreground)] md:px-8">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-6">
        <header className="flex flex-col gap-2 border-b border-[var(--line)] pb-5 md:flex-row md:items-end md:justify-between">
          <div>
            <p className="text-sm font-medium uppercase tracking-[0.18em] text-[var(--accent-strong)]">
              ColorWind
            </p>
            <h1 className="text-3xl font-semibold tracking-normal md:text-4xl">
              Pintar por numeros
            </h1>
          </div>
          <p className="max-w-xl text-sm leading-6 text-[#5d6459]">
            Subi una imagen, ajusta las zonas y genera una vista en lineas lista
            para revisar.
          </p>
        </header>

        <section className="grid gap-5 lg:grid-cols-[360px_minmax(0,1fr)]">
          <aside className="flex flex-col gap-4">
            <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] p-4 shadow-sm">
              <input
                ref={inputRef}
                type="file"
                accept="image/png,image/jpeg"
                className="hidden"
                onChange={onFileInput}
              />
              <button
                type="button"
                onClick={() => inputRef.current?.click()}
                onDragEnter={() => setIsDragging(true)}
                onDragLeave={() => setIsDragging(false)}
                onDragOver={(event) => event.preventDefault()}
                onDrop={onDrop}
                className={`flex min-h-52 w-full flex-col items-center justify-center gap-3 rounded-md border border-dashed p-5 text-center transition ${
                  isDragging
                    ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                    : "border-[#aeb8a7] bg-[var(--panel-muted)] hover:border-[var(--accent)]"
                }`}
              >
                <span className="flex size-11 items-center justify-center rounded-md bg-white text-[var(--accent-strong)] shadow-sm">
                  <ImagePlus size={24} aria-hidden="true" />
                </span>
                <span className="text-base font-medium">Subir JPG o PNG</span>
                <span className="text-sm leading-5 text-[#66705f]">
                  Arrastra una imagen o elegila desde el sistema.
                </span>
              </button>
              {file ? (
                <div className="mt-3 rounded-md bg-[var(--panel-muted)] px-3 py-2 text-sm text-[#434a40]">
                  {file.name}
                </div>
              ) : null}
            </div>

            <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] p-4 shadow-sm">
              <fieldset className="space-y-3">
                <legend className="text-sm font-semibold">Modo de imagen</legend>
                <div className="grid grid-cols-2 gap-2 rounded-md bg-[var(--panel-muted)] p-1">
                  <button
                    type="button"
                    onClick={() => setMode("color")}
                    className={`rounded px-3 py-2 text-sm font-medium ${
                      mode === "color"
                        ? "bg-white text-[var(--accent-strong)] shadow-sm"
                        : "text-[#596256]"
                    }`}
                  >
                    Color
                  </button>
                  <button
                    type="button"
                    onClick={() => setMode("bw")}
                    className={`rounded px-3 py-2 text-sm font-medium ${
                      mode === "bw"
                        ? "bg-white text-[var(--accent-strong)] shadow-sm"
                        : "text-[#596256]"
                    }`}
                  >
                    Linea
                  </button>
                </div>
              </fieldset>

              <div className="mt-5 space-y-5">
                <label className="block">
                  <span className="flex items-center justify-between text-sm font-semibold">
                    <span>Cantidad de colores/zonas</span>
                    <span>{numZones}</span>
                  </span>
                  <input
                    type="range"
                    min={5}
                    max={30}
                    value={numZones}
                    onChange={(event) => setNumZones(Number(event.target.value))}
                    className="mt-3 w-full accent-[var(--accent)]"
                  />
                </label>

                <label className="block">
                  <span className="flex items-center justify-between text-sm font-semibold">
                    <span>Tamano minimo de zona</span>
                    <span>{minZoneAreaPx}px</span>
                  </span>
                  <input
                    type="range"
                    min={minZoneAreaOptions.min}
                    max={minZoneAreaOptions.max}
                    step={minZoneAreaOptions.step}
                    value={minZoneAreaPx}
                    onChange={(event) => setMinZoneAreaPx(Number(event.target.value))}
                    className="mt-3 w-full accent-[var(--accent)]"
                  />
                </label>
              </div>

              <button
                type="button"
                onClick={() => void generatePreview()}
                disabled={isLoading || !imageBase64}
                className="mt-6 inline-flex h-11 w-full items-center justify-center gap-2 rounded-md bg-[var(--accent)] px-4 text-sm font-semibold text-white shadow-sm transition hover:bg-[var(--accent-strong)] disabled:cursor-not-allowed disabled:bg-[#9eb8b4]"
              >
                {isLoading ? (
                  <Loader2 className="animate-spin" size={18} aria-hidden="true" />
                ) : result ? (
                  <RefreshCw size={18} aria-hidden="true" />
                ) : (
                  <Upload size={18} aria-hidden="true" />
                )}
                {isLoading ? "Generando" : result ? "Regenerar preview" : "Generar preview"}
              </button>
            </div>

            {error ? (
              <div className="rounded-lg border border-[#e2a2a8] bg-[#fff5f5] p-4 text-sm leading-6 text-[var(--danger)]">
                {error}
              </div>
            ) : null}
          </aside>

          <section className="min-h-[620px] rounded-lg border border-[var(--line)] bg-[var(--panel)] p-4 shadow-sm">
            <div className="mb-4 flex flex-col gap-3 border-b border-[var(--line)] pb-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-lg font-semibold">Preview</h2>
                <p className="text-sm text-[#657061]">
                  {result
                    ? `${zoneCount} zonas generadas`
                    : "El resultado segmentado aparece aca."}
                </p>
              </div>
              {result ? (
                <div className="flex flex-wrap items-center gap-2">
                  <div className="grid grid-cols-2 rounded-md bg-[var(--panel-muted)] p-1">
                    <button
                      type="button"
                      onClick={() => setPreviewView("zones")}
                      className={`rounded px-3 py-1.5 text-sm font-medium ${
                        previewView === "zones"
                          ? "bg-white text-[var(--accent-strong)] shadow-sm"
                          : "text-[#596256]"
                      }`}
                    >
                      Lineas
                    </button>
                    <button
                      type="button"
                      onClick={() => setPreviewView("reference")}
                      className={`rounded px-3 py-1.5 text-sm font-medium ${
                        previewView === "reference"
                          ? "bg-white text-[var(--accent-strong)] shadow-sm"
                          : "text-[#596256]"
                      }`}
                    >
                      Referencia
                    </button>
                  </div>
                  <span className="rounded-md bg-[var(--accent-soft)] px-3 py-1 text-sm font-medium text-[var(--accent-strong)]">
                    Proyecto {result.project_id.slice(0, 8)}
                  </span>
                </div>
              ) : null}
            </div>

            <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_260px]">
              <div className="flex min-h-[500px] items-center justify-center overflow-auto rounded-md bg-[#fbfcf9] p-4">
                {isLoading ? (
                  <div className="flex flex-col items-center gap-3 text-[#66705f]">
                    <Loader2 className="animate-spin text-[var(--accent)]" size={30} />
                    <span className="text-sm font-medium">Procesando imagen</span>
                  </div>
                ) : result && previewView === "zones" ? (
                  <div className="relative w-full max-w-4xl">
                    <div
                      className="[&_svg]:h-auto [&_svg]:max-h-[70vh] [&_svg]:w-full"
                      dangerouslySetInnerHTML={{ __html: result.zones_svg }}
                    />
                    {previewWidth > 0 && previewHeight > 0 ? (
                      <svg
                        className="pointer-events-none absolute inset-0 h-full w-full"
                        viewBox={`0 0 ${previewWidth} ${previewHeight}`}
                        aria-hidden="true"
                      >
                        {zoneLabels.map((label) => (
                          <text
                            key={label.id}
                            x={label.x}
                            y={label.y}
                            textAnchor="middle"
                            dominantBaseline="central"
                            fontSize={Math.max(12, Math.min(previewWidth, previewHeight) / 34)}
                            fontWeight={700}
                            fill="#111111"
                            paintOrder="stroke"
                            stroke="#ffffff"
                            strokeWidth={4}
                          >
                            {label.id}
                          </text>
                        ))}
                      </svg>
                    ) : null}
                  </div>
                ) : referenceUrl ? (
                  <img
                    src={referenceUrl}
                    alt="Imagen subida"
                    className="max-h-[70vh] w-auto max-w-full rounded-md object-contain"
                  />
                ) : (
                  <div className="flex flex-col items-center gap-3 text-center text-[#66705f]">
                    <ImagePlus size={32} aria-hidden="true" />
                    <span className="text-sm font-medium">Sin imagen cargada</span>
                  </div>
                )}
              </div>

              <div className="rounded-md bg-[var(--panel-muted)] p-4">
                <h3 className="text-sm font-semibold">Referencia</h3>
                {referenceUrl ? (
                  <img
                    src={referenceUrl}
                    alt="Referencia original"
                    className="mt-3 aspect-square w-full rounded-md object-contain"
                  />
                ) : (
                  <div className="mt-3 flex aspect-square items-center justify-center rounded-md bg-white text-sm text-[#66705f]">
                    No cargada
                  </div>
                )}
              </div>
            </div>
          </section>
        </section>
      </div>
    </main>
  );
}
