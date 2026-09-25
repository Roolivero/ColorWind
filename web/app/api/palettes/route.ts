import { NextRequest, NextResponse } from "next/server";
import { randomUUID } from "node:crypto";

import { ColorMap, getDb, nowIso, PaletteRecord, parseJson } from "@/lib/db";

export const runtime = "nodejs";

type PalettePayload = {
  name?: string;
  num_colors?: number;
  num_zones?: number;
  colors?: ColorMap;
};

function serializePalette(row: PaletteRecord) {
  return {
    id: row.id,
    name: row.name,
    num_colors: row.num_colors,
    num_zones: row.num_zones,
    colors: parseJson<ColorMap>(row.colors_json, {}),
    created_at: row.created_at,
  };
}

export async function GET() {
  const rows = getDb()
    .prepare("SELECT * FROM palettes ORDER BY created_at DESC")
    .all() as PaletteRecord[];
  return NextResponse.json({ palettes: rows.map(serializePalette) });
}

export async function POST(request: NextRequest) {
  const payload = (await request.json().catch(() => null)) as PalettePayload | null;
  const name = payload?.name?.trim();
  const numColors = payload?.num_colors ?? payload?.num_zones;
  const colors = payload?.colors;

  if (
    !name ||
    typeof numColors !== "number" ||
    !Number.isInteger(numColors) ||
    numColors < 1 ||
    numColors > 30 ||
    !colors ||
    typeof colors !== "object"
  ) {
    return NextResponse.json({ error: "Datos de paleta invalidos." }, { status: 400 });
  }

  const id = randomUUID();
  const createdAt = nowIso();
  getDb()
    .prepare(
      "INSERT INTO palettes (id, name, num_colors, num_zones, colors_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
    )
    .run(id, name, numColors, numColors, JSON.stringify(colors), createdAt);

  return NextResponse.json({
    palette: {
      id,
      name,
      num_colors: numColors,
      num_zones: numColors,
      colors,
      created_at: createdAt,
    },
  });
}
