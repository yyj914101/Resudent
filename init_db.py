"""Database initialization and seed data."""
import os, sys
from datetime import datetime, date

# 085 R-085-03：value≠label 结构化种子（label-only 分类不进 DICT_SEEDS）。
# 与 scripts/migrations/m039_venue_facility.py 的 VENUE_DICT_SEEDS 逐值一致
#（tests/test_085_venue_facility.py 断言两处同值；080 闭集登记于 STRUCTURED_SEED_EXCEPTIONS）。
STRUCTURED_DICT_SEEDS = {
    # 085 缴费项目：value≠label（clean_fee 编码定稿于一页纸 v1.1），用 (value, label) 元组；
    #   label-only 分类勿用元组形态。存量库由 m039 幂等补录，两路同值。
    '缴费项目': [('clean_fee', '清洁费（街道门店）')],
}

def init_db(app):
    from models import db, User, Role, DictCategory, DictItem

    with app.app_context():
        db.create_all()

        # Skip if already initialized
        if Role.query.count() > 0:
            return

        # === Roles ===
        roles = [
            Role(id=1, name='超级系统管理员', description='拥有全部权限', is_system=True),
            Role(id=2, name='业务经办人', description='文书/网格员', is_system=True),
            Role(id=3, name='村级负责人', description='村负责人', is_system=True),
            Role(id=4, name='办事员', description='仅查询', is_system=True),
        ]
        perms_super = {
            # 046：首装权限真源在 app.py 的角色种子；本脚本当前无调用方，仅作历史初始化脚本保留
            'resident.view': True, 'resident.add': True, 'resident.edit': True, 'resident.delete': True,
            'resident.export': True, 'dibao.view': True, 'dibao.add': True, 'dibao.edit': True,
            'disabled.view': True, 'disabled.add': True, 'disabled.edit': True,
            'care.view': True, 'care.add': True, 'care.edit': True,
            'org.view': True, 'org.add': True, 'org.edit': True, 'org.delete': True,
            'party.view': True, 'party.add': True, 'party.edit': True, 'party.delete': True,
            'subsidy.view': True, 'subsidy.add': True, 'subsidy.edit': True, 'subsidy.delete': True,
            'welfare.view': True, 'welfare.add': True, 'welfare.edit': True, 'welfare.delete': True,
            'mediation.view': True, 'mediation.add': True, 'mediation.edit': True, 'mediation.delete': True,
            'todo.view': True, 'todo.add': True, 'todo.edit': True, 'todo.delete': True,
            'certificate.view': True, 'certificate.add': True, 'certificate.edit': True,
            'certificate.delete': True, 'certificate.export': True,
            # 045：补贴公示（业务经办人/村级负责人无 delete 位，对齐 certificate 口径）
            'notice.view': True, 'notice.add': True, 'notice.edit': True,
            'notice.delete': True, 'notice.export': True,
            # 店铺信息（居民管理组）：手动录入 + Excel 批量导入/导出
            'shop.view': True, 'shop.add': True, 'shop.edit': True,
            'shop.delete': True, 'shop.export': True,
            'settings.edit': True, 'dict.manage': True, 'user.manage': True,
            'backup.manage': True, 'log.view': True, 'dashboard.view': True,
            # T18-1：走访计划模块权限位（Sprint5）
            'visit.view': True, 'visit.add': True, 'visit.edit': True, 'visit.delete': True,
            # T19：到期预警模块权限位（Sprint5，warn 仅 view/edit 有对应端点）
            'warn.view': True, 'warn.edit': True,
            # T15-4：死亡名单对账模块权限位（Sprint6，reconcile 无 delete 端点）
            'reconcile.view': True, 'reconcile.add': True, 'reconcile.edit': True,
            'reconcile.export': True,
        }
        roles[0].permissions = json.dumps(perms_super)

        perms_clerk = perms_super.copy()
        for k in ['resident.delete','org.delete','party.delete',
                  'subsidy.delete','welfare.delete','mediation.delete',
                  'todo.delete','certificate.delete','notice.delete','shop.delete',
                  'settings.edit','dict.manage','user.manage','backup.manage']:
            perms_clerk[k] = False
        roles[1].permissions = json.dumps(perms_clerk)

        perms_leader = perms_clerk.copy()
        perms_leader['settings.edit'] = True
        perms_leader['dict.manage'] = True
        perms_leader['backup.manage'] = True
        for k in ['resident.delete','org.delete','party.delete',
                  'subsidy.delete','welfare.delete','mediation.delete',
                  'todo.delete','certificate.delete','notice.delete','shop.delete']:
            perms_leader[k] = False
        roles[2].permissions = json.dumps(perms_leader)

        perms_viewer = {}
        for k,v in perms_super.items():
            if k.endswith('.view') or k.endswith('.export') or k in ['dashboard.view','log.view']:
                perms_viewer[k] = v
        perms_viewer['resident.export'] = False
        roles[3].permissions = json.dumps(perms_viewer)

        db.session.add_all(roles)

        # === Default admin ===
        admin = User(username='admin', real_name='超级管理员', role_id=1, is_active=True)
        admin.set_password('admin123')
        db.session.add(admin)

        # === Dictionary Data ===
        dict_data = {
            '所在组': ['思榜','旧寨'],
            '与户主关系': ['户主本人','配偶','子女','父母','儿媳','女婿','孙子','孙女','外孙子','外孙女','曾孙子','曾孙女','兄弟姐妹','岳父母','公婆','祖父','祖母','外祖父','外祖母','其他亲属','非亲属'],
            '政治面貌': ['中共党员','中共预备党员','共青团员','群众','民主党派','无党派人士'],
            '学历': ['研究生','本科','大专','高中/中专','初中','小学','文盲'],
            '健康状态': ['健康','一般','长期慢性病','重大疾病','残疾'],
            '户属性': ['一般农户','脱贫户','监测户','五保户'],
            # T11-3：政策性居民标签（resident_tag.tag_code 来源）；T16-2 补「特困供养」（special_support 回填，存量库经 utils/tag_backfill.ensure_tag_dict 幂等补录）
            '居民标签': ['低保','残疾','困难老人','留守儿童','困境儿童','特困供养','独居老人','高龄老人','脱贫户','监测户','五保户'],
            '户口类型': ['农业户口','非农业户口','统一居民户口'],
            '残疾类型': ['视力','听力','言语','肢体','智力','精神','多重'],
            '残疾等级': ['一级','二级','三级','四级'],
            '低保类型': ['A类','B类','C类'],
            '岗位类型（公益岗）': ['护林员','保洁员','护路员','巡河员','其他'],
            '纠纷类型': ['邻里纠纷','土地纠纷','婚姻家庭纠纷','债务纠纷','其他'],
            '风险等级': ['低风险','中风险','高风险'],
            '婚姻状况': ['未婚','已婚','离异','丧偶'],
            '人员状态': ['正常','死亡','迁出'],
            '务工状态': ['本地务工','外出务工','返乡','无务工'],
            '民族': ['汉族','蒙古族','回族','藏族','维吾尔族','苗族','彝族','壮族','布依族','朝鲜族','满族','侗族','瑶族','白族','土家族','哈尼族','哈萨克族','傣族','黎族','傈僳族','佤族','畲族','高山族','拉祜族','水族','东乡族','纳西族','景颇族','柯尔克孜族','土族','达斡尔族','仫佬族','羌族','布朗族','撒拉族','毛南族','仡佬族','锡伯族','阿昌族','普米族','塔吉克族','怒族','乌孜别克族','俄罗斯族','鄂温克族','德昂族','保安族','裕固族','京族','塔塔尔族','独龙族','鄂伦春族','赫哲族','门巴族','珞巴族','基诺族'],
            '党支部': ['中国共产党融水苗族自治县融水镇三合村小源屯支部委员会','中国共产党融水苗族自治县融水镇三合村思榜屯支部委员会','中国共产党融水苗族自治县融水镇三合村黎邓屯支部委员会','中国共产党融水苗族自治县融水镇三合村古营屯支部委员会','中国共产党融水苗族自治县融水镇三合村黄洞屯支部委员会','中国共产党融水苗族自治县融水镇三合村旧寨屯支部委员会'],
            '开户行': ['中国工商银行','中国农业银行','中国银行','中国建设银行','交通银行','中国邮政储蓄银行','招商银行','中信银行','中国光大银行','华夏银行','中国民生银行','广发银行','兴业银行','平安银行','上海浦东发展银行','广西农村商业银行','农村信用合作社','北部湾银行','桂林银行','柳州银行'],
            '原服役兵种': ['陆军','海军','空军','火箭军','战略支援部队','联勤保障部队','武警部队','其他'],
            '户籍性质': ['农业户口','非农业户口'],
            '安置情况': ['自主就业','安排工作','退休','供养','其他'],
            '组织职务': ['书记','副书记','委员','主任','副主任','主席','副主席','连长','指导员','其他'],
            # GLB-44：特困供养表单下拉（存量库由 utils/dict_seed.ensure_care_support_dict 幂等补录）
            '供养方式': ['集中供养','分散供养','亲属供养','其他'],
            '供养机构': ['镇敬老院','县福利院','村级照料中心','其他'],
        }
        # 085 R-085-03：结构化种子（value≠label）统一从模块常量注入。
        # 085 常量按 (value, label) 存，此处归一为 (label, value)，与 DICT_SEEDS 的 seed_pair 口径一致。
        dict_data.update({k: [((item[1], item[0]) if isinstance(item, tuple) else item)
                             for item in v] for k, v in STRUCTURED_DICT_SEEDS.items()})
        # 080 R-1：与 utils/dict_seed.DICT_SEEDS 重复的分类以种子真源为准，避免字面量三源并存
        from utils.dict_seed import DICT_SEEDS, seed_pair
        dict_data.update({k: list(v) for k, v in DICT_SEEDS.items()})

        sort = 0
        for cat_name, items in dict_data.items():
            sort += 10
            cat = DictCategory(name=cat_name, description=cat_name, sort_order=sort)
            db.session.add(cat)
            db.session.flush()
            for idx, item in enumerate(items):
                # 090A / 085：种子条目统一按 (label, value) 展开（字符串 → (值, 值)）；
                # 085 的 (value, label) 常量已在上方注入时归一。
                label, value = seed_pair(item)
                di = DictItem(category_id=cat.id, label=label, value=value, sort_order=idx)
                db.session.add(di)

        # === Care type entries (for dictionary too) ===
        care_cat = DictCategory(name='关爱对象分类', description='关爱对象分类标签', sort_order=200)
        db.session.add(care_cat)
        db.session.flush()
        for idx, label in enumerate(['留守儿童','独居老人','高龄老人']):
            di = DictItem(category_id=care_cat.id, label=label, value=label, sort_order=idx)
            db.session.add(di)

        # === Certificate templates ===
        from models.certificate import CertificateTemplate
        templates = [
            CertificateTemplate(name='常住证明（默认）', cert_type='residence',
                template_content='''兹证明 {name}（身份证号：{id_card}），现居住于 {address}，特此证明。\n\n{date}'''),
            CertificateTemplate(name='亲属关系证明（默认）', cert_type='kinship',
                template_content='''兹证明 {name_a}（身份证号：{id_card_a}）与 {name_b}（身份证号：{id_card_b}）系 {relation} 关系。\n\n{date}'''),
            CertificateTemplate(name='死亡证明（默认）', cert_type='death',
                template_content='''兹证明 {name}（身份证号：{id_card}），于 {death_date} 因 {death_reason} 去世。\n\n{date}'''),
        ]
        db.session.add_all(templates)

        db.session.commit()
        print('[OK] 数据库初始化完成，默认账号: admin / admin123')

import json
