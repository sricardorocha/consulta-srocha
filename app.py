#!/usr/bin/env python3
"""Consulta Srocha - API de consulta (container 'api').

Login proprio por token (a tela HTML fica no OneDrive e chama esta API).
Usuarios e senhas vem da variavel USUARIOS="sergio:senha1;debora:senha2".
"""
import base64, hashlib, hmac, json, os, re, sqlite3, time, unicodedata
import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware

VERSAO = '1.1.0'
DATA = os.environ.get('DATA_DIR', '/data')
DB = os.path.join(DATA, 'consulta_srocha.db')
CALC = os.environ.get('CALCULADORA_URL', 'http://calculadora:8080/api')
def _segredo():
    if os.environ.get('SEGREDO_TOKEN'):
        return os.environ['SEGREDO_TOKEN'].encode()
    p = os.path.join(DATA, 'segredo.key')
    if not os.path.exists(p):
        os.makedirs(DATA, exist_ok=True)
        with open(p, 'wb') as f:
            f.write(base64.urlsafe_b64encode(os.urandom(48)))
        os.chmod(p, 0o600)
    return open(p, 'rb').read().strip()


SEGREDO = _segredo()
# usuario sem diferenca de maiusculas/minusculas; espacos, aspas e quebras de linha sobrando sao ignorados
USUARIOS = {u.strip().strip('"\'').lower(): s.strip().strip('"\'') for u, s in
            (p.split(':', 1) for p in os.environ.get('USUARIOS', '').strip().strip('"\'').split(';') if ':' in p) if u.strip()}
VALIDADE = 12 * 3600
online = {}  # usuario -> ultimo acesso (epoch)

app = FastAPI(title='Consulta Srocha', version=VERSAO, docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])


# ---------------- autenticacao ----------------
def _assinar(msg):
    return base64.urlsafe_b64encode(hmac.new(SEGREDO, msg.encode(), hashlib.sha256).digest()).decode().rstrip('=')


def _usuario(token):
    try:
        usuario, exp, sig = (token or '').split('.')
        if hmac.compare_digest(sig, _assinar(f'{usuario}.{exp}')) and int(exp) > time.time() and usuario in USUARIOS:
            online[usuario] = time.time()
            return usuario
    except ValueError:
        pass
    raise HTTPException(401, 'Sessão inválida ou expirada. Entre novamente.')


@app.post('/api/login')
async def login(req: Request):
    d = await req.json()
    u, s = (d.get('usuario') or '').strip().lower(), (d.get('senha') or '').strip()
    if u not in USUARIOS or not hmac.compare_digest(USUARIOS[u], s):
        time.sleep(1)
        raise HTTPException(401, 'Usuário ou senha incorretos.')
    exp = str(int(time.time()) + VALIDADE)
    online[u] = time.time()
    return {'token': f'{u}.{exp}.{_assinar(f"{u}.{exp}")}', 'usuario': u, 'expira': int(exp)}


# ---------------- utilitarios ----------------
def con():
    if not os.path.exists(DB):
        raise HTTPException(503, 'Base ainda sendo montada. Tente em alguns minutos.')
    c = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    return c


def linhas(cur):
    return [dict(r) for r in cur]


def sem_acento(t):
    return ''.join(ch for ch in unicodedata.normalize('NFD', t.lower()) if unicodedata.category(ch) != 'Mn')


def trecho(texto, termos, tam=320):
    base = sem_acento(texto)
    pos = min([base.find(t) for t in termos if base.find(t) >= 0] or [0])
    ini = max(0, pos - 80)
    return ('…' if ini else '') + re.sub(r'\s+', ' ', texto[ini:ini + tam]).strip() + '…'


def cest_casa(cod, campo):
    """O NCM do CEST vem como lista de codigos ('2202.10.00 2202.99.00') ou por capitulo
    ('Capitulos 13 e 15 a 23', segmento de venda porta a porta)."""
    campo = campo or ''
    if sem_acento(campo).startswith('capitulo'):
        caps = set()
        for a, b in re.findall(r'(\d{1,2})(?:\s*a\s*(\d{1,2}))?', campo):
            caps.update(range(int(a), int(b or a) + 1))
        return int(cod[:2]) in caps
    return any(len(x) >= 2 and cod.startswith(x) for x in (re.sub(r'\D', '', t) for t in campo.split()))


# ---------------- rotas ----------------
@app.get('/api/saude')
def saude():
    return {'ok': True, 'versao': VERSAO, 'base': os.path.exists(DB), 'usuarios_cadastrados': len(USUARIOS)}


@app.get('/api/status')
async def status(x_token: str = Header(None)):
    u = _usuario(x_token)
    st = {}
    p = os.path.join(DATA, 'status.json')
    if os.path.exists(p):
        st = json.load(open(p, encoding='utf-8'))
    calc = {'ok': False}
    try:
        async with httpx.AsyncClient(timeout=5) as cli:
            r = await cli.get(f'{CALC}/calculadora/dados-abertos/situacoes-tributarias/cbs-ibs', params={'data': time.strftime('%Y-%m-%d')})
            calc = {'ok': r.status_code == 200, 'http': r.status_code}
    except Exception as e:
        calc['erro'] = str(e)[:120]
    fontes = linhas(con().execute('select * from fonte')) if os.path.exists(DB) else []
    agora = time.time()
    return {'api_versao': VERSAO, 'voce': u, 'atualizacao': st, 'calculadora_receita': calc, 'fontes': fontes,
            'conectados': [{'usuario': k, 'ha_segundos': int(agora - v)} for k, v in online.items() if agora - v < 600]}


@app.get('/api/busca')
def busca(q: str, x_token: str = Header(None), limite: int = 30, fonte: str = None):
    _usuario(x_token)
    termos = [t for t in re.findall(r'[\w]+', sem_acento(q)) if len(t) > 1][:12]
    if not termos:
        return {'resultados': []}
    expr = ' AND '.join(f'"{t}"' for t in termos) if not q.startswith('"') else '"' + ' '.join(termos) + '"'
    c = con()
    sql = ('select d.id, d.norma_id, d.rotulo, d.texto, n.titulo, n.esfera, coalesce(d.url, n.url_oficial) as url_oficial, n.fonte_id '
           'from busca b join dispositivo d on d.id=b.rowid join norma n on n.id=d.norma_id where busca match ?')
    par = [expr]
    if fonte:
        fs = [f for f in fonte.split(',') if f][:6]
        sql += ' and n.fonte_id in (%s)' % ','.join('?' * len(fs))
        par += fs
    sql += ' order by rank limit ?'
    par.append(max(1, min(limite, 100)))
    res = [{**{k: r[k] for k in ('id', 'norma_id', 'rotulo', 'titulo', 'esfera', 'url_oficial', 'fonte_id')},
            'trecho': trecho(r['texto'], termos)} for r in c.execute(sql, par)]
    return {'consulta': q, 'total': len(res), 'resultados': res}


@app.get('/api/norma/{norma_id:path}')
def norma(norma_id: str, x_token: str = Header(None)):
    _usuario(x_token)
    c = con()
    n = c.execute('select * from norma where id=?', (norma_id,)).fetchone()
    if not n:
        raise HTTPException(404, 'Norma não encontrada.')
    return {'norma': dict(n), 'dispositivos': linhas(c.execute('select id, rotulo, texto, url from dispositivo where norma_id=? order by ordem', (norma_id,)))}


@app.get('/api/normas')
def normas(x_token: str = Header(None), fonte: str = '', uf: str = ''):
    """Lista de normas de uma fonte (ex.: sefaz_sp, sefaz_es) com o numero de dispositivos."""
    _usuario(x_token)
    sql, par = ('select n.id, n.titulo, n.ementa, n.url_oficial, n.alterado_em, n.uf, n.fonte_id, count(d.id) as dispositivos '
                'from norma n left join dispositivo d on d.norma_id=n.id where 1=1'), []
    if fonte:
        sql += ' and n.fonte_id=?'
        par.append(fonte)
    if uf:
        sql += ' and n.uf=?'
        par.append(uf.upper())
    return {'resultados': linhas(con().execute(sql + ' group by n.id order by n.rowid limit 500', par))}


@app.get('/api/ncm/{codigo}')
def ncm(codigo: str, x_token: str = Header(None)):
    _usuario(x_token)
    cod = re.sub(r'\D', '', codigo)
    c = con()
    hier = linhas(c.execute("select * from ncm where ? like codigo || '%' order by length(codigo)", (cod,)))
    if not hier:
        raise HTTPException(404, 'NCM não encontrado.')
    vistos, cest = set(), []
    for r in linhas(c.execute('select * from cest where revogado=0')):
        if cest_casa(cod, r['ncm']) and (r['cest'], r['descricao']) not in vistos:
            vistos.add((r['cest'], r['descricao']))
            cest.append(r)
    cct = linhas(c.execute('select m.cclasstrib, m.anexo, m.condicao, m.excecao, m.codigo as ncm_base, t.cst, t.red_cbs_receita, '
                           't.red_ibs_receita, t.fundamentacao, t.confronto, t.confronto_detalhe from cclasstrib_ncm m '
                           "left join cclasstrib t on t.codigo=m.cclasstrib where m.tipo='NCM' and ? like m.codigo || '%'", (cod,)))
    return {'ncm': cod, 'hierarquia': hier, 'ipi_tipi': (hier[-1] or {}).get('aliquota_ipi'),
            'cest': cest, 'cclasstrib_aplicaveis': cct,
            'aviso': 'Sem cClassTrib específico: tributação integral (000001), salvo outra hipótese legal.' if not cct else None}


@app.get('/api/cclasstrib')
def cclasstrib(q: str = '', x_token: str = Header(None), situacao: str = None):
    _usuario(x_token)
    c = con()
    sql, par = 'select * from cclasstrib where 1=1', []
    if q:
        sql += ' and (codigo like ? or nome_receita like ? or nome_cff like ?)'
        par += [f'{q}%', f'%{q}%', f'%{q}%']
    if situacao:
        sql += ' and confronto=?'
        par.append(situacao)
    return {'resultados': linhas(c.execute(sql + ' order by codigo', par))}


@app.get('/api/confronto')
def confronto(x_token: str = Header(None), situacao: str = 'diverge'):
    _usuario(x_token)
    return {'resultados': linhas(con().execute('select * from confronto where situacao=? order by tema, chave', (situacao,)))}


@app.get('/api/conectados')
def conectados(x_token: str = Header(None)):
    _usuario(x_token)
    agora = time.time()
    return [{'usuario': k, 'ha_segundos': int(agora - v)} for k, v in online.items() if agora - v < 600]


@app.api_route('/api/rtc/{caminho:path}', methods=['GET', 'POST'])
async def rtc(caminho: str, req: Request, x_token: str = Header(None)):
    """Repassa para a calculadora oficial da Receita (ex.: calculadora/regime-geral)."""
    _usuario(x_token)
    async with httpx.AsyncClient(timeout=60) as cli:
        r = await cli.request(req.method, f'{CALC}/{caminho}', params=dict(req.query_params),
                              content=await req.body(), headers={'content-type': req.headers.get('content-type', 'application/json')})
    return Response(r.content, status_code=r.status_code, media_type=r.headers.get('content-type'))


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000, log_level='warning')
