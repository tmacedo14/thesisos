# AI Investment Brief — Groq Adapter v1

## Estado

Este adapter é experimental e isolado.

Ainda não está ligado ao `server.py`, ao endpoint autenticado ou ao botão do
frontend. A feature flag normal continua desativada.

## Provider e modelo

- Provider: `groq`
- Modelo recomendado: `openai/gpt-oss-120b`
- Endpoint: `https://api.groq.com/openai/v1/responses`
- Secret: `THESISOS_AI_BRIEF_GROQ_API_KEY`

O adapter reutiliza o pedido, schema, parsing, retries e normalização já
testados pelo adapter OpenAI, mas redireciona o transporte para o endpoint
Groq e normaliza o provider final como `groq`.

## Segurança

- chave apenas em Replit Secrets;
- chave fora do body, logs e `repr`;
- grounding ThesisOS validado por SHA-256;
- Structured Outputs com `strict=true`;
- nenhum tool, browser ou function call;
- `store=false` forçado no smoke test;
- uma chamada HTTP real no máximo por processo;
- CI apenas com transportes falsos;
- conteúdo integral do brief não é impresso pelo smoke.

Antes de utilizar dados reais, ativar Zero Data Retention nos Data Controls
da organização GroqCloud.

## Dry-run

Sem chave e sem rede:

```bash
python3 ai_brief_groq_smoke.py --dry-run
```

Resultado esperado:

- `network_calls: 0`
- `provider: groq`
- `model: openai/gpt-oss-120b`
- `strict_structured_outputs: true`
- `tools_enabled: false`
- `runtime_activated: false`

## Uma chamada real

Depois de criar o Replit Secret:

`THESISOS_AI_BRIEF_GROQ_API_KEY`

executar:

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
- `request.store: false`
- `success: true`
- `runtime_activated: false`

## Próxima decisão

Só depois do smoke real passar deverá ser criado o patch separado que:

1. reconhece o secret Groq em `ProviderConfig`;
2. adiciona `groq` ao provider registry;
3. liga a factory Groq ao runtime;
4. mantém o provider desativado por defeito;
5. testa o botão numa Beta controlada.
