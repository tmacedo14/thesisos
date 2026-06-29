# AI Investment Brief — Provider Adapter v1.0

## Objetivo

Esta camada define a fronteira entre o grounding bundle ThesisOS e um
provider futuro de IA.

Nesta fase não existe qualquer implementação HTTP de um provider.

## Feature flag

A funcionalidade fica desativada por defeito.

Variáveis previstas:

- `THESISOS_AI_BRIEF_ENABLED`;
- `THESISOS_AI_BRIEF_PROVIDER`;
- `THESISOS_AI_BRIEF_MODEL`;
- `THESISOS_AI_BRIEF_API_KEY`;
- `THESISOS_AI_BRIEF_TIMEOUT_SECONDS`.

A chave nunca é guardada no objeto de configuração. O runtime conserva
apenas o booleano `api_key_configured`.

## Ordem de decisão

1. Validar o hash do grounding bundle.
2. Se a feature estiver desligada, devolver `disabled`.
3. Se faltarem dados críticos, devolver `insufficient_data`.
4. Se a configuração estiver incompleta, devolver
   `provider_unavailable`.
5. Só depois invocar o adapter.
6. Validar e limitar a resposta antes de construir o brief final.

Assim, um provider nunca é chamado quando a funcionalidade está
desligada ou quando faltam dados críticos.

## Interface do adapter

Um adapter implementa:

```python
generate(
    grounding_bundle,
    *,
    timeout_seconds,
) -> ProviderResult
```

O adapter recebe apenas uma cópia do grounding bundle previamente
validado.

## Resposta permitida

O provider pode devolver apenas:

- `sections`;
- `decision`;
- `limitations`.

Campos adicionais, decisões inválidas, confiança fora de 0–100 ou
ausência de resumo executivo produzem `generation_failed`.

A resposta bruta não é incluída no objeto final.

## Normalização de estados

- feature desligada: `disabled`;
- dados críticos em falta: `insufficient_data`;
- configuração incompleta ou indisponibilidade temporária:
  `provider_unavailable`;
- exceção, parsing ou contrato inválido: `generation_failed`;
- sucesso com cobertura completa: `ready`;
- sucesso com campos opcionais em falta: `partial`.

## Segurança

O frontend nunca recebe a API key.

A configuração pública futura poderá expor somente:

- estado da feature;
- provider;
- modelo;
- timeout;
- booleano indicando se a chave está configurada.

A implementação real do provider deverá permanecer server-side e usar
timeouts, rate limit específico, logs normalizados e sem prompts ou
respostas brutas.
