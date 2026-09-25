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
  num_colors: number;
  num_zones: number;
  colors_json: string;
  created_at: string;
};

export type ProjectRecord = {
  id: string;
  name: string | null;
  mode: "color" | "bw";
  num_colors: number;
  detail_level: number;
  num_zones: number;
  min_zone_area_px: number;
  zones_geojson: string;
  palette_colors: string;
  created_at: string;
  updated_at: string;
};

function ensureColumn(
  db: Database.Database,
  table: "projects" | "palettes",
  column: string,
  definition: string,
) {
  const columns = db.prepare(`PRAGMA table_info(${table})`).all() as { name: string }[];
  if (!columns.some((item) => item.name === column)) {
    db.exec(`ALTER TABLE ${table} ADD COLUMN ${column} ${definition}`);
  }
}

function migrate(db: Database.Database) {
  ensureColumn(db, "projects", "num_colors", "INTEGER NOT NULL DEFAULT 12");
  ensureColumn(db, "projects", "detail_level", "REAL NOT NULL DEFAULT 0.5");
  ensureColumn(db, "palettes", "num_colors", "INTEGER NOT NULL DEFAULT 12");
}

export function getDb() {
  if (database) {
    return database;
  }

  fs.mkdirSync(dbDir, { recursive: true });
  database = new Database(dbPath);
  database.pragma("journal_mode = WAL");
  database.pragma("foreign_keys = ON");
  database.exec(fs.readFileSync(schemaPath, "utf8"));
  migrate(database);
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
