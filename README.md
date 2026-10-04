# 考古地层关系推断台

基于 **Python Flask + SQLite + 原生 HTML/CSS/JavaScript** 的单容器应用。
考古人员录入发掘区内的地层、遗迹单位以及它们之间的 **早于 / 切割 / 填充**
关系，系统维护一张带关系类型的有向偏序图（Harris 矩阵的时间语义），
自动完成：

- **约束校验**：拒绝跨发掘区引用、自环、重复关系；
- **矛盾检测**：每次提交做有向环检测，并给出经过新边的**最短矛盾链**；
- **层位推断**：**按发掘区分别**计算每个对象在本区全部约束下的
  **最早 / 最晚层位**（ASAP/ALAP），层位相等即位置被完全锁定——
  一区的长链不会抬高或压低另一区对象的层位；
- **可互换对象组**：在**同一发掘区内**找出严格前驱、后继集合完全
  相同的不可比对象（组内对象在本区任何合法年代序列中都能互换；
  分属不同区的孤立对象不会被误并为一组）；
- **双视图举证**：关系矩阵（直接关系 + 间接可达底纹）与可点击的
  分层 SVG 关系图，可高亮任意两对象间的最短推断链；
- **增量重算**：增删边后仅失效/重建**所在区**的闭包与层位缓存，
  受影响对象（本区节点）由 API 返回并在图中金色闪烁高亮，别区推断不动；
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
> 跨区拦截与**多区层位隔离**的 T2 探方（目前仅一个孤立地层 G1），
> 可直接体验环检测与可互换组。

### 多发掘区复核（层位不串扰）

层位窗口与可互换组按**对象所在区的偏序关系**各自计算：一区增删关系
改变本区最长链时，只有本区分支的最晚层位随之浮动，别区推断与缓存
完全不变。启动后可按下列步骤复核：

```bash
# 1) 首次读取：T1 最长链为 5 层，但 T2 的孤立对象 G1 最晚层位必须是 0（本区高度 0）
curl -s localhost:8000/api/state | python3 -m json.tool | grep -A8 '"code": "G1"'
#   期望 G1: "earliest": 0, "latest": 0, "area_height": 0
#   对照 T1 的 L1: "earliest": 5, "latest": 5, "area_height": 5

# 2) 跨区关系仍被拒绝（409 cross_area）
curl -sX POST localhost:8000/api/relations -H 'Content-Type: application/json' \
  -d '{"type":"earlier","subject":9,"object":1}'   # subject=G1(T2), object=L1(T1)

# 3) 在 T2 内建自己的链，观察 T2 层位随本区高度变化、T1 完全不动
curl -sX POST localhost:8000/api/units -H 'Content-Type: application/json' \
  -d '{"area_id":2,"code":"G2","kind":"layer"}'
curl -sX POST localhost:8000/api/units -H 'Content-Type: application/json' \
  -d '{"area_id":2,"code":"G3","kind":"layer"}'
# G1 早于 G2 后：G1=0/0、G2=1/1、孤立的 G3 在 T2 高度 1 内浮动为 0/1；
# 再加 G2 早于 G3：T2 高度升到 2（G3=2/2），而全局 height 仍为 5（T1），
# T1 各对象 earliest/latest/area_height 全程不变。
# groups 数组中每组只含同一 area_id 的成员，G1 不会与任何 T1 对象成组。

# 4) 删除 G2→G3：T2 高度缩回 1（G3 回到 0/1），T1 不受影响；
#    回滚到初始版本后，G1 重新为 0/0、后加对象消失：
curl -sX POST localhost:8000/api/versions/1/restore | python3 -m json.tool
```

页面“层位推断”表的窗口条按每行对象的**本区高度**归一化（T2 不会被
T1 的 5 层长链压成一条），可互换组卡片以 `[T1]` 等区编号开头，可直
观核对分组没有跨区。

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
- **层位推断**：每个对象的层位窗口条形图、最早/最晚层位与浮动量
  （均为本区内数值，窗口条按本区时间跨度归一化）；
  下方列出同区可互换对象组，并提供任意两对象的最短推断链查询。
- **版本 / 日志**：版本快照列表可回滚（回滚本身也生成新版本，不丢数据）；
  事件日志区分“通过 / 拒绝”并记录矛盾链等原因。

## HTTP API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/state` | 全量推断状态（区域/对象/关系/层位/可互换组/受影响对象）。层位与可互换组按区计算；节点含 `area_height`（本区高度，窗口条归一化用），顶层 `height` 为各区高度最大值；每个 group 带 `area_id/area_code` |
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
  祖先/后代闭包记忆化 DFS；加边时以 `Anc(u) × Desc(v)` 精确给出受影响
  节点，删边时在**删边前**图上 BFS 求 `Anc(u)∪Desc(v)` 作为高亮集合；
  结构变更后本区闭包与层位缓存整体失效、下次查询沿新图惰性重建（避免链式
  删边后陈旧闭包被后续加边的并集合并带回），别区缓存完全不动；
  **层位高度按发掘区计算**（区内最长链边数），最晚层位 = 本区高度 −
  本区最长后继链，全局 `height` 仅取各区最大；可互换组键含 `area_id`，
  绝不跨区成组。环检测使用 BFS 求“晚端 → 早端”的最短路径，即最短矛盾链。
  引擎附带 600 组**多区**随机差分测试，验证增删边/节点后增量结果与全量
  重建一致（含链式删边后再加边暴露陈旧闭包的场景）。
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
