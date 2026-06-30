# AI Investment Brief — Groq Adapter v2

## Estado

Este adapter continua experimental e isolado.

Ainda não está ligado ao `server.py`, ao endpoint autenticado ou ao botão do
frontend. A feature flag normal continua desativada.

## Provider e modelo

- Provider: `groq`
- Modelo recomendado: `openai/gpt-oss-120b`
- Endpoint: `https://api.groq.com/openai/v1/chat/completions`
- Secret: `THESISOS_AI_BRIEF_GROQ_API_KEY`

Para reduzir falhas ocasionais em gerações longas, o pedido usa:

- `reasoning_effort: low`;
- `include_reasoning: false`;
- `max_completion_tokens: 3200`;
- retry de erros HTTP `422` no provider normal.

A integração passou da Groq Responses API para Chat Completions porque:

- autenticação, modelo e inferência foram validados;
- a Responses API mínima funcionou;
- o pedido completo ThesisOS recebeu `403`;
- o mesmo grounding e o mesmo JSON Schema estrito funcionaram através de
  Chat Completions com `HTTP 200` e contrato válido.

O adapter continua a reutilizar o construtor de pedido do adapter OpenAI para
preservar as instruções, o grounding, o SHA-256 e o schema ThesisOS. Apenas o
formato HTTP específico da Groq é convertido para Chat Completions.

## Segurança

- chave apenas em Replit Secrets;
- chave fora do body, logs e `repr`;
- grounding ThesisOS validado por SHA-256;
- Structured Outputs com `strict=true`;
- nenhum tool, browser ou function call;
- o pedido Chat Completions não envia o parâmetro `store`;
- uma chamada HTTP real no máximo por processo de smoke;
- CI apenas com transportes falsos;
- conteúdo integral do brief não é impresso pelo smoke.

Manter Zero Data Retention ativado nos Data Controls da organização GroqCloud.

## Dry-run

Sem rede:

```bash
python3 ai_brief_groq_smoke.py --dry-run
```

Resultado esperado:

- `network_calls: 0`
- `provider: groq`
- `model: openai/gpt-oss-120b`
- `strict_structured_outputs: true`
- `tools_enabled: false`
- `store_parameter_sent: false`
- `runtime_activated: false`

## Uma chamada real

Com o Replit Secret `THESISOS_AI_BRIEF_GROQ_API_KEY` configurado:

```bash
THESISOS_AI_BRIEF_MODEL=openai/gpt-oss-120b \
THESISOS_AI_BRIEF_GROQ_SMOKE_CONFIRM=RUN_ONE_GROQ_CALL \
python3 ai_brief_groq_smoke.py --live
```

Um resultado bem-sucedido deve apresentar:

- `network_calls: 1`
- `transport_attempts: 1`
- `status: ready` ou `partial`
- `schema_valid: true`
- `request.store_parameter_sent: false`
- `request.strict: true`
- `request.tools_enabled: false`
- `success: true`
- `runtime_activated: false`

## Próxima decisão

Só depois do smoke real passar novamente deverá ser criado um patch separado
para ligar `groq` ao runtime. Essa integração deve continuar desativada por
defeito e preservar o adapter OpenAI.
