# ERA5 转 Excel 网页工具

这是一个 Flask 小工具：批量上传 ERA5 NetCDF（`.nc`）文件或包含 `.nc` 文件的 `.zip`、`.tar.gz`/`.tgz` 压缩包，点击下载时才开始转换；输出 Excel 的第一列固定为“北京时间”（UTC + 8），原始变量和单位保持不变。

## 启动

```powershell
python -m pip install flask netCDF4 pandas openpyxl numpy
python app.py
```

然后打开 http://127.0.0.1:5000。如果要让局域网其他人访问，使用运行电脑的局域网 IP（例如 `http://192.168.1.10:5000`），并在防火墙放行 5000 端口。

Windows 用户也可以直接双击 [启动ERA5工具.bat](启动ERA5工具.bat)，它会自动启动服务并打开网页。不要直接双击 `templates/index.html`；该文件需要 Flask 服务来提供转换接口。

“单文件下载”会生成对应文件名的 `.xlsx`；如果上传的是压缩包，会先读取其中全部 `.nc` 并合并。“全部下载为 Excel”会将队列中的文件合并到一个 `ERA5_批量转换.xlsx`，同一北京时间、同一纬度、同一经度的数据会合并到同一行；数据表列顺序为“来源文件 → 北京时间 → UTC时间 → 经纬度 → 变量”，同时附带“变量说明”表。`convert_to_excel.py` 文件夹脚本也使用同样的合并规则。

## 部署为在线网页

项目包含 `render.yaml`，可连接 GitHub 后在 Render 中一键部署。部署完成后会得到一个公开 HTTPS 地址，用户可以直接上传 `.nc` 或压缩包并下载 Excel。

### 部署到 `manji123.cn/era5-to-excel/`

项目已为同域名子路径准备好部署文件，且前端会使用相对地址请求接口，所以 API 会保持在 `https://manji123.cn/era5-to-excel/api/...`，不会误请求主站根目录。

服务器只需将本项目放到 `/opt/era5-to-excel`，创建虚拟环境并安装 `requirements.txt`，安装 `deploy/era5-to-excel.service` 后执行 `systemctl enable --now era5-to-excel`。将 `deploy/nginx-era5-to-excel.conf` 的内容加入 `manji123.cn` 的 HTTPS Nginx server 块，检查配置并重载 Nginx 即可。部署文件不包含 API Key、密码或环境变量。
