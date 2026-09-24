CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  name TEXT,
  mode TEXT NOT NULL,
  num_zones INTEGER NOT NULL,
  min_zone_area_px INTEGER NOT NULL,
  zones_geojson TEXT NOT NULL,
  palette_colors TEXT NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS palettes (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  num_zones INTEGER NOT NULL,
  colors_json TEXT NOT NULL,
  created_at DATETIME NOT NULL
);
