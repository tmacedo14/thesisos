# AI Investment Brief Frontend v1

## Objetivo

Ligar a interface de análise ao runtime server-side do AI Investment Brief sem
expor chaves, sem aceitar grounding produzido pelo cliente e sem gerar conteúdo
automaticamente.

## Fluxo

1. O utilizador carrega uma análise integrada.
2. O frontend consulta `/api/ai-brief/config`.
3. A geração só acontece após clique em **Investment Brief**.
4. `cloudRequest(..., true)` obtém a sessão Supabase e envia o bearer token.
5. O corpo contém apenas `identifier`, `base_currency`, `exchange` e `ticker`.
6. O servidor resolve novamente a análise e constrói o grounding.
7. A resposta existe apenas na análise atual em memória.

## Segurança

- A chave OpenAI nunca aparece no browser.
- O frontend não envia grounding nem o payload completo da análise.
- Conteúdo do modelo é inserido com `createElement` e `textContent`.
- Não existe `innerHTML` no renderer do AI Brief.
- Tokens, pedidos e respostas não são guardados em `localStorage`.
- O brief não integra a sincronização cloud.
- Um `AbortController` cancela pedidos obsoletos.
- Uma nova pesquisa invalida o brief anterior.
- O provider permanece desativado por defeito.

## Estados explícitos

- `ready`
- `partial`
- `insufficient_data`
- `disabled`
- `provider_unavailable`
- `generation_failed`

Estados de erro ou falta de dados nunca são convertidos numa recomendação
artificial.

## Testes

`test_ai_brief_frontend.py` valida o pedido mínimo, autenticação reutilizada,
cancelamento, ausência de geração automática, renderer seguro, ausência de
persistência e fixtures determinísticas dos seis estados. Não existem chamadas
OpenAI reais.
