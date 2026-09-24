"use client";

import {
  ChangeEvent,
  DragEvent,
  MouseEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AlertTriangle,
  Download,
  ImagePlus,
  Loader2,
  Merge,
  Palette,
  RefreshCw,
  Save,
  Split,
  Undo2,
  Upload,
} from "lucide-react";

type Mode = "color" | "bw";
type PreviewView = "lines" | "color" | "reference";
type EditTool = "merge" | "split";
type PaperSize = "A4" | "Letter";
type PdfOrientation = "portrait" | "landscape";
type ColorMap = Record<string, string>;
type Point = { x: number; y: number };

type ZoneFeature = {
  id?: string;
  properties?: {
    zone_id?: string;
    area_px?: number;
    bbox?: number[];
    requires_manual_color?: boolean;
  };
  geometry: {
    type: "Polygon";
    coordinates: number[][][];
  };
};

type ZonesGeoJson = {
  type: "FeatureCollection";
  properties?: {
    width?: number;
    height?: number;
    mode?: Mode;
  };
  features: ZoneFeature[];
};

type SuggestedPalette = {
  name: string;
  colors: ColorMap;
};

type SegmentResponse = {
  project_id: string;
  zones_svg: string;
  zones_geojson: ZonesGeoJson;
  suggested_palettes: SuggestedPalette[];
};

type SavedPalette = {
  id: string;
  name: string;
  num_zones: number;
  colors: ColorMap;
  created_at: string;
};

type CurrentProject = {
  id: string;
  name: string | null;
  mode: Mode;
  num_zones: number;
  min_zone_area_px: number;
  zones_geojson: ZonesGeoJson;
  palette_colors: ColorMap;
};

type MergeConfirmation = {
  requires_confirmation: true;
  reason: "area_difference_below_threshold";
  candidates: string[];
};

type SplitDraft = {
  zoneId: string;
  points: Point[];
};

type HistoryEntry = {
  result: SegmentResponse;
  activePalette: ColorMap;
  activePaletteName: string;
  previewView: PreviewView;
};

const minZoneAreaOptions = {
  min: 100,
  max: 3000,
  step: 50,
};

const fallbackColors = [
  "#d86f45",
  "#3f8f87",
  "#f2bd4b",
  "#8f6cc9",
  "#4d86c6",
  "#7fb069",
  "#d95d8c",
  "#52615a",
];

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

function zoneId(feature: ZoneFeature, index: number) {
  return feature.properties?.zone_id ?? feature.id ?? String(index + 1);
}

function zoneIds(geojson: ZonesGeoJson) {
  return geojson.features.map((feature, index) => zoneId(feature, index));
}

function completePalette(zoneIdList: string[], colors: ColorMap, fallback: ColorMap = {}) {
  return zoneIdList.reduce<ColorMap>((nextColors, id, index) => {
    nextColors[id] = colors[id] ?? fallback[id] ?? fallbackColors[index % fallbackColors.length];
    return nextColors;
  }, {});
}

function polygonPath(points: number[][]) {
  if (!points.length) {
    return "";
  }

  const [first, ...rest] = points;
  return [`M ${first[0]} ${first[1]}`, ...rest.map((point) => `L ${point[0]} ${point[1]}`), "Z"].join(" ");
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

function geojsonToSvg(geojson: ZonesGeoJson) {
  const width = geojson.properties?.width ?? 1;
  const height = geojson.properties?.height ?? 1;
  const paths = geojson.features
    .map((feature, index) => {
      const id = zoneId(feature, index);
      const ring = feature.geometry.coordinates[0] ?? [];
      return `<path id="zone-${id}" data-zone-id="${id}" d="${polygonPath(
        ring,
      )}" fill="none" stroke="#000000" stroke-width="2" vector-effect="non-scaling-stroke"/>`;
    })
    .join("");

  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}">${paths}</svg>`;
}

function paletteMismatchMessage(paletteZones: number, currentZones: number) {
  if (!currentZones || paletteZones === currentZones) {
    return "";
  }
  if (paletteZones < currentZones) {
    return `La paleta tiene ${paletteZones} colores y esta imagen tiene ${currentZones} zonas. Se aplica igual; las zonas sin color conservan el color actual.`;
  }
  return `La paleta tiene ${paletteZones} colores y esta imagen tiene ${currentZones} zonas. Se aplica igual; los colores sobrantes no se usan.`;
}

function cloneResult(result: SegmentResponse): SegmentResponse {
  return {
    project_id: result.project_id,
    zones_svg: result.zones_svg,
    zones_geojson: JSON.parse(JSON.stringify(result.zones_geojson)) as ZonesGeoJson,
    suggested_palettes: JSON.parse(JSON.stringify(result.suggested_palettes)) as SuggestedPalette[],
  };
}

function manualColorZoneIds(geojson: ZonesGeoJson) {
  return geojson.features
    .map((feature, index) => ({
      id: zoneId(feature, index),
      needsColor: Boolean(feature.properties?.requires_manual_color),
    }))
    .filter((zone) => zone.needsColor)
    .map((zone) => zone.id);
}

function isMergeConfirmation(data: unknown): data is MergeConfirmation {
  return (
    Boolean(data) &&
    typeof data === "object" &&
    (data as MergeConfirmation).requires_confirmation === true &&
    (data as MergeConfirmation).reason === "area_difference_below_threshold" &&
    Array.isArray((data as MergeConfirmation).candidates)
  );
}

function ZonesPreview({
  geojson,
  colors,
  view,
  editTool,
  selectedMergeZone,
  splitDraft,
  disabled,
  onZoneClick,
}: {
  geojson: ZonesGeoJson;
  colors: ColorMap;
  view: "lines" | "color";
  editTool: EditTool;
  selectedMergeZone: string;
  splitDraft: SplitDraft | null;
  disabled: boolean;
  onZoneClick: (zoneId: string, point: Point) => void;
}) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const width = geojson.properties?.width ?? 1;
  const height = geojson.properties?.height ?? 1;
  const fontSize = Math.max(12, Math.min(width, height) / 34);

  function svgPoint(event: MouseEvent<SVGPathElement>): Point {
    const svg = svgRef.current;
    if (!svg) {
      return { x: 0, y: 0 };
    }
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const matrix = svg.getScreenCTM();
    if (!matrix) {
      return { x: 0, y: 0 };
    }
    const transformed = point.matrixTransform(matrix.inverse());
    return {
      x: Math.round(transformed.x),
      y: Math.round(transformed.y),
    };
  }

  return (
    <svg
      ref={svgRef}
      className="h-auto max-h-[70vh] w-full touch-none"
      viewBox={`0 0 ${width} ${height}`}
    >
      {geojson.features.map((feature, index) => {
        const id = zoneId(feature, index);
        const ring = feature.geometry.coordinates[0] ?? [];
        const isSelected = id === selectedMergeZone || id === splitDraft?.zoneId;
        return (
          <path
            key={`zone-${id}`}
            d={polygonPath(ring)}
            fill={view === "color" ? colors[id] ?? "#ffffff" : "transparent"}
            stroke={isSelected ? "#d86f45" : "#111111"}
            strokeWidth={isSelected ? 4 : 2}
            vectorEffect="non-scaling-stroke"
            pointerEvents="all"
            className={disabled ? "cursor-wait" : editTool === "merge" ? "cursor-copy" : "cursor-crosshair"}
            onClick={(event) => {
              if (disabled) {
                return;
              }
              event.stopPropagation();
              onZoneClick(id, svgPoint(event));
            }}
          />
        );
      })}
      {splitDraft?.points[0] ? (
        <circle
          cx={splitDraft.points[0].x}
          cy={splitDraft.points[0].y}
          r={Math.max(4, Math.min(width, height) / 110)}
          fill="#d86f45"
          stroke="#ffffff"
          strokeWidth={2}
          pointerEvents="none"
        />
      ) : null}
      {geojson.features.map((feature, index) => {
        const id = zoneId(feature, index);
        const ring = feature.geometry.coordinates[0] ?? [];
        const centroid = polygonCentroid(ring);
        return (
          <text
            key={`label-${id}`}
            x={centroid.x}
            y={centroid.y}
            textAnchor="middle"
            dominantBaseline="central"
            fontSize={fontSize}
            fontWeight={700}
            fill="#111111"
            paintOrder="stroke"
            stroke="#ffffff"
            strokeWidth={4}
            pointerEvents="none"
          >
            {id}
          </text>
        );
      })}
    </svg>
  );
}

export default function Home() {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const persistTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [imageBase64, setImageBase64] = useState("");
  const [referenceUrl, setReferenceUrl] = useState("");
  const [mode, setMode] = useState<Mode>("color");
  const [numZones, setNumZones] = useState(12);
  const [minZoneAreaPx, setMinZoneAreaPx] = useState(500);
  const [result, setResult] = useState<SegmentResponse | null>(null);
  const [previewView, setPreviewView] = useState<PreviewView>("lines");
  const [activePalette, setActivePalette] = useState<ColorMap>({});
  const [activePaletteName, setActivePaletteName] = useState("Paleta actual");
  const [savedPalettes, setSavedPalettes] = useState<SavedPalette[]>([]);
  const [selectedSavedPalette, setSelectedSavedPalette] = useState("");
  const [paletteName, setPaletteName] = useState("Mi paleta");
  const [paletteWarning, setPaletteWarning] = useState("");
  const [paletteStatus, setPaletteStatus] = useState("");
  const [projectStatus, setProjectStatus] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isSavingPalette, setIsSavingPalette] = useState(false);
  const [error, setError] = useState("");
  const [isDragging, setIsDragging] = useState(false);
  const [editTool, setEditTool] = useState<EditTool>("merge");
  const [selectedMergeZone, setSelectedMergeZone] = useState("");
  const [splitDraft, setSplitDraft] = useState<SplitDraft | null>(null);
  const [isEditingZones, setIsEditingZones] = useState(false);
  const [editStatus, setEditStatus] = useState("");
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [mergeConfirmation, setMergeConfirmation] = useState<MergeConfirmation | null>(null);
  const [paperSize, setPaperSize] = useState<PaperSize>("A4");
  const [pdfOrientation, setPdfOrientation] = useState<PdfOrientation>("portrait");
  const [isExportingPdf, setIsExportingPdf] = useState(false);
  const [exportStatus, setExportStatus] = useState("");

  const zoneCount = result?.zones_geojson.features.length ?? 0;
  const currentZoneIds = useMemo(
    () => (result ? zoneIds(result.zones_geojson) : []),
    [result],
  );

  useEffect(() => {
    async function loadInitialState() {
      try {
        const [palettesResponse, projectResponse] = await Promise.all([
          fetch("/api/palettes"),
          fetch("/api/projects/current"),
        ]);

        if (palettesResponse.ok) {
          const data = (await palettesResponse.json()) as { palettes: SavedPalette[] };
          setSavedPalettes(data.palettes);
        }

        if (projectResponse.ok) {
          const data = (await projectResponse.json()) as { project: CurrentProject | null };
          if (data.project) {
            const restored: SegmentResponse = {
              project_id: data.project.id,
              zones_svg: geojsonToSvg(data.project.zones_geojson),
              zones_geojson: data.project.zones_geojson,
              suggested_palettes: [],
            };
            setResult(restored);
            setMode(data.project.mode);
            setNumZones(data.project.num_zones);
            setMinZoneAreaPx(data.project.min_zone_area_px);
            setActivePalette(data.project.palette_colors);
            setActivePaletteName("Proyecto guardado");
            setPreviewView("color");
            setProjectStatus("Proyecto restaurado desde SQLite.");
          }
        }
      } catch {
        setProjectStatus("No se pudo leer el estado guardado.");
      }
    }

    void loadInitialState();
  }, []);

  useEffect(() => {
    if (!result || !Object.keys(activePalette).length) {
      return;
    }

    if (persistTimer.current) {
      clearTimeout(persistTimer.current);
    }

    persistTimer.current = setTimeout(() => {
      void persistProject(result, activePalette);
    }, 250);

    return () => {
      if (persistTimer.current) {
        clearTimeout(persistTimer.current);
      }
    };
  }, [result, activePalette, mode, numZones, minZoneAreaPx]);

  async function persistProject(nextResult: SegmentResponse, colors: ColorMap) {
    try {
      const response = await fetch("/api/projects/current", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id: nextResult.project_id,
          name: "Proyecto actual",
          mode,
          num_zones: nextResult.zones_geojson.features.length,
          min_zone_area_px: minZoneAreaPx,
          zones_geojson: nextResult.zones_geojson,
          palette_colors: colors,
        }),
      });

      if (!response.ok) {
        throw new Error("No se pudo guardar el proyecto.");
      }
      setProjectStatus("Proyecto guardado.");
    } catch {
      setProjectStatus("No se pudo guardar el proyecto.");
    }
  }

  async function acceptFile(nextFile: File) {
    if (!["image/jpeg", "image/png"].includes(nextFile.type)) {
      setError("Subi una imagen JPG o PNG.");
      return;
    }

    setError("");
    setResult(null);
    setActivePalette({});
    setPaletteWarning("");
    setProjectStatus("");
    setEditStatus("");
    setSelectedMergeZone("");
    setSplitDraft(null);
    setHistory([]);
    setMergeConfirmation(null);
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
    setPaletteStatus("");

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

      const nextResult = data as SegmentResponse;
      const ids = zoneIds(nextResult.zones_geojson);
      const firstPalette = nextResult.suggested_palettes[0];
      const nextPalette = completePalette(ids, firstPalette?.colors ?? {});
      setResult(nextResult);
      setActivePalette(nextPalette);
      setActivePaletteName(firstPalette?.name ?? "Paleta actual");
      setPaletteWarning("");
      setSelectedSavedPalette("");
      setPreviewView("color");
      setPaletteName(`Paleta ${new Date().toLocaleDateString("es-AR")}`);
      setSelectedMergeZone("");
      setSplitDraft(null);
      setHistory([]);
      setMergeConfirmation(null);
      setEditStatus("");
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

  function applySuggestedPalette(palette: SuggestedPalette) {
    if (!result) {
      return;
    }
    setActivePalette(completePalette(currentZoneIds, palette.colors, activePalette));
    setActivePaletteName(palette.name);
    setPaletteWarning("");
    setSelectedSavedPalette("");
    setPreviewView("color");
  }

  function updateColor(id: string, color: string) {
    setActivePalette((current) => ({
      ...current,
      [id]: color,
    }));
    setActivePaletteName("Paleta editada");
    setPreviewView("color");
  }

  async function savePalette() {
    if (!result || !paletteName.trim()) {
      return;
    }

    setIsSavingPalette(true);
    setPaletteStatus("");

    try {
      const response = await fetch("/api/palettes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: paletteName.trim(),
          num_zones: zoneCount,
          colors: activePalette,
        }),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data?.error ?? "No se pudo guardar la paleta.");
      }

      setSavedPalettes((current) => [data.palette as SavedPalette, ...current]);
      setPaletteStatus("Paleta guardada.");
    } catch (saveError) {
      setPaletteStatus(
        saveError instanceof Error ? saveError.message : "No se pudo guardar la paleta.",
      );
    } finally {
      setIsSavingPalette(false);
    }
  }

  function applySavedPalette(paletteId: string) {
    setSelectedSavedPalette(paletteId);
    const palette = savedPalettes.find((item) => item.id === paletteId);
    if (!palette || !result) {
      return;
    }

    setActivePalette(completePalette(currentZoneIds, palette.colors, activePalette));
    setActivePaletteName(palette.name);
    setPaletteWarning(paletteMismatchMessage(palette.num_zones, zoneCount));
    setPreviewView("color");
  }

  function currentSnapshot(): HistoryEntry | null {
    if (!result) {
      return null;
    }
    return {
      result: cloneResult(result),
      activePalette: { ...activePalette },
      activePaletteName,
      previewView,
    };
  }

  function applyEditedResult(nextResult: SegmentResponse, snapshot: HistoryEntry | null) {
    const ids = zoneIds(nextResult.zones_geojson);
    const responsePalette = nextResult.suggested_palettes[0];
    const nextPalette = completePalette(ids, responsePalette?.colors ?? {}, activePalette);
    const manualZones = manualColorZoneIds(nextResult.zones_geojson);

    if (snapshot) {
      setHistory((current) => [...current, snapshot]);
    }
    setResult(nextResult);
    setActivePalette(nextPalette);
    setActivePaletteName(responsePalette?.name ?? "Paleta actualizada");
    setSelectedSavedPalette("");
    setPreviewView("color");
    setNumZones(ids.length);
    setSelectedMergeZone("");
    setSplitDraft(null);
    setMergeConfirmation(null);
    setPaletteWarning(
      manualZones.length
        ? `La zona ${manualZones.join(", ")} quedo con gris neutro y requiere asignacion manual.`
        : "",
    );
    void persistProject(nextResult, nextPalette);
    setEditStatus("Zonas actualizadas.");
  }

  function undoLastEdit() {
    const previous = history.at(-1);
    if (!previous) {
      return;
    }
    setHistory((current) => current.slice(0, -1));
    setResult(cloneResult(previous.result));
    setActivePalette(previous.activePalette);
    setActivePaletteName(previous.activePaletteName);
    setPreviewView(previous.previewView);
    setNumZones(previous.result.zones_geojson.features.length);
    setSelectedMergeZone("");
    setSplitDraft(null);
    setMergeConfirmation(null);
    setPaletteWarning("");
    void persistProject(previous.result, previous.activePalette);
    setEditStatus("Ultima edicion deshecha.");
  }

  async function requestMerge(zoneIdsToMerge: string[], keepZoneId?: string) {
    if (!result) {
      return;
    }

    const snapshot = currentSnapshot();
    setIsEditingZones(true);
    setError("");
    setEditStatus("Fusionando zonas...");

    try {
      const response = await fetch("/api/zones/merge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: result.project_id,
          zone_ids: zoneIdsToMerge,
          palette_colors: activePalette,
          ...(keepZoneId ? { keep_zone_id: keepZoneId } : {}),
        }),
      });
      const data = (await response.json()) as SegmentResponse | MergeConfirmation | { detail?: string; error?: string };

      if (!response.ok) {
        throw new Error(
          "error" in data && data.error
            ? data.error
            : "detail" in data && data.detail
              ? data.detail
              : "No se pudo fusionar las zonas.",
        );
      }

      if (isMergeConfirmation(data)) {
        setMergeConfirmation(data);
        setEditStatus("Hay que elegir que color conservar.");
        return;
      }

      applyEditedResult(data as SegmentResponse, snapshot);
    } catch (mergeError) {
      setEditStatus("");
      setError(mergeError instanceof Error ? mergeError.message : "No se pudo fusionar las zonas.");
    } finally {
      setIsEditingZones(false);
    }
  }

  async function requestSplit(zoneIdToSplit: string, points: Point[]) {
    if (!result) {
      return;
    }

    const snapshot = currentSnapshot();
    setIsEditingZones(true);
    setError("");
    setEditStatus("Dividiendo zona...");

    try {
      const response = await fetch("/api/zones/split", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: result.project_id,
          zone_id: zoneIdToSplit,
          split_line: points.map((point) => [point.x, point.y]),
          palette_colors: activePalette,
        }),
      });
      const data = (await response.json()) as SegmentResponse | { detail?: string; error?: string };

      if (!response.ok) {
        throw new Error(
          "error" in data && data.error
            ? data.error
            : "detail" in data && data.detail
              ? data.detail
              : "No se pudo dividir la zona.",
        );
      }

      applyEditedResult(data as SegmentResponse, snapshot);
    } catch (splitError) {
      setEditStatus("");
      setError(splitError instanceof Error ? splitError.message : "No se pudo dividir la zona.");
    } finally {
      setIsEditingZones(false);
    }
  }

  function handleZoneClick(id: string, point: Point) {
    if (!result || isEditingZones) {
      return;
    }

    if (editTool === "merge") {
      setSplitDraft(null);
      if (!selectedMergeZone) {
        setSelectedMergeZone(id);
        setEditStatus(`Zona ${id} seleccionada.`);
        return;
      }
      if (selectedMergeZone === id) {
        setSelectedMergeZone("");
        setEditStatus("");
        return;
      }
      void requestMerge([selectedMergeZone, id]);
      return;
    }

    setSelectedMergeZone("");
    if (!splitDraft || splitDraft.zoneId !== id) {
      setSplitDraft({ zoneId: id, points: [point] });
      setEditStatus(`Zona ${id} seleccionada.`);
      return;
    }
    void requestSplit(id, [splitDraft.points[0], point]);
  }

  async function downloadPdf() {
    if (!result) {
      setError("Genera un preview antes de descargar el PDF.");
      return;
    }

    setIsExportingPdf(true);
    setExportStatus("");
    setError("");

    try {
      const response = await fetch("/api/export/pdf", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: result.project_id,
          palette: activePalette,
          paper_size: paperSize,
          orientation: pdfOrientation,
        }),
      });

      if (!response.ok) {
        const contentType = response.headers.get("content-type") ?? "";
        if (contentType.includes("application/json")) {
          const data = await response.json();
          throw new Error(data?.error ?? data?.detail ?? "No se pudo generar el PDF.");
        }
        throw new Error((await response.text()) || "No se pudo generar el PDF.");
      }

      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `colorwind-${result.project_id.slice(0, 8)}.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);

      const seconds = response.headers.get("x-generation-seconds");
      setExportStatus(seconds ? `PDF generado en ${seconds}s.` : "PDF generado.");
    } catch (exportError) {
      setExportStatus("");
      setError(exportError instanceof Error ? exportError.message : "No se pudo generar el PDF.");
    } finally {
      setIsExportingPdf(false);
    }
  }

  return (
    <main className="min-h-screen px-5 py-6 text-[var(--foreground)] md:px-8">
      <div className="mx-auto flex w-full max-w-[1500px] flex-col gap-6">
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
            Subi una imagen, ajusta las zonas y edita la paleta con preview en
            vivo.
          </p>
        </header>

        <section className="grid gap-5 xl:grid-cols-[340px_minmax(0,1fr)_330px]">
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
                    ? `${zoneCount} zonas generadas - ${activePaletteName}`
                    : "El resultado segmentado aparece aca."}
                </p>
              </div>
              {result ? (
                <div className="flex flex-wrap items-center justify-end gap-2">
                  <div className="grid grid-cols-2 rounded-md bg-[var(--panel-muted)] p-1">
                    {(["merge", "split"] as EditTool[]).map((tool) => (
                      <button
                        key={tool}
                        type="button"
                        onClick={() => {
                          setEditTool(tool);
                          setSelectedMergeZone("");
                          setSplitDraft(null);
                          setPreviewView((current) => (current === "reference" ? "color" : current));
                          setEditStatus("");
                        }}
                        className={`inline-flex items-center justify-center gap-1 rounded px-3 py-1.5 text-sm font-medium ${
                          editTool === tool
                            ? "bg-white text-[var(--accent-strong)] shadow-sm"
                            : "text-[#596256]"
                        }`}
                      >
                        {tool === "merge" ? (
                          <Merge size={15} aria-hidden="true" />
                        ) : (
                          <Split size={15} aria-hidden="true" />
                        )}
                        {tool === "merge" ? "Fusionar" : "Dividir"}
                      </button>
                    ))}
                  </div>
                  <button
                    type="button"
                    onClick={undoLastEdit}
                    disabled={!history.length || isEditingZones}
                    className="inline-flex h-9 items-center gap-2 rounded-md border border-[var(--line)] bg-white px-3 text-sm font-semibold text-[#4c5548] hover:border-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <Undo2 size={16} aria-hidden="true" />
                    Deshacer
                  </button>
                  <div className="grid grid-cols-3 rounded-md bg-[var(--panel-muted)] p-1">
                    {(["color", "lines", "reference"] as PreviewView[]).map((view) => (
                      <button
                        key={view}
                        type="button"
                        onClick={() => setPreviewView(view)}
                        className={`rounded px-3 py-1.5 text-sm font-medium ${
                          previewView === view
                            ? "bg-white text-[var(--accent-strong)] shadow-sm"
                            : "text-[#596256]"
                        }`}
                      >
                        {view === "color" ? "Color" : view === "lines" ? "Lineas" : "Ref"}
                      </button>
                    ))}
                  </div>
                  <span className="rounded-md bg-[var(--accent-soft)] px-3 py-1 text-sm font-medium text-[var(--accent-strong)]">
                    Proyecto {result.project_id.slice(0, 8)}
                  </span>
                </div>
              ) : null}
            </div>
            {editStatus ? (
              <div className="mb-4 rounded-md bg-[var(--accent-soft)] px-3 py-2 text-sm font-medium text-[var(--accent-strong)]">
                {editStatus}
              </div>
            ) : null}

            <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_240px]">
              <div className="flex min-h-[500px] items-center justify-center overflow-auto rounded-md bg-[#fbfcf9] p-4">
                {isLoading ? (
                  <div className="flex flex-col items-center gap-3 text-[#66705f]">
                    <Loader2 className="animate-spin text-[var(--accent)]" size={30} />
                    <span className="text-sm font-medium">Procesando imagen</span>
                  </div>
                ) : result && previewView !== "reference" ? (
                  <div className="w-full max-w-4xl">
                    <ZonesPreview
                      geojson={result.zones_geojson}
                      colors={activePalette}
                      view={previewView === "color" ? "color" : "lines"}
                      editTool={editTool}
                      selectedMergeZone={selectedMergeZone}
                      splitDraft={splitDraft}
                      disabled={isEditingZones}
                      onZoneClick={handleZoneClick}
                    />
                  </div>
                ) : referenceUrl ? (
                  <img
                    src={referenceUrl}
                    alt="Imagen subida"
                    className="max-h-[70vh] w-auto max-w-full rounded-md object-contain"
                  />
                ) : result ? (
                  <div className="text-center text-sm leading-6 text-[#66705f]">
                    La referencia original no se guarda en SQLite en esta fase.
                  </div>
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
                  <div className="mt-3 flex aspect-square items-center justify-center rounded-md bg-white px-4 text-center text-sm text-[#66705f]">
                    No cargada
                  </div>
                )}
                {projectStatus ? (
                  <p className="mt-3 text-xs leading-5 text-[#66705f]">{projectStatus}</p>
                ) : null}
                <div className="mt-4 border-t border-[var(--line)] pt-4">
                  <h3 className="text-sm font-semibold">PDF</h3>
                  <div className="mt-3 grid grid-cols-2 gap-2">
                    <label className="block">
                      <span className="text-xs font-semibold text-[#596256]">Papel</span>
                      <select
                        value={paperSize}
                        onChange={(event) => setPaperSize(event.target.value as PaperSize)}
                        className="mt-1 h-9 w-full rounded-md border border-[var(--line)] bg-white px-2 text-sm"
                      >
                        <option value="A4">A4</option>
                        <option value="Letter">Carta</option>
                      </select>
                    </label>
                    <label className="block">
                      <span className="text-xs font-semibold text-[#596256]">Orientacion</span>
                      <select
                        value={pdfOrientation}
                        onChange={(event) => setPdfOrientation(event.target.value as PdfOrientation)}
                        className="mt-1 h-9 w-full rounded-md border border-[var(--line)] bg-white px-2 text-sm"
                      >
                        <option value="portrait">Vertical</option>
                        <option value="landscape">Horizontal</option>
                      </select>
                    </label>
                  </div>
                  <button
                    type="button"
                    onClick={() => void downloadPdf()}
                    disabled={!result || isExportingPdf}
                    className="mt-3 inline-flex h-10 w-full items-center justify-center gap-2 rounded-md bg-[var(--accent)] px-3 text-sm font-semibold text-white hover:bg-[var(--accent-strong)] disabled:cursor-not-allowed disabled:bg-[#9eb8b4]"
                  >
                    {isExportingPdf ? (
                      <Loader2 className="animate-spin" size={16} aria-hidden="true" />
                    ) : (
                      <Download size={16} aria-hidden="true" />
                    )}
                    {isExportingPdf ? "Generando PDF" : "Descargar PDF"}
                  </button>
                  {exportStatus ? (
                    <p className="mt-2 text-xs leading-5 text-[#66705f]">{exportStatus}</p>
                  ) : null}
                </div>
              </div>
            </div>
          </section>

          <aside className="rounded-lg border border-[var(--line)] bg-[var(--panel)] p-4 shadow-sm">
            <div className="mb-4 flex items-center gap-2">
              <Palette size={20} className="text-[var(--accent-strong)]" aria-hidden="true" />
              <h2 className="text-lg font-semibold">Paleta</h2>
            </div>

            {!result ? (
              <div className="rounded-md bg-[var(--panel-muted)] p-4 text-sm leading-6 text-[#66705f]">
                Genera un preview para editar colores y guardar paletas custom.
              </div>
            ) : (
              <div className="space-y-5">
                <section>
                  <h3 className="text-sm font-semibold">Sugeridas</h3>
                  <div className="mt-3 grid gap-2">
                    {result.suggested_palettes.length ? (
                      result.suggested_palettes.map((palette) => (
                        <button
                          key={palette.name}
                          type="button"
                          onClick={() => applySuggestedPalette(palette)}
                          className="flex items-center justify-between rounded-md border border-[var(--line)] px-3 py-2 text-sm transition hover:border-[var(--accent)]"
                        >
                          <span>{palette.name}</span>
                          <span className="flex">
                            {Object.values(palette.colors)
                              .slice(0, 5)
                              .map((color, index) => (
                                <span
                                  key={`${palette.name}-${color}-${index}`}
                                  className="-ml-1 size-5 rounded-full border border-white first:ml-0"
                                  style={{ backgroundColor: color }}
                                />
                              ))}
                          </span>
                        </button>
                      ))
                    ) : (
                      <p className="text-sm leading-6 text-[#66705f]">
                        Este proyecto fue restaurado; las sugeridas originales no se
                        persisten en esta fase.
                      </p>
                    )}
                  </div>
                </section>

                <section>
                  <h3 className="text-sm font-semibold">Guardadas</h3>
                  <select
                    value={selectedSavedPalette}
                    onChange={(event) => applySavedPalette(event.target.value)}
                    className="mt-3 h-10 w-full rounded-md border border-[var(--line)] bg-white px-3 text-sm"
                  >
                    <option value="">Elegir paleta guardada</option>
                    {savedPalettes.map((palette) => (
                      <option key={palette.id} value={palette.id}>
                        {palette.name} ({palette.num_zones})
                      </option>
                    ))}
                  </select>
                  {paletteWarning ? (
                    <div className="mt-3 flex gap-2 rounded-md border border-[#e6c45f] bg-[#fff8df] p-3 text-sm leading-5 text-[#6f5410]">
                      <AlertTriangle size={18} className="mt-0.5 shrink-0" aria-hidden="true" />
                      <span>{paletteWarning}</span>
                    </div>
                  ) : null}
                </section>

                <section>
                  <h3 className="text-sm font-semibold">Colores</h3>
                  <div className="mt-3 max-h-[360px] space-y-2 overflow-auto pr-1">
                    {currentZoneIds.map((id) => (
                      <label
                        key={id}
                        className="flex items-center gap-3 rounded-md bg-[var(--panel-muted)] px-3 py-2"
                      >
                        <span className="flex size-7 shrink-0 items-center justify-center rounded bg-white text-xs font-semibold">
                          {id}
                        </span>
                        <input
                          type="color"
                          value={activePalette[id] ?? "#ffffff"}
                          onChange={(event) => updateColor(id, event.target.value)}
                          className="h-8 w-10 cursor-pointer rounded border border-[var(--line)] bg-transparent"
                          aria-label={`Color zona ${id}`}
                        />
                        <span className="text-sm font-medium uppercase text-[#4c5548]">
                          {activePalette[id] ?? "#ffffff"}
                        </span>
                      </label>
                    ))}
                  </div>
                </section>

                <section>
                  <h3 className="text-sm font-semibold">Guardar paleta</h3>
                  <div className="mt-3 flex gap-2">
                    <input
                      type="text"
                      value={paletteName}
                      onChange={(event) => setPaletteName(event.target.value)}
                      className="min-w-0 flex-1 rounded-md border border-[var(--line)] px-3 text-sm"
                      placeholder="Nombre"
                    />
                    <button
                      type="button"
                      onClick={() => void savePalette()}
                      disabled={isSavingPalette || !paletteName.trim()}
                      className="inline-flex h-10 items-center gap-2 rounded-md bg-[var(--accent)] px-3 text-sm font-semibold text-white hover:bg-[var(--accent-strong)] disabled:cursor-not-allowed disabled:bg-[#9eb8b4]"
                    >
                      {isSavingPalette ? (
                        <Loader2 className="animate-spin" size={16} aria-hidden="true" />
                      ) : (
                        <Save size={16} aria-hidden="true" />
                      )}
                      Guardar
                    </button>
                  </div>
                  {paletteStatus ? (
                    <p className="mt-2 text-sm leading-5 text-[#66705f]">{paletteStatus}</p>
                  ) : null}
                </section>
              </div>
            )}
          </aside>
        </section>
      </div>
      {mergeConfirmation ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 px-4">
          <div
            role="dialog"
            aria-modal="true"
            className="w-full max-w-md rounded-lg border border-[var(--line)] bg-[var(--panel)] p-5 shadow-xl"
          >
            <h2 className="text-lg font-semibold">Elegir color a conservar</h2>
            <p className="mt-2 text-sm leading-6 text-[#66705f]">
              Las zonas tienen areas similares. Elegi que color queda en la zona fusionada.
            </p>
            <div className="mt-4 grid gap-2">
              {mergeConfirmation.candidates.map((candidate) => (
                <button
                  key={candidate}
                  type="button"
                  onClick={() => void requestMerge(mergeConfirmation.candidates, candidate)}
                  disabled={isEditingZones}
                  className="flex items-center justify-between rounded-md border border-[var(--line)] px-3 py-2 text-sm font-semibold hover:border-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <span>Zona {candidate}</span>
                  <span className="flex items-center gap-2 uppercase text-[#4c5548]">
                    <span
                      className="size-6 rounded border border-[var(--line)]"
                      style={{ backgroundColor: activePalette[candidate] ?? "#ffffff" }}
                    />
                    {activePalette[candidate] ?? "#ffffff"}
                  </span>
                </button>
              ))}
            </div>
            <button
              type="button"
              onClick={() => {
                setMergeConfirmation(null);
                setEditStatus("");
              }}
              className="mt-4 h-10 w-full rounded-md border border-[var(--line)] bg-white px-3 text-sm font-semibold text-[#4c5548] hover:border-[var(--accent)]"
            >
              Cancelar
            </button>
          </div>
        </div>
      ) : null}
    </main>
  );
}
