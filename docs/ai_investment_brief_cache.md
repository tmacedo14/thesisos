# AI Investment Brief — Cache & Observability v1

## Objetivo

Esta camada reduz chamadas repetidas ao provider quando o mesmo grounding
bundle é processado com o mesmo provider e modelo.

O cache é local ao processo e não substitui uma solução distribuída.

## Chave

A chave SHA-256 inclui:

- namespace e versão da camada de cache;
- versão do grounding;
- hash integral do grounding bundle;
- provider;
- modelo.

Qualquer alteração nos dados, provider ou modelo produz uma chave
diferente.

## Política

Valores padrão:

- cache ativo;
- TTL de 900 segundos;
- máximo de 128 entradas;
- política LRU;
- limite máximo configurável de 512 entradas;
- TTL máximo configurável de 86 400 segundos.

Apenas briefs com estado `ready` ou `partial` são guardados.

Não são guardados:

- `disabled`;
- `insufficient_data`;
- `provider_unavailable`;
- `generation_failed`.

## Segurança

O valor é copiado na escrita e na leitura para impedir mutações externas.

A chave inclui o hash integral do grounding, que por sua vez inclui o
contexto normalizado utilizado na decisão. Assim, dados diferentes não
partilham uma entrada.

Os logs nunca incluem:

- grounding ou analysis payload;
- prompts;
- respostas brutas;
- API keys;
- Authorization headers;
- user id;
- mensagens de erro externas.

## Observabilidade

Quando `THESISOS_AI_BRIEF_LOG_EVENTS=true`, são emitidas linhas JSON
server-side com campos limitados:

- evento;
- estado final;
- provider;
- modelo;
- prefixo do grounding hash;
- prefixo da chave de cache;
- duração;
- código de erro normalizado.

## Configuração

```text
THESISOS_AI_BRIEF_CACHE_ENABLED=true
THESISOS_AI_BRIEF_CACHE_TTL_SECONDS=900
THESISOS_AI_BRIEF_CACHE_MAX_ENTRIES=128
THESISOS_AI_BRIEF_LOG_EVENTS=false
```

## Limitações

- não é partilhado entre instâncias;
- perde-se quando o processo reinicia;
- as métricas são locais ao processo;
- não existe invalidação distribuída;
- não substitui controlo de custos e quotas do provider.

Uma fase futura poderá trocar a implementação por Redis ou armazenamento
equivalente sem alterar o contrato do AI Investment Brief.
