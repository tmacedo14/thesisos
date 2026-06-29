# AI Investment Brief — OpenAI Responses Adapter v1

## Objetivo

Este módulo implementa o primeiro provider HTTP concreto para o AI
Investment Brief usando a OpenAI Responses API.

A feature continua desativada por defeito. Os testes não fazem chamadas
reais e não consomem créditos.

## Configuração

Variáveis server-side:

- `THESISOS_AI_BRIEF_ENABLED=true`;
- `THESISOS_AI_BRIEF_PROVIDER=openai`;
- `THESISOS_AI_BRIEF_MODEL=<modelo disponível na conta>`;
- `THESISOS_AI_BRIEF_API_KEY=<segredo>`;
- `THESISOS_AI_BRIEF_TIMEOUT_SECONDS=20`.

O modelo não é fixado no código para permitir alterações sem novo deploy.

## Pedido

O adapter envia:

- o modelo configurado;
- instruções de sistema fixas;
- apenas o grounding bundle validado;
- Structured Outputs através de JSON Schema estrito;
- limite de 1 800 output tokens.

Não são ativadas ferramentas, web search, file search ou function
calling.

## Proteções

- endpoint fixo, não configurável pelo cliente;
- API key apenas no header Authorization;
- chave omitida do `repr`;
- grounding máximo de 200 000 bytes;
- strings do bundle tratadas como dados não confiáveis;
- output limitado por schema fechado;
- raw response não entra no brief final;
- erros externos truncados;
- nenhuma resposta ou prompt é registado.

## Retry

São permitidas no máximo três tentativas para:

- HTTP 408;
- HTTP 409;
- HTTP 429;
- HTTP 500;
- HTTP 502;
- HTTP 503;
- HTTP 504;
- falhas transitórias de rede.

O header `Retry-After` é respeitado até dois segundos. Erros de
autenticação ou pedidos inválidos não são repetidos.

Antes de uma chamada, o runtime consulta o cache local usando o hash
do grounding, provider e modelo. Apenas respostas `ready` ou `partial`
são reutilizadas.

## Estados finais

O adapter reutiliza a normalização existente:

- indisponibilidade transitória → `provider_unavailable`;
- resposta inválida, recusa ou autenticação inválida →
  `generation_failed`;
- sucesso completo → `ready`;
- sucesso com cobertura opcional em falta → `partial`.

## Ativação segura

Antes de ativar em produção:

1. definir um modelo válido;
2. guardar a API key nos Secrets do Replit;
3. manter a feature desligada durante o primeiro deploy;
4. executar um teste manual controlado;
5. confirmar custos, latência e logs;
6. só depois definir `THESISOS_AI_BRIEF_ENABLED=true`.
