"""
Platner Rotas — Servidor principal (Flask)

• Serve o HTML do frontend com CSS premium injetado
• HTML sempre abre LIMPO — clientes importam seu próprio XLSX
• Verifica licença via trial_guard.py antes de liberar acesso
• Implementa a API do Google Apps Script localmente (/api/sheets)
  → o botão "Conectar / Sync" do HTML funciona sem configurar nada no Google
"""
import os, re, json, webbrowser, threading, datetime
import pandas as pd
from flask import Flask, request, jsonify, Response, send_from_directory

app = Flask(__name__, static_folder='static')

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
HTML_FILE = os.path.join(BASE_DIR, 'rotas_mapa_v14 (5).html')
DATA_FILE = os.path.join(BASE_DIR, 'data.json')
SK        = 'rotas_cat_v9'


# ──────────────────────────────────────────────────────────
#  LICENÇA
# ──────────────────────────────────────────────────────────

def _check_license():
    """Retorna (ok, mensagem_curta, machine_id, dias_restantes)."""
    try:
        import trial_guard as tg
        state = tg._load_state()
        mid   = tg.machine_id()
        hoje  = datetime.date.today()

        if not state.get('expira'):
            state = tg._set_license(tg.TRIAL_DAYS_PADRAO, 'trial')

        last = tg._parse_date(state.get('last_run', ''))
        if last and hoje < last:
            return False, 'Relógio do sistema alterado.', mid, 0

        state['last_run'] = hoje.isoformat()
        tg._save_state(state)

        remota = tg._check_remote(mid)
        if remota is not None:
            if not remota.get('ativo', True):
                return False, 'Licença desativada pelo administrador.', mid, 0
            exp_r = tg._parse_date(remota.get('expira', ''))
            if exp_r:
                if hoje <= exp_r:
                    state['expira'] = remota['expira']
                    state['tipo']   = 'licenciado'
                    tg._save_state(state)
                    dias = (exp_r - hoje).days
                    return True, state.get('tipo', 'trial'), mid, dias
                else:
                    return False, 'Licença expirada.', mid, 0

        exp = tg._parse_date(state.get('expira', ''))
        if exp and hoje <= exp:
            dias = (exp - hoje).days
            return True, state.get('tipo', 'trial'), mid, dias

        return False, 'Licença expirada.', mid, 0

    except Exception as e:
        return False, f'Erro: {e}', 'N/A', 0


def _license_page(msg: str, mid: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ativação — Platner Rotas</title>
<link rel="stylesheet" href="/static/premium.css">
<style>body{{margin:0}}</style>
</head>
<body>
<div id="license-screen">
  <div class="license-card">
    <div style="font-size:52px;margin-bottom:12px">🗺️</div>
    <h1>Platner Rotas</h1>
    <p>{msg}<br>Envie o <strong>ID desta máquina</strong> ao administrador<br>para receber seu código de ativação.</p>
    <div class="license-mid" title="Clique para selecionar">{mid}</div>
    <input class="license-input" id="codigo" type="password"
           placeholder="Cole aqui o código de ativação" autocomplete="off">
    <button class="license-btn" onclick="ativar()">🔓 Ativar Licença</button>
    <div id="lic-msg"></div>
  </div>
</div>
<script>
async function ativar(){{
  const c=document.getElementById('codigo').value.trim();
  if(!c)return;
  const r=await fetch('/api/ativar',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{codigo:c}})}});
  const d=await r.json();
  const el=document.getElementById('lic-msg');
  if(d.ok){{el.className='license-ok';el.textContent='✅ '+d.msg;setTimeout(()=>location.reload(),1500);}}
  else{{el.className='license-error';el.textContent='❌ '+d.msg;}}
}}
document.getElementById('codigo').addEventListener('keydown',e=>{{if(e.key==='Enter')ativar();}});
</script>
</body></html>"""


# ──────────────────────────────────────────────────────────
#  UTILITÁRIOS EXCEL
# ──────────────────────────────────────────────────────────

def _safe_float(v):
    try:
        f = float(v)
        return f if f != 0 else None
    except:
        return None

def _safe_str(v):
    s = str(v) if v is not None else ''
    return '' if s.lower() in ('nan','none','nat') else s.strip()

def _norm_h(v):
    s = _safe_str(v)
    return re.sub(r'^(\d{1,2})h(\d{2})$', r'\1:\2', s, flags=re.I)

def excel_to_records(path):
    xl, records = pd.ExcelFile(path), []
    for sn in xl.sheet_names:
        if re.search(r'deslig', sn, re.I):
            continue
        df = xl.parse(sn, header=None, dtype=str)
        cur_van = None
        for _, row in df.iterrows():
            v = list(row)
            c0 = _safe_str(v[0]) if v else ''
            c2 = _safe_str(v[2]) if len(v) > 2 else ''
            if not c0 and re.match(r'^(VAN|ONIBUS|ÔNIBUS|MICRO)\s*\d+', c2, re.I):
                cur_van = c2; continue
            try:
                ordem = int(float(c0))
                if ordem <= 0: continue
            except:
                continue
            nc = len(v)
            if nc >= 16:
                mat,nom,end,bai,cid,emb = v[2],v[3],v[4],v[5],v[6],v[7]
                hor,rot,tur             = v[8],v[10],v[11]
                lc,oc,le,oe             = v[12],v[13],v[14],v[15]
            elif nc >= 15:
                mat,nom,end,bai,cid,emb = v[1],v[2],v[3],v[4],v[5],v[6]
                hor,rot,tur             = v[7],v[9],v[10]
                lc,oc,le,oe             = v[11],v[12],v[13],v[14]
            else:
                mat,nom,end,bai,cid,emb = v[1],v[2],v[3],v[4],v[5],v[6]
                hor = v[7] if nc>7 else ''
                rot = v[8] if nc>8 else ''
                tur = v[9] if nc>9 else ''
                lc,oc,le,oe = (v[i] if nc>i else None for i in (10,11,12,13))
            nome = _safe_str(nom)
            if not nome: continue
            rota = _safe_str(rot)
            records.append({'sheet':sn,'van':cur_van or rota,'ordem':ordem,
                'matricula':_safe_str(mat),'nome':nome,'endereco':_safe_str(end),
                'bairro':_safe_str(bai),'cidade':_safe_str(cid),'embarque':_safe_str(emb),
                'horario':_norm_h(hor),'rota':rota,'turno':_safe_str(tur),
                'lat_casa':_safe_float(lc),'lon_casa':_safe_float(oc),
                'lat_emb':_safe_float(le),'lon_emb':_safe_float(oe)})
    return records

def _load_records():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, encoding='utf-8') as f:
                d = json.load(f)
            if d: return d
        except:
            pass
    return []

def _save_records(records):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=True, indent=2)


# ──────────────────────────────────────────────────────────
#  ROTAS FLASK
# ──────────────────────────────────────────────────────────

@app.route('/static/<path:fn>')
def static_files(fn):
    return send_from_directory(app.static_folder, fn)


@app.route('/')
def index():
    ok, tipo, mid, dias = _check_license()
    if not ok:
        return Response(_license_page(tipo, mid), mimetype='text/html; charset=utf-8')

    with open(HTML_FILE, encoding='utf-8') as f:
        html = f.read()

    # ── 1. Limpa localStorage ANTES do init (no <head>) ──────────────────
    clear_script = f"""<script>
(function(){{try{{localStorage.removeItem('{SK}');}}catch(e){{}};}})();
</script>"""
    html = html.replace('</head>', clear_script + '\n</head>', 1)

    # ── 2. HTML sempre começa limpo (sem dados hardcoded) ────────────────
    html = re.sub(r'const builtin=\[.*?\];', 'const builtin=[];', html, flags=re.DOTALL)

    # ── 3. Auto-conecta ao servidor Flask como "Sheets" ──────────────────
    #    Injeta a URL diretamente no JS (não depende de localStorage)
    sheets_url = 'http://localhost:8502/api/sheets'
    html = html.replace(
        "let sheetsUrl=localStorage.getItem(SK_SHEETS)||'';",
        f"let sheetsUrl=localStorage.getItem(SK_SHEETS)||'{sheets_url}';",
        1
    )

    # ── 4. CSS premium ───────────────────────────────────────────────────
    html = html.replace('</head>',
        '<link rel="stylesheet" href="/static/premium.css">\n</head>', 1)

    # ── 5. Remove badge "Caterpillar" ────────────────────────────────────
    html = html.replace('<span class="badge">Caterpillar</span>', '', 1)

    # ── 6. Badge LICENCIADO/TRIAL como dropdown no header ────────────────
    exp_str = (datetime.date.today() + datetime.timedelta(days=dias)).isoformat() \
              if dias > 0 else '—'
    if tipo == 'licenciado':
        badge_txt  = 'LICENCIADO'
        badge_color= '#059669'
        lic_bg     = '#f0fdf4'; lic_border = '#bbf7d0'
        lic_icon   = '&#10003;'; lic_color = '#065f46'
        lic_label  = f'Licenciado &mdash; <b>{dias} dias restantes</b> ({exp_str})'
    else:
        badge_txt  = f'TRIAL {dias}d'
        badge_color= '#d97706'
        lic_bg     = '#fff7ed'; lic_border = '#fed7aa'
        lic_icon   = '&#8987;'; lic_color  = '#92400e'
        lic_label  = f'Trial &mdash; <b>{dias} dias restantes</b> ({exp_str})'

    # Painel que abre/fecha ao clicar no badge
    lic_panel = f"""<div id="lic-panel" style="display:none;position:absolute;top:50px;left:10px;
  z-index:2000;background:{lic_bg};border:1px solid {lic_border};border-radius:12px;
  padding:12px 14px;font-size:11px;font-family:inherit;width:250px;
  box-shadow:0 8px 28px rgba(0,0,0,.18);">
  <div style="font-weight:700;color:{lic_color};margin-bottom:8px;">
    {lic_icon}&nbsp; {lic_label}
  </div>
  <div style="color:#374151;font-size:10px;font-weight:600;margin-bottom:4px;">
    ID desta m&aacute;quina (envie ao suporte):
  </div>
  <div onclick="navigator.clipboard.writeText('{mid}').then(()=>{{try{{showToast('ID copiado!')}}catch(e){{}}}});"
    title="Clique para copiar"
    style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:7px;
      padding:7px 10px;font-family:'Courier New',monospace;font-size:13px;
      font-weight:700;color:#1e40af;letter-spacing:2px;cursor:pointer;
      text-align:center;user-select:all;">{mid}</div>
  <div style="color:#6b7280;font-size:10px;margin-top:7px;text-align:center;line-height:1.4;">
    Copie e mande por WhatsApp/e-mail<br>para o suporte ativar sua licen&ccedil;a.
  </div>
</div>"""

    # Badge botão com seta ▼ que abre o painel
    lic_badge = (
        f'<button onclick="var p=document.getElementById(\'lic-panel\');'
        f'p.style.display=p.style.display===\'none\'?\'block\':\'none\';"'
        f' style="background:{badge_color};color:#fff;font-size:10px;font-weight:800;'
        f'padding:3px 11px;border-radius:20px;letter-spacing:.4px;cursor:pointer;border:none;"'
        f' title="Machine ID: {mid}">{badge_txt} &#9660;</button>'
        + lic_panel
    )
    # Insere logo depois do h1 do header
    html = html.replace('<span class="save-ind', lic_badge + '\n  <span class="save-ind', 1)

    # Fecha painel ao clicar fora
    close_outside = """<script>
document.addEventListener('click', function(e) {
  var p = document.getElementById('lic-panel');
  if (!p) return;
  if (!p.contains(e.target) && !e.target.closest('[onclick*="lic-panel"]')) {
    p.style.display = 'none';
  }
});
</script>"""

    # ── 7. Esconde sync-row da sidebar; cria dropdown no header igual ao de licença ──
    sheets_panel = f"""<div id="sheets-panel" style="display:none;position:absolute;
  top:50px;right:80px;z-index:2000;background:#fff;border:1px solid #dde3ec;
  border-radius:12px;padding:12px 14px;width:330px;
  box-shadow:0 8px 28px rgba(0,0,0,.18);font-family:inherit;">
  <div style="font-weight:700;color:#0f172a;font-size:12px;margin-bottom:10px;">
    &#9729;&#65039; Conex&atilde;o Google Sheets
  </div>
  <input id="sheets-url-h" type="text"
    placeholder="Cole a URL do Apps Script (/exec)..."
    value="{sheets_url}"
    style="width:100%;font-size:11px;padding:5px 8px;border:1.5px solid #dde3ec;
      border-radius:7px;height:30px;margin-bottom:8px;box-sizing:border-box;
      font-family:inherit;">
  <div style="display:flex;gap:6px;">
    <button class="bp" style="flex:1;height:28px;font-size:11px;"
      onclick="_sheetsPanelAction('connect')">Conectar</button>
    <button class="bg" style="height:28px;font-size:11px;padding:0 12px;"
      onclick="_sheetsPanelAction('load')" title="Carregar dados">&#8595; Carregar</button>
    <button class="bs" style="height:28px;font-size:11px;padding:0 12px;"
      onclick="_sheetsPanelAction('sync')" title="Salvar alteracoes">&#8593; Sync</button>
  </div>
</div>"""

    hide_sync = """<script>
function _sheetsPanelAction(action) {
  var urlH = document.getElementById('sheets-url-h');
  var urlS = document.getElementById('sheets-url');
  if (urlH && urlS) urlS.value = urlH.value;
  if (action === 'connect') connectSheets();
  else if (action === 'load') loadFromSheets();
  else if (action === 'sync') syncToSheets();
  if (action === 'connect') {
    setTimeout(function() {
      var p = document.getElementById('sheets-panel');
      if (p) p.style.display = 'none';
    }, 2000);
  }
}

document.addEventListener('DOMContentLoaded', function() {
  // Esconde a sync-row da sidebar permanentemente
  var sr = document.querySelector('.sync-row');
  if (sr) sr.style.display = 'none';

  // Envolve sheets-status num botao com seta ▼
  var ss = document.getElementById('sheets-status');
  if (ss) {
    ss.style.cursor = 'pointer';
    var arrow = document.createElement('span');
    arrow.textContent = ' ▼';
    arrow.style.cssText = 'font-size:8px;opacity:.8;vertical-align:middle;';
    ss.appendChild(arrow);
    ss.addEventListener('click', function(e) {
      e.stopPropagation();
      var p = document.getElementById('sheets-panel');
      p.style.display = p.style.display === 'none' ? 'block' : 'none';
      // Sincroniza URL do painel com o campo oculto
      var urlS = document.getElementById('sheets-url');
      var urlH = document.getElementById('sheets-url-h');
      if (urlS && urlH && urlS.value) urlH.value = urlS.value;
    });
  }

  // Fecha sheets-panel ao clicar fora
  document.addEventListener('click', function(e) {
    var p = document.getElementById('sheets-panel');
    if (p && !p.contains(e.target) && e.target.id !== 'sheets-status'
        && !e.target.closest('#sheets-status')) {
      p.style.display = 'none';
    }
  });
});
</script>"""

    html = html.replace(
        '<span id="sheets-status"',
        sheets_panel + '\n  <span id="sheets-status"',
        1
    )
    html = html.replace('</body>', close_outside + hide_sync + '\n</body>', 1)

    return Response(html, mimetype='text/html; charset=utf-8')


# ──────────────────────────────────────────────────────────
#  API — LICENÇA
# ──────────────────────────────────────────────────────────

@app.route('/api/ativar', methods=['POST'])
def api_ativar():
    try:
        import trial_guard as tg
        body   = request.get_json(force=True, silent=True) or {}
        codigo = str(body.get('codigo', '')).strip()
        for dias in [30, 60, 90, 180, 365]:
            if codigo == f'{tg.SENHA_MESTRA}|{dias}':
                tg._set_license(dias, 'licenciado')
                return jsonify({'ok': True, 'msg': f'Licença ativada por {dias} dias!'})
        return jsonify({'ok': False, 'msg': 'Código inválido.'})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


# ──────────────────────────────────────────────────────────
#  API — SHEETS (emula Google Apps Script localmente)
# ──────────────────────────────────────────────────────────

def _jsonp(cb: str, data: dict):
    """Retorna resposta JSONP: callback({...})"""
    body = f'{cb}({json.dumps(data, ensure_ascii=True)})'
    return Response(body, mimetype='application/javascript')


@app.route('/api/sheets', methods=['GET', 'POST'])
def api_sheets():
    if request.method == 'GET':
        action = request.args.get('action', '')
        cb     = request.args.get('callback', 'cb')

        if action == 'ping':
            return _jsonp(cb, {'ok': True, 'ts': int(datetime.datetime.now().timestamp() * 1000)})

        if action == 'load':
            records = _load_records()
            return _jsonp(cb, {
                'ok':            True,
                'records':       records,
                'vanDestTimes':  {},
                'changeHistory': [],
            })

        if action == 'verifyLastSave':
            records = _load_records()
            return _jsonp(cb, {'ok': True, 'saved': len(records)})

        return _jsonp(cb, {'ok': False, 'error': 'unknown action'})

    # POST — salva dados enviados pelo HTML (no-cors, sem resposta legível)
    try:
        raw  = request.get_data(as_text=True)
        body = json.loads(raw) if raw else {}
        if body.get('action') == 'save':
            records = body.get('records', [])
            _save_records(records)
    except:
        pass
    return Response('ok', mimetype='text/plain')


# ──────────────────────────────────────────────────────────
#  API — DADOS BRUTOS
# ──────────────────────────────────────────────────────────

@app.route('/api/data')
def api_data():
    return jsonify(_load_records())

@app.route('/api/save', methods=['POST'])
def api_save():
    body    = request.get_json(force=True, silent=True) or {}
    records = body.get('records', body) if isinstance(body, dict) else body
    _save_records(records)
    return jsonify({'ok': True})


# ──────────────────────────────────────────────────────────
#  MAIN
# ──────────────────────────────────────────────────────────

def _open_browser():
    webbrowser.open('http://localhost:8502')

if __name__ == '__main__':
    print('=' * 52)
    print('  Platner Rotas - Servidor iniciado')
    print('  http://localhost:8502')
    print('=' * 52)
    threading.Timer(1.3, _open_browser).start()
    app.run(host='127.0.0.1', port=8502, debug=False)
