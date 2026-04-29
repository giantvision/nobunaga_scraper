# 游戏领域关系型知识抽取与推理系统技术分析报告

> 版本：v1.0  
> 日期：2026-04-29  
> 主题：基于 LLM Agent 的游戏场景实体关系数据抽取、存储、维护与使用方案

---

## 1. 背景与目标

当前需求是围绕游戏场景中的实体关系进行结构化数据构建。典型实体包括：

- 武将
- 战术
- 技能
- 势力
- 兵种
- 地形
- 装备
- 事件
- 资源

其中，武将可能包含以下属性：

- 勇气
- 领导力
- 智力
- 行政能力
- 速度
- 魅力

战术则包括：

- 战术名称
- 战术类型
- 详细说明
- 触发条件
- 消耗资源
- 作用目标
- 克制关系
- 适配武将
- 适配兵种
- 适配地形
- 风险等级
- 推荐组合

数据源是大量纯文本文件，并且未来会持续有新文档进入，更新频率不确定。因此，系统不能只做一次性抽取，而应该设计成一个可持续增量维护的知识抽取系统。

最终目标不是简单地把文本转成 JSON，而是构建一个可以被多种业务场景复用的游戏知识资产系统，包括：

- 游戏知识后台查询
- RAG 问答系统
- 游戏内用户原声聊天信息分析
- 复杂关系查询
- 多跳图谱推理
- 战术推荐
- 游戏平衡性分析
- Agent 自动化辅助策划与审核

---

## 2. 总体技术定位

该系统应被设计为：

> 面向游戏领域的增量式知识抽取、事实审核、关系存储与图谱推理系统。

推荐总体架构如下：

```text
持续进入的新文档
  ↓
文档解析 / 切片 / 去重 / 版本管理
  ↓
LLM 结构化抽取 Agent
  ↓
实体归一 / 关系抽取 / 冲突检测
  ↓
人工审核 / 置信度合并 / 真值发布
  ↓
PostgreSQL 主事实库 + pgvector 语义检索
  ↓
Neo4j 图谱副库 / 多跳关系推理
  ↓
后台查询 / RAG 问答 / 用户原声分析 / 推荐与推理服务
```

核心设计原则：

```text
LLM 只产生候选事实，不直接产生最终真值；
PostgreSQL 管事实，pgvector 管语义召回，Neo4j 管复杂关系路径；
所有数据都需要有来源、证据、置信度、版本和审核状态；
下游回答尽量基于结构化查询结果和证据，而不是纯生成。
```

---

## 3. 推荐技术选型

### 3.1 主数据库：PostgreSQL

PostgreSQL 作为系统的主事实库，负责存储：

- 原始文档元信息
- 文本切片
- LLM 原始抽取结果
- 候选事实
- 正式实体
- 正式关系
- 证据链
- 冲突记录
- 审核记录
- 发布版本
- 业务查询视图

PostgreSQL 适合作为主库的原因：

- 支持成熟的关系型数据建模
- 支持复杂 SQL 查询
- 支持事务
- 支持 JSONB，便于保存 LLM 的半结构化抽取结果
- 易于和后端服务、管理后台、BI 工具集成
- 可以通过 pgvector 扩展支持向量检索

### 3.2 向量检索：pgvector

pgvector 作为 PostgreSQL 的向量扩展，适合处理：

- RAG 问答召回
- 相似文本搜索
- 相似武将搜索
- 相似战术搜索
- 抽取前上下文补充
- 冲突审核时寻找相似证据
- 根据玩家自然语言输入召回相关知识

采用 pgvector 的好处是可以把结构化查询与语义检索放在同一套 PostgreSQL 体系中。例如：

```text
找出和“适合快速突袭的高速度武将”最相似的文本 chunk，
同时要求 entity_type = general，
同时要求 source_priority >= 0.8。
```

这类查询如果使用 PostgreSQL + pgvector，会比单独维护一个向量数据库更简单。

### 3.3 图数据库：Neo4j

Neo4j 不建议作为主事实库，而建议作为复杂关系查询和多跳推理的图谱副库。

适合 Neo4j 的场景包括：

- 武将到战术的多跳关系查询
- 战术克制链分析
- 武将、兵种、地形、技能之间的组合路径分析
- 战术协同网络分析
- 策略推荐路径解释
- 图谱中心性、社区发现、相似性分析

推荐原则：

```text
PostgreSQL 是事实主库；
Neo4j 是关系推理副库；
只把 PostgreSQL 中已发布的正式实体和正式关系同步到 Neo4j。
```

### 3.4 LLM Agent 与结构化输出

LLM Agent 负责从文本中抽取实体、属性、关系和证据，但不直接写入正式表。

推荐使用：

- JSON Schema
- Structured Outputs
- Pydantic 校验
- Prompt 版本管理
- Schema 版本管理
- 模型输出置信度
- 抽取结果回溯

### 3.5 其他工程组件

推荐技术栈：

```text
语言：Python
Web 框架：FastAPI
主数据库：PostgreSQL
向量扩展：pgvector
图数据库：Neo4j
数据校验：Pydantic
异步任务：Celery / Dramatiq / RQ
管理后台：React / Next.js
缓存：Redis，可选
对象存储：S3 / MinIO，可选
```

---

## 4. 数据抽取方案设计

### 4.1 抽取系统不应设计为单个大 Agent

不建议采用如下方式：

```text
把所有文本丢给一个 Agent，让它自己理解、抽取、判断、入库。
```

这种方式会带来：

- 抽取结果不可控
- 字段不稳定
- 实体命名不统一
- 关系方向错误
- 冲突难发现
- 数据无法追溯
- 后期维护困难

推荐设计为流水线式 Agent 系统。

### 4.2 推荐 Agent 角色拆分

建议拆分为以下 8 类 Agent 或处理模块：

```text
1. Ingestion Agent
   负责发现新文档、记录来源、计算 hash。

2. Chunking Agent
   负责文本切片、章节识别、上下文保留。

3. Classification Agent
   判断文本属于武将、战术、技能、势力、兵种、事件等哪类。

4. Extraction Agent
   按 JSON Schema 抽取实体、属性、关系。

5. Normalization Agent
   做实体标准化、别名归一、单位归一、枚举归一。

6. Validation Agent
   做字段校验、范围校验、规则校验。

7. Conflict Detection Agent
   识别新旧数据冲突。

8. Review Agent
   生成审核建议、差异说明、证据摘要。
```

### 4.3 增量文档处理流程

由于会持续有新文档进入，系统需要支持增量处理。

文档进入后，需要记录：

- 文件 hash
- 来源 source
- 来源优先级 source_priority
- 版本 version
- 语言 language
- 文档状态 status
- 创建时间 created_at
- 更新时间 updated_at

推荐状态流转：

```text
NEW
  ↓
PARSED
  ↓
CHUNKED
  ↓
EMBEDDED
  ↓
EXTRACTED
  ↓
VALIDATED
  ↓
CONFLICT_CHECKED
  ↓
REVIEW_REQUIRED / AUTO_APPROVED
  ↓
PUBLISHED
```

如果文档 hash 没变，则跳过重复处理。

如果文档发生变化，只重新处理：

- 变化的 chunk
- 受影响的实体
- 受影响的关系
- 受影响的向量索引
- 受影响的图谱边

### 4.4 文本切片策略

游戏文本通常具有标题、章节、条目、人物介绍、技能说明、战术说明等结构，因此切片不应只按固定 token 数。

推荐切片优先级：

```text
第一优先级：按标题 / 小节 / 条目切
第二优先级：按实体边界切，例如一个武将一段、一个战术一段
第三优先级：按长度兜底切
```

每个 chunk 建议保留：

- chunk_id
- document_id
- chunk_index
- title_path
- content
- content_hash
- start_offset
- end_offset
- token_count
- embedding
- metadata

示例 title_path：

```text
三国志设定集 > 蜀国武将 > 赵云
战术设定 > 火攻类战术 > 连环火计
```

### 4.5 主要抽取对象

#### 4.5.1 武将 General

建议字段：

```text
name
aliases
faction
courage
leadership
intelligence
administration
speed
charisma
description
skills
preferred_units
preferred_tactics
evidence
confidence
```

#### 4.5.2 战术 Tactic

建议字段：

```text
name
aliases
tactic_type
description
effect
target_type
trigger_condition
cost
cooldown
risk
countered_by
counters
suitable_generals
suitable_units
suitable_terrains
evidence
confidence
```

#### 4.5.3 技能 Skill

建议字段：

```text
name
skill_type
description
effect
owner_generals
related_tactics
trigger_condition
evidence
confidence
```

#### 4.5.4 兵种 Unit

建议字段：

```text
name
unit_type
advantages
weaknesses
suitable_tactics
countered_by_tactics
evidence
confidence
```

#### 4.5.5 势力 Faction

建议字段：

```text
name
description
generals
tactical_style
strengths
weaknesses
evidence
confidence
```

#### 4.5.6 关系 Relation

统一关系结构：

```json
{
  "source_entity": "赵云",
  "source_type": "general",
  "relation_type": "suitable_for",
  "target_entity": "突袭",
  "target_type": "tactic",
  "properties": {
    "reason": "速度高，适合快速突击",
    "condition": "敌方阵型松散或后排暴露"
  },
  "confidence": 0.87,
  "evidence": {
    "document_id": "doc_001",
    "chunk_id": "chunk_1024",
    "quote": "赵云机动极高，适合突袭敌方后阵。"
  }
}
```

---

## 5. 真值标准、冲突检测与人工审核

### 5.1 核心原则

LLM 抽取结果不应直接成为正式数据，而应先进入候选事实层。

推荐分三层：

```text
raw_extractions：LLM 原始抽取结果
candidate_facts：候选事实层
canonical_facts：正式事实层
```

原因是同一个实体可能存在多个冲突候选，例如：

```text
赵云 speed = 88
赵云 speed = 91
赵云 speed = 95
```

这些候选都应该保留来源、证据和置信度，然后通过规则或人工审核确定最终真值。

### 5.2 候选事实 Claim 模型

每一条抽取结果都可以看作一个 claim：

```text
claim_id: c_001
subject: 赵云
predicate: speed
object: 91
source: 设定集_A
evidence: chunk_1024
model_confidence: 0.86
source_priority: 0.95
status: pending
```

### 5.3 置信度评分模型

可以先采用一个简单、可解释的评分公式：

```text
final_score =
  model_confidence * 0.4
+ source_priority * 0.3
+ evidence_quality * 0.2
+ consistency_score * 0.1
```

字段解释：

```text
model_confidence：LLM 对抽取结果的置信度
source_priority：数据源优先级
evidence_quality：证据是否明确，是否包含直接原文描述
consistency_score：是否与已有数据一致
```

数据源优先级示例：

```text
官方设定文档：1.00
策划内部文档：0.95
正式版本配置文本：0.90
历史版本文本：0.70
玩家社区整理：0.50
未知来源文本：0.30
```

### 5.4 冲突检测类型

#### 数值型冲突

示例：

```text
赵云 courage = 95
赵云 courage = 88
```

可设置规则：

```text
差异 <= 3：认为是轻微差异，可自动合并或标记为近似
差异 > 3：进入冲突审核
```

#### 枚举型冲突

示例：

```text
火攻 tactic_type = 计策
火攻 tactic_type = 攻击
```

如果枚举字段不允许多值，则进入人工审核。

#### 关系型冲突

示例：

```text
火攻 counters 藤甲兵
火攻 countered_by 藤甲兵
```

这属于关系方向冲突，需要人工审核。

#### 描述型冲突

示例：

```text
战术 A 适合山地
战术 A 不适合山地
```

这类冲突需要展示证据片段，由人工判断。

### 5.5 人工审核工作台

审核页面应展示：

- 实体名称
- 字段或关系类型
- 当前正式值
- 新候选值
- 差异说明
- 来源文档
- 来源优先级
- 原文证据
- 模型置信度
- 历史审核记录
- 推荐处理动作

审核动作建议包括：

```text
接受新值
保留旧值
合并为多值
标记为版本差异
标记为错误抽取
创建别名映射
创建新实体
```

---

## 6. 数据存储与更新维护方案

### 6.1 推荐存储分层

推荐采用五层存储：

```text
1. Source Layer：原始文档层
2. Chunk Layer：文本切片与向量层
3. Extraction Layer：LLM 原始抽取层
4. Canonical Layer：正式结构化事实层
5. Serving Layer：面向应用的查询视图 / 缓存 / 图谱副本
```

### 6.2 PostgreSQL 职责

PostgreSQL 负责：

- 原始文档
- 文本 chunk
- 抽取任务
- 抽取结果
- 候选事实
- 正式实体
- 正式关系
- 证据链
- 冲突记录
- 人工审核记录
- 发布版本

### 6.3 pgvector 职责

pgvector 负责：

- RAG 问答召回
- 相似文本召回
- 相似战术查找
- 相似武将查找
- 抽取前上下文补充
- 冲突审核时查找类似证据

### 6.4 Neo4j 职责

Neo4j 负责：

- 多跳路径查询
- 战术克制链
- 武将与战术的关系路径
- 技能、兵种、地形之间的组合推理
- 图算法分析
- 路径解释

### 6.5 数据版本管理

建议正式服务依赖发布版本，而不是直接读取 pending 数据。

版本管理应支持：

- 版本快照
- 版本回滚
- 变更 diff
- 数据发布记录
- 图谱同步版本
- RAG 索引版本

---

## 7. PostgreSQL 表结构设计

以下是一版 MVP 可用的核心表结构。

### 7.1 原始文档表

```sql
CREATE TABLE source_documents (
    id UUID PRIMARY KEY,
    title TEXT,
    source_type TEXT,
    source_uri TEXT,
    source_priority NUMERIC(4, 3),
    content_hash TEXT UNIQUE,
    version TEXT,
    language TEXT DEFAULT 'zh',
    status TEXT,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);
```

### 7.2 文本切片表

```sql
CREATE TABLE document_chunks (
    id UUID PRIMARY KEY,
    document_id UUID REFERENCES source_documents(id),
    chunk_index INT,
    title_path TEXT,
    content TEXT NOT NULL,
    content_hash TEXT,
    token_count INT,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT now()
);
```

如果启用 pgvector：

```sql
ALTER TABLE document_chunks
ADD COLUMN embedding vector;
```

实际向量维度需要与 embedding 模型一致。

### 7.3 抽取任务表

```sql
CREATE TABLE extraction_jobs (
    id UUID PRIMARY KEY,
    document_id UUID REFERENCES source_documents(id),
    chunk_id UUID REFERENCES document_chunks(id),
    job_type TEXT,
    model_name TEXT,
    prompt_version TEXT,
    schema_version TEXT,
    status TEXT,
    error_message TEXT,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT now()
);
```

### 7.4 LLM 原始抽取结果表

```sql
CREATE TABLE raw_extractions (
    id UUID PRIMARY KEY,
    job_id UUID REFERENCES extraction_jobs(id),
    chunk_id UUID REFERENCES document_chunks(id),
    extraction_json JSONB NOT NULL,
    model_confidence NUMERIC(4, 3),
    schema_version TEXT,
    created_at TIMESTAMP DEFAULT now()
);
```

### 7.5 实体主表

```sql
CREATE TABLE entities (
    id UUID PRIMARY KEY,
    entity_type TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    display_name TEXT,
    description TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now(),
    UNIQUE(entity_type, canonical_name)
);
```

entity_type 建议包括：

```text
general
tactic
skill
faction
unit
terrain
equipment
event
resource
```

### 7.6 实体别名表

```sql
CREATE TABLE entity_aliases (
    id UUID PRIMARY KEY,
    entity_id UUID REFERENCES entities(id),
    alias TEXT NOT NULL,
    alias_type TEXT,
    source_document_id UUID REFERENCES source_documents(id),
    confidence NUMERIC(4, 3),
    created_at TIMESTAMP DEFAULT now(),
    UNIQUE(entity_id, alias)
);
```

用于处理：

```text
赵云
赵子龙
常山赵云
Zhao Yun
```

### 7.7 武将属性表

#### 固定字段表

适合稳定核心属性：

```sql
CREATE TABLE general_profiles (
    entity_id UUID PRIMARY KEY REFERENCES entities(id),
    faction_id UUID REFERENCES entities(id),
    courage INT CHECK (courage BETWEEN 0 AND 100),
    leadership INT CHECK (leadership BETWEEN 0 AND 100),
    intelligence INT CHECK (intelligence BETWEEN 0 AND 100),
    administration INT CHECK (administration BETWEEN 0 AND 100),
    speed INT CHECK (speed BETWEEN 0 AND 100),
    charisma INT CHECK (charisma BETWEEN 0 AND 100),
    published_version TEXT,
    updated_at TIMESTAMP DEFAULT now()
);
```

#### 通用属性表

适合扩展属性：

```sql
CREATE TABLE entity_attribute_values (
    id UUID PRIMARY KEY,
    entity_id UUID REFERENCES entities(id),
    attribute_key TEXT NOT NULL,
    attribute_value_text TEXT,
    attribute_value_number NUMERIC,
    value_type TEXT,
    confidence NUMERIC(4, 3),
    status TEXT,
    evidence_id UUID,
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);
```

建议使用混合方案：

```text
核心稳定属性用固定字段表；
扩展属性用通用属性表。
```

### 7.8 战术详情表

```sql
CREATE TABLE tactic_profiles (
    entity_id UUID PRIMARY KEY REFERENCES entities(id),
    tactic_type TEXT,
    description TEXT,
    effect TEXT,
    target_type TEXT,
    trigger_condition TEXT,
    cost JSONB,
    cooldown JSONB,
    risk_level TEXT,
    published_version TEXT,
    updated_at TIMESTAMP DEFAULT now()
);
```

cost 示例：

```json
{
  "morale": 20,
  "food": 100,
  "cooldown_turns": 3
}
```

### 7.9 关系类型表

```sql
CREATE TABLE relation_types (
    id UUID PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    inverse_name TEXT,
    is_directed BOOLEAN DEFAULT true,
    allowed_source_types TEXT[],
    allowed_target_types TEXT[]
);
```

建议预置关系类型：

```text
belongs_to
has_skill
suitable_for
counters
countered_by
requires
synergizes_with
restrained_by
located_in
leads
allied_with
rival_of
strong_against
weak_against
recommended_with
```

### 7.10 实体关系表

```sql
CREATE TABLE entity_relations (
    id UUID PRIMARY KEY,
    source_entity_id UUID REFERENCES entities(id),
    relation_type_id UUID REFERENCES relation_types(id),
    target_entity_id UUID REFERENCES entities(id),
    properties JSONB,
    confidence NUMERIC(4, 3),
    status TEXT DEFAULT 'candidate',
    evidence_id UUID,
    valid_from TIMESTAMP,
    valid_to TIMESTAMP,
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);
```

properties 示例：

```json
{
  "reason": "速度高，适合突袭",
  "condition": "敌方后排暴露",
  "weight": 0.82
}
```

### 7.11 证据表

```sql
CREATE TABLE evidences (
    id UUID PRIMARY KEY,
    document_id UUID REFERENCES source_documents(id),
    chunk_id UUID REFERENCES document_chunks(id),
    raw_extraction_id UUID REFERENCES raw_extractions(id),
    quote TEXT,
    start_offset INT,
    end_offset INT,
    evidence_quality NUMERIC(4, 3),
    created_at TIMESTAMP DEFAULT now()
);
```

### 7.12 候选事实表

```sql
CREATE TABLE fact_candidates (
    id UUID PRIMARY KEY,
    subject_entity_id UUID REFERENCES entities(id),
    predicate TEXT NOT NULL,
    object_entity_id UUID REFERENCES entities(id),
    object_value JSONB,
    evidence_id UUID REFERENCES evidences(id),
    model_confidence NUMERIC(4, 3),
    source_priority NUMERIC(4, 3),
    final_score NUMERIC(4, 3),
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT now()
);
```

### 7.13 冲突记录表

```sql
CREATE TABLE conflict_cases (
    id UUID PRIMARY KEY,
    conflict_type TEXT,
    entity_id UUID REFERENCES entities(id),
    predicate TEXT,
    existing_fact_id UUID,
    candidate_fact_id UUID REFERENCES fact_candidates(id),
    severity TEXT,
    status TEXT DEFAULT 'open',
    summary TEXT,
    created_at TIMESTAMP DEFAULT now(),
    resolved_at TIMESTAMP
);
```

### 7.14 审核记录表

```sql
CREATE TABLE review_decisions (
    id UUID PRIMARY KEY,
    conflict_case_id UUID REFERENCES conflict_cases(id),
    reviewer_id TEXT,
    decision TEXT,
    reason TEXT,
    created_at TIMESTAMP DEFAULT now()
);
```

### 7.15 发布快照表

```sql
CREATE TABLE published_snapshots (
    id UUID PRIMARY KEY,
    version TEXT UNIQUE NOT NULL,
    description TEXT,
    published_by TEXT,
    published_at TIMESTAMP DEFAULT now(),
    metadata JSONB
);
```

---

## 8. 数据使用方案设计

### 8.1 游戏知识后台查询

典型问题：

```text
查询所有速度 > 90 且勇气 > 85 的武将
查询适合火攻的武将
查询蜀国下所有高领导力武将
查询某个战术的来源证据
```

此类场景优先使用 PostgreSQL。

示例 SQL：

```sql
SELECT e.canonical_name, gp.courage, gp.speed, gp.leadership
FROM entities e
JOIN general_profiles gp ON e.id = gp.entity_id
WHERE e.entity_type = 'general'
  AND gp.speed >= 90
  AND gp.courage >= 85;
```

### 8.2 RAG 问答系统

RAG 问答不要只依赖向量检索，推荐采用混合检索：

```text
结构化 SQL 查询
+ 向量语义召回
+ 图谱关系补充
+ LLM 最终组织回答
```

例如用户问：

```text
哪些武将适合快速突袭？
```

处理流程：

```text
1. LLM 识别意图：查询适合突袭的武将
2. PostgreSQL 查询 speed、courage 等结构化属性
3. entity_relations 查询 suitable_for = 突袭
4. pgvector 召回含有“快速、机动、突袭”的原文片段
5. LLM 基于结构化结果和证据生成答案
```

### 8.3 游戏内用户原声聊天信息分析

该场景可以分为两类。

#### 玩家反馈分析

玩家输入示例：

```text
火攻太强了，打藤甲兵基本没法反制。
赵云速度太离谱，每局都能先手。
```

可抽取：

```text
玩家提到的实体：火攻、藤甲兵、赵云
反馈类型：平衡性问题
情绪倾向：负向
问题类型：过强、缺少反制、先手过高
关联属性：speed、counter relation
```

可用于：

- 某个战术被投诉次数统计
- 某个武将是否经常被认为过强
- 某个兵种是否经常被反馈无解
- 平衡性调整优先级分析

#### 游戏内策略理解

玩家输入示例：

```text
我现在有赵云和马超，对面都是弓兵，应该怎么打？
```

处理流程：

```text
1. 抽取玩家当前阵容和敌方阵容
2. 查询结构化库：赵云、马超属性
3. 查询关系库：骑兵 vs 弓兵、突袭战术、克制关系
4. 查询图谱：武将—兵种—战术—地形组合
5. 生成策略建议
```

### 8.4 复杂关系查询

典型问题：

```text
找出所有能通过两跳关系关联到“火攻”的武将
找出“蜀国武将—适合战术—克制兵种”的路径
找出某个战术组合的协同链路
```

此类场景适合使用 Neo4j。

### 8.5 逻辑推理与推荐

不建议把复杂推理全部交给 LLM。

推荐设计为：

```text
规则引擎 / SQL / 图查询 / LLM 解释
```

例如推荐战术时：

```text
候选战术分数 =
  武将适配分
+  兵种克制分
+  地形适配分
+  敌方弱点分
+  资源成本惩罚
+  历史反馈修正
```

LLM 负责把结果解释成人话，而不是直接决定事实。

---

## 9. Neo4j 多跳推理设计

### 9.1 Neo4j 节点设计

建议节点类型：

```text
:General
:Tactic
:Skill
:Faction
:Unit
:Terrain
:Attribute
:Event
:Resource
:GameVersion
```

示例：

```cypher
(:General {id, name, courage, leadership, speed})
(:Tactic {id, name, tactic_type})
(:Unit {id, name, unit_type})
(:Terrain {id, name})
```

### 9.2 Neo4j 边设计

建议边类型：

```text
(:General)-[:BELONGS_TO]->(:Faction)
(:General)-[:HAS_SKILL]->(:Skill)
(:General)-[:SUITABLE_FOR]->(:Tactic)
(:Tactic)-[:COUNTERS]->(:Unit)
(:Tactic)-[:SUITABLE_TERRAIN]->(:Terrain)
(:Skill)-[:ENHANCES]->(:Tactic)
(:Tactic)-[:SYNERGIZES_WITH]->(:Tactic)
(:Unit)-[:WEAK_AGAINST]->(:Tactic)
```

边上建议保存：

```text
confidence
source_priority
evidence_id
version
weight
reason
postgres_relation_id
```

### 9.3 多跳查询示例

#### 查询赵云三跳以内的战术关联

```cypher
MATCH p = (g:General {name: "赵云"})-[*1..3]-(x)
WHERE x:Tactic
RETURN p
LIMIT 30;
```

#### 查询某武将到某战术的解释路径

```cypher
MATCH p = shortestPath(
  (g:General {name: "赵云"})-[*1..4]-(t:Tactic {name: "突袭"})
)
RETURN p;
```

#### 查询战术克制链

```cypher
MATCH p = (t:Tactic {name: "火攻"})-[:COUNTERS|COUNTERED_BY*1..4]-(x)
RETURN p
LIMIT 50;
```

#### 查询组合推荐路径

```cypher
MATCH p =
  (g:General)-[:SUITABLE_FOR]->(t:Tactic)-[:COUNTERS]->(u:Unit)
WHERE g.name IN ["赵云", "马超"]
  AND u.name = "弓兵"
RETURN g, t, u, p;
```

### 9.4 多跳推理限制

多跳推理不能无限扩展，否则容易出现弱相关路径。

建议限制：

```text
普通解释：1—2 跳
策略推荐：最多 3 跳
探索分析：最多 4 跳
默认不允许无限跳
```

路径评分建议：

```text
path_score =
  所有边 confidence 的乘积
  × 路径长度惩罚
  × 来源优先级
  × 关系类型权重
```

示例：

```text
赵云 -> suitable_for -> 突袭
```

这是一跳，可信度较高。

```text
赵云 -> 蜀国 -> 诸葛亮 -> 火攻 -> 藤甲兵
```

这是多跳，只能作为弱推理，不能直接作为强结论。

### 9.5 PostgreSQL 与 Neo4j 同步策略

推荐原则：

```text
PostgreSQL 是事实主库
Neo4j 是关系推理副库
```

同步流程：

```text
PostgreSQL canonical_entities / canonical_relations
  ↓
发布事件 entity_published / relation_published
  ↓
同步任务
  ↓
Neo4j upsert node / edge
```

同步要求：

```text
只同步 status = published 的数据
candidate / pending / conflict 数据不进正式图谱
每条边保留 postgres_relation_id
每个节点保留 postgres_entity_id
支持按 version 回滚或重建
```

---

## 10. LLM 抽取 JSON Schema 方向

第一版可以设计一个统一抽取 Schema：

```json
{
  "entities": [
    {
      "entity_type": "general",
      "name": "赵云",
      "aliases": ["赵子龙", "常山赵云"],
      "attributes": {
        "courage": 95,
        "leadership": 83,
        "intelligence": 76,
        "administration": 58,
        "speed": 91,
        "charisma": 87
      },
      "description": "赵云机动性强，擅长突袭与护卫。",
      "confidence": 0.86,
      "evidence": [
        {
          "quote": "赵云机动极高，适合突袭敌方后阵。",
          "chunk_id": "chunk_1024"
        }
      ]
    }
  ],
  "relations": [
    {
      "source_entity": "赵云",
      "source_type": "general",
      "relation_type": "suitable_for",
      "target_entity": "突袭",
      "target_type": "tactic",
      "properties": {
        "reason": "速度高，适合快速突击"
      },
      "confidence": 0.87,
      "evidence": [
        {
          "quote": "赵云机动极高，适合突袭敌方后阵。",
          "chunk_id": "chunk_1024"
        }
      ]
    }
  ]
}
```

后续可以按实体类型拆分更细 Schema：

- general_extraction_schema
- tactic_extraction_schema
- skill_extraction_schema
- relation_extraction_schema
- feedback_analysis_schema

---

## 11. 典型完整流程示例

### 11.1 新增一份战术文档

```text
1. 新文档进入 source_documents
2. 计算 hash，确认是新文档
3. 切成 chunks
4. 为 chunk 生成 embedding
5. Classification Agent 判断属于 tactic 文档
6. Extraction Agent 抽取战术实体和关系
7. Validation Agent 校验字段
8. Normalization Agent 识别“火计”和“火攻”是否为同义
9. Conflict Detection Agent 发现“火攻冷却时间”与已有数据冲突
10. 生成 conflict_case
11. 人工审核
12. 审核通过后写入 tactic_profiles 和 entity_relations
13. 发布新版本
14. 同步 Neo4j
15. RAG 和后台查询可用
```

### 11.2 玩家提出策略问题

玩家输入：

```text
我现在有赵云和马超，对面都是弓兵，应该怎么打？
```

系统处理：

```text
1. LLM / NLU 抽取当前上下文：
   - 我方武将：赵云、马超
   - 敌方兵种：弓兵

2. PostgreSQL 查询：
   - 赵云、马超属性
   - 适配兵种
   - 适配战术

3. Neo4j 查询：
   - 赵云 / 马超 -> 适合战术 -> 克制弓兵 的路径

4. pgvector 召回：
   - 和弓兵克制、骑兵突袭、阵型突破相关的文本证据

5. 规则引擎打分：
   - 战术适配分
   - 克制关系分
   - 资源成本分
   - 风险惩罚

6. LLM 生成最终解释：
   - 推荐战术
   - 推荐理由
   - 风险提醒
   - 替代方案
```

---

## 12. 分阶段实施路线图

### 第一阶段：MVP

目标：跑通抽取、审核、查询闭环。

建议实现：

```text
文本上传 / 新文档监听
文档 hash 去重
文本切片
LLM 结构化抽取
Pydantic 校验
候选事实入库
基础冲突检测
人工审核
正式发布
PostgreSQL 查询接口
基础 RAG 问答
```

推荐技术：

```text
Python
FastAPI
PostgreSQL
pgvector
Pydantic
LLM Structured Outputs
Celery / RQ / Dramatiq
React / Next.js 管理后台
```

### 第二阶段：知识质量与增量维护

目标：提升数据质量和长期可维护性。

建议实现：

```text
实体消歧
别名管理
来源优先级体系
置信度评分模型
审核工作台
版本快照
差异比较
增量重抽取
批量回滚
抽取质量评估集
```

### 第三阶段：图谱与复杂推理

目标：支持复杂关系查询和路径推理。

建议实现：

```text
Neo4j 图谱副库
PostgreSQL 到 Neo4j 同步
多跳路径查询
战术组合推荐
克制链分析
关系解释
图算法分析
```

### 第四阶段：游戏内智能应用

目标：服务真实业务场景。

建议实现：

```text
游戏内玩家输入解析
阵容识别
战术推荐
平衡性反馈分析
RAG + SQL + Graph 混合问答
用户原声数据分析看板
策略推荐 API
策划辅助 Agent
```

---

## 13. 关键风险与应对策略

### 13.1 LLM 抽取不稳定

风险：

- 字段缺失
- 格式漂移
- 关系方向错误
- 编造实体

应对：

```text
使用严格 JSON Schema
使用 Pydantic 校验
所有抽取都进入候选层
必须绑定证据
低置信度进入人工审核
Prompt 和 Schema 做版本管理
```

### 13.2 数据冲突不断累积

风险：

- 新旧文档冲突
- 不同来源冲突
- 版本差异冲突
- 人工审核成本升高

应对：

```text
建立来源优先级
建立置信度评分
建立冲突检测规则
建立审核队列优先级
支持版本化数据
支持自动合并低风险差异
```

### 13.3 图谱关系过度泛化

风险：

- 多跳路径看似相关但实际弱相关
- 推理链过长导致结论不可靠
- 图谱噪声影响推荐

应对：

```text
限制默认跳数
路径打分
边置信度过滤
关系类型加权
只同步已发布数据
图谱推理结果作为辅助，不直接作为事实
```

### 13.4 RAG 回答幻觉

风险：

- 只靠向量召回导致回答不精确
- LLM 根据相似文本生成错误结论
- 忽略正式结构化数据

应对：

```text
RAG 必须结合 SQL 查询
回答附带证据
优先使用 canonical facts
语义召回只作为补充上下文
关键数值和关系从数据库读取
```

---

## 14. 最终推荐方案

结合当前需求：

```text
1. 文档持续增量进入，更新频率不确定；
2. 数据存在冲突，需要人工审核或基于来源优先级计算置信度；
3. 下游场景包括后台查询、RAG、用户原声分析、复杂关系查询和逻辑推理。
```

推荐采用：

```text
PostgreSQL 作为主事实库
pgvector 作为语义检索层
Neo4j 作为复杂关系推理层
LLM Agent 作为结构化抽取与解释层
人工审核系统作为真值仲裁层
```

最终架构核心可以概括为：

```text
文本 → 候选事实 → 审核真值 → 结构化主库 → 向量检索 → 图谱推理 → 多场景服务
```

优先落地顺序建议：

```text
第一优先级：
  PostgreSQL 表结构、文本切片、LLM JSON Schema、候选事实入库。

第二优先级：
  冲突检测、人工审核、版本发布、基础 RAG。

第三优先级：
  Neo4j 同步、多跳路径查询、策略推荐、用户原声分析。

第四优先级：
  图算法分析、平衡性洞察、策划辅助 Agent、自动化知识维护。
```

---

## 15. 下一步可继续细化的方向

建议后续继续拆解以下技术细节：

1. **LLM 抽取 JSON Schema 详细设计**
   - 武将 Schema
   - 战术 Schema
   - 关系 Schema
   - 用户反馈 Schema

2. **PostgreSQL 完整 DDL**
   - 索引设计
   - 约束设计
   - 分区策略
   - 查询视图

3. **Agent 工作流设计**
   - 各 Agent 的输入输出
   - Prompt 模板
   - 错误重试
   - 抽取评估

4. **审核工作台产品设计**
   - 冲突队列
   - 证据对比
   - 版本发布
   - 审核权限

5. **Neo4j 图谱模型设计**
   - 节点类型
   - 边类型
   - 路径评分
   - 多跳推理模板

6. **RAG + SQL + Graph 混合问答架构**
   - Query Router
   - SQL Agent
   - Graph Agent
   - Retriever
   - Answer Composer

7. **游戏内实时使用方案**
   - 延迟要求
   - 缓存策略
   - 推荐 API
   - 用户输入理解
   - 风险控制
