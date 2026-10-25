"""Provided-input access for the 60-estimator harness.

Every array an idea may read is obtained through this module, which is where the rule-clean
guarantees live:

* official scored GEOIDs come only from the frozen GEOID-only reader in
  ``scripts/audit_v2_component_masks.py``; score placeholder cells are never opened;
* only the columns an idea declares are read, and every read is recorded in ``accessed``;
* per-tract Overture aggregates come from the workspace feature cache, strata columns from the
  workspace strata cache, and raw geometry only from the public Source Cooperative objects of the
  provided package;
* no removed reference layer (TIGER, Microsoft buildings, USGS facilities, CBP), no label, and no
  leaderboard value can be reached through this module: the path tokens are matched against the
  preregistered forbidden list before any open.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

DELEGATED_READER = ROOT / "scripts/audit_v2_component_masks.py"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    for key in ("vocabulary", "regions", "inputs", "forbidden", "orthogonality", "seeds"):
        if key not in config:
            raise ValueError(f"preregistration config missing key: {key}")
    return config


def official_scored_pairs(workspace: Path,
                          reader: Callable[[Path], list[tuple[str, str]]] | None = None,
                          ) -> list[tuple[str, str]]:
    """Return official (GEOID, region) pairs through the frozen GEOID-only reader."""
    if reader is not None:
        return reader(workspace)
    spec = importlib.util.spec_from_file_location("bias60_delegated_reader", DELEGATED_READER)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load the delegated official-ID reader from {DELEGATED_READER}")
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.official_scored_ids(workspace)


class ProvidedInputs:
    """Region-scoped, column-declared access to the provided datasets."""

    def __init__(self, config: dict[str, Any], workspace: Path, region: str,
                 geoids: list[str], *, filesystem: Any | None = None,
                 s3_region: str = "us-west-2") -> None:
        self.config = config
        self.workspace = Path(workspace)
        self.region = region
        self.geoids = list(geoids)
        self.accessed: dict[str, list[str]] = {"parquet_columns": [], "csv_columns": [],
                                               "layers": [], "caches": []}
        self._filesystem = filesystem
        self._s3_region = s3_region
        self._strata: dict[str, np.ndarray] | None = None
        self._features: dict[str, np.ndarray] | None = None
        self._tracts: Any | None = None
        self._metric_tree: Any | None = None

    # ---------------------------------------------------------------- discovery
    def _require_openable(self, path: str) -> None:
        lowered = path.lower()
        for token in self.config["forbidden"]["path_tokens"]:
            if token.lower() in lowered:
                raise PermissionError(f"forbidden source path token {token!r} in {path!r}")

    @property
    def filesystem(self) -> Any:
        if self._filesystem is None:
            import pyarrow.fs as pafs
            # Anonymous access: the package is public, and anonymous signing guarantees that no
            # local AWS credential is used to reach it.
            self._filesystem = pafs.S3FileSystem(anonymous=True, region=self._s3_region)
        return self._filesystem

    def polygon_layer_path(self, layer: str) -> str:
        """Provided polygon layer (the ACS housing table carries the official tract polygons)."""
        template = self.config["inputs"]["package_polygon_path_template"]
        path = template.format(bucket=self.config["inputs"]["package_bucket"],
                               region=self.region, layer=layer)
        self._require_openable(path)
        self.accessed["layers"].append(layer)
        return path

    def layer_path(self, layer: str) -> str:
        template = self.config["inputs"]["package_layer_path_template"]
        path = template.format(bucket=self.config["inputs"]["package_bucket"],
                               region=self.region, layer=layer)
        self._require_openable(path)
        self.accessed["layers"].append(layer)
        return path

    # ---------------------------------------------------------------- tabular caches
    def _read_parquet_aligned(self, path: Path, columns: list[str],
                              *, kind: str) -> dict[str, np.ndarray]:
        import pyarrow.parquet as pq
        self._require_openable(str(path))
        if not path.is_file():
            raise FileNotFoundError(f"missing provided {kind} cache: {path}")
        available = set(pq.ParquetFile(path).schema_arrow.names)
        missing = [column for column in ["GEOID", *columns] if column not in available]
        if missing:
            raise ValueError(f"{kind} cache {path.name} lacks declared columns: {missing}")
        table = pq.read_table(path, columns=["GEOID", *columns])
        ids = [str(value) for value in table.column("GEOID").to_pylist()]
        if len(set(ids)) != len(ids):
            raise ValueError(f"{kind} cache {path.name} has duplicate GEOIDs")
        position = {geoid: index for index, geoid in enumerate(ids)}
        absent = [geoid for geoid in self.geoids if geoid not in position]
        if absent:
            raise ValueError(f"{kind} cache {path.name} is missing {len(absent)} official GEOIDs "
                             f"(first: {absent[0]})")
        self.accessed["parquet_columns"].extend(f"{path.name}:{column}" for column in ["GEOID", *columns])
        self.accessed["caches"].append(path.name)
        out: dict[str, np.ndarray] = {}
        for column in columns:
            chunk = table[column].take([position[geoid] for geoid in self.geoids]).to_numpy(
                zero_copy_only=False)
            out[column] = chunk
        return out

    def strata(self, columns: list[str]) -> dict[str, np.ndarray]:
        if self._strata is None:
            self._strata = {}
        template = self.config["inputs"]["strata_cache_template"]
        path = self.workspace / template.format(region=self.region)
        if self._strata == {}:
            self._strata = self._read_parquet_aligned(path, columns, kind="strata")
        else:
            for column in columns:
                if column not in self._strata:
                    self._strata.update(self._read_parquet_aligned(path, [column], kind="strata"))
        return {column: self._strata[column] for column in columns}

    def features(self, columns: list[str]) -> dict[str, np.ndarray]:
        if self._features is None:
            self._features = {}
        template = self.config["inputs"]["feature_cache_template"]
        path = self.workspace / template.format(region=self.region)
        if self._features == {}:
            self._features = self._read_parquet_aligned(path, columns, kind="feature")
        else:
            for column in columns:
                if column not in self._features:
                    self._features.update(self._read_parquet_aligned(path, [column], kind="feature"))
        return {column: self._features[column] for column in columns}

    # ---------------------------------------------------------------- geometry
    def tract_geometries(self) -> Any:
        """Official tract polygons in EPSG:4326, in official order, from the provided ACS layer."""
        if self._tracts is not None:
            return self._tracts
        import pyarrow.parquet as pq
        import shapely
        path = self.polygon_layer_path(self.config["inputs"]["polygon_layer"])
        table = pq.ParquetFile(self.filesystem.open_input_file(path)).read(columns=["GEOID", "geometry"])
        ids = [str(value) for value in table.column("GEOID").to_pylist()]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate ACS GEOIDs in {self.region}")
        position = {geoid: index for index, geoid in enumerate(ids)}
        absent = [geoid for geoid in self.geoids if geoid not in position]
        if absent:
            raise ValueError(f"{self.region}: {len(absent)} official GEOIDs absent from the ACS "
                             f"polygon layer (first: {absent[0]})")
        self.accessed["parquet_columns"].append(f"{self.region}-census-acs-housing:GEOID,geometry")
        wkb = table.column("geometry").to_pylist()
        tracts = shapely.from_wkb([wkb[position[geoid]] for geoid in self.geoids])
        if np.any(shapely.is_empty(tracts)) or np.any(~shapely.is_valid(tracts)):
            raise ValueError(f"invalid ACS tract geometry in {self.region}")
        self._tracts = tracts
        return tracts

    def metric_tree(self) -> Any:
        """STRtree over the official tract polygons projected to the EPSG:5070 equal-area CRS."""
        if self._metric_tree is not None:
            return self._metric_tree
        import shapely
        from pyproj import Transformer
        transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
        metric = shapely.transform(self.tract_geometries(), transformer.transform,
                                   interleaved=False)
        self._metric_tree = shapely.STRtree(metric)
        return self._metric_tree

    def named_class_km(self, layer: str) -> tuple[np.ndarray, dict[str, Any]]:
        """Per-tract named-class length in km for one provided road layer, clipped in EPSG:5070.

        Both layers are measured on the same equal-area basis so that the filtered-versus-unfiltered
        contrast cannot be an artefact of two different length conventions. Results are cached in the
        workspace and revalidated against the layer's row count.
        """
        import pyarrow.parquet as pq
        import shapely
        from pyproj import Transformer

        named_classes = set(self.config["seeds"]["Sc5"]["road_named_classes"])
        cache_dir = self.workspace / self.config["inputs"]["ideas_cache_dir"]
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{self.region}-{layer}-named_km.npz"
        path = self.layer_path(layer)
        parquet_file = pq.ParquetFile(self.filesystem.open_input_file(path))
        rows_total = int(parquet_file.metadata.num_rows)
        schema_names = set(parquet_file.schema_arrow.names)
        required = set(self.config["seeds"]["Sc5"]["road_columns_required"])
        if not required.issubset(schema_names):
            raise ValueError(f"{layer} lacks required columns: {sorted(required - schema_names)}")
        if cache_path.is_file():
            stored = np.load(cache_path, allow_pickle=True)
            meta = json.loads(str(stored["metadata_json"]))
            if (meta.get("rows_total") == rows_total and meta.get("region") == self.region
                    and meta.get("layer") == layer and meta.get("road_named_classes") == sorted(named_classes)
                    and len(stored["km"]) == len(self.geoids)):
                return np.asarray(stored["km"], dtype=np.float64), {**meta, "cache_hit": True}

        transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
        metric_tracts = shapely.transform(self.tract_geometries(), transformer.transform,
                                          interleaved=False)
        tree = self.metric_tree()
        km = np.zeros(len(self.geoids), dtype=np.float64)
        rows_named = rows_scanned = 0
        for index in range(parquet_file.metadata.num_row_groups):
            table = parquet_file.read_row_group(index, columns=["class", "geometry"])
            classes = table.column("class").to_pylist()
            rows_scanned += len(classes)
            mask = np.fromiter((value in named_classes for value in classes), dtype=bool,
                               count=len(classes))
            if not mask.any():
                continue
            rows_named += int(mask.sum())
            wkb = table.column("geometry").to_pylist()
            selected = np.flatnonzero(mask)
            if any(wkb[position] is None for position in selected):
                raise ValueError(f"{layer}: null geometry on a named-class segment")
            lines = shapely.from_wkb([wkb[position] for position in selected])
            if np.any(shapely.is_empty(lines)) or np.any(~shapely.is_valid(lines)):
                raise ValueError(f"{layer}: invalid geometry on a named-class segment")
            metric_lines = shapely.transform(lines, transformer.transform, interleaved=False)
            pairs = tree.query(metric_lines, predicate="intersects")
            if not pairs.size:
                continue
            line_idx, tract_idx = pairs
            clipped = shapely.intersection(metric_lines[line_idx], metric_tracts[tract_idx])
            lengths = shapely.length(clipped)
            valid = np.isfinite(lengths) & (lengths > 0.0)
            km += np.bincount(tract_idx[valid], weights=lengths[valid] / 1000.0,
                              minlength=len(self.geoids))
        meta = {"region": self.region, "layer": layer, "rows_total": rows_total,
                "rows_scanned": rows_scanned, "rows_named": rows_named,
                "road_named_classes": sorted(named_classes),
                "crs": "EPSG:5070", "truncated_rows": 0}
        np.savez_compressed(cache_path, km=km,
                            metadata_json=np.array(json.dumps(meta, sort_keys=True)))
        return km, {**meta, "cache_hit": False}


def sample_rows(workspace: Path, template: str, regions: list[str]) -> dict[str, list[str]]:
    """Read the official sample GEOIDs directly (used only to verify the delegated reader)."""
    out: dict[str, list[str]] = {}
    for region in regions:
        path = Path(workspace) / template.format(region=region)
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.reader(stream)
            header = next(reader)
            index = header.index("GEOID")
            out[region] = [str(row[index]) for row in reader if row]
    return out
