import Database from "better-sqlite3";
import fs from "node:fs";
import path from "node:path";

const dbDir = path.join(process.cwd(), "db");
const dbPath = path.join(dbDir, "app.db");
const schemaPath = path.join(dbDir, "schema.sql");

let database: Database.Database | null = null;

export type ColorMap = Record<string, string>;

export type PaletteRecord = {
  id: string;
  name: string;
  num_zones: number;
  colors_json: string;
  created_at: string;
};

export type ProjectRecord = {
  id: string;
  name: string | null;
  mode: "color" | "bw";
  num_zones: number;
  min_zone_area_px: number;
  zones_geojson: string;
  palette_colors: string;
  created_at: string;
  updated_at: string;
};

export function getDb() {
  if (database) {
    return database;
  }

  fs.mkdirSync(dbDir, { recursive: true });
  database = new Database(dbPath);
  database.pragma("journal_mode = WAL");
  database.pragma("foreign_keys = ON");
  database.exec(fs.readFileSync(schemaPath, "utf8"));
  return database;
}

export function nowIso() {
  return new Date().toISOString();
}

export function parseJson<T>(value: string, fallback: T): T {
  try {
    return JSON.parse(value) as T;
  } catch {
    return fallback;
  }
}
