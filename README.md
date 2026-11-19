# 中国铁路实况

在地图上按北京时间回放铁路时刻表。点车站可以看下一批到发列车，点车次可以跟随它沿线路行驶，或拖动时间轴观察同一段铁路一天中的车流。位置和速度由时刻表及径路推演。

## Docker 运行

镜像包含 Python 服务、网页和地图渲染库。运行需要一份与代码兼容的独立快照，包含 `data/`、`map/` 和 `space-time/`。已有快照可按下节迁移。当前仓库没有提供整套数据的公共下载地址。仅有 GTFS 压缩包还需补齐底图、站线资料并构建索引。[快照维护说明](docs/snapshot.md)

将快照置于仓库根目录的 `snapshot/`，从仓库根目录执行：

```sh
docker build -t rails-offline .
docker run --rm -p 127.0.0.1:8080:8080 \
  --mount "type=bind,src=$(pwd)/snapshot,dst=/snapshot,readonly" rails-offline
```

打开 <http://127.0.0.1:8080/>。容器以 UID 65534 运行，需要对快照有读取和目录遍历权限。默认地图和空白底图可离线使用，选择卫星模式时会向影像服务请求瓦片。

## 迁移当前快照

在拥有完整工作快照的机器上，等数据构建结束后运行：

```sh
python3 tools/export_snapshot.py --source snapshot dist/current
```

目标须为新目录。将整个 `dist/current/` 复制到目标机并作为 `/snapshot` 挂载。导出包含当前生效版本、数据库、底图和署名说明；原始下载、采集缓存和历史时空片留在工作快照里。换数据版本时，可启动第二个容器在另一端口验收。

## 本地开发

在 Linux 上使用 Python 3.11 或以上版本。运行服务使用 Python 标准库。构建快照中的运动曲线另需 Node.js，浏览器验收另需 Chromium 和 Python 的 `websockets` 包。

```sh
python3 tools/prepare.py
python3 build/app/serve.py
```

默认监听 `127.0.0.1:8080`，可用 `RAILS_BIND`、`RAILS_PORT` 调整。修改源码后重新执行 `prepare.py` 并重启本地服务。`--snapshot /path/to/snapshot` 可指定另一份快照。启动时会检查所需文件以及索引和运动模型的指纹。

| 目录 | 用途 |
| --- | --- |
| `web/` | 页面、列车绘制、时间轴、时空片客户端及第三方渲染库 |
| `backend/` | HTTP API、站线资料、车序与区域查询 |
| `tools/snapshot/` | 时刻表导入、底图和时空片编译、数据审计 |
| `tests/snapshot/` | 快照回归与整日时空片对账 |
| `tests/browser/` | 浏览器交互回归及性能测量 |
| `snapshot/`、`build/`、`dist/` | 本地数据、组装后的运行目录、导出产物，均由 Git 忽略 |

已有工作快照并完成 `prepare.py` 后可运行：

```sh
python3 -m unittest discover -s tests/snapshot -p 'test_*.py'
python3 tests/snapshot/verify_space_tiles.py
```

浏览器测试连接一个专用 Chromium 调试实例，例如以独立用户目录和 `--remote-debugging-port=19851` 启动 Chromium，再执行 `python3 tests/browser/check_train_camera_browser.py 8080`。环境变量 `RAILS_CDP_PORT` 可覆盖调试端口。[历史性能基准及复测方法](docs/space-time.md)

## 许可

原创代码采用 MIT 许可。随附地图库保留其原始许可。时刻表、铁路底图和卫星影像分别列有来源、署名及再分发条件。[MIT 许可](LICENSE) · [第三方软件与数据](THIRD_PARTY.md)
