#!/usr/bin/env python3
"""Consulta Srocha - rotina de atualizacao das fontes (roda no container 'base').

Todo dia (05:00 de Brasilia) e na partida do container:
  1. atualiza os repositorios-fonte do GitHub;
  2. gera a base oficial da Receita (scripts flyway) em SQLite;
  3. roda o ingest.py e troca a base de consulta de forma atomica;
  4. grava /data/status.json (o que foi atualizado, quando, e erros).
"""
import datetime, glob, json, os, re, shutil, sqlite3, subprocess, sys, time, traceback

DATA = os.environ.get('DATA_DIR', '/data')
SRC = os.path.join(DATA, 'fontes')
AQUI = os.path.dirname(os.path.abspath(__file__))
REPOS = {
    'Matheuscfs_legislacao-tributaria': 'https://github.com/Matheuscfs/legislacao-tributaria.git',
    'brunoqualhato_tributos': 'https://github.com/brunoqualhato/tributos.git',
    'rf_calculadora-rtc-regime-regular': 'https://github.com/ReceitaFederal/calculadora-rtc-regime-regular.git',
}
BRT = datetime.timezone(datetime.timedelta(hours=-3))


def agora():
    return datetime.datetime.now(BRT).isoformat(timespec='seconds')


def git(dest, url):
    if os.path.isdir(os.path.join(dest, '.git')):
        subprocess.run(['git', '-C', dest, 'fetch', '-q', '--depth', '1', 'origin'], check=True, timeout=900)
        subprocess.run(['git', '-C', dest, 'reset', '-q', '--hard', 'FETCH_HEAD'], check=True, timeout=300)
    else:
        subprocess.run(['git', 'clone', '-q', '--depth', '1', url, dest], check=True, timeout=1800)
    out = subprocess.check_output(['git', '-C', dest, 'log', '-1', '--format=%h|%cI|%s'], text=True).strip()
    h, d, s = out.split('|', 2)
    return {'commit': h, 'data': d, 'mensagem': s[:160]}


def base_receita(destino):
    sql = os.path.join(SRC, 'rf_calculadora-rtc-regime-regular', 'flyway', 'sql')
    tmp = destino + '.tmp'
    if os.path.exists(tmp):
        os.remove(tmp)
    c = sqlite3.connect(tmp)
    erros = []
    arqs = [os.path.join(sql, 'criacao', 'beforeMigrate.sql'), os.path.join(sql, 'criacao', 'B0001__sistema_tributario_completo.sql')]
    arqs += sorted(glob.glob(os.path.join(sql, 'manutencao', 'V*.sql')), key=lambda p: int(re.search(r'V(\d+)', os.path.basename(p)).group(1)))
    arqs.append(os.path.join(sql, 'criacao', 'afterMigrate.sql'))
    for f in arqs:
        if not os.path.exists(f):
            continue
        s = re.sub(r'(?i)PRAGMA\s+foreign_keys\s*=\s*ON\s*;', '', open(f, encoding='utf-8').read())
        c.execute('PRAGMA foreign_keys=OFF')
        try:
            c.executescript(s)
        except Exception as e:
            erros.append(f'{os.path.basename(f)}: {e}')
    c.commit()
    versao = c.execute('select VRBD_VERSAO_BASE_DADO, VRBD_DATA, VRBD_DESCRICAO from VERSAO_BASE_DADO order by VRBD_ID desc limit 1').fetchone()
    c.close()
    os.replace(tmp, destino)
    return {'erros': erros, 'ultima_versao': [str(x) for x in (versao or [])]}


def rodada():
    st = {'inicio': agora(), 'fontes': {}, 'erros': []}
    os.makedirs(SRC, exist_ok=True)
    for nome, url in REPOS.items():
        try:
            st['fontes'][nome] = git(os.path.join(SRC, nome), url)
        except Exception as e:
            st['erros'].append(f'{nome}: {e}')
    try:
        st['base_receita'] = base_receita(os.path.join(DATA, 'rf_oficial.db'))
    except Exception:
        st['erros'].append('base_receita: ' + traceback.format_exc()[-400:])
    try:
        tmp = os.path.join(DATA, 'consulta_srocha.db.tmp')
        r = subprocess.run([sys.executable, os.path.join(AQUI, 'ingest.py'), SRC, tmp, os.path.join(DATA, 'rf_oficial.db')],
                           capture_output=True, text=True, timeout=3600)
        st['ingest'] = r.stdout[-1500:]
        if r.returncode != 0:
            raise RuntimeError(r.stderr[-800:])
        os.replace(tmp, os.path.join(DATA, 'consulta_srocha.db'))
    except Exception as e:
        st['erros'].append(f'ingest: {e}')
    st['fim'] = agora()
    st['situacao'] = 'ok' if not st['erros'] else 'com_erros'
    with open(os.path.join(DATA, 'status.json'), 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: st[k] for k in ('inicio', 'fim', 'situacao', 'erros')}, ensure_ascii=False), flush=True)


def proxima_execucao():
    n = datetime.datetime.now(BRT)
    alvo = n.replace(hour=5, minute=0, second=0, microsecond=0)
    if alvo <= n:
        alvo += datetime.timedelta(days=1)
    return (alvo - n).total_seconds()


if __name__ == '__main__':
    while True:
        try:
            rodada()
        except Exception:
            traceback.print_exc()
        time.sleep(proxima_execucao())
