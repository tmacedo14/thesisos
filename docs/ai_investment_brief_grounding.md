# AI Investment Brief — Grounding Bundle v1.0

## Objetivo

Este módulo converte um payload interno de análise do ThesisOS num
bundle determinístico, limitado e auditável antes de qualquer chamada
futura a um provider de IA.

Nesta fase não existe:

- chamada a modelos;
- endpoint público;
- alteração do frontend;
- pesquisa autónoma na internet;
- persistência de prompts ou respostas.

## Propriedades do bundle

O bundle contém apenas:

- identidade normalizada do ativo;
- secções ThesisOS explicitamente permitidas;
- cobertura e campos em falta;
- referências para as evidências incluídas;
- tipos de fontes;
- hash SHA-256 canónico.

As secções permitidas são:

- `fundamentals`;
- `framework`;
- `valuation`;
- `technical`;
- `evidence`;
- `portfolio`;
- `market_context`.

Qualquer outra secção do payload original é ignorada.

## Segurança e minimização

O sanitizador remove chaves sensíveis como:

- API keys;
- passwords;
- tokens;
- cookies;
- authorization headers;
- prompts;
- respostas brutas;
- headers de pedidos externos.

Existem ainda limites determinísticos de:

- profundidade;
- tamanho de listas;
- tamanho de strings.

## Estados de cobertura

### `ready`

Os campos críticos e opcionais estão presentes.

### `partial`

Os campos críticos estão presentes, mas faltam secções opcionais.
Uma geração futura pode ser permitida, desde que a incerteza seja
apresentada explicitamente.

### `insufficient_data`

Faltam campos críticos. O provider não deve ser chamado.

Para ações, ETFs e fundos, `fundamentals` é uma secção crítica.
Para todos os ativos, `asset.symbol`, o tipo declarado do ativo e
`framework` são críticos.

## Determinismo

O módulo não usa o relógio atual, rede, aleatoriedade ou estado global
mutável para construir o bundle.

O mesmo payload lógico produz o mesmo `payload_sha256`, mesmo quando
a ordem das chaves do dicionário muda.

O hash cobre todo o bundle exceto o próprio campo
`payload_sha256`.

## Integração futura

A função `brief_grounding_metadata()` produz o objeto `grounding`
compatível com o contrato AI Investment Brief v1.0.

A integração prevista é:

1. `build_analysis_payload()` produz a análise ThesisOS;
2. `build_grounding_bundle()` aplica whitelist e sanitização;
3. cobertura insuficiente bloqueia a chamada ao provider;
4. o adapter recebe exclusivamente o bundle validado;
5. a resposta final inclui o hash e as referências de evidência.

Nesta fase, o módulo permanece isolado de `server.py` para permitir
validação sem regressão no runtime Alpha.
