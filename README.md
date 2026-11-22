# ifclibgen — генератор IFC-библиотеки оборудования

Шаблоны изделий (YAML) + таблицы типоразмеров → один IFC4-файл с типами
(IfcAirTerminalType, IfcDamperType и т. д.) с геометрией, портами и Pset.
Файл подключается в Bonsai через Project Library или открывается в любом IFC-софте.

В репозитории два выдуманных семейства-примера (`templates/example`). Библиотеки реальных производителей держатся в отдельных репозиториях с той же структурой `templates/` + `data/` и собираются этим же ядром (`--templates`, `--data`). Готовые .ifc в git не кладутся.

Аддон Blender — `blender/` (стыковка по портам, меш → тип IFC), см. [blender/README.md](blender/README.md). Планируется верстак FreeCAD (`freecad/`: семейства как параметрические модели со Spreadsheet, экспорт в библиотеку, вставка и стыковка).

## Запуск

```
pip install .        # или: pip install ifcopenshell pyyaml numpy matplotlib
python -m ifclibgen build -o out/library.ifc                 # вся библиотека
python -m ifclibgen build -o out/x.ifc --only example/damper  # только одно семейство
python -m ifclibgen build --split -o out/lib/                 # файл на семейство (имя — data-файл или его ключ file:)
python -m ifclibgen one example/damper W=650 H=350 -o out/d.ifc   # один тип нестандартного размера
python -m ifclibgen pack -o out/packages/                     # шаблоны → папки-пакеты
python -m ifclibgen build --split -o out/lib/ --packages out/packages/ --no-templates   # из пакетов
python -m ifclibgen check out/library.ifc                    # схема, геометрия, порты
python -m ifclibgen.preview out/lib/example-dampers.ifc out/p.png "Example damper 600x400"
```

## Как устроено

Источник семейства → **пакет типоразмеров** → .ifc группы.

- Источники: YAML-шаблон + таблица (здесь), позже верстак FreeCAD (прогон Spreadsheet) и аддон Blender (меш → тип).
- Пакет — геометрия уже посчитана: манифест `package.yaml` + сетка на каждый типоразмер (`meshes/*.json` или `.obj`). Формат описан в `ifclibgen/package.py`.
- Запись в IFC одна для всех источников: класс, порты, Pset, точки привязки, стабильный GlobalId (из `source` + имени), хэш содержимого в `LibGen_Parameters.ContentHash` — по нему «Обновить типы» пропускает неизменённые.

## Структура

- `ifclibgen/` — ядро: `package.py` (формат пакета), `expr.py` (формулы), `geometry.py` (примитивы → сетки), `core.py` (шаблоны → пакеты → IFC), `check.py`, `preview.py`.
- `blender/` — аддон Blender, `tests/` — тесты ядра (`python -m pytest tests`).
- `templates/<производитель>/<семейство>.yaml` — как строится изделие.
- `data/<производитель>/*.yaml|csv` — какие типоразмеры генерировать.

## Шаблон

```yaml
manufacturer: Acme
ifc: {class: IfcDamperType, predefined: CONTROLDAMPER}   # USERDEFINED + element_type: ...
name: "Acme damper {E}x{B}"            # {формула} подставляется
constants: {L: 122}                     # константы и таблицы-списки
params:                                 # входные параметры (default — необязательный)
  E: {doc: ширина, мм}
  F: {doc: ширина фланца, мм, default: 30}
derived:                                # вычисляемые, по порядку
  nb: "max(1, round(B / 100))"
styles: {galv: {rgb: [0.72, 0.74, 0.76]}}
default_style: galv
geometry:                               # список примитивов, мм, Z вверх
  - frame: {size: [L, E, B], center: ["L/2", 0, 0], axis: x, wall: 1.5}
  - box: {size: [95, "E - 4", 2], center: ["L/2", 0, "-B/2 + B*(i + 0.5)/nb"]}
    repeat: {var: i, count: nb}         # повтор
    when: "nb > 1"                      # условие
ports:
  - {name: Inlet, shape: rect, w: E, h: B, at: [0, 0, 0], dir: [-1, 0, 0], xdir: [0, 1, 0], flow: SINK}
psets:
  Pset_DamperTypeCommon: {Operation: MANUAL, NumberofBlades: "=nb"}   # "=" — формула, иначе текст
```

Примитивы: `box`, `frame` (прямоугольная труба), `plate` (лист, можно с круглым
отверстием), `cylinder`, `tube`, `revolve` (сечение [[r, h], …] вокруг оси),
`arc_ring` (тонкая оболочка вращения: дуга-лопатка или конус), `extrude_polygon`.

Вся геометрия пишется сеткой (IfcPolygonalFaceSet), генератор режет её сам:
- число сегментов круга = π·D / 25 мм, вверх до кратного 4, от 16 до 64;
  первая вершина на 0°, поэтому детали одного диаметра стыкуются грань в грань;
  длина грани меняется флагом `--chord`;
- от единиц углов проекта ничего не зависит, профилей (IfcProfileDef) в файле нет.

Точки привязки (микротреугольник 0,1 мм) ставятся автоматически в точку
установки (начало координат) и в центр каждого порта; дополнительные — списком
`points: [{name, at}]` в шаблоне. Поле `insertion` — текст про точку установки,
он дописывается в описание типа. Формулы: арифметика,
сравнения, `a if c else b`, списки и индексы, `min max round sqrt sin cos pi`,
`by_range(x, [границы], [значения])`.

## Данные

```yaml
template: acme/diffuser
table: diffuser.csv       # строки из CSV
rows: [{...}]             # или строки прямо здесь
grid: {inlet: [side, top]}   # декартово произведение, комбинируется с каждой строкой
fixed: {series: AD}       # константы
map: {neck_d: E}          # вычисляемые столбцы (переименование под шаблон)
where: "B <= E"           # фильтр
```

GlobalId типа считается из шаблона и имени, поэтому повторная генерация даёт те же
идентификаторы и не плодит дубли.

## Соглашения о начале координат

- Потолочные терминалы: центр на плоскости потолка, лицо ниже Z=0, присоединение вверх.
- Настенные решётки: центр лица на плоскости стены, лицо в XZ, глубина по +Y.
- Линейные элементы (клапаны, глушители): центр входа, ось по +X.
- Пленумы: центр нижнего присоединения, Z вверх.

## Лицензия

GPL-3.0-or-later, см. [LICENSE](LICENSE).
