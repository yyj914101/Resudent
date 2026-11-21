"""
授权管理 - 路由
"""
from flask import Blueprint, render_template, request, session, jsonify, send_file
from utils.decorators import login_required
from utils.license import get_license_status, verify_license
import json, io, os
from datetime import datetime

license_bp = Blueprint('license', __name__, template_folder='../templates')


@license_bp.route('/')
@login_required
def index():
    """授权管理页面"""
    return render_template('settings/license.html')


@license_bp.route('/api/status')
@login_required
def api_status():
    """获取当前授权状态"""
    return jsonify(success=True, data=get_license_status())


@license_bp.route('/api/export', methods=['GET'])
@login_required
def api_export():
    """导出配置信息文件"""
    from utils.license import get_machine_code
    machine = get_machine_code()
    info = {
        'machine_code': machine,
        'system': '村务管理系统 v2.4',
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'note': '请将此文件发给作者以获取授权'
    }
    content = json.dumps(info, ensure_ascii=False, indent=2)
    buf = io.BytesIO(content.encode('utf-8'))
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name=f'machine_config_{machine}.json',
        mimetype='application/json'
    )


@license_bp.route('/api/import', methods=['POST'])
@login_required
def api_import():
    """导入授权文件"""
    if 'file' not in request.files:
        return jsonify(success=False, msg='请选择授权文件')

    file = request.files['file']
    if file.filename == '':
        return jsonify(success=False, msg='文件名为空')

    try:
        data = json.load(file)
    except Exception:
        return jsonify(success=False, msg='文件格式错误，请上传有效的 .key 或 .json 文件')

    ok, msg, info = verify_license(data)
    if not ok:
        return jsonify(success=False, msg=f'授权文件无效：{msg}')

    # 保存授权文件
    try:
        from config import LICENSE_FILE
        lic_path = LICENSE_FILE
    except ImportError:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        lic_path = os.path.normpath(os.path.join(base, 'license.key'))
    with open(lic_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return jsonify(success=True, msg='授权成功！已切换为局域网模式', data=info)


