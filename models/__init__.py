from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

from .resident import Resident
from .user import User, Role
from .dict_model import DictCategory, DictItem
from .dibao import Dibao
from .disabled import Disabled
# 093 B-6：残疾人服务事项 1:N 服务记录（m027 建表，create_all 新库直建）
from .disabled_service_record import DisabledServiceRecord
from .care_target import CareTarget, SpecialSupport, UnsupportedChild
# 093 B-7：老年人服务事项 1:N 服务记录（m027 建表，create_all 新库直建）
from .elder_service_record import ElderServiceRecord
from .organization import OrganizationMember
from .party import PartyMember, PartyFee, PartyTransfer, PartyPosition
from .welfare_job import WelfareJob
from .mediation import Mediation
from .todo import TodoItem
from .certificate import CertificateIssue, CertificateTemplate
from .log import OperationLog
from .death_report import DeathReport
from .veteran import Veteran
# 优抚对象台账（2026-11-16：民政对象第 6 类；m043 建表，新库 create_all 直建）
from .youshu import Youshu

from .medical_insurance import MedicalInsurance
from .task import AsyncTask
# T08 数据底座：18 新表（subsidy_project/record、标签、走访、预警、对账、级联、导入六表、治理、迁移留痕）
from .subsidy_project import SubsidyProject, SubsidyRecord, SubsidyBatchImport, SubsidyBatchError
from .resident_tag import ResidentTag
from .visit import VisitPlan, VisitRecord
from .warn_record import WarnRecord
from .reconcile import ReconcileTask, ReconcileResult
from .cascade import CascadeChecklist, CascadeNotice
from .import_engine import (ImportBatch, ImportFieldMapping, ImportRowError,
                            ImportFieldRegistry, ImportProfile)
from .schema_governance import SchemaChangeRequest, DataModelVersion
from .migration import MigrationRecord, MigrationLog, MigrationOrphanLog
from .resident_sync_log import ResidentSyncLog
from .safety import SafetyNode
# 087 B-10：综合安全检查结构化闭环（检查单 1:N 隐患行；m028 建表，create_all 新库直建）
from .safety_check import SafetyCheck, SafetyCheckHazard
from .notice import NoticeRecord, NoticeTemplate
from .shop import Shop
from .device_registry import DeviceRegistry
# 078 软表引擎：元数据三表（业务台账零物理 DDL，ADR-025）
from .dynamic_table import DynamicTable, DynamicField, DynamicRecord
# 107 软表批量导入：批次总控 + 失败行暂存（模式 A，m045 建表）
from .dynamic_table import SofttableBatchImport, SofttableBatchError
# 084 就业服务：就业状态登记 + 灵活就业补贴申报（m037 建表，create_all 新库直建）
from .welfare_employment import WelfareEmployment
from .welfare_flex_subsidy import WelfareFlexSubsidy
# 086 信访维稳：受理主表 + 办理记录子表（m033 建表，create_all 新库直建）
from .governance_petition import GovernancePetition, GovernancePetitionHandle
# 092 模板附件区：通用模块级附件表（m036 建表，create_all 新库直建；不复用 SafetyNode）
from .module_attachment import ModuleAttachment
# 083A 党务活动：活动主表 + 民主评议逐人子表（m032 建表，create_all 新库直建）
from .party_activity import PartyActivity, PartyActivityReview
# 083B 党员发展：五阶段名册主表 + 阶段迁移日志子表（m041 建表，create_all 新库直建）
from .party_development import PartyDevelopment, PartyDevelopmentLog
# 090B 公共事务·选举：届次 + 选民名册 + 结果三表（m035 建表，create_all 新库直建）
from .election import (ElectionRound, ElectionVoter, ElectionResult,
                       ElectionBatchImport, ElectionBatchError)
# 090A 民生诉求：受理主表 + 处理记录子表（m034 建表，create_all 新库直建）
from .demand import Demand, DemandHandle
# 089 文明实践站：居民公约版本固定表（m040 建表，create_all 新库直建；
# 活动记录走软表 civilization_activity，无固定模型）
from .civilization import CivConvention
# 第三批：通用域异步批导入（医保/民政等共用，m047 建表）
from .domain_batch import DomainBatchImport, DomainBatchError
