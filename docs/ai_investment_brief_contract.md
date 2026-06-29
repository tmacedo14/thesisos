# AI Investment Brief — Contract v1.0

## Estado

Este documento define o contrato do AI Investment Brief previsto para
ThesisOS Beta `v0.5.0-beta`.

O contrato ainda não ativa qualquer provider de IA, endpoint ou elemento
de interface.

## Princípio de grounding

O provider recebe exclusivamente um bundle preparado pelo backend do
ThesisOS.

O provider não deve:

- pesquisar autonomamente na internet;
- introduzir números não presentes no bundle;
- substituir valores em falta por estimativas próprias;
- converter opiniões externas em factos;
- inventar fontes ou referências;
- tomar decisões sem indicar incerteza e limitações.

Cada evidência utilizada inclui:

- identificador;
- tipo de fonte;
- título;
- data de referência;
- campos concretos do payload que sustentam a conclusão.

O hash SHA-256 identifica o payload exato usado na geração.

## Estados explícitos

### `ready`

Existe grounding suficiente e a geração terminou com sucesso.

### `partial`

A geração terminou, mas existem campos não críticos em falta.

### `insufficient_data`

Faltam dados críticos. O provider não deve ser chamado.

### `provider_unavailable`

O bundle é válido, mas o provider está temporariamente indisponível.

### `disabled`

A funcionalidade está desativada por configuração ou feature flag.

### `generation_failed`

O provider respondeu, mas a geração, parsing ou validação final falhou.

## Incerteza

O objeto `uncertainty` contém:

- nível: `low`, `medium`, `high` ou `unknown`;
- razões explícitas;
- campos em falta.

A confiança da decisão varia entre 0 e 100 e não substitui a descrição
das limitações.

## Decisão

As ações permitidas são:

- `buy`;
- `initiate_small`;
- `accumulate`;
- `hold`;
- `watch`;
- `reduce`;
- `avoid`;
- `sell`;
- `unavailable`.

A decisão deve estar ligada ao evidence bundle e não apenas ao texto
produzido pelo modelo.

## Segurança

A API key do provider permanece exclusivamente no backend.

A interface nunca recebe:

- API keys;
- prompts internos;
- respostas brutas do provider;
- headers de autenticação do provider.

Por defeito, os prompts e respostas brutas não devem ser persistidos.

Logs devem conter apenas:

- estado;
- duração;
- provider;
- modelo;
- request id;
- tamanho do bundle;
- hash do bundle;
- código de erro normalizado.

## Próximas fases

1. Implementar o grounding bundle determinístico.
2. Validar cobertura e campos críticos.
3. Criar adapter abstrato do provider.
4. Adicionar provider atrás de feature flag.
5. Criar endpoint autenticado e rate limit próprio.
6. Integrar os estados na interface Beta.
