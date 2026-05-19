# Roteirizador — Guia Operacional
**Versão:** Web App (GitHub Pages) | **Dono:** Pedro Platner

---

## VISÃO GERAL DO SISTEMA

```
PEDRO (admin)                        CLIENTE
     |                                   |
     | 1. Cria planilha via admin panel  |
     | 2. Gera token com validade        |
     |---------------------------------->|
     |                                   | 3. Acessa o link do app
     |                                   | 4. Digita o token → entra
     |                                   | 5. Dados ficam no Google Sheets
     |                                   |    (↑ Sync / ↓ Carregar)
     |                                   |
     | 6. Renova ou revoga quando quiser |
     |<----------------------------------|
```

**Stack:**
- **Front:** HTML/JS estático hospedado no GitHub Pages
- **Auth:** Token validado via n8n proxy → Google Apps Script
- **Dados:** Google Sheets por cliente (criado automaticamente via n8n)
- **Backend:** Google Apps Script (GAS) central — lê e grava qualquer planilha autorizada

---

## LINKS IMPORTANTES

| O que | URL |
|---|---|
| App do cliente | https://pedroplatner.github.io/roteirizador/ |
| Painel admin | https://pedroplatner.github.io/roteirizador/admin_tokens.html |
| Repositório | https://github.com/pedroplatner/roteirizador |
| n8n (proxy + criar planilha) | https://awkwardlookingfrilledshark-n8n.cloudfy.live |

---

## PARTE 1 — NOVO CLIENTE (fluxo completo)

### Passo a passo

**1. Abra o painel admin**
```
https://pedroplatner.github.io/roteirizador/admin_tokens.html
```
Entre com a chave de administrador.

**2. Preencha o formulário "Gerar novo token"**
- **Nome do cliente:** nome da empresa (ex: "Transportadora ABC")
- **URL GAS:** deixe vazio (usa a URL central automaticamente)
- **Bancos de dados:** deixe vazio — o n8n cria a planilha automaticamente
- **Validade:** 30, 60 ou 90 dias

**3. Clique em "⚡ Gerar token"**
- O n8n cria uma planilha Google Sheets nova para o cliente
- O token é gerado com acesso à planilha
- Você vê o token na lista — copie e mande para o cliente

**4. Envie ao cliente:**
```
Acesse: https://pedroplatner.github.io/roteirizador/
Token: XXXX-XXXX-XXXX
```

**5. Cliente faz:**
1. Abre o link
2. Digita o token → clica Validar
3. App abre com os dados dele no Google Sheets
4. Importa o Excel com os funcionários (botão "Importar XLSX")
5. Clica ↑ Sync para salvar na planilha

---

## PARTE 2 — GERENCIAR TOKENS

### Painel admin → aba "Tokens cadastrados"

| Ação | Como |
|---|---|
| **Renovar** | Clique em "+30d" ou defina nova validade |
| **Revogar** | Clique no ícone 🔴 — token para de funcionar imediatamente |
| **Ver detalhes** | Clique em "Bancos" para ver planilhas vinculadas |
| **Adicionar banco** | Token pode ter múltiplas planilhas — cliente alterna entre elas no app |

### Aba "Conexões" (configuração global)
- **URL GAS:** URL central do Apps Script (não altere sem necessidade)
- **URL n8n proxy:** webhook do gas-proxy
- **URL n8n criar planilha:** webhook do binho-criar-planilha

---

## PARTE 3 — ATUALIZAÇÕES DO SISTEMA

### Atualizar o código

```bash
# No seu PC, dentro da pasta do projeto:
git add .
git commit -m "Descrição da mudança"
git push
```

GitHub Pages atualiza automaticamente em ~1 minuto. O cliente não precisa fazer nada — ao recarregar a página já tem a versão nova.

---

## PARTE 4 — ARQUITETURA TÉCNICA

### Fluxo de autenticação
```
Cliente digita token
  → POST para n8n (gas-proxy)
  → n8n chama GAS com action=validarToken
  → GAS confere token na planilha Tokens
  → Retorna: { ok, cliente, databases[], expira }
  → App libera o acesso e configura sheetsUrl + sheetId
```

### Fluxo de dados (Sync)
```
Cliente clica ↑ Sync
  → fetch POST no-cors → GAS (action=save, sheetId=X)
  → GAS abre planilha do cliente por ID
  → Grava aba "dados" + aba "meta"
  → App confirma via JSONP (action=verifyLastSave)
```

### Fluxo de criação de planilha (novo cliente)
```
Admin clica "Gerar token"
  → POST para n8n (binho-criar-planilha)
  → n8n cria Google Sheet "{nome} — Rotas Caterpillar"
  → n8n compartilha planilha com selborgestao@gmail.com (dono do GAS)
  → n8n retorna { sheet_id }
  → Admin salva token com databases=[{sheetId, gasUrl}]
```

### n8n Webhooks
| Webhook | Path | Função |
|---|---|---|
| gas-proxy | `/webhook/gas-proxy` | Proxy CORS: repassa chamadas do app para o GAS |
| criar-planilha | `/webhook/binho-criar-planilha` | Cria planilha + compartilha + retorna sheet_id |

### Google Apps Script
- **URL:** `https://script.google.com/macros/s/AKfycbwdBGtYKmiZo_XFZM98CH6KtTy83AW78uCQa_s5RCKbkvASvH8eiG75StiEFRALHvc0Dg/exec`
- **Conta:** selborgestao@gmail.com
- **Planilha de tokens:** ID `1Cv6OsKh8kxI0CgkACW9_cCBoRVRtWMarXjOsZ6-_WG0`
- **Ações disponíveis:** `ping`, `load`, `save`, `verifyLastSave`, `validarToken`, `adminTokens`

---

## PARTE 5 — TROUBLESHOOTING

| Problema | Causa provável | Solução |
|---|---|---|
| "Token inválido" | Token expirado ou revogado | Renovar no painel admin |
| "Erro ao carregar" | GAS sem acesso à planilha | Verificar se planilha está compartilhada com selborgestao@gmail.com |
| "n8n não retornou sheet_id" | Webhook binho-criar-planilha com erro | Verificar logs do n8n |
| App não atualiza | Cache do browser | Ctrl+F5 / hard refresh |
| Sync sempre mostra "enviado" (não "ok") | GAS não consegue abrir planilha | Permissão cross-account (ver acima) |

---

## PARTE 6 — REFERÊNCIA RÁPIDA

| O que | Onde |
|---|---|
| Link do app | https://pedroplatner.github.io/roteirizador/ |
| Painel admin | https://pedroplatner.github.io/roteirizador/admin_tokens.html |
| Código | https://github.com/pedroplatner/roteirizador |
| GAS (código backend) | selborgestao@gmail.com → Google Apps Script |
| Planilha de tokens | Drive selborgestao → "1Cv6OsKh8kxI0Cgk..." |

---

*Atualizado em 2026-05-19 — versão web app (GitHub Pages + GAS + n8n)*
