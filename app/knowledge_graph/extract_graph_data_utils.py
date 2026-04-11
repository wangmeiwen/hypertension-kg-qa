"""
高血压知识图谱抽取工具模块。

本模块用于从文本中抽取结构化的高血压临床知识，包括：
- 实体抽取：疾病、药物、检查、指标、指南等
- 关系抽取：疾病-药物、疾病-症状、药物-不良反应等关系
- 批量处理：从文件夹中批量抽取知识

核心函数：
- extract_hypertension_knowledge: 从单段文本抽取知识
- normalize_extraction_result: 标准化抽取结果格式
- build_entity_map: 构建去重实体映射
- build_relation_list: 构建去重关系列表

主要类：
- Entity: 实体数据结构
- Relation: 关系数据结构
- HypertensionKnowledgeGraph: 知识图谱数据结构

"""
from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from langchain_core.prompts import PromptTemplate
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional, Tuple, Literal, Callable
import json
import logging

from langchain_openai import ChatOpenAI
from app.common.config import Config
from app.common.perf_logger import log_duration

logger = logging.getLogger(__name__)


GRAPH_LLM_TIMEOUT_SECONDS = 600
_default_llm_bundle: Optional[Dict[str, Any]] = None


# 
# 枚举定义：实体类型
# 
EntityType = Literal[
    "Disease",             # 高血压、继发性高血压、糖尿病、慢性肾病等
    "DiseaseStage",        # 高血压分期
    "DiseaseGrade",        # 高血压分级（1级/2级/3级等）
    "RiskStratification",  # 心血管风险分层（低危/中危/高危/很高危）
    "Phenotype",           # 白大衣性高血压、隐蔽性高血压、夜间高血压、单纯收缩期高血压等
    "BloodPressurePattern",# 清晨高血压、夜间高血压、勺型/非勺型等模式
    "Symptom",             # 头晕、头痛、心悸
    "RiskFactor",          # 高盐饮食、肥胖、吸烟、家族史、过量饮酒
    "Complication",        # 脑卒中、心衰、慢性肾病
    "TargetOrganDamage",   # 左室肥厚、蛋白尿、视网膜病变、动脉硬化等
    "Check",               # 动态血压监测、家庭血压监测、肾功能检查、眼底检查
    "MeasurementMethod",   # 诊室血压、AOBP、ABPM、HBPM 等测量方式
    "Indicator",           # 收缩压、舒张压、UACR、eGFR、LVMI
    "MeasurementContext",  # 诊室血压、家庭血压、动态血压、夜间血压、清晨血压
    "ThresholdRule",       # 阈值规则实体
    "Drug",                # 氨氯地平、缬沙坦
    "DrugClass",           # ACEI、ARB、CCB、利尿剂、β受体阻滞剂
    "Reaction",            # 踝部水肿、干咳、高尿酸血症
    "TreatmentPlan",       # 生活方式干预、联合降压方案、随访管理方案
    "PatientProfile",      # 老年高血压患者、妊娠高血压患者、合并糖尿病患者
    "ClinicalRule",        # 用药规则、分层规则、检查规则、随访规则
    "Guideline",           # 指南
    "Source"               # 文献、数据库、网页、病例来源
]


# 
# 枚举定义：关系类型
# 
RelationType = Literal[
    "HAS_STAGE",                 # 疾病 -> 疾病分期
    "HAS_GRADE",                 # 疾病/患者画像 -> 疾病分级
    "HAS_RISK_STRATIFICATION",   # 疾病/患者画像 -> 风险分层
    "HAS_PHENOTYPE",             # 疾病 -> 临床表型
    "HAS_BP_PATTERN",            # 疾病/患者画像 -> 血压模式
    "HAS_SYMPTOM",               # 疾病 -> 症状
    "HAS_RISK_FACTOR",           # 疾病 -> 危险因素
    "HAS_COMPLICATION",          # 疾病 -> 并发症
    "HAS_TARGET_ORGAN_DAMAGE",   # 疾病 -> 靶器官损害

    "NEEDS_CHECK",               # 疾病/患者画像 -> 检查项目
    "DIAGNOSED_BY_METHOD",       # 疾病/表型/血压模式 -> 测量方式
    "HAS_INDICATOR",             # 疾病/检查/靶器官损害 -> 指标
    "METHOD_MEASURES",           # 测量方式/检查 -> 指标
    "MEASURED_IN_CONTEXT",       # 指标/检查 -> 测量场景
    "HAS_THRESHOLD",             # 疾病/表型/指标/场景 -> 阈值规则
    "HAS_CONTROL_TARGET",        # 疾病/患者画像/血压模式 -> 血压控制目标
    "START_TREATMENT_AT",        # 疾病/患者画像 -> 启动治疗阈值
    "IDENTIFIED_BY",             # 表型/靶器官损害 -> 检查项目

    "COMMON_DRUG",               # 疾病 -> 常用药物
    "BELONGS_TO_CLASS",          # 药物 -> 药物类别
    "MAY_CAUSE_REACTION",        # 药物 -> 不良反应
    "TREATS_DISEASE",            # 药物/方案 -> 治疗疾病
    "SUITABLE_FOR_PROFILE",      # 药物/方案 -> 适用患者画像

    "HAS_COMORBIDITY",           # 患者画像 -> 合并疾病
    "HAS_TRIGGER",               # 临床规则 -> 触发条件
    "APPLIES_TO_DISEASE",        # 临床规则 -> 适用疾病
    "APPLIES_TO_PROFILE",        # 临床规则 -> 适用患者画像
    "RECOMMENDS_DRUG",           # 临床规则 -> 推荐药物
    "RECOMMENDS_DRUG_CLASS",     # 临床规则 -> 推荐药物类别
    "RECOMMENDS_CHECK",          # 临床规则 -> 推荐检查
    "RECOMMENDS_PLAN",           # 临床规则 -> 推荐治疗方案

    "FROM_GUIDELINE",            # 实体/规则 -> 指南
    "FROM_SOURCE",               # 实体/规则 -> 来源
    "SUPPORTED_BY"               # 实体/规则 -> 被来源支持
]


# 
# 实体 / 关系 / 输出结构
# 
class Entity(BaseModel):
    """定义知识图谱中的标准化实体结构。"""

    name: str
    type: EntityType
    canonical_name: Optional[str] = None
    attributes: Dict[str, Any] = Field(default_factory=dict)


class Relation(BaseModel):
    """定义知识图谱中的实体关系结构。"""

    subject: str
    subject_type: EntityType
    relation: RelationType
    object: str
    object_type: EntityType
    attributes: Dict[str, Any] = Field(default_factory=dict)


class HypertensionKnowledgeGraph(BaseModel):
    """定义高血压知识图谱抽取结果的聚合结构。"""

    entities: List[Entity] = Field(default_factory=list)
    relations: List[Relation] = Field(default_factory=list)


# 
# 输出解析器
# 
parser = JsonOutputParser(pydantic_object=HypertensionKnowledgeGraph)


def create_graph_llm_bundle(
    *,
    api_key: str,
    base_url: str,
    model_name: str,
    timeout: int = GRAPH_LLM_TIMEOUT_SECONDS,
    max_retries: int = 0,
    lane_id: Optional[int] = None,
) -> Dict[str, Any]:
    """按 lane 创建独立的大模型调用链。"""
    llm = ChatOpenAI(
        api_key=api_key,
        base_url=base_url,
        model=model_name,
        timeout=timeout,
        max_retries=max_retries,
    )
    return {
        "lane_id": lane_id,
        "model_name": model_name,
        "llm": llm,
        "primary_chain": primary_prompt | llm | parser,
        "judge_chain": omission_judge_prompt | llm | StrOutputParser(),
        "supplement_chain": supplement_prompt | llm | parser,
    }


def _get_default_graph_llm_bundle() -> Dict[str, Any]:
    """获取默认图谱抽取模型调用链。"""
    global _default_llm_bundle
    if _default_llm_bundle is None:
        conf = Config()
        _default_llm_bundle = create_graph_llm_bundle(
            api_key=str(conf.MODEL_API_KEY or ""),
            base_url=str(conf.MODEL_BASE_URL or ""),
            model_name=str(conf.MODEL_NAME or ""),
            timeout=GRAPH_LLM_TIMEOUT_SECONDS,
            max_retries=0,
            lane_id=1,
        )
    return _default_llm_bundle


# 
# Prompt（主抽取）
# 
primary_prompt = PromptTemplate(
    template=(
        "你是一个高血压临床知识图谱抽取专家。请从输入文本中抽取结构化知识。\n"
        "你的目标不是泛泛抽名词，而是抽取“实体 + 明确关系 + 可追溯来源”的临床知识。\n\n"

        "【核心原则】\n"
        "1. 只抽取文本中明确表达的关系，禁止依赖常识补全。\n"
        "2. 若文本只列举概念、没有形成明确关系，返回空结构：\n"
        "{{\"entities\": [], \"relations\": []}}\n"
        "3. 严格去重：同名同类型实体只保留一个。\n"
        "4. 若同一实体同时有中文名和英文缩写，name 保留正文主名，另一个放入 canonical_name 或 attributes.alias。\n"
        "5. 若文本中有指南、文献、数据库、PMID、DOI、URL、章节号、发布机构等来源信息，尽量创建 Guideline 或 Source 实体，并建立来源关系。\n\n"

        "【抽取范围】\n"
        "面向高血压领域，抽取以下内容：\n"
        "- 疾病及其表型\n"
        "- 高血压分期、分级、危险分层\n"
        "- 血压模式（清晨、夜间、白大衣、隐蔽性等）\n"
        "- 症状、危险因素、并发症、靶器官损害\n"
        "- 检查、测量方式、指标、测量场景、阈值规则\n"
        "- 药物、药物类别、不良反应、治疗方案\n"
        "- 患者画像、复杂临床规则\n"
        "- 指南/文献/数据库等证据来源\n\n"

        "【实体类型说明】\n"
        "- Disease：疾病，如高血压、继发性高血压、糖尿病、慢性肾病\n"
        "- DiseaseStage：疾病分期，如高血压前期、确诊期等\n"
        "- DiseaseGrade：疾病分级，如1级高血压、2级高血压、3级高血压\n"
        "- RiskStratification：风险分层，如低危、中危、高危、很高危\n"
        "- Phenotype：高血压表型，如白大衣性高血压、隐蔽性高血压、夜间高血压、单纯收缩期高血压\n"
        "- BloodPressurePattern：血压模式，如清晨高血压、夜间高血压、勺型/非勺型\n"
        "- Symptom：症状，如头晕、头痛、心悸\n"
        "- RiskFactor：危险因素，如高盐饮食、肥胖、吸烟、家族史\n"
        "- Complication：并发症，如脑卒中、心力衰竭、慢性肾病\n"
        "- TargetOrganDamage：靶器官损害，如左室肥厚、蛋白尿、视网膜病变、动脉硬化\n"
        "- Check：检查项目，如动态血压监测、家庭血压监测、肾功能检查、眼底检查\n"
        "- MeasurementMethod：血压测量方式，如诊室血压、AOBP、ABPM、HBPM\n"
        "- Indicator：指标，如收缩压、舒张压、UACR、eGFR、LVMI\n"
        "- MeasurementContext：测量场景，如诊室血压、家庭血压、动态血压、夜间血压、清晨血压\n"
        "- ThresholdRule：阈值规则实体，用于承载不同场景下的诊断阈值、控制目标、预警阈值\n"
        "- Drug：具体药物，如氨氯地平、缬沙坦\n"
        "- DrugClass：药物类别，如 ACEI、ARB、CCB、利尿剂、β受体阻滞剂\n"
        "- Reaction：药物不良反应，如踝部水肿、干咳\n"
        "- TreatmentPlan：治疗方案/管理方案，如生活方式干预、联合降压治疗、随访管理\n"
        "- PatientProfile：患者画像/适用人群，如老年高血压患者、妊娠高血压患者、合并糖尿病患者\n"
        "- ClinicalRule：复杂临床规则实体，用于承载“什么患者/什么条件下推荐什么”的规则\n"
        "- Guideline：指南实体\n"
        "- Source：文献、数据库、网页、病例来源\n\n"

        "【关系类型说明】\n"
        "- HAS_STAGE：疾病具有某分期\n"
        "- HAS_GRADE：疾病或患者画像具有某分级\n"
        "- HAS_RISK_STRATIFICATION：疾病或患者画像对应某风险分层\n"
        "- HAS_PHENOTYPE：疾病具有某临床表型\n"
        "- HAS_BP_PATTERN：疾病或患者画像具有某血压模式\n"
        "- HAS_SYMPTOM：疾病具有症状\n"
        "- HAS_RISK_FACTOR：疾病具有危险因素\n"
        "- HAS_COMPLICATION：疾病具有并发症\n"
        "- HAS_TARGET_ORGAN_DAMAGE：疾病具有靶器官损害\n"
        "- NEEDS_CHECK：疾病或患者画像需要某项检查\n"
        "- DIAGNOSED_BY_METHOD：疾病/表型/血压模式通过某测量方式诊断\n"
        "- HAS_INDICATOR：疾病/检查/靶器官损害关联某指标\n"
        "- METHOD_MEASURES：测量方式或检查可测量某指标\n"
        "- MEASURED_IN_CONTEXT：指标或检查对应某测量场景\n"
        "- HAS_THRESHOLD：疾病/表型/指标/场景具有某阈值规则\n"
        "- HAS_CONTROL_TARGET：疾病/患者画像/血压模式具有血压控制目标阈值\n"
        "- START_TREATMENT_AT：疾病/患者画像达到某阈值时应启动药物治疗\n"
        "- IDENTIFIED_BY：某表型或靶器官损害通过某检查识别\n"
        "- COMMON_DRUG：疾病常用某药物（仅当文本是直接描述常用药时使用）\n"
        "- BELONGS_TO_CLASS：药物属于某药物类别\n"
        "- MAY_CAUSE_REACTION：药物可能导致某不良反应\n"
        "- TREATS_DISEASE：药物或方案治疗某疾病\n"
        "- SUITABLE_FOR_PROFILE：药物或方案适用于某患者画像\n"
        "- HAS_COMORBIDITY：患者画像合并某疾病\n"
        "- HAS_TRIGGER：临床规则的触发条件，可连接症状/危险因素/指标/靶器官损害/表型/疾病\n"
        "- APPLIES_TO_DISEASE：临床规则适用于某疾病\n"
        "- APPLIES_TO_PROFILE：临床规则适用于某类患者画像\n"
        "- RECOMMENDS_DRUG：临床规则推荐某具体药物\n"
        "- RECOMMENDS_DRUG_CLASS：临床规则推荐某药物类别\n"
        "- RECOMMENDS_CHECK：临床规则推荐某检查\n"
        "- RECOMMENDS_PLAN：临床规则推荐某治疗方案\n"
        "- FROM_GUIDELINE：实体或规则来源于某指南\n"
        "- FROM_SOURCE：实体或规则来源于某来源\n"
        "- SUPPORTED_BY：实体或规则被某来源支持\n\n"

        "【关键约束】\n"
        "1. 严格区分 Check 与 Indicator。\n"
        "   - Check 是检查项目或检查方法\n"
        "   - Indicator 是可测量指标或量化指标\n\n"
        "2. 严格区分 Symptom 与 Reaction。\n"
        "   - Symptom 是疾病症状\n"
        "   - Reaction 是药物不良反应\n\n"
        "3. 严格区分 Complication 与 TargetOrganDamage。\n"
        "   - Complication 更偏明确临床结局或并发症\n"
        "   - TargetOrganDamage 更偏亚临床损害、器官损伤、器官受累表现\n\n"
        "4. 若文本中出现不同测量场景下的血压阈值、目标血压、诊断标准，优先创建 ThresholdRule 实体，不要把阈值仅写成普通字符串。\n\n"
        "5. 若文本出现复杂规则，例如：\n"
        "   “对于合并糖尿病且尿白蛋白升高的高血压患者，优先考虑 ACEI 或 ARB。”\n"
        "   必须创建 ClinicalRule 实体，并建立：\n"
        "   - ClinicalRule -> APPLIES_TO_DISEASE -> 高血压\n"
        "   - ClinicalRule -> APPLIES_TO_PROFILE -> 合并糖尿病患者\n"
        "   - ClinicalRule -> HAS_TRIGGER -> 尿白蛋白升高\n"
        "   - ClinicalRule -> RECOMMENDS_DRUG_CLASS -> ACEI / ARB\n"
        "   不要简单压缩成“高血压 -> COMMON_DRUG -> ACEI”。\n\n"

        "6. 若文本明确出现推荐类别、推荐强度、证据等级、章节、页码等，应尽量写入 attributes。\n\n"

        "【attributes 字段建议】\n"
        "所有实体和关系都允许使用 attributes 承载补充信息，常见字段可包括但不限于：\n"
        "- alias\n"
        "- subtype\n"
        "- definition\n"
        "- method\n"
        "- purpose\n"
        "- frequency\n"
        "- unit\n"
        "- comparison\n"
        "- value\n"
        "- threshold_type\n"
        "- target_bp\n"
        "- rule_type\n"
        "- condition\n"
        "- recommendation\n"
        "- recommendation_class\n"
        "- evidence_level\n"
        "- source_type\n"
        "- title\n"
        "- organization\n"
        "- year\n"
        "- section\n"
        "- doi\n"
        "- pmid\n"
        "- url\n"
        "- note\n\n"

        "【示例：文本 -> 抽取结果】\n"
        "示例1（复杂规则）：\n"
        "输入文本：\n"
        "对于合并糖尿病且尿白蛋白升高的高血压患者，优先考虑 ACEI 或 ARB。\n"
        "示例输出：\n"
        "{{\n"
        "  \"entities\": [\n"
        "    {{\"name\": \"高血压\", \"type\": \"Disease\"}},\n"
        "    {{\"name\": \"合并糖尿病患者\", \"type\": \"PatientProfile\", \"attributes\": {{\"condition\": \"高血压\", \"has_comorbidity\": \"糖尿病\"}}}},\n"
        "    {{\"name\": \"尿白蛋白升高\", \"type\": \"Indicator\"}},\n"
        "    {{\"name\": \"优先用药规则\", \"type\": \"ClinicalRule\", \"attributes\": {{\"recommendation\": \"优先考虑\"}}}},\n"
        "    {{\"name\": \"ACEI\", \"type\": \"DrugClass\"}},\n"
        "    {{\"name\": \"ARB\", \"type\": \"DrugClass\"}}\n"
        "  ],\n"
        "  \"relations\": [\n"
        "    {{\"subject\": \"优先用药规则\", \"subject_type\": \"ClinicalRule\", \"relation\": \"APPLIES_TO_DISEASE\", \"object\": \"高血压\", \"object_type\": \"Disease\"}},\n"
        "    {{\"subject\": \"优先用药规则\", \"subject_type\": \"ClinicalRule\", \"relation\": \"APPLIES_TO_PROFILE\", \"object\": \"合并糖尿病患者\", \"object_type\": \"PatientProfile\"}},\n"
        "    {{\"subject\": \"优先用药规则\", \"subject_type\": \"ClinicalRule\", \"relation\": \"HAS_TRIGGER\", \"object\": \"尿白蛋白升高\", \"object_type\": \"Indicator\"}},\n"
        "    {{\"subject\": \"优先用药规则\", \"subject_type\": \"ClinicalRule\", \"relation\": \"RECOMMENDS_DRUG_CLASS\", \"object\": \"ACEI\", \"object_type\": \"DrugClass\"}},\n"
        "    {{\"subject\": \"优先用药规则\", \"subject_type\": \"ClinicalRule\", \"relation\": \"RECOMMENDS_DRUG_CLASS\", \"object\": \"ARB\", \"object_type\": \"DrugClass\"}}\n"
        "  ]\n"
        "}}\n\n"

        "示例2（阈值与测量场景）：\n"
        "输入文本：\n"
        "家庭血压 ≥ 135/85 mmHg 可作为高血压诊断阈值之一。\n"
        "示例输出：\n"
        "{{\n"
        "  \"entities\": [\n"
        "    {{\"name\": \"高血压\", \"type\": \"Disease\"}},\n"
        "    {{\"name\": \"家庭血压\", \"type\": \"MeasurementContext\"}},\n"
        "    {{\"name\": \"家庭血压高血压诊断阈值\", \"type\": \"ThresholdRule\", \"attributes\": {{\"systolic\": \"135\", \"diastolic\": \"85\", \"unit\": \"mmHg\", \"threshold_type\": \"diagnostic\"}}}}\n"
        "  ],\n"
        "  \"relations\": [\n"
        "    {{\"subject\": \"高血压\", \"subject_type\": \"Disease\", \"relation\": \"HAS_THRESHOLD\", \"object\": \"家庭血压高血压诊断阈值\", \"object_type\": \"ThresholdRule\"}},\n"
        "    {{\"subject\": \"家庭血压高血压诊断阈值\", \"subject_type\": \"ThresholdRule\", \"relation\": \"MEASURED_IN_CONTEXT\", \"object\": \"家庭血压\", \"object_type\": \"MeasurementContext\"}}\n"
        "  ]\n"
        "}}\n\n"

        "示例3（药物类别与不良反应）：\n"
        "输入文本：\n"
        "氨氯地平属于 CCB，可能引起踝部水肿。\n"
        "示例输出：\n"
        "{{\n"
        "  \"entities\": [\n"
        "    {{\"name\": \"氨氯地平\", \"type\": \"Drug\"}},\n"
        "    {{\"name\": \"CCB\", \"type\": \"DrugClass\"}},\n"
        "    {{\"name\": \"踝部水肿\", \"type\": \"Reaction\"}}\n"
        "  ],\n"
        "  \"relations\": [\n"
        "    {{\"subject\": \"氨氯地平\", \"subject_type\": \"Drug\", \"relation\": \"BELONGS_TO_CLASS\", \"object\": \"CCB\", \"object_type\": \"DrugClass\"}},\n"
        "    {{\"subject\": \"氨氯地平\", \"subject_type\": \"Drug\", \"relation\": \"MAY_CAUSE_REACTION\", \"object\": \"踝部水肿\", \"object_type\": \"Reaction\"}}\n"
        "  ]\n"
        "}}\n\n"

        "说明：以上示例用于格式与关系建模参考，不要求逐字段照搬；但关系方向、实体类型、关系类型必须一致。\n\n"

        "【执行步骤】\n"
        "1. 先识别实体，再建立关系，最后做一致性校验。\n"
        "2. 关系中的 subject/object 必须与 entities 中某实体同名且类型匹配。\n"
        "3. 若关系已识别但实体遗漏，必须补齐对应实体。\n"
        "4. 如无可抽取关系，返回空结构。\n\n"

        "【输出要求】\n"
        "所有输出必须严格符合以下 JSON 格式：\n"
        "{format_instructions}\n\n"

        "输入文本：\n{text}"
    ),
    input_variables=["text"],
    partial_variables={"format_instructions": parser.get_format_instructions()}
)


# 
# Prompt（漏抽判定）
# 
omission_judge_prompt = PromptTemplate(
    template=(
        "你是知识图谱抽取质检专家。\n"
        "给定原文与当前抽取结果，判断是否还存在明确可抽取但未抽取的实体或关系。\n"
        "仅输出 YES 或 NO，不要输出其他内容。\n\n"
        "原文：\n{text}\n\n"
        "当前抽取结果：\n{draft_json}"
    ),
    input_variables=["text", "draft_json"]
)


# 
# Prompt（增量补抽）
# 
supplement_prompt = PromptTemplate(
    template=(
        "你是高血压知识图谱补抽专家。\n"
        "请基于原文与当前抽取结果，只输出遗漏的增量实体与增量关系。\n"
        "禁止重复输出已存在项。\n"
        "若无增量，返回：{{\"entities\": [], \"relations\": []}}\n\n"
        "一致性要求：\n"
        "1. 新关系中的 subject/object 必须在增量 entities 或当前结果 entities 中存在。\n"
        "2. 仅保留文本中明确表达的事实，禁止常识补全。\n\n"
        "当前抽取结果（仅供参考去重）：\n{draft_json}\n\n"
        "原文：\n{text}\n\n"
        "输出必须严格符合：\n{format_instructions}"
    ),
    input_variables=["text", "draft_json"],
    partial_variables={"format_instructions": parser.get_format_instructions()}
)


# 
# 抽取主函数
# 
def _emit_stage_event(
    stage_callback: Optional[Callable[..., None]],
    action: str,
    stage: str,
    detail: str,
) -> None:
    """安全触发阶段事件回调。"""
    if not stage_callback:
        return
    try:
        stage_callback(action, stage, detail, detail)
    except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError):
        logger.debug("kg.stage_callback_failed", exc_info=True)


def extract_hypertension_knowledge(
    text: str,
    max_gleanings: int = 1,
    stage_callback: Optional[Callable[..., None]] = None,
    llm_bundle: Optional[Dict[str, Any]] = None,
) -> Dict[str, List[dict]]:
    """
    从文本中抽取高血压知识图谱。

    执行主抽取、漏抽判定与增量补抽，返回最终结构化结果。

    Args:
        text: 输入文本
        max_gleanings: 最大补抽次数，默认 1

    Returns:
        包含 entities 和 relations 的字典
    """
    bundle = llm_bundle or _get_default_graph_llm_bundle()
    primary_chain = bundle["primary_chain"]
    judge_chain = bundle["judge_chain"]
    supplement_chain = bundle["supplement_chain"]

    _emit_stage_event(stage_callback, "enter", "kg_primary_extract", "正在执行图谱主抽取")
    with log_duration("kg.llm_extract.primary", text_len=len(text)):
        primary_result = primary_chain.invoke({"text": text})
    _emit_stage_event(stage_callback, "complete", "kg_primary_extract", "图谱主抽取完成")

    merged_result = normalize_extraction_result(primary_result)
    glean_times = max(0, int(max_gleanings))

    for round_index in range(glean_times):
        draft_json = json.dumps(merged_result, ensure_ascii=False)
        _emit_stage_event(
            stage_callback,
            "enter",
            "kg_omission_judge",
            f"正在执行漏抽判定（第 {round_index + 1}/{glean_times} 轮）",
        )
        with log_duration("kg.llm_extract.omission_judge", text_len=len(text)):
            judge_result = judge_chain.invoke({"text": text, "draft_json": draft_json})
        _emit_stage_event(
            stage_callback,
            "complete",
            "kg_omission_judge",
            f"漏抽判定完成（第 {round_index + 1}/{glean_times} 轮）",
        )

        judge_text = str(judge_result or "").strip().upper()
        if "YES" not in judge_text and "是" not in str(judge_result or ""):
            break

        try:
            _emit_stage_event(
                stage_callback,
                "enter",
                "kg_supplement_extract",
                f"正在执行补抽（第 {round_index + 1}/{glean_times} 轮）",
            )
            with log_duration("kg.llm_extract.supplement", text_len=len(text)):
                delta_result = supplement_chain.invoke({"text": text, "draft_json": draft_json})
            _emit_stage_event(
                stage_callback,
                "complete",
                "kg_supplement_extract",
                f"补抽完成（第 {round_index + 1}/{glean_times} 轮）",
            )
            normalized_delta = normalize_extraction_result(delta_result)
            merged_result = merge_extraction_results(merged_result, normalized_delta)
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as e:
            logger.exception(
                "补抽阶段失败。error_type=%s, error=%s",
                type(e).__name__,
                str(e),
            )
            raise

    return merged_result


# 
# 标准化抽取结果
# 
def normalize_extraction_result(result: Any) -> Dict[str, List[dict]]:
    """
    将模型输出规范化为 entities/relations 两个列表结构。

    Args:
        result: 模型输出结果（可以是 Pydantic 模型或字典）

    Returns:
        标准化的抽取结果字典
    """
    if hasattr(result, "model_dump"):
        raw = result.model_dump()
    elif isinstance(result, dict):
        raw = result
    else:
        raw = {}

    entities = raw.get("entities") or []
    relations = raw.get("relations") or []

    if not isinstance(entities, list):
        entities = []
    if not isinstance(relations, list):
        relations = []

    normalized_entities = []
    for e in entities:
        if not isinstance(e, dict):
            continue
        normalized_entities.append({
            "name": str(e.get("name") or "").strip(),
            "type": str(e.get("type") or "").strip(),
            "canonical_name": str(e.get("canonical_name") or "").strip() or None,
            "attributes": e.get("attributes") or {}
        })

    normalized_relations = []
    for r in relations:
        if not isinstance(r, dict):
            continue
        normalized_relations.append({
            "subject": str(r.get("subject") or "").strip(),
            "subject_type": str(r.get("subject_type") or "").strip(),
            "relation": str(r.get("relation") or "").strip(),
            "object": str(r.get("object") or "").strip(),
            "object_type": str(r.get("object_type") or "").strip(),
            "attributes": r.get("attributes") or {}
        })

    return {
        "entities": normalized_entities,
        "relations": normalized_relations
    }


# 
# 工具函数
# 
def sanitize_props(props: dict) -> dict:
    """
    清洗属性字典，仅保留可序列化且键名有效的键值对。

    Args:
        props: 原始属性字典

    Returns:
        清洗后的属性字典
    """
    out = {}
    for k, v in (props or {}).items():
        if v is None:
            continue
        key = str(k).strip()
        if not key:
            continue
        if isinstance(v, (str, int, float, bool)):
            out[key] = v
        else:
            out[key] = str(v)
    return out


def safe_label(s: str) -> str:
    """
    将任意字符串转换为可用于 Neo4j 标签的安全名称。

    Args:
        s: 原始字符串

    Returns:
        安全的标签名称
    """
    value = "".join(c if c.isalnum() or c == "_" else "_" for c in str(s))
    return value or "Entity"


def canonical_or_name(entity: dict) -> str:
    """
    优先返回 canonical_name，不存在时回退为 name。

    Args:
        entity: 实体字典

    Returns:
        规范化名称或实体名称
    """
    canonical_name = str(entity.get("canonical_name") or "").strip()
    name = str(entity.get("name") or "").strip()
    return canonical_name or name


# 
# 构建去重实体映射
# key = (canonical_or_name, type)
# 
def build_entity_map(entities: List[dict]) -> Dict[Tuple[str, str], dict]:
    """
    基于 (canonical_or_name, type) 构建实体映射并合并属性去重。

    Args:
        entities: 实体列表

    Returns:
        实体映射字典，键为 (name, type) 元组
    """
    entity_map: Dict[Tuple[str, str], dict] = {}

    for entity in entities:
        name = str(entity.get("name") or "").strip()
        entity_type = str(entity.get("type") or "").strip()
        if not name or not entity_type:
            continue

        canonical_name = str(entity.get("canonical_name") or "").strip() or None
        key_name = canonical_name or name
        key = (key_name, entity_type)

        if key not in entity_map:
            entity_map[key] = {
                "name": name,
                "type": entity_type,
                "canonical_name": canonical_name,
                "attributes": sanitize_props(entity.get("attributes") or {})
            }
        else:
            old_attrs = entity_map[key]["attributes"]
            new_attrs = sanitize_props(entity.get("attributes") or {})
            old_attrs.update({k: v for k, v in new_attrs.items() if k not in old_attrs})
            if not entity_map[key].get("canonical_name") and canonical_name:
                entity_map[key]["canonical_name"] = canonical_name

    return entity_map


# 
# 构建关系列表（去重）
# 
def build_relation_list(relations: List[dict]) -> List[dict]:
    """
    基于主客体、关系类型与属性对关系进行去重并标准化。

    Args:
        relations: 关系列表

    Returns:
        去重后的关系列表
    """
    relation_seen = set()
    relation_list = []

    for relation in relations:
        subject = str(relation.get("subject") or "").strip()
        subject_type = str(relation.get("subject_type") or "").strip()
        relation_type = str(relation.get("relation") or "").strip()
        obj = str(relation.get("object") or "").strip()
        object_type = str(relation.get("object_type") or "").strip()
        attributes = sanitize_props(relation.get("attributes") or {})

        if not all([subject, subject_type, relation_type, obj, object_type]):
            continue

        record_key = (
            subject,
            subject_type,
            relation_type,
            obj,
            object_type,
            tuple(sorted(attributes.items()))
        )
        if record_key in relation_seen:
            continue

        relation_seen.add(record_key)
        relation_list.append({
            "subject": subject,
            "subject_type": subject_type,
            "relation": relation_type,
            "object": obj,
            "object_type": object_type,
            "attributes": attributes
        })

    return relation_list


# 
# 合并抽取结果
# 
def merge_extraction_results(base_result: Dict[str, List[dict]], delta_result: Dict[str, List[dict]]) -> Dict[str, List[dict]]:
    """
    合并主抽取与补抽结果，并通过现有去重逻辑统一输出。

    Args:
        base_result: 基础抽取结果
        delta_result: 补抽结果

    Returns:
        合并后的抽取结果
    """
    combined_entities = (base_result.get("entities") or []) + (delta_result.get("entities") or [])
    combined_relations = (base_result.get("relations") or []) + (delta_result.get("relations") or [])

    entity_map = build_entity_map(combined_entities)
    relation_list = build_relation_list(combined_relations)

    return {
        "entities": list(entity_map.values()),
        "relations": relation_list
    }


# 
# 构建 Neo4j 节点查询
# 
def build_node_queries(entity_map: Dict[Tuple[str, str], dict]) -> List[Tuple[str, dict]]:
    """
    将实体映射按标签分组，生成批量写入 Neo4j 的节点查询语句。

    Args:
        entity_map: 实体映射字典

    Returns:
        Cypher 查询语句和参数元组列表
    """
    by_label: Dict[str, List[dict]] = {}

    for (_name, _type), record in entity_map.items():
        row = {
            "name": record["name"],
            "canonical_name": record.get("canonical_name"),
            "props": sanitize_props(record.get("attributes") or {})
        }
        by_label.setdefault(record["type"], []).append(row)

    queries: List[Tuple[str, dict]] = []
    for label, rows in by_label.items():
        safe = safe_label(label)
        cypher = f"""
        UNWIND $rows AS row
        MERGE (n:{safe} {{name: row.name}})
        SET n += row.props
        SET n.canonical_name = CASE
            WHEN row.canonical_name IS NULL OR row.canonical_name = "" THEN n.canonical_name
            ELSE row.canonical_name
        END
        SET n.entity_type = "{safe}"
        """
        queries.append((cypher.strip(), {"rows": rows}))

    return queries


# 
# 构建 Neo4j 关系查询
# 
def build_relation_queries(relation_list: List[dict]) -> List[Tuple[str, dict]]:
    """
    将关系列表按三元组分组，生成批量写入 Neo4j 的关系查询语句。

    Args:
        relation_list: 关系列表

    Returns:
        Cypher 查询语句和参数元组列表
    """
    by_triple: Dict[Tuple[str, str, str], List[dict]] = {}

    for relation in relation_list:
        key = (relation["subject_type"], relation["relation"], relation["object_type"])
        by_triple.setdefault(key, []).append({
            "subj": relation["subject"],
            "obj": relation["object"],
            "props": sanitize_props(relation.get("attributes") or {})
        })

    queries: List[Tuple[str, dict]] = []
    for (subject_type, relation_type, object_type), rows in by_triple.items():
        source_label = safe_label(subject_type)
        rel_label = safe_label(relation_type) or "REL"
        target_label = safe_label(object_type)

        cypher = f"""
        UNWIND $rows AS row
        MATCH (a:{source_label} {{name: row.subj}})
        MATCH (b:{target_label} {{name: row.obj}})
        MERGE (a)-[r:{rel_label}]->(b)
        SET r += row.props
        """
        queries.append((cypher.strip(), {"rows": rows}))

    return queries


# 
# 文本 -> 图谱抽取 -> Neo4j 查询
# 
def extract_text_to_graph_queries(
    text: str,
    max_chars: int = 12000,
    stage_callback: Optional[Callable[..., None]] = None,
    llm_bundle: Optional[Dict[str, Any]] = None,
) -> dict:
    """
    从文本抽取图谱结果并构建可直接执行的 Neo4j 节点与关系查询。

    Args:
        text: 输入文本
        max_chars: 最大字符数限制，默认 12000

    Returns:
        包含抽取结果、实体映射、关系列表和查询语句的字典
    """
    with log_duration("kg.extract_text_to_graph_queries", text_len=len(text or ""), max_chars=max_chars):
        clean_text = (text or "").strip()
        if not clean_text:
            return {
                "extraction": {"entities": [], "relations": []},
                "entity_map": {},
                "relation_list": [],
                "node_queries": [],
                "relation_queries": [],
                "stats": {"entities": 0, "relations": 0}
            }

        limited_text = clean_text[:max_chars]
        extraction = normalize_extraction_result(
            extract_hypertension_knowledge(
                limited_text,
                stage_callback=stage_callback,
                llm_bundle=llm_bundle,
            )
        )
        _emit_stage_event(stage_callback, "enter", "build_neo4j_queries", "正在构建 Neo4j 节点与关系查询")
        entity_map = build_entity_map(extraction["entities"])
        relation_list = build_relation_list(extraction["relations"])
        node_queries = build_node_queries(entity_map)
        relation_queries = build_relation_queries(relation_list)
        _emit_stage_event(stage_callback, "complete", "build_neo4j_queries", "Neo4j 查询构建完成")

        logger.info(
            f"[PERF] kg.extract_text_to_graph_queries entities={len(entity_map)} relations={len(relation_list)}"
        )

        return {
            "extraction": extraction,
            "entity_map": entity_map,
            "relation_list": relation_list,
            "node_queries": node_queries,
            "relation_queries": relation_queries,
            "stats": {
                "entities": len(entity_map),
                "relations": len(relation_list)
            }
        }


# 
# 示例运行
# 
if __name__ == "__main__":
    demo_cases = [
        {
            "name": "复杂规则与来源证据",
            "text": '''
            《中国高血压防治指南》指出，对于合并糖尿病且尿白蛋白升高的高血压患者，
            优先考虑 ACEI 或 ARB。高血压患者常见症状包括头晕、头痛，长期控制不佳可并发脑卒中、
            慢性肾病。常用检查包括动态血压监测、肾功能检查。氨氯地平属于 CCB，可能引起踝部水肿。
            家庭血压 ≥ 135/85 mmHg 可作为高血压诊断阈值之一。文献 PMID:12345678 支持上述推荐。
            '''
        },
        {
            "name": "分级分层与启动治疗阈值",
            "text": '''
            1级高血压定义为收缩压 140-159 mmHg 和或舒张压 90-99 mmHg。
            对于心血管高危患者，当诊室血压持续 ≥140/90 mmHg 时建议启动药物治疗。
            若为中低危患者，可先进行生活方式干预并随访评估。
            '''
        },
        {
            "name": "测量方式与场景阈值",
            "text": '''
            ABPM 可用于识别夜间高血压和隐蔽性高血压。
            24小时动态血压平均值 ≥130/80 mmHg 可提示高血压，
            日间平均血压 ≥135/85 mmHg、夜间平均血压 ≥120/70 mmHg 也具有诊断意义。
            '''
        },
        {
            "name": "药物类别与不良反应",
            "text": '''
            ACEI 和 ARB 常用于高血压合并慢性肾病患者。
            ACEI 可能导致干咳，CCB 可能导致踝部水肿，
            噻嗪类利尿剂可用于联合降压治疗方案。
            '''
        },
        {
            "name": "仅概念列举（应尽量返回空结构）",
            "text": '''
            高血压、糖尿病、脑卒中、慢性肾病、ACEI、ARB、CCB、ABPM、HBPM。
            '''
        }
    ]

    for idx, case in enumerate(demo_cases, start=1):
        print(f"\n===== Demo {idx}: {case['name']} =====")
        result = extract_hypertension_knowledge(case["text"])
        normalized = normalize_extraction_result(result)
        print(
            f"entities={len(normalized['entities'])}, relations={len(normalized['relations'])}"
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))

