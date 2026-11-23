#!/usr/bin/env python3
"""Consulta Srocha - montagem da base unificada (Fase 1).

Uso: python3 ingest.py <pasta_dos_repositorios> <arquivo_saida.db>

Fontes (todas publicas, clonadas do GitHub):
  - Matheuscfs/legislacao-tributaria : normas federais compiladas (Planalto),
    Confaz (convenios, ajustes, protocolos), tabelas NCM, TIPI, CEST, cClassTrib.
  - ReceitaFederal/calculadora-rtc-regime-regular : base oficial da Receita
    (cClassTrib, CST, fundamentacao, NCM, NBS, municipios) -> rf_oficial.db
  - brunoqualhato/tributos : SO CATALOGO (textos nao compilados, ver confronto).
"""
import csv, json, re, sqlite3, subprocess, sys, os, datetime

REPOS, SAIDA = sys.argv[1], sys.argv[2]
MAT = os.path.join(REPOS, 'Matheuscfs_legislacao-tributaria')
BRU = os.path.join(REPOS, 'brunoqualhato_tributos')
RFDB = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(SAIDA), 'rf_oficial.db')
HOJE = datetime.date.today().isoformat()

if os.path.exists(SAIDA):
    os.remove(SAIDA)
db = sqlite3.connect(SAIDA)
db.executescript("""
CREATE TABLE fonte(id TEXT PRIMARY KEY, nome TEXT, tipo TEXT, url TEXT,
  confiabilidade TEXT, observacao TEXT, atualizado_em TEXT);
CREATE TABLE norma(id TEXT PRIMARY KEY, fonte_id TEXT, esfera TEXT, uf TEXT, municipio TEXT,
  tipo TEXT, numero TEXT, data TEXT, titulo TEXT, ementa TEXT, url_oficial TEXT,
  texto_compilado INTEGER, alterado_em TEXT);
CREATE TABLE dispositivo(id INTEGER PRIMARY KEY, norma_id TEXT, rotulo TEXT, ordem INTEGER, texto TEXT);
CREATE VIRTUAL TABLE busca USING fts5(rotulo, texto, norma_titulo, content='', tokenize='unicode61 remove_diacritics 2');
CREATE TABLE ncm(codigo TEXT PRIMARY KEY, descricao TEXT, inicio TEXT, fim TEXT, aliquota_ipi TEXT, fonte_id TEXT);
CREATE TABLE cest(cest TEXT, ncm TEXT, item TEXT, descricao TEXT, revogado INTEGER);
CREATE TABLE cclasstrib(codigo TEXT PRIMARY KEY, cst TEXT, nome_receita TEXT, nome_cff TEXT,
  red_cbs_receita REAL, red_ibs_receita REAL, red_cbs_cff REAL, red_ibs_cff REAL,
  fundamentacao TEXT, link_lei TEXT, confronto TEXT, confronto_detalhe TEXT);
CREATE TABLE cclasstrib_ncm(cclasstrib TEXT, tipo TEXT, codigo TEXT, item TEXT, anexo TEXT, condicao TEXT, excecao TEXT);
CREATE TABLE confronto(tema TEXT, chave TEXT, situacao TEXT, fontes TEXT, detalhe TEXT, verificado_em TEXT);
""")

def commit_date(path):
    try:
        return subprocess.check_output(['git', '-C', path, 'log', '-1', '--format=%cI'], text=True).strip()
    except Exception:
        return None

db.executemany('INSERT INTO fonte VALUES (?,?,?,?,?,?,?)', [
    ('planalto', 'Planalto (via legislacao-tributaria)', 'texto de lei', 'https://www.planalto.gov.br',
     'alta', 'Texto compilado, captura diaria', commit_date(MAT)),
    ('confaz', 'Confaz (via legislacao-tributaria)', 'texto de norma', 'https://www.confaz.fazenda.gov.br',
     'alta', 'Convenios, ajustes e protocolos', commit_date(MAT)),
    ('receita_calc', 'Calculadora RTC oficial da Receita Federal', 'tabela oficial',
     'https://github.com/ReceitaFederal/calculadora-rtc-regime-regular', 'oficial', 'Base SQLite gerada dos scripts flyway', None),
    ('cff', 'Portal Conformidade Facil (via legislacao-tributaria)', 'tabela oficial',
     'https://dfe-portal.svrs.rs.gov.br/CFF', 'oficial', 'cClassTrib por NCM/NBS', commit_date(MAT)),
    ('siscomex', 'NCM Siscomex / TIPI / CEST (via legislacao-tributaria)', 'tabela oficial', None, 'alta', None, commit_date(MAT)),
    ('catalogo_bruno', 'Acervo tributos (brunoqualhato)', 'catalogo', 'https://github.com/brunoqualhato/tributos',
     'baixa', 'Textos ORIGINAIS nao compilados (RICMS-SP de 2000, LC 214 sem LC 227). Usar so como indice.', commit_date(BRU)),
])

# ---------- normas federais e Confaz ----------
ART = re.compile(r'(?m)^\s*(Art\.\s*\d+[º°o]?(?:-[A-Z])?\.?|Cl[áa]usula\s+\w+)')

def fatiar(norma_id, titulo, texto):
    pos = [m.start() for m in ART.finditer(texto)]
    if not pos:
        pos = [0]
    pos.append(len(texto))
    for i in range(len(pos) - 1):
        trecho = texto[pos[i]:pos[i + 1]].strip()
        if not trecho:
            continue
        m = ART.match(trecho)
        rotulo = re.sub(r'\s+', ' ', m.group(1)).strip(' .') if m else 'Preâmbulo'
        cur = db.execute('INSERT INTO dispositivo(norma_id,rotulo,ordem,texto) VALUES (?,?,?,?)',
                         (norma_id, rotulo, i, trecho))
        db.execute('INSERT INTO busca(rowid,rotulo,texto,norma_titulo) VALUES (?,?,?,?)',
                   (cur.lastrowid, rotulo, trecho, titulo))

for n in json.load(open(os.path.join(MAT, 'normas.json'), encoding='utf-8')):
    p = os.path.join(MAT, 'normas', 'federal', n['id'], 'texto.md')
    if not os.path.exists(p):
        continue
    db.execute('INSERT INTO norma VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
               (n['id'], 'planalto', 'federal', None, None, n.get('tipo'), n.get('numero'), n.get('data'),
                n.get('nome'), n.get('assunto'), n.get('url'), 1, None))
    fatiar(n['id'], n.get('nome'), open(p, encoding='utf-8').read())

for r in csv.DictReader(open(os.path.join(MAT, 'normas', 'confaz', 'indice.csv'), encoding='utf-8')):
    p = os.path.join(MAT, r['arquivo'])
    if not os.path.exists(p):
        continue
    nid = 'confaz:' + r['arquivo'].split('/')[-1][:-3]
    db.execute('INSERT OR IGNORE INTO norma VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
               (nid, 'confaz', 'confaz', None, None, r['secao'], None, r['ano'], r['titulo'], r['ementa'],
                r['url'], 1, r['alterado_em']))
    fatiar(nid, r['titulo'], open(p, encoding='utf-8').read())

# ---------- catalogo (so indice, sem texto) ----------
for raiz, _, arqs in os.walk(BRU):
    if '/.git' in raiz or '_auditoria' in raiz:
        continue
    for a in arqs:
        if not a.endswith('.md') or a in ('README.md', 'ACERVO.md', 'PROGRESSO.md', 'WORKLOG.md', 'Home.md', 'AGENT.md'):
            continue
        p = os.path.join(raiz, a)
        cab = open(p, encoding='utf-8').read(3000)
        meta = dict(re.findall(r'(?m)^(\w+):\s*"?(.*?)"?\s*$', cab.split('---')[1] if cab.startswith('---') else ''))
        rel = os.path.relpath(p, BRU)
        partes = rel.split('/')
        uf = partes[1].upper() if partes[0] in ('estaduais', 'municipais') and len(partes) > 1 else None
        db.execute('INSERT OR IGNORE INTO norma VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   ('cat:' + rel, 'catalogo_bruno', meta.get('esfera', partes[0]), uf,
                    'capital' if partes[0] == 'municipais' else None, meta.get('tipo'), meta.get('numero'),
                    meta.get('data'), meta.get('titulo', a), meta.get('ementa'), meta.get('fonte'), 0, None))

# ---------- tabelas ----------
tipi = {}
for r in csv.DictReader(open(os.path.join(MAT, 'tabelas', 'tipi', 'tipi.csv'), encoding='utf-8')):
    if not r['ex']:
        tipi[r['ncm'].replace('.', '')] = r['aliquota_ipi']
for r in csv.DictReader(open(os.path.join(MAT, 'tabelas', 'ncm', 'ncm.csv'), encoding='utf-8')):
    cod = r['codigo'].replace('.', '')
    db.execute('INSERT OR REPLACE INTO ncm VALUES (?,?,?,?,?,?)',
               (cod, r['descricao'], r['inicio_vigencia'], r['fim_vigencia'] or None, tipi.get(cod), 'siscomex'))
for r in csv.DictReader(open(os.path.join(MAT, 'tabelas', 'cest', 'cest.csv'), encoding='utf-8')):
    db.execute('INSERT INTO cest VALUES (?,?,?,?,?)', (r['cest'], r['ncm'], r['item'], r['descricao'], r['revogado'] == 'true'))
for r in csv.DictReader(open(os.path.join(MAT, 'tabelas', 'ibs-cbs', 'classificacao-por-ncm-nbs.csv'), encoding='utf-8')):
    if r['fim_vigencia'] and r['fim_vigencia'] < HOJE:
        continue
    db.execute('INSERT INTO cclasstrib_ncm VALUES (?,?,?,?,?,?,?)',
               (r['cclasstrib'], r['tipo'], r['codigo'], r['item'], r['anexo'], r['condicao'], r['excecao']))

# ---------- cClassTrib com confronto Receita x Conformidade Facil ----------
rf = sqlite3.connect(RFDB)
vig = '(%s_INICIO_VIGENCIA<=? and (%s_FIM_VIGENCIA is null or %s_FIM_VIGENCIA>=?))'
sit = dict(rf.execute('select SITR_ID,SITR_CD from SITUACAO_TRIBUTARIA'))
rfc = {r[0]: r for r in rf.execute('select CLTR_CD,CLTR_DESCRICAO,CLTR_SITR_ID,CLTR_ID from CLASSIFICACAO_TRIBUTARIA where ' + vig % (('CLTR',) * 3), (HOJE, HOJE))}
red = {}
for cid, tb, v in rf.execute('select PERE_CLTR_ID,PERE_TBTO_ID,PERE_VALOR from PERCENTUAL_REDUCAO where ' + vig % (('PERE',) * 3), (HOJE, HOJE)):
    red.setdefault(cid, {})[tb] = v
fund = {}
for cid, ref, curto in rf.execute('select fc.FDCL_CLTR_ID, fl.FDLG_REFERENCIA_NORMATIVA, fl.FDLG_TEXTO_CURTO from FUNDAMENTACAO_CLASSIFICACAO fc '
                                  'join FUNDAMENTACAO_LEGAL fl on fl.FDLG_ID=fc.FDCL_FDLG_ID where ' + vig % (('fc.FDCL',) * 3), (HOJE, HOJE)):
    fund[cid] = (curto or ref or '')[:500]
cff = {r['cclasstrib']: r for r in csv.DictReader(open(os.path.join(MAT, 'tabelas', 'ibs-cbs', 'classificacao-tributaria.csv'), encoding='utf-8'))
       if not r['fim_vigencia'] or r['fim_vigencia'] >= HOJE}

def palavras(t):
    return set(re.findall(r'[a-zà-ú]{5,}', (t or '').lower()))

for cod in sorted(set(rfc) | set(cff)):
    a, b = rfc.get(cod), cff.get(cod)
    rr = red.get(a[3], {}) if a else {}
    cst_a = sit.get(a[2]) if a else None
    det = []
    if not a: det.append('ausente na calculadora da Receita')
    if not b: det.append('ausente no Conformidade Facil')
    if a and b:
        if cst_a != b['cst']: det.append(f'CST {cst_a} x {b["cst"]}')
        if rr.get(2, 0) != float(b['reducao_cbs_pct'] or 0) or rr.get(3, 0) != float(b['reducao_ibs_pct'] or 0):
            det.append(f'reducao Receita {rr.get(2,0):g}%/{rr.get(3,0):g}% x CFF {b["reducao_cbs_pct"]}%/{b["reducao_ibs_pct"]}%')
        pa, pb = palavras(a[1]), palavras(b['nome'])
        if pa and not (pa & pb): det.append('descricoes sem termo em comum - conferir na IT oficial')
    situacao = 'concorda' if not det else 'diverge'
    db.execute('INSERT INTO cclasstrib VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
               (cod, cst_a or (b and b['cst']), a and a[1], b and b['nome'], rr.get(2), rr.get(3),
                b and float(b['reducao_cbs_pct'] or 0), b and float(b['reducao_ibs_pct'] or 0),
                fund.get(a[3]) if a else None, b and b['legislacao'], situacao, '; '.join(det)))
    db.execute('INSERT INTO confronto VALUES (?,?,?,?,?,?)',
               ('cClassTrib', cod, situacao, 'receita_calc,cff', '; '.join(det), HOJE))

# NCM: Receita x Siscomex
rfn = {r[0] for r in rf.execute('select NCM_CD from NCM where NCM_FIM_VIGENCIA>=?', (HOJE,)) if len(r[0]) == 8}
sis = {r[0] for r in db.execute("select codigo from ncm where length(codigo)=8 and (fim is null or fim>=?)", (HOJE,))}
for cod in rfn ^ sis:
    db.execute('INSERT INTO confronto VALUES (?,?,?,?,?,?)',
               ('NCM', cod, 'diverge', 'receita_calc,siscomex', 'so na Receita' if cod in rfn else 'so no Siscomex', HOJE))
db.execute('INSERT INTO confronto VALUES (?,?,?,?,?,?)',
           ('NCM', '*', 'concorda' if rfn == sis else 'diverge', 'receita_calc,siscomex', f'{len(rfn & sis)} codigos iguais', HOJE))

db.commit()
for t in ['norma', 'dispositivo', 'ncm', 'cest', 'cclasstrib', 'cclasstrib_ncm', 'confronto']:
    print(f'{t:16} {db.execute(f"select count(*) from {t}").fetchone()[0]:>8}')
print('cClassTrib divergentes:', db.execute("select count(*) from cclasstrib where confronto='diverge'").fetchone()[0])
