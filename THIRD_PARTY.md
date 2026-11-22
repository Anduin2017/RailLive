# 第三方软件与数据

| 内容 | 来源与许可 |
| --- | --- |
| 本地渲染库及样式 | `web/vendor/` 随附 Mapbox GL JS，保留 Mapbox 的 BSD 三条款及其依赖的版权说明。本项目对打包库的字形回退处理作过调整。[随附许可](web/vendor/mapbox-gl-LICENSE.txt) |
| 2026-11-25 普速换号 | 依据中国铁路 12306 公布的按日车次、开行日历与逐站时刻，对首批 Z/N/V 班次编排。数据整理于 2026-11-22。停站、跨日到发时刻全部吻合且旧普速轨迹唯一时，沿用基础 GTFS 几何。马磁线补充站点取自 OpenStreetMap contributors（ODbL）。[临水](https://www.openstreetmap.org/node/2329134709) · [新市区](https://www.openstreetmap.org/node/9258877063) · [12306 公告](https://mobile.12306.cn/mormhweb/zxdt/202611/t20261105_46591.html) · [N3 逐站数据](https://kyfw.12306.cn/otn/queryTrainInfo/query?leftTicketDTO.train_no=24000000N300&leftTicketDTO.train_date=2026-11-25&rand_code=) |
| 2026-11-25 目标运行日 | 全部客运类别按 12306 始发日开行日历、当日旅客时刻、车站到发记录和铁路局公告交叉核对。上海金山线新桥站坐标取自 OpenStreetMap contributors（ODbL）。[当日加开公告](https://www.12306.cn/mormhweb/zxdt_news/202611/t20261119_46654.html) · [新桥站](https://www.openstreetmap.org/node/7742242697) · [武汉环线运营资料](https://www.hb-railway.cn/ADMG/indexBanner/202603/t20260319_172166.html) |
| 列车时刻、停站、开行日历及 GTFS 轨迹 | 时刻表来源为 Jeremy Gao（wensimehrp）的 Chinese Railway GTFS，采用 CC BY 4.0。应用从发行数据导入、合并车站、推演轨迹并生成索引和时空片。[原始发行版](https://github.com/wensimehrp/chinese-railway-gtfs/releases/tag/gtfs-20261117-101334) · [随附许可](licenses/GTFS-CC-BY-4.0.txt) |
| 铁路底图、站卡、线路资料、地图样式、精灵和字形 | 本地工作快照部分取自中国铁路地图，发行包配套线路表另记有 jprailfan.com 来源。当前未确认整套资源的公开再分发授权。数据包单独管理，各项来源和署名应随其保留。[中国铁路地图](https://cnrail.geogv.org/zhcn/) |
| 地理背景 | 底图标注 © OpenStreetMap contributors。OpenStreetMap 地理数据使用 ODbL，第三方制图和瓦片服务有各自的使用条件。[版权与署名](https://www.openstreetmap.org/copyright) |
| 可选 OpenRailwayMap 在线图层 | 按当前视野向经典版公共服务请求铁路瓦片。数据 © OpenStreetMap contributors（ODbL），样式 © OpenRailwayMap（CC BY-SA 2.0）。[公共服务使用条款](https://wiki.openstreetmap.org/wiki/OpenRailwayMap/API#Terms_of_use) |
| 可选卫星影像 | 浏览器在卫星模式下向 EOX 请求 Sentinel-2 cloudless 2016。该年份影像使用 CC BY 4.0，含修改后的 Copernicus Sentinel 数据，影像署名显示在地图上。[EOX 产品与许可](https://cloudless.eox.at/documentation/faq) |

仓库根目录的 MIT 许可适用于本项目原创软件。公开发布快照或其衍生数据库前，请按实际包含的来源核实再分发条件。导出工具适用于迁移已有快照；导出文件夹中的来源说明应一并保留。
