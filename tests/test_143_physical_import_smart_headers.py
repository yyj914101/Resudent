# -*- coding: utf-8 -*-
"""tests/test_143_physical_import_smart_headers.py — Patch C-3 物理表智能跳过大标题与表头别名自动匹配验收。"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_detect_header_skips_banner_and_preserves_compact_tables():
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        'sr', os.path.join(root, 'utils', 'spreadsheet_reader.py'))
    sr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sr)

    # 1) 单行大标题 + 正式表头
    rows1 = [
        ['2026年低保对象统计表', None, None, None],
        ['序号', '成员姓名', '身份证号', '月保障金额'],
        ['1', '张三', '110101199001011234', '650'],
    ]
    assert sr._detect_header_row_index(rows1) == 1

    # 2) 大标题 + 填报说明双栏元数据行 + 正式表头
    rows2 = [
        ['2026年退役军人信息汇总表', None, None, None, None],
        ['填报单位：示范村', None, None, '填报日期：2026-11-01', None],
        ['序号', '姓名', '身份证号', '联系电话', '家庭住址'],
        ['1', '李四', '110101198802021234', '13800001111', '一组'],
    ]
    assert sr._detect_header_row_index(rows2) == 2

    # 3) 正常 2 列表格不得误跳过首行
    rows_2col = [['姓名', '金额'], ['张三', '100'], ['李四', '200']]
    assert sr._detect_header_row_index(rows_2col) == 0

    # 4) 正常 1 列表格不得误跳过首行
    rows_1col = [['名称'], ['项目A'], ['项目B']]
    assert sr._detect_header_row_index(rows_1col) == 0

    # 5) CSV 端到端跳过大标题 + 清理换行
    csv_bytes = (
        '2026年低保花名册,,,\n'
        '序号,姓名,证件号码,手机号\n'
        '1,张三,110101199001011234,13800001111\n'
    ).encode('utf-8')
    headers, data_rows = sr.read_spreadsheet_table('dibao.csv', io.BytesIO(csv_bytes))
    assert headers == ['序号', '姓名', '证件号码', '手机号']
    assert len(data_rows) == 1 and data_rows[0][1] == '张三'


def test_header_aliases_across_physical_domains():
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        'ha', os.path.join(root, 'utils', 'header_aliases.py'))
    ha = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ha)

    # 低保：姓名 -> 成员姓名，证件号码 -> 身份证号，且不与户主姓名冲突
    col_dibao = ha.build_header_index(['户主', '姓名*', '证件号码（必填）', '手机号', '金额'], domain='dibao')
    assert col_dibao['户主姓名'] == 0
    assert col_dibao['成员姓名'] == 1
    assert col_dibao['身份证号'] == 2
    assert col_dibao['联系电话'] == 3
    assert col_dibao['月保障金额'] == 4

    # 党员：导出「手机号」回导匹配模板「手机号码」，党支部 -> 所在支部
    col_party = ha.build_header_index(['党员姓名', '身份证\n号码', '手机号', '党支部'], domain='party')
    assert col_party['姓名'] == 0
    assert col_party['身份证号'] == 1
    assert col_party['手机号码'] == 2
    assert col_party['所在支部'] == 3

    # 退役军人：身份证号 -> 身份证号码，手机号 -> 本人联系方式，住址 -> 家庭详细地址
    col_vet = ha.build_header_index(['姓名', '身份证号', '手机号', '住址', '学历'], domain='veteran')
    assert col_vet['身份证号码'] == 1
    assert col_vet['本人联系方式'] == 2
    assert col_vet['家庭详细地址'] == 3
    assert col_vet['文化程度'] == 4

    # 困境儿童 & 店铺
    col_child = ha.build_header_index(['姓名', '身份证号', '监护人'], domain='care_child')
    assert col_child['儿童姓名'] == 0
    assert col_child['身份证号码'] == 1
    assert col_child['监护人姓名'] == 2

    col_shop = ha.build_header_index(['商户名称', '经营者', '电话', '地址'], domain='shop')
    assert col_shop['店铺名称'] == 0
    assert col_shop['负责人'] == 1
    assert col_shop['联系电话'] == 2
    assert col_shop['经营地址'] == 3

    # 异步 domain_batch 行级字典归一化
    norm = ha.normalize_domain_row({'姓名*': '王五', '证件号码': '110101199001011234', '手机号': '13900001111'}, domain='dibao')
    assert norm['成员姓名'] == '王五'
    assert norm['身份证号'] == '110101199001011234'
    assert norm['联系电话'] == '13900001111'
