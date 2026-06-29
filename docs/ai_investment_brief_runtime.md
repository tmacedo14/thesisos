# AI Investment Brief — Runtime Integration v1

## Estado desta fase

O backend expõe duas rotas:

- `GET /api/ai-brief/config`;
- `POST /api/ai-brief`.

A feature permanece desativada por defeito. O provider HTTP OpenAI
Responses está implementado, mas só é construído quando a feature,
provider, modelo e API key estiverem configurados no servidor.

O frontend não foi alterado.

## Configuração pública

`GET /api/ai-brief/config` não exige autenticação.

A resposta pode indicar:

- se a feature está ativa;
- provider e modelo configurados;
- timeout;
- se existe uma chave configurada;
- limite do body;
- rate limit;
- se a autenticação é obrigatória.

A chave nunca é devolvida.

## Pedido autenticado

`POST /api/ai-brief` exige um Bearer token Supabase válido.

Corpo permitido:

```json
{
  "identifier": "AAPL",
  "base_currency": "EUR",
  "exchange": null,
  "ticker": null
}
```

Apenas estes quatro campos são aceites. O cliente não pode enviar:

- grounding bundles;
- analysis payloads;
- prompts;
- provider ou modelo;
- respostas previamente geradas.

## Sequência de processamento

1. validar a sessão Supabase;
2. aplicar rate limit por utilizador;
3. limitar o body a 16 KiB;
4. validar os campos do pedido;
5. construir a análise através do pipeline ThesisOS existente;
6. construir o grounding bundle server-side;
7. carregar a configuração server-side;
8. consultar o cache por grounding hash, provider e modelo;
9. produzir o estado normalizado através do provider adapter;
10. guardar apenas respostas `ready` ou `partial`.

Com a configuração por defeito, a resposta é `disabled`.

Se a feature for ativada sem provider real, a resposta será
`provider_unavailable`.

## Limites

- body máximo: 16 384 bytes;
- rate limit: 5 pedidos por utilizador em 60 segundos;
- identificador: máximo 64 caracteres;
- moeda base: código alfabético de três caracteres;
- exchange e ticker: máximo 64 caracteres.

O rate limit é local ao processo e não substitui uma solução
distribuída futura.

## Segurança

- autenticação antes da análise;
- grounding construído apenas no servidor;
- API key exclusivamente em variável de ambiente;
- configuração pública expõe apenas um booleano sobre a chave;
- nenhum provider HTTP é chamado enquanto a feature estiver desligada;
- cache usa cópias defensivas e não guarda falhas;
- logs opcionais não incluem payloads, prompts ou segredos;
- respostas de erro não incluem payloads ou credenciais de providers.

## Próxima fase

A fase seguinte deverá acrescentar:

- cache por hash do grounding;
- logs estruturados sem prompts ou respostas brutas;
- rate limit distribuído;
- testes de timeout e indisponibilidade;
- integração no frontend apenas depois de o backend estar estabilizado.
