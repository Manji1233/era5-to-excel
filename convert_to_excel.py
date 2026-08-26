# -*- coding: utf-8 -*-
"""Convert all NetCDF files in this folder using the same logic as the web app."""
from pathlib import Path

from app import _write_workbook
from werkzeug.datastructures import FileStorage


def main():
    folder = Path(__file__).resolve().parent
    paths = sorted(folder.glob("*.nc"))
    if not paths:
        print("当前文件夹未找到 .nc 文件。")
        return
    handles = []
    try:
        uploads = []
        for path in paths:
            handle = path.open("rb")
            handles.append(handle)
            uploads.append(FileStorage(stream=handle, filename=path.name))
        workbook = _write_workbook(uploads, batch=True)
        output = folder / "ERA5_合并数据.xlsx"
        output.write_bytes(workbook.getvalue())
        print(f"完成：{output}（{len(paths)} 个文件）")
    finally:
        for handle in handles:
            handle.close()


if __name__ == "__main__":
    main()
