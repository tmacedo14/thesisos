# AI Investment Brief — Live Smoke Harness v1

## Objetivo

Validar uma única integração real com a OpenAI Responses API sem ativar o
provider no runtime normal, sem criar endpoints e sem expor o secret.

O harness usa o grounding determinístico já versionado em:

`contracts/examples/ai_investment_brief_grounding.ready.json`

## Garantias

- `--dry-run` é o modo predefinido e nunca toca na rede.
- `--live` exige confirmação literal e um Replit Secret.
- Um wrapper permite no máximo uma chamada HTTP real por processo.
- Uma tentativa de retry é bloqueada antes de chegar novamente à rede.
- `store=false` é forçado no pedido do smoke test.
- Não existem tools, browsing ou function calls.
- O adapter mantém `text.format` com JSON Schema estrito.
- O grounding é validado por SHA-256 antes da chamada.
- O resultado é validado contra os campos, estados e ações do contrato v1.
- A saída não contém prompt, grounding, conteúdo gerado ou chave.
- O CI usa apenas transporte falso e nunca recebe o secret.
- `THESISOS_AI_BRIEF_ENABLED` pode continuar desativado no ambiente normal.

## Replit Secret

Criar apenas no painel Secrets do Replit:

`THESISOS_AI_BRIEF_API_KEY`

Não escrever a chave no terminal, ficheiro `.env`, código, log ou chat.

O dry-run usa apenas o identificador local `dry-run-model`, que nunca é
enviado à rede. O modo live exige que `THESISOS_AI_BRIEF_MODEL` seja definido
explicitamente para evitar escolher silenciosamente um modelo real.

## Dry-run

```bash
python3 ai_brief_live_smoke.py --dry-run
```

Deve indicar:

- `network_calls: 0`
- `strict_structured_outputs: true`
- `tools_enabled: false`
- `store_will_be_forced_false: true`
- `actual_request_limit: 1`

## Uma chamada real

Executar apenas depois de rever o dry-run:

```bash
THESISOS_AI_BRIEF_MODEL=<MODEL_ID> \
THESISOS_AI_BRIEF_LIVE_SMOKE_CONFIRM=RUN_ONE_REAL_CALL \
python3 ai_brief_live_smoke.py --live
```

A confirmação é aplicada apenas a esse processo. Não deve ser guardada como
variável permanente.

## Saída permitida

O JSON final contém apenas:

- status;
- ação e confiança;
- provider e modelo;
- response id e request id;
- usage/tokens disponíveis;
- tempo decorrido;
- prefixo do hash;
- metadados não sensíveis do pedido;
- resultado da validação.

Não contém o resumo executivo nem qualquer outra secção gerada.

## Resultado esperado

Um teste bem-sucedido termina com:

- `network_calls: 1`
- `status: ready` ou `partial`
- `schema_valid: true`
- `request.store: false`
- `success: true`

Qualquer outro resultado termina com código diferente de zero.

## Depois do teste

Confirmar o consumo no dashboard da OpenAI. O runtime normal e o botão da
Beta permanecem desativados até existir uma decisão explícita de ativação.
