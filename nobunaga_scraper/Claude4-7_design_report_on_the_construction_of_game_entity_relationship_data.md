# 游戏实体关系数据构建 — 技术方案设计报告

## 目录

- [一、项目概述](#一项目概述)
- [二、需求分析与关键决策](#二需求分析与关键决策)
- [三、整体架构设计](#三整体架构设计)
- [四、数据抽取方案](#四数据抽取方案)
- [五、数据存储与更新维护](#五数据存储与更新维护)
- [六、冲突仲裁 LLM Judge 设计](#六冲突仲裁-llm-judge-设计)
- [七、Neo4j 多跳推理实现](#七neo4j-多跳推理实现)
- [八、数据使用方案（按场景）](#八数据使用方案按场景)
- [九、后续待细化方向](#九后续待细化方向)

---

## 一、项目概述

### 1.1 项目目标

以游戏场景的实体关系为核心，通过 LLM Agent 自动化抽取关系型数据并存储，支撑多种下游使用场景。核心实体包括武将（六维属性：勇气、领导力、智力、行政能力、速度、魅力）和战术（名称、分类、详细说明等）。

### 1.2 核心技术挑战

- 从异构纯文本文件（CSV/Markdown/TXT）中自动化抽取结构化的实体、属性和关系数据
- 处理持续增量的新文档，解决跨文档的数据冲突与实体消歧
- 支撑多种混合使用场景：后台查询、RAG 问答、用户聊天分析、复杂关系推理

### 1.3 已确认的需求边界

| 决策点 | 确认结论 |
|--------|---------|
| 数据源格式 | CSV（规整/半结构化/混乱均有）、Markdown、TXT |
| 增量频率 | 持续有新文档进入，频率不确定 |
| 真值标准 | 数据冲突走人工审核 + 来源优先级置信度 |
| 定性→定量推断 | 允许，但需明确边界 |
| 关系类型 | 开放发现 + 纲领约束 |
| 模型策略 | 大模型处理，延迟无强要求 |
| 冲突仲裁粒度 | 三级：低危自动 / 中危观察 / 高危人工 |
| 版本/时点 | 暂不考虑版本管理 |
| 下游场景 | 多场景混合：后台查询、RAG、聊天分析、关系推理、逻辑推理 |

---

## 二、需求分析与关键决策

### 2.1 Schema 先行原则

LLM 抽取的质量上限由 schema 设计决定。领域模型需先固化：

- **实体表**：武将（General）、战术（Tactic）、兵种（UnitType）、地形（Terrain）、势力（Faction）
- **属性表**：每个实体的结构化字段
- **关系表**：武将-战术（习得/精通）、战术-战术（克制/连携）、武将-势力、武将-武将（宿敌/师承）等

所有 schema 用 Pydantic / JSON Schema 表达，让 LLM 用 Structured Output / Tool Use 直接产出符合 schema 的 JSON，杜绝"自由发挥"。

### 2.2 数据库选型结论

采用 **PostgreSQL + pgvector 做权威事实层，Neo4j 做关系推理层** 的混合架构：

- PostgreSQL 是 **唯一写入入口**（Source of Truth）
- Neo4j 通过 CDC 异步同步，作为派生视图
- pgvector 在 PostgreSQL 内提供向量检索能力，免去独立向量库

选型理由：

| 维度 | PostgreSQL + pgvector | Neo4j |
|------|----------------------|-------|
| 角色 | 权威事实存储 | 关系推理加速 |
| 优势 | ACID、JSONB、复杂约束、向量检索 | 多跳查询、路径发现、图算法 |
| 数据量甜点 | 万级到百万级实体 | 万级到十万级节点 |
| 写入模式 | 唯一写入入口 | CDC 同步，只读 |

---

## 三、整体架构设计

### 3.1 三层存储架构

```
┌─────────────────────────────────────┐
│  Neo4j（关系推理层，派生视图）        │  ← 多跳查询、图算法推理
├─────────────────────────────────────┤
│  PostgreSQL + pgvector（权威事实层）  │  ← Source of Truth
├─────────────────────────────────────┤
│  Object Storage（原始文档 + staging）│  ← 可回放
└─────────────────────────────────────┘
```

### 3.2 端到端数据流

```
原始文档 → 文本类型路由 → 预处理与分块 → 实体发现 → 属性抽取 → 关系抽取
        → 实体消歧 → 后置校验 → Staging 表 → 冲突检测
        → Judge 风险分级 → 自动通过 / 观察 / 人工审核队列
        → 主库 Upsert → CDC 同步 Neo4j
```

---

## 四、数据抽取方案

### 4.1 文本类型路由

三种格式加 CSV 三态共五条通道，单一 prompt 不可行，需先分流：

```
输入文件
  ├── .csv → CSV 形态判别（列数、表头规整度、单元格长度分布）
  │     ├── 规整二维表   → 代码直通解析（pandas）+ Schema 映射，不调 LLM
  │     ├── 半结构化     → pandas 解析骨架 + LLM 抽取长文本列
  │     └── 混乱表格     → 整表转 Markdown 后走通用文本通道
  ├── .md  → 按标题层级切块 → 通用文本通道
  └── .txt → 按段落/语义切块 → 通用文本通道
```

CSV 形态判别函数：

```python
def classify_csv(df: pd.DataFrame) -> Literal["clean", "semi", "messy"]:
    header_clean = all(re.match(r'^[\w\u4e00-\u9fff]+$', c) for c in df.columns)
    long_text_ratio = sum(
        df[c].astype(str).str.len().mean() > 50 for c in df.columns
    ) / len(df.columns)
    has_merged_pattern = df.iloc[:, 0].isna().sum() > len(df) * 0.3

    if has_merged_pattern or not header_clean:
        return "messy"
    if long_text_ratio > 0.3:
        return "semi"
    return "clean"
```

核心原则：**规整 CSV 不浪费 LLM 调用**，代码直接解析又快又准。LLM 只处理真正有歧义的非结构化部分。

### 4.2 文档预处理与分块

每个 chunk 必须携带 Provenance 信息：

| 字段 | 说明 |
|------|------|
| `source_id` | 文档唯一 ID：hash(file_path + content_hash) |
| `source_priority` | 来源优先级（官方设定 > wiki > 同人） |
| `chunk_offset` | 在原文中的字符位置 |
| `ingested_at` | 入库时间 |
| `doc_version` | 文档版本 |

分块策略按内容类型分流：

- **结构化文本**（武将词条、战术列表）：按标题/条目边界切，一个实体一个 chunk
- **叙事文本**（小说、剧情）：按语义切（500-1500 token），保留上下文重叠
- **表格转文本**：单独路径，先用代码解析，LLM 仅做兜底

### 4.3 多阶段 LLM 抽取

让 LLM 一次性吐出"实体+属性+关系"的复杂 JSON，错误率随字段数指数级上升。分阶段每步都简单，整体准确率反而更高。

#### Stage 1：实体发现

只识别实体，不抽属性。输出候选实体列表：

```json
{
  "entities": [
    {
      "type": "general",
      "mentions": ["赵云", "子龙"],
      "first_offset": 120
    },
    {
      "type": "tactic",
      "mentions": ["龙胆突刺"],
      "first_offset": 200
    }
  ],
  "candidates": []
}
```

Prompt 关键约束：
- 同一实体不同称呼合并到 mentions
- 模糊群体描述（"诸将""敌方战术"）不输出
- 不确定的名词放入 candidates 而非 entities
- 严禁编造文本中未出现的实体

#### Stage 2：武将属性抽取（核心 Prompt）

**三个关键设计解决"允许推断但需明确边界"的需求：**

**（1）抽取来源类型分类**

每个字段必须标注抽取来源类型：

| source_type | 定义 | 置信度上限 | 数值上限 |
|-------------|------|-----------|---------|
| `explicit` | 原文有明确数值 | 0.95 | 100 |
| `qualitative` | 原文有定性描述需映射 | 0.70 | 100 |
| `behavioral` | 从具体行为/事件推断 | 0.40 | **85** |
| `missing` | 原文未提及 | — | null |

行为推断上限 85 是核心约束——推断永远不能取到最高分，保留高分给硬证据。

**（2）定性映射锚点表**

| 描述强度 | 数值区间 |
|----------|---------|
| 史无前例/绝代 | 95-100 |
| 冠绝当世/无双 | 88-94 |
| 一流/出众 | 78-87 |
| 良好/优秀 | 65-77 |
| 中等/普通 | 45-64 |
| 较弱/平庸 | 25-44 |
| 极弱/低劣 | 0-24 |

**（3）置信度分档（不允许自由打分）**

| 分档值 | 适用场景 |
|--------|---------|
| 0.95 | 原文明确数值，evidence 逐字精确 |
| 0.85 | 原文明确数值但表述略模糊 |
| 0.70 | 定性描述映射，描述明确无歧义 |
| 0.55 | 定性描述映射，描述含糊或可多重解释 |
| 0.40 | 行为推断，证据充分（多个一致行为） |
| 0.25 | 行为推断，证据单一或可能反向解读 |

LLM 在离散档位间选择比连续打分稳定得多，显著降低标定误差。

**完整输出 Schema（以单字段为例）：**

```json
{
  "entity_name": "赵云",
  "fields": {
    "courage": {
      "value": 95,
      "source_type": "explicit",
      "confidence": 0.95,
      "evidence_text": "赵云武力值 95",
      "evidence_offset": [120, 130],
      "reasoning": null
    },
    "leadership": {
      "value": 82,
      "source_type": "qualitative",
      "confidence": 0.70,
      "evidence_text": "统兵有方，号令严明",
      "evidence_offset": [200, 210],
      "reasoning": "描述为'统兵有方'，属一流水平，取区间中值82"
    }
  },
  "extraction_notes": ""
}
```

**Evidence 硬性要求**：
- evidence_text 必须是原文逐字片段，不允许改写、概括、补全
- 长度控制在 10-80 字
- 后置校验会做 `evidence_text in chunk_text` 字符串匹配，匹配失败直接作废该字段

#### Stage 3：战术属性抽取

战术 schema 部分开放，分三层：

- **核心字段（强制结构化）**：canonical_name、category（枚举：突击/控制/治疗/增益/减益/防御/召唤/其他）、target_type、applicable_units、description_summary
- **可选字段（无则 null）**：damage_formula、mp_cost、cooldown_turns、preconditions
- **开放字段（JSONB 兜底）**：extra_attrs，key 用英文 snake_case，最多 8 个 key

#### Stage 4：关系抽取

采用 **纲领约束 + 开放发现** 的混合设计：

**预定义纲领（优先匹配）：**

| 分类 | 关系类型 | 说明 |
|------|---------|------|
| 强关系 | `learns` | 武将能习得某战术 |
| 强关系 | `masters` | 武将精通某战术 |
| 强关系 | `counters` | 战术克制另一战术 |
| 强关系 | `synergizes_with` | 战术配合另一战术 |
| 强关系 | `applicable_to_unit` | 战术适用某兵种 |
| 强关系 | `belongs_to_faction` | 武将属于某势力 |
| 弱关系 | `mentor_of` / `disciple_of` | 师徒 |
| 弱关系 | `rival_of` | 宿敌 |
| 弱关系 | `ally_of` | 同盟 |
| 弱关系 | `subordinate_of` | 上下级 |

**开放发现的二次门槛**：
- 新关系类型名必须用 snake_case 英文
- 必须给出该关系类型的简短定义
- 单 chunk 内出现至少 2 个实例才可创建新类型（单例不创建）
- 标记 `is_novel_type = true`，便于后续人工 review

### 4.4 实体消歧（Entity Resolution）

持续增量场景下最棘手的一步。三层策略：

| 层级 | 方法 | 适用场景 |
|------|------|---------|
| 精确匹配 | name/aliases 完全命中 | 常规情况 |
| 模糊匹配 | name embedding 余弦相似度 > 0.92 + 类型相同 | 别名变体 |
| LLM 仲裁 | 两个实体的所有属性和 evidence 喂给 LLM 判断 | 复杂歧义 |

### 4.5 后置校验层

LLM 输出后必须经过校验才能进 staging：

```python
def validate_extraction(payload: dict, chunk_text: str) -> ValidationResult:
    errors = []

    # 1. Schema 校验（Pydantic）
    parsed = ExtractionResult.model_validate(payload)

    # 2. Evidence 逐字匹配
    for field_name, field_data in parsed.fields.items():
        if field_data.evidence_text:
            if field_data.evidence_text not in chunk_text:
                field_data.value = None  # 该字段作废

    # 3. Source_type 与数值一致性
    # behavioral 类型的 value 不得超过 85
    for field_name, field_data in parsed.fields.items():
        if field_data.source_type == "behavioral" and field_data.value > 85:
            field_data.value = 85

    # 4. 数值范围校验（Pydantic Field(ge=0, le=100) 自动处理）
    # 5. 关系两端实体存在性检查

    return Accept(parsed, warnings=errors)
```

### 4.6 多次采样获取外部置信度信号

对关键字段（六维属性、强关系），同一 chunk 用 temperature=0.3 跑 3 次，对比一致性：

| 一致性 | external_consistency |
|--------|---------------------|
| 3/3 一致 | 1.0 |
| 2/3 一致 | 0.66 |
| 全不同 | 0.0 |

最终入库置信度：`confidence_combined = 0.6 × llm_self_confidence + 0.4 × external_consistency`

### 4.7 增量与回放设计

- **prompt_version + model_version** 写入 staging 表，换模型/改 prompt 后可选择性回放历史 chunk
- **chunk-level 幂等**：同一 chunk + 同一 prompt_version + 同一 model_version 重跑结果一致（temperature=0）
- **任务队列化**：新文档进来 → 入队 → 异步抽取 → 人工审核 → 入库

---

## 五、数据存储与更新维护

### 5.1 PostgreSQL 表设计

#### 实体表

```sql
CREATE TABLE generals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name  TEXT NOT NULL UNIQUE,
    aliases         TEXT[] NOT NULL DEFAULT '{}',

    -- 六维属性
    courage         SMALLINT CHECK (courage BETWEEN 0 AND 100),
    leadership      SMALLINT CHECK (leadership BETWEEN 0 AND 100),
    intelligence    SMALLINT CHECK (intelligence BETWEEN 0 AND 100),
    administration  SMALLINT CHECK (administration BETWEEN 0 AND 100),
    speed           SMALLINT CHECK (speed BETWEEN 0 AND 100),
    charisma        SMALLINT CHECK (charisma BETWEEN 0 AND 100),

    -- 半结构化
    description     TEXT,
    description_embedding VECTOR(1024),
    extra_attrs     JSONB DEFAULT '{}',

    -- 状态
    status          TEXT NOT NULL DEFAULT 'active',  -- active/draft/conflict
    confidence_avg  REAL,
    version         INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX ON generals USING gin (aliases);
CREATE INDEX ON generals USING ivfflat (description_embedding vector_cosine_ops);
CREATE INDEX ON generals USING gin (extra_attrs jsonb_path_ops);
```

```sql
CREATE TABLE tactics (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name  TEXT NOT NULL UNIQUE,
    category        TEXT,
    applicable_units TEXT[],
    cost            JSONB,
    formula         TEXT,
    description     TEXT,
    description_embedding VECTOR(1024),
    extra_attrs     JSONB DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'active',
    version         INTEGER NOT NULL DEFAULT 1
);
```

#### 统一关系表

```sql
CREATE TABLE entity_relations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type     TEXT NOT NULL,  -- 'general' | 'tactic' | ...
    source_id       UUID NOT NULL,
    target_type     TEXT NOT NULL,
    target_id       UUID NOT NULL,
    relation_type   TEXT NOT NULL,  -- 'learns' | 'counters' | ...
    properties      JSONB DEFAULT '{}',
    confidence      REAL NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    UNIQUE (source_type, source_id, target_type, target_id, relation_type)
);

CREATE INDEX ON entity_relations (source_type, source_id, relation_type);
CREATE INDEX ON entity_relations (target_type, target_id, relation_type);
```

为什么统一而非每种关系独立建表：关系类型会持续增加（含开放发现的新类型），统一表 + relation_type 字段更易扩展，也方便整体导入 Neo4j。

#### Staging 与 Provenance 表

```sql
CREATE TABLE extraction_staging (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id           TEXT NOT NULL,
    chunk_offset        INT4RANGE,
    raw_text            TEXT,
    extracted_payload   JSONB NOT NULL,
    target_entity_type  TEXT,
    target_entity_id    UUID,
    prompt_version      TEXT NOT NULL,
    model_version       TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    -- pending / merged / conflict / rejected / human_review
    confidence          REAL,
    source_priority     SMALLINT NOT NULL
);
```

#### 字段级证据表

```sql
CREATE TABLE field_evidence (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type         TEXT NOT NULL,
    entity_id           UUID NOT NULL,
    field_name          TEXT NOT NULL,
    field_value         JSONB NOT NULL,
    source_id           TEXT NOT NULL,
    source_priority     SMALLINT NOT NULL,
    confidence          REAL NOT NULL,
    evidence_text       TEXT,
    is_active           BOOLEAN NOT NULL DEFAULT true,
    extracted_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON field_evidence (entity_type, entity_id, field_name, is_active);
```

每个属性字段的每次抽取都留痕。主表的值实际上是从 field_evidence 中按规则选出来的"当前最佳值"。

### 5.2 来源优先级配置

```sql
CREATE TABLE source_priorities (
    source_pattern  TEXT PRIMARY KEY,
    priority        SMALLINT NOT NULL,
    description     TEXT
);

-- 示例
INSERT INTO source_priorities VALUES
  ('official/data_table/%',     100, '官方数值表'),
  ('official/manual/%',          90, '官方手册'),
  ('wiki/%',                     50, '社区 Wiki'),
  ('fanfic/%',                   10, '同人作品');
```

### 5.3 字段值采纳规则

对每个 `(entity_id, field_name)`，按以下优先级选 `is_active = true` 的那条：

1. 人工锁定（source_priority = 1000）
2. 最高 source_priority 中 confidence 最高的
3. 多条同分时取最新 extracted_at

### 5.4 增量更新流程

```
新文档到达
  → hash 去重（同一文档不重复处理）
  → 切块入 staging
  → 异步 LLM 抽取
  → 实体消歧（找已存在的或建新的）
  → 字段级写入 field_evidence
  → 触发"采纳规则"重算主表字段
  → 检测冲突 → 自动通过 / 进审核队列
  → 主表变更 → CDC → 同步 Neo4j
```

---

## 六、冲突仲裁 LLM Judge 设计

### 6.1 整体工作流

```
新数据写入 field_evidence
  ↓
触发字段级冲突检测（规则层，不调 LLM）
  ↓
有冲突？
  ├── 否 → 按 source_priority + confidence 直接采纳
  └── 是 ↓
        Judge Stage 1：风险分级（LLM 调用）
          ├── LOW    → 按规则自动选值，记录冲突日志
          ├── MEDIUM → 按规则自动选值，标 status='observing'，
          │           累计 N 次同字段冲突后升级
          └── HIGH   → 进人工审核队列，主表字段保持原值
```

### 6.2 规则层冲突检测（不调 LLM）

```python
def detect_conflict(field_name: str, candidates: list[FieldEvidence]) -> ConflictType:
    # 数值字段
    if field_name in NUMERIC_FIELDS:
        values = [c.value for c in candidates]
        if max(values) - min(values) <= 2:
            return ConflictType.NONE       # 容差内
        if same_tier(values) and stdev(values) < 5:
            return ConflictType.SOFT       # 弱冲突
        return ConflictType.HARD

    # 数组字段（如 applicable_units）
    if field_name in ARRAY_FIELDS:
        sets = [set(c.value) for c in candidates]
        if all(s.issubset(union(*sets)) for s in sets):
            return ConflictType.MERGEABLE  # 可合并
        return ConflictType.HARD

    # 文本字段
    if field_name in TEXT_FIELDS:
        if max_pairwise_similarity(candidates) > 0.85:
            return ConflictType.NONE       # 表述差异
        return ConflictType.SEMANTIC       # 需 LLM 判

    # 枚举字段
    if len(set(c.value for c in candidates)) == 1:
        return ConflictType.NONE
    return ConflictType.HARD
```

MERGEABLE 直接合并并集；NONE 按规则选值；剩下的 SOFT/HARD/SEMANTIC 交给 Judge。

### 6.3 三级风险分级定义

| 等级 | 行为 | 典型场景 |
|------|------|---------|
| **LOW** | 自动按规则选值，记录日志 | 数值差异 ≤ 5%，或高优先级来源 confidence 远高于低优先级 |
| **MEDIUM** | 自动按规则选值，标 `status='observing'`，同字段累计 3 次后升级为 HIGH | 数值差异 5-15%，多个中等优先级来源不一致 |
| **HIGH** | 进人工审核队列，主表字段保持原值或 null | 数值差异 > 15%，explicit 与 explicit 直接冲突，或语义层面相互否定 |

### 6.4 Judge Prompt 核心设计

Judge 综合考虑四个评估维度：

1. **数据偏差幅度**：候选值之间的实际差异占值域比例
2. **来源权威性差距**：高优先级来源是否明显胜出
3. **抽取来源类型组合风险**：explicit vs qualitative vs behavioral 的组合
4. **下游用途敏感度**：影响数值平衡/RAG 直接回答 → 提高一档

**关键约束规则**：

- 当 `judge_self_confidence < 0.70` 时，即使倾向 LOW/MEDIUM，也必须强制升级为 HIGH
- 当存在"低优先级 + explicit 铁证" vs "高优先级 + qualitative 推断"的悖论情形时，必须 HIGH
- 候选值的 evidence_text 之间存在直接事实矛盾时，必须 HIGH

**Judge 输出 Schema**：

```json
{
  "risk_level": "LOW | MEDIUM | HIGH",
  "risk_reasoning": "评估理由（不超过 150 字）",
  "key_factors": ["关键因素1", "关键因素2"],
  "suggested_choice": "C1 | C2 | null",
  "suggested_choice_reason": "推荐原因 | null",
  "human_review_focus": "人工审核应重点看什么 | null",
  "judge_self_confidence": "0.95 | 0.85 | 0.70 | 0.55"
}
```

### 6.5 字段重要性配置

```sql
CREATE TABLE field_criticality (
    entity_type     TEXT NOT NULL,
    field_name      TEXT NOT NULL,
    criticality     SMALLINT NOT NULL,  -- 1-5，5最关键
    downstream_uses TEXT[] NOT NULL,
    description     TEXT,
    PRIMARY KEY (entity_type, field_name)
);
```

| entity_type | field_name | criticality | downstream_uses |
|-------------|-----------|-------------|-----------------|
| general | courage | 5 | balance, rag, display |
| general | description | 3 | rag, display |
| tactic | damage_formula | 5 | balance |
| tactic | description | 3 | rag, display |
| general | extra_attrs | 2 | display |

### 6.6 中危观察与自动升级

```sql
CREATE TABLE conflict_observations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type     TEXT NOT NULL,
    entity_id       UUID NOT NULL,
    field_name      TEXT NOT NULL,
    judge_verdict   JSONB NOT NULL,
    occurred_at     TIMESTAMPTZ DEFAULT now(),
    auto_resolved   BOOLEAN DEFAULT true
);
```

触发器逻辑：同字段 30 天内累计 3 次 MEDIUM → 自动升级为 HIGH 进人工队列。反复在同一字段冲突说明该实体或来源有系统性问题。

### 6.7 人工审核队列

```sql
CREATE TABLE human_review_queue (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type     TEXT NOT NULL,
    entity_id       UUID NOT NULL,
    field_name      TEXT NOT NULL,
    candidates      JSONB NOT NULL,
    judge_output    JSONB NOT NULL,
    reason          TEXT NOT NULL,
    priority        SMALLINT NOT NULL,
    status          TEXT DEFAULT 'pending',
    resolved_by     TEXT,
    resolved_at     TIMESTAMPTZ,
    final_value     JSONB,
    locked          BOOLEAN DEFAULT false,
    review_notes    TEXT
);
```

**审核界面呈现内容**：

1. 字段名 + 实体名 + 字段定义 + 下游用途
2. 所有候选值（按 Judge 的 suggested_choice 排序），含 source_priority/confidence/evidence
3. Judge 的 risk_reasoning 和 human_review_focus
4. 操作按钮：采纳某候选 / 自定义值 / 锁定不再变 / 来源降权

### 6.8 审核回流机制

人工审核结果不能只用一次就丢——它是改善系统最宝贵的信号：

1. 更新 field_evidence 和主表字段
2. 若 locked=true，写入字段锁，未来抽取不再自动覆盖
3. 记录到训练数据集（用于后续 prompt 优化 few-shot）
4. Judge 推荐与人工决策不一致时，触发来源权重复审信号

### 6.9 Judge 可观测性

监控指标：

```sql
CREATE VIEW judge_quality_metrics AS
SELECT
    DATE_TRUNC('day', occurred_at) AS day,
    risk_level,
    COUNT(*) AS total,
    AVG(CASE
        WHEN risk_level = 'HIGH'
         AND human_decision_matches_judge_suggestion THEN 1.0
        ELSE 0.0 END) AS judge_human_agreement_rate,
    AVG(judge_self_confidence) AS avg_self_confidence
FROM conflict_observations co
LEFT JOIN human_review_queue hrq ON ...
GROUP BY day, risk_level;
```

Judge 与人工分歧率突然升高 → 报警，可能 prompt 漂移、模型版本变化、或新来源不适配。

---

## 七、Neo4j 多跳推理实现

### 7.1 设计原则

| 原则 | 说明 |
|------|------|
| 跳数上限 5 | 超过的查询走 GDS 图算法 |
| 引入 confidence 权重 | 关系的 weight 字段参与剪枝和排序 |
| 强制返回推理路径 | 任何查询结果都附带 evidence_paths |
| Neo4j 主导 + 关键属性冗余 | 高频过滤属性冗余到节点，描述文本留 Postgres |

### 7.2 图模型设计

#### 节点

```cypher
(:General {
  id: 'uuid-xxx',
  canonical_name: '赵云',
  courage: 95, leadership: 88, intelligence: 76,
  administration: 65, speed: 92, charisma: 80,
  status: 'active',
  confidence_avg: 0.91
  // 不冗余：description, description_embedding, extra_attrs
})

(:Tactic {
  id: 'uuid-yyy',
  canonical_name: '龙胆突刺',
  category: '突击',
  target_type: 'single_enemy',
  mp_cost: 30,
  cooldown_turns: 3
})

(:Faction { id: 'uuid-zzz', name: '蜀' })
(:UnitType { id: 'uuid-www', name: '骑兵', category: '机动' })
```

冗余原则：只冗余查询中用作过滤或排序的属性。`confidence_avg` 是实用的剪枝信号，可直接过滤低质量节点。

#### 关系（带权重和元数据）

```cypher
(:General)-[:LEARNS    { mastery, confidence, source_count, weight }]->(:Tactic)
(:General)-[:MASTERS   { mastery, confidence, source_count, weight }]->(:Tactic)
(:Tactic)-[:COUNTERS   { strength, confidence, conditions, weight }]->(:Tactic)
(:Tactic)-[:SYNERGIZES_WITH { synergy_score, confidence, weight }]->(:Tactic)
(:Tactic)-[:APPLICABLE_TO]->(:UnitType)
(:General)-[:BELONGS_TO]->(:Faction)
(:General)-[:RIVAL_OF  { intensity, confidence, weight }]->(:General)
(:General)-[:MENTOR_OF { confidence, weight }]->(:General)
```

**weight 综合权重公式**（由 Postgres 端在 CDC 同步时计算）：

| 关系类型 | weight 计算 |
|---------|------------|
| MASTERS | mastery × confidence × 1.0 |
| LEARNS | mastery × confidence × 0.7 |
| COUNTERS | strength × confidence × 0.9 |
| SYNERGIZES_WITH | synergy_score × confidence × 0.8 |
| BELONGS_TO | 1.0 × confidence × 1.0 |
| RIVAL_OF | intensity × confidence × 0.5 |
| MENTOR_OF | 1.0 × confidence × 0.6 |

#### 索引

```cypher
-- 主键约束
CREATE CONSTRAINT general_id FOR (n:General) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT tactic_id  FOR (n:Tactic)  REQUIRE n.id IS UNIQUE;

-- 名称查询（入口节点定位）
CREATE INDEX general_name FOR (n:General) ON (n.canonical_name);
CREATE INDEX tactic_name  FOR (n:Tactic)  ON (n.canonical_name);

-- 数值范围查询
CREATE INDEX general_courage      FOR (n:General) ON (n.courage);
CREATE INDEX general_intelligence FOR (n:General) ON (n.intelligence);
CREATE INDEX general_confidence   FOR (n:General) ON (n.confidence_avg);
```

### 7.3 Postgres → Neo4j 同步方案

| 方案 | 适用阶段 | 实时性 | 运维复杂度 |
|------|---------|--------|-----------|
| CDC + 消息队列（Debezium → Kafka → Neo4j sink） | 生产环境 | 秒级 | 中 |
| 定时 ETL（按 updated_at 增量同步） | 起步阶段 | 分钟级 | 低 |

无论哪种，写入 Neo4j 用 MERGE 而非 CREATE，保证幂等。

### 7.4 核心查询模式

#### 模式一：定向多跳（已知起点和终点类型）

场景：找出能克制赵云所学战术的武将。

```cypher
MATCH (g1:General {canonical_name: $start_name})
MATCH path = (g1)-[r1:LEARNS|MASTERS]->(t1:Tactic)
              <-[r2:COUNTERS]-(t2:Tactic)
              <-[r3:LEARNS|MASTERS]-(g2:General)
WHERE g1 <> g2
  AND r1.weight > $min_weight
  AND r2.weight > $min_weight
  AND r3.weight > $min_weight
  AND g2.confidence_avg > 0.7
WITH g2,
     reduce(w = 1.0, r in relationships(path) | w * r.weight) AS path_weight,
     collect({
       intermediate_tactic: t1.canonical_name,
       counter_tactic: t2.canonical_name,
       weights: [r1.weight, r2.weight, r3.weight]
     }) AS evidence_paths
ORDER BY path_weight DESC
RETURN g2.canonical_name AS general,
       g2.courage, g2.leadership,
       max(path_weight) AS best_path_weight,
       size(evidence_paths) AS path_count,
       evidence_paths[0..3] AS top_evidence
LIMIT $top_k;
```

关键技巧：
- `reduce` 计算路径权重乘积（短板效应）
- 聚合时返回 evidence_paths（可解释）
- WHERE 条件做早期剪枝

#### 模式二：变长路径（关系链未知）

场景：关羽和孙权之间通过什么关系链相连。

```cypher
MATCH (a:General {canonical_name: '关羽'}),
      (b:General {canonical_name: '孙权'})
MATCH path = allShortestPaths((a)-[*..5]-(b))
WHERE all(r IN relationships(path) WHERE r.weight > 0.4)
RETURN path,
       [n in nodes(path) | {name: n.canonical_name, type: labels(n)[0]}] AS node_chain,
       [r in relationships(path) | type(r)] AS rel_chain
LIMIT 10;
```

性能对策：
- 优先用 `shortestPath()` / `allShortestPaths()` 而非自由 `*..N`
- 大数据量用 GDS 加权最短路径算法（Dijkstra）

#### 模式三：组合推荐（子图模式匹配）

场景：给定阵容 [赵云, 张飞, 关羽]，推荐补充武将。

```cypher
WITH ['赵云', '张飞', '关羽'] AS team_names

// Step 1: 阵容当前战术
MATCH (g:General)-[:LEARNS|MASTERS]->(t:Tactic)
WHERE g.canonical_name IN team_names
WITH team_names, collect(DISTINCT t) AS team_tactics

// Step 2: 协同战术
UNWIND team_tactics AS t1
MATCH (t1)-[s:SYNERGIZES_WITH]-(t2:Tactic)
WHERE NOT t2 IN team_tactics AND s.weight > 0.5
WITH team_names, t2, sum(s.weight) AS synergy_score
ORDER BY synergy_score DESC LIMIT 20

// Step 3: 候选武将
MATCH (g_candidate:General)-[r:LEARNS|MASTERS]->(t2)
WHERE NOT g_candidate.canonical_name IN team_names AND r.weight > 0.5
WITH g_candidate,
     sum(synergy_score * r.weight) AS recommendation_score,
     collect(DISTINCT t2.canonical_name) AS bridging_tactics
WHERE size(bridging_tactics) >= 2

// Step 4: 排除宿敌
OPTIONAL MATCH (g_candidate)-[rivalry:RIVAL_OF]-(team_member:General)
WHERE team_member.canonical_name IN ['赵云', '张飞', '关羽']
  AND rivalry.weight > 0.7
WITH g_candidate, recommendation_score, bridging_tactics,
     count(rivalry) AS rival_count
WHERE rival_count = 0

RETURN g_candidate.canonical_name AS recommended_general,
       recommendation_score, bridging_tactics
ORDER BY recommendation_score DESC LIMIT 10;
```

#### 模式四：图算法推理（GDS）

| 算法 | 用途 | 场景 |
|------|------|------|
| PageRank | 识别关系网络中心人物 | 关键角色分析 |
| Louvain 社区发现 | 自动识别派系/集团 | 阵营平衡分析 |
| 节点相似度 | 基于共同关系找相似实体 | 推荐系统 |

**PageRank 示例**：

```cypher
CALL gds.graph.project('general_network',
  ['General', 'Faction'],
  { BELONGS_TO: {properties: 'weight'}, RIVAL_OF: {properties: 'weight'} });

CALL gds.pageRank.stream('general_network', {
  relationshipWeightProperty: 'weight'
})
YIELD nodeId, score
WITH gds.util.asNode(nodeId) AS n, score
WHERE 'General' IN labels(n)
RETURN n.canonical_name, score ORDER BY score DESC LIMIT 20;
```

#### 模式五：路径解释（推理可解释性）

```cypher
MATCH (a:General {id: $start_id}),
      (b:General {id: $end_id})
MATCH path = shortestPath((a)-[*..5]-(b))
WITH path, relationships(path) AS rels, nodes(path) AS ns
RETURN [i in range(0, size(rels)-1) | {
  from: ns[i].canonical_name,
  from_type: labels(ns[i])[0],
  relation: type(rels[i]),
  to: ns[i+1].canonical_name,
  to_type: labels(ns[i+1])[0],
  weight: rels[i].weight,
  confidence: rels[i].confidence
}] AS reasoning_chain;
```

返回结构化推理链，可直接喂给 LLM 生成自然语言解释。

### 7.5 Agent 工具封装

将查询模式封装为 5 个结构化工具，不允许 Agent 直接写 Cypher：

| 工具 | 对应模式 | 路由到 |
|------|---------|-------|
| `find_counters_to_general` | 定向多跳 | Neo4j |
| `find_relationship_path` | 变长路径 | Neo4j |
| `recommend_team_members` | 组合推荐 | Neo4j |
| `find_similar_generals` | 图算法 / 向量 | Neo4j 或 Postgres |
| `explain_inference_path` | 路径解释 | Neo4j |

**工具路由指南**：

| 查询类型 | 走 Postgres + pgvector | 走 Neo4j |
|---------|----------------------|----------|
| 精确属性过滤 | ✅ | |
| 单实体详情 | ✅ | |
| 描述性语义检索 | ✅ | |
| 简单一跳关系 | ✅ | |
| 多跳关系推理 | | ✅ |
| 路径发现 | | ✅ |
| 复合推荐 | | ✅ |
| 图算法 | | ✅ |

### 7.6 性能优化要点

**必须避免的反模式**：

| 反模式 | 问题 | 正确写法 |
|--------|------|---------|
| 无属性的 MATCH 两节点 | 笛卡儿积 | 在 MATCH 中直接指定属性 |
| 变长路径无权重过滤 | 路径爆炸 | `WHERE all(r IN ... WHERE r.weight > 0.4)` |
| 返回前不 LIMIT | 内存溢出 | 任何变长查询都必须 LIMIT |

**GDS 图投影刷新策略**：
- 每天凌晨全量刷新一次
- 当 CDC 同步事件累积超过阈值（如 1000 条）触发增量刷新

**容量规划参考**（万级实体）：
- 节点 1-10 万级 → 内存几 GB
- 关系数通常为节点数的 10-50 倍
- 查询 QPS：单实例 100-500（含多跳）
- P95 响应目标 < 500ms

---

## 八、数据使用方案（按场景）

### 8.1 后台运营查询

直接 SQL 查询，对接 BI 工具（Metabase/Superset）。在 Postgres 上建语义化视图层，屏蔽底层冲突字段和 staging 细节。

### 8.2 RAG 问答系统

混合检索：结构化字段做硬过滤（精确、快），向量做软相关（处理模糊描述）。

```
用户问："适合带骑兵打山地战的高智力武将"
  ↓
Query 解析 → 硬过滤：intelligence > 80 AND applicable_unit = '骑兵'
           → 软相关：description embedding ~ "山地战"
  ↓
SQL + pgvector 联合查询 → Top-K → LLM 生成回答（带 citation）
```

### 8.3 用户聊天信息分析

- 实体识别（复用抽取 pipeline 的 Stage 1）→ 命中 aliases 索引
- 情感/意图分类
- 聚合统计（热门武将、战术口碑变化）
- 高频提及但库里属性不全的实体 → 数据补全队列

### 8.4 Agent 工具调用

数据库能力包装成工具暴露给 Agent，工具描述里把 schema 字段含义写得极清楚，复杂关系路由到 Neo4j，属性筛选路由到 Postgres。

### 8.5 复杂逻辑推理

LLM + Neo4j 协作：LLM 做"分解 + 综合"，数据库做"事实查询"。不让 LLM 凭训练知识回答游戏内事实。

---

## 九、后续待细化方向

| 方向 | 说明 | 优先级 |
|------|------|--------|
| RAG 混合检索设计 | Postgres + Neo4j + 向量的查询重写与混合排序 | 高 |
| Agent 工具 schema 文档规范 | 决定 NL2SQL 准确率的关键 | 高 |
| CDC 同步具体实现 | Debezium 配置、故障恢复、数据一致性保障 | 中 |
| GDS 算法授权与选型 | 社区版 vs Enterprise，按需评估 | 中 |
| 抽取 prompt 的 few-shot 库建设 | 人工审核回流驱动的持续优化 | 中 |
| 冲突预测分类器 | 基于历史数据训练，提前标记可能冲突的抽取 | 低 |
