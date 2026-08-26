# -*- coding: utf-8 -*-
"""ERA5 NetCDF -> Excel web application. Run with: python app.py"""
from __future__ import annotations

import io
import os
import re
import shutil
import tarfile
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template, request, send_file
from netCDF4 import Dataset, num2date

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024
COORD_NAMES = {
    "time": {"valid_time", "time", "forecast_time"},
    "lat": {"latitude", "lat", "y"},
    "lon": {"longitude", "lon", "x"},
}
SKIP_VARIABLES = {"number", "valid_time", "time", "forecast_time", "latitude", "lat", "longitude", "lon", "expver", "ensemble", "surface", "level"}
ARCHIVE_SUFFIXES = (".zip", ".tar.gz", ".tgz", ".tar")
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024

def _find_name(names, candidates):
    return next((name for name in names if name in candidates), None)

def _safe_scalar(value):
    if np.ma.is_masked(value):
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, (float, np.floating)) and not np.isfinite(value):
        return None
    return value

def _utc_datetimes(time_var):
    decoded = num2date(
        time_var[:], units=getattr(time_var, "units", "seconds since 1970-01-01 00:00:00"),
        calendar=getattr(time_var, "calendar", "standard"), only_use_cftime_datetimes=False,
        only_use_python_datetimes=True,
    )
    return [datetime(x.year, x.month, x.day, x.hour, x.minute, x.second) for x in decoded]

def _read_one(path, source_name):
    ds = Dataset(path, mode="r")
    try:
        names = list(ds.variables.keys())
        time_name = _find_name(names, COORD_NAMES["time"])
        lat_name = _find_name(names, COORD_NAMES["lat"])
        lon_name = _find_name(names, COORD_NAMES["lon"])
        if not (time_name and lat_name and lon_name):
            raise ValueError("找不到时间、纬度或经度坐标（需要 valid_time/latitude/longitude 等变量）")
        times = _utc_datetimes(ds.variables[time_name])
        lats = np.asarray(ds.variables[lat_name][:]).reshape(-1)
        lons = np.asarray(ds.variables[lon_name][:]).reshape(-1)
        nt, nlat, nlon = len(times), len(lats), len(lons)
        data_vars, metadata = [], []
        for name, var in ds.variables.items():
            dims = tuple(var.dimensions)
            if name in SKIP_VARIABLES or name in {time_name, lat_name, lon_name}:
                continue
            if not (time_name in dims and lat_name in dims and lon_name in dims) or len(dims) != 3:
                continue
            data = np.transpose(np.ma.asarray(var[:]), axes=[dims.index(time_name), dims.index(lat_name), dims.index(lon_name)])
            if data.shape != (nt, nlat, nlon):
                continue
            data_vars.append((name, data))
            metadata.append({"变量名": name, "说明": getattr(var, "long_name", name), "单位": getattr(var, "units", ""), "来源文件": source_name})
        if not data_vars:
            raise ValueError("文件中没有找到包含时间/纬度/经度的三维数据变量")
        rows = nt * nlat * nlon
        time_col = np.repeat(np.asarray(times, dtype=object), nlat * nlon)
        frame = pd.DataFrame({
            "北京时间": [x + timedelta(hours=8) for x in time_col], "UTC时间": time_col,
            "纬度": np.tile(np.repeat(lats, nlon), nt), "经度": np.tile(lons, nt * nlat),
        }, index=range(rows))
        for name, data in data_vars:
            frame[name] = [_safe_scalar(v) for v in data.reshape(-1)]
        return frame, metadata
    finally:
        ds.close()

def _is_safe_member(name):
    """Reject archive members that could escape the temporary extraction folder."""
    path = Path(name.replace("\\", "/"))
    return not path.is_absolute() and ".." not in path.parts


def _archive_files(upload, temp_root):
    """Save an upload and return (path, display name) pairs for its NetCDF files."""
    original_name = Path(upload.filename).name
    lower = original_name.lower()
    upload_path = Path(temp_root) / ("upload_" + re.sub(r"[^A-Za-z0-9_.-]", "_", original_name))
    upload.save(str(upload_path))
    if lower.endswith(".nc"):
        return [(str(upload_path), original_name)]

    extracted = []
    extract_root = Path(temp_root) / (upload_path.stem + "_contents")
    extract_root.mkdir(parents=True, exist_ok=True)
    if lower.endswith(".zip"):
        with zipfile.ZipFile(upload_path) as archive:
            members = [m for m in archive.infolist() if not m.is_dir() and m.filename.lower().endswith(".nc")]
            if len(members) > 500:
                raise ValueError(f"{original_name} 中的 .nc 文件超过 500 个")
            if sum(m.file_size for m in members) > MAX_ARCHIVE_BYTES:
                raise ValueError(f"{original_name} 解压后的数据过大")
            for member in members:
                if not _is_safe_member(member.filename):
                    raise ValueError(f"压缩包包含不安全路径：{member.filename}")
                destination = extract_root / Path(member.filename)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, open(destination, "wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                extracted.append((str(destination), f"{original_name} / {member.filename}"))
    elif lower.endswith((".tar.gz", ".tgz", ".tar")):
        with tarfile.open(upload_path, mode="r:*") as archive:
            members = [m for m in archive.getmembers() if m.isfile() and m.name.lower().endswith(".nc")]
            if len(members) > 500:
                raise ValueError(f"{original_name} 中的 .nc 文件超过 500 个")
            if sum(m.size for m in members) > MAX_ARCHIVE_BYTES:
                raise ValueError(f"{original_name} 解压后的数据过大")
            for member in members:
                if not _is_safe_member(member.name):
                    raise ValueError(f"压缩包包含不安全路径：{member.name}")
                source = archive.extractfile(member)
                if source is None:
                    continue
                destination = extract_root / Path(member.name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with source, open(destination, "wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                extracted.append((str(destination), f"{original_name} / {member.name}"))
    else:
        raise ValueError(f"{original_name} 不是 .nc、.zip 或 .tar.gz 压缩包")
    if not extracted:
        raise ValueError(f"{original_name} 中没有找到 .nc 文件")
    return extracted


def _write_workbook(files, batch=False):
    frames, metadata = [], []
    with tempfile.TemporaryDirectory(prefix="era5_upload_") as temp_root:
        for upload in files:
            if not upload or not upload.filename:
                continue
            for path, source_name in _archive_files(upload, temp_root):
                frame, info = _read_one(path, source_name)
                if batch:
                    frame.insert(0, "来源文件", source_name)
                frames.append(frame); metadata.extend(info)
        if not frames:
            raise ValueError("没有收到可转换的 .nc 文件")
        result = pd.concat(frames, ignore_index=True)
        if batch:
            # Different ERA5 files often contain different variables for the
            # same grid/time. Fold those records into one wide row.
            # Build explicit text keys. This avoids pandas treating an equal
            # timestamp as different when source files use different date
            # classes/calendars, and avoids tiny float representation errors.
            result["__merge_time"] = pd.to_datetime(result["北京时间"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
            result["__merge_lat"] = pd.to_numeric(result["纬度"], errors="coerce").round(6).map(lambda x: "" if pd.isna(x) else f"{x:.6f}")
            result["__merge_lon"] = pd.to_numeric(result["经度"], errors="coerce").round(6).map(lambda x: "" if pd.isna(x) else f"{x:.6f}")
            key_columns = ["__merge_time", "__merge_lat", "__merge_lon"]

            def first_value(series):
                values = series.dropna()
                return values.iloc[0] if not values.empty else None

            def source_values(series):
                values = []
                for value in series.dropna().astype(str):
                    for part in value.split("；"):
                        if part and part not in values:
                            values.append(part)
                return "；".join(values)

            aggregations = {
                column: (source_values if column == "来源文件" else first_value)
                for column in result.columns if column not in key_columns
            }
            result = result.groupby(key_columns, as_index=False, dropna=False).agg(aggregations)
            result = result.drop(columns=key_columns)
        result = result.sort_values([c for c in ["北京时间", "纬度", "经度"] if c in result.columns]).reset_index(drop=True)
        first_columns = (["来源文件", "北京时间", "UTC时间", "纬度", "经度"]
                         if batch and "来源文件" in result.columns
                         else ["北京时间", "UTC时间", "纬度", "经度"])
        first_columns = [c for c in first_columns if c in result.columns]
        result = result[first_columns + [c for c in result.columns if c not in first_columns]]
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl", datetime_format="yyyy-mm-dd hh:mm:ss") as writer:
            result.to_excel(writer, sheet_name="数据", index=False)
            pd.DataFrame(metadata).drop_duplicates().to_excel(writer, sheet_name="变量说明", index=False)
        output.seek(0)
        return output

@app.get("/")
def index():
    return render_template("index.html")

@app.post("/api/convert")
def convert_single():
    upload = request.files.get("file")
    if not upload: return jsonify(error="未找到上传文件"), 400
    try:
        workbook = _write_workbook([upload])
        base = re.sub(r"(\.tar\.gz|\.tgz|\.tar|\.zip|\.nc)$", "", Path(upload.filename).name, flags=re.I)
        name = base + ".xlsx"
        return send_file(workbook, as_attachment=True, download_name=name, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as exc:
        return jsonify(error=str(exc)), 422

@app.post("/api/convert/batch")
def convert_batch():
    try:
        workbook = _write_workbook(request.files.getlist("files"), batch=True)
        return send_file(workbook, as_attachment=True, download_name="ERA5_批量转换.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as exc:
        return jsonify(error=str(exc)), 422

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
