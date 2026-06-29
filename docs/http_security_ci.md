# Beta HTTP Security & CI v1

## Objetivo

Esta fase adiciona uma baseline de segurança HTTP e integração contínua
sem ativar providers externos nem depender de segredos.

## Headers

Todas as respostas, incluindo API e ficheiros estáticos, recebem:

- `Content-Security-Policy`;
- `X-Content-Type-Options: nosniff`;
- `Referrer-Policy: strict-origin-when-cross-origin`;
- `Permissions-Policy`;
- `X-Frame-Options: DENY`;
- `Cross-Origin-Opener-Policy: same-origin-allow-popups`;
- `Cross-Origin-Resource-Policy: same-origin`;
- `X-Permitted-Cross-Domain-Policies: none`.

`Strict-Transport-Security` é enviado apenas quando o proxy indica HTTPS
através de `X-Forwarded-Proto`. Não é aplicado no servidor local HTTP.

## CSP transitória

O frontend atual contém JavaScript, CSS e atributos `style` inline. Uma
política estrita baseada apenas em nonces ou hashes quebraria a aplicação.

A política inicial:

- bloqueia objetos;
- impede framing;
- restringe `base-uri` e formulários;
- exclui `unsafe-eval`;
- permite temporariamente `unsafe-inline`;
- permite recursos HTTPS necessários ao frontend atual;
- força upgrade de pedidos inseguros.

A remoção de `unsafe-inline` depende da extração gradual do JavaScript e
CSS para ficheiros próprios.

## Métodos HTTP

- `OPTIONS` responde `204` com a lista de métodos suportados;
- `TRACE`, `PUT`, `PATCH` e `DELETE` respondem `405`;
- os erros usam JSON normalizado;
- `GET`, `HEAD`, `POST` e `OPTIONS` permanecem permitidos.

## Banner

O banner padrão `SimpleHTTP/... Python/...` é substituído por
`ThesisOS`, reduzindo divulgação desnecessária da stack.

## CI

O workflow GitHub Actions:

- usa Python 3.11;
- instala dependências pinadas;
- compila os módulos;
- executa contratos e testes do AI Brief;
- executa testes HTTP estáticos e live;
- valida Supabase Auth com configuração dummy pública;
- executa a suite QUICK;
- não executa FULL, por depender de providers e rede;
- não usa segredos;
- mantém o AI Brief desativado;
- usa permissões `contents: read`.

## Limitações e próximos passos

- CSP ainda permite `unsafe-inline`;
- rate limit e cache continuam locais ao processo;
- HSTS depende do proxy HTTPS;
- não existe análise automática de dependências;
- FULL permanece uma validação manual antes de checkpoints e releases.
