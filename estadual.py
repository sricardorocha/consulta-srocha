#!/usr/bin/env python3
"""Consulta Srocha - RICMS de SP e do ES (Fase 3, etapa 1).

baixar(pasta)  -> baixa as fontes oficiais (uma vez por dia, chamado pelo sync.py)
normas(pasta)  -> devolve [(norma, [(rotulo, texto), ...]), ...] para o ingest.py

SP: pagina "RICMS/2000 - Integral" da Sefaz-SP. As redacoes anteriores vem marcadas
    com class="revogado" e sao descartadas; fica so o texto vigente.
ES: "RICMS Consolidado - Texto - PARA CONSULTA" e os anexos em PDF da pagina
    "Legislacao em Destaque" da Sefaz-ES. As redacoes anteriores vem no proprio
    texto ("Redacao anterior...", "Redacao original...", "efeitos de X a Y:") e
    sao retiradas por regra.
Se o download falhar, os arquivos do dia anterior continuam valendo.
"""
import html, json, os, re, shutil, subprocess, time, unicodedata, urllib.parse, urllib.request

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131 Safari/537.36 ConsultaSrocha/1.0'
SP_BASE = 'https://legislacao.fazenda.sp.gov.br/Paginas/'
SP_INTEGRAL = SP_BASE + 'textoricms.aspx'
SP_INDICE = SP_BASE + 'ind_art.aspx'
ES_PAGINA = 'https://sefaz.es.gov.br/legislacao-destaque'
ROMANOS = 'C|XC|L|XL|X|IX|V|IV|I'


def _get(url, destino, timeout=180):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        dados = r.read()
    if len(dados) < 1000:
        raise RuntimeError(f'resposta pequena demais ({len(dados)} bytes): {url}')
    tmp = destino + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(dados)
    os.replace(tmp, destino)
    return len(dados)


def baixar(pasta):
    """Baixa tudo; devolve dict com o que foi baixado e os erros (nunca levanta)."""
    os.makedirs(os.path.join(pasta, 'es_anexos'), exist_ok=True)
    st = {'quando': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'arquivos': {}, 'erros': []}
    for nome, url in (('sp_integral.html', SP_INTEGRAL), ('sp_indice.html', SP_INDICE), ('es_pagina.html', ES_PAGINA)):
        try:
            st['arquivos'][nome] = _get(url, os.path.join(pasta, nome))
        except Exception as e:
            st['erros'].append(f'{nome}: {e}')
    try:
        pag = open(os.path.join(pasta, 'es_pagina.html'), encoding='utf-8', errors='replace').read()
        links = [html.unescape(h) for h in re.findall(r'href="([^"]+?\.pdf)(?:\?[^"]*)?"', pag, re.I)]
        texto = next((h for h in links if 'ricms-consolidado-texto' in h.lower()), None)
        anexos = {}
        for h in links:
            m = re.search(r'Anexos/ANEXO%20([A-Z]+(?:-[A-Z])?)\.pdf$', h, re.I)
            if m and 'Legisla' in h:
                anexos[m.group(1).upper()] = h
        if texto:
            st['arquivos']['es_texto.pdf'] = _get(urllib.parse.urljoin(ES_PAGINA, texto), os.path.join(pasta, 'es_texto.pdf'), 300)
            st['es_texto_url'] = urllib.parse.urljoin(ES_PAGINA, texto)
        else:
            st['erros'].append('es: link do RICMS consolidado nao encontrado na pagina')
        ok = 0
        for rom, h in anexos.items():
            try:
                _get(urllib.parse.urljoin(ES_PAGINA, h), os.path.join(pasta, 'es_anexos', rom + '.pdf'), 120)
                ok += 1
            except Exception as e:
                st['erros'].append(f'es anexo {rom}: {e}')
            time.sleep(0.3)
        st['es_anexos'] = ok
        json.dump({k: urllib.parse.urljoin(ES_PAGINA, v) for k, v in anexos.items()},
                  open(os.path.join(pasta, 'es_anexos.json'), 'w'), ensure_ascii=False)
    except Exception as e:
        st['erros'].append(f'es: {e}')
    json.dump(st, open(os.path.join(pasta, 'estadual_status.json'), 'w'), ensure_ascii=False, indent=1)
    return st


# ---------------------------------------------------------------- utilidades
def _limpa(t):
    t = html.unescape(t).replace('\xa0', ' ')
    t = re.sub(r'[ \t\r\f\v]+', ' ', t)
    return '\n'.join(l.strip() for l in t.split('\n') if l.strip())


def _html_para_linhas(h):
    h = re.sub(r'(?is)<(script|style)\b.*?</\1>', ' ', h)
    h = re.sub(r'(?is)<(p|td)\b[^>]*class="[^"]*revogad[^"]*"[^>]*>.*?</\1>', ' ', h)
    # notas (remissoes e textos de outros atos citados) nunca abrem artigo novo
    h = re.sub(r'(?i)(<p\b[^>]*class="[^"]*(?:\bnota\b|NotaRemissiva)[^"]*"[^>]*>)', r'\1\n§NOTA§ ', h)
    h = re.sub(r'(?i)<br\s*/?>|</(p|div|li|tr|h\d|table)>', '\n', h)
    h = re.sub(r'(?i)</td>', ' | ', h)
    h = re.sub(r'<[^>]+>', ' ', h)
    return _limpa(h).split('\n')


def _pdf_texto(arq):
    if not shutil.which('pdftotext'):
        try:
            subprocess.run(['apt-get', 'install', '-y', '-qq', 'poppler-utils'], capture_output=True, timeout=600)
        except Exception:
            pass
    if shutil.which('pdftotext'):
        r = subprocess.run(['pdftotext', '-enc', 'UTF-8', arq, '-'], capture_output=True, timeout=900)
        return unicodedata.normalize('NFC', r.stdout.decode('utf-8', 'replace'))
    try:
        import pypdf
    except ImportError:
        subprocess.run(['pip', 'install', '-q', 'pypdf'], capture_output=True, timeout=600)
        import pypdf
    return unicodedata.normalize('NFC', '\n'.join((p.extract_text() or '') for p in pypdf.PdfReader(arq).pages))


def _romano_int(r):
    v = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100}
    n = 0
    for i, ch in enumerate(r):
        a = v.get(ch, 0)
        n += -a if i + 1 < len(r) and v.get(r[i + 1], 0) > a else a
    return n


# ---------------------------------------------------------------- SP
SP_ART = re.compile(r'^Artigo\s+(\d+)(?:\s*[º°o])?(-[A-Z]{1,2})?\b')
SP_ANEXO = re.compile(r'^ANEXO\s+([IVXLC]+)\s*[-–]\s*(.{3,120})$')


def _sp_mapa_paginas(pasta):
    """Artigo do corpo -> pagina propria no site (ex.: 52 -> art052.aspx)."""
    try:
        h = open(os.path.join(pasta, 'sp_indice.html'), encoding='utf-8', errors='replace').read()
    except OSError:
        return []
    pares = []
    for href, txt in re.findall(r'href="[^"]*?(art\d+[a-z0-9_]*)\.aspx"[^>]*>\s*(?:<[^>]+>\s*)*([^<]{1,30})<', h, re.I):
        m = re.match(r'\s*(\d+)', html.unescape(txt))
        if m:
            pares.append((int(m.group(1)), SP_BASE + href + '.aspx'))
    return sorted(set(pares))


def _sp_url(mapa, n):
    url = SP_INTEGRAL
    for ini, u in mapa:
        if ini <= n:
            url = u
        else:
            break
    return url


def normas_sp(pasta):
    arq = os.path.join(pasta, 'sp_integral.html')
    if not os.path.exists(arq):
        return []
    h = open(arq, encoding='utf-8', errors='replace').read()
    ini = h.find('class="ms-rtestate-field"')
    fim = h.find('ControlWrapper_RichHtmlField', ini + 100)
    linhas = _html_para_linhas(h[ini if ini > 0 else 0: fim if fim > 0 else len(h)])
    mapa = _sp_mapa_paginas(pasta)
    alterado = time.strftime('%Y-%m-%d', time.localtime(os.path.getmtime(arq)))
    blocos = {}          # chave da norma -> [ (rotulo, [linhas]) ]
    titulos = {'corpo': 'RICMS/SP'}
    atual, ddtt, art, buf = 'corpo', False, None, []
    vistos = {}

    def fecha():
        if art and buf:
            blocos.setdefault(atual, []).append((art, buf[:]))

    partes = {}
    for l in linhas:
        if re.fullmatch(r'[|\s\u200b]*', l) or (len(l) < 40 and l.rstrip().endswith('|')):
            continue          # sumario/indice e celulas vazias
        if l.startswith('§NOTA§'):
            l = l[6:].strip()
            if art and l:
                buf.append(l)
            continue
        ma = SP_ANEXO.match(l)
        if ma and not l.endswith(':'):
            chave = 'anexo-' + ma.group(1).lower()
            if chave != atual:
                fecha()
                atual, art, buf, ddtt = chave, None, [], False
                titulos[chave] = f'RICMS/SP – Anexo {ma.group(1)} – ' + re.sub(r'\s*\(.*$', '', ma.group(2)).strip().capitalize()
            continue
        if atual == 'corpo' and re.match(r'^T[ÍI]TULO\s+II\s*[-–]\s*DAS\s+DISPOSI[CÇ][OÕ]ES\s+TRANSIT', l, re.I):
            ddtt = True
            continue
        mm = SP_ART.match(l)
        if mm:
            num = mm.group(1) + (mm.group(2) or '')
            rot = ('DDTT art. ' if ddtt or '(DDTT)' in l[:40] else 'Art. ') + num
            fecha()
            art, buf = rot, [l]
            continue
        if not art and atual != 'corpo':
            partes[atual] = partes.get(atual, 0) + 1
            art, buf = f"Anexo {atual.split('-')[1].upper()} ({partes[atual]})", []
        if art:
            buf.append(l)
            if art.startswith('Anexo ') and sum(map(len, buf)) > 1500:
                fecha()
                art, buf = None, []
    fecha()

    saida = []
    for chave, arts in blocos.items():
        nid = 'sp:ricms' + ('' if chave == 'corpo' else ':' + chave)
        disp, ja = [], set()
        for rot, ls in arts:
            texto = '\n'.join(ls).strip()
            if (rot, texto[:200]) in ja or len(texto) < 15:
                continue
            ja.add((rot, texto[:200]))
            disp.append((rot, texto))
        if not disp:
            continue
        num0 = int(re.sub(r'\D.*', '', disp[0][0].split(' ')[-1]) or 0) if chave == 'corpo' else 0
        saida.append(({'id': nid, 'fonte_id': 'sefaz_sp', 'esfera': 'estadual', 'uf': 'SP', 'tipo': 'Regulamento',
                       'numero': '45.490/2000', 'data': '2000-11-30', 'titulo': titulos.get(chave, nid),
                       'ementa': 'Regulamento do ICMS de São Paulo (Decreto 45.490/2000), texto vigente da Sefaz-SP',
                       'url_oficial': SP_INTEGRAL, 'alterado_em': alterado},
                      [(r, t, _sp_url(mapa, int(re.sub(r'\D.*', '', r.split(' ')[-1]) or 0)) if chave == 'corpo' and r.startswith('Art.') else SP_INTEGRAL)
                       for r, t in disp]))
    return saida


# ---------------------------------------------------------------- ES
NIVEL = [(re.compile(r'^Art\.\s*\d'), 0), (re.compile(r'^(§\s*\d|Parágrafo único)'), 1),
         (re.compile(r'^[IVXLC]+\s*[-–]\s'), 2), (re.compile(r'^[a-z]\)\s'), 3), (re.compile(r'^\d+(\.\d+)*\.\s'), 4)]
NOTA = re.compile(r'^(Nova redação|Redação ant\w*|Redação original|Redações? dada|(Art\.?|Artigo|Inciso|Incisos|Alínea|Alíneas|'
                  r'Parágrafo|Parágrafos|§+|Item|Itens|Subitem|Caput|Anexo|Capítulo|Seção|Subseção|Título|Nota|Tabela|Quadro|'
                  r'Expressão|Redação)\b.{0,220}?(incluíd|acrescentad|acrescid|revogad|alterad|renumerad|suprimid|redação|efeitos))', re.I)
VELHA = re.compile(r'(Redação ant\w*|Redação original|efeitos\s+(de|desde)\s+\S+\s+(até|a)\s+\d|efeitos\s+até\s+\d)', re.I)
ES_ART = re.compile(r'^Art\.\s*(\d+(?:\.\d{3})?(?:\s*-\s*[A-Z]+)*)\s*\.?\s*[º°o]?\s*\.?\s(?!\s*(revogad|incluíd|acrescentad|alterad|renumerad|com\s+reda))')
LIXO = re.compile(r'^(\d{1,4}|Página \d+.*|RICMS/ES.*\d{2}\.\d{2}\.\d{2,4}.*)$')


def _nivel(l):
    for rx, n in NIVEL:
        if rx.match(l):
            return n
    return None


def _es_vigente(texto):
    """Tira as redacoes anteriores; junta notas quebradas em varias linhas."""
    linhas = [l.strip() for l in texto.replace('\f', '\n').split('\n')]
    linhas = [l for l in linhas if l and not LIXO.match(l)]
    out, pular, nivel_pulo, i = [], False, None, 0
    while i < len(linhas):
        l = linhas[i]
        if NOTA.match(l) and (not ES_ART.match(l) or re.search(r'(pel[oa]s?\s*(Decreto|Lei)|efeitos)', l, re.I)):
            if pular and nivel_pulo is not None:
                # nota dentro de um bloco antigo: so encerra o pulo se o proximo dispositivo estiver no mesmo nivel ou acima
                j = i + 1
                while j < len(linhas) and _nivel(linhas[j]) is None and j - i < 6:
                    j += 1
                nj = _nivel(linhas[j]) if j < len(linhas) else 0
                if nj is None or nj > nivel_pulo:
                    i += 1
                    continue
            nota = l
            while not nota.rstrip().endswith(':') and i + 1 < len(linhas) and len(nota) < 400 and _nivel(linhas[i + 1]) is None:
                i += 1
                nota += ' ' + linhas[i]
            sem_dois_pontos = i + 1 < len(linhas) and _nivel(linhas[i + 1]) is not None
            if nota.rstrip().endswith(':') or sem_dois_pontos:
                pular = bool(VELHA.search(nota))
                nivel_pulo = None
                if not pular:
                    out.append('(' + nota.rstrip(':').strip() + ')')
                i += 1
                continue
            l = nota
        if pular:
            n = _nivel(l)
            if nivel_pulo is None:
                nivel_pulo = n if n is not None else 9
                i += 1
                continue
            if n is not None and n <= nivel_pulo:
                pular = False
            else:
                i += 1
                continue
        out.append(l)
        i += 1
    return out


def _juntar(linhas):
    """Junta linhas quebradas pelo PDF em paragrafos."""
    paras = []
    for l in linhas:
        if paras and _nivel(l) is None and not l.startswith('(') and not re.match(r'^(TÍTULO|CAPÍTULO|SEÇÃO|SUBSEÇÃO|LIVRO)\b', l) \
                and not paras[-1].endswith(':') and not re.match(r'^[A-ZÁÉÍÓÚÂÊÔÃÕÇ ,\-]{6,}$', paras[-1]):
            paras[-1] += ' ' + l
        else:
            paras.append(l)
    return paras


def normas_es(pasta):
    saida = []
    st = {}
    try:
        st = json.load(open(os.path.join(pasta, 'estadual_status.json')))
    except Exception:
        pass
    arq = os.path.join(pasta, 'es_texto.pdf')
    if os.path.exists(arq):
        paras = _juntar(_es_vigente(_pdf_texto(arq)))
        disp, rot, buf = [], 'Preâmbulo', []
        for idx, p in enumerate(paras):
            m = ES_ART.match(p)
            if m:
                if buf:
                    # notas logo antes do artigo pertencem a ele
                    notas = []
                    while buf and buf[-1].startswith('('):
                        notas.insert(0, buf.pop())
                    if buf:
                        disp.append((rot, '\n'.join(buf)))
                    buf = notas
                rot = 'Art. ' + re.sub(r'\s+', '', m.group(1))
                if rot == 'Art. 1' and any(r == 'Art. 1' for r, _ in disp):
                    # o que veio antes era o decreto que aprova o regulamento
                    disp = [(('Decreto, ' + r.lower()) if r.startswith('Art.') else r, t) for r, t in disp]
            buf.append(p)
        if buf:
            disp.append((rot, '\n'.join(buf)))
        # no consolidado a redacao vigente vem primeiro; repeticoes do mesmo artigo sao redacoes antigas
        vistos, unicos = set(), []
        for r, t in disp:
            if r in vistos and r.startswith('Art.'):
                continue
            vistos.add(r)
            unicos.append((r, t))
        disp = unicos
        url = st.get('es_texto_url') or ES_PAGINA
        saida.append(({'id': 'es:ricms', 'fonte_id': 'sefaz_es', 'esfera': 'estadual', 'uf': 'ES', 'tipo': 'Regulamento',
                       'numero': '1.090-R/2002', 'data': '2002-10-25', 'titulo': 'RICMS/ES',
                       'ementa': 'Regulamento do ICMS do Espírito Santo (Decreto 1.090-R/2002), texto consolidado da Sefaz-ES',
                       'url_oficial': url, 'alterado_em': time.strftime('%Y-%m-%d', time.localtime(os.path.getmtime(arq)))},
                      [(r, t, url) for r, t in disp]))
    try:
        urls = json.load(open(os.path.join(pasta, 'es_anexos.json')))
    except Exception:
        urls = {}
    dir_an = os.path.join(pasta, 'es_anexos')
    if os.path.isdir(dir_an):
        for a in sorted(os.listdir(dir_an), key=lambda x: _romano_int(x.split('.')[0].split('-')[0])):
            if not a.endswith('.pdf'):
                continue
            rom = a[:-4]
            paras = _juntar(_es_vigente(_pdf_texto(os.path.join(dir_an, a))))
            if not paras:
                continue
            titulo = next((p for p in paras[:8] if not re.match(r'^(ANEXO\b|\()', p, re.I) and len(p) > 8), '')[:140]
            disp, buf, tam, parte = [], [], 0, 1
            for p in paras:
                buf.append(p)
                tam += len(p)
                if tam > 1500:
                    disp.append((f'Anexo {rom} ({parte})', '\n'.join(buf)))
                    buf, tam, parte = [], 0, parte + 1
            if buf:
                disp.append((f'Anexo {rom} ({parte})', '\n'.join(buf)))
            url = urls.get(rom, ES_PAGINA)
            saida.append(({'id': 'es:ricms:anexo-' + rom.lower(), 'fonte_id': 'sefaz_es', 'esfera': 'estadual', 'uf': 'ES',
                           'tipo': 'Regulamento', 'numero': '1.090-R/2002', 'data': '2002-10-25',
                           'titulo': f'RICMS/ES – Anexo {rom}', 'ementa': titulo, 'url_oficial': url,
                           'alterado_em': time.strftime('%Y-%m-%d', time.localtime(os.path.getmtime(os.path.join(dir_an, a))))},
                          [(r, t, url) for r, t in disp]))
    return saida


def normas(pasta):
    out = []
    for f in (normas_sp, normas_es):
        try:
            out += f(pasta)
        except Exception as e:
            print('estadual:', f.__name__, 'falhou:', e, flush=True)
    return out


if __name__ == '__main__':
    import sys
    pasta = sys.argv[2] if len(sys.argv) > 2 else '.'
    if sys.argv[1] == 'baixar':
        print(json.dumps(baixar(pasta), ensure_ascii=False, indent=1))
    else:
        for n, d in normas(pasta):
            print(f"{n['id']:28} {len(d):5} disp  {n['titulo'][:70]}")
