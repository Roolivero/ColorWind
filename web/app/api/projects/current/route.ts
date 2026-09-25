import { NextRequest, NextResponse } from "next/server";

import { ColorMap, getDb, nowIso, parseJson, ProjectRecord } from "@/lib/db";

export const runtime = "nodejs";

type ProjectPayload = {
  id?: string;
  name?: string;
  mode?: "color" | "bw";
  num_colors?: number;
  detail_level?: number;
  zone_count?: number;
  num_zones?: number;
  min_zone_area_px?: number;
  zones_geojson?: unknown;
  palette_colors?: ColorMap;
};

function serializeProject(row: ProjectRecord) {
  return {
    id: row.id,
    name: row.name,
    mode: row.mode,
    num_colors: row.num_colors,
    detail_level: row.detail_level,
    num_zones: row.num_zones,
    min_zone_area_px: row.min_zone_area_px,
    zones_geojson: parseJson<unknown>(row.zones_geojson, null),
    palette_colors: parseJson<ColorMap>(row.palette_colors, {}),
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

export async function GET() {
  const row = getDb()
    .prepare("SELECT * FROM projects ORDER BY updated_at DESC LIMIT 1")
    .get() as ProjectRecord | undefined;

  return NextResponse.json({ project: row ? serializeProject(row) : null });
}

export async function POST(request: NextRequest) {
  const payload = (await request.json().catch(() => null)) as ProjectPayload | null;
  const numColors = payload?.num_colors;
  const detailLevel = payload?.detail_level;

  if (
    !payload?.id ||
    !payload.mode ||
    typeof numColors !== "number" ||
    !Number.isInteger(numColors) ||
    numColors < 1 ||
    numColors > 30 ||
    typeof detailLevel !== "number" ||
    detailLevel < 0 ||
    detailLevel > 1 ||
    !payload.zones_geojson ||
    !payload.palette_colors
  ) {
    return NextResponse.json({ error: "Datos de proyecto invalidos." }, { status: 400 });
  }

  const db = getDb();
  const existing = db.prepare("SELECT id FROM projects WHERE id = ?").get(payload.id);
  const updatedAt = nowIso();
  const zoneCount = payload.zone_count ?? payload.num_zones ?? 0;
  const minZoneAreaPx = payload.min_zone_area_px ?? 0;

  if (existing) {
    db.prepare(
      `UPDATE projects
       SET name = ?, mode = ?, num_colors = ?, detail_level = ?, num_zones = ?, min_zone_area_px = ?,
           zones_geojson = ?, palette_colors = ?, updated_at = ?
       WHERE id = ?`,
    ).run(
      payload.name ?? "Proyecto actual",
      payload.mode,
      numColors,
      detailLevel,
      zoneCount,
      minZoneAreaPx,
      JSON.stringify(payload.zones_geojson),
      JSON.stringify(payload.palette_colors),
      updatedAt,
      payload.id,
    );
  } else {
    db.prepare(
      `INSERT INTO projects
       (id, name, mode, num_colors, detail_level, num_zones, min_zone_area_px,
        zones_geojson, palette_colors, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).run(
      payload.id,
      payload.name ?? "Proyecto actual",
      payload.mode,
      numColors,
      detailLevel,
      zoneCount,
      minZoneAreaPx,
      JSON.stringify(payload.zones_geojson),
      JSON.stringify(payload.palette_colors),
      updatedAt,
      updatedAt,
    );
  }

  return NextResponse.json({ ok: true, updated_at: updatedAt });
}
