# Consulta Srocha

Base de consulta tributária com **confronto entre fontes oficiais** (Srocha Consultoria e Contabilidade).

| Parte | O que faz |
|---|---|
| `sync.py` | Todo dia às 05:00 (Brasília): atualiza as fontes, gera a base oficial da Receita e remonta a base de consulta |
| `ingest.py` | Monta a base unificada (normas federais compiladas, Confaz, NCM/TIPI/CEST, cClassTrib) e registra o confronto entre fontes |
| `app.py` | API com login própria, consumida pela tela HTML |
| `docker-compose.yml` | Projeto da VPS: `base`, `calculadora` (Calculadora RTC oficial da Receita Federal) e `api` |

## Fontes

- Planalto e Confaz via [Matheuscfs/legislacao-tributaria](https://github.com/Matheuscfs/legislacao-tributaria) (MIT, captura diária)
- [ReceitaFederal/calculadora-rtc-regime-regular](https://github.com/ReceitaFederal/calculadora-rtc-regime-regular) (MIT)
- Portal Conformidade Fácil (cClassTrib por NCM/NBS)
- [brunoqualhato/tributos](https://github.com/brunoqualhato/tributos): **somente como catálogo**. Os textos não são compilados (RICMS-SP de 2000; LC 214 sem a LC 227/2026)

Os dados são públicos: texto de lei não tem direito autoral (Lei 9.610/98, art. 8º, IV).
