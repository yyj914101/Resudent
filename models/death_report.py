from . import db
from datetime import datetime

class DeathReport(db.Model):
    __tablename__ = 'death_reports'
    id = db.Column(db.Integer, primary_key=True)
    # 基本信息
    village = db.Column(db.String(50), default='')           # 所属村
    village_group = db.Column(db.String(50), default='')      # 所在组
    deceased_name = db.Column(db.String(50), nullable=False, index=True)  # 死者姓名
    address = db.Column(db.String(200), default='')           # 家庭住址
    gender = db.Column(db.String(10), default='')             # 性别
    age = db.Column(db.Integer, default=0)                    # 年龄
    nation = db.Column(db.String(20), default='')             # 民族
    id_card = db.Column(db.String(18), index=True)          # 身份证号码（R12 加密后原列置 NULL，去除 default='' 避免覆盖）
    id_card_enc = db.Column(db.Text)                            # R12 字段级加密（密文）
    id_card_hash = db.Column(db.String(64))                     # SHA-256 等值检索
    death_date = db.Column(db.Date, nullable=False)           # 死亡时间
    person_type = db.Column(db.String(50), default='')        # 对象类型（低保户、五保户、一般农户等）
    remark = db.Column(db.Text, default='')                   # 备注
    # 填报信息
    report_time = db.Column(db.DateTime, default=datetime.now) # 填报时间
    report_unit = db.Column(db.String(100), default='')       # 填报单位
    reporter = db.Column(db.String(50), default='')           # 填报人

    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    # R5（m002）：级联触发
    cascade_status = db.Column(db.String(10), nullable=False, default='pending')  # pending/done/failed
    confirm_by = db.Column(db.Integer)
    confirmed_at = db.Column(db.DateTime)

    __table_args__ = (
        db.Index('idx_death_report_name', 'deceased_name'),
        db.Index('idx_death_report_date', 'death_date'),
    )


def _death_report_id_hash(dr):
    """死亡登记身份证哈希：优先加密列 id_card_hash，回退明文列（迁移过渡态兼容）。

    死亡登记 id_card 接入字段级加密后原列置 NULL，级联/撤回须以哈希列匹配居民。
    """
    if getattr(dr, 'id_card_hash', None):
        return dr.id_card_hash
    raw = getattr(dr, 'id_card', None)
    if raw:
        from utils.crypto import sha256_hash
        return sha256_hash(str(raw).strip().upper())
    return None


class CascadeService:
    """R5 死亡级联（T15-1，owner=models/）。静态方法类，禁新建 services/。

    死亡登记确认后单事务级联 6 项（ISS-04，全部软失效禁物理删除）：
      ① residents.status→deceased  ② subsidy_record 在册→停发
      ③ dibao/disabled/care_target/special_support 软失效  ④ resident_tag 失效
      ⑤ 一老一小台账标记失效（care_target，③已覆盖）  ⑥ 未处置预警关闭
    复用既有唯一入口（防双写漂移）：utils/death_soft_invalidate.soft_invalidate_vulnerable（③④⑤）、
    utils/warn_service.close_warnings_for_death（⑥）。
    事务纪律：不自行 commit；由调用方（路由）统一 commit/except rollback（对齐 resident.api_status 范本）。
    幂等：cascade_status pending/failed 可执行；done 拒绝（重复确认）；软失效/预警函数本身幂等。
    """

    @staticmethod
    def run_cascade(dr, operator_id=None):
        """执行单事务 6 项级联。入参 DeathReport 实例；返回各表影响计数 dict。
        任一步骤失败抛异常，由调用方整体回滚（R5-E4）。
        """
        if dr.cascade_status == 'done':
            raise ValueError('该死亡登记已确认，请勿重复操作')

        from models.resident import Resident

        id_hash = _death_report_id_hash(dr)
        if not id_hash:
            raise ValueError('死亡登记缺少身份证号，无法匹配居民档案')

        # ① 反查居民档案（id_card_hash 等值检索，对齐 T16-2 正向口径）
        resident = Resident.query.filter_by(id_card_hash=id_hash).first()
        if not resident:
            raise ValueError('未找到匹配的居民档案，请先登记居民')

        # ② residents.status→deceased（语义同 routes/resident.py api_status 死亡分支）
        resident.status = 'deceased'
        resident.status_date = dr.death_date
        resident.status_reason = '死亡登记确认'

        # ③ subsidy_record 在册→停发（D2 状态迁移，业务字段只增不改；已停发不动，幂等）
        from models.subsidy_project import SubsidyRecord
        now = datetime.now()
        stop_note = '死亡停发(%s)' % now.strftime('%Y-%m-%d')
        subsidies = SubsidyRecord.query.filter_by(resident_id=resident.id, status='正常').all()
        for s in subsidies:
            s.status = '停发'
            s.note = (s.note + ';' if s.note else '') + stop_note

        # ④⑤⑥ 弱势群体四表 + resident_tag 软失效（③④⑤，一次调用，幂等）
        from utils.death_soft_invalidate import soft_invalidate_vulnerable
        soft_result = soft_invalidate_vulnerable(resident.id, reason='死亡注销')
        # ⑦ 未处置预警关闭（⑥，复用 T19-4 唯一入口，同事务）
        from utils.warn_service import close_warnings_for_death
        close_warnings_for_death(resident.id)
        # ⑧ 防误发清单 + 停发告知单（T15-3，同事务自动生成，R5 B1/D4）
        docs = CascadeService._build_cascade_docs(
            dr, resident, subsidies, soft_result, now, operator_id)

        # 级联完成标记（全部成功才落，失败由调用方回滚保持 pending）
        dr.cascade_status = 'done'
        dr.confirm_by = operator_id
        dr.confirmed_at = now

        return {
            'residents': 1,
            'subsidies': len(subsidies),
            'dibao': soft_result.get('dibao', 0),
            'disabled': soft_result.get('disabled', 0),
            'care_target': soft_result.get('care_target', 0),
            'special_support': soft_result.get('special_support', 0),
            'tags': soft_result.get('tags', 0),
            'checklist_id': docs['checklist_id'],
            'notice_id': docs['notice_id'],
        }

    @staticmethod
    def _build_cascade_docs(dr, resident, subsidies, soft_result, now, operator_id=None):
        """T15-3：同事务生成防误发清单（cascade_checklist）+ 停发告知单（cascade_notice）。

        - affected_items JSON：停发补贴项目（项目名/最近发放日期/金额/适用标签）
          + 台账影响计数（dibao/disabled/care_target/special_support）+ 受影响标签
        - notice：notice_no（TZ-YYYYMMDD-####）、姓名、身份证密文+hash（取自居民档案）、
          project_ids（被停发项目）、stop_from（确认当月 YYYY-MM）、reason='死亡'、operator
        - 不自行 commit；失败由调用方整体回滚（R5-E4）；幂等靠 cascade_status done 拒绝重复确认
        """
        import json

        from models.cascade import CascadeChecklist, CascadeNotice
        from models.subsidy_project import SubsidyProject
        from models.resident_tag import ResidentTag
        from models.dict_model import DictItem
        from utils.notice_no import generate_notice_no

        # ① 受影响补贴项目明细（项目名/最近发放日期/金额/适用标签）
        subsidies_detail = []
        project_ids = []
        for s in subsidies:
            proj = SubsidyProject.query.get(s.project_id) if s.project_id else None
            subsidies_detail.append({
                'project_id': s.project_id,
                'project_name': proj.name if proj else '未知项目',
                'issue_date': s.issue_date.strftime('%Y-%m-%d') if s.issue_date else '',
                'amount': str(s.amount) if s.amount is not None else '',
                'target_tags': proj.target_tags if proj else None,
            })
            project_ids.append(s.project_id)

        # ② 受影响标签明细（本次级联软失效的，tag_code → 字典 label 可读名）
        tags = ResidentTag.query.filter_by(
            resident_id=resident.id, status='失效', invalid_reason='死亡注销').all()
        tag_label_map = {}
        if tags:
            codes = [t.tag_code for t in tags]
            for item in DictItem.query.filter(DictItem.value.in_(codes)).all():
                tag_label_map.setdefault(item.value, item.label)
        tags_detail = [{'tag_code': t.tag_code,
                        'tag_name': tag_label_map.get(t.tag_code, t.tag_code)} for t in tags]

        affected = {
            'subsidies': subsidies_detail,
            'counts': {
                'dibao': soft_result.get('dibao', 0),
                'disabled': soft_result.get('disabled', 0),
                'care_target': soft_result.get('care_target', 0),
                'special_support': soft_result.get('special_support', 0),
            },
            'tags': tags_detail,
        }

        # ③ 防误发清单（可回溯）
        checklist = CascadeChecklist(
            death_report_id=dr.id,
            affected_items=json.dumps(affected, ensure_ascii=False),
            status='有效',
        )
        db.session.add(checklist)
        db.session.flush()  # 取 id

        # ④ 停发告知单（notice_no 当日唯一）
        notice = CascadeNotice(
            death_report_id=dr.id,
            notice_no=generate_notice_no(),
            resident_name=resident.name,
            id_card_enc=resident.id_card_enc,
            id_card_hash=resident.id_card_hash,
            project_ids=json.dumps(project_ids, ensure_ascii=False),
            stop_from=now.strftime('%Y-%m'),
            reason='死亡',
            operator=operator_id,
        )
        db.session.add(notice)
        db.session.flush()

        return {'checklist_id': checklist.id, 'notice_id': notice.id}

    @staticmethod
    def revoke_cascade(dr, operator_id=None):
        """R5/B2 死亡级联撤回（T15-2）：确认后 48h 内单事务回滚 6 表级联状态。

        只恢复本次级联影响的记录（恢复标记键，幂等）：
          resident：status_reason=='死亡登记确认'
          subsidy ：note 含 '死亡停发'
          四表/tag：invalid_reason=='死亡注销'
          warn    ：handle_result=='已注销停发'
        任一步骤失败抛异常，由调用方整体回滚（撤回不部分生效）。
        成功后 cascade_status 重置 pending + 清 confirm 字段（可重新确认，log_operation 留痕追溯）。
        """
        if dr.cascade_status != 'done':
            raise ValueError('仅已确认（done）的死亡登记可撤回')

        from models.resident import Resident

        id_hash = _death_report_id_hash(dr)
        if not id_hash:
            raise ValueError('死亡登记缺少身份证号，无法匹配居民档案')
        resident = Resident.query.filter_by(id_card_hash=id_hash).first()
        if not resident:
            raise ValueError('未找到匹配的居民档案')

        now = datetime.now()
        # ① resident 状态恢复
        if resident.status == 'deceased' and resident.status_reason == '死亡登记确认':
            resident.status = 'normal'
            resident.status_date = None
            resident.status_reason = None
            r_count = 1
        else:
            r_count = 0

        # ② subsidy_record 停发恢复（note 只增不改保审计）
        from models.subsidy_project import SubsidyRecord
        subsidies = [s for s in SubsidyRecord.query.filter_by(resident_id=resident.id).all()
                     if s.status == '停发' and s.note and '死亡停发' in s.note]
        for s in subsidies:
            s.status = '正常'
            s.note = (s.note + ';' if s.note else '') + '撤回恢复(%s)' % now.strftime('%Y-%m-%d')

        # ③④ 弱势群体四表 + resident_tag 软失效恢复（仅本次级联置失效的，reason=='死亡注销'）
        from models.dibao import Dibao
        from models.disabled import Disabled
        from models.care_target import CareTarget, SpecialSupport
        from models import ResidentTag

        def _revive(model, rows):
            n = 0
            for r in rows:
                if r.status == '失效' and r.invalid_reason == '死亡注销':
                    r.status = '有效'
                    r.invalid_reason = None
                    n += 1
            return n

        counts = {'dibao': 0, 'disabled': 0, 'care_target': 0, 'special_support': 0, 'tags': 0}
        counts['dibao'] = _revive(Dibao, Dibao.query.filter_by(resident_id=resident.id).all())
        counts['disabled'] = _revive(Disabled, Disabled.query.filter_by(resident_id=resident.id).all())
        counts['care_target'] = _revive(CareTarget, CareTarget.query.filter_by(resident_id=resident.id).all())
        # special_support：无 resident_id，按 id_card_hash 反查（对齐 soft_invalidate_vulnerable）
        if resident.id_card_hash:
            from utils.crypto import sha256_hash as _sha

            def _sp_hash(sp):
                # R12 过渡态兼容：优先哈希列，回退明文列
                if getattr(sp, 'household_id_card_hash', None):
                    return sp.household_id_card_hash
                raw = getattr(sp, 'household_id_card', None)
                return _sha(str(raw).strip().upper()) if raw else None

            sp_rows = [sp for sp in SpecialSupport.query.all()
                       if _sp_hash(sp) == resident.id_card_hash]
            counts['special_support'] = _revive(SpecialSupport, sp_rows)
        # tag：失效 + invalid_reason=='死亡注销' → 恢复（含 invalid_time 清空）
        tags = ResidentTag.query.filter_by(resident_id=resident.id).all()
        for t in tags:
            if t.status == '失效' and t.invalid_reason == '死亡注销':
                t.status = '有效'
                t.invalid_reason = None
                t.invalid_time = None
                counts['tags'] += 1

        # ⑤⑥ 预警关闭恢复（未处置 + todo 恢复 todo）
        from models.warn_record import WarnRecord
        from models.todo import TodoItem
        warns = [w for w in WarnRecord.query.filter_by(resident_id=resident.id).all()
                 if w.status == '已关闭' and w.handle_result == '已注销停发']
        for w in warns:
            w.status = '未处置'
            w.handle_result = None
            w.handled_at = None
            todo = TodoItem.query.filter_by(todo_type='warn', related_id=w.id, status='dismissed').first()
            if todo:
                todo.status = 'todo'
                todo.completed_at = None

        # ⑦ cascade_checklist 置作废（T15-3 生成后生效，当前预留）
        from models.cascade import CascadeChecklist
        checklists = CascadeChecklist.query.filter_by(death_report_id=dr.id, status='有效').all()
        for c in checklists:
            c.status = '作废'

        # 收尾：重置 pending（可重新确认），清 confirm 字段
        dr.cascade_status = 'pending'
        dr.confirm_by = None
        dr.confirmed_at = None

        return {
            'residents': r_count,
            'subsidies': len(subsidies),
            'dibao': counts['dibao'],
            'disabled': counts['disabled'],
            'care_target': counts['care_target'],
            'special_support': counts['special_support'],
            'tags': counts['tags'],
            'warns': len(warns),
            'checklists': len(checklists),
        }
