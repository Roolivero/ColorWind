CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  name TEXT,
  mode TEXT NOT NULL,
  num_colors INTEGER NOT NULL DEFAULT 12,
  detail_level REAL NOT NULL DEFAULT 0.5,
  num_zones INTEGER NOT NULL DEFAULT 0,
  min_zone_area_px INTEGER NOT NULL DEFAULT 0,
  zones_geojson TEXT NOT NULL,
  palette_colors TEXT NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS palettes (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  num_colors INTEGER NOT NULL DEFAULT 12,
  num_zones INTEGER NOT NULL DEFAULT 0,
  colors_json TEXT NOT NULL,
  created_at DATETIME NOT NULL
);
