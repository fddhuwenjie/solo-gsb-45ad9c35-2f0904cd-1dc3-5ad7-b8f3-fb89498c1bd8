# 考古地层关系推断台

基于 **Python Flask + SQLite + 原生 HTML/CSS/JavaScript** 的单容器应用。
考古人员录入发掘区内的地层、遗迹单位以及它们之间的 **早于 / 切割 / 填充**
关系，系统维护一张带关系类型的有向偏序图（Harris 矩阵的时间语义），
自动完成：

- **约束校验**：拒绝跨发掘区引用、自环、重复关系；
- **矛盾检测**：每次提交做有向环检测，并给出经过新边的**最短矛盾链**；
- **层位推断**：计算每个对象在全部约束下的**最早 / 最晚层位**（ASAP/ALAP），
  层位相等即位置被完全锁定；
- **可互换对象组**：找出严格前驱、后继集合完全相同的不可比对象
  （它们在任何合法年代序列中都能互换）；
- **双视图举证**：关系矩阵（直接关系 + 间接可达底纹）与可点击的
  分层 SVG 关系图，可高亮任意两对象间的最短推断链；
- **增量重算**：增删边后只合并/失效受影响节点的缓存，
  API 返回受影响对象并在图中金色闪烁高亮；
- **版本管理**：每次成功提交自动保存完整快照，支持一键回滚，
  所有被拒绝的提交也会进入事件日志。

## 一条命令启动

```bash
docker compose up -d --build
```

然后浏览器访问： <http://localhost:8000>

数据保存在命名卷 `strata-data`（容器内 `/data/strata.db`），
停止/重建容器不丢失。停止与清理：

```bash
docker compose down          # 停止（保留数据）
docker compose down -v       # 停止并删除数据卷
```

> 首次启动会自动建库并写入一个演示探方（T1：L4–L1 自然堆积、
> 灰坑 H1 及其填土 F1、两个关系不明的柱洞 H2/H3）和一个用于验证
> 跨区拦截的 T2 探方，可直接体验环检测与可互换组。

## 不使用 Docker 的本地运行

```bash
pip install -r requirements.txt
export DATABASE_PATH=./strata.db      # 可选，默认 /data/strata.db
python app/app.py                      # 监听 0.0.0.0:8000
```

## 关系语义与时间方向

图中每条边都按**考古时间方向（早 → 晚）**参与偏序计算，
但矩阵/图上保留原始关系类型标注：

| 关系 | 含义 | 时间边（早 → 晚） |
|---|---|---|
| `earlier` 早于 | A 的形成早于 B | `A → B` |
| `cuts` 切割 | A（遗迹）打破 B（地层/遗迹） | `B → A` |
| `fills` 填充 | A 是 B 的填土/填充物 | `B → A` |

同一对单位之间可以并存多种关系类型（如“切割”与“早于”），
它们共享同一条时间边，但在界面上分别列出、可分别删除。

## 界面说明

- **关系图**：按最早层位分层（最早在底，符合地层直觉），
  绿/红/蓝箭头分别表示早于/切割/填充。滚轮缩放、拖拽平移；
  点击节点高亮其全部前因后果并在侧栏查看/删除关系；
  提交后边侧栏会列出“本次增量重算”的对象。
- **关系矩阵**：行是较早端、列是较晚端。对角格（自环）与
  跨区格直接禁用并做底纹标记；点击空格可选择要添加的关系类型，
  点击已有关系可删除，点间接格可在图中高亮其推断链。
- **层位推断**：每个对象的层位窗口条形图、最早/最晚层位与浮动量；
  下方列出可互换对象组，并提供任意两对象的最短推断链查询。
- **版本 / 日志**：版本快照列表可回滚（回滚本身也生成新版本，不丢数据）；
  事件日志区分“通过 / 拒绝”并记录矛盾链等原因。

## HTTP API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/state` | 全量推断状态（区域/对象/关系/层位/可互换组/受影响对象） |
| POST | `/api/areas` | 新建发掘区 `{code,name}` |
| POST | `/api/units` | 新建对象 `{area_id,code,kind,name}` |
| DELETE | `/api/units/<id>` | 删除对象及其全部关系 |
| POST | `/api/relations` | 提交关系 `{type,subject,object,note}`；冲突返回 409 |
| DELETE | `/api/relations/<id>` | 删除关系（增量重算） |
| GET | `/api/relations/<id>/basis` | 去掉该边后的最短间接推断链 |
| GET | `/api/versions` / `/api/versions/<id>` | 版本列表 / 快照详情 |
| POST | `/api/versions/<id>/restore` | 回滚 |
| GET | `/api/events` | 操作与拒绝日志 |

409 响应的 `code` 取值：`self_loop`（自环）、`cross_area`（跨发掘区）、
`duplicate`（重复关系）、`cycle`（有向环，附 `cycle` 节点序列与
`chain` 最短矛盾链文字说明）。

示例：

```bash
curl -sX POST localhost:8000/api/relations \
  -H 'Content-Type: application/json' \
  -d '{"type":"cuts","subject":5,"object":3}'
```

## 实现要点

- `app/engine/graph.py`：偏序图引擎。邻接表 + 反向邻接表；
  祖先/后代闭包记忆化 DFS；加边时以 `Anc(u) × Desc(v)` 精确合并闭包，
  删边时只失效可能变短的缓存条目；层位（最长链）在结构变更后惰性重算；
  环检测使用 BFS 求“晚端 → 早端”的最短路径，即为最短矛盾链。
  引擎附带 1000 组随机差分测试验证增量结果与全量重建一致。
- `app/storage.py`：SQLite 建表与 CRUD（`areas / units / relations /
  versions / events`），版本保存完整 JSON 快照。
- `app/app.py`：Flask 路由、校验顺序（存在性 → 自环 → 跨区 → 重复 →
  环）、引擎与数据库的同步、快照回滚。
- `app/static/`：零依赖原生前端，SVG 手绘关系图。

## 目录结构

```
.
├── Dockerfile
├── docker-compose.yaml
├── requirements.txt
├── README.md
└── app
    ├── app.py              # Flask 路由与业务编排
    ├── storage.py          # SQLite 持久层
    ├── engine/
    │   └── graph.py        # 带关系类型的偏序图引擎（环检测/层位/增量）
    └── static/
        ├── index.html
        ├── style.css
        └── app.js          # 矩阵 + SVG 关系图 + 版本/日志
```
